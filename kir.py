"""KIR：Kite 的带类型中间表示（M1）

流程
  带类型的 AST（类型检查器在每个表达式节点上记下 _t）─lower─▶ KIR ─passes─▶ KIR ─emit_c─▶ C

KIR 是基于基本块的寄存器式 IR：
  - 每个虚拟寄存器 vN 有固定类型：i64 / f64 / b1（不装箱的标量）或 val（装箱的任意值）；Unit 不占寄存器
  - 指令  vD = op 参数...（参数是寄存器或常量）
  - 终结  br bK | cbr vC, bT, bF | ret [v] | panic "消息" | unreachable
  - 局部变量可以多次赋值（类似 Cranelift 的 Variable）；临时值只赋值一次
  - 类型由检查器决定：标量表达式全程不装箱，只在和 val 世界交界处插入 box / unbox

覆盖范围（第二阶段）：非泛型和泛型的顶层函数，函数体可以用
  标量与装箱运算、字符串插值、列表 / 元组 / 结构体字面量、字段 / 下标 / 元组下标、
  let / var（含解构）、赋值给局部变量、if / while / loop / for（区间循环编译成计数循环）、
  break / continue / return、任意模式的 match 和 is、?、直接 / 间接 / 方法调用、管道。
不支持（整个函数回退到字节码路径）：lambda、嵌套定义、defer、spawn / race / select、
给字段 / 下标 / 全局变量赋值。两条路径通过装箱包装函数互相调用。
"""
from typecheck import prune, TCon, INT as INT_T

SCALAR = {"Int": "i64", "Float": "f64", "Bool": "b1"}
CTYPE = {"i64": "int64_t", "f64": "double", "b1": "bool", "val": "V", "cell": "KUp *"}
FOLDABLE = {"copy", "add.i64", "sub.i64", "mul.i64", "div.i64", "rem.i64", "add.f64", "sub.f64", "mul.f64",
            "div.f64", "neg.i64", "neg.f64", "not", "lt", "gt", "le", "ge", "eq", "ne", "i2f"}
# 删除后不会改变程序行为的指令（不会出错、没有副作用）
NO_FAIL = {"copy", "add.f64", "sub.f64", "mul.f64", "neg.f64", "not", "lt", "gt", "le", "ge", "eq", "ne",
           "i2f", "abs.f64", "min", "max", "box", "unbox", "list", "tuple", "pbget", "payload", "b2i", "cget",
           "bit.band", "bit.bor", "bit.bxor", "bit.bnot"}


class Unsupported(Exception):
    pass


class Reg:
    __slots__ = ("id", "ty", "name")
    def __init__(self, id, ty, name=None): self.id, self.ty, self.name = id, ty, name
    def __repr__(self): return f"v{self.id}"


class Const:
    __slots__ = ("v", "ty")
    def __init__(self, v, ty): self.v, self.ty = v, ty
    def __repr__(self):
        if self.ty == "b1": return "true" if self.v else "false"
        if self.ty == "val": return "()" if self.v is UNIT_V else repr(self.v)
        return repr(self.v)
    def __eq__(self, o): return isinstance(o, Const) and o.ty == self.ty and o.v == self.v and type(o.v) is type(self.v)
    def __hash__(self): return hash((self.ty, repr(self.v)))


class _UnitV:
    def __repr__(self): return "()"
UNIT_V = _UnitV()


class Instr:
    __slots__ = ("dst", "op", "args", "line", "extra")
    def __init__(self, dst, op, args, line, extra=None):
        self.dst, self.op, self.args, self.line, self.extra = dst, op, list(args), line, extra


class Block:
    def __init__(self, id):
        self.id, self.instrs, self.term = id, [], None
    # term: ("br", b) | ("cbr", v, bt, bf) | ("ret", v|None) | ("panic", msg, v|None, line) | ("unreachable",)


class KFunc:
    def __init__(self, name, ret):
        self.name, self.ret = name, ret
        self.params, self.blocks, self.nreg = [], [], 0
        self.line = 0
        self.npb = 0          # 模式绑定槽位数
        self.pats = []        # [(编译后的模式, 行)]
        self.ndefer = 0       # defer 语句个数（> 0 时登记栈帧，返回和展开时执行）
        self.closure = False  # lambda：以 KFn 调用约定生成，捕获的值从 self->ups 读取
        self.caps = []        # lambda 捕获的寄存器
        self.lambdas = []     # 本函数（含嵌套）里的 lambda

    def reg(self, ty, name=None):
        r = Reg(self.nreg, ty, name); self.nreg += 1; return r

    def block(self):
        b = Block(len(self.blocks)); self.blocks.append(b); return b


def kty(t):
    """检查器类型 → KIR 类型"""
    t = prune(t)
    if isinstance(t, TCon) and not t.args:
        if t.name in SCALAR: return SCALAR[t.name]
        if t.name == "Unit": return "unit"
        if t.name == "Never": return "never"
    return "val"


def ty_of(e):
    t = getattr(e, "_t", None)
    if t is None:
        # 检查器改写时合成的节点（如 a > b → a.cmp(b) > 0 里的 0）或类型名（Shape.Rect 里的 Shape）
        if e.kind == "Lit":
            v = e.value
            return "b1" if isinstance(v, bool) else "i64" if isinstance(v, int) else "f64" if isinstance(v, float) else "val"
        return "val"
    return kty(t)


BITF = {"__band": ("ab", "&"), "__bor": ("ab", "|"), "__bxor": ("ab", "^"), "__bnot": ("a", "~"),
        "__shl": ("ab", "<<"), "__shr": ("ab", ">>")}


def _walk_nodes(x, out):
    from kite import Node
    if isinstance(x, Node):
        out.append(x)
        for k, v in x.__dict__.items():
            if k not in ("kind", "line", "_t"): _walk_nodes(v, out)
    elif isinstance(x, (list, tuple)):
        for v in x: _walk_nodes(v, out)
    return out


def _assigned_names(body):
    """函数体里被赋值或用 var 声明的名字（闭包不能捕获它们）"""
    out = set()
    for n in _walk_nodes(body, []):
        if n.kind == "Assign" and n.target.kind == "Name": out.add(n.target.name)
        if n.kind == "Let" and n.mutable: out.update(_pat_names(n.pat))
        if n.kind == "For": pass
    return out


def _inplace_names(fn_node):
    """可以原地写下标的局部名字：除了 x[i]、x[i] = v、len(x)、x = e、return x / 函数体末尾的 x，
    名字从不以别的方式出现（不会被别名、传参、捕获、迭代），且只由参数或 let/var 简单绑定"""
    body = fn_node.body
    nodes = _walk_nodes(body, [])
    ok, bad = set(), set()
    for n in nodes:
        k = n.kind
        if k == "Index" and n.obj.kind == "Name": ok.add(id(n.obj))
        elif k == "Call" and n.fn.kind == "Name" and n.fn.name == "len" and len(n.args) == 1 and n.args[0].kind == "Name":
            ok.add(id(n.fn)); ok.add(id(n.args[0]))
        elif k == "Assign" and n.target.kind == "Name": ok.add(id(n.target))
        elif k == "Return" and getattr(n.value, "kind", None) == "Name": ok.add(id(n.value))
        elif k == "For": bad.update(_pat_names(n.pat))
        elif k == "Match":
            for arm in n.arms: bad.update(_pat_names(arm[0]))
        elif k == "Let" and n.pat[0] != "bind": bad.update(_pat_names(n.pat))
    st = getattr(body, "stmts", None)
    if st and st[-1].kind == "ExprStmt" and st[-1].expr.kind == "Name": ok.add(id(st[-1].expr))
    names = set()
    for n in nodes:
        if n.kind == "Name":
            names.add(n.name)
            if id(n) not in ok: bad.add(n.name)
    return names - bad - _captured_names(body)


def _captured_names(body):
    """在 lambda / defer / spawn / race 里出现的名字"""
    out = set()
    for n in _walk_nodes(body, []):
        if n.kind in ("Lambda", "Fn"): inner = [n.body]
        elif n.kind == "Defer": inner = [n.body]
        elif n.kind in ("Spawn", "Race"): inner = list(n.items)
        else: continue
        for x in _walk_nodes(inner, []):
            if x.kind == "Name": out.add(x.name)
    return out


def _pat_names(p):
    k = p[0]
    if k == "bind": return [p[1]]
    if k == "tuple": return [n for x in p[1] for n in _pat_names(x)]
    if k == "list": return [n for x in p[1] for n in _pat_names(x)] + ([p[2]] if p[2] and p[2] != "_" else []) + \
        [n for x in (p[3] if len(p) > 3 else []) for n in _pat_names(x)]
    if k == "variant" and p[2]: return [n for x in p[2] for n in _pat_names(x)]
    if k == "struct": return [n for _, x in p[2] for n in _pat_names(x)]
    if k == "or": return _pat_names(p[1][0])
    if k == "at": return _pat_names(p[1]) + _pat_names(p[2])
    return []


