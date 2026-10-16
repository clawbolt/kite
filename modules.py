"""Kite 模块系统：把一个程序及其引用的模块/依赖包解析、改名、拼接成一个扁平的程序。

所有引擎（树解释器、虚拟机、原生后端）和类型检查器都只看到拼接后的 AST，所以模块系统
不需要改动任何后端。

规则
- 一个 .kite 文件就是一个模块。包的根目录有 kite.toml；源码在 src/ 下，
  库的入口是 src/lib.kite，可执行程序的入口是 src/main.kite。
- `use geom`            引入模块，之后用 geom.area(...)、geom.Point 访问
- `use geom.area`       把 pub 项 area 引入当前作用域
- `use geom.{area, Point}`
- `use geom.shapes`     子模块（geom/shapes.kite），别名为最后一段 shapes
- 查找顺序：当前包 src/ 下的文件（a.kite 或 a/mod.kite）→ 依赖包（kite.lock 里的包）
- 只有 pub 项能被其他模块访问；impl 里的方法总是公开的
- 模块里的顶层名字在内部被改名为 “模块路径.名字”（例如 geom.area、geom.Point），
  所以不同模块里的同名函数、类型互不冲突；主文件的名字保持不变
- 枚举变体是全局名字（与单文件时一致），不同模块的变体不能重名，这是当前的已知限制
- 循环引用会报错

行号编码：第 i 个源文件的第 n 行记为 i * LINE_BASE + n，所有引擎原样传递行号，
报错时由 kite.locate() 还原成 “文件:行”。主文件 i = 0，所以单文件程序的行号不变。
"""
import os, sys
import kite
from kite import KiteError, Node, Parser, lex, QName

LINE_BASE = kite.LINE_BASE
# 用 Kite 写的标准库模块（std/json.kite 等）；io / fs / math 是内建模块，不在这里
STD_DIR = os.path.join(os.path.dirname(os.path.abspath(kite.__file__)), "std")


# std 按 Go 的包路径分层（std/go/ast.kite → use std.go.ast）。旧的平铺名字保留一个版本，用到时给出弃用警告
STD_RENAMED = {
    "adler32": "hash.adler32", "crc32": "hash.crc32", "crc64": "hash.crc64", "fnv": "hash.fnv",
    "aes": "crypto.aes", "cipher": "crypto.cipher", "des": "crypto.des", "dsa": "crypto.dsa",
    "ecdh": "crypto.ecdh", "ecdsa": "crypto.ecdsa", "ed25519": "crypto.ed25519", "elliptic": "crypto.elliptic",
    "hkdf": "crypto.hkdf", "hmac": "crypto.hmac", "md5": "crypto.md5", "mlkem": "crypto.mlkem",
    "pbkdf2": "crypto.pbkdf2", "rc4": "crypto.rc4", "rsa": "crypto.rsa", "sha1": "crypto.sha1",
    "sha256": "crypto.sha256", "sha3": "crypto.sha3", "sha512": "crypto.sha512", "subtle": "crypto.subtle",
    "crand": "crypto.rand",
    "ascii85": "encoding.ascii85", "asn1": "encoding.asn1", "base32": "encoding.base32", "base64": "encoding.base64",
    "binary": "encoding.binary", "csv": "encoding.csv", "hex": "encoding.hex", "pem": "encoding.pem",
    "xml": "encoding.xml", "json": "encoding.json", "jsontext": "encoding.json.jsontext", "jsonv2": "encoding.json.v2",
    "big": "math.big", "bits": "math.bits", "cmplx": "math.cmplx", "rand": "math.rand",
    "bigfloat": "math.big.float", "rat": "math.big.rat",
    "bzip2": "compress.bzip2", "flate": "compress.flate", "gzip": "compress.gzip", "lzw": "compress.lzw", "zlib": "compress.zlib",
    "tar": "archive.tar", "zip": "archive.zip",
    "heap": "container.heap", "list": "container.list", "ring": "container.ring",
    "color": "image.color", "draw": "image.draw", "gif": "image.gif", "jpeg": "image.jpeg", "png": "image.png",
    "go_token": "go.token", "go_scanner": "go.scanner", "go_ast": "go.ast", "go_parser": "go.parser",
    "go_printer": "go.printer", "go_format": "go.format", "goversion": "go.version",
    "go_doc_comment": "go.doc.comment", "constraint": "go.build.constraint",
    "scanner": "text.scanner", "tabwriter": "text.tabwriter", "template": "text.template",
    "multipart": "mime.multipart", "quotedprintable": "mime.quotedprintable",
    "http": "net.http", "mail": "net.mail", "netip": "net.netip", "textproto": "net.textproto", "url": "net.url",
    "regexp_syntax": "regexp.syntax", "utf8": "unicode.utf8", "utf16": "unicode.utf16",
    "slog": "log.slog", "suffixarray": "index.suffixarray",
}
_std_warned = set()


