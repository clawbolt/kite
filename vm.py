"""Kite 字节码编译器 + 虚拟机 (KBC, M1)

AST ──compile──▶ Proto(字节码) ──VM──▶ 结果

设计要点
- 栈式字节码；局部变量在编译期解析为槽位（slot），不再按名字逐层查找环境
- 闭包使用 Lua 式 upvalue：捕获时指向外层帧的槽位（open），作用域结束时 CLOSE 成独立单元，
  因此 for 循环里每次迭代创建的闭包各自持有自己的变量
- 编译期跟踪操作数栈深度，break / continue 从表达式中间跳出时能精确弹栈
- defer 编译为零参闭包，块结束、break/continue、return、? 提前返回、运行时错误时都会执行
- 编译期再次检查不可变绑定，即使用 --no-check 跳过类型检查也不能给 let 赋值
- 运行时值、内建函数、标准库与树遍历解释器共用（kite.py），保证两个引擎语义一致
"""
import sys
_rc = sys.getrefcount
from kite import TS, MAIN_TASK, TASK_WRITE_ERR, run_task, spawn_all, sched_reset, Sched, cancel_point
from kite import append_pattern
from kite import (range_ok, std_use_entries, PMap, PSet, KiteError, Parser, lex, Interp, Unit, StructVal, EnumVal, EnumType,
                  StructType, Module, Builtin, KFunc, NoneV, show, type_name)

# ───────────────────────── 指令集 ─────────────────────────
OPNAMES = """CONST LOAD_L STORE_L LOAD_U STORE_U LOAD_G STORE_G DEF_G POP POPN
ADD SUB MUL DIV MOD LT GT LE GE EQ NE RANGE RANGE_INC NEG NOT
JUMP JIF JIF_OR_POP JIT_OR_POP
CALL CALL_M RET CLOSURE LIST TUPLE STRUCT FIELD TIDX INDEX WFIELD WINDEX
INTERP TEST_PAT MATCH_OR_FAIL MATCH_FAIL TRY ITER FOR_NEXT CLOSE DEFER RUN_DEFERS SPAWN APPEND_L WINDEX_L""".split()
for _i, _n in enumerate(OPNAMES):
    globals()[_n] = _i

BINOPS = {"+": ADD, "-": SUB, "*": MUL, "/": DIV, "%": MOD, "<": LT, ">": GT, "<=": LE,
          ">=": GE, "==": EQ, "!=": NE, "..": RANGE, "..=": RANGE_INC}
OPSYM = {v: k for k, v in BINOPS.items()}
DECLS = ("Fn", "Struct", "Enum", "Trait", "Impl", "Alias", "Use", "Mod")


class Proto:
    __slots__ = ("name", "nparams", "nlocals", "code", "lines", "updesc")
    def __init__(self, name, nparams):
        self.name, self.nparams = name, nparams
        self.nlocals, self.code, self.lines, self.updesc = 0, [], [], []


class Closure:
    __slots__ = ("proto", "ups")
    def __init__(self, proto, ups): self.proto, self.ups = proto, ups


class Upval:
    __slots__ = ("ref", "i", "owner")
    def __init__(self, ref, i): self.ref, self.i, self.owner = ref, i, TS.cur