# ───────────────────────── AST → KIR ─────────────────────────
class Lower:
    def __init__(self, fn_node, sigs, key=None):
        key = key or fn_node.name
        self.node, self.sigs = fn_node, sigs          # sigs: 名字 → ([参数类型], 返回类型)
        ptys, ret = sigs[key]
        self.f = KFunc(key, ret)
        self.f.line = fn_node.line
        self.scopes = [{}]
        for (name, _), ty in zip(fn_node.params, ptys):
            r = self.f.reg(ty, name); self.f.params.append(r); self.scopes[-1][name] = r
        self.cur = self.f.block()
        self.mutable_names = _assigned_names(fn_node.body)
        self.cell_names = _captured_names(fn_node.body) & self.mutable_names
        for name, _ in fn_node.params: self.maybe_cell(name, fn_node.line)
        self.loops = []     # (continue 块, break 块)
        self.user_names = set()
        self.lambdas = self.f.lambdas
        self.captured = set()
        try: self.inplace = _inplace_names(fn_node)
        except Exception: self.inplace = set()
        self.nd = 0              # 当前作用域里已登记的 defer 个数（编译期）

    def run(self):
        body = self.node.body
        ret = self.f.ret
        v = self.block(body, want=ret not in ("unit", "never"))
        if self.cur is not None:
            self.term(("ret", self.coerce(v, ret, body.line) if ret not in ("unit", "never") else None))
        return self.f

    # 工具
    def emit(self, op, args, ty, line, extra=None):
        if self.cur is None: self.cur = self.f.block()   # 不可达代码
        dst = self.f.reg(ty) if ty not in (None, "unit", "never") else None
        self.cur.instrs.append(Instr(dst, op, args, line, extra))
        return dst

    def term(self, t):
        if self.cur is None: return
        self.cur.term = t; self.cur = None

    def goto(self, b):
        self.term(("br", b.id))

    def lookup(self, name):
        for s in reversed(self.scopes):
            if name in s: return s[name]
        return None

    def local_fns(self, fns, line):
        """块里的 fn 定义：先为每个名字建好单元（支持递归和相互递归），再创建闭包写进去"""
        if not fns: return
        for fn in fns:
            if fn.body is None: raise Unsupported("没有函数体的局部 fn")
            c = self.emit("mkcell", [Const(UNIT_V, "val")], "cell", fn.line); c.name = fn.name
            self.scopes[-1][fn.name] = c
        old = self.cell_names
        self.cell_names = old | {fn.name for fn in fns}
        for fn in fns:
            sig = getattr(fn, "_sig", None)
            st = prune(sig) if sig is not None else None
            if isinstance(st, TCon) and st.name == "fn" and len(st.args) == len(fn.params) + 1:
                ptys = ["val" if t in ("unit", "never") else t for t in (kty(a) for a in st.args[:-1])]
                ret = kty(st.args[-1])
            else:
                ptys, ret = ["val"] * len(fn.params), "val"
            cl = self.make_closure(fn.params, ptys, fn.body, ret, fn.line, f"<{fn.name}>")
            self.emit("cset", [self.scopes[-1][fn.name], cl], None, fn.line)

    def maybe_cell(self, name, line):
        """被闭包捕获又会被修改的变量放进 upvalue 单元（与字节码的 upvalue 语义相同）"""
        if name not in self.cell_names: return
        r = self.scopes[-1].get(name)
        if r is None or r.ty == "cell": return
        c = self.emit("mkcell", [self.coerce(r, "val", line)], "cell", line)
        c.name = name
        self.scopes[-1][name] = c

    def coerce(self, v, ty, line):
        """把值转换成需要的 KIR 类型（标量 ↔ val）；Unit 需要值时用 ()"""
        if ty in ("unit", "never"): return None
        if v is None:
            if ty == "val": return Const(UNIT_V, "val")
            if self.cur is None: return Const(0, ty) if ty != "f64" else Const(0.0, "f64")
            raise Unsupported("Unit 值用在标量位置")
        if v.ty == ty: return v
        if ty == "val":
            return self.emit("box", [v], "val", line)
        if v.ty == "val":
            return self.emit("unbox", [v], ty, line)
        raise Unsupported(f"类型转换 {v.ty} → {ty}")

    def emit_copy(self, dst, v, line):
        v = self.coerce(v, dst.ty, line)
        if self.cur is None: self.cur = self.f.block()
        self.cur.instrs.append(Instr(dst, "copy", [v], line))

    # 模式：编译成与字节码后端相同的形式（绑定写入本函数的 PB 槽位）
    def cpat(self, p, binds):
        k = p[0]
        if k == "bind":
            if getattr(self, "_in_or", 0):   # 或模式的各分支共用同名变量的槽位
                for n, sl in binds:
                    if n == p[1]: return ("bind", sl)
            slot = self.f.npb; self.f.npb += 1; binds.append((p[1], slot)); return ("bind", slot)
        if k == "or":
            self._in_or = getattr(self, "_in_or", 0) + 1
            try: return ("or", [self.cpat(x, binds) for x in p[1]])
            finally: self._in_or -= 1
        if k == "at": return ("at", self.cpat(p[1], binds), self.cpat(p[2], binds))
        if k == "tuple": return ("tuple", [self.cpat(x, binds) for x in p[1]])
        if k == "list":
            rest = p[2]
            pre = [self.cpat(x, binds) for x in p[1]]
            r = None if rest is None else ("wild",) if rest == "_" else self.cpat(("bind", rest), binds)
            if len(p) > 3: return ("list", pre, r, [self.cpat(x, binds) for x in p[3]])
            return ("list", pre, r)
        if k == "variant":
            return ("variant", p[1], None if p[2] is None else [self.cpat(x, binds) for x in p[2]])
        if k == "struct":
            return ("struct", p[1], [(f, self.cpat(x, binds)) for f, x in p[2]])
        return p

    def test_pat(self, pat, subj, line):
        """返回 (b1 结果, [(名字, 槽位)])；绑定在匹配成功后用 bind_pat 取出"""
        binds = []
        cp = self.cpat(pat, binds)
        idx = len(self.f.pats); self.f.pats.append(cp)
        v = self.coerce(subj, "val", line)
        return self.emit("tpat", [v], "b1", line, extra=idx), binds

    def bind_pat(self, binds, line):
        for name, slot in binds:
            r = self.f.reg("val", name)
            self.cur.instrs.append(Instr(r, "pbget", [], line, extra=slot))
            self.scopes[-1][name] = r; self.maybe_cell(name, line)

    def destructure(self, pat, v, line, msg="解构失败: %s"):
        """let / for 的模式：失败就报错"""
        if pat[0] == "bind":
            r = self.f.reg(v.ty if v is not None else "val", pat[1])
            self.emit_copy(r, v, line); self.scopes[-1][pat[1]] = r; self.maybe_cell(pat[1], line); return
        if pat[0] == "wild": return
        v = self.coerce(v, "val", line)
        ok, binds = self.test_pat(pat, v, line)
        good, bad = self.f.block(), self.f.block()
        self.term(("cbr", ok, good.id, bad.id))
        self.cur = bad; self.term(("panic", msg, v, line))
        self.cur = good
        self.bind_pat(binds, line)

    # 语句与块
    def block(self, b, want):
        self.scopes.append({})
        nd0 = self.nd
        try:
            v = None
            stmts = b.stmts
            self.local_fns([s for s in stmts if s.kind == "Fn"], b.line)
            for i, s in enumerate(stmts):
                if s.kind == "Fn": continue
                if s.kind in ("Struct", "Enum", "Trait", "Impl", "Alias", "Use"):
                    continue   # 局部类型和局部 use 在编译期就登记好了（与字节码相同），运行时什么也不做
                if s.kind == "Mod":
                    raise Unsupported("局部 mod")
                last = i == len(stmts) - 1
                if last and want and s.kind == "ExprStmt":
                    v = self.expr(s.expr)
                else:
                    self.stmt(s)
            if self.nd > nd0 and self.cur is not None:
                self.emit("rundefers", [], None, b.line, extra=nd0)
            return v
        finally:
            self.scopes.pop(); self.nd = nd0

    def stmt(self, s):
        k = s.kind
        if k == "ExprStmt":
            e = s.expr
            self.expr(e, want=e.kind not in ("If", "Match", "Block") and ty_of(e) not in ("unit", "never"))
            return
        if k == "Let":
            ty = ty_of(s.value)
            v = self.expr(s.value, want=ty not in ("unit", "never"))
            if self.cur is None and ty == "never": return
            if s.pat[0] == "bind" and ty in ("unit", "never"):
                ty = "val"
            if s.pat[0] == "bind":
                r = self.f.reg(ty, s.pat[1])
                self.emit_copy(r, v, s.line)
                self.scopes[-1][s.pat[1]] = r; self.maybe_cell(s.pat[1], s.line)
                return
            self.destructure(s.pat, v, s.line)
            return
        if k == "Assign":
            t = s.target
            if t.kind != "Name": return self.assign_path(s)
            r = self.lookup(t.name)
            if r is None: raise Unsupported("赋值给全局变量")
            if r.ty == "cell":
                v = self.expr(s.value)
                if s.op != "=":
                    cur = self.coerce(self.emit("cget", [r], "val", s.line), ty_of(s.value), s.line)
                    v = self.binop(s.op[0], cur, v, ty_of(s.value), ty_of(s.value), ty_of(s.value), s.line)
                self.emit("cset", [r, self.coerce(v, "val", s.line)], None, s.line)
                return
            if r.id in self.captured: raise Unsupported("闭包修改捕获的变量")
            v = self.expr(s.value)
            if s.op != "=":
                v = self.binop(s.op[0], r, v, ty_of(s.value), ty_of(s.value), r.ty, s.line)
            self.emit_copy(r, v, s.line)
            return
        if k == "While":
            head, body, done = self.f.block(), self.f.block(), self.f.block()
            self.goto(head); self.cur = head
            if s.cond is not None:
                c = self.coerce(self.expr(s.cond), "b1", s.line)
                self.term(("cbr", c, body.id, done.id))
            else:
                self.goto(body)
            self.cur = body
            self.loops.append((head, done, self.nd, getattr(s, "label", None), None))
            self.block(s.body, want=False)
            self.loops.pop()
            self.goto(head)
            self.cur = done
            return
        if k == "For":
            return self.for_(s)
        if k == "Break":
            lp = self.loop_target(s)
            val = getattr(s, "value", None)
            if val is not None or lp[4] is not None:
                if lp[4] is None: raise Unsupported("while / for 的 break 带值")
                v = self.expr(val) if val is not None else None
                if lp[4][0] is not None and self.cur is not None:
                    self.emit_copy(lp[4][0], v if v is not None else Const(UNIT_V, "val"), s.line)
            if self.cur is None: return
            self.unwind_defers(lp[2], s.line)
            self.goto(lp[1]); return
        if k == "Continue":
            lp = self.loop_target(s)
            self.unwind_defers(lp[2], s.line)
            self.goto(lp[0]); return
        if k == "Defer":
            c = self.make_closure([], [], s.body, "unit", s.line, "<defer>")
            self.emit("defer", [c], None, s.line)
            self.nd += 1; self.f.ndefer += 1
            return
        if k == "Return":
            ret = self.f.ret
            if s.value is not None:
                v = self.expr(s.value, want=ret not in ("unit", "never"))
                self.term(("ret", self.coerce(v, ret, s.line) if ret not in ("unit", "never") else None))
            else:
                self.term(("ret", None if ret in ("unit", "never") else self.coerce(None, ret, s.line)))
            return
        raise Unsupported(f"语句 {k}")

    def assign_path(self, s):
        """a.b[i].c = v：值语义，逐层用 k_wfield / k_windex 生成新值再写回根变量（与字节码同序求值）"""
        t = s.target
        root = t
        while root.kind in ("Field", "Index"): root = root.obj
        if root.kind != "Name": raise Unsupported("赋值目标")
        r = self.lookup(root.name)
        if r is None: raise Unsupported("赋值给全局变量")
        if r.id in self.captured and r.ty != "cell": raise Unsupported("闭包修改捕获的变量")
        line = s.line

        def leaf():
            if s.op == "=": return self.expr(s.value)
            a = self.expr(t); b = self.expr(s.value)
            return self.binop(s.op[0], a, b, ty_of(t), ty_of(s.value), ty_of(t), line)

        def update(node, produce):
            if node.kind == "Name":
                if r.ty == "cell": self.emit("cset", [r, self.coerce(produce(), "val", line)], None, line)
                else: self.emit_copy(r, produce(), line)
                return
            if node.kind == "Field":
                def p2():
                    o = self.coerce(self.expr(node.obj), "val", line)
                    v = self.coerce(produce(), "val", line)
                    return self.emit("wfield", [o, v], "val", line, extra=node.name)
            else:
                def p2():
                    o = self.coerce(self.expr(node.obj), "val", line)
                    i = self.coerce(self.expr(node.index), "val", line)
                    v = self.coerce(produce(), "val", line)
                    if node.obj.kind == "Name" and node.obj.name in self.inplace and r.ty == "val":
                        return self.emit("windexl", [o, i, v], "val", line, extra=r.id)
                    return self.emit("windex", [o, i, v], "val", line)
            update(node.obj, p2)
        update(t, leaf)

    def loop_target(self, s):
        lab = getattr(s, "label", None)
        for lp in reversed(self.loops):
            if lab is None or lp[3] == lab: return lp
        raise Unsupported(f"循环外的 {s.kind.lower()}")

    def unwind_defers(self, n, line):
        if self.nd > n and self.cur is not None:
            self.emit("rundefers", [], None, line, extra=n)

    def for_(self, s):
        it = s.iter
        # for i in a..b（Int）：计数循环，不分配列表
        if it.kind == "Bin" and it.op in ("..", "..=") and ty_of(it.left) == "i64" and ty_of(it.right) == "i64" \
                and s.pat[0] in ("bind", "wild"):
            lo = self.coerce(self.expr(it.left), "i64", s.line)
            hi = self.coerce(self.expr(it.right), "i64", s.line)
            i = self.f.reg("i64"); self.emit_copy(i, lo, s.line)
            hreg = self.f.reg("i64"); self.emit_copy(hreg, hi, s.line)
            head, body, step, done = self.f.block(), self.f.block(), self.f.block(), self.f.block()
            self.goto(head); self.cur = head
            c = self.emit("le" if it.op == "..=" else "lt", [i, hreg], "b1", s.line)
            self.term(("cbr", c, body.id, done.id))
            self.cur = body
            self.scopes.append({})
            if s.pat[0] == "bind":
                x = self.f.reg("i64", s.pat[1]); self.emit_copy(x, i, s.line); self.scopes[-1][s.pat[1]] = x; self.maybe_cell(s.pat[1], s.line)
            self.loops.append((step, done, self.nd, getattr(s, "label", None), None))
            self.block(s.body, want=False)
            self.loops.pop(); self.scopes.pop()
            self.goto(step); self.cur = step
            if it.op == "..=":
                last = self.emit("eq", [i, hreg], "b1", s.line)
                nx = self.f.block(); self.term(("cbr", last, done.id, nx.id)); self.cur = nx
            self.cur.instrs.append(Instr(i, "inc", [i], s.line))
            self.goto(head)
            self.cur = done
            return
        src = self.coerce(self.expr(it), "val", s.line)
        itr = self.emit("iter", [src], "val", s.line)
        head, body, done = self.f.block(), self.f.block(), self.f.block()
        self.goto(head); self.cur = head
        item = self.f.reg("val")
        ok = self.emit("next", [itr], "b1", s.line, extra=item)
        self.term(("cbr", ok, body.id, done.id))
        self.cur = body
        self.scopes.append({})
        el = self.elem_ty(it)
        if s.pat[0] == "bind" and el in ("i64", "f64", "b1"):
            x = self.f.reg(el, s.pat[1]); self.emit_copy(x, item, s.line); self.scopes[-1][s.pat[1]] = x; self.maybe_cell(s.pat[1], s.line)
        else:
            self.destructure(s.pat, item, s.line)
        self.loops.append((head, done, self.nd, getattr(s, "label", None), None))
        self.block(s.body, want=False)
        self.loops.pop(); self.scopes.pop()
        self.goto(head)
        self.cur = done

    def elem_ty(self, it):
        t = prune(getattr(it, "_t", None)) if getattr(it, "_t", None) is not None else None
        if isinstance(t, TCon) and t.name in ("List", "Chan") and t.args:
            return kty(t.args[0])
        return "val"

    # 表达式：返回寄存器 / 常量（Unit 返回 None），并按节点类型转换
    def expr(self, e, want=True):
        v = self._expr(e, want)
        if not want: return v
        ty = ty_of(e)
        if ty in ("unit", "never"): return v
        if v is None and self.cur is None: return None
        return self.coerce(v, ty, e.line)

    def _expr(self, e, want):
        k = e.kind
        if k == "Lit":
            v = e.value
            if isinstance(v, bool): return Const(v, "b1")
            if isinstance(v, int):
                if not -(1 << 63) <= v < (1 << 63): raise Unsupported("整数常量超出 64 位")
                return Const(v, "i64")
            if isinstance(v, float): return Const(v, "f64")
            return Const(v, "val")
        if k == "Name":
            r = self.lookup(e.name)
            if r is not None:
                if r.ty == "cell": return self.emit("cget", [r], "val", e.line)
                return r
            return self.emit("gload", [], "val", e.line, extra=e.name)
        if k == "Block":
            return self.block(e, want)
        if k == "Bin":
            if e.op in ("&&", "||"): return self.logic(e)
            a = self.expr(e.left); b = self.expr(e.right)
            return self.binop(e.op, a, b, ty_of(e.left), ty_of(e.right), ty_of(e), e.line)
        if k == "Unary":
            a = self.expr(e.operand)
            if e.op == "!": return self.emit("not", [self.coerce(a, "b1", e.line)], "b1", e.line)
            if a.ty == "i64": return self.emit("neg.i64", [a], "i64", e.line)
            if a.ty == "f64": return self.emit("neg.f64", [a], "f64", e.line)
            return self.emit("vneg", [self.coerce(a, "val", e.line)], "val", e.line)
        if k == "Loop":
            r = self.result(e, want)
            head, done = self.f.block(), self.f.block()
            self.goto(head); self.cur = head
            self.loops.append((head, done, self.nd, e.label, (r,)))
            self.block(e.body, want=False)
            self.loops.pop()
            if self.cur is not None: self.goto(head)
            self.cur = done
            return r
        if k == "If":
            return self.if_(e, want)
        if k == "Match":
            return self.match(e, want)
        if k == "Is":
            v = self.expr(e.value)
            ok, _ = self.test_pat(e.pat, v, e.line)
            return ok
        if k == "Call":
            return self.call(e.fn, list(e.args), None, e.line, ty_of(e))
        if k == "Pipe":
            x = self.expr(e.left)
            r = e.right
            if r.kind == "Call": return self.call(r.fn, list(r.args), x, e.line, ty_of(e))
            return self.call(r, [], x, e.line, ty_of(e))
        if k == "Tuple":
            if not e.items: return Const(UNIT_V, "val") if want else None
            vs = [self.coerce(self.expr(x), "val", e.line) for x in e.items]
            return self.emit("tuple", vs, "val", e.line)
        if k == "List":
            vs = [self.coerce(self.expr(x), "val", e.line) for x in e.items]
            return self.emit("list", vs, "val", e.line)
        if k == "StructLit":
            vs = [self.coerce(self.expr(x), "val", e.line) for _, x in e.fields]
            return self.emit("struct", vs, "val", e.line, extra=(e.name, tuple(f for f, _ in e.fields)))
        if k == "Interp":
            parts, vs = [], []
            for p in e.parts:
                if isinstance(p, str): parts.append(p)
                else: parts.append(None); vs.append(self.coerce(self.expr(p), "val", e.line))
            return self.emit("interp", vs, "val", e.line, extra=parts)
        if k == "Field":
            o = self.coerce(self.expr(e.obj), "val", e.line)
            return self.emit("field", [o], "val", e.line, extra=e.name)
        if k == "TupleIndex":
            o = self.coerce(self.expr(e.obj), "val", e.line)
            return self.emit("tidx", [o], "val", e.line, extra=e.index)
        if k == "Index":
            o = self.coerce(self.expr(e.obj), "val", e.line)
            i = self.coerce(self.expr(e.index), "val", e.line)
            return self.emit("index", [o, i], "val", e.line)
        if k == "Try":
            v = self.coerce(self.expr(e.value), "val", e.line)
            ok = self.emit("tryok", [v], "b1", e.line)
            good, bad = self.f.block(), self.f.block()
            self.term(("cbr", ok, good.id, bad.id))
            self.cur = bad
            if self.f.ret != "val": raise Unsupported("? 用在返回值不是 Result / Option 的函数里")
            self.term(("ret", v))
            self.cur = good
            return self.emit("payload", [v], "val", e.line)
        if k == "Await":
            return self.expr(e.value, want)
        if k == "Lambda":
            return self.lambda_(e)
        if k == "Select":
            from kite import lower_select
            return self._expr(lower_select(e), want)
        if k in ("Spawn", "Race"):
            cs = [self.make_closure([], [], x, ty_of(x), e.line, "<spawn>" if k == "Spawn" else "<race>") for x in e.items]
            if k == "Spawn":
                if not cs: return Const(UNIT_V, "val")
                return self.emit("spawn", cs, "val", e.line)
            lst = self.emit("list", cs, "val", e.line)
            return self.emit("callg", [lst], "val", e.line, extra="__race")
        raise Unsupported(f"表达式 {k}")

    def lambda_(self, e):
        ft = prune(getattr(e, "_t", None)) if getattr(e, "_t", None) is not None else None
        if not isinstance(ft, TCon) or ft.name != "fn" or len(ft.args) != len(e.params) + 1:
            return self.make_closure(e.params, ["val"] * len(e.params), e.body, "val", e.line, "<lambda>")
        ptys = [kty(a) for a in ft.args[:-1]]
        ptys = ["val" if t in ("unit", "never") else t for t in ptys]
        ret = kty(ft.args[-1])
        return self.make_closure(e.params, ptys, e.body, ret, e.line, "<lambda>")

    def make_closure(self, params, ptys, body, ret, line, label):
        """lambda / defer / spawn 的任务体：只捕获不可变变量"""
        pnames = {n for n, _ in params}
        caps = []
        for n in sorted({x.name for x in _walk_nodes(body, []) if x.kind == "Name"} - pnames):
            r = self.lookup(n)
            if r is None: continue
            if n in self.mutable_names and r.ty != "cell": raise Unsupported("闭包捕获可变变量")
            caps.append((n, r))
        lw = Lower.__new__(Lower)
        lw.node, lw.sigs, lw.user_names = None, self.sigs, self.user_names
        lw.mutable_names, lw.lambdas, lw.loops = self.mutable_names, self.lambdas, []
        lw.cell_names = self.cell_names
        lw.f = KFunc(f"{label[:-1]}@{line}>", ret); lw.f.closure = True; lw.f.line = line; lw.f.label = label
        lw.scopes = [{}]; lw.captured = set(); lw.nd = 0
        for n, r in caps:
            cr = lw.f.reg(r.ty, n); lw.f.caps.append(cr); lw.scopes[0][n] = cr; lw.captured.add(cr.id)
        lw.scopes.append({})
        for (n, _), t in zip(params, ptys):
            pr = lw.f.reg(t, n); lw.f.params.append(pr); lw.scopes[1][n] = pr
        lw.cur = lw.f.block()
        for n, _ in params: lw.maybe_cell(n, line)
        want = ret not in ("unit", "never")
        v = lw.block(body, want) if body.kind == "Block" else lw.expr(body, want)
        if lw.cur is not None:
            lw.term(("ret", lw.coerce(v, ret, line) if want else None))
        self.lambdas.append(lw.f)
        args = [r if r.ty == "cell" else self.coerce(r, "val", line) for _, r in caps]
        return self.emit("closure", args, "val", line, extra=lw.f)

    def binop(self, op, a, b, ta, tb, tr, line):
        if op in ("+", "-", "*", "/", "%") and ta == tb and ta in ("i64", "f64") and tr == ta:
            name = {"+": "add", "-": "sub", "*": "mul", "/": "div", "%": "rem"}[op]
            return self.emit(f"{name}.{ta}", [self.coerce(a, ta, line), self.coerce(b, ta, line)], ta, line)
        if op in ("<", ">", "<=", ">=", "==", "!=") and ta == tb and ta in ("i64", "f64", "b1"):
            name = {"<": "lt", ">": "gt", "<=": "le", ">=": "ge", "==": "eq", "!=": "ne"}[op]
            return self.emit(name, [self.coerce(a, ta, line), self.coerce(b, ta, line)], "b1", line)
        av = self.coerce(a, "val", line); bv = self.coerce(b, "val", line)
        return self.emit("vbin", [av, bv], "val", line, extra=op)

    def logic(self, e):
        r = self.f.reg("b1")
        self.emit_copy(r, self.expr(e.left), e.line)
        rhs, done = self.f.block(), self.f.block()
        if e.op == "&&": self.term(("cbr", r, rhs.id, done.id))
        else: self.term(("cbr", r, done.id, rhs.id))
        self.cur = rhs
        self.emit_copy(r, self.expr(e.right), e.line)
        self.goto(done); self.cur = done
        return r

    def result(self, e, want):
        ty = ty_of(e) if want else "unit"
        if ty in ("unit", "never"): return None
        return self.f.reg(ty)

    def if_(self, e, want):
        r = self.result(e, want)
        c = self.coerce(self.expr(e.cond), "b1", e.line)
        tb, eb, done = self.f.block(), self.f.block(), self.f.block()
        self.term(("cbr", c, tb.id, eb.id))
        self.cur = tb
        v = self.block(e.then, r is not None)
        if self.cur is not None:
            if r is not None: self.emit_copy(r, v, e.line)
            self.goto(done)
        self.cur = eb
        if e.els is not None:
            v = self.expr(e.els, r is not None) if e.els.kind != "Block" else self.block(e.els, r is not None)
            if self.cur is not None:
                if r is not None: self.emit_copy(r, v, e.line)
                self.goto(done)
        else:
            if r is not None: self.emit_copy(r, None, e.line)
            self.goto(done)
        self.cur = done
        return r

    def match(self, e, want):
        r = self.result(e, want)
        subj = self.expr(e.subject)
        if subj is None: subj = Const(UNIT_V, "val")
        st = subj.ty
        done = self.f.block()
        for pat, guard, body in e.arms:
            nxt = self.f.block()
            self.scopes.append({})
            k = pat[0]
            if k == "lit" and st in ("i64", "f64", "b1") and not isinstance(pat[1], str):
                v = pat[1]
                c = Const(float(v), "f64") if st == "f64" else Const(v, st)
                t = self.emit("eq", [subj, c], "b1", e.line)
                ok = self.f.block(); self.term(("cbr", t, ok.id, nxt.id)); self.cur = ok
            elif k == "range" and st in ("i64", "f64"):
                lo, hi, inc = pat[1], pat[2], pat[3]
                cv = (lambda x: Const(float(x), "f64")) if st == "f64" else (lambda x: Const(x, "i64"))
                if lo is not None:
                    t1 = self.emit("ge", [subj, cv(lo)], "b1", e.line)
                    ok1 = self.f.block(); self.term(("cbr", t1, ok1.id, nxt.id)); self.cur = ok1
                if hi is not None:
                    t2 = self.emit("le" if inc else "lt", [subj, cv(hi)], "b1", e.line)
                    ok = self.f.block(); self.term(("cbr", t2, ok.id, nxt.id)); self.cur = ok
            elif k == "bind" and len(pat) == 2:
                b = self.f.reg(st, pat[1]); self.emit_copy(b, subj, e.line); self.scopes[-1][pat[1]] = b; self.maybe_cell(pat[1], e.line)
            elif k == "wild":
                pass
            else:
                t, binds = self.test_pat(pat, subj, e.line)
                ok = self.f.block(); self.term(("cbr", t, ok.id, nxt.id)); self.cur = ok
                self.bind_pat(binds, e.line)
            if guard is not None:
                g = self.coerce(self.expr(guard), "b1", e.line)
                ok = self.f.block(); self.term(("cbr", g, ok.id, nxt.id)); self.cur = ok
            if body.kind == "Block":
                v = self.block(body, r is not None)
            else:
                v = self.expr(body, r is not None)
            if self.cur is not None:
                if r is not None: self.emit_copy(r, v, e.line)
                self.goto(done)
            self.scopes.pop()
            self.cur = nxt
        self.term(("panic", "match 没有覆盖值 %s", subj, e.line))
        self.cur = done
        return r

    def call(self, fn, args, pre, line, rty):
        """pre：管道左边的值（作为第一个参数，方法调用时排在接收者之后）"""
        if fn.kind == "Field":
            recv = self.coerce(self.expr(fn.obj), "val", line)
            vs = ([self.coerce(pre, "val", line)] if pre is not None else []) + \
                 [self.coerce(self.expr(a), "val", line) for a in args]
            r = self.emit("callm", [recv] + vs, "val", line, extra=fn.name)
            return self.after_call(r, rty)
        if fn.kind == "Name" and self.lookup(fn.name) is None:
            name = fn.name
            pv = [pre] if pre is not None else []
            if name in self.sigs:
                ptys, ret = self.sigs[name]
                raw = pv + [self.expr(a) for a in args]
                if len(ptys) != len(raw): raise Unsupported("参数个数")
                vs = [self.coerce(v, t, line) for v, t in zip(raw, ptys)]
                r = self.emit("call", vs, ret, line, extra=name)
                if ret == "never": self.term(("unreachable",))
                return r
            raw = pv + [self.expr(a) for a in args]
            tys = [v.ty if v is not None else "unit" for v in raw]
            if name in self.user_names: tys = ["user"] * len(raw)   # 用户定义的同名函数优先
            if name in BITF and len(raw) == len(BITF[name][0]) and all(t == "i64" for t in tys):
                return self.emit(f"bit.{name[2:]}", raw, "i64", line)
            if name == "abs" and len(raw) == 1 and tys[0] in ("i64", "f64"):
                return self.emit(f"abs.{tys[0]}", raw, tys[0], line)
            if name in ("min", "max") and len(raw) == 2 and tys[0] == tys[1] and tys[0] in ("i64", "f64"):
                return self.emit(name, raw, tys[0], line)
            if name == "float" and len(raw) == 1 and tys[0] in ("i64", "f64"):
                return raw[0] if tys[0] == "f64" else self.emit("i2f", raw, "f64", line)
            if name == "int" and len(raw) == 1 and tys[0] in ("i64", "f64", "b1"):
                if tys[0] == "i64": return raw[0]
                return self.emit("f2i" if tys[0] == "f64" else "b2i", raw, "i64", line)
            vs = [self.coerce(v, "val", line) for v in raw]
            r = self.emit("callg", vs, "val", line, extra=name)
            return self.after_call(r, rty)
        f = self.coerce(self.expr(fn), "val", line)
        vs = ([self.coerce(pre, "val", line)] if pre is not None else []) + \
             [self.coerce(self.expr(a), "val", line) for a in args]
        r = self.emit("callv", [f] + vs, "val", line)
        return self.after_call(r, rty)

    def after_call(self, r, rty):
        if rty == "never": self.term(("unreachable",)); return None
        if rty == "unit": return None
        return r