def std_find(segs, warn=False):
    """segs 是 use std. 之后的路径段。返回 (文件, 用掉的段数, 规范模块名如 "go.ast")；不是 Kite 写的 std 模块时返回 None。
    取最长的模块文件前缀，规则和用户包相同（a/b.kite 或 a/b/mod.kite）；旧的平铺名字按 STD_RENAMED 映射"""
    segs = list(segs)
    if not segs or not isinstance(segs[0], str): return None
    alias = 0
    if segs[0] in STD_RENAMED and not os.path.isfile(os.path.join(STD_DIR, segs[0] + ".kite")):
        new = STD_RENAMED[segs[0]].split(".")
        alias = len(new) - 1
        if warn and segs[0] not in _std_warned:
            _std_warned.add(segs[0])
            print(f"警告: std.{segs[0]} 已改名为 std.{STD_RENAMED[segs[0]]}，旧名字将在下个版本删除", file=sys.stderr)
        segs = new + segs[1:]
    for n in range(len(segs), 0, -1):
        parts = segs[:n]
        if not all(isinstance(x, str) for x in parts): continue
        for f in (os.path.join(STD_DIR, *parts) + ".kite", os.path.join(STD_DIR, *parts, "mod.kite")):
            if os.path.isfile(f):
                if n <= alias: return None
                return f, n - alias, ".".join(parts)
    return None


def kite_std(name):
    r = std_find([name]) if isinstance(name, str) else None
    return bool(r) and r[1] == 1


def kstd_use(p):
    """use 路径 p（以 std 开头）是否引用 Kite 写的 std 模块"""
    if not p or p[0] != "std" or len(p) < 2: return False
    segs = list(p[1:])
    if isinstance(segs[-1], list):
        pre = segs[:-1]
        if pre and std_find(pre): return True
        return any(_full_std(pre + [kite.use_name(m)[0]]) for m in segs[-1])
    return std_find(segs) is not None


def _full_std(segs):
    r = std_find(segs)
    return bool(r) and r[1] == len(segs)
DECL_NAMED = ("Fn", "Struct", "Enum", "Trait", "Alias")


class ModInfo:
    def __init__(self, key, prefix, path, pkg):
        self.key, self.prefix, self.path, self.pkg = key, prefix, path, pkg
        self.items = []
        self.names = {}     # 顶层名字 → 改名后的名字
        self.pub = set()    # pub 的顶层名字
        self.vis = {}       # pub 名字 → "pub" / "pkg" / "mod"
        self.types = set()  # 顶层类型/trait 名字
        self.deps = []      # 依赖的模块 key（按出现顺序）


class Package:
    """一个包：根目录、源码目录、名字前缀、依赖表（名字 → Package）"""
    def __init__(self, name, root, src_dir, prefix):
        self.name, self.root, self.src_dir, self.prefix = name, root, src_dir, prefix
        self.deps = {}


def mangle(prefix, name):
    return f"{prefix}.{name}" if prefix else name


def pat_binds(p, out):
    if not isinstance(p, tuple): return out
    k = p[0]
    if k == "bind": out.append(p[1])
    elif k == "tuple":
        for x in p[1]: pat_binds(x, out)
    elif k == "list":
        for x in p[1]: pat_binds(x, out)
        if p[2] and p[2] != "_": out.append(p[2])
    elif k == "variant" and p[2]:
        for x in p[2]: pat_binds(x, out)
    elif k == "struct":
        for _, x in p[2]: pat_binds(x, out)
    elif k == "or":
        pat_binds(p[1][0], out)
    elif k == "at":
        pat_binds(p[1], out); pat_binds(p[2], out)
    if k == "list" and len(p) > 3:
        for x in p[3]: pat_binds(x, out)
    return out


_LM_COUNTER = [0]

def _rename_mod(x, old, new, exact=False):
    pre, npre = old + ".", new + "."
    def rn(v):
        if isinstance(v, QName):
            q = v.qual
            if isinstance(q, str) and (q == old or q.startswith(pre)): v.qual = new + q[len(old):]
            return v
        if isinstance(v, str):
            if exact and v == old: return new
            return npre + v[len(pre):] if v.startswith(pre) else v
        if isinstance(v, Node):
            if v.kind == "Name" and v.name == old: v.name = new
            if v.kind == "Use" and isinstance(getattr(v, "path", None), list) and v.path and v.path[0] == old:
                v.path[0] = new
            for k, w in list(v.__dict__.items()):
                if k not in ("kind", "line"): setattr(v, k, rn(w))
            return v
        if isinstance(v, list): return [rn(w) for w in v]
        if isinstance(v, tuple): return tuple(rn(w) for w in v)
        if isinstance(v, dict): return {k: rn(w) for k, w in v.items()}
        return v
    return rn(x)