# ───────────────────────── 编译器 ─────────────────────────
class FnComp:
    def __init__(self, cc, parent, name, nparams=0, chunk=False):
        self.cc, self.parent, self.chunk = cc, parent, chunk
        self.p = Proto(name, nparams)
        self.scopes = []         # [{名字: (槽位, 可变)}]
        self.next_slot = 0
        self.upnames = {}        # 名字 → (upvalue 下标, 可变)
        self.depth = 0           # 编译期操作数栈深度
        self.loops = []          # [{"breaks": [], "cont": 目标, "depth", "slot", "defers"}]
        self.ndefers = 0         # 编译期 defer 嵌套计数
        self.line = 0

    # 发射
    def emit(self, op, arg=None, eff=0):
        self.p.code.append((op, arg)); self.p.lines.append(self.line)
        self.depth += eff
        return len(self.p.code) - 1

    def here(self): return len(self.p.code)

    def patch(self, i, target=None):
        op, arg = self.p.code[i]
        t = self.here() if target is None else target
        self.p.code[i] = (op, (arg[0], t) if op == FOR_NEXT else t)

    # 作用域
    def push_scope(self):
        self.scopes.append({})
        return self.next_slot, self.ndefers

    def pop_scope(self, base, dbase, emit_close=True):
        self.scopes.pop()
        if self.ndefers > dbase:
            self.emit(RUN_DEFERS, dbase)
        if emit_close and self.next_slot > base:
            self.emit(CLOSE, base)
        self.next_slot, self.ndefers = base, dbase

    def alloc(self):
        s = self.next_slot; self.next_slot += 1
        self.p.nlocals = max(self.p.nlocals, self.next_slot)
        return s

    def declare(self, name, mutable=False):
        s = self.alloc()
        self.scopes[-1][name] = (s, mutable)
        return s

    def resolve(self, name):
        for sc in reversed(self.scopes):
            if name in sc:
                s, m = sc[name]; return ("local", s, m)
        if name in self.upnames:
            i, m = self.upnames[name]; return ("upval", i, m)
        if self.parent is not None:
            r = self.parent.resolve(name)
            if r[0] in ("local", "upval"):
                i = len(self.p.updesc)
                self.p.updesc.append((r[0] == "local", r[1]))
                self.upnames[name] = (i, r[2])
                return ("upval", i, r[2])
        return ("global", name, self.cc.global_mut.get(name, False))

    def load(self, name):
        r = self.resolve(name)
        self.emit({"local": LOAD_L, "upval": LOAD_U, "global": LOAD_G}[r[0]], r[1], +1)

    def store(self, name, line):
        r = self.resolve(name)
        if not r[2]:
            raise KiteError(f"编译错误: 不能给不可变绑定 `{name}` 赋值（用 var 声明）", line)
        self.emit({"local": STORE_L, "upval": STORE_U, "global": STORE_G}[r[0]], r[1], -1)

    def bind_target(self, name, mutable):
        """let / 模式绑定：块内为局部槽位，顶层为全局"""
        if self.chunk and not self.scopes:
            self.cc.global_mut[name] = mutable
            return ("gbind", name)
        return ("bind", self.declare(name, mutable))

    # 模式
    def cpat(self, p, mutable=False):
        k = p[0]
        if k == "bind":
            m = mutable or len(p) > 2
            ob = getattr(self, "_orbind", None)
            if ob is None: return self.bind_target(p[1], m)
            if p[1] not in ob: ob[p[1]] = self.bind_target(p[1], m)   # 或模式的各分支共用槽位
            return ob[p[1]]
        if k == "at": return ("at", self.cpat(p[1], mutable), self.cpat(p[2], mutable))
        if k == "or":
            saved = getattr(self, "_orbind", None)
            if saved is None: self._orbind = {}
            try: return ("or", [self.cpat(x, mutable) for x in p[1]])
            finally:
                if saved is None: self._orbind = None
        if k == "tuple": return ("tuple", [self.cpat(x, mutable) for x in p[1]])
        if k == "list":
            rest = p[2]
            if rest is None: r = None
            elif rest == "_": r = ("wild",)
            else: r = self.bind_target(rest, mutable)
            pre = [self.cpat(x, mutable) for x in p[1]]
            if len(p) > 3: return ("list", pre, r, [self.cpat(x, mutable) for x in p[3]])
            return ("list", pre, r)
        if k == "variant":
            return ("variant", p[1], None if p[2] is None else [self.cpat(x, mutable) for x in p[2]])
        if k == "struct":
            return ("struct", p[1], [(f, self.cpat(x, mutable)) for f, x in p[2]])
        return p

    # 函数
    def compile_fn(self, name, params, body, parent):
        fc = FnComp(self.cc, parent, name, len(params))
        fc.line = body.line
        fc.scopes.append({})
        for pname, _ in params:
            fc.declare(pname)
        if body.kind == "Block":
            fc.block_body(body)
        else:
            fc.expr(body)
        fc.emit(RET, None, -1)
        return fc.p

    def block_body(self, b):
        """函数体：与 block 相同，但不需要 CLOSE（帧随返回销毁）"""
        self.block(b, close=False)

    # 块与语句
    def hoist(self, stmts):
        decls = [s for s in stmts if s.kind in DECLS]
        if not decls:
            return
        self.cc.declare_types(decls, self)
        fns = [s for s in decls if s.kind == "Fn" and s.body is not None]
        if self.chunk and not self.scopes:
            return
        slots = [(f, self.declare(f.name)) for f in fns]
        for f, slot in slots:
            self.line = f.line
            proto = self.compile_fn(f.name, f.params, f.body, self)
            self.emit(CLOSURE, proto, +1)
            self.emit(STORE_L, slot, -1)

    def block(self, b, close=True):
        base, dbase = self.push_scope()
        self.hoist(b.stmts)
        body = [s for s in b.stmts if s.kind not in DECLS]
        if not body:
            self.emit(CONST, Unit, +1)
        for i, s in enumerate(body):
            last = i == len(body) - 1
            if s.kind == "ExprStmt":
                self.expr(s.expr)
                if not last: self.emit(POP, None, -1)
            else:
                self.stmt(s)
                if last: self.emit(CONST, Unit, +1)
        self.pop_scope(base, dbase, emit_close=close)

    def stmt(self, s):
        self.line = s.line
        k = s.kind
        if k == "ExprStmt":
            self.expr(s.expr); self.emit(POP, None, -1)
        elif k == "Let":
            self.expr(s.value)
            if s.pat[0] == "bind":
                t = self.bind_target(s.pat[1], s.mutable)
                if t[0] == "gbind": self.emit(DEF_G, t[1], -1)
                else: self.emit(STORE_L, t[1], -1)
            else:
                self.emit(MATCH_OR_FAIL, self.cpat(s.pat, s.mutable), -1)
        elif k == "Assign":
            self.assign(s)
        elif k == "Return":
            if s.value is None: self.emit(CONST, Unit, +1)
            else: self.expr(s.value)
            self.emit(RET, None, -1)
        elif k in ("Break", "Continue"):
            if not self.loops:
                raise KiteError(f"编译错误: {k.lower()} 只能出现在循环里", s.line)
            lab = getattr(s, "label", None)
            lp = self.loops[-1] if lab is None else next((l for l in reversed(self.loops) if l["label"] == lab), None)
            if lp is None: raise KiteError(f"编译错误: 找不到循环标签 '{lab}", s.line)
            val = getattr(s, "value", None)
            if k == "Break" and "res" in lp:      # loop 表达式：值先存进隐藏槽位
                if val is None: self.emit(CONST, Unit, +1)
                else: self.expr(val)
                self.emit(STORE_L, lp["res"], -1)
            elif val is not None:
                raise KiteError("编译错误: 只有 loop 的 break 能带值", s.line)
            extra = self.depth - lp["depth"]
            if extra: self.emit(POPN, extra, 0)
            if self.ndefers > lp["defers"]: self.emit(RUN_DEFERS, lp["defers"])
            self.emit(CLOSE, lp["slot"])
            if k == "Break":
                lp["breaks"].append(self.emit(JUMP, None))
            else:
                self.emit(JUMP, lp["cont"])
        elif k == "While":
            top = self.here()
            exit_j = None
            if s.cond is not None:
                self.expr(s.cond); exit_j = self.emit(JIF, None, -1)
            lp = {"breaks": [], "cont": top, "depth": self.depth, "slot": self.next_slot, "defers": self.ndefers, "label": getattr(s, "label", None)}
            self.loops.append(lp)
            self.block(s.body); self.emit(POP, None, -1)
            self.loops.pop()
            self.emit(JUMP, top)
            if exit_j is not None: self.patch(exit_j)
            for j in lp["breaks"]: self.patch(j)
        elif k == "For":
            base, dbase = self.push_scope()
            it_slot = self.alloc()
            self.expr(s.iter); self.emit(ITER, it_slot, -1)
            top = self.here()
            nxt = self.emit(FOR_NEXT, (it_slot, None), +1)
            ibase, idb = self.push_scope()
            self.emit(MATCH_OR_FAIL, self.cpat(s.pat), -1)
            # continue 自己会执行 POPN / RUN_DEFERS / CLOSE，然后直接回到循环顶部
            lp = {"breaks": [], "cont": top, "depth": self.depth, "slot": ibase, "defers": self.ndefers, "label": getattr(s, "label", None)}
            self.loops.append(lp)
            self.block(s.body); self.emit(POP, None, -1)
            self.loops.pop()
            self.pop_scope(ibase, idb)
            self.emit(JUMP, top)
            self.patch(nxt)
            for j in lp["breaks"]: self.patch(j)
            self.pop_scope(base, dbase)
        elif k == "Defer":
            proto = self.compile_fn("<defer>", [], s.body, self)
            self.emit(CLOSURE, proto, +1); self.emit(DEFER, None, -1)
            self.ndefers += 1
        elif k in DECLS:
            pass
        else:
            raise KiteError(f"编译错误: 未知语句 {k}", s.line)

    def assign(self, s):
        t = s.target
        root = t
        while root.kind in ("Field", "Index"):
            root = root.obj
        if root.kind != "Name":
            raise KiteError("编译错误: 赋值目标无效", s.line)
        r = self.resolve(root.name)
        if not r[2]:
            raise KiteError(f"编译错误: 不能修改不可变绑定 `{root.name}`（用 var 声明）", s.line)

        def leaf_value():
            if s.op == "=":
                self.expr(s.value)
            else:
                self.expr(t); self.expr(s.value)
                self.emit(BINOPS[s.op[0]], None, -1)

        def update(node, produce):
            if node.kind == "Name":
                produce(); self.store(node.name, s.line)
            elif node.kind == "Field":
                def p2():
                    self.expr(node.obj); produce(); self.emit(WFIELD, node.name, -1)
                update(node.obj, p2)
            else:
                def p3():
                    self.expr(node.obj); self.expr(node.index); produce(); self.emit(WINDEX, None, -2)
                update(node.obj, p3)
        # xs = push(xs, e) / xs += e / xs = xs + e（局部 var）：编译成 APPEND_L，运行时列表没有别的引用就原地追加
        # xs[i] = v（局部 var）：WINDEX_L，列表没有别的引用就原地写入
        if (t.kind == "Index" and t.obj.kind == "Name" and s.op == "=" and r[0] == "local" and self.cc.append_ops):
            self.expr(t.index); self.expr(s.value); self.emit(WINDEX_L, r[1], -2); return
        if t.kind == "Name" and r[0] == "local" and self.cc.append_ops:
            fast = append_pattern(s)
            if fast and (fast[0] or self.resolve("push")[0] == "global"):
                self.expr(fast[1]); self.emit(APPEND_L, (r[1], fast[0]), -1); return
        update(t, leaf_value)

    # 表达式（每个表达式恰好压入一个值）
    def expr(self, e):
        self.line = e.line
        k = e.kind
        if k == "Lit":
            self.emit(CONST, e.value, +1)
        elif k == "Name":
            self.load(e.name)
        elif k == "Interp":
            for p in e.parts:
                if isinstance(p, str): self.emit(CONST, p, +1)
                else: self.expr(p)
            self.emit(INTERP, len(e.parts), 1 - len(e.parts))
        elif k == "Bin":
            if e.op in ("&&", "||"):
                self.expr(e.left)
                j = self.emit(JIF_OR_POP if e.op == "&&" else JIT_OR_POP, None, -1)
                self.expr(e.right)
                self.patch(j)
            else:
                self.expr(e.left); self.expr(e.right)
                self.line = e.line
                self.emit(BINOPS[e.op], None, -1)
        elif k == "Unary":
            self.expr(e.operand); self.emit(NEG if e.op == "-" else NOT, None, 0)
        elif k == "Tuple":
            for x in e.items: self.expr(x)
            if e.items: self.emit(TUPLE, len(e.items), 1 - len(e.items))
            else: self.emit(TUPLE, 0, +1)
        elif k == "List":
            for x in e.items: self.expr(x)
            self.emit(LIST, len(e.items), 1 - len(e.items))
        elif k == "Block":
            self.block(e)
        elif k == "Loop":
            base, dbase = self.push_scope()
            rs = self.alloc()
            top = self.here()
            lp = {"breaks": [], "cont": top, "depth": self.depth, "slot": self.next_slot, "defers": self.ndefers,
                  "label": e.label, "res": rs}
            self.loops.append(lp)
            self.block(e.body); self.emit(POP, None, -1)
            self.loops.pop()
            self.emit(JUMP, top)
            for j in lp["breaks"]: self.patch(j)
            self.emit(LOAD_L, rs, +1)
            self.pop_scope(base, dbase)
        elif k == "If":
            self.expr(e.cond); j = self.emit(JIF, None, -1)
            self.block(e.then)
            end = self.emit(JUMP, None)
            self.patch(j); self.depth -= 1
            if e.els is None: self.emit(CONST, Unit, +1)
            else: self.expr(e.els)
            self.patch(end)
        elif k == "Match":
            self.match(e)
        elif k == "Is":
            self.expr(e.value)
            base, dbase = self.push_scope()
            self.emit(TEST_PAT, self.cpat(e.pat), 0)
            self.scopes.pop(); self.next_slot = base
        elif k == "Lambda":
            proto = self.compile_fn("<lambda>", e.params, e.body, self)
            self.emit(CLOSURE, proto, +1)
        elif k == "Call":
            self.call(e.fn, e.args, None, e.line)
        elif k == "Pipe":
            tmp_base = self.next_slot
            if not self.scopes:
                self.scopes.append({}); pushed = True
            else:
                pushed = False
            tmp = self.alloc()
            self.expr(e.left); self.emit(STORE_L, tmp, -1)
            r = e.right
            if r.kind == "Call":
                self.call(r.fn, r.args, tmp, e.line)
            else:
                self.call(r, [], tmp, e.line)
            if pushed:
                self.scopes.pop()
        elif k == "Field":
            self.expr(e.obj); self.line = e.line; self.emit(FIELD, e.name, 0)
        elif k == "TupleIndex":
            self.expr(e.obj); self.emit(TIDX, e.index, 0)
        elif k == "Index":
            self.expr(e.obj); self.expr(e.index); self.line = e.line; self.emit(INDEX, None, -1)
        elif k == "Try":
            self.expr(e.value); self.line = e.line; self.emit(TRY, None, 0)
        elif k == "StructLit":
            for _, x in e.fields: self.expr(x)
            n = len(e.fields)
            self.line = e.line
            self.emit(STRUCT, (e.name, tuple(f for f, _ in e.fields)), 1 - n)
        elif k == "Select":
            from kite import lower_select
            self.expr(lower_select(e))
        elif k == "Race":
            # race：每一项编译成无参闭包，交给内置 __race（第一个完成的胜出，其余取消）
            self.line = e.line
            self.emit(LOAD_G, "__race", +1)
            for x in e.items:
                proto = self.compile_fn("<race>", [], x, self)
                self.emit(CLOSURE, proto, +1)
            self.emit(LIST, len(e.items), 1 - len(e.items))
            self.emit(CALL, 1, -1)
        elif k == "Spawn":
            # 每一项编译成无参闭包（任务），SPAWN 并行（参考实现中顺序）执行它们并收集成元组
            for x in e.items:
                proto = self.compile_fn("<spawn>", [], x, self)
                self.emit(CLOSURE, proto, +1)
            self.line = e.line
            self.emit(SPAWN, len(e.items), 1 - len(e.items))
        elif k == "Await":
            self.expr(e.value)
        else:
            raise KiteError(f"编译错误: 未知表达式 {k}", e.line)

    def call(self, fnode, args, pre_slot, line):
        n = len(args) + (1 if pre_slot is not None else 0)
        if fnode.kind == "Field":
            self.expr(fnode.obj)
            if pre_slot is not None: self.emit(LOAD_L, pre_slot, +1)
            for a in args: self.expr(a)
            self.line = line
            self.emit(CALL_M, (fnode.name, n), -n)
        else:
            self.expr(fnode)
            if pre_slot is not None: self.emit(LOAD_L, pre_slot, +1)
            for a in args: self.expr(a)
            self.line = line
            self.emit(CALL, n, -n)

    def match(self, e):
        base, dbase = self.push_scope()
        subj = self.alloc()
        self.expr(e.subject); self.emit(STORE_L, subj, -1)
        ends = []
        for pat, guard, body in e.arms:
            abase, adb = self.push_scope()
            self.emit(LOAD_L, subj, +1)
            self.emit(TEST_PAT, self.cpat(pat), 0)
            nxt = self.emit(JIF, None, -1)
            nxt2 = None
            if guard is not None:
                self.expr(guard); nxt2 = self.emit(JIF, None, -1)
            self.expr(body)
            self.pop_scope(abase, adb)
            ends.append(self.emit(JUMP, None))
            self.depth -= 1
            self.patch(nxt)
            if nxt2 is not None: self.patch(nxt2)
        self.line = e.line
        self.emit(LOAD_L, subj, +1)
        self.emit(MATCH_FAIL, None, 0)
        for j in ends: self.patch(j)
        self.scopes.pop(); self.next_slot = base


