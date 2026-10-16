"""Kite 原生后端：字节码 → C → 本机可执行文件

流程
  源码 ─类型检查─▶ AST ─vm.Compiler─▶ 字节码(Proto) ─本模块─▶ C 源码 ─cc -O2─▶ 可执行文件

要点
- 每个 Proto 生成一个 C 函数；操作数栈深度在编译期已知，栈槽变成固定下标的局部数组，
  跳转变成 goto，C 编译器可以把热点路径优化成寄存器操作
- Int / Float / Bool 不装箱；整数加减乘带溢出检查
- 调用一个从未被重新赋值的顶层函数时生成直接 C 调用（不走间接调用）
- 模式匹配为每个模式生成专用的 C 匹配函数
- 方法分派生成 switch 表；运行时（runtime/kite_rt.c）提供值、字符串、集合、内建函数和标准库
- C 编译器自动探测：$CC → cc → clang → gcc → zig cc（pip install ziglang）
"""
import os
import shutil
import subprocess
import sys

import vm as V
import kir
from vm import Proto, Closure
from kite import (KiteError, Builtin, StructType, EnumType, EnumVal, Module, Unit, UnitType)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.path.join(HERE, "runtime", "kite_rt.c")

BUILTIN_C = {
    "print": "b_print", "len": "b_len", "str": "b_str", "int": "b_int", "float": "b_float",
    "type_of": "b_type_of", "map": "b_map", "filter": "b_filter", "each": "b_each",
    "reduce": "b_reduce", "fold": "b_reduce", "sum": "b_sum", "count": "b_count", "any": "b_any",
    "all": "b_all", "find": "b_find", "first": "b_first", "last": "b_last", "get": "b_get",
    "take": "b_take", "skip": "b_skip", "reverse": "b_reverse", "sort": "b_sort", "sort_by": "b_sort", "sort_with": "b_sort_with", "__fmt": "b_fmt", "__repeat": "b_repeat",
    "zip": "b_zip", "enumerate": "b_enumerate", "push": "b_push", "concat": "b_concat",
    "contains": "b_contains", "join": "b_join", "split": "b_split", "trim": "b_trim",
    "upper": "b_upper", "lower": "b_lower", "starts_with": "b_starts_with", "ends_with": "b_ends_with",
    "replace": "b_replace", "chars": "b_chars", "parse_int": "b_parse_int",
    "parse_float": "b_parse_float", "keys": "b_keys", "remove": "b_remove", "values": "b_values", "set": "b_set",
    "Map": "b_Map", "Set": "b_Set", "insert": "b_insert", "union": "b_union", "intersect": "b_intersect",
    "difference": "b_difference", "is_subset": "b_is_subset", "to_list": "b_to_list",
    "__set_lit": "b_Set", "__set_union": "b_union", "__set_intersect": "b_intersect", "__set_difference": "b_difference", "min": "b_min", "max": "b_max", "abs": "b_abs", "Ok": "b_Ok", "Err": "b_Err",
    "Some": "b_Some", "unwrap": "b_unwrap", "unwrap_or": "b_unwrap_or", "is_ok": "b_is_ok",
    "is_err": "b_is_err", "is_some": "b_is_some", "is_none": "b_is_none", "panic": "b_panic",
    "assert": "b_assert", "assert_eq": "b_assert_eq", "assert_ne": "b_assert_ne", "__kite_test": "b_kite_test",
    "__race": "b_race", "timeout": "b_timeout",
    "actor": "b_actor", "tell": "b_tell", "ask": "b_ask",
    "__recover_raw": "b_recover_raw", "__repanic": "b_repanic", "__restart_log": "b_restart_log_k", "__restart_log_w": "b_restart_log_w", "supervise": "b_supervise", "actor_supervised": "b_actor_supervised",
    "__env_get": "b_env_get", "__args": "b_args", "__exit": "b_exit", "__run": "b_run", "__ord": "b_ord",
    "__chr": "b_chr", "__unix_ms": "b_unix_ms", "__str_slice": "b_str_slice", "__slice": "b_slice", "__band": "b_band", "__bor": "b_bor", "__bxor": "b_bxor", "__bnot": "b_bnot", "__shl": "b_shl", "__shr": "b_shr", "__str_find": "b_str_find",
    "__tcp_listen": "b_tcp_listen", "__tcp_port": "b_tcp_port", "__tcp_accept": "b_tcp_accept",
    "__tcp_connect": "b_tcp_connect", "__tcp_read_line": "b_tcp_read_line", "__tcp_read_bytes": "b_tcp_read_bytes",
    "__tcp_read_all": "b_tcp_read_all", "__tcp_write": "b_tcp_write", "__tcp_flush": "b_tcp_flush",
    "__tcp_close": "b_tcp_close", "__byte_len": "b_byte_len", "__bytes": "b_bytes", "__from_bytes": "b_from_bytes", "__eprint": "b_eprint", "__fs_stat": "b_fs_stat", "__getcwd": "b_getcwd", "__chdir": "b_chdir", "__chmod": "b_chmod", "__env_set": "b_env_set", "__env_unset": "b_env_unset", "__environ": "b_environ", "__hostname": "b_hostname", "__getpid": "b_getpid", "__read_bytes": "b_read_bytes", "__write_bytes": "b_write_bytes", "__tz_offset": "b_tz_offset", "__random_bytes": "b_random_bytes", "__unix_ns": "b_unix_ns",
    "__tls_server_ctx": "b_tls_server_ctx", "__tls_accept": "b_tls_accept", "__tls_client": "b_tls_client",
    "__tls_handshake": "b_tls_handshake",
    "__select": "b_select", "sleep": "b_sleep", "now_ms": "b_now_ms",
    "chan": "b_chan", "send": "b_send", "recv": "b_recv", "try_recv": "b_try_recv", "close": "b_close", "par_map": "b_par_map",
}
FIXED_TYPES = ["Unit", "Bool", "Int", "Float", "Str", "List", "Tuple", "Map", "Fn", "Result", "Option", "Chan", "Set"]