def hoist_local_mods(items):
    """把函数体（含方法、lambda、嵌套块）里的 mod 块取出来，返回提升后的 Mod 节点列表"""
    out = []
    def walk(x):
        if isinstance(x, Node):
            if x.kind == "Mod": return    # 内联模块自己的条目由子模块的 declare 处理
            if x.kind == "Block":
                keep, mods = [], []
                for st in x.stmts:
                    if st.kind == "Mod" and getattr(st, "items", None) is not None: mods.append(st)
                    else: keep.append(st)
                seen = set()
                for m in mods:
                    if m.name == "super":
                        raise KiteError("super 是保留名字，不能用作模块名", m.line)
                    if m.name in seen:
                        raise KiteError(f"模块 {m.name} 重复定义", m.line)
                    seen.add(m.name)
                if mods:
                    x.stmts = keep
                    for m in mods:
                        _LM_COUNTER[0] += 1
                        new = f"__lm{_LM_COUNTER[0]}_{m.name}"
                        for o in mods: _rename_mod(o.items, m.name, new)
                        x.stmts = _rename_mod(x.stmts, m.name, new)
                        m.name = new; m.pub = False
                        out.append(m)
                # 块里的 use <局部 mod>.X：提升为顶层 use … as __lmuN_X，并在整个块里改名（和 Rust 一样对整个块可见）
                uses = [st for st in x.stmts if st.kind == "Use" and isinstance(st.path, list)
                        and isinstance(st.path[0], str) and st.path[0].startswith("__lm")]
                if uses:
                    x.stmts = [st for st in x.stmts if st not in uses]
                    for st in uses:
                        p = st.path
                        if p[-1] == "*":
                            raise KiteError("函数体里不支持 use m.*，请逐个列出名字", st.line)
                        ents = [kite.use_name(e) for e in p[-1]] if isinstance(p[-1], list) \
                            else [(p[-1], getattr(st, "alias", None) or p[-1])]
                        for orig, bind in ents:
                            _LM_COUNTER[0] += 1
                            nb = f"__lmu{_LM_COUNTER[0]}_{bind}"
                            out.append(Node("Use", st.line, path=list(p[:-1]) + [orig], alias=nb, pub=False, vis=None))
                            x.stmts = _rename_mod(x.stmts, bind, nb, bind[:1].isupper())
            for k, v in x.__dict__.items():
                if k not in ("kind", "line"): walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x: walk(v)
    for it in items:
        if it.kind in ("Fn", "Impl", "Let", "Trait"): walk(it)
    return out


