"""格式化器与语言服务器的自动化测试：python3 tests/test_tooling.py"""
import glob, json, os, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import kite; sys.modules["kite"] = kite
from fmt import fmt

fails = 0
def ok(cond, name):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails += 1

# ── 格式化 ──
for f in sorted(glob.glob(os.path.join(ROOT, "examples/*.kite"))):
    src = open(f, encoding="utf-8").read()
    ok(fmt(src) == src, f"fmt 已格式化 {os.path.relpath(f, ROOT)}")

messy = """type Point={x:Float,y:Float}
fn largest<T:Ord>(xs:List<T>)->Option<T>{
        var best=first(xs)?
  for x in xs{
      if x>best{best=x}   // 更新
  }



  Some(best)
}
fn main(){
let ok=!false&&-3<0
let xs=[1,2,3]
    |>map(x=>x*2)
match largest(xs){
Some(v)=>print(v,ok,1..=3)
None=>print("空")
}
}
"""
want = """type Point = { x: Float, y: Float }
fn largest<T: Ord>(xs: List<T>) -> Option<T> {
  var best = first(xs)?
  for x in xs {
    if x > best { best = x }   // 更新
  }

  Some(best)
}
fn main() {
  let ok = !false && -3 < 0
  let xs = [1, 2, 3]
    |> map(x => x * 2)
  match largest(xs) {
    Some(v) => print(v, ok, 1..=3)
    None    => print("空")
  }
}
"""
got = fmt(messy)
ok(got == want, "fmt 规范化缩进、空格、空行并对齐 match 分支")
if got != want: print(got)
ok(fmt(got) == got, "fmt 幂等")

