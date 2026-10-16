"""自举对拍的模糊测试：随机删改 examples / tests / std 里的 token，kitec 和 Python 版的输出（AST 或报错）必须相同

    python3 tools/kitec_fuzz.py KITEC_BIN 个数 种子
"""
import sys, random, subprocess, os, glob
K = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, K); sys.path.insert(0, K + "/tools")
sys.setrecursionlimit(100000)
import ast_dump
from kite import lex, Parser, KiteError
BIN = sys.argv[1]; N = int(sys.argv[2]); seed = int(sys.argv[3])
random.seed(seed)
files = sorted(glob.glob(K + "/examples/*.kite") + glob.glob(K + "/tests/*.kite") + glob.glob(K + "/std/**/*.kite", recursive=True))
files = [f for f in files if os.path.getsize(f) < 20000]
toks_cache = {}
def py_dump(src):
    try:
        items = Parser(lex(src)).program()
    except KiteError as e:
        return f"ERR {e.line if e.line is not None else '-'}:{e.col if e.col is not None else '-'} {e.msg}\n"
    except RecursionError:
        return None
    except Exception as e:
        return None   # Python 版自身崩溃的输入跳过
    out = []
    for it in items:
        o = []; ast_dump.dump(it, o); out.append("".join(o))
    return "".join(x + "\n" for x in out)
bad = 0; done = 0; errs = 0
pieces = ["(", ")", "{", "}", "[", "]", ",", ".", "..", "=>", "->", "|", "?", ":", ";", "\n", "let", "fn", "if", "else", "match",
          "x", "Foo", "1", "1.5", '"s{x}"', "'a", "@test", "<", ">", ">>", "&&", "is", "as", "loop", "break 1", "-", "_", "async", ".await"]
tmp = f"/tmp/kc_fuzz{seed}.kite"
while done < N:
    f = random.choice(files)
    src = open(f, encoding="utf-8").read()
    try:
        ts = [t for t in lex(src) if t.pos is not None]
    except KiteError:
        continue
    if len(ts) < 3: continue
    a = random.randrange(len(ts))
    op = random.random()
    p0 = ts[a].pos
    p1 = ts[a].epos if ts[a].epos is not None else p0
    if op < 0.4:
        b = min(len(ts) - 1, a + random.randrange(3))
        e = ts[b].epos or p1
        m = src[:p0] + src[e:]
    elif op < 0.8:
        m = src[:p0] + random.choice(pieces) + " " + src[p0:]
    else:
        b = random.randrange(len(ts)); 
        m = src[:p0] + src[ts[b].pos:(ts[b].epos or ts[b].pos)] + src[p1:]
    exp = py_dump(m)
    if exp is None: continue
    open(tmp, "w", encoding="utf-8").write(m)
    r = subprocess.run([BIN, "parse", tmp], capture_output=True, text=True)
    got = r.stdout + r.stderr
    done += 1
    if exp.startswith("ERR"): errs += 1
    if got != exp:
        bad += 1
        if bad <= 8:
            fn = f"/tmp/kitec_fz_{seed}_{bad}.kite"; open(fn, "w").write(m)
            print("MISMATCH", f, fn); print("  py :", exp[:300].strip()); print("  kc :", got[:300].strip())
print(f"done={done} errs={errs} bad={bad}")
sys.exit(1 if bad else 0)