class Compiler:
    """跨多次编译（REPL）保留全局状态"""
    def __init__(self, vm):
        self.vm = vm
        self.global_mut = {}
        self.append_ops = True    # native 后端自己有均摊 O(1) 的 push，关掉 APPEND_L

    def compile_chunk(self, items):
        fc = FnComp(self, None, "<main>", 0, chunk=True)
        # 顶层 var 先登记可变性：函数在提升阶段就会编译，可能先于 var 声明引用它
        from modules import pat_binds
        for s in items:
            if s.kind == "Let" and s.mutable:
                for nm in pat_binds(s.pat, []): self.global_mut[nm] = True
        fc.hoist(items)
        body = [s for s in items if s.kind not in DECLS]
        for i, s in enumerate(body):
            last = i == len(body) - 1
            if s.kind == "ExprStmt" and last:
                fc.expr(s.expr)
            else:
                fc.stmt(s)
                if last: fc.emit(CONST, Unit, +1)
        if not body:
            fc.emit(CONST, Unit, +1)
        fc.emit(RET, None, -1)
        return fc.p

    def declare_types(self, decls, fc):
        vm = self.vm
        top = fc.chunk and not fc.scopes
        for it in decls:
            k = it.kind
            if k == "Struct":
                vm.globals[it.name] = StructType(it.name, [f for f, _ in it.fields])
            elif k == "Enum":
                et = EnumType(it.name)
                for vn, fs in it.variants:
                    et.variants[vn] = fs
                    vm.variants.add(vn)
                    if fs:
                        vm.globals[vn] = vm.rt.variant_ctor(et, vn, fs)
                    else:
                        vm.globals[vn] = EnumVal(it.name, vn, ())
                vm.globals[it.name] = et
            elif k == "Trait":
                vm.traits[it.name] = it.methods
            elif k == "Use":
                p = it.path
                if p[0] == "std" and len(p) >= 2:
                    for e in std_use_entries(it):
                        mod = self._mod(e[1], it.line)
                        if e[0] == "mod": vm.globals[e[2]] = mod
                        elif e[0] == "all": vm.globals.update(mod.members)
                        else: vm.globals[e[3]] = mod.members[e[2]]
        parent = None if top else fc
        for it in decls:
            if it.kind == "Fn" and it.body is not None and top:
                fc.line = it.line
                vm.globals[it.name] = Closure(fc.compile_fn(it.name, it.params, it.body, None), [])
            elif it.kind == "Fn" and getattr(it, "extern", None) is not None and top:
                from kite import make_externs
                for nm, b in make_externs(it, lambda f, a, _ln=it.line: vm.call_value(f, a, _ln)):
                    vm.globals[nm] = b
            elif it.kind == "Impl":
                tbl = vm.methods.setdefault(it.target, {})
                ms = list(it.methods)
                own = {m.name for m in ms}
                if it.trait and it.trait in vm.traits:
                    ms += [m for m in vm.traits[it.trait] if m.body is not None and m.name not in own]
                for m in ms:
                    if parent is not None:
                        # 局部 impl：方法不捕获外层变量
                        pass
                    tbl[m.name] = Closure(fc.compile_fn(m.name, m.params, m.body, None), [])

    def _mod(self, name, line):
        rt = self.vm.rt
        mod = rt._std_mod(name, line)
        return Module(name, {k: rt._wrap(k, v) for k, v in mod.items()})


