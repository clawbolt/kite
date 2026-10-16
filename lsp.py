"""Kite 语言服务器（LSP，stdio）

支持：
- 实时诊断：语法错误 + 全部类型错误（每次编辑重新检查）
- 悬停：函数签名、类型/枚举/trait 定义、变体、方法、内建函数，附带声明上方的 // 注释作为文档
- 跳转到定义：函数、类型、枚举、变体、trait、方法；跨文件（m.f、use m.f 导入的名字）
- 重命名：局部绑定、顶层名字、字段/方法；pub 的顶层名字会连同包内 use 它的文件一起改
- 文档大纲（documentSymbol）：函数、类型、枚举及其变体、trait、impl 及其方法
- 补全：关键字、内建函数、本文件声明；输入 `.` 后补全方法、字段和标准库模块成员
- 格式化：调用 kite fmt

启动：python3 lsp.py   （编辑器通过 stdin/stdout 通信）
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kite  # noqa: E402
sys.modules.setdefault("kite", kite)
from kite import lex, Parser, KiteError, KEYWORDS, Interp  # noqa: E402
import fmt as kfmt  # noqa: E402

WORD = re.compile(r"[A-Za-z_\u0080-\uffff][A-Za-z0-9_\u0080-\uffff]*")

_rt = Interp(typecheck=False)
BUILTINS = sorted(n for n in _rt.g.vars if not n[0].isupper() or n in ("Ok", "Err", "Some", "None"))
STD = {name: sorted(mod) for name, mod in _rt.std.items()}

SK = {"fn": 12, "struct": 23, "enum": 10, "member": 22, "trait": 11, "method": 6, "impl": 5, "var": 13, "field": 8}
CK = {"fn": 3, "struct": 22, "enum": 13, "member": 20, "trait": 8, "method": 2, "kw": 14, "var": 6,
      "module": 9, "field": 5}


# ───────────────────────── 分析 ─────────────────────────
class Sym:
    def __init__(self, name, kind, line, detail, doc="", parent=None, end=None):
        self.name, self.kind, self.line, self.detail, self.doc = name, kind, line, detail, doc
        self.parent, self.end, self.children = parent, end or line, []


def _block_end(src_lines, start):
    """从声明所在行开始，按括号配对找到块结束行（跳过字符串和注释）"""
    toks = kfmt.tokenize("\n".join(src_lines[start - 1:]))
    depth, opened = 0, False
    for i, ln in enumerate(toks):
        for t in ln:
            if t.k == "op" and t.s in "{(":
                depth += 1; opened = True
            elif t.k == "op" and t.s in "})":
                depth -= 1
        if opened and depth <= 0:
            return start + i
        if not opened and i > 0:
            return start
    return start


def _doc_above(src_lines, line):
    out, i = [], line - 2
    while i >= 0 and src_lines[i].strip().startswith("//"):
        out.append(src_lines[i].strip()[2:].strip()); i -= 1
    return "\n".join(reversed(out))


def _header(src_lines, line):
    """声明的签名：去掉函数体"""
    text = src_lines[line - 1].strip()
    toks = kfmt.tokenize(text)[0]
    depth, out = 0, []
    for t in toks:
        if t.k == "comment": break
        if t.s == "{" and depth == 0 and text.split()[0] in ("fn", "pub", "impl", "trait"):
            break
        if t.s in "([<": depth += 1
        if t.s in ")]>": depth -= 1
        out.append(t)
    return kfmt.format_line(out) if out else text


def analyze(src, path=None):
    """返回 (诊断列表, 符号列表)。给出文件路径且引用了其他模块时，带上模块和依赖包一起检查"""
    diags, syms = [], []
    analyze.locals = []
    lines = src.split("\n")
    try:
        items = Parser(lex(src)).program()
    except KiteError as e:
        return [_diag(lines, e.line, e.col, e.msg)], syms
    except RecursionError:
        return [_diag(lines, 1, None, "表达式嵌套过深")], syms
    check_items = items
    if path and re.search(r"^\s*(pub\s+)?use\s+(?!std\b)", src, re.M):
        try:
            import modules, kite as K
            check_items = modules.Loader(path, overlay=src).load_main()
        except KiteError as e:
            return [_diag(lines, _local_line(e.line), e.col, _mod_msg(e))], syms
        except Exception as e:
            return [_diag(lines, 1, None, f"模块加载失败: {e}")], syms
    locs = []
    try:
        from typecheck import Checker, show_t, prune
        ck = Checker(); ck.quiet = True
        errs = ck.check(check_items)
        for ln, wmsg in ck.warnings:
            if ln < 1000000:
                d = _diag(lines, ln, None, wmsg); d["severity"] = 2; diags.append(d)
        seen = set()
        for ln, nm, t, kind in ck.locals:
            if ln >= 1000000 or (ln, nm) in seen: continue   # 别的模块里的绑定
            seen.add((ln, nm))
            try: ts = show_t(prune(t))
            except Exception: continue
            locs.append((ln, nm, ts, kind))
        for e in errs:
            if getattr(e, "line", None) and e.line >= 1000000:   # 错误在别的模块里
                diags.append(_diag(lines, 1, None, _mod_msg(e))); continue
            diags.append(_diag(lines, getattr(e, "line", None), getattr(e, "col", None),
                               getattr(e, "msg", str(e))))
    except KiteError as e:
        diags.append(_diag(lines, e.line, e.col, e.msg))
    except Exception as e:  # 检查器自身异常不应让服务器崩溃
        diags.append(_diag(lines, 1, None, f"类型检查器内部错误: {e}"))

    def add(name, kind, line, parent=None):
        s = Sym(name, kind, line, _header(lines, line), _doc_above(lines, line), parent,
                _block_end(lines, line))
        (parent.children if parent else syms).append(s)
        return s

    for it in items:
        k = it.kind
        n0 = len(syms)
        if k == "Fn":
            add(it.name, "fn", it.line)
        elif k in ("Struct", "Alias"):
            s = add(it.name, "struct", it.line)
            for f, _ in getattr(it, "fields", []) or []:
                ln = _find_line(lines, f, it.line, s.end)
                c = Sym(f, "field", ln, f"{it.name}.{f}", "", s); s.children.append(c)
        elif k == "Enum":
            s = add(it.name, "enum", it.line)
            for vn, _ in it.variants:
                ln = _find_line(lines, vn, it.line, s.end)
                c = Sym(vn, "member", ln, lines[ln - 1].strip(), f"{it.name} 的变体", s)
                s.children.append(c)
        elif k == "Trait":
            s = add(it.name, "trait", it.line)
            for m in it.methods:
                add(m.name, "method", m.line, s)
        elif k == "Impl":
            label = f"impl {it.trait} for {it.target}" if it.trait else f"impl {it.target}"
            s = add(label, "impl", it.line)
            for m in it.methods:
                add(m.name, "method", m.line, s)
        elif k == "Let" and it.pat[0] == "bind":
            add(it.pat[1], "var", it.line)
        elif k == "Extern":
            for f in it.fns:
                s2 = add(f.name, "fn", f.line); s2.pub = bool(getattr(f, "pub", False))
        for s2 in syms[n0:]:
            if not hasattr(s2, "pub"): s2.pub = bool(getattr(it, "pub", False))
    analyze.locals = locs
    return diags, syms


def local_at(syms, locs, word, line):
    """光标（1 起的行号）处名字 word 对应的局部绑定：同一个顶层定义内、在光标之前最近的那个"""
    encl = None
    for s in syms:
        if s.kind in ("fn", "impl") and s.line <= line <= (s.end or s.line):
            encl = s
    lo, hi = (encl.line, encl.end or encl.line) if encl else (0, line)
    best = None
    for ln, nm, ts, kind in locs:
        if nm == word and lo <= ln <= min(hi, line) and (best is None or ln >= best[0]):
            best = (ln, nm, ts, kind)
    return best


def _find_line(lines, word, start, end):
    pat = re.compile(r"\b" + re.escape(word) + r"\b")
    for ln in range(start, min(end, len(lines)) + 1):
        if pat.search(lines[ln - 1]):
            return ln
    return start


def _local_line(line):
    return line if line and line < 1000000 else 1


def _mod_msg(e):
    import kite as K
    line = getattr(e, "line", None)
    if line and line >= 1000000:
        f, n = K.locate(line)
        return f"{f}:{n}: {e.msg}"
    return e.msg


def _diag(lines, line, col, msg):
    line = line or 1
    line = max(1, min(line, len(lines)))
    text = lines[line - 1]
    sc = (col - 1) if col else len(text) - len(text.lstrip())
    msg = re.sub(r"^\d+(:\d+)?: ", "", msg)
    return {"range": {"start": {"line": line - 1, "character": sc},
                      "end": {"line": line - 1, "character": max(len(text), sc + 1)}},
            "severity": 1, "source": "kite", "message": msg}


def _walk(syms):
    for s in syms:
        yield s
        yield from _walk(s.children)


def _name_range(lines, s):
    text = lines[s.line - 1] if s.line - 1 < len(lines) else ""
    name = s.name.split()[-1]
    m = re.search(r"\b" + re.escape(name) + r"\b", text)
    c = m.start() if m else 0
    return {"start": {"line": s.line - 1, "character": c},
            "end": {"line": s.line - 1, "character": c + len(name)}}


def _rng(ln, c, word):
    return {"start": {"line": ln - 1, "character": c}, "end": {"line": ln - 1, "character": c + len(word)}}


def occurrences(src, word):
    """源码里名字 word 的每次出现：(行号 1 起, 列 0 起, 前面是否是 ., 后面第一个非空白字符)。
    用词法分析器跳过注释与字符串，字符串插值 {…} 里的名字也算"""
    lines = src.split("\n")
    try: toks = lex(src)
    except KiteError: return []
    out = []
    def after(ln, end):
        rest = lines[ln - 1][end:].lstrip() if ln - 1 < len(lines) else ""
        return rest[:1]
    def emit(ln, c, prev):
        out.append((ln, c, prev == ".", after(ln, c + len(word))))
    prev = None
    for t in toks:
        if t.kind == "IDENT" and t.val == word:
            emit(t.line, t.col - 1, prev)
        elif t.kind == "STR" and isinstance(t.val, list):
            line = lines[t.line - 1] if t.line - 1 < len(lines) else ""
            off = t.col - 1
            for part in t.val:
                if isinstance(part, tuple) and part[0] == "expr":
                    base = line.find(part[1], off)
                    if base < 0: continue
                    off = base + len(part[1]); p2 = None
                    try: sub = lex(part[1])
                    except KiteError: continue
                    for u in sub:
                        if u.kind == "IDENT" and u.val == word: emit(t.line, base + u.col - 1, p2)
                        p2 = u.val if u.kind == "OP" else None
        prev = t.val if t.kind == "OP" else None
    return out


# ───────────────────────── 跨文件 ─────────────────────────
def _uri_path(uri):
    from urllib.parse import unquote, urlparse
    return os.path.realpath(unquote(urlparse(uri).path)) if uri.startswith("file://") else None


def _path_uri(path):
    from urllib.parse import quote
    return "file://" + quote(path)


def _use_stmts(src):
    """文件里的 use 语句：[(path, alias, 语句里的 IDENT 词法单元列表（按顺序，带 as 标记）, 覆盖的行集合)]"""
    try:
        toks = lex(src); items = Parser(list(toks)).program()
    except KiteError:
        # 正在输入、整个文件暂时解析不了：只留下 use 行（其余行换成空行，行号不变）再试一次
        only = "\n".join(l if re.match(r"\s*(pub\s+)?use\b", l) else "" for l in src.split("\n"))
        try:
            toks = lex(only); items = Parser(list(toks)).program()
        except KiteError:
            return []
    uses = [it for it in items if it.kind == "Use"]
    out = []
    i = 0
    for u in uses:
        while i < len(toks) and not (toks[i].kind == "KW" and toks[i].val == "use" and toks[i].line >= u.line):
            i += 1
        j = i + 1; ids = []; depth = 0; after_as = False
        while j < len(toks):
            t = toks[j]
            if t.kind == "OP" and t.val == "{": depth += 1
            elif t.kind == "OP" and t.val == "}": depth -= 1
            elif t.kind in ("NEWLINE", "EOF") and depth <= 0: break
            elif t.kind == "OP" and t.val == ";" and depth <= 0: break
            if t.kind == "KW" and t.val == "as": after_as = True
            elif t.kind == "IDENT":
                ids.append((t, after_as, depth)); after_as = False
            j += 1
        lines = set(range(u.line, (toks[j - 1].line if j > i + 1 else u.line) + 1))
        out.append((u.path, u.alias, ids, lines))
        i = j
    return out


def _resolve_static(loader, path):
    """不加载、只看文件系统：use 路径 → (模块文件 realpath, 用掉的段数) 或 None"""
    pkg = loader.root_pkg
    segs = [x for x in path if isinstance(x, str) and x != "*"]
    f, n = loader.find_module_file(pkg.src_dir, segs)
    if f: return os.path.realpath(f), n
    if pkg.name and path and path[0] == pkg.name:
        f, n = loader.find_module_file(pkg.src_dir, segs[1:])
        if f: return os.path.realpath(f), 1 + n
        lib = os.path.join(pkg.src_dir, "lib.kite")
        if os.path.isfile(lib): return os.path.realpath(lib), 1
    return None


def _scan_root(path):
    """包含 path 的包根（有 kite.toml）或 path 所在目录"""
    import modules
    try: pkg = modules.Loader(path).root_pkg
    except Exception: return os.path.dirname(path)
    return pkg.root if os.path.exists(os.path.join(pkg.root, "kite.toml")) else os.path.dirname(path)


def _kite_files(root, limit=2000):
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".") and x not in ("node_modules", "__pycache__", "server")]
        for f in files:
            if f.endswith(".kite"):
                out.append(os.path.realpath(os.path.join(d, f)))
                if len(out) >= limit: return out
    return out


def qualified_occ(src, chain, word):
    """chain.word 形式的出现（chain 是名字列表，如 ["geo"]）：[(行, 列)]；chain 前面不能再接 ."""
    try: toks = [t for t in lex(src) if t.kind != "NEWLINE"]
    except KiteError: return []
    out, k = [], len(chain)
    for i, t in enumerate(toks):
        if t.kind != "IDENT" or t.val != word or i < 2 * k: continue
        ok = all(toks[i - 2 * (k - m)].kind == "IDENT" and toks[i - 2 * (k - m)].val == chain[m]
                 and toks[i - 2 * (k - m) + 1].kind == "OP" and toks[i - 2 * (k - m) + 1].val == "." for m in range(k))
        b = i - 2 * k - 1
        if ok and not (b >= 0 and toks[b].kind == "OP" and toks[b].val == "."):
            out.append((t.line, t.col - 1))
    return out


def call_at(text, line, ch):
    """光标所在的调用：(名字所在行, 名字所在列, 名字, 前面是不是 ., 点前面的名字, 第几个实参) 或 None。
    跳过字符串和 // 注释；遇到外层的 [ 或 { 就停（在列表、代码块或结构体字面量里）"""
    lines = text.split("\n")
    if line >= len(lines): return None
    src = "\n".join(lines[:line] + [lines[line][:ch]])
    sig, i, n = [], 0, len(src)              # 字符串和注释之外的字符位置
    while i < n:
        c = src[i]
        if c == '"':
            i += 1
            while i < n and src[i] != '"':
                i += 2 if src[i] == "\\" else 1
            i += 1; continue
        if c == "/" and src.startswith("//", i):
            while i < n and src[i] != "\n": i += 1
            continue
        sig.append(i); i += 1
    depth, commas = 0, 0
    for k in range(len(sig) - 1, -1, -1):
        c = src[sig[k]]
        if c in ")]}": depth += 1
        elif c in "([{":
            if depth: depth -= 1; continue
            if c != "(": return None
            j = sig[k]
            while j > 0 and src[j - 1] in " \t": j -= 1
            e = j
            while j > 0 and (src[j - 1].isalnum() or src[j - 1] == "_"): j -= 1
            name = src[j:e]
            if not name or not (name[0].isalpha() or name[0] == "_") or name in KEYWORDS: return None
            dot = j > 0 and src[j - 1] == "."
            recv = ""
            if dot:
                r = j - 1
                while r > 0 and (src[r - 1].isalnum() or src[r - 1] == "_"): r -= 1
                recv = src[r:j - 1]
            ln = src.count("\n", 0, j); col = j - (src.rfind("\n", 0, j) + 1)
            return ln, col, name, dot, recv, commas
        elif c == "," and depth == 0: commas += 1
    return None


