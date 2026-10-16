"""Dump KIR (after optimization) for every function kir.collect accepts; self-hosting step 3d: `kitec kir` must match byte for byte.

    python3 tools/kir_dump.py FILE

Compiles like native.build (type check on, APPEND_L off), then runs kir.collect(vmach.last_items).
Output: for each function in kir.all_funcs order (sorted names, lambdas after their owner) a block

    fn NAME (P v0:i64:x, ...) -> RET [closure LABEL caps v3:val:y ...] npb N ndefer N
    pat K <ast_dump of compiled pattern>
    b0:
      L12 v3:i64 = add.i64 v1, 5 | extra
      ...
      <terminator>

then `why NAME: reason` lines sorted by name. Load / type errors are printed like tools/c_dump.py.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kite
sys.modules.setdefault("kite", kite)
from kite import Parser, lex, KiteError
import ast_dump


def cs(s):
    from native import c_str
    return c_str(s)


def A(a):
    import kir
    if isinstance(a, kir.Reg): return f"v{a.id}"
    if a.ty == "val": return "()" if a.v is kir.UNIT_V else "S" + cs(a.v)
    if a.ty == "b1": return "true" if a.v else "false"
    if a.ty == "i64": return str(a.v)
    v = float(a.v)
    if v != v: return "nan"
    if v in (float("inf"), float("-inf")): return "inf" if v > 0 else "-inf"
    return v.hex()


def R(r):
    return f"v{r.id}:{r.ty}" + (f":{r.name}" if r.name else "")


def X(op, x):
    import kir
    if x is None: return ""
    if op == "next": return R(x)
    if op == "closure": return x.name
    if op == "struct": return x[0] + " " + ",".join(x[1])
    if op == "interp": return ",".join("_" if p is None else cs(p) for p in x)
    if isinstance(x, str): return x
    return str(x)


def rend(v):
    out = []
    ast_dump.dump(v, out)
    return "".join(out)


def dump(f, L):
    hdr = f"fn {f.name} (" + ", ".join(R(p) for p in f.params) + f") -> {f.ret}"
    if f.closure: hdr += f" closure {getattr(f, 'label', '<lambda>')} caps " + " ".join(R(c) for c in f.caps)
    hdr += f" npb {f.npb} ndefer {f.ndefer} line {f.line}"
    L.append(hdr)
    for i, p in enumerate(f.pats): L.append(f"pat {i} {rend(p)}")
    for b in f.blocks:
        L.append(f"b{b.id}:")
        for ins in b.instrs:
            lhs = R(ins.dst) + " = " if ins.dst is not None else ""
            ex = X(ins.op, ins.extra)
            L.append(f"  L{ins.line} {lhs}{ins.op} " + ", ".join(A(a) for a in ins.args) + (f" | {ex}" if ex != "" else ""))
        t = b.term
        if t is None or t[0] == "unreachable": L.append("  unreachable")
        elif t[0] == "br": L.append(f"  br b{t[1]}")
        elif t[0] == "cbr": L.append(f"  cbr {A(t[1])}, b{t[2]}, b{t[3]}")
        elif t[0] == "ret": L.append("  ret" + (f" {A(t[1])}" if t[1] is not None else ""))
        elif t[0] == "panic": L.append(f"  panic L{t[3]} {cs(t[1])}" + (f" {A(t[2])}:{t[2].ty}" if t[2] is not None else ""))


def main(path):
    import modules, vm as V, kir
    modules._LM_COUNTER[0] = 0
    try:
        Parser(lex(open(path, encoding="utf-8").read())).program()
    except KiteError:
        print("SKIP parse"); return
    try:
        items = modules.load_program(path)
    except KiteError as e:
        if getattr(e, "loc", None): print("SKIP parse")
        else: print(f"L {e.line or 0} {getattr(e, 'msg', str(e))}")
        return
    vmach = V.VM(typecheck=True)
    vmach.compiler.append_ops = False
    try:
        vmach.compile(items)
    except kite.CheckFailed as e:
        for x in sorted(e.errors, key=lambda x: x.line or 0): print(f"E {x.line or 0} {getattr(x, 'msg', str(x))}")
        return
    except KiteError as e:
        print(f"C {e.line or 0} {getattr(e, 'msg', str(e))}"); return
    fns, why = kir.collect(vmach.last_items)
    L = []
    for f in kir.all_funcs(fns): dump(f, L)
    for n in sorted(why): L.append(f"why {n}: {why[n]}")
    print("\n".join(L))


if __name__ == "__main__":
    sys.setrecursionlimit(100000)
    main(sys.argv[1])
