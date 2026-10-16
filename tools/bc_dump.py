"""Dump the bytecode that the native backend consumes (self-hosting step 3b: `kitec bc` must match byte for byte).

    python3 tools/bc_dump.py FILE

Compiles like native.build (type check on, APPEND_L off). Output, in native.Gen's order:
  G name fn|struct F[fields]|enum V[<variant fields>…]   every global that is a Closure, StructType or EnumType
  M Type.method Pidx                                       method tables
  P idx name nparams nlocals [<is_local slot>…]            each Proto, in Gen.add_proto order, then one line per instruction:
    pc OP arg line                                         arg in tools/ast_dump.py format; a Proto is {P@idx}, Unit is {U@0}
Load error → "L line msg", syntax error in an imported file → "SKIP parse", type errors → "E line msg".
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kite
sys.modules.setdefault("kite", kite)
from kite import Parser, lex, KiteError, Node, Unit
import ast_dump


def main(path):
    import modules, vm as V
    from vm import Closure, Proto
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
        chunk = vmach.compile(items)
    except kite.CheckFailed as e:
        for x in sorted(e.errors, key=lambda x: x.line or 0): print(f"E {x.line or 0} {getattr(x, 'msg', str(x))}")
        return
    except KiteError as e:
        print(f"C {e.line or 0} {getattr(e, 'msg', str(e))}"); return
    protos, pid = [], {}
    def add(p):
        if id(p) in pid: return
        pid[id(p)] = len(protos); protos.append(p)
        for op, arg in p.code:
            if op == V.CLOSURE: add(arg)
    add(chunk)
    out = []
    for name, v in vmach.globals.items():
        if type(v) is Closure:
            add(v.proto); out.append(f"G {ast_dump.q(name)} fn")
        elif isinstance(v, kite.StructType):
            out.append(f"G {ast_dump.q(name)} struct " + rend(list(v.fields), pid))
        elif isinstance(v, kite.EnumType):
            out.append(f"G {ast_dump.q(name)} enum " + rend([(vn, fs) for vn, fs in v.variants.items()], pid))
    for t, tbl in vmach.methods.items():
        for m, c in tbl.items():
            c.proto.name = f"{t}.{m}"
            add(c.proto)
            out.append(f"M {ast_dump.q(t + '.' + m)} P{pid[id(c.proto)]}")
    for p in protos:
        out.append(f"P {pid[id(p)]} {ast_dump.q(p.name)} {p.nparams} {p.nlocals} " + rend([tuple(u) for u in p.updesc], pid))
        for i, (op, arg) in enumerate(p.code):
            out.append(f"  {i} {V.OPNAMES[op]} {rend(arg, pid)} {p.lines[i]}")
    print("\n".join(out))


def conv(v, pid):
    from vm import Proto
    if isinstance(v, Proto): return Node("P", pid[id(v)])
    if v is Unit: return Node("U", 0)
    if isinstance(v, kite.QName) or isinstance(v, str): return v
    if isinstance(v, tuple): return tuple(conv(x, pid) for x in v)
    if isinstance(v, list): return v.__class__(conv(x, pid) for x in v)
    return v


def rend(v, pid):
    out = []
    ast_dump.dump(conv(v, pid), out)
    return "".join(out)


if __name__ == "__main__":
    sys.setrecursionlimit(100000)
    main(sys.argv[1])