# ───────────────────────── 选择可编译的函数 ─────────────────────────
def collect(items):
    """返回 ({名字: KFunc}, {名字: 回退原因})。items 是已通过类型检查（节点带 _t）的顶层定义"""
    fns = {it.name: it for it in items
           if it.kind == "Fn" and it.body is not None and not getattr(it, "prelude", False)}
    sigs, why = {}, {}
    user = {it.name for it in items if it.kind in ("Fn", "Let") and not getattr(it, "prelude", False) and hasattr(it, "name")}
    for name, it in fns.items():
        sig = getattr(it, "_sig", None)
        if sig is None:
            why[name] = "没有签名"; continue
        st = prune(sig)
        ptys = [kty(a) for a in st.args[:-1]]
        ptys = ["val" if t in ("unit", "never") else t for t in ptys]
        ptys += [kty(t) for t in (getattr(it, "_cg_ptys", None) or [] if getattr(it, "_cg_added", False) else [])]
        sigs[name] = (ptys, kty(st.args[-1]))
    # impl 里自己写的方法（trait 的默认方法由多个 impl 共用节点，类型信息会被覆盖，不进 KIR）
    seen, snaps = {}, {}
    # 顶层 impl 加上函数体里的局部 impl（字节码 hoist 已把它们登记进 vm.methods，名字全局唯一或被下面的重名检查排除）
    impls = [it for it in items if it.kind == "Impl"]
    top = {id(it) for it in impls}
    for it in items:
        if it.kind in ("Fn", "Impl") and not getattr(it, "prelude", False):
            impls += [n for n in _walk_nodes(it, []) if n.kind == "Impl" and id(n) not in top]
    for it in impls:
        for m in getattr(it, "_all_methods", it.methods):
            if not any(m is x for x in it.methods):   # trait 默认方法：用这个 impl 的类型快照
                snap = getattr(m, "_tsnap", {}).get(it.target)
                if snap is None: continue
                snaps[f"{it.target}.{m.name}#m"] = snap
            key = f"{it.target}.{m.name}#m"
            seen[key] = seen.get(key, 0) + 1
            sig = getattr(m, "_sigs", {}).get(it.target)
            if m.body is None or sig is None: continue
            fns[key] = m
            st = prune(sig)
            ptys = ["val" if t in ("unit", "never") else t for t in (kty(a) for a in st.args[:-1])]
            if getattr(m, "_cg_added", False): ptys += [kty(t) for t in m._cg_ptys]
            if len(ptys) != len(m.params): continue
            sigs[key] = (ptys, kty(st.args[-1]))
    for key, c in seen.items():
        if c > 1 and key in sigs:
            del sigs[key]; why[key] = "同一类型上有同名方法"
    while True:   # 不动点（直接调用另一个 KIR 函数要求它也能编译）
        out, changed = {}, False
        for name in list(sigs):
            try:
                if name in snaps:
                    for node, t in snaps[name]: node._t = t
                lw = Lower(fns[name], sigs, name); lw.user_names = user
                out[name] = lw.run()
            except Unsupported as ex:
                why[name] = str(ex); del sigs[name]; changed = True
            except RecursionError:
                why[name] = "嵌套过深"; del sigs[name]; changed = True
        if not changed:
            for f in out.values():
                for lf in f.lambdas: optimize(lf)
            return {n: optimize(f) for n, f in out.items()}, why


