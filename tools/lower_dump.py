"""Dump the AST after module loading, prelude/lowering, type checking and erase_typeapps, i.e. exactly
what vm.Compiler.compile_chunk receives (self-hosting step 3a: `kitec lower` must match byte for byte).

    python3 tools/lower_dump.py FILE

Same format as tools/ast_dump.py; node attributes starting with "_" (checker annotations) are skipped.
Load error → "L line msg"; syntax error in an imported file → "SKIP parse"; type errors → "E line msg" lines only.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kite
sys.modules.setdefault("kite", kite)
from kite import Parser, lex, KiteError, Node
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ast_dump


def strip(v, seen):
    if isinstance(v, Node):
        out = Node(v.kind, v.line)
        for k, x in v.__dict__.items():
            if k in ("kind", "line") or k.startswith("_"): continue
            out.__dict__[k] = strip(x, seen)
        return out
    if isinstance(v, kite.RecFields): return kite.RecFields(strip(x, seen) for x in v)
    if isinstance(v, list): return [strip(x, seen) for x in v]
    if isinstance(v, tuple) and not isinstance(v, kite.QName): return tuple(strip(x, seen) for x in v)
    if isinstance(v, dict): return {k: strip(x, seen) for k, x in v.items()}
    return v


def main(path):
    import modules
    from typecheck import Checker
    modules._LM_COUNTER[0] = 0
    try:
        Parser(lex(open(path, encoding="utf-8").read())).program()
    except KiteError:
        print("SKIP parse"); return
    try:
        items = kite.with_prelude(modules.load_program(path))
    except KiteError as e:
        if getattr(e, "loc", None): print("SKIP parse")
        else: print(f"L {e.line or 0} {getattr(e, 'msg', str(e))}")
        return
    c = Checker(); c.quiet = True
    try:
        errs = c.check(items)
    except KiteError as e:
        errs = [e]
    if errs:
        for e in sorted(errs, key=lambda e: e.line or 0): print(f"E {e.line or 0} {getattr(e, 'msg', str(e))}")
        return
    kite.erase_typeapps(items)
    for it in items:
        out = []
        ast_dump.dump(strip(it, None), out)
        print("".join(out))


if __name__ == "__main__":
    sys.setrecursionlimit(100000)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main(sys.argv[1])