# ───────────────────────── 虚拟机 ─────────────────────────
class Bridge(Interp):
    """复用解释器的内建函数与运行时辅助；内建函数回调闭包时转给 VM"""
    def __init__(self, vm):
        self.vm = vm
        super().__init__(typecheck=False)

    def call(self, f, args, line):
        if type(f) is Closure:
            return self.vm.execute(f, args)
        return super().call(f, args, line)


def _pm(p, v, L, vm):
    t = p[0]
    if t == "bind":
        L[p[1]] = v; return True
    if t == "wild": return True
    if t == "lit":
        return v == p[1] and type(v) == type(p[1])
    if t == "variant":
        name, subs = p[1], p[2]
        if type(v) is not EnumVal or v.variant != name:
            return subs is None and name not in vm.variants and type(v) is StructVal and v.tname == name
        if subs is None: return True
        if len(subs) != len(v.values):
            raise KiteError(f"模式 {name} 需要 {len(v.values)} 个字段")
        for sp, sv in zip(subs, v.values):
            if not _pm(sp, sv, L, vm): return False
        return True
    if t == "tuple":
        if type(v) is not tuple or len(v) != len(p[1]): return False
        for sp, sv in zip(p[1], v):
            if not _pm(sp, sv, L, vm): return False
        return True
    if t == "gbind":
        vm.globals[p[1]] = v; return True
    if t == "or":
        for a in p[1]:
            if _pm(a, v, L, vm): return True
        return False
    if t == "range":
        return range_ok(p, v)
    if t == "at":
        return _pm(p[2], v, L, vm) and _pm(p[1], v, L, vm)
    if t == "list":
        items, rest = p[1], p[2]
        post = p[3] if len(p) > 3 else ()
        if type(v) is not list: return False
        if rest is None and len(v) != len(items): return False
        if len(v) < len(items) + len(post): return False
        for sp, sv in zip(items, v):
            if not _pm(sp, sv, L, vm): return False
        tail = len(v) - len(post)
        for sp, sv in zip(post, v[tail:]):
            if not _pm(sp, sv, L, vm): return False
        if rest is not None:
            return _pm(rest, v[len(items):tail], L, vm)
        return True
    if t == "struct":
        name, fs = p[1], p[2]
        if type(v) is StructVal and v.tname == name:
            d = v.fields
        elif type(v) is EnumVal and v.variant == name and v.names:
            d = dict(zip(v.names, v.values))
        else:
            return False
        for f, sp in fs:
            if f not in d or not _pm(sp, d[f], L, vm): return False
        return True
    raise KiteError(f"未知模式 {t}")