# ───────────────────────── 优化 ─────────────────────────
def _defs(f):
    d = {}
    for p in f.params: d[p.id] = d.get(p.id, 0) + 1
    for b in f.blocks:
        for ins in b.instrs:
            if ins.dst is not None: d[ins.dst.id] = d.get(ins.dst.id, 0) + 1
            if ins.op == "next": d[ins.extra.id] = d.get(ins.extra.id, 0) + 1
    return d


def _fold(op, vals, ty):
    """常量折叠；会出错（溢出、除零）的不折叠，返回 None"""
    if op not in FOLDABLE or any(v.ty == "val" for v in vals): return None
    a = vals[0].v; b = vals[1].v if len(vals) > 1 else None
    M = 1 << 63
    if op == "copy": return vals[0]
    if op in ("add.i64", "sub.i64", "mul.i64"):
        r = a + b if op[0] == "a" else a - b if op[0] == "s" else a * b
        return Const(r, "i64") if -M <= r < M else None
    if op == "div.i64":
        if b == 0 or (a == -M and b == -1): return None
        q = abs(a) // abs(b); return Const(q if (a >= 0) == (b >= 0) else -q, "i64")
    if op == "rem.i64":
        if b == 0: return None
        q = abs(a) // abs(b); q = q if (a >= 0) == (b >= 0) else -q
        return Const(a - b * q, "i64")
    if op in ("add.f64", "sub.f64", "mul.f64"):
        return Const(a + b if op[0] == "a" else a - b if op[0] == "s" else a * b, "f64")
    if op == "div.f64": return None if b == 0.0 else Const(a / b, "f64")
    if op == "neg.i64": return None if a == -M else Const(-a, "i64")
    if op == "neg.f64": return Const(-a, "f64")
    if op == "not": return Const(not a, "b1")
    if op in ("lt", "gt", "le", "ge", "eq", "ne"):
        r = {"lt": a < b, "gt": a > b, "le": a <= b, "ge": a >= b, "eq": a == b, "ne": a != b}[op]
        return Const(r, "b1")
    if op == "i2f": return Const(float(a), "f64")
    return None