class Loader:
    def __init__(self, main_path, overlay=None, pkg=None):
        """main_path: 入口文件；overlay: 入口文件的内存内容（LSP 用）；pkg: 根 Package（None 时自动推断）"""
        self.given = main_path
        self.main_path = os.path.abspath(main_path)
        self.overlay = overlay
        self.mods = {}          # key → ModInfo
        self.order = []         # 依赖在前的拓扑顺序
        self.loading = []       # 正在加载的 key 栈（检测循环）
        self.files = []
        self.by_path = {}
        self.root_pkg = pkg or self.infer_package()

    # ── 包与路径 ──
    def infer_package(self):
        d = os.path.dirname(self.main_path)
        root = d
        while True:
            if os.path.exists(os.path.join(root, "kite.toml")): break
            parent = os.path.dirname(root)
            if parent == root: root = None; break
            root = parent
        if root is None:
            return Package("", d, d, "")
        import pkg as P
        src = os.path.join(root, "src")
        # 包里的文件（src/、tests/ 等）都以 src/ 为模块根；包外的散文件以自身目录为根
        src_dir = src if os.path.isdir(src) else d
        rp = Package(P.read_manifest(root)["name"], root, src_dir, "")
        rp.deps = P.dependency_packages(root, Package)
        return rp

    def find_module_file(self, base_dir, segs):
        """在 base_dir 下找最长的模块文件前缀，返回 (路径, 用掉的段数)"""
        for n in range(len(segs), 0, -1):
            parts = [s for s in segs[:n]]
            if not all(isinstance(s, str) for s in parts): continue
            f1 = os.path.join(base_dir, *parts) + ".kite"
            f2 = os.path.join(base_dir, *parts, "mod.kite")
            for f in (f1, f2):
                if os.path.isfile(f) and os.path.abspath(f) != self.main_path:
                    return f, n
        return None, 0

    def resolve_use(self, info, path, line):
        """返回 (模块 key, 剩余段)"""
        pkg = info.pkg
        head = path[0]
        im = getattr(info, "inline_mods", {})
        if isinstance(head, str) and head in im:
            key = im[head]; rest = list(path[1:])
            while rest and isinstance(rest[0], str) and rest[0] in getattr(self.mods[key], "inline_mods", {}):
                key = self.sub_mod(key, rest[0], line, info); rest = rest[1:]
            return key, rest
        r = std_find(path[1:], warn=True) if head == "std" and len(path) >= 2 else None
        if r:
            f, n, name = r
            parts = name.split(".")
            if "internal" in parts:
                # 和 Go 一样：std/a/internal/x 只能被 std/a 及其下的模块使用
                par = ".".join(["std"] + parts[:parts.index("internal")])
                if not (info.key + ".").startswith(par + "."):
                    raise KiteError(f"不能使用内部模块 std.{name}（只供 {par} 及其子模块使用）", line)
            if not hasattr(self, "std_pkg"): self.std_pkg = Package("std", STD_DIR, STD_DIR, "std")
            return self.load(f"std.{name}", f, self.std_pkg, line), path[1 + n:]
        f, n = self.find_module_file(pkg.src_dir, path)
        if f:
            key = mangle(pkg.prefix, ".".join(path[:n]))
            return self.load(key, f, pkg, line), path[n:]
        if pkg is self.root_pkg and head == pkg.name and pkg.name:
            # 包内的测试或示例引用本包：use geom → src/lib.kite（以包名为前缀）
            lib = os.path.join(pkg.src_dir, "lib.kite")
            if os.path.isfile(lib) and os.path.abspath(lib) != self.main_path:
                f, n = self.find_module_file(pkg.src_dir, path[1:])
                if f: return self.load(mangle(pkg.prefix, ".".join(path[1:1 + n])), f, pkg, line), path[1 + n:]
                return self.load(pkg.name, lib, pkg, line), path[1:]
        if isinstance(head, str) and head in pkg.deps:
            dp = pkg.deps[head]
            f, n = self.find_module_file(dp.src_dir, path[1:])
            if f:
                key = mangle(dp.prefix, ".".join(path[1:1 + n]))
                return self.load(key, f, dp, line), path[1 + n:]
            lib = os.path.join(dp.src_dir, "lib.kite")
            if not os.path.isfile(lib):
                raise KiteError(f"依赖包 {head} 没有 src/lib.kite", line)
            return self.load(dp.prefix, lib, dp, line), path[1:]
        where = os.path.relpath(os.path.join(pkg.src_dir, str(head) + ".kite"))
        raise KiteError(f"找不到模块 {head}（没有 {where}，也不是 kite.toml 里的依赖）", line)

    # ── 加载 ──
    def parse_file(self, path, src=None):
        idx = len(self.files)
        self.files.append(path)
        if src is None:
            try:
                with open(path, encoding="utf-8") as fh: src = fh.read()
            except OSError as e:
                raise KiteError(f"无法读取 {path}: {e.strerror}")
        try:
            toks = lex(src)
            if idx:
                for t in toks: t.line += idx * LINE_BASE
            return Parser(toks).program()
        except KiteError as e:
            # 被导入文件里的词法 / 语法错误：报这个文件自己的路径和行号
            if idx and not getattr(e, "loc", None) and e.line is not None:
                n = e.line % LINE_BASE
                e.loc = f"{path}:{n}:{e.col}" if e.col is not None else f"{path}:{n}"
            raise

    def load(self, key, path, pkg, line):
        ap = os.path.abspath(path)
        key = self.by_path.setdefault(ap, key)   # 同一个文件只加载一次
        if key in self.mods:
            if key in self.loading:
                chain = " → ".join(self.loading[self.loading.index(key):] + [key])
                raise KiteError(f"模块循环引用: {chain}", line)
            return key
        info = ModInfo(key, key, path, pkg)
        self.mods[key] = info
        self.loading.append(key)
        info.items = self.parse_file(path)
        self.process(info)
        self.loading.pop()
        self.order.append(key)
        return key

    def load_main(self):
        info = ModInfo("", "", self.main_path, self.root_pkg)
        self.mods[""] = info
        self.loading.append("")
        info.items = self.parse_file(self.main_path, self.overlay)
        self.process(info)
        self.loading.pop()
        def rel(f):
            r = os.path.relpath(f)
            return os.path.relpath(f, self.root_pkg.root) if r.startswith(".." + os.sep + "..") else r
        kite.SOURCES[:] = [self.given] + [rel(f) for f in self.files[1:]]
        variants = {}
        for key in self.order + [""]:
            for it in self.mods[key].items:
                if it.kind == "Enum":
                    for vn, _ in it.variants:
                        if vn in variants and variants[vn] != key:
                            a = variants[vn] or "主文件"
                            raise KiteError(f"枚举变体 {vn} 在 {a} 和 {key or '主文件'} 中重复定义"
                                            f"（变体目前是全局名字）", it.line)
                        variants[vn] = key
        out = []
        for key in self.order + [""]:
            out.extend(self.mods[key].items)
        return order_consts(out)

    def process(self, info):
        self.declare(info)
        self.finish(info)

    def declare(self, info):
        """第 1 步：登记顶层名字，并递归登记所有内联子模块的名字（这样子模块之间、子模块与父模块之间可以互相引用）"""
        # 0) 函数体里的 mod m { … }：提升为所在模块的私有内联模块 __lm<N>_m，
        #    并在声明它的块里把 m / m.X 改写成新名字（super 仍指向所在模块，和 Rust 一致）
        info.items = hoist_local_mods(info.items) + info.items
        # 1) 顶层名字
        for it in info.items:
            names = []
            if it.kind in DECL_NAMED: names = [it.name]
            elif it.kind == "Let": names = pat_binds(it.pat, [])
            for nm in names:
                info.names[nm] = mangle(info.prefix, nm)
                if getattr(it, "pub", False):
                    info.pub.add(nm); info.vis[nm] = getattr(it, "vis", None) or "pub"
                if it.kind in ("Struct", "Enum", "Trait", "Alias"): info.types.add(nm)
        # 1.2) 内联模块 mod m { … }：当作同一文件里的子模块（key = 父 key.m），父模块自动得到别名 m；
        #      先登记父模块的顶层名字（第 1 步），子模块才能用 super.x 引用父模块（包括私有项）
        inline = dict(getattr(info, "inline_mods", {}))
        rest = []
        for it in info.items:
            if it.kind == "Mod" and getattr(it, "items", None) is not None:
                if it.name == "super":
                    raise KiteError("super 是保留名字，不能用作模块名", it.line)
                if it.name in inline:
                    raise KiteError(f"模块 {it.name} 重复定义", it.line)
                ckey = f"{info.key}.{it.name}" if info.key else it.name
                if ckey in self.mods:
                    raise KiteError(f"内联模块 {it.name} 和已有模块 {ckey} 重名", it.line)
                child = ModInfo(ckey, ckey, info.path, info.pkg)
                child.items = it.items; child.inline = True; child.parent = info
                child.inline_mods = {"super": info.key}; child.inline_pub = {"super": False}
                self.mods[ckey] = child
                self.declare(child)
                info.children = getattr(info, "children", []) + [child]
                inline[it.name] = ckey
                info.inline_pub = getattr(info, "inline_pub", {}); info.inline_pub[it.name] = bool(getattr(it, "pub", False))
                info.deps.append(ckey)
            else:
                rest.append(it)
        info.items = rest
        info.inline_mods = inline

    def finish(self, info):
        for child in getattr(info, "children", []):
            self.loading.append(child.key)
            self.finish(child)
            self.loading.pop()
            self.order.append(child.key)
        # 1.5) 函数体里的 use std.X（X 是 Kite 写的模块）提升到顶层；与 VM 对内建模块的处理一致，名字全局可见
        def is_kstd(u):
            p = u.path
            return kstd_use(p)
        hoisted = []
        def walk(x):
            if isinstance(x, Node):
                if x.kind == "Block":
                    hoisted.extend(st for st in x.stmts if st.kind == "Use" and is_kstd(st))
                    x.stmts = [st for st in x.stmts if not (st.kind == "Use" and is_kstd(st))]
                for k, v in x.__dict__.items():
                    if k not in ("kind", "line"): walk(v)
            elif isinstance(x, (list, tuple)):
                for v in x: walk(v)
        for it in info.items:
            if it.kind in ("Fn", "Impl", "Let"): walk(it)
        info.items = hoisted + info.items
        # 2) use：别名与导入
        aliases, imports = dict(info.inline_mods), {}
        kept = []
        uses = []
        for it in info.items:
            p = it.path if it.kind == "Use" else None
            pre = list(p[1:-1]) if p and p[0] == "std" and len(p) >= 2 and isinstance(p[-1], list) else None
            if pre is not None and all(isinstance(x, str) for x in pre) and any(_full_std(pre + [kite.use_name(m)[0]]) for m in p[-1]):
                # use std.{json, math} / use std.go.{ast, parser}：Kite 写的模块逐个处理，内建模块留给后端
                for m in p[-1]:
                    nm, al = kite.use_name(m)
                    if _full_std(pre + [nm]): uses.append(Node("Use", it.line, path=["std"] + pre + [nm], alias=al))
                rest = [m for m in p[-1] if not _full_std(pre + [kite.use_name(m)[0]])]
                if rest:
                    if pre: raise KiteError(f"std.{'.'.join(pre)} 下没有模块 {', '.join(kite.use_name(m)[0] for m in rest)}", it.line)
                    uses.append(Node("Use", it.line, path=["std", rest], **{k: v for k, v in it.__dict__.items() if k not in ("kind", "line", "path")}))
            else:
                uses.append(it)
        info.items = uses
        for it in info.items:
            if it.kind == "Use" and it.path and (it.path[0] != "std" or kstd_use(it.path)):
                key, rest = self.resolve_use(info, it.path, it.line)
                while rest and isinstance(rest[0], str) and rest[0] in getattr(self.mods[key], "inline_mods", {}):
                    key = self.sub_mod(key, rest[0], it.line, info); rest = rest[1:]
                if key not in info.deps: info.deps.append(key)
                al = getattr(it, "alias", None)
                if not rest:
                    aliases[al or it.path[-1]] = key
                elif rest == ["*"]:
                    m = self.mods[key]
                    for nm in sorted(m.pub):
                        if self.visible(m, nm, info): imports[nm] = m.names[nm]
                else:
                    if len(rest) > 1 and not isinstance(rest[-1], list):
                        raise KiteError(f"找不到模块 {'.'.join(map(str, it.path[:-1]))}", it.line)
                    names = rest[-1] if isinstance(rest[-1], list) else [(rest[-1], al or rest[-1])]
                    for e in names:
                        nm, b = kite.use_name(e)
                        imports[b] = self.export(key, nm, it.line, info)
                continue
            kept.append(it)
        info.items = kept
        # 3) 改名
        R = Resolver(self, info, aliases, imports)
        for it in info.items:
            R.top(it)

    @staticmethod
    def within(req, m):
        """req 是 m 本身或 m 的内联后代（后代能看到祖先的私有项）"""
        while req is not None:
            if req is m: return True
            req = getattr(req, "parent", None)
        return False

    def sub_mod(self, key, name, line, req):
        """从模块 key 进入它的内联子模块 name；非 pub 的子模块只有父模块自己能用"""
        m = self.mods[key]
        ck = getattr(m, "inline_mods", {}).get(name)
        if ck is None: return None
        if name == "super" and not self.within(req, m):
            raise KiteError(f"{key}.super 只能在模块 {key} 内部使用", line)
        if not self.within(req, m) and not getattr(m, "inline_pub", {}).get(name):
            raise KiteError(f"{key}.{name} 是私有模块（写 pub mod {name} 才能在外面使用）", line)
        return ck

    def export(self, key, name, line, req=None):
        m = self.mods[key]
        if name not in m.names:
            raise KiteError(f"模块 {key} 里没有 {name}", line)
        if name not in m.pub and not (req is not None and self.within(req, m)):
            raise KiteError(f"{key}.{name} 是私有的（在定义前加 pub 才能被其他模块使用）", line)
        if req is not None and not self.visible(m, name, req):
            lvl = m.vis.get(name)
            what = "本包" if lvl == "pkg" else "所在目录的模块"
            raise KiteError(f"{key}.{name} 是 pub({lvl})，只对{what}可见", line)
        return m.names[name]

    @staticmethod
    def mod_scope(m):
        """pub(mod) 的可见范围：定义文件的父模块目录（lib.kite 为整个包）"""
        d = os.path.dirname(os.path.abspath(m.path))
        base = os.path.basename(m.path)
        if base == "lib.kite": return os.path.abspath(m.pkg.src_dir)
        if base == "mod.kite": return os.path.dirname(d)
        return d

    def visible(self, m, name, req):
        lvl = m.vis.get(name, "pub")
        if lvl == "pub" or self.within(req, m): return True
        if req.pkg is not m.pkg: return False
        if lvl == "pkg": return True
        scope = self.mod_scope(m)
        rp = os.path.abspath(req.path)
        return rp == scope or rp.startswith(scope + os.sep)