# ── 语言服务器 ──
p = subprocess.Popen([sys.executable, os.path.join(ROOT, "lsp.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
def send(m):
    b = json.dumps(m).encode(); p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(b) + b); p.stdin.flush()
def recv():
    n = None
    while True:
        l = p.stdout.readline().strip()
        if not l: break
        if l.lower().startswith(b"content-length"): n = int(l.split(b":")[1])
    return json.loads(p.stdout.read(n))
def request(i, method, params):
    send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
    while True:
        r = recv()
        if r.get("id") == i: return r
uri = "file:///t.kite"
doc = {"uri": uri}
init = request(1, "initialize", {"capabilities": {}})
ok(init["result"]["capabilities"]["hoverProvider"], "lsp initialize")

bad = """// 求最大值
fn largest<T>(xs: List<T>) -> T {
  var best = xs[0]
  for x in xs { if x > best { best = x } }
  best
}
type Dog = { name: Str }
fn main() {
  let n: Int = 1.5
  print(largest([3, 1]))
}
"""
send({"jsonrpc": "2.0", "method": "textDocument/didOpen",
      "params": {"textDocument": {"uri": uri, "languageId": "kite", "version": 1, "text": bad}}})
d = recv()["params"]["diagnostics"]
lines = sorted(x["range"]["start"]["line"] for x in d)
ok(len(d) >= 2 and 3 in lines and 8 in lines, f"lsp 诊断报告多个类型错误（行 {[l + 1 for l in lines]}）")

h = request(2, "textDocument/hover", {"textDocument": doc, "position": {"line": 9, "character": 9}})
v = h["result"]["contents"]["value"]
ok("fn largest<T>(xs: List<T>) -> T" in v and "求最大值" in v, "lsp 悬停显示签名与文档注释")

h = request(21, "textDocument/hover", {"textDocument": doc, "position": {"line": 2, "character": 7}})
v = h["result"]["contents"]["value"]
ok("var best: T" in v, f"lsp 悬停显示局部变量类型 {v!r}")
h = request(22, "textDocument/hover", {"textDocument": doc, "position": {"line": 3, "character": 13}})
v = h["result"]["contents"]["value"]
ok("(参数) xs: List<T>" in v, f"lsp 悬停显示参数类型 {v!r}")

df = request(3, "textDocument/definition", {"textDocument": doc, "position": {"line": 9, "character": 9}})
ok(df["result"]["range"]["start"] == {"line": 1, "character": 3}, "lsp 跳转到定义")
df = request(31, "textDocument/definition", {"textDocument": doc, "position": {"line": 3, "character": 30}})
ok(df["result"]["range"]["start"] == {"line": 2, "character": 6}, f"lsp 跳转到局部变量定义 {df.get('result')}")
rn = request(32, "textDocument/rename", {"textDocument": doc, "position": {"line": 2, "character": 7}, "newName": "top"})
pos = sorted((e["range"]["start"]["line"], e["range"]["start"]["character"]) for e in rn["result"]["changes"][uri])
ok(pos == [(2, 6), (3, 23), (3, 30), (4, 2)], f"lsp 重命名局部变量 {pos}")
rn = request(33, "textDocument/rename", {"textDocument": doc, "position": {"line": 1, "character": 4}, "newName": "biggest"})
pos = sorted((e["range"]["start"]["line"], e["range"]["start"]["character"]) for e in rn["result"]["changes"][uri])
ok(pos == [(1, 3), (9, 8)], f"lsp 重命名函数 {pos}")
rn = request(34, "textDocument/rename", {"textDocument": doc, "position": {"line": 1, "character": 4}, "newName": "fn"})
ok("error" in rn, "lsp 重命名拒绝关键字")

sy = request(4, "textDocument/documentSymbol", {"textDocument": doc})
names = [s["name"] for s in sy["result"]]
ok(names == ["largest", "Dog", "main"], f"lsp 文档大纲 {names}")

fm = request(5, "textDocument/formatting", {"textDocument": doc, "options": {"tabSize": 2, "insertSpaces": True}})
new = fm["result"][0]["newText"] if fm["result"] else bad
ok("for x in xs { if x > best { best = x } }" in new, "lsp 格式化")

send({"jsonrpc": "2.0", "method": "textDocument/didChange",
      "params": {"textDocument": {"uri": uri, "version": 2}, "contentChanges": [{"text": "use std.io\nfn main() { io. }\n"}]}})
recv()
c = request(6, "textDocument/completion", {"textDocument": doc, "position": {"line": 1, "character": 15}})
ok(any(i["label"] == "print" for i in c["result"]), "lsp 补全标准库模块成员")

# 跨文件：跳转到别的模块里的定义；pub 名字的重命名连同 use 它的文件一起改
import tempfile
td = os.path.realpath(tempfile.mkdtemp())
files = {
    "geo.kite": "pub type Point = { x: Int, y: Int }\n\npub fn area(w: Int, h: Int) -> Int { w * h }\n\n"
                "pub fn twice(p: Point) -> Int { area(p.x, p.y) * 2 }\n\nfn hidden() -> Int { 1 }\n",
    "main.kite": "use geo\nuse geo.area\n\nfn main() {\n  let p = geo.Point { x: 2, y: 3 }\n"
                 "  print(geo.area(p.x, p.y), area(1, 2), geo.twice(p))\n}\n",
    "other.kite": "use geo.{area as a, Point}\n\nfn f(area: Int) -> Int { area + 1 }\n\n"
                  "fn main() { print(a(2, 2), f(1), Point { x: 1, y: 1 }) }\n",
}
for n, t in files.items():
    with open(os.path.join(td, n), "w") as f: f.write(t)
muri = "file://" + os.path.join(td, "main.kite")
send({"jsonrpc": "2.0", "method": "textDocument/didOpen",
      "params": {"textDocument": {"uri": muri, "languageId": "kite", "version": 1, "text": files["main.kite"]}}})
d = recv()["params"]["diagnostics"]
ok(d == [], f"lsp 多文件诊断 {d}")
df = request(40, "textDocument/definition", {"textDocument": {"uri": muri}, "position": {"line": 5, "character": 13}})
ok(df["result"] and df["result"]["uri"].endswith("/geo.kite") and df["result"]["range"]["start"] == {"line": 2, "character": 7},
   f"lsp 跨文件跳转到定义 {df.get('result')}")
def edits(r):
    return {u.rsplit("/", 1)[1]: sorted((e["range"]["start"]["line"], e["range"]["start"]["character"]) for e in es)
            for u, es in r["result"]["changes"].items()}
rn = edits(request(41, "textDocument/rename", {"textDocument": {"uri": muri}, "position": {"line": 5, "character": 13}, "newName": "surface"}))
ok(rn == {"geo.kite": [(2, 7), (4, 32)], "main.kite": [(1, 8), (5, 12), (5, 28)], "other.kite": [(0, 9)]},
   f"lsp 跨文件重命名函数（as 别名与同名参数不动）{rn}")
rn = edits(request(42, "textDocument/rename", {"textDocument": {"uri": muri}, "position": {"line": 4, "character": 15}, "newName": "Pt"}))
ok(rn == {"geo.kite": [(0, 9), (4, 16)], "main.kite": [(4, 14)], "other.kite": [(0, 20), (4, 33)]}, f"lsp 跨文件重命名类型 {rn}")
guri = "file://" + os.path.join(td, "geo.kite")
send({"jsonrpc": "2.0", "method": "textDocument/didOpen",
      "params": {"textDocument": {"uri": guri, "languageId": "kite", "version": 1, "text": files["geo.kite"]}}})
recv()
rn = edits(request(43, "textDocument/rename", {"textDocument": {"uri": guri}, "position": {"line": 0, "character": 20}, "newName": "px"}))
ok(rn == {"geo.kite": [(0, 19), (4, 39)], "main.kite": [(4, 22), (5, 19)], "other.kite": [(4, 41)]}, f"lsp 跨文件重命名字段 {rn}")
rn = edits(request(44, "textDocument/rename", {"textDocument": {"uri": guri}, "position": {"line": 6, "character": 4}, "newName": "h2"}))
ok(rn == {"geo.kite": [(6, 3)]}, f"lsp 私有函数只在本文件重命名 {rn}")

def refs(r):
    out = {}
    for o in r["result"]: out.setdefault(o["uri"].rsplit("/", 1)[1], []).append((o["range"]["start"]["line"], o["range"]["start"]["character"], o["range"]["end"]["character"] - o["range"]["start"]["character"]))
    return out
rf = refs(request(45, "textDocument/references", {"textDocument": {"uri": muri}, "position": {"line": 5, "character": 13}, "context": {"includeDeclaration": True}}))
ok(rf == {"geo.kite": [(2, 7, 4), (4, 32, 4)], "main.kite": [(1, 8, 4), (5, 12, 4), (5, 28, 4)], "other.kite": [(0, 9, 4), (0, 17, 1), (4, 18, 1)]},
   f"lsp 跨文件查找引用（含 as 别名的调用，不含同名参数）{rf}")
rf = refs(request(46, "textDocument/references", {"textDocument": {"uri": muri}, "position": {"line": 5, "character": 13}, "context": {"includeDeclaration": False}}))
ok("geo.kite" in rf and (2, 7, 4) not in rf["geo.kite"], f"lsp 查找引用不含定义处 {rf}")
rf = refs(request(47, "textDocument/references", {"textDocument": {"uri": guri}, "position": {"line": 0, "character": 20}, "context": {"includeDeclaration": True}}))
ok(rf == {"geo.kite": [(0, 19, 1), (4, 39, 1)], "main.kite": [(4, 22, 1), (5, 19, 1)], "other.kite": [(4, 41, 1)]}, f"lsp 跨文件查找字段引用 {rf}")
hl = request(48, "textDocument/documentHighlight", {"textDocument": {"uri": guri}, "position": {"line": 2, "character": 8}})
ok(sorted((h["range"]["start"]["line"], h["range"]["start"]["character"]) for h in hl["result"]) == [(2, 7), (4, 32)], f"lsp 文档内高亮 {hl.get('result')}")
nr = request(49, "textDocument/references", {"textDocument": {"uri": guri}, "position": {"line": 1, "character": 0}, "context": {"includeDeclaration": True}})
ok(nr["result"] == [], f"lsp 空白处没有引用 {nr.get('result')}")

# 签名提示：正在输入（文件暂时解析不了）时也能用；跨文件、内建函数、x.m(…) 不显示 self
suri = "file://" + os.path.join(td, "sig.kite")
stext = "use geo\nuse geo.area\n\nfn main() {\n  let n = area(2, 3)\n  print(geo.area(1, \n"
with open(os.path.join(td, "sig.kite"), "w") as f: f.write(stext)
send({"jsonrpc": "2.0", "method": "textDocument/didOpen",
      "params": {"textDocument": {"uri": suri, "languageId": "kite", "version": 1, "text": stext}}})
recv()
def sig(line, ch):
    r = request(50 + line * 100 + ch, "textDocument/signatureHelp", {"textDocument": {"uri": suri}, "position": {"line": line, "character": ch}})["result"]
    if not r: return None
    x = r["signatures"][r["activeSignature"]]
    return x["label"], [x["label"][a:b] for a, b in (q["label"] for q in x["parameters"])], r["activeParameter"]
sg = sig(5, 20)
ok(sg == ("pub fn area(w: Int, h: Int) -> Int", ["w: Int", "h: Int"], 1), f"lsp 签名提示：跨文件 m.f，第二个参数 {sg}")
sg = sig(4, 15)
ok(sg == ("pub fn area(w: Int, h: Int) -> Int", ["w: Int", "h: Int"], 0), f"lsp 签名提示：use 导入的名字 {sg}")
sg = sig(4, 10)
ok(sg is None, f"lsp 签名提示：不在调用里 {sg}")
stext2 = stext + "  push(xs, \n"
send({"jsonrpc": "2.0", "method": "textDocument/didChange",
      "params": {"textDocument": {"uri": suri, "version": 2}, "contentChanges": [{"text": stext2}]}})
recv()
sg = sig(6, 11)
ok(sg == ("push(List<a>, a) -> List<a>", ["List<a>", "a"], 1), f"lsp 签名提示：内建函数 {sg}")

request(7, "shutdown", None); send({"jsonrpc": "2.0", "method": "exit"}); p.wait(timeout=5)
ok(p.returncode == 0, "lsp 正常退出")
sys.exit(1 if fails else 0)
