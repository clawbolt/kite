"""Dump the C source the native backend generates without KIR (KITE_NO_KIR=1); self-hosting step 3c: `kitec c` must match byte for byte.

    python3 tools/c_dump.py FILE          （KITE_NO_KIR=1，对应 kitec c）
    python3 tools/c_dump.py --kir FILE    （走 KIR，和 native.build 默认相同；对应 kitec ck，自举第 3d 步）

Compiles like native.build (type check on, APPEND_L off), then prints native.Gen.generate(chunk) with no KIR functions.
Backend error → "N msg". Load error → "L line msg", syntax error in an imported file → "SKIP parse", type errors → "E line msg".
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kite
sys.modules.setdefault("kite", kite)
from kite import Parser, lex, KiteError, Node, Unit
import ast_dump


def main(path, use_kir=False):
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
    import native
    gen = native.Gen(vmach, os.path.basename(path))
    if use_kir:
        import kir
        gen.kir_fns, gen.kir_why = kir.collect(vmach.last_items)
    try:
        csrc = gen.generate(chunk)
    except KiteError as e:
        print(f"N {getattr(e, 'msg', str(e))}"); return
    print(csrc)


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
    if sys.argv[1] == "--kir": main(sys.argv[2], True)
    else: main(sys.argv[1])