# 栈效应：(落空路径的深度变化, 是否终止, 跳转目标下标在 arg 中的位置)
def effect(op, arg):
    if op in (V.CONST, V.LOAD_L, V.LOAD_U, V.LOAD_G, V.CLOSURE): return 1
    if op in (V.STORE_L, V.STORE_U, V.STORE_G, V.DEF_G, V.POP, V.JIF, V.INDEX, V.WFIELD,
              V.MATCH_OR_FAIL, V.ITER, V.DEFER, V.JIF_OR_POP, V.JIT_OR_POP): return -1
    if op in V.BINOPS.values(): return -1
    if op == V.POPN: return -arg
    if op == V.CALL: return -arg
    if op == V.CALL_M: return -arg[1]
    if op in (V.LIST, V.TUPLE, V.INTERP, V.SPAWN): return 1 - arg
    if op == V.STRUCT: return 1 - len(arg[1])
    if op == V.WINDEX: return -2
    if op == V.FOR_NEXT: return 1
    return 0   # NEG NOT FIELD TIDX TEST_PAT TRY CLOSE RUN_DEFERS JUMP RET MATCH_FAIL


def c_str(s):
    b = s.encode("utf-8")
    out = []
    for ch in b:
        if ch in (34, 92): out.append("\\" + chr(ch))
        elif 32 <= ch < 127 and ch != 63: out.append(chr(ch))
        else: out.append("\\%03o" % ch)
    return '"' + "".join(out) + '"'