def optimize(f):
    for _ in range(4):
        changed = const_prop(f)
        changed |= simplify_cfg(f)
        changed |= dce(f)
        if not changed: break
    return f


def const_prop(f):
    """单次定义的寄存器：常量传播 + 折叠；常量条件的 cbr 变成 br"""
    defs = _defs(f); known = {}; changed = False
    sub = lambda a: known.get(a.id, a) if isinstance(a, Reg) else a
    for _ in range(3):
        for b in f.blocks:
            for ins in b.instrs:
                new = [sub(a) for a in ins.args]
                if any(x is not y for x, y in zip(new, ins.args)): ins.args = new; changed = True
                if ins.dst is not None and defs.get(ins.dst.id) == 1 and ins.args \
                        and all(isinstance(a, Const) for a in ins.args):
                    c = _fold(ins.op, ins.args, ins.dst.ty)
                    if c is not None and c.ty == ins.dst.ty:
                        if ins.dst.id not in known:
                            known[ins.dst.id] = c
                            if ins.op != "copy": ins.op, ins.args = "copy", [c]; changed = True
            t = b.term
            if t and t[0] == "cbr":
                c = sub(t[1])
                if isinstance(c, Const):
                    b.term = ("br", t[2] if c.v else t[3]); changed = True
                elif c is not t[1]:
                    b.term = ("cbr", c, t[2], t[3])
            elif t and t[0] == "ret" and t[1] is not None:
                c = sub(t[1])
                if c is not t[1]: b.term = ("ret", c); changed = True
            elif t and t[0] == "panic" and isinstance(t[2], Reg):
                c = sub(t[2])
                if c is not t[2]: b.term = ("panic", t[1], c, t[3])
    return changed