def _split_params(text):
    """参数列表文本 → [(起, 止)]，按顶层逗号切开"""
    out, depth, st = [], 0, 0
    for i, c in enumerate(text + ","):
        if c in "([<{": depth += 1
        elif c in ")]>}": depth -= 1
        elif c == "," and depth == 0:
            a, b = st, i
            while a < b and text[a] == " ": a += 1
            while b > a and text[b - 1] == " ": b -= 1
            if b > a: out.append((a, b))
            st = i + 1
    return out


def word_at(text, line, ch):
    lines = text.split("\n")
    if line >= len(lines): return None, ""
    l = lines[line]
    for m in WORD.finditer(l):
        if m.start() <= ch <= m.end():
            return m.group(), l[:m.start()]
    return None, l[:ch]


# ───────────────────────── 服务器 ─────────────────────────
class Server:
    def __init__(self, inp=sys.stdin.buffer, out=sys.stdout.buffer):
        self.inp, self.out = inp, out
        self.docs = {}       # uri → 文本
        self.cache = {}      # uri → (诊断, 符号)
        self.locals = {}     # uri → 局部绑定 [(行, 名字, 类型, 种类)]
        self.docs_ro = {}    # 没打开、但跨文件跳转 / 重命名读过的文件
        self.running = True

    def send(self, msg):
        body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        self.out.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        self.out.flush()

    def read(self):
        length = None
        while True:
            line = self.inp.readline()
            if not line: return None
            line = line.strip()
            if not line: break
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1])
        return json.loads(self.inp.read(length).decode("utf-8")) if length else None

    def serve(self):
        while self.running:
            msg = self.read()
            if msg is None: break
            try:
                self.handle(msg)
            except Exception as e:  # 单个请求失败不影响后续
                if "id" in msg:
                    self.send({"jsonrpc": "2.0", "id": msg["id"],
                               "error": {"code": -32603, "message": str(e)}})
                print(f"kite-lsp: {e!r}", file=sys.stderr)

    def reply(self, msg, result):
        self.send({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    def analyze(self, uri):
        text = self.docs.get(uri, "")
        path = None
        if uri.startswith("file://"):
            from urllib.parse import unquote, urlparse
            path = unquote(urlparse(uri).path)
        diags, syms = analyze(text, path)
        old = self.cache.get(uri)
        if not syms and diags and old and old[1]:
            # 正在输入、暂时解析不了：沿用上一次成功分析的符号和局部绑定（悬停、补全、签名提示还能用）
            self.cache[uri] = (diags, old[1])
        else:
            self.cache[uri] = (diags, syms)
            self.locals[uri] = getattr(analyze, "locals", [])
        self.send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                   "params": {"uri": uri, "diagnostics": self.cache[uri][0]}})

    def syms(self, uri):
        if uri not in self.cache: self.analyze(uri)
        return self.cache[uri][1]

    def handle(self, msg):
        m, p = msg.get("method"), msg.get("params") or {}
        if m == "initialize":
            self.reply(msg, {"capabilities": {
                "textDocumentSync": 1, "hoverProvider": True, "definitionProvider": True,
                "documentSymbolProvider": True, "documentFormattingProvider": True,
                "completionProvider": {"triggerCharacters": ["."]},
                "signatureHelpProvider": {"triggerCharacters": ["(", ","], "retriggerCharacters": [")"]},
                "renameProvider": {"prepareProvider": True}, "referencesProvider": True,
                "documentHighlightProvider": True},
                "serverInfo": {"name": "kite-lsp", "version": "1.00"}})
        elif m == "shutdown":
            self.reply(msg, None)
        elif m == "exit":
            self.running = False
        elif m == "textDocument/didOpen":
            d = p["textDocument"]; self.docs[d["uri"]] = d["text"]; self.analyze(d["uri"])
        elif m == "textDocument/didChange":
            uri = p["textDocument"]["uri"]
            self.docs[uri] = p["contentChanges"][-1]["text"]; self.analyze(uri)
        elif m == "textDocument/didClose":
            uri = p["textDocument"]["uri"]
            self.docs.pop(uri, None); self.cache.pop(uri, None)
            self.send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                       "params": {"uri": uri, "diagnostics": []}})
        elif m == "textDocument/hover":
            self.reply(msg, self.hover(p))
        elif m == "textDocument/definition":
            self.reply(msg, self.definition(p))
        elif m == "textDocument/documentSymbol":
            self.reply(msg, self.symbols(p))
        elif m == "textDocument/completion":
            self.reply(msg, self.complete(p))
        elif m == "textDocument/prepareRename":
            self.reply(msg, self.prepare_rename(p))
        elif m == "textDocument/rename":
            try: self.reply(msg, self.rename(p))
            except ValueError as e:
                self.send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32602, "message": str(e)}})
        elif m == "textDocument/signatureHelp":
            self.reply(msg, self.signature(p))
        elif m == "textDocument/references":
            self.reply(msg, self.references(p))
        elif m == "textDocument/documentHighlight":
            self.reply(msg, self.highlight(p))
        elif m == "textDocument/formatting":
            self.reply(msg, self.format(p))
        elif "id" in msg:
            self.send({"jsonrpc": "2.0", "id": msg["id"],
                       "error": {"code": -32601, "message": f"不支持的方法 {m}"}})

    # 功能
    def _lookup(self, uri, word, prefix):
        cands = [s for s in _walk(self.syms(uri)) if s.name == word]
        if not cands: return None
        if prefix.rstrip().endswith("."):   # x.method → 优先方法和字段
            for s in cands:
                if s.kind in ("method", "field"): return s
        order = ["fn", "struct", "enum", "trait", "member", "var", "method", "field"]
        return sorted(cands, key=lambda s: order.index(s.kind) if s.kind in order else 99)[0]

    def hover(self, p):
        uri = p["textDocument"]["uri"]; pos = p["position"]
        word, prefix = word_at(self.docs.get(uri, ""), pos["line"], pos["character"])
        if not word: return None
        loc = None
        if not prefix.rstrip().endswith("."):
            self.syms(uri)
            loc = local_at(self.syms(uri), self.locals.get(uri, []), word, pos["line"] + 1)
        s = None if loc else self._lookup(uri, word, prefix)
        if loc:
            kw = {"param": "参数", "let": "let", "var": "var"}[loc[3]]
            val = f"```kite\n{'(参数) ' if kw == '参数' else kw + ' '}{word}: {loc[2]}\n```"
        elif s:
            detail = s.detail
            if s.kind == "method" and s.parent:
                detail = f"// {s.parent.name}\n{detail}"
            val = f"```kite\n{detail}\n```" + (f"\n\n{s.doc}" if s.doc else "")
        elif word in BUILTINS:
            val = f"```kite\n{word}\n```\n\nKite 内建函数"
        elif word in STD:
            val = f"```kite\nuse std.{word}\n```\n\n标准库模块：{', '.join(STD[word])}"
        elif word in KEYWORDS:
            val = f"关键字 `{word}`"
        else:
            return None
        return {"contents": {"kind": "markdown", "value": val}}

    def _target(self, uri, pos):
        """光标处名字指向的东西：("local", 绑定) / ("sym", 符号) / None"""
        word, prefix = word_at(self.docs.get(uri, ""), pos["line"], pos["character"])
        if not word or word in KEYWORDS: return word, None
        syms = self.syms(uri)
        if not prefix.rstrip().endswith("."):
            loc = local_at(syms, self.locals.get(uri, []), word, pos["line"] + 1)
            if loc: return word, ("local", loc)
        s = self._lookup(uri, word, prefix)
        return word, (("sym", s) if s else None)

    def _text(self, path):
        uri = _path_uri(path)
        for u, t in self.docs.items():
            if _uri_path(u) == path: return u, t
        try:
            with open(path, encoding="utf-8") as f: return uri, f.read()
        except OSError: return uri, None

    def _file_info(self, uri, src, path):
        """(语法符号, 局部绑定)：打开的文件用缓存，没打开的现场分析"""
        if uri in self.docs:
            return self.syms(uri), self.locals.get(uri, [])
        _, syms = analyze(src, path)
        return syms, getattr(analyze, "locals", [])

    def _importers(self, fpath):
        """包内 use 了模块文件 fpath 的其他文件：[(路径, 源码, (path, alias, ids, 行集合), 模块段数)]"""
        import modules
        out = []
        for g in _kite_files(_scan_root(fpath)):
            if g == fpath: continue
            _, src = self._text(g)
            if not src or "use" not in src: continue
            try: loader = modules.Loader(g)
            except Exception: continue
            for u in _use_stmts(src):
                r = _resolve_static(loader, u[0])
                if r and r[0] == fpath: out.append((g, src, u, r[1]))
        return out

    def _xref(self, uri, pos):
        """光标处是别的模块里的名字（m.f 或 use 导入的 f）时：(定义文件路径, 真实名字)"""
        gpath = _uri_path(uri)
        if not gpath: return None
        text = self.docs.get(uri, "")
        word, prefix = word_at(text, pos["line"], pos["character"])
        if not word: return None
        import modules
        try: loader = modules.Loader(gpath, overlay=text)
        except Exception: return None
        m = re.search(r"((?:" + WORD.pattern + r")\s*\.\s*)+$", prefix)
        chain = re.findall(WORD.pattern, m.group(0)) if m else []
        line_no = pos["line"] + 1
        for path, alias, ids, lines in _use_stmts(text):
            r = _resolve_static(loader, path)
            if not r: continue
            f, n = r; rest = path[n:]
            if line_no in lines:                     # 光标在 use 语句本身上
                for t, after_as, _ in ids[n:]:
                    if t.line == line_no and t.col - 1 <= pos["character"] <= t.col - 1 + len(t.val) and t.val == word and not after_as:
                        return f, word
                continue
            if chain:
                if not rest and chain == [alias or path[n - 1]]: return f, word
                continue
            if not rest: continue
            item = rest[0]
            if item == word and not alias and len(rest) == 1: return f, word
            if alias == word and len(rest) == 1 and isinstance(item, str): return f, item
            if isinstance(item, list):
                for it in item:
                    if it == word: return f, word
                    if isinstance(it, tuple) and it[1] == word: return f, it[0]
            if item == "*":
                _, src = self._text(f)
                if src and re.search(r"^\s*pub\b.*\b" + re.escape(word) + r"\b", src, re.M): return f, word
        return None

    def _xtarget(self, uri, pos):
        """(定义所在 uri, 名字, 目标)；目标同 _target，跨文件时是定义文件里的顶层符号"""
        word, prefix = word_at(self.docs.get(uri, ""), pos["line"], pos["character"])
        xr = self._xref(uri, pos) if prefix.rstrip().endswith(".") else None   # m.f 优先按模块解析
        if not xr:
            word, t = self._target(uri, pos)
            if t: return uri, word, t
            xr = self._xref(uri, pos)
        if not xr: return uri, word, None
        f, name = xr
        furi, src = self._text(f)
        if src is None: return uri, word, None
        syms, _ = self._file_info(furi, src, f)
        s = next((x for x in syms if x.name == name and x.kind not in ("impl",)), None)
        if s is None: return uri, word, None
        if furi not in self.docs: self.docs_ro[furi] = src
        return furi, name, ("sym", s)

    def _src(self, uri):
        return self.docs.get(uri) if uri in self.docs else self.docs_ro.get(uri, "")

    def definition(self, p):
        uri = p["textDocument"]["uri"]; pos = p["position"]
        duri, word, t = self._xtarget(uri, pos)
        if not t: return None
        if t[0] == "local":
            for ln, c, dot, _ in occurrences(self.docs[uri], word):
                if ln == t[1][0] and not dot:
                    return {"uri": uri, "range": _rng(ln, c, word)}
            return None
        return {"uri": duri, "range": _name_range(self._src(duri).split("\n"), t[1])}

    def prepare_rename(self, p):
        uri = p["textDocument"]["uri"]; pos = p["position"]
        _, word, t = self._xtarget(uri, pos)
        if not t: return None
        line = self.docs[uri].split("\n")[pos["line"]]
        for m in WORD.finditer(line):
            if m.start() <= pos["character"] <= m.end():
                return {"range": _rng(pos["line"] + 1, m.start(), m.group(0)), "placeholder": m.group(0)}
        return None

    @staticmethod
    def _file_edits(src, syms, locs, word, t, new, skip_lines=()):
        """一个文件里的重命名编辑：局部绑定只改同一作用域里解析到它的引用；顶层名字改所有未被局部遮蔽的引用；
        字段 / 方法改 .name、定义处和结构体字面量里的 name:"""
        edits = []
        for ln, c, dot, nxt in occurrences(src, word):
            if ln in skip_lines: continue
            loc = None if dot else local_at(syms, locs, word, ln)
            if t is None:                           # 别的文件：导入进来的顶层名字
                hit = not dot and loc is None
            elif t[0] == "local":
                hit = loc is not None and loc[0] == t[1][0]
            elif t[1].kind in ("method", "field"):
                par = t[1].parent
                inside = par is not None and par.line <= ln <= (par.end or par.line) and not dot and loc is None
                hit = dot or inside or (t[1].kind == "field" and nxt == ":" and loc is None)
            else:
                hit = not dot and loc is None
            if hit: edits.append({"range": _rng(ln, c, word), "newText": new})
        return edits

    def rename(self, p):
        """重命名。pub 的顶层名字（以及 pub 类型的字段 / 方法）跨文件：包内 use 了定义模块的文件里，
        m.name、use m.name / use m.{name} 里的名字、按名字导入后的未遮蔽引用都会改；use m.name as x 只改 use 里的 name"""
        uri = p["textDocument"]["uri"]; pos = p["position"]; new = p["newName"]
        if not re.fullmatch(WORD.pattern, new) or new in KEYWORDS:
            raise ValueError(f"{new!r} 不是合法的名字")
        return self._collect(uri, pos, new)

    def references(self, p):
        """查找所有引用：和重命名同一套解析（作用域、遮蔽、跨文件 use），另外把 use … as 别名在导入文件里的引用也算上；
        includeDeclaration 为假时去掉定义处"""
        uri = p["textDocument"]["uri"]; pos = p["position"]
        try: ch = self._collect(uri, pos, "", refs=True)["changes"]
        except ValueError: return []
        out = [{"uri": u, "range": e["range"]} for u, es in ch.items() for e in es]
        if not (p.get("context") or {}).get("includeDeclaration", True):
            d = self.definition(p)
            if d: out = [o for o in out if not (o["uri"] == d["uri"] and o["range"]["start"] == d["range"]["start"])]
        return sorted(out, key=lambda o: (o["uri"], o["range"]["start"]["line"], o["range"]["start"]["character"]))

    def signature(self, p):
        """签名提示：用户定义的函数 / 方法（跨文件同跳转到定义），x.m(…) 不显示 self；
        内建函数和 std 模块函数用类型检查器里的签名（可能有多个重载）"""
        uri = p["textDocument"]["uri"]; pos = p["position"]
        c = call_at(self.docs.get(uri, ""), pos["line"], pos["character"])
        if not c: return None
        ln, col, name, dot, recv, act = c
        sigs = []
        _, _, t = self._xtarget(uri, {"line": ln, "character": col})
        if t and t[0] == "sym" and t[1].kind in ("fn", "method"):
            label = t[1].detail.split("\n")[-1].strip()
            m = re.search(r"\b" + re.escape(name) + r"\s*(<[^(]*>)?\s*\(", label)
            if m:
                o = m.end(); depth, e = 1, o
                while e < len(label) and depth:
                    depth += {"(": 1, ")": -1}.get(label[e], 0); e += 1
                ps = [(o + a, o + b) for a, b in _split_params(label[o:e - 1])]
                if dot and ps and re.fullmatch(r"(mut\s+)?self\b.*", label[ps[0][0]:ps[0][1]]) and not recv[:1].isupper():
                    ps = ps[1:]
                sigs.append({"label": label, "parameters": [{"label": list(x)} for x in ps],
                             **({"documentation": t[1].doc} if t[1].doc else {})})
        elif not t:
            import typecheck
            cands = []
            if dot and recv in typecheck.STD_SIGS and name in typecheck.STD_SIGS[recv]:
                cands = [(recv + "." + name, typecheck.STD_SIGS[recv][name], False)]
            elif name in typecheck.BUILTIN_SIGS:
                b = typecheck.BUILTIN_SIGS[name]
                cands = [(name, x, dot) for x in (b.values() if isinstance(b, dict) else [b])]
            for nm, sg, method in cands:
                if not isinstance(sg, str) or not sg.startswith("fn("): continue
                depth, e = 1, 3
                while e < len(sg) and depth:
                    depth += {"(": 1, ")": -1}.get(sg[e], 0); e += 1
                label = nm + sg[2:]
                o = len(nm) + 1
                ps = [(o + a, o + b) for a, b in _split_params(sg[3:e - 1])]
                if method: ps = ps[1:]
                sigs.append({"label": label, "parameters": [{"label": list(x)} for x in ps]})
        if not sigs and not t:                   # 从没成功解析过的文件：按文本找 fn name(…) 声明
            src = self.docs.get(uri, ""); lines = src.split("\n")
            m = re.search(r"^[ \t]*(pub(\([a-z]+\))?\s+)?fn\s+" + re.escape(name) + r"\b", src, re.M)
            if m and not dot:
                label = _header(lines, src.count("\n", 0, m.start()) + 1)
                k = label.find("(", label.find(name))
                if k >= 0:
                    depth, e = 1, k + 1
                    while e < len(label) and depth:
                        depth += {"(": 1, ")": -1}.get(label[e], 0); e += 1
                    ps = [(k + 1 + a, k + 1 + b) for a, b in _split_params(label[k + 1:e - 1])]
                    sigs.append({"label": label, "parameters": [{"label": list(x)} for x in ps]})
        if not sigs: return None
        best = next((i for i, x in enumerate(sigs) if len(x["parameters"]) > act), 0)
        return {"signatures": sigs, "activeSignature": best, "activeParameter": act}

    def highlight(self, p):
        """本文件里同一个名字的引用（documentHighlight）"""
        uri = p["textDocument"]["uri"]
        return [{"range": r["range"]} for r in self.references({**p, "context": {"includeDeclaration": True}}) if r["uri"] == uri]

    def _collect(self, uri, pos, new, refs=False):
        duri, word, t = self._xtarget(uri, pos)
        if not t: raise ValueError("这里没有可以重命名的名字")
        dsrc = self._src(duri); dpath = _uri_path(duri)
        syms, locs = self._file_info(duri, dsrc, dpath)
        changes = {duri: self._file_edits(dsrc, syms, locs, word, t, new)}
        if t[0] != "sym" or not dpath: return {"changes": changes}
        s = t[1]
        top = s.parent is None and s.kind in ("fn", "struct", "enum", "trait", "var")
        member = s.kind in ("method", "field") and s.parent is not None and getattr(s.parent, "pub", False)
        if not ((top and getattr(s, "pub", False)) or member): return {"changes": changes}
        for g, gsrc, (path, alias, ids, ulines), n in self._importers(dpath):
            guri = next((u for u in self.docs if _uri_path(u) == g), _path_uri(g))
            ed = changes.setdefault(guri, [])
            seen = {(e["range"]["start"]["line"], e["range"]["start"]["character"]) for e in ed}
            def put(ln, c, w=word):
                if (ln - 1, c) not in seen:
                    seen.add((ln - 1, c)); ed.append({"range": _rng(ln, c, w), "newText": new})
            if member:
                gs, gl = self._file_info(guri, gsrc, g) if s.kind == "field" else (None, None)
                for ln, c, dot, nxt in occurrences(gsrc, word):
                    if ln in ulines: continue
                    if dot or (s.kind == "field" and nxt == ":" and local_at(gs, gl, word, ln) is None): put(ln, c)
                continue
            rest = path[n:]
            if not rest:
                for ln, c in qualified_occ(gsrc, [alias or path[n - 1]], word): put(ln, c)
                continue
            item = rest[0]
            aname = alias if refs and item == word and len(rest) == 1 else None
            cand = ids[n:n + 1] if not isinstance(item, list) else ids[n:]
            for k, (tok, after_as, depth) in enumerate(cand):
                if tok.val == word and not after_as and (isinstance(item, list) == (depth >= 1)):
                    put(tok.line, tok.col - 1)
                    if refs and isinstance(item, list) and k + 1 < len(cand) and cand[k + 1][1]:
                        aname = cand[k + 1][0].val; put(cand[k + 1][0].line, cand[k + 1][0].col - 1, aname)
            if aname and not isinstance(item, list):
                for tok, after_as, depth in ids:
                    if after_as and tok.val == aname: put(tok.line, tok.col - 1, aname)
            if aname:                                # 查找引用：use … as 别名的引用也算
                gs, gl = self._file_info(guri, gsrc, g)
                if not any(x.name == aname and x.parent is None for x in gs):
                    for e in self._file_edits(gsrc, gs, gl, aname, None, new, ulines):
                        put(e["range"]["start"]["line"] + 1, e["range"]["start"]["character"], aname)
            plain = (item == word and not alias and len(rest) == 1) or item == "*" or \
                (isinstance(item, list) and word in item)
            if plain:
                gs, gl = self._file_info(guri, gsrc, g)
                if any(x.name == word and x.parent is None for x in gs): continue   # 本文件另有同名定义
                for e in self._file_edits(gsrc, gs, gl, word, None, new, ulines):
                    put(e["range"]["start"]["line"] + 1, e["range"]["start"]["character"])
        return {"changes": {u: e for u, e in changes.items() if e}}

    def symbols(self, p):
        uri = p["textDocument"]["uri"]
        lines = self.docs.get(uri, "").split("\n")

        def conv(s):
            sel = _name_range(lines, s)
            end_line = max(s.end, s.line) - 1
            end_ch = len(lines[end_line]) if end_line < len(lines) else 0
            return {"name": s.name, "kind": SK[s.kind], "detail": s.detail,
                    "range": {"start": {"line": s.line - 1, "character": 0},
                              "end": {"line": end_line, "character": end_ch}},
                    "selectionRange": sel, "children": [conv(c) for c in s.children]}
        return [conv(s) for s in self.syms(uri)]

    def complete(self, p):
        uri = p["textDocument"]["uri"]; pos = p["position"]
        text = self.docs.get(uri, "")
        line = text.split("\n")[pos["line"]] if pos["line"] < len(text.split("\n")) else ""
        before = line[:pos["character"]]
        m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_]*)$", before)
        items = []
        if m:
            obj = m.group(1)
            if obj in STD:
                return [{"label": n, "kind": CK["fn"], "detail": f"std.{obj}"} for n in STD[obj]]
            seen = set()
            for s in _walk(self.syms(uri)):
                if s.kind in ("method", "field") and s.name not in seen:
                    seen.add(s.name)
                    items.append({"label": s.name, "kind": CK[s.kind], "detail": s.detail})
            return items
        for s in _walk(self.syms(uri)):
            if s.kind in ("fn", "struct", "enum", "trait", "member", "var"):
                items.append({"label": s.name, "kind": CK[s.kind], "detail": s.detail})
        items += [{"label": b, "kind": CK["fn"], "detail": "内建函数"} for b in BUILTINS]
        items += [{"label": k, "kind": CK["kw"]} for k in sorted(KEYWORDS)]
        items += [{"label": n, "kind": CK["module"], "detail": f"std.{n}"} for n in STD]
        return items

    def format(self, p):
        uri = p["textDocument"]["uri"]
        text = self.docs.get(uri, "")
        try:
            new = kfmt.fmt(text)
        except KiteError:
            return []   # 有语法错误时不格式化
        if new == text: return []
        n = text.count("\n")
        return [{"range": {"start": {"line": 0, "character": 0},
                           "end": {"line": n + 1, "character": 0}}, "newText": new}]


if __name__ == "__main__":
    Server().serve()