class Gen:
    def __init__(self, vmach, src_name):
        self.vm = vmach
        self.src_name = src_name
        self.protos = []
        self.pid = {}
        self.syms, self.symid = [], {}
        self.strs, self.strid = [], {}
        self.gnames, self.gid = [], {}
        self.types, self.tid = list(FIXED_TYPES), {n: i for i, n in enumerate(FIXED_TYPES)}
        self.pats = []           # C 代码
        self.fsyms = []          # 静态 int 数组
        self.fsym_id = {}
        self.out = []
        for s in ("Ok", "Err", "Some", "None"):
            self.sym(s)

    # 注册表
    def sym(self, name):
        if name not in self.symid:
            self.symid[name] = len(self.syms); self.syms.append(name)
        return self.symid[name]

    def sconst(self, s):
        if s not in self.strid:
            self.strid[s] = len(self.strs); self.strs.append(s)
        return f"KS[{self.strid[s]}]"

    def glob(self, name):
        if name not in self.gid:
            self.gid[name] = len(self.gnames); self.gnames.append(name)
        return self.gid[name]

    def type_id(self, name):
        if name not in self.tid:
            self.tid[name] = len(self.types); self.types.append(name)
        return self.tid[name]

    def symarr(self, names):
        key = tuple(names)
        if key not in self.fsym_id:
            self.fsym_id[key] = len(self.fsyms)
            self.fsyms.append([self.sym(n) for n in names])
        return f"FS{self.fsym_id[key]}"

    def add_proto(self, p):
        if id(p) in self.pid: return
        self.pid[id(p)] = len(self.protos); self.protos.append(p)
        for op, arg in p.code:
            if op == V.CLOSURE: self.add_proto(arg)

    # 入口
    def generate(self, chunk):
        vmach = self.vm
        self.add_proto(chunk)
        for name, v in vmach.globals.items():
            if type(v) is Closure: self.add_proto(v.proto)
        for t, tbl in vmach.methods.items():
            for m, c in tbl.items():
                c.proto.name = f"{t}.{m}"
                self.add_proto(c.proto)
        # 类型
        self.structs, self.enums = {}, {}
        for name, v in vmach.globals.items():
            if isinstance(v, StructType):
                self.structs[name] = v; self.type_id(name)
            elif isinstance(v, EnumType):
                self.enums[name] = v; self.type_id(name)
        for t in vmach.methods: self.type_id(t)
        # 被重新赋值过的全局 → 不能直接调用
        self.assigned = set()
        for p in self.protos:
            for op, arg in p.code:
                if op in (V.STORE_G, V.DEF_G): self.assigned.add(arg)
                if op in (V.LOAD_G, V.STORE_G, V.DEF_G): self.glob(arg)
                if op == V.CALL_M:   # UFCS：x.len() → len(x)
                    gv = vmach.globals.get(arg[0])
                    if type(gv) is Closure or isinstance(gv, Builtin): self.glob(arg[0])
        for name in list(vmach.globals):
            v = vmach.globals[name]
            if type(v) is Closure or isinstance(v, (StructType, EnumType)): self.glob(name)
        self.glob("main")
        kf = getattr(self, "kir_fns", None) or {}
        self.kir_names = kir.cnames(kf)
        self.kir_proto = {}
        for name, f in kf.items():
            if name.endswith("#m"):
                tn, mn = name[:-2].rsplit(".", 1)
                v = vmach.methods.get(tn, {}).get(mn)
                if type(v) is Closure and id(v.proto) in self.pid:
                    self.kir_proto[id(v.proto)] = f
                continue
            v = vmach.globals.get(name)
            if type(v) is Closure and id(v.proto) in self.pid and name not in self.assigned:
                self.kir_proto[id(v.proto)] = f
        body = []
        if kf:
            body.append("/* ───── KIR 函数（不装箱） ───── */")
            allf = kir.all_funcs(kf)
            body += [kir.c_sig(f, self.kir_names) + ";" for f in allf]
            body.append(kir.C_HELPERS)
            body += [kir.emit_c(f, self.kir_names, self) for f in allf]
        body += [self.gen_proto(p) for p in self.protos]
        return self.assemble(chunk, body)

    # 每个 Proto
    def depths(self, p):
        code = p.code
        depth = [None] * len(code)
        targets = set()
        work = [(0, 0)]
        while work:
            pc, d = work.pop()
            while pc < len(code):
                if depth[pc] is not None:
                    if depth[pc] != d:
                        raise KiteError(f"原生后端内部错误: {p.name} 第 {pc} 条指令栈深度不一致")
                    break
                depth[pc] = d
                op, arg = code[pc]
                if op == V.JUMP:
                    targets.add(arg); pc = arg; continue
                if op in (V.RET, V.MATCH_FAIL):
                    break
                if op == V.JIF:
                    targets.add(arg); work.append((arg, d - 1))
                elif op in (V.JIF_OR_POP, V.JIT_OR_POP):
                    targets.add(arg); work.append((arg, d))
                elif op == V.FOR_NEXT:
                    targets.add(arg[1]); work.append((arg[1], d))
                elif op == V.TRY:
                    pass
                d += effect(op, arg)
                pc += 1
        return depth, targets

    def gen_proto(self, p):
        k = self.pid[id(p)]
        if id(p) in getattr(self, "kir_proto", {}):
            return kir.emit_wrapper(self.kir_proto[id(p)], self.kir_names, k, p.nparams)
        depth, targets = self.depths(p)
        maxd = max([d for d in depth if d is not None] + [0]) + 2
        ndef = sum(1 for op, _ in p.code if op == V.DEFER) or 1
        L = []
        w = L.append
        w(f"static V p{k}(KFn *self, int argc, V *argv) {{ /* {p.name} */")
        w(f"  if (argc != {p.nparams}) arity(self, {p.nparams}, argc);")
        w("  SAFEPOINT();")
        w(f"  V L[{max(p.nlocals, 1)}]; V S[{maxd}]; V rv; KUp *open = NULL; V defers[{ndef}]; int nd = 0;")
        if p.nparams: w(f"  memcpy(L, argv, sizeof(V) * {p.nparams});")
        if p.nlocals > p.nparams:
            w(f"  for (int i = {p.nparams}; i < {p.nlocals}; i++) L[i] = UNIT;")
        # 有 defer 或会创建闭包的函数登记栈帧，取消展开时据此执行 defer、关闭被捕获的变量
        need_frame = any(op in (V.DEFER, V.CLOSURE) for op, _ in p.code)
        if need_frame:
            w("  KFrame fr_ = { cur_frame, &open, L, defers, &nd }; cur_frame = &fr_;")
        w("  CANCEL_POINT();")
        known = {}
        cur_line = None
        for pc, (op, arg) in enumerate(p.code):
            d = depth[pc]
            if pc in targets:
                w(f" I{pc}:;")
                cur_line = None
                if d is not None:
                    known = {s: f for s, f in known.items() if s < d}
            if d is None:
                continue
            ln = p.lines[pc]
            if ln != cur_line and op not in (V.CONST, V.LOAD_L, V.STORE_L, V.POP, V.JUMP, V.CLOSE):
                w(f"  kline = {ln};"); cur_line = ln
            self.cur_pc = pc
            code, newknown = self.op(p, op, arg, d, known)
            w("  " + code)
            nd_ = d + effect(op, arg)
            nowrite = op in (V.STORE_L, V.STORE_U, V.STORE_G, V.DEF_G, V.POP, V.POPN, V.DEFER, V.ITER,
                             V.MATCH_OR_FAIL, V.JIF, V.JIF_OR_POP, V.JIT_OR_POP, V.CLOSE, V.RUN_DEFERS, V.JUMP)
            limit = nd_ if nowrite else nd_ - 1
            known = {s_: f for s_, f in known.items() if s_ < limit}
            if newknown is not None:
                known[d] = newknown
            if op in (V.CALL, V.CALL_M):
                cur_line = None
        w(" ret:")
        w("  while (nd > 0) { nd--; call_v(defers[nd], 0, NULL); }")
        w("  if (open) close_ups(&open, L);")
        if need_frame: w("  cur_frame = fr_.prev;")
        w("  return rv;")
        w("}")
        return "\n".join(L)

    def op(self, p, op, a, d, known):
        t = d - 1   # 栈顶下标
        S = lambda i: f"S[{i}]"
        if op == V.CONST:
            return f"S[{d}] = {self.const(a)};", None
        if op == V.LOAD_L: return f"S[{d}] = L[{a}];", None
        if op == V.STORE_L: return f"L[{a}] = S[{t}];", None
        if op == V.LOAD_U: return f"S[{d}] = *self->ups[{a}]->ref;", None
        if op == V.STORE_U: return f"k_store_up(self->ups[{a}], S[{t}]);", None
        if op == V.LOAD_G:
            g = self.gid[a]
            v = self.vm.globals.get(a)
            fk = None
            if type(v) is Closure and a not in self.assigned:
                fk = self.pid[id(v.proto)]
                return f"S[{d}] = G[{g}];", fk
            return f"S[{d}] = G[{g}]; if (S[{d}].t == T_UNDEF) die(\"未定义的名字 %s\", {c_str(a)});", None
        if op == V.DEF_G: return f"G[{self.gid[a]}] = S[{t}];", None
        if op == V.STORE_G: return f"k_store_g(&G[{self.gid[a]}], S[{t}]);", None
        if op in (V.POP, V.POPN): return ";", None
        if op in V.BINOPS.values():
            fn = {V.ADD: "f_add", V.SUB: "f_sub", V.MUL: "f_mul", V.DIV: "k_div", V.MOD: "k_mod",
                  V.LT: "f_lt", V.GT: "f_gt", V.LE: "f_le", V.GE: "f_ge", V.EQ: "f_eq", V.NE: "f_ne"}.get(op)
            if fn: return f"S[{t-1}] = {fn}(S[{t-1}], S[{t}]);", None
            return f"S[{t-1}] = k_range(S[{t-1}], S[{t}], {1 if op == V.RANGE_INC else 0});", None
        if op == V.NEG: return f"S[{t}] = f_neg(S[{t}]);", None
        if op == V.NOT: return f"S[{t}] = mkbool(!truthy(S[{t}]));", None
        if op == V.JUMP:
            # 循环回边是 GC 安全点：长时间不分配的循环也不会挡住其他线程的回收
            if a <= self.cur_pc: return f"SAFEPOINT(); CANCEL_POINT(); goto I{a};", None
            return f"goto I{a};", None
        if op == V.JIF: return f"if (!truthy(S[{t}])) goto I{a};", None
        if op == V.JIF_OR_POP: return f"if (!truthy(S[{t}])) goto I{a};", None
        if op == V.JIT_OR_POP: return f"if (truthy(S[{t}])) goto I{a};", None
        if op == V.CALL:
            n = a; b = d - n - 1
            fk = known.get(b)
            if fk is not None:
                return f"S[{b}] = p{fk}(&CL{fk}, {n}, &S[{b + 1}]);", None
            return f"S[{b}] = call_v(S[{b}], {n}, &S[{b + 1}]);", None
        if op == V.CALL_M:
            name, n = a; b = d - n - 1
            return f"S[{b}] = call_m({self.sym(name)}, {n}, &S[{b}]);", None
        if op == V.RET: return f"rv = S[{t}]; goto ret;", None
        if op == V.CLOSURE:
            k = self.pid[id(a)]
            parts = [f"{{ V c = mkclosure(p{k}, {c_str(a.name)}, {len(a.updesc)}); KFn *f = FN(c);"]
            for i, (is_local, idx) in enumerate(a.updesc):
                parts.append(f"f->ups[{i}] = find_up(&open, &L[{idx}]);" if is_local else f"f->ups[{i}] = self->ups[{idx}];")
            parts.append(f"S[{d}] = c; }}")
            return " ".join(parts), None
        if op == V.LIST:
            b = d - a
            return f"S[{b}] = mklist({a}, &S[{b}]);", None
        if op == V.SPAWN:
            if a == 0: return f"S[{d}] = UNIT;", None
            b = d - a
            return f"S[{b}] = k_spawn({a}, &S[{b}]);", None
        if op == V.TUPLE:
            if a == 0: return f"S[{d}] = UNIT;", None
            b = d - a
            return f"S[{b}] = mktuple({a}, &S[{b}]);", None
        if op == V.STRUCT:
            name, fields = a; n = len(fields); b = d - n
            return f"S[{b}] = k_struct(&ST_{self.tid[name]}, {n}, {self.symarr(fields)}, &S[{b}]);", None
        if op == V.FIELD: return f"S[{t}] = k_field(S[{t}], {self.sym(a)});", None
        if op == V.TIDX: return f"S[{t}] = k_tidx(S[{t}], {a});", None
        if op == V.INDEX: return f"S[{t-1}] = k_index(S[{t-1}], S[{t}]);", None
        if op == V.WFIELD: return f"S[{t-1}] = k_wfield(S[{t-1}], {self.sym(a)}, S[{t}]);", None
        if op == V.WINDEX: return f"S[{t-2}] = k_windex(S[{t-2}], S[{t-1}], S[{t}]);", None
        if op == V.INTERP:
            b = d - a
            return f"S[{b}] = k_interp({a}, &S[{b}]);", None
        if op == V.TEST_PAT: return f"S[{t}] = mkbool({self.pat(a)}(S[{t}], L));", None
        if op == V.MATCH_OR_FAIL:
            return f"if (!{self.pat(a)}(S[{t}], L)) die(\"解构失败: %s\", cshow(S[{t}]));", None
        if op == V.MATCH_FAIL: return f"die(\"match 没有覆盖值 %s\", cshow(S[{t}]));", None
        if op == V.TRY:
            return (f"{{ V v = S[{t}]; int vr = v.t == T_ENUM ? ENUMV(v)->var : -1;"
                    f" if (vr == SYM_Ok || vr == SYM_Some) S[{t}] = ENUMV(v)->a[0];"
                    f" else if (vr == SYM_Err || vr == SYM_None) {{ rv = v; goto ret; }}"
                    f" else die(\"? 只能用于 Result 或 Option\"); }}"), None
        if op == V.ITER: return f"L[{a}] = k_iter(S[{t}]);", None
        if op == V.FOR_NEXT: return f"if (!k_next(L[{a[0]}], &S[{d}])) goto I{a[1]};", None
        if op == V.CLOSE: return f"if (open) close_ups(&open, &L[{a}]);", None
        if op == V.DEFER: return f"defers[nd++] = S[{t}];", None
        if op == V.RUN_DEFERS: return f"while (nd > {a}) {{ nd--; call_v(defers[nd], 0, NULL); }}", None
        raise KiteError(f"原生后端: 不支持的指令 {V.OPNAMES[op]}")

    def const(self, v):
        if v is True or v is False: return f"mkbool({int(v)})"
        if isinstance(v, int):
            if v >= 2 ** 63 or v < -2 ** 63: raise KiteError(f"整数常量 {v} 超出 64 位范围")
            return f"mkint({v}LL)" if v != -2 ** 63 else "mkint(INT64_MIN)"
        if isinstance(v, float):
            if v != v: return "mkflt(NAN)"
            if v in (float("inf"), float("-inf")): return "mkflt(INFINITY)" if v > 0 else "mkflt(-INFINITY)"
            return f"mkflt({v.hex()})"
        if isinstance(v, str): return self.sconst(v)
        if isinstance(v, UnitType) or v is Unit: return "UNIT"
        raise KiteError(f"原生后端: 不支持的常量 {v!r}")

    # 模式 → C 函数
    def pat(self, p):
        k = len(self.pats)
        self.pats.append(None)
        name = f"pat{k}"
        t = p[0]
        lines = [f"static bool {name}(V v, V *L) {{"]
        w = lines.append
        if t == "wild":
            w("  (void)v; (void)L; return true;")
        elif t == "bind":
            w(f"  L[{p[1]}] = v; return true;")
        elif t == "gbind":
            w(f"  (void)L; G[{self.glob(p[1])}] = v; return true;")
        elif t == "lit":
            c = p[1]
            if c is True or c is False: w(f"  (void)L; return v.t == T_BOOL && v.u.i == {int(c)};")
            elif isinstance(c, int): w(f"  (void)L; return v.t == T_INT && v.u.i == {c}LL;")
            elif isinstance(c, float): w(f"  (void)L; return v.t == T_FLOAT && v.u.f == {c.hex()};")
            elif isinstance(c, str): w(f"  (void)L; return v.t == T_STR && k_eq(v, {self.sconst(c)});")
            else: w("  (void)L; return v.t == T_UNIT;")
        elif t == "range":
            lo, hi, inc = p[1], p[2], p[3]
            probe = lo if lo is not None else hi
            conds = ["v.t == T_STR" if isinstance(probe, str) else "isnum(v)"]
            if lo is not None: conds.append(f"k_cmp(v, {self.const(lo)}) >= 0")
            if hi is not None: conds.append(f"k_cmp(v, {self.const(hi)}) {'<=' if inc else '<'} 0")
            w("  (void)L; return " + " && ".join(conds) + ";")
        elif t == "at":
            sb, ss = self.pat(p[1]), self.pat(p[2])
            w(f"  return {ss}(v, L) && {sb}(v, L);")
        elif t == "or":
            subs = [self.pat(x) for x in p[1]]
            w("  return " + " || ".join(f"{s}(v, L)" for s in subs) + ";")
        elif t == "tuple":
            subs = [self.pat(x) for x in p[1]]
            w(f"  if (v.t != T_TUPLE || TUP(v)->n != {len(subs)}) return false;")
            for i, s in enumerate(subs): w(f"  if (!{s}(TUP(v)->a[{i}], L)) return false;")
            w("  return true;")
        elif t == "list":
            subs = [self.pat(x) for x in p[1]]; n = len(subs)
            w("  if (v.t != T_LIST) return false;")
            post = [self.pat(x) for x in (p[3] if len(p) > 3 else [])]; m = len(post)
            if p[2] is None: w(f"  if (LIST(v)->len != {n}) return false;")
            w(f"  if (LIST(v)->len < {n + m}) return false;")
            for i, s in enumerate(subs): w(f"  if (!{s}(LIST(v)->a[{i}], L)) return false;")
            for i, s in enumerate(post): w(f"  if (!{s}(LIST(v)->a[LIST(v)->len - {m - i}], L)) return false;")
            if p[2] is not None:
                r = self.pat(p[2])
                w(f"  return {r}(slice(LIST(v), {n}, LIST(v)->len - {m}), L);")
            else:
                w("  return true;")
        elif t == "variant":
            vn, subs = p[1], p[2]
            sid = self.sym(vn)
            w(f"  if (!(v.t == T_ENUM && ENUMV(v)->var == {sid})) {{")
            if subs is None and vn not in self.vm.variants:
                w(f"    return v.t == T_STRUCT && !strcmp(STRUCTV(v)->ty->name, {c_str(vn)});")
            else:
                w("    return false;")
            w("  }")
            if subs is None:
                w("  (void)L; return true;")
            else:
                cs = [self.pat(x) for x in subs]
                w(f"  if (ENUMV(v)->n != {len(cs)}) die(\"模式 {vn} 需要 %d 个字段\", ENUMV(v)->n);")
                for i, s in enumerate(cs): w(f"  if (!{s}(ENUMV(v)->a[{i}], L)) return false;")
                w("  return true;")
        elif t == "struct":
            sn, fs = p[1], p[2]
            cs = [(self.sym(f), self.pat(x)) for f, x in fs]
            w(f"  if (v.t == T_STRUCT && !strcmp(STRUCTV(v)->ty->name, {c_str(sn)})) {{")
            for fsym, s in cs:
                w(f"    {{ int i = sfield(STRUCTV(v), {fsym}); if (i < 0 || !{s}(STRUCTV(v)->f[i], L)) return false; }}")
            w("    return true;")
            w(f"  }} else if (v.t == T_ENUM && ENUMV(v)->var == {self.sym(sn)} && ENUMV(v)->names) {{")
            for fsym, s in cs:
                w(f"    {{ int i, ok = 0; for (i = 0; i < ENUMV(v)->n; i++) if (ENUMV(v)->names[i] == {fsym}) {{ ok = {s}(ENUMV(v)->a[i], L); break; }} if (!ok) return false; }}")
            w("    return true;")
            w("  }")
            w("  return false;")
        else:
            raise KiteError(f"原生后端: 未知模式 {t}")
        w("}")
        self.pats[k] = "\n".join(lines)
        return name

    # 组装
    def assemble(self, chunk, bodies):
        vmach = self.vm
        out = []
        w = out.append
        with open(RUNTIME, encoding="utf-8") as f:
            rt = f.read()
        with open(os.path.join(HERE, "runtime", "kite_case.h"), encoding="utf-8") as f:
            rt = rt.replace('#include "kite_case.h"\n', f.read(), 1)
        w(rt)
        w("\n/* ───────────── 生成代码 ───────────── */")
        # 先确保所有符号都已登记
        ctor_defs = []
        etype_cases = []
        for en, et in self.enums.items():
            tid = self.tid[en]
            cases = []
            for vn, fs in et.variants.items():
                sid = self.sym(vn)
                if en in ("Result", "Option"):
                    if vn == "None": cases.append(f"case {sid}: return kNone();")
                    else: cases.append(f"case {sid}: return mkp(T_FN, &bf_b_{vn});")
                elif fs:
                    rec = type(fs).__name__ == "RecFields"        # 记录变体：字段名数组末尾加 __rec 标记
                    names = self.symarr([f for f, _ in fs] + (["__rec"] if rec else []))
                    ctor_defs.append((tid, sid, len(fs), names, vn))
                    cases.append(f"case {sid}: return mkp(T_FN, &CT_{tid}_{sid});")
                else:
                    cases.append(f"case {sid}: return mkenum({tid}, {sid}, 0, NULL, NULL);")
            etype_cases.append((tid, cases))
        for sn, st in self.structs.items():
            self.symarr(st.fields)
        method_cases = {}
        for tn, tbl in vmach.methods.items():
            method_cases[self.tid[tn]] = [(self.sym(m), self.pid[id(c.proto)]) for m, c in tbl.items()]
        ufcs = []
        for name in self.gnames:
            v = vmach.globals.get(name)
            if name in self.symid and (type(v) is Closure or isinstance(v, Builtin)):
                ufcs.append((self.symid[name], self.gid[name]))
        pat_code = list(self.pats)
        # 符号与类型名
        w("const char *SYMS[] = {" + ", ".join(c_str(s) for s in self.syms) + "};")
        w("const char *TNAMES[] = {" + ", ".join(c_str(s) for s in self.types) + "};")
        w(f"static V G[{max(len(self.gnames), 1)}];")
        w(f"static V KS[{max(len(self.strs), 1)}];")
        for i, arr in enumerate(self.fsyms):
            w(f"static const int FS{i}[] = {{{', '.join(map(str, arr)) or '0'}}};")
        for sn, st in self.structs.items():
            tid = self.tid[sn]
            w(f"static KSType ST_{tid} = {{ {tid}, {c_str(sn)}, {len(st.fields)}, {self.symarr(st.fields)} }};")
        for en in self.enums:
            tid = self.tid[en]
            w(f"static KEType ET_{tid} = {{ {tid}, {c_str(en)} }};")
        for tid, sid, n, names, vn in ctor_defs:
            w(f"static KFn CT_{tid}_{sid} = {{ ctor_code, {c_str(vn)}, {tid}, {sid}, {n}, {names}, 0 }};")
        # 前向声明
        for k, p in enumerate(self.protos):
            w(f"static V p{k}(KFn *self, int argc, V *argv);")
            w(f"static KFn CL{k} = {{ p{k}, {c_str(p.name)}, 0, 0, 0, 0, 0 }};")
        w("static bool pat_dummy(void) { return false; }")
        # 快速路径
        w(r"""
static inline V f_add(V a, V b) { int64_t r; if (a.t == T_INT && b.t == T_INT && !__builtin_add_overflow(a.u.i, b.u.i, &r)) return mkint(r); return k_add(a, b); }
static inline V f_sub(V a, V b) { int64_t r; if (a.t == T_INT && b.t == T_INT && !__builtin_sub_overflow(a.u.i, b.u.i, &r)) return mkint(r); return k_sub(a, b); }
static inline V f_mul(V a, V b) { int64_t r; if (a.t == T_INT && b.t == T_INT && !__builtin_mul_overflow(a.u.i, b.u.i, &r)) return mkint(r); return k_mul(a, b); }
static inline V f_lt(V a, V b) { if (a.t == T_INT && b.t == T_INT) return mkbool(a.u.i < b.u.i); k_ordchk("<", a, b); return mkbool(k_cmp(a, b) < 0); }
static inline V f_gt(V a, V b) { if (a.t == T_INT && b.t == T_INT) return mkbool(a.u.i > b.u.i); k_ordchk(">", a, b); return mkbool(k_cmp(a, b) > 0); }
static inline V f_le(V a, V b) { if (a.t == T_INT && b.t == T_INT) return mkbool(a.u.i <= b.u.i); k_ordchk("<=", a, b); return mkbool(k_cmp(a, b) <= 0); }
static inline V f_ge(V a, V b) { if (a.t == T_INT && b.t == T_INT) return mkbool(a.u.i >= b.u.i); k_ordchk(">=", a, b); return mkbool(k_cmp(a, b) >= 0); }
static inline V f_eq(V a, V b) { if (a.t == T_INT && b.t == T_INT) return mkbool(a.u.i == b.u.i); return mkbool(k_eq(a, b)); }
static inline V f_ne(V a, V b) { if (a.t == T_INT && b.t == T_INT) return mkbool(a.u.i != b.u.i); return mkbool(!k_eq(a, b)); }
static inline V f_neg(V a) {
  if (a.t == T_INT) { if (a.u.i == INT64_MIN) die("整数溢出"); return mkint(-a.u.i); }
  if (a.t == T_FLOAT) return mkflt(-a.u.f);
  die("不能对 %s 取负", tname(a)); return UNIT;
}""")
        # 方法表
        w("static KFn *find_method(int tid, int sym) {")
        w("  switch (tid) {")
        for tid, ms in method_cases.items():
            w(f"  case {tid}: switch (sym) {{ " + " ".join(f"case {s}: return &CL{k};" for s, k in ms) + " } break;")
        w("  }")
        w("  return NULL;")
        w("}")
        w("static int global_of_sym(int sym) {")
        w("  switch (sym) { " + " ".join(f"case {s}: return {g};" for s, g in ufcs) + " }")
        w("  return -1;")
        w("}")
        w("static V etype_member(KEType *et, int sym) {")
        w("  switch (et->id) {")
        for tid, cases in etype_cases:
            w(f"  case {tid}: switch (sym) {{ " + " ".join(cases) + " } break;")
        w("  }")
        w("  die(\"%s 没有变体 %s\", et->name, SYMS[sym]); return UNIT;")
        w("}")
        w("""static V call_m(int sym, int n, V *base) {
  V recv = base[0];
  if (recv.t == T_MODULE || recv.t == T_ETYPE) return call_v(k_field(recv, sym), n, base + 1);
  KFn *m = find_method(tid_of(recv), sym);
  if (m) return m->code(m, n + 1, base);
  if (recv.t == T_STRUCT) { int i = sfield(STRUCTV(recv), sym); if (i >= 0) return call_v(STRUCTV(recv)->f[i], n, base + 1); }
  int g = global_of_sym(sym);
  if (g >= 0 && G[g].t == T_FN) return call_v(G[g], n + 1, base);
  die("%s 没有方法 %s", tname(recv), SYMS[sym]); return UNIT;
}""")
        # 模式函数（前向声明，因为互相引用）
        for i in range(len(pat_code)):
            w(f"static bool pat{i}(V v, V *L);")
        for c in pat_code:
            w(c)
        for b in bodies:
            w(b)
        # 初始化
        # extern fn：生成直接调用 C 符号的包装（用汇编名绑定，避免和运行时已包含的头文件声明冲突）
        CTY = {"Int": "int64_t", "Float": "double", "Bool": "bool", "Str": "const char *", "Unit": "void", "Ptr": "void *",
               "I8": "int8_t", "I16": "int16_t", "I32": "int32_t", "I64": "int64_t", "U8": "uint8_t", "U16": "uint16_t",
               "U32": "uint32_t", "U64": "uint64_t", "F32": "float", "F64": "double"}
        from kite import C_RANGES
        self.ext_id, self.libs = {}, []
        exts = [(n, v) for n, v in vmach.globals.items() if isinstance(v, Builtin) and getattr(v, "ext", None)]
        if exts:
            w("#define KX_STR2(x) #x\n#define KX_STR(x) KX_STR2(x)")
            w("static int64_t kx_rng(int64_t v, int64_t lo, int64_t hi, const char *t) "
              "{ if (v < lo || v > hi) die(\"extern: %lld 超出 %s 的范围\", (long long)v, t); return v; }")
        cstructs = {}
        def cty(d):
            if not isinstance(d, tuple): return CTY[d]
            if d[0] == "F": return "void *"
            if d not in cstructs:
                body = " ".join(f"{cty(fd)} {f};" for f, fd in d[2])
                cstructs[d] = f"kxs_{len(cstructs)}_{d[1]}"
                w(f"typedef struct {{ {body} }} {cstructs[d]};")
            return cstructs[d]
        def to_c(src, d):
            # Kite 值 → C 值（结构体按声明顺序取字段，用 GNU 语句表达式拼出 C 结构体）
            if not isinstance(d, tuple):
                if d in C_RANGES:
                    lo, hi = C_RANGES[d]
                    return f"(({CTY[d]})kx_rng(({src}).u.i, {lo}LL, {hi}LL, {c_str(d)}))"
                flt = "(({s}).t == T_INT ? (double)({s}).u.i : ({s}).u.f)"
                return {"Int": "({s}).u.i", "I64": "({s}).u.i", "U64": "(uint64_t)({s}).u.i", "Float": flt, "F64": flt,
                        "F32": "(float)" + flt, "Bool": "(bool)({s}).u.i",
                        "Str": "CSTR({s})", "Ptr": "(void *)(intptr_t)({s}).u.i"}[d].format(s=src)
            sets = " ".join(f"t_.{f} = {to_c(f'STRUCTV({src})->f[{k}]', fd)};" for k, (f, fd) in enumerate(d[2]))
            return f"({{ {cty(d)} t_; {sets} t_; }})"
        def from_c(src, d):
            if not isinstance(d, tuple):
                if d in CTY and d[0] in "IU": return f"mkint((int64_t)({src}))"
                if d in ("F32", "F64"): return f"mkflt((double)({src}))"
                return {"Int": "mkint({s})", "Float": "mkflt({s})", "Bool": "mkbool({s})",
                        "Ptr": "mkint((int64_t)(intptr_t)({s}))", "Str": "cstr(({s}) ? ({s}) : \"\")"}[d].format(s=src)
            vals = ", ".join(from_c(f"({src}).{f}", fd) for f, fd in d[2])
            fs = [f for f, _ in d[2]]
            return (f"({{ V v_[{len(fs)}] = {{ {vals} }}; k_struct(&ST_{self.tid[d[1]]}, {len(fs)}, "
                    f"{self.symarr(fs)}, v_); }})")
        for i, (n, v) in enumerate(exts):
            cname, ptys, rty, libs, nfixed = v.ext
            self.ext_id[n] = i
            for l in libs:
                if l not in self.libs: self.libs.append(l)
            if nfixed is not None:     # 可变参数变体：C 原型写 …，多出来的参数由 C 编译器做默认提升
                proto = ", ".join([cty(t) for t in ptys[:nfixed]] + ["..."])
            else:
                proto = ", ".join(cty(t) for t in ptys) or "void"
            w(f"extern {cty(rty)} kx_sym{i}({proto}) __asm__(KX_STR(__USER_LABEL_PREFIX__) {c_str(cname)});")
            # C 回调：每个回调参数一个跳板函数 + 线程局部槽；调用期间槽里放 Kite 函数，返回后恢复（支持重入）
            cbs = [j for j, t in enumerate(ptys) if isinstance(t, tuple) and t[0] == "F"]
            for j in cbs:
                _, cps, cr = ptys[j]
                w(f"static __thread V kxcb{i}_{j};")
                params = ", ".join(f"{CTY[t]} a{k}" for k, t in enumerate(cps)) or "void"
                vals = ", ".join(from_c(f"a{k}", t) for k, t in enumerate(cps))
                arr = f"V cv_[{max(len(cps), 1)}] = {{ {vals or 'UNIT'} }};"
                callv = f"call_v(kxcb{i}_{j}, {len(cps)}, cv_)"
                body = f"{arr} {callv};" if cr == "Unit" else f"{arr} V r_ = {callv}; return {to_c('r_', cr)};"
                w(f"static {CTY[cr]} kxtr{i}_{j}({params}) {{ {body} }}")
            args = ", ".join(f"(void *)kxtr{i}_{j}" if j in cbs else to_c(f"argv[{j}]", t) for j, t in enumerate(ptys))
            call = f"kx_sym{i}({args})"
            save = " ".join(f"V sv{j}_ = kxcb{i}_{j}; kxcb{i}_{j} = argv[{j}];" for j in cbs)
            rest = " ".join(f"kxcb{i}_{j} = sv{j}_;" for j in cbs)
            if rty == "Unit": ret = f"{save} {call}; {rest} return UNIT;"
            else: ret = f"{save} {cty(rty)} r = {call}; {rest} return {from_c('r', rty)};"
            w(f"B(bx{i}) {{ NARGS({len(ptys)}, {len(ptys)}); {ret} }}")
            w(f"static KFn bfx{i} = {{ bx{i}, {c_str(cname)}, 0, 0, 0, 0, 0 }};")
        w("static void kite_main(void) {")
        w(f"  KITE_FILE = {c_str(self.src_name)};")
        import kite as _k
        if len(_k.SOURCES) > 1:
            w(f"  static const char *files[] = {{ {', '.join(c_str(os.path.basename(f) if i == 0 else f) for i, f in enumerate(_k.SOURCES))} }};")
            w(f"  KITE_FILES = files; KITE_NFILES = {len(_k.SOURCES)};")
        w(f"  gc_add_root(G, {max(len(self.gnames), 1)}); gc_add_root(KS, {max(len(self.strs), 1)});")
        if "__rec" in self.symid: w(f"  KITE_SYM_REC = {self.symid['__rec']};")
        w(f"  SYM_Ok = {self.symid['Ok']}; SYM_Err = {self.symid['Err']}; SYM_Some = {self.symid['Some']}; SYM_None = {self.symid['None']};")
        for i, s in enumerate(self.strs):
            w(f"  KS[{i}] = mkstr({c_str(s)}, {len(s.encode('utf-8'))});")
        for name in self.gnames:
            g = self.gid[name]
            v = vmach.globals.get(name, None)
            init = self.global_init(name, v)
            if init: w(f"  G[{g}] = {init};")
        w(f"  p{self.pid[id(chunk)]}(&CL{self.pid[id(chunk)]}, 0, NULL);")
        mg = self.gid["main"]
        mv = vmach.globals.get("main")
        if type(mv) is Closure:
            w(f"  if (G[{mg}].t == T_FN) call_v(G[{mg}], 0, NULL);")
        w("}")
        return "\n".join(out) + "\n"

    def global_init(self, name, v):
        if v is None:
            return None
        if isinstance(v, Builtin) and name in getattr(self, "ext_id", {}):
            return f"mkp(T_FN, &bfx{self.ext_id[name]})"
        if type(v) is Closure:
            return f"mkp(T_FN, &CL{self.pid[id(v.proto)]})"
        if isinstance(v, StructType):
            return f"mkp(T_STYPE, &ST_{self.tid[name]})"
        if isinstance(v, EnumType):
            return f"mkp(T_ETYPE, &ET_{self.tid[v.name]})"
        if isinstance(v, EnumVal):
            if v.variant == "None": return "kNone()"
            return f"mkenum({self.tid[v.ename]}, {self.sym(v.variant)}, 0, NULL, NULL)"
        if isinstance(v, Module):
            if v.name not in ("io", "fs", "math", "ptr"):
                raise KiteError(f"原生后端暂不支持模块 {v.name}")
            return f"mkp(T_MODULE, (void *){c_str(v.name)})"
        if isinstance(v, Builtin):
            # 用户枚举的变体构造器
            for en, et in self.enums.items():
                if en not in ("Result", "Option") and v.name in et.variants and et.variants[v.name]:
                    return f"mkp(T_FN, &CT_{self.tid[en]}_{self.sym(v.name)})"
            if v.name in BUILTIN_C:
                return f"mkp(T_FN, &bf_{BUILTIN_C[v.name]})"
            for mod, members in self.vm.rt.std.items():
                if v.name in members:
                    return f"module_get({c_str(mod)}, {c_str(v.name)})"
            raise KiteError(f"原生后端暂不支持内建函数 {v.name}")
        if isinstance(v, bool): return f"mkbool({int(v)})"
        if isinstance(v, (int, float, str)): return self.const(v)
        return None