def _succ(t):
    if t is None: return []
    if t[0] == "br": return [t[1]]
    if t[0] == "cbr": return [t[2], t[3]]
    return []


def simplify_cfg(f):
    changed = False
    fwd = {}
    for b in f.blocks:
        if not b.instrs and b.term and b.term[0] == "br" and b.term[1] != b.id:
            fwd[b.id] = b.term[1]
    def res(x):
        seen = set()
        while x in fwd and x not in seen:
            seen.add(x); x = fwd[x]
        return x
    for b in f.blocks:
        t = b.term
        if t and t[0] == "br" and res(t[1]) != t[1]: b.term = ("br", res(t[1])); changed = True
        elif t and t[0] == "cbr":
            nt = ("cbr", t[1], res(t[2]), res(t[3]))
            if nt[2] == nt[3]: nt = ("br", nt[2])
            if nt != t: b.term = nt; changed = True
    reach, stack = set(), [0]
    byid = {b.id: b for b in f.blocks}
    while stack:
        x = stack.pop()
        if x in reach or x not in byid: continue
        reach.add(x); stack.extend(_succ(byid[x].term))
    keep = [b for b in f.blocks if b.id in reach]
    if len(keep) != len(f.blocks): changed = True
    preds = {}
    for b in keep:
        for s in _succ(b.term): preds[s] = preds.get(s, 0) + 1
    merged = True
    while merged:
        merged = False
        byid = {b.id: b for b in keep}
        for b in keep:
            t = b.term
            if t and t[0] == "br" and t[1] != b.id and t[1] != 0 and preds.get(t[1]) == 1:
                c = byid[t[1]]
                b.instrs += c.instrs; b.term = c.term
                keep.remove(c); merged = changed = True
                break
    f.blocks = keep
    return changed


def dce(f):
    changed = False
    while True:
        used = set()
        for b in f.blocks:
            for ins in b.instrs:
                for a in ins.args:
                    if isinstance(a, Reg): used.add(a.id)
            t = b.term
            if t:
                for x in t[1:]:
                    if isinstance(x, Reg): used.add(x.id)
        removed = False
        for b in f.blocks:
            keep = []
            for ins in b.instrs:
                if ins.dst is not None and ins.dst.id not in used and ins.op in NO_FAIL:
                    removed = True; continue
                keep.append(ins)
            b.instrs = keep
        if not removed: return changed
        changed = True


# ───────────────────────── 文本形式 ─────────────────────────
def dump(f):
    L = [f"fn {f.name}(" + ", ".join(f"{p}: {p.ty}" + (f" /*{p.name}*/" if p.name else "") for p in f.params) + f") -> {f.ret} {{"]
    for b in f.blocks:
        L.append(f"b{b.id}:")
        for ins in b.instrs:
            args = ", ".join(map(repr, ins.args))
            op = ins.op
            if op in ("call", "callg", "gload"): op += f" @{ins.extra}"
            elif op == "callm": op += f" .{ins.extra}"
            elif op == "field": op += f" .{ins.extra}"
            elif op in ("vbin",): op += f" {ins.extra}"
            elif op == "next": op += f" -> {ins.extra!r}"
            elif op == "pbget": op += f" #{ins.extra}"
            elif op == "tpat": op += f" pat{ins.extra}"
            elif op == "struct": op += f" {ins.extra[0]}"
            elif op == "tidx": op += f" .{ins.extra}"
            note = f"   // {ins.dst.name}" if ins.dst is not None and ins.dst.name else ""
            L.append(f"  {ins.dst!r}: {ins.dst.ty} = {op} {args}{note}".rstrip() if ins.dst is not None else f"  {op} {args}".rstrip())
        t = b.term
        if t is None: L.append("  unreachable")
        elif t[0] == "br": L.append(f"  br b{t[1]}")
        elif t[0] == "cbr": L.append(f"  cbr {t[1]!r}, b{t[2]}, b{t[3]}")
        elif t[0] == "ret": L.append("  ret" + (f" {t[1]!r}" if t[1] is not None else ""))
        elif t[0] == "panic": L.append(f"  panic {t[1]!r}")
        elif t[0] == "unreachable": L.append("  unreachable")
    L.append("}")
    for lf in f.lambdas:
        L.append(""); L.append("// " + ("捕获 " + ", ".join(f"{c}={c.name}" for c in lf.caps) if lf.caps else "不捕获变量"))
        lf2 = lf.lambdas; lf.lambdas = []
        L.append(dump(lf)); lf.lambdas = lf2
    return "\n".join(L)


# ───────────────────────── KIR → C ─────────────────────────
def cnames(fns):
    """KIR 函数名 → C 函数名（确定性，按名字排序编号）；lambda 按 id 编号"""
    names = {n: f"kf{i}_" + "".join(c if c.isascii() and c.isalnum() else "_" for c in n) for i, n in enumerate(sorted(fns))}
    k = 0
    for n in sorted(fns):
        for lf in fns[n].lambdas:
            names[id(lf)] = f"kl{k}"; k += 1
    return names


