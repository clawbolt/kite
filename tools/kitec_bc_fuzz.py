"""自举第 3b 步的模糊测试：随机变异 token，kitec bc 与 tools/bc_dump.py（字节码）的输出必须相同

    python3 tools/kitec_bc_fuzz.py KITEC_BIN 个数 种子

只对能解析的单文件变异体计数；kitec 报“未支持”的跳过。
"""
import sys, random, subprocess, os, glob, io, contextlib
K = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, K); sys.path.insert(0, K + "/tools")
sys.setrecursionlimit(100000)
import bc_dump as tc_dump
from kite import lex, KiteError
BIN = sys.argv[1]; N = int(sys.argv[2]); seed = int(sys.argv[3])
random.seed(seed)
files = sorted(glob.glob(K + "/examples/*.kite") + glob.glob(K + "/tests/*.kite"))
files = [f for f in files if os.path.getsize(f) < 12000]
def py_out(path):
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf): tc_dump.main(path)
    except Exception:
        return None
    return buf.getvalue()
ok = []
for f in files:
    o = py_out(f)
    if o is None or o.startswith("SKIP"): continue
    r = subprocess.run([BIN, "bc", f, K + "/prelude.kite"], capture_output=True, text=True)
    if not r.stdout.startswith("UNSUP"): ok.append(f)
pieces = ["1", "1.5", '"s"', "true", "x", "Foo", "None", "Some(1)", "[]", "()", "+", "-", "==", "<", "&&", "!", ".len()", ".0",
          "as Float", "?", "self", "Int", "Str", "List<Int>", "_"]
tmp = f"/tmp/kc_tcfuzz{seed}.kite"
bad = done = errs = 0
while done < N:
    f = random.choice(ok)
    src = open(f, encoding="utf-8").read()
    try: ts = [t for t in lex(src) if t.pos is not None and t.kind in ("IDENT", "INT", "FLOAT", "STR", "OP", "KW")]
    except KiteError: continue
    a = random.randrange(len(ts)); p0 = ts[a].pos; p1 = ts[a].epos if ts[a].epos is not None else p0
    op = random.random()
    if op < 0.5:
        b = random.randrange(len(ts)); m = src[:p0] + src[ts[b].pos:(ts[b].epos or ts[b].pos)] + src[p1:]
    elif op < 0.8:
        m = src[:p0] + random.choice(pieces) + src[p1:]
    else:
        m = src[:p0] + src[p1:]
    open(tmp, "w", encoding="utf-8").write(m)
    exp = py_out(tmp)
    if exp is None or exp.startswith("SKIP"): continue
    r = subprocess.run([BIN, "bc", tmp, K + "/prelude.kite"], capture_output=True, text=True, timeout=60)
    got = r.stdout + r.stderr
    if got.startswith("UNSUP"): continue
    done += 1
    if exp.startswith("E"): errs += 1
    if got != exp:
        bad += 1
        if bad <= 6:
            fn = f"/tmp/kitec_tcfz_{seed}_{bad}.kite"; open(fn, "w").write(m)
            print("MISMATCH", os.path.relpath(f, K), fn)
            import difflib
            for l in difflib.unified_diff(exp.splitlines(), got.splitlines(), "python", "kitec", lineterm="", n=0): print("   ", l[:200])
print(f"done={done} errs={errs} bad={bad}")
sys.exit(1 if bad else 0)