class Resolver:
    """把一个模块的 AST 改写成全局名字：自己的顶层名字加前缀、导入名展开、别名.成员展开"""
    def __init__(self, loader, info, aliases, imports):
        self.L, self.info, self.aliases, self.imports = loader, info, aliases, imports
        self.scopes = []

    # 名字解析
    def local(self, name):
        return any(name in s for s in self.scopes)

    def value(self, name):
        if self.local(name): return name
        if name in self.info.names: return self.info.names[name]
        if name in self.imports: return self.imports[name]
        return name

    def type_name(self, name, generics=()):
        if name in generics: return name
        if "." in name:
            head, rest = name.split(".", 1)
            if head in self.aliases:
                key, parts = self.aliases[head], rest.split(".")
                while len(parts) > 1 and parts[0] in getattr(self.L.mods[key], "inline_mods", {}):
                    key = self.L.sub_mod(key, parts[0], None, self.info); parts = parts[1:]
                if len(parts) == 1:
                    return self.L.export(key, parts[0], None, self.info)
            return name
        if name in self.info.types: return self.info.names[name]
        if name in self.imports: return self.imports[name]
        return name

    def ty(self, t, gens):
        if t is None: return None
        k = t[0]
        if k == "con":
            return ("con", self.type_name(t[1], gens), [self.ty(a, gens) for a in t[2]], t[3])
        if k == "tuple":
            return ("tuple", [self.ty(a, gens) for a in t[1]], t[2])
        if k == "fn":
            return ("fn", [self.ty(a, gens) for a in t[1]], self.ty(t[2], gens), t[3])
        if k in ("dyn", "impl"):
            return (k, [self.type_name(b, gens) for b in t[1]], t[2])
        if k == "unsafe":
            return ("unsafe", self.ty(t[1], gens), t[2])
        return t

    def pat(self, p):
        """改写模式里的类型名，并返回新模式（绑定名由调用者登记）"""
        if not isinstance(p, tuple): return p
        k = p[0]
        if k == "tuple": return ("tuple", [self.pat(x) for x in p[1]])
        if k == "list": return ("list", [self.pat(x) for x in p[1]], p[2], *([[self.pat(x) for x in p[3]]] if len(p) > 3 else []))
        if k == "at": return ("at", p[1], self.pat(p[2]))
        if k == "variant":
            if p[2] is None and not isinstance(p[1], QName) and p[1] in self.info.names and not self.local(p[1]):
                return ("variant", self.info.names[p[1]], None)   # 本模块的常量 / 单元结构体
            return ("variant", p[1], [self.pat(x) for x in p[2]] if p[2] else p[2])
        if k == "struct": return ("struct", self.type_name(p[1]), [(f, self.pat(x)) for f, x in p[2]])
        if k == "or": return ("or", [self.pat(x) for x in p[1]])
        return p

    def bind(self, p):
        for nm in pat_binds(p, []): self.scopes[-1].add(nm)

    # 顶层
    def top(self, it):
        k = it.kind
        gens = tuple(getattr(it, "generics", None) or ())
        if k in ("Struct", "Enum", "Alias") and getattr(it, "bounds", None):
            it.bounds = {g: [self.type_name(b, gens) for b in bs] for g, bs in it.bounds.items()}
        if k == "Fn":
            it.name = self.value(it.name)
            self.fn(it, ())
        elif k == "Struct":
            it.name = self.info.names[it.name]
            it.fields = [(f, self.ty(t, gens)) for f, t in it.fields]
        elif k == "Enum":
            it.name = self.info.names[it.name]
            it.variants = [(vn, type(fs)((f, self.ty(t, gens)) for f, t in fs) if fs else fs) for vn, fs in it.variants]   # 保留 RecFields
        elif k == "Alias":
            it.name = self.info.names[it.name]
            it.target = self.ty(it.target, gens)
        elif k == "Trait":
            it.name = self.info.names[it.name]
            it.assoc = [(an, [self.type_name(b) for b in bs], self.ty(d, ("Self",))) for an, bs, d in getattr(it, "assoc", [])]
            it.supers = [self.type_name(b) for b in getattr(it, "supers", [])]
            it.consts = [(n, self.ty(t, ("Self",)), self.expr(e) if e is not None else None, ln)
                         for n, t, e, ln in getattr(it, "consts", [])]
            for m in it.methods: self.fn(m, ("Self",))
        elif k == "Impl":
            self.impl(it)
        elif k == "Let":
            it.value = self.expr(it.value)
            it.ty = self.ty(it.ty, ())
            it.pat = self.top_pat(self.pat(it.pat))
        elif k in ("Use", "Mod"):
            pass
        else:
            self.scopes.append(set())
            self.stmt(it)
            self.scopes.pop()

    def top_pat(self, p):
        if not isinstance(p, tuple): return p
        k = p[0]
        if k == "bind": return ("bind", self.info.names.get(p[1], p[1]))
        if k == "or": return ("or", [self.top_pat(x) for x in p[1]])
        if k == "at": return ("at", self.top_pat(p[1]), self.top_pat(p[2]))
        if k == "tuple": return ("tuple", [self.top_pat(x) for x in p[1]])
        if k == "list":
            rest = self.info.names.get(p[2], p[2]) if p[2] else p[2]
            return ("list", [self.top_pat(x) for x in p[1]], rest, *([[self.top_pat(x) for x in p[3]]] if len(p) > 3 else []))
        if k == "variant": return ("variant", p[1], [self.top_pat(x) for x in p[2]] if p[2] else p[2])
        if k == "struct": return ("struct", p[1], [(f, self.top_pat(x)) for f, x in p[2]])
        return p

    def impl(self, it):
        gens = tuple(it.generics or ())
        it.target = self.type_name(it.target, gens)
        it.target_t = self.ty(it.target_t, gens)
        if it.trait: it.trait = self.type_name(it.trait, gens)
        it.assoc = [(an, self.ty(t, gens + ("Self",))) for an, t in getattr(it, "assoc", [])]
        for m in it.methods: self.fn(m, gens + ("Self",))
        it.consts = [(n, self.ty(t, gens + ("Self",)), self.expr(e) if e is not None else None, ln)
                     for n, t, e, ln in getattr(it, "consts", [])]

    def fn(self, f, outer_gens):
        gens = tuple(outer_gens) + tuple(f.generics or ())
        f.params = [(n, self.ty(t, gens)) for n, t in f.params]
        f.ret = self.ty(f.ret, gens)
        if getattr(f, "bounds", None):
            f.bounds = {g: [self.type_name(b, gens) for b in bs] for g, bs in f.bounds.items()}
        if f.body is not None:
            self.scopes.append({n for n, _ in f.params})
            self.gens = gens
            f.body = self.expr(f.body)
            self.scopes.pop()

    # 语句与表达式
    def block(self, b):
        self.scopes.append(set())
        for s in b.stmts:   # 块内声明提升
            if s.kind in DECL_NAMED: self.scopes[-1].add(s.name)
        for s in b.stmts: self.stmt(s)
        self.scopes.pop()
        return b

    def stmt(self, s):
        k = s.kind
        if k == "Let":
            s.value = self.expr(s.value)
            s.ty = self.ty(s.ty, getattr(self, "gens", ()))
            s.pat = self.pat(s.pat)
            self.bind(s.pat)
        elif k == "Fn":
            self.fn(s, getattr(self, "gens", ()))
        elif k == "Impl":
            self.impl(s)
        elif k in ("Struct", "Enum", "Alias", "Trait", "Use", "Mod"):
            pass
        elif k == "ExprStmt":
            s.expr = self.expr(s.expr)
        elif k == "Assign":
            s.target = self.expr(s.target); s.value = self.expr(s.value)
        elif k == "Return":
            if s.value is not None: s.value = self.expr(s.value)
        elif k in ("Break", "Continue"):
            if getattr(s, "value", None) is not None: s.value = self.expr(s.value)
        elif k == "Defer":
            s.body = self.expr(s.body)
        elif k == "For":
            s.iter = self.expr(s.iter)
            s.pat = self.pat(s.pat)
            self.scopes.append(set()); self.bind(s.pat)
            s.body = self.expr(s.body)
            self.scopes.pop()
        elif k == "While":
            if s.cond is not None: s.cond = self.expr(s.cond)
            s.body = self.expr(s.body)
        else:
            self.expr(s)
        return s

    def expr(self, e):
        if e is None or not isinstance(e, Node): return e
        k = e.kind
        if k == "Name":
            e.name = self.value(e.name); return e
        if k == "Field":
            o = e.obj
            if o.kind == "Name" and not self.local(o.name) and o.name in self.aliases:
                im = getattr(self.L.mods[self.aliases[o.name]], "inline_mods", {})
                if e.name in im:          # 内联子模块 a.b：记成模块引用，由外层的 .成员 解析
                    r = Node("Field", e.line, obj=o, name=e.name)
                    r._modkey = self.L.sub_mod(self.aliases[o.name], e.name, e.line, self.info); return r
                return Node("Name", e.line, name=self.L.export(self.aliases[o.name], e.name, e.line, self.info))
            if o.kind == "Field":
                o2 = self.expr(o)
                key = getattr(o2, "_modkey", None)
                if key is not None:
                    im = getattr(self.L.mods[key], "inline_mods", {})
                    if e.name in im:
                        r = Node("Field", e.line, obj=o2, name=e.name)
                        r._modkey = self.L.sub_mod(key, e.name, e.line, self.info); return r
                    return Node("Name", e.line, name=self.L.export(key, e.name, e.line, self.info))
                e.obj = o2; return e
            e.obj = self.expr(o); return e
        if k == "Block": return self.block(e)
        if k == "TypeApp":
            e.fn = self.expr(e.fn)
            e.targs = [self.ty(t, getattr(self, "gens", ())) if t is not None else None for t in e.targs]
            return e
        if k == "Lambda":
            e.params = [(n, self.ty(t, getattr(self, "gens", ()))) for n, t in e.params]
            self.scopes.append({n for n, _ in e.params})
            e.body = self.expr(e.body)
            self.scopes.pop(); return e
        if k == "Match":
            e.subject = self.expr(e.subject)
            arms = []
            for pat, guard, body in e.arms:
                pat = self.pat(pat)
                self.scopes.append(set()); self.bind(pat)
                arms.append((pat, self.expr(guard), self.expr(body)))
                self.scopes.pop()
            e.arms = arms; return e
        if k == "Select":
            arms = []
            for kind, a, b, pat, guard, body in e.arms:
                a = self.expr(a) if a is not None else None
                b = self.expr(b) if b is not None else None
                guard = self.expr(guard) if guard is not None else None
                self.scopes.append(set())
                if pat is not None:
                    pat = self.pat(pat); self.bind(pat)
                arms.append((kind, a, b, pat, guard, self.expr(body)))
                self.scopes.pop()
            e.arms = arms; return e
        if k == "Is":
            e.value = self.expr(e.value); e.pat = self.pat(e.pat); return e
        if k == "StructLit":
            q = getattr(e.name, "qual", None)
            e.name = self.type_name(e.name)
            if isinstance(q, str):
                # 限定的记录变体 N.Rect { … } / m.N.Rect { … }：限定名也要按导入改名
                nq = self.type_name(q)
                if nq != q:
                    qn = QName(str(e.name))
                    qn.qual = nq
                    e.name = qn
            e.fields = [(f, self.expr(x)) for f, x in e.fields]
            if getattr(e, "base", None) is not None: e.base = self.expr(e.base)   # 结构体更新 ..base 里的名字也要改名
            return e
        if k == "Interp":
            e.parts = [p if isinstance(p, str) else self.expr(p) for p in e.parts]; return e
        if k in ("Let", "Assign", "Return", "Defer", "For", "While", "ExprStmt", "Break", "Continue",
                 "Fn", "Impl", "Struct", "Enum", "Alias", "Trait", "Use", "Mod"):
            return self.stmt(e)
        # 其余节点：逐个字段改写子节点
        for f, v in list(e.__dict__.items()):
            if f in ("kind", "line"): continue
            if isinstance(v, Node):
                setattr(e, f, self.expr(v))
            elif isinstance(v, list) and v and all(isinstance(x, Node) for x in v):
                setattr(e, f, [self.expr(x) for x in v])
        return e