def all_funcs(fns):
    out = []
    for n in sorted(fns):
        out.append(fns[n]); out.extend(fns[n].lambdas)
    return out


def c_ret(ty):
    return "void" if ty in ("unit", "never") else CTYPE[ty]


def c_sig(f, names):
    if f.closure: return f"static V {names[id(f)]}(KFn *self, int argc, V *argv)"
    ps = ", ".join(f"{CTYPE[p.ty]} r{p.id}" for p in f.params) or "void"
    return f"static {c_ret(f.ret)} {names[f.name]}({ps})"


C_HELPERS = r"""
static inline int kir_try(V v) {
  int vr = v.t == T_ENUM ? ENUMV(v)->var : -1;
  if (vr == SYM_Ok || vr == SYM_Some) return 1;
  if (vr == SYM_Err || vr == SYM_None) return 0;
  die("? 只能用于 Result 或 Option"); return 0;
}
static KUp *kir_up(V v) {
  KUp *u = kalloc(sizeof(KUp)); u->closed = v; u->ref = &u->closed; u->next = NULL; u->owner = cur_task; return u;
}
static inline V kir_gload(int g, const char *name) {
  if (__builtin_expect(G[g].t == T_UNDEF, 0)) die("未定义的名字 %s", name);
  return G[g];
}
static V kir_vbin(int op, V a, V b) {
  switch (op) {
  case 0: return f_add(a, b); case 1: return f_sub(a, b); case 2: return f_mul(a, b);
  case 3: return k_div(a, b); case 4: return k_mod(a, b);
  case 5: return f_lt(a, b); case 6: return f_gt(a, b); case 7: return f_le(a, b); case 8: return f_ge(a, b);
  case 9: return f_eq(a, b); case 10: return f_ne(a, b);
  case 11: return k_range(a, b, 0); case 12: return k_range(a, b, 1);
  }
  return UNIT;
}
"""
VBIN = {"+": 0, "-": 1, "*": 2, "/": 3, "%": 4, "<": 5, ">": 6, "<=": 7, ">=": 8, "==": 9, "!=": 10, "..": 11, "..=": 12}


def box(ty, x):
    return {"i64": f"mkint({x})", "f64": f"mkflt({x})", "b1": f"mkbool({x})", "val": x}[ty]


def unbox(ty, x):
    return {"i64": f"({x}).u.i", "f64": f"({x}).u.f", "b1": f"(({x}).u.i != 0)", "val": x}[ty]


