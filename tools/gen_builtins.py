"""从 typecheck.py 的 BUILTIN_SIGS / STD_SIGS 生成 kitec/src/builtins.kite（自举类型检查器用）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kite; sys.modules.setdefault("kite", kite)
import typecheck as tc

def q(s): return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("{", "\\{") + '"'

def ent(name, sig):
    if sig == tc.VARIADIC: return f"  ({q(name)}, \"v\", []),"
    if isinstance(sig, dict):
        return f"  ({q(name)}, \"d\", [" + ", ".join(f"({q(k)}, {q(v)})" for k, v in sig.items()) + "]),"
    return f"  ({q(name)}, \"s\", [(\"\", {q(sig)})]),"

out = ["// 由 tools/gen_builtins.py 从 typecheck.py 生成，不要手改", "",
       "pub fn builtin_sigs() -> List<(Str, Str, List<(Str, Str)>)> {", "  ["]
out += [ent(n, s) for n, s in tc.BUILTIN_SIGS.items()]
out += ["  ]", "}", "", "pub fn std_sigs() -> List<(Str, List<(Str, Str, List<(Str, Str)>)>)> {", "  ["]
for m, tbl in tc.STD_SIGS.items():
    out.append(f"  ({q(m)}, [")
    out += ["  " + ent(n, s) for n, s in tbl.items()]
    out.append("  ]),")
out += ["  ]", "}", ""]
open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kitec/src/builtins.kite"), "w").write("\n".join(out))