def find_cc():
    env = os.environ.get("CC")
    if env: return env.split()
    for c in ("cc", "clang", "gcc"):
        if shutil.which(c): return [c]
    try:
        import ziglang  # noqa: F401
        # 默认 -mcpu=native 在部分虚拟化 CPU 上会生成非法指令（zig 0.17 实测），改用通用基线
        return [sys.executable, "-m", "ziglang", "cc", "-mcpu=baseline"]
    except ImportError:
        pass
    return None


def build(src, src_path, out_path, emit_c=None, opt="-O2", typecheck=True, typed=False):
    """typed=True：调用方已经对 src（语法树）做过类型检查，节点带类型，可以走 KIR"""
    vmach = V.VM(typecheck=typecheck)
    vmach.compiler.append_ops = False
    chunk = vmach.compile(src)
    gen = Gen(vmach, os.path.basename(src_path))
    if (typecheck or typed) and os.environ.get("KITE_NO_KIR") != "1":
        gen.kir_fns, gen.kir_why = kir.collect(vmach.last_items)
    csrc = gen.generate(chunk)
    c_path = emit_c or (out_path + ".c")
    with open(c_path, "w", encoding="utf-8") as f:
        f.write(csrc)
    cc = find_cc()
    if cc is None:
        raise KiteError("找不到 C 编译器：请安装 clang / gcc，或 pip install ziglang；也可设置环境变量 CC")
    cmd = cc + [opt, "-w", "-o", out_path, c_path, "-lm", "-lpthread"] + [f"-l{l}" for l in getattr(gen, "libs", [])]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise KiteError("C 编译失败（原生后端内部错误）:\n" + r.stderr[-3000:])
    if emit_c is None:
        os.remove(c_path)
    return out_path