def emit_c(f, names, gen):
    """gen：原生后端的 Gen（登记字符串、符号、全局、模式，提供已知闭包的直接调用）"""
    def A(a):
        if isinstance(a, Reg): return f"r{a.id}"
        if a.ty == "val": return "UNIT" if a.v is UNIT_V else gen.const(a.v)
        if a.ty == "b1": return "1" if a.v else "0"
        if a.ty == "i64": return "INT64_MIN" if a.v == -(1 << 63) else f"INT64_C({a.v})"
        v = float(a.v)
        if v != v: return "NAN"
        if v in (float("inf"), float("-inf")): return "INFINITY" if v > 0 else "(-INFINITY)"
        return v.hex()
    L = [c_sig(f, names) + " {"]
    regs = {}
    for b in f.blocks:
        for ins in b.instrs:
            if ins.dst is not None: regs[ins.dst.id] = ins.dst.ty
            if ins.op == "next": regs[ins.extra.id] = "val"
    pids = {p.id for p in f.params}
    if f.closure:
        n = len(f.params)
        L.append(f"  if (argc != {n}) arity(self, {n}, argc);")
        for i, p in enumerate(f.params): L.append(f"  {CTYPE[p.ty]} r{p.id} = {unbox(p.ty, f'argv[{i}]')};")
        for i, c in enumerate(f.caps):
            if c.ty == "cell": L.append(f"  KUp *r{c.id} = self->ups[{i}];")
            else: L.append(f"  {CTYPE[c.ty]} r{c.id} = {unbox(c.ty, f'*self->ups[{i}]->ref')};")
        pids |= {c.id for c in f.caps}
    for rid, ty in sorted(regs.items()):
        if rid not in pids: L.append(f"  {CTYPE[ty]} r{rid} = {'UNIT' if ty == 'val' else 'NULL' if ty == 'cell' else '0'};")
    pats = [gen.pat(p) for p in f.pats]
    if f.ndefer:
        L.append(f"  V defers[{f.ndefer}]; int nd = 0; KUp *open = NULL; V FL_[1];")
        L.append("  KFrame fr_ = { cur_frame, &open, FL_, defers, &nd }; cur_frame = &fr_;")
    if f.pats: L.append(f"  V PB[{max(f.npb, 1)}]; for (int i_ = 0; i_ < {max(f.npb, 1)}; i_++) PB[i_] = UNIT;")
    L.append("  SAFEPOINT(); CANCEL_POINT();")
    order = {b.id: i for i, b in enumerate(f.blocks)}

    def argv(vals):
        if not vals: return "0, NULL", ""
        return f"{len(vals)}, a_", f"V a_[{len(vals)}] = {{{', '.join(vals)}}}; "

    def direct(name):
        """已知且从未被重新赋值的全局函数：直接调用它的 C 函数"""
        from vm import Closure
        v = gen.vm.globals.get(name)
        if type(v) is Closure and name not in gen.assigned and id(v.proto) in gen.pid:
            return gen.pid[id(v.proto)]
        return None

    owns = sorted({ins.extra for b in f.blocks for ins in b.instrs if ins.op == "windexl"})
    if owns: L.append("  " + " ".join(f"int own{o} = 0;" for o in owns))
    owns = set(owns)
    for i, b in enumerate(f.blocks):
        L.append(f" B{b.id}:;")
        cur_line = None
        wl_dst = None
        for ins in b.instrs:
            if ins.dst is not None and ins.dst.id in owns and ins.op != "windexl":
                src = ins.args[0] if ins.op == "copy" and ins.args else None
                if not (src is not None and getattr(src, "id", None) == wl_dst):
                    L_reset = f"own{ins.dst.id} = 0;"
                else: L_reset = None
            else: L_reset = None
            wl_dst = ins.dst.id if ins.op == "windexl" and ins.dst is not None else None
            d = f"r{ins.dst.id}" if ins.dst is not None else None
            if L_reset: L.append("  " + L_reset)
            a = [A(x) for x in ins.args]; op = ins.op; ln = ins.line
            # 可能出错的运行时调用之前更新行号
            if op in ("cset", "wfield", "windex", "windexl", "vbin", "vneg", "callg", "callm", "callv", "field", "tidx", "index", "interp", "struct",
                      "iter", "next", "tryok", "tpat", "unbox", "gload", "call") and ln != cur_line:
                L.append(f"  kline = {ln};"); cur_line = ln
            if op == "copy": L.append(f"  {d} = {a[0]};")
            elif op == "inc": L.append(f"  r{ins.dst.id} = r{ins.dst.id} + 1;")
            elif op in ("add.i64", "sub.i64", "mul.i64"):
                L.append(f"  if (__builtin_{op[:3]}_overflow({a[0]}, {a[1]}, &{d})) {{ kline = {ln}; die(\"整数溢出\"); }}")
            elif op == "div.i64":
                L.append(f"  if ({a[1]} == 0) {{ kline = {ln}; die(\"除以零\"); }}"
                         f" if ({a[0]} == INT64_MIN && {a[1]} == -1) {{ kline = {ln}; die(\"整数溢出\"); }} {d} = {a[0]} / {a[1]};")
            elif op == "rem.i64":
                L.append(f"  if ({a[1]} == 0) {{ kline = {ln}; die(\"除以零\"); }} {d} = {a[1]} == -1 ? 0 : {a[0]} % {a[1]};")
            elif op in ("add.f64", "sub.f64", "mul.f64"):
                L.append(f"  {d} = {a[0]} {dict(add='+', sub='-', mul='*')[op[:3]]} {a[1]};")
            elif op == "div.f64":
                L.append(f"  if ({a[1]} == 0.0) {{ kline = {ln}; die(\"除以零\"); }} {d} = {a[0]} / {a[1]};")
            elif op == "rem.f64":
                L.append(f"  if ({a[1]} == 0.0) {{ kline = {ln}; die(\"除以零\"); }} {d} = fmod({a[0]}, {a[1]});")
            elif op == "neg.i64":
                L.append(f"  if (__builtin_sub_overflow((int64_t)0, {a[0]}, &{d})) {{ kline = {ln}; die(\"整数溢出\"); }}")
            elif op == "neg.f64": L.append(f"  {d} = -{a[0]};")
            elif op in ("bit.band", "bit.bor", "bit.bxor"):
                L.append(f"  {d} = {a[0]} {BITF['__' + op[4:]][1]} {a[1]};")
            elif op == "bit.bnot": L.append(f"  {d} = ~{a[0]};")
            elif op in ("bit.shl", "bit.shr"):
                L.append(f"  if ((uint64_t){a[1]} >= 64) {{ kline = {ln}; die(\"移位位数超出范围: %lld\", (long long){a[1]}); }}")
                L.append(f"  {d} = (int64_t)((uint64_t){a[0]} << {a[1]});" if op == "bit.shl" else f"  {d} = {a[0]} >> {a[1]};")
            elif op == "not": L.append(f"  {d} = !{a[0]};")
            elif op in ("lt", "gt", "le", "ge", "eq", "ne"):
                L.append(f"  {d} = {a[0]} {dict(lt='<', gt='>', le='<=', ge='>=', eq='==', ne='!=')[op]} {a[1]};")
            elif op == "i2f": L.append(f"  {d} = (double){a[0]};")
            elif op == "f2i":
                L.append(f"  if (!({a[0]} >= -9223372036854775808.0 && {a[0]} < 9223372036854775808.0)) {{ kline = {ln}; die(\"整数溢出\"); }} {d} = (int64_t){a[0]};")
            elif op == "b2i": L.append(f"  {d} = {a[0]} ? 1 : 0;")
            elif op == "abs.i64":
                L.append(f"  if ({a[0]} == INT64_MIN) {{ kline = {ln}; die(\"整数溢出\"); }} {d} = {a[0]} < 0 ? -{a[0]} : {a[0]};")
            elif op == "abs.f64": L.append(f"  {d} = fabs({a[0]});")
            elif op in ("min", "max"):
                c = "<" if op == "min" else ">"
                L.append(f"  {d} = {a[1]} {c} {a[0]} ? {a[1]} : {a[0]};")
            elif op == "box": L.append(f"  {d} = {box(ins.args[0].ty, a[0])};")
            elif op == "unbox": L.append(f"  {d} = {unbox(ins.dst.ty, a[0])};")
            elif op == "mkcell": L.append(f"  {d} = kir_up({a[0]});")
            elif op == "cget": L.append(f"  {d} = *{a[0]}->ref;")
            elif op == "cset": L.append(f"  k_store_up({a[0]}, {a[1]});")
            elif op == "defer": L.append(f"  defers[nd++] = {a[0]};")
            elif op == "rundefers": L.append(f"  while (nd > {ins.extra}) {{ nd--; call_v(defers[nd], 0, NULL); }}")
            elif op == "spawn":
                L.append(f"  {{ V a_[{len(a)}] = {{{', '.join(a)}}}; {d} = k_spawn({len(a)}, a_); }}")
            elif op == "closure":
                lf = ins.extra; n = len(a)
                ups = " ".join(f"f_->ups[{j}] = {x if ins.args[j].ty == 'cell' else f'kir_up({x})'};" for j, x in enumerate(a))
                L.append(f"  {{ V c_ = mkclosure({names[id(lf)]}, \"{getattr(lf, 'label', '<lambda>')}\", {n}); KFn *f_ = FN(c_); {ups} {d} = c_; }}")
            elif op == "call":
                call = f"{names[ins.extra]}({', '.join(a)})"
                L.append(f"  {d} = {call};" if d else f"  {call};")
            elif op in ("callg", "callv", "callm"):
                if op == "callg":
                    k = direct(ins.extra)
                    cnt, decl = argv(a)
                    if k is not None: call = f"p{k}(&CL{k}, {cnt})"
                    else: call = f"call_v(kir_gload({gen.glob(ins.extra)}, {_cs(ins.extra)}), {cnt})"
                elif op == "callv":
                    cnt, decl = argv(a[1:])
                    call = f"call_v({a[0]}, {cnt})"
                else:
                    decl = f"V a_[{len(a)}] = {{{', '.join(a)}}}; "
                    call = f"call_m({gen.sym(ins.extra)}, {len(a) - 1}, a_)"
                L.append(f"  {{ {decl}{d + ' = ' if d else ''}{call}; }}")
            elif op == "gload": L.append(f"  {d} = kir_gload({gen.glob(ins.extra)}, {_cs(ins.extra)});")
            elif op == "vbin": L.append(f"  {d} = kir_vbin({VBIN[ins.extra]}, {a[0]}, {a[1]});")
            elif op == "vneg": L.append(f"  {d} = f_neg({a[0]});")
            elif op in ("list", "tuple"):
                if not a: L.append(f"  {d} = {'mklist(0, NULL)' if op == 'list' else 'UNIT'};")
                else: L.append(f"  {{ V a_[{len(a)}] = {{{', '.join(a)}}}; {d} = mk{op}({len(a)}, a_); }}")
            elif op == "struct":
                sn, fs = ins.extra
                L.append(f"  {{ V a_[{max(len(a), 1)}] = {{{', '.join(a) or 'UNIT'}}}; {d} = k_struct(&ST_{gen.tid[sn]}, {len(a)}, {gen.symarr(fs)}, a_); }}")
            elif op == "interp":
                parts, it = [], iter(a)
                for p in ins.extra: parts.append(gen.sconst(p) if p is not None else next(it))
                L.append(f"  {{ V a_[{len(parts)}] = {{{', '.join(parts)}}}; {d} = k_interp({len(parts)}, a_); }}")
            elif op == "field": L.append(f"  {d} = k_field({a[0]}, {gen.sym(ins.extra)});")
            elif op == "tidx": L.append(f"  {d} = k_tidx({a[0]}, {ins.extra});")
            elif op == "wfield": L.append(f"  {d} = k_wfield({a[0]}, {gen.sym(ins.extra)}, {a[1]});")
            elif op == "windex": L.append(f"  {d} = k_windex({a[0]}, {a[1]}, {a[2]});")
            elif op == "windexl": L.append(f"  {d} = k_windex_l(&own{ins.extra}, {a[0]}, {a[1]}, {a[2]});")
            elif op == "index": L.append(f"  {d} = k_index({a[0]}, {a[1]});")
            elif op == "iter": L.append(f"  {d} = k_iter({a[0]});")
            elif op == "next": L.append(f"  {d} = k_next({a[0]}, &r{ins.extra.id});")
            elif op == "tpat": L.append(f"  {d} = {pats[ins.extra]}({a[0]}, PB);")
            elif op == "pbget": L.append(f"  {d} = PB[{ins.extra}];")
            elif op == "tryok": L.append(f"  {d} = kir_try({a[0]});")
            elif op == "payload": L.append(f"  {d} = ENUMV({a[0]})->a[0];")
            else:
                raise Unsupported(f"C 生成: {op}")
        t = b.term
        if t is None or t[0] == "unreachable": L.append("  __builtin_unreachable();")
        elif t[0] == "br":
            back = order.get(t[1], 0) <= i
            L.append(("  SAFEPOINT(); CANCEL_POINT(); " if back else "  ") + f"goto B{t[1]};")
        elif t[0] == "cbr":
            bt, bf = t[2], t[3]
            st = lambda x: (f"{{ SAFEPOINT(); CANCEL_POINT(); goto B{x}; }}" if order.get(x, 0) <= i else f"goto B{x};")
            L.append(f"  if ({A(t[1])}) {st(bt)} else {st(bf)}")
        elif t[0] == "ret":
            if f.ndefer:
                L.append("  while (nd > 0) { nd--; call_v(defers[nd], 0, NULL); } cur_frame = fr_.prev;")
            if f.closure:
                L.append(f"  return {box(t[1].ty, A(t[1]))};" if t[1] is not None else "  return UNIT;")
            else:
                L.append(f"  return {A(t[1])};" if t[1] is not None else "  return;")
        elif t[0] == "panic":
            v = t[2]
            if v is not None:
                L.append(f"  kline = {t[3]}; die(\"{t[1]}\", cshow({box(v.ty, A(v))}));")
            else:
                L.append(f"  kline = {t[3]}; die(\"{t[1]}\");")
    L.append("}")
    return "\n".join(L)


def _cs(s):
    from native import c_str
    return c_str(s)


def emit_wrapper(f, names, k, nparams):
    """字节码世界调用 KIR 函数用的装箱包装：签名与 p{k} 相同"""
    args = ", ".join(unbox(p.ty, f"argv[{i}]") for i, p in enumerate(f.params))
    call = f"{names[f.name]}({args})"
    body = f"{call}; return UNIT;" if f.ret in ("unit", "never") else f"return {box(f.ret, call)};"
    return (f"static V p{k}(KFn *self, int argc, V *argv) {{ /* {f.name}（KIR） */\n"
            f"  if (argc != {nparams}) arity(self, {nparams}, argc);\n  {body}\n}}")