def order_consts(items):
    """顶层 const 按依赖排序（内联子模块的 const 可以通过 super 引用父模块后面才出现的 const）。
    其他项保持原顺序；所有 const 放到第一个 const 的位置。"""
    idx = [i for i, it in enumerate(items) if it.kind == "Let"]
    if len(idx) < 2: return items
    if any(it.kind not in DECL_NAMED + ("Impl", "Use", "Mod", "Let") for it in items) or \
            any(not getattr(items[i], "is_const", False) for i in idx):
        return items          # 旧语法的顶层语句（REPL / --migrate）保持原顺序
    consts = [items[i] for i in idx]
    owner = {}
    for c in consts:
        for nm in pat_binds(c.pat, []): owner[nm] = c
    def refs(x, acc):
        if isinstance(x, Node):
            if x.kind == "Name" and x.name in owner: acc.add(id(owner[x.name]))
            for k, v in x.__dict__.items():
                if k not in ("kind", "line"): refs(v, acc)
        elif isinstance(x, (list, tuple)):
            for v in x: refs(v, acc)
        return acc
    deps = {id(c): refs(c.value, set()) for c in consts}
    done, out, busy = set(), [], set()
    def visit(c):
        if id(c) in done or id(c) in busy: return
        busy.add(id(c))
        for d in consts:
            if id(d) in deps[id(c)]: visit(d)
        busy.discard(id(c)); done.add(id(c)); out.append(c)
    for c in consts: visit(c)
    rest = [it for it in items if it.kind != "Let"]
    first = sum(1 for it in items[:idx[0]] if it.kind != "Let")
    return rest[:first] + out + rest[first:]


def load_program(path, overlay=None):
    """入口：返回拼接后的 AST（顶层项列表）"""
    return Loader(path, overlay).load_main()