_FAST = (int, float, str)


class VM:
    def __init__(self, typecheck=True):
        sched_reset()
        self.globals = {}
        self.methods = {}
        self.traits = {}
        self.variants = {"Ok", "Err", "Some", "None"}
        self.rt = Bridge(self)
        for name, slot in self.rt.g.vars.items():
            self.globals[name] = slot[0]
        self.compiler = Compiler(self)
        self.bpush = self.globals["push"]
        self.checker = None
        if typecheck:
            from typecheck import Checker
            self.checker = Checker()
        self.last_proto = None

    def compile(self, src):
        from kite import with_prelude
        items = with_prelude(src if isinstance(src, list) else Parser(lex(src)).program())
        if self.checker is not None:
            errs = self.checker.check(items)
            if errs:
                from kite import CheckFailed
                errs.sort(key=lambda e: e.line or 0)
                raise CheckFailed(errs)
        from kite import erase_typeapps
        erase_typeapps(items)
        proto = self.compiler.compile_chunk(items)
        self.last_proto = proto
        self.last_items = items
        return proto

    def run(self, src, call_main=True):
        proto = self.compile(src)
        result = self.execute(Closure(proto, []), [])
        main = self.globals.get("main")
        if call_main and type(main) is Closure:
            self.execute(main, [])
        return result

    def call_value(self, f, args, line):
        if type(f) is Closure:
            return self.execute(f, args)
        return self.rt.call(f, args, line)

    def execute(self, cl, args):
        proto = cl.proto
        if len(args) != proto.nparams:
            raise KiteError(f"{proto.name} 需要 {proto.nparams} 个参数，给了 {len(args)} 个")
        code, lines = proto.code, proto.lines
        L = list(args) + [None] * (proto.nlocals - len(args))
        ups = cl.ups
        stack = []
        push, pop = stack.append, stack.pop
        G = self.globals
        rt = self.rt
        open_up = {}
        defers = []
        pc = 0
        if Sched.cancels or Sched.timers: cancel_point()
        try:
            while True:
                op, arg = code[pc]
                pc += 1
                if op == LOAD_L:
                    push(L[arg])
                elif op == CONST:
                    push(arg)
                elif op == STORE_L:
                    L[arg] = pop()
                elif op == LOAD_G:
                    try:
                        push(G[arg])
                    except KeyError:
                        raise KiteError(f"未定义的名字 {arg}")
                elif op == LOAD_U:
                    u = ups[arg]; push(u.ref[u.i])
                elif op == JIF:
                    v = pop()
                    if v is not True:
                        if v is False: pc = arg
                        else: raise KiteError(f"条件必须是 Bool，实际是 {type_name(v)}")
                elif op == JUMP:
                    if arg < pc and (Sched.cancels or Sched.timers): cancel_point()
                    pc = arg
                elif op == ADD:
                    b = pop(); a = stack[-1]; ta = type(a)
                    if ta is type(b) and ta in _FAST:
                        r = a + b
                        if ta is int and not -9223372036854775808 <= r <= 9223372036854775807: raise KiteError("整数溢出")
                        stack[-1] = r
                    else:
                        stack[-1] = rt.binop("+", a, b, None)
                elif op == SUB:
                    b = pop(); a = stack[-1]; ta = type(a)
                    if ta is type(b) and (ta is int or ta is float):
                        r = a - b
                        if ta is int and not -9223372036854775808 <= r <= 9223372036854775807: raise KiteError("整数溢出")
                        stack[-1] = r
                    else:
                        stack[-1] = rt.binop("-", a, b, None)
                elif op == MUL:
                    b = pop(); a = stack[-1]; ta = type(a)
                    if ta is type(b) and (ta is int or ta is float):
                        r = a * b
                        if ta is int and not -9223372036854775808 <= r <= 9223372036854775807: raise KiteError("整数溢出")
                        stack[-1] = r
                    else:
                        stack[-1] = rt.binop("*", a, b, None)
                elif op == LT or op == GT or op == LE or op == GE:
                    b = pop(); a = stack[-1]; ta = type(a)
                    if ta is type(b) and ta in _FAST:
                        stack[-1] = a < b if op == LT else a > b if op == GT else a <= b if op == LE else a >= b
                    else:
                        stack[-1] = rt.binop(OPSYM[op], a, b, None)
                elif op == EQ or op == NE:
                    b = pop(); a = stack[-1]; ta = type(a)
                    r = (a == b) if ta is type(b) and ta in _FAST else rt.binop("==", a, b, None)
                    stack[-1] = r if op == EQ else not r
                elif op == CALL:
                    if arg:
                        a = stack[-arg:]; del stack[-arg:]
                    else:
                        a = []
                    f = pop()
                    push(self.execute(f, a) if type(f) is Closure else rt.call(f, a, lines[pc - 1]))
                elif op == RET:
                    return pop()
                elif op == CALL_M:
                    name, n = arg
                    if n:
                        a = stack[-n:]; del stack[-n:]
                    else:
                        a = []
                    recv = pop()
                    push(self.call_method(recv, name, a, lines[pc - 1]))
                elif op == DIV or op == MOD:
                    b = pop(); stack[-1] = rt.binop(OPSYM[op], stack[-1], b, None)
                elif op == RANGE or op == RANGE_INC:
                    b = pop(); stack[-1] = rt.binop(OPSYM[op], stack[-1], b, None)
                elif op == FIELD:
                    o = stack[-1]
                    if type(o) is StructVal and arg in o.fields:
                        stack[-1] = o.fields[arg]
                    else:
                        stack[-1] = rt.get_field(o, arg, None)
                elif op == TEST_PAT:
                    stack[-1] = _pm(arg, stack[-1], L, self)
                elif op == FOR_NEXT:
                    try:
                        push(next(L[arg[0]]))
                    except StopIteration:
                        pc = arg[1]
                elif op == MATCH_OR_FAIL:
                    v = pop()
                    if not _pm(arg, v, L, self):
                        raise KiteError(f"解构失败: {show(v, True)}")
                elif op == CLOSE:
                    if open_up:
                        for k in [k for k in open_up if k >= arg]:
                            u = open_up.pop(k); u.ref = [u.ref[u.i]]; u.i = 0
                elif op == POP:
                    pop()
                elif op == CLOSURE:
                    nu = []
                    for is_local, idx in arg.updesc:
                        if is_local:
                            u = open_up.get(idx)
                            if u is None:
                                u = Upval(L, idx); open_up[idx] = u
                            nu.append(u)
                        else:
                            nu.append(ups[idx])
                    push(Closure(arg, nu))
                elif op == INTERP:
                    parts = stack[-arg:]; del stack[-arg:]
                    push("".join(x if type(x) is str else show(x) for x in parts))
                elif op == LIST:
                    if arg:
                        v = stack[-arg:]; del stack[-arg:]; push(v)
                    else:
                        push([])
                elif op == TUPLE:
                    if arg:
                        v = tuple(stack[-arg:]); del stack[-arg:]; push(v)
                    else:
                        push(Unit)
                elif op == STORE_U:
                    u = ups[arg]; o = u.owner
                    while o.done: o = o.parent
                    if o is not TS.cur: raise KiteError(TASK_WRITE_ERR)
                    u.ref[u.i] = pop()
                elif op == STORE_G:
                    if TS.cur is not MAIN_TASK: raise KiteError(TASK_WRITE_ERR)
                    G[arg] = pop()
                elif op == APPEND_L:
                    sl, cat = arg; x = pop(); lst = L[sl]
                    # 解释器的临时变量（如上一次 CALL 的参数列表 a）可能还引用着这个列表，先清掉再数引用
                    u = v = b = a = r = f = recv = o = k = parts = fs = fields = vals = st = d = e = None
                    if cat:
                        if type(lst) is list and type(x) is list and _rc(lst) == 3:
                            lst.extend(x)
                        elif type(lst) is int and type(x) is int:
                            r = lst + x
                            if not -9223372036854775808 <= r <= 9223372036854775807: raise KiteError("整数溢出")
                            L[sl] = r
                        else:
                            L[sl] = rt.binop("+", lst, x, None)
                    else:
                        if type(lst) is list and _rc(lst) == 3 and G.get("push") is self.bpush:
                            lst.append(x)
                        else:
                            L[sl] = rt.call(G["push"], [lst, x], None)
                    x = lst = None
                elif op == SPAWN:
                    if arg:
                        fs = stack[-arg:]; del stack[-arg:]
                        push(tuple(spawn_all([lambda f=f: self.execute(f, []) for f in fs]).results))
                    else:
                        push(Unit)
                elif op == DEF_G:
                    G[arg] = pop()
                elif op == JIF_OR_POP or op == JIT_OR_POP:
                    v = stack[-1]
                    if type(v) is not bool:
                        raise KiteError(f"条件必须是 Bool，实际是 {type_name(v)}")
                    if v is (op == JIT_OR_POP):
                        pc = arg
                    else:
                        pop()
                elif op == NOT:
                    v = stack[-1]
                    if type(v) is not bool:
                        raise KiteError(f"条件必须是 Bool，实际是 {type_name(v)}")
                    stack[-1] = not v
                elif op == NEG:
                    v = stack[-1]
                    if type(v) not in (int, float):
                        raise KiteError(f"不能对 {type_name(v)} 取负")
                    if v == -9223372036854775808 and type(v) is int: raise KiteError("整数溢出")
                    stack[-1] = -v
                elif op == INDEX:
                    i = pop(); o = stack[-1]
                    try:
                        stack[-1] = o[i]
                    except (IndexError, KeyError):
                        raise KiteError(f"下标越界: {show(i, True)}")
                    except TypeError:
                        raise KiteError(f"{type_name(o)} 不支持下标")
                elif op == TIDX:
                    o = stack[-1]
                    if type(o) is tuple: stack[-1] = o[arg]
                    elif type(o) is EnumVal: stack[-1] = o.values[arg]
                    else: raise KiteError(f"{type_name(o)} 不能用 .{arg} 取元素")
                elif op == TRY:
                    v = stack[-1]
                    if type(v) is not EnumVal or v.variant not in ("Ok", "Err", "Some", "None"):
                        raise KiteError("? 只能用于 Result 或 Option")
                    if v.variant == "Ok" or v.variant == "Some":
                        stack[-1] = v.values[0]
                    else:
                        return v
                elif op == STRUCT:
                    name, fields = arg
                    n = len(fields)
                    vals = stack[-n:] if n else []
                    if n: del stack[-n:]
                    st = G.get(name)
                    if type(st) is not StructType:
                        raise KiteError(f"未知结构体 {name}")
                    d = dict(zip(fields, vals))
                    missing = [f for f in st.fields if f not in d]
                    extra = [f for f in d if f not in st.fields]
                    if missing: raise KiteError(f"{name} 缺少字段: {', '.join(missing)}")
                    if extra: raise KiteError(f"{name} 没有字段: {', '.join(extra)}")
                    push(StructVal(name, {f: d[f] for f in st.fields}))
                elif op == WFIELD:
                    v = pop(); o = stack[-1]
                    if type(o) is not StructVal:
                        raise KiteError("只能给结构体字段赋值")
                    nf = dict(o.fields); nf[arg] = v
                    stack[-1] = StructVal(o.tname, nf)
                elif op == WINDEX_L:
                    x = pop(); i = pop(); lst = L[arg]
                    u = v = b = a = r = f = recv = o = k = parts = fs = fields = vals = st = d = e = None
                    if type(lst) is list and _rc(lst) == 3:
                        lst[i] = x
                    elif type(lst) is PMap:
                        L[arg] = lst.set(i, x)
                    else:
                        cp = list(lst); cp[i] = x; L[arg] = cp
                    x = lst = None
                elif op == WINDEX:
                    v = pop(); i = pop(); o = stack[-1]
                    if type(o) is PMap: stack[-1] = o.set(i, v)
                    else:
                        cp = list(o); cp[i] = v; stack[-1] = cp
                elif op == ITER:
                    v = pop()
                    L[arg] = iter(v.items() if type(v) is PMap else v.keys() if type(v) is PSet else v)
                elif op == POPN:
                    del stack[-arg:]
                elif op == MATCH_FAIL:
                    raise KiteError(f"match 没有覆盖值 {show(stack[-1], True)}")
                elif op == DEFER:
                    defers.append(pop())
                elif op == RUN_DEFERS:
                    while len(defers) > arg:
                        self.execute(defers.pop(), [])
                else:
                    raise KiteError(f"未知指令 {op}")
        except KiteError as e:
            if e.line is None:
                e.line = lines[pc - 1] if pc else None
                e.args = (f"{e.line}: {e.msg}",)
            raise
        except ZeroDivisionError:
            raise KiteError("除以零", lines[pc - 1])
        except TypeError as ex:
            raise KiteError(f"运行时类型错误: {ex}", lines[pc - 1])
        finally:
            while defers:
                self.execute(defers.pop(), [])

    def call_method(self, recv, name, args, line):
        if isinstance(recv, (Module, EnumType)):
            return self.call_value(self.rt.get_field(recv, name, line), args, line)
        tbl = self.methods.get(type_name(recv))
        if tbl and name in tbl:
            return self.execute(tbl[name], [recv] + args)
        if type(recv) is StructVal and name in recv.fields:
            return self.call_value(recv.fields[name], args, line)
        f = self.globals.get(name)
        if f is not None and isinstance(f, (Closure, Builtin, KFunc)):
            return self.call_value(f, [recv] + args, line)
        raise KiteError(f"{type_name(recv)} 没有方法 {name}", line)


# ───────────────────────── 反汇编 ─────────────────────────
def disassemble(proto, out=sys.stdout, seen=None, vm=None):
    seen = set() if seen is None else seen
    if id(proto) in seen:
        return
    seen.add(id(proto))
    print(f"\n== {proto.name}  参数 {proto.nparams}  局部槽 {proto.nlocals}  upvalue {len(proto.updesc)} ==", file=out)
    nested = []
    last_line = None
    for i, ((op, arg), ln) in enumerate(zip(proto.code, proto.lines)):
        lt = f"{ln:>4}" if ln != last_line else "   |"
        last_line = ln
        if isinstance(arg, Proto):
            a = f"<{arg.name}>"; nested.append(arg)
        elif op == CONST:
            a = show(arg, True)
        elif op in (TEST_PAT, MATCH_OR_FAIL):
            a = _show_pat(arg)
        elif arg is None:
            a = ""
        else:
            a = str(arg)
        print(f"{lt} {i:>5}  {OPNAMES[op]:<14}{a}", file=out)
    for p in nested:
        disassemble(p, out, seen)
    if vm is not None and proto.name == "<main>":
        for g, v in vm.globals.items():
            if type(v) is Closure:
                disassemble(v.proto, out, seen)
        for t, tbl in vm.methods.items():
            for m, c in tbl.items():
                c.proto.name = f"{t}.{m}" if "." not in c.proto.name else c.proto.name
                disassemble(c.proto, out, seen)


def _show_pat(p):
    t = p[0]
    if t == "bind": return f"${p[1]}"
    if t == "gbind": return f"@{p[1]}"
    if t == "wild": return "_"
    if t == "lit": return show(p[1], True)
    if t == "tuple": return "(" + ", ".join(_show_pat(x) for x in p[1]) + ")"
    if t == "variant": return p[1] + ("" if p[2] is None else "(" + ", ".join(_show_pat(x) for x in p[2]) + ")")
    if t == "list": return "[" + ", ".join(_show_pat(x) for x in p[1]) + (", .." if p[2] else "") + "]"
    if t == "struct": return p[1] + " {" + ", ".join(f"{f}: {_show_pat(x)}" for f, x in p[2]) + "}"
    if t == "range": return f"{p[1]}..{'=' if p[3] else ''}{p[2]}"
    if t == "or": return " | ".join(_show_pat(x) for x in p[1])
    if t == "at": return f"{_show_pat(p[1])} @ {_show_pat(p[2])}"
    return str(p)
