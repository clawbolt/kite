#!/usr/bin/env python3
"""Kite 原型解释器 (M0, tree-walking)

用法:
    python3 kite.py file.kite      运行文件（若定义了 main 则自动调用）
    python3 kite.py                 进入 REPL

原型范围: 词法/语法完整覆盖 grammar.ebnf 的 M0 子集；类型注解会被解析但暂不检查；
spawn 在参考实现里按顺序执行（语义与并行一致：任务之间不能写对方的变量）；所有绑定默认不可变（let），var 可重新赋值。
"""
import functools
import re
import os
import sys, math

# ───────────────────────── 错误 ─────────────────────────
# 行号编码：第 i 个源文件第 n 行 = i * LINE_BASE + n（主文件 i = 0），见 modules.py
LINE_BASE = 1000000
SOURCES = []
PARSE_LEGACY = False    # kite fmt --migrate：按 0.48 的优先级解析
ALLOW_TOP_STMTS = False # REPL：允许顶层语句

def locate(line, default=None):
    """把编码后的行号还原成 (文件, 行)"""
    if line is None: return default, None
    i, n = divmod(line, LINE_BASE)
    if i and i < len(SOURCES): return SOURCES[i], n
    return (SOURCES[0] if SOURCES else default), n

def format_error(e, default=None):
    line = getattr(e, "line", None)
    msg = getattr(e, "msg", str(e))
    if getattr(e, "loc", None): return f"{e.loc}: {msg}"     # 重新抛出的错误保留原位置
    f, n = locate(line, default)
    if n is None: return f"{f}: {msg}" if f else msg
    col = getattr(e, "col", None)
    return f"{f}:{n}:{col}: {msg}" if col is not None else f"{f}:{n}: {msg}"


class KiteError(Exception):
    def __init__(self, msg, line=None, col=None):
        if isinstance(msg, str) and "__lm" in msg:
            import re as _re
            msg = _re.sub(r"__lmu?\d+_", "", msg)     # 函数体里 mod / use 提升后的内部名字，报错时还原
        self.msg, self.line, self.col = msg, line, col
        loc = (f"{line}:{col}: " if col is not None else f"{line}: ") if line is not None else ""
        super().__init__(f"{loc}{msg}")

I64_MIN, I64_MAX = -(1 << 63), (1 << 63) - 1


def ck(r, line=None):
    """Int 结果必须在 64 位范围内（与原生后端一致）"""
    if type(r) is int and not I64_MIN <= r <= I64_MAX: raise KiteError("整数溢出", line)
    return r


def f2i(x):
    if x != x or not -9223372036854775808.0 <= x < 9223372036854775808.0: raise KiteError("整数溢出")
    return int(x)


class FatalError(KiteError):
    """不可恢复的错误（死锁等）：recover / supervise 不会接住"""

class Reraised(KiteError):
    """repanic 重新抛出的错误：位置是原来的出错处"""
    def __init__(self, msg, loc):
        super().__init__(msg, 0); self.loc = loc

# 运行时错误分类：(消息前缀, PanicKind 序号)，与 runtime/kite_rt.c 的 PANIC_PREFIXES 保持一致
PANIC_PREFIXES = [("panic: ", 0), ("除以零", 1), ("整数溢出", 2), ("下标越界", 3), ("unwrap 失败", 4),
                  ("assert", 5), ("向已关闭的通道发送", 6), ("actor 已经停止", 6), ("通道已经关闭", 6)]
def classify_panic(msg):
    """返回 (种类序号, 展示用消息)；panic(...) 的消息去掉 "panic: " 前缀"""
    for p, k in PANIC_PREFIXES:
        if msg.startswith(p): return k, (msg[len(p):] if k == 0 else msg)
    return 7, msg
def panic_location(e):
    if getattr(e, "loc", None): return e.loc
    f, n = locate(e.line)
    if f and SOURCES and f == SOURCES[0]: f = os.path.basename(f)   # 与原生一致：主文件只写文件名
    return f"{f or '?'}:{n if n is not None else 0}"

# 预导入：PanicKind、Panic、recover、repanic、supervise_if（Kite 源码，见 prelude.kite）
_PRELUDE_SRC = None
def _subst_self(t, target_t):
    if t is None or not isinstance(t, tuple): return t
    k = t[0]
    if k == "con":
        if t[1] == "Self" and not t[2]: return target_t
        return ("con", t[1], [_subst_self(a, target_t) for a in t[2]], t[3])
    if k == "tuple": return ("tuple", [_subst_self(a, target_t) for a in t[1]], t[2])
    if k == "fn": return ("fn", [_subst_self(a, target_t) for a in t[1]], _subst_self(t[2], target_t), t[3])
    return t

TRAIT_CONSTS = {}   # trait 名 → [(常量名, 类型, 默认值, 行)]


def lower_consts(items):
    """关联常量：impl 里的 const N: T = e 生成顶层函数 "类型.N"() 和实例方法 __c_N(self)；
    trait 里的 const 变成方法 __c_N(self)（有默认值时带默认体）。类型.N / impl 里的 Self.N 改写为调用 "类型.N"()，
    trait 默认方法里的 Self.N 改写为 self.__c_N()（按值的实际类型动态分派）。"""
    import copy
    tconsts = {it.name: getattr(it, "consts", []) for it in items if it.kind == "Trait"}
    TRAIT_CONSTS.clear(); TRAIT_CONSTS.update(tconsts)      # 类型检查器用它给 T.N 生成隐藏参数
    tsupers = {it.name: getattr(it, "supers", []) for it in items if it.kind == "Trait"}
    if not any(tconsts.values()) and not any(getattr(it, "consts", None) for it in items if it.kind == "Impl"):
        return items
    def tclosure(t, out=None):
        out = [] if out is None else out
        if t in out: return out
        out.append(t)
        for s in tsupers.get(t, []): tclosure(s, out)
        return out
    owned, extra = set(), []
    def mk_body(e, ln): return Node("Block", ln, stmts=[Node("ExprStmt", ln, expr=e)])
    for it in items:
        if it.kind == "Trait":
            for name, ty, val, ln in it.consts:
                m = Node("Fn", ln, name=f"__c_{name}", params=[("self", None)], ret=ty,
                         body=None if val is None else mk_body(val, ln), generics=[], bounds={})
                m.attrs = []; m.const_name = name
                it.methods.append(m)
        elif it.kind == "Impl":
            given = list(getattr(it, "consts", []))
            if it.trait:
                decl = tconsts.get(it.trait, [])
                dnames = {c[0] for c in decl}
                for c in given:
                    if it.trait in tconsts and c[0] not in dnames:
                        raise KiteError(f"类型错误: trait {it.trait} 没有关联常量 {c[0]}", c[3])
                gn = {c[0] for c in given}
                for c in decl:
                    if c[0] not in gn and c[2] is not None:
                        given.append((c[0], c[1], copy.deepcopy(c[2]), c[3]))
            for name, ty, val, ln in given:
                ty2 = _subst_self(ty, it.target_t)
                fn = Node("Fn", ln, name=f"{it.target}.{name}", params=[], ret=ty2, body=mk_body(val, ln),
                          generics=list(it.generics), bounds=dict(getattr(it, "bounds", {}) or {}))
                fn.pub = True; fn.static_of = it.target; fn.is_unsafe = False; fn.is_async = False
                fn.cgens = list(getattr(it, "cgens", []) or [])
                if getattr(it, "prelude", False): fn.prelude = True
                extra.append(fn); owned.add((it.target, name))
                ln2 = ln
                call = Node("Call", ln2, fn=Node("Name", ln2, name=f"{it.target}.{name}"), args=[])
                m = Node("Fn", ln, name=f"__c_{name}", params=[("self", None)], ret=ty, body=mk_body(call, ln),
                         generics=[], bounds={})
                m.attrs = []; m.const_name = name; m.pub = True
                it.methods.append(m)

    def walk(x, target, trait, has_self):
        if isinstance(x, Node):
            if x.kind == "Impl": target, trait = x.target, None
            elif x.kind == "Trait": target, trait = None, x.name
            elif x.kind == "Fn" and (target or trait) and not x.name.startswith("__c_"):
                has_self = bool(x.params) and x.params[0][0] == "self"
            if x.kind == "Field" and isinstance(x.obj, Node) and x.obj.kind == "Name":
                q = x.obj.name
                if q == "Self" and target: q = target
                if (q, x.name) in owned:
                    return Node("Call", x.line, fn=Node("Name", x.line, name=f"{q}.{x.name}"), args=[])
                if q == "Self" and trait and any(c[0] == x.name for t in tclosure(trait) for c in tconsts.get(t, [])):
                    if not has_self:
                        raise KiteError(f"类型错误: trait 里没有 self 的方法不能使用 Self.{x.name}", x.line)
                    return Node("Call", x.line, fn=Node("Field", x.line, obj=Node("Name", x.line, name="self"), name=f"__c_{x.name}"), args=[])
            for k, v in list(x.__dict__.items()):
                if k not in ("kind", "line", "consts"): x.__dict__[k] = walk(v, target, trait, has_self)
            return x
        if isinstance(x, list): return [walk(v, target, trait, has_self) for v in x]
        if isinstance(x, tuple) and x and isinstance(x[0], str) and x[0] in ("con", "fn", "tuple", "array"): return x
        if isinstance(x, tuple): return tuple(walk(v, target, trait, has_self) for v in x)
        return x
    for fn in extra: fn.body = walk(fn.body, fn.static_of, None, False)
    return [walk(it, None, None, False) for it in items] + extra


def lower_statics(items):
    """静态方法：impl 里不带 self 的方法另生成顶层函数 "类型.方法"，并把 类型.方法 / Self.方法 改写为该函数名。"""
    statics, extra = set(), []
    for it in items:
        if it.kind != "Impl" or it.trait: continue
        for m in it.methods:
            if m.params and m.params[0][0] == "self": continue
            statics.add((it.target, m.name))
            b = dict(getattr(it, "bounds", {}) or {}); b.update(m.bounds or {})
            fn = Node("Fn", m.line, name=f"{it.target}.{m.name}",
                      params=[(n, _subst_self(t, it.target_t)) for n, t in m.params],
                      ret=_subst_self(m.ret, it.target_t), body=m.body,
                      generics=list(it.generics) + [g for g in m.generics if g not in it.generics], bounds=b)
            fn.pub = getattr(m, "pub", True); fn.static_of = it.target
            fn.is_unsafe = getattr(m, "is_unsafe", False); fn.is_async = getattr(m, "is_async", False)
            fn.cgens = list(getattr(it, "cgens", []) or []) + [g for g in (getattr(m, "cgens", []) or []) if g not in (getattr(it, "cgens", []) or [])]
            if getattr(it, "prelude", False): fn.prelude = True
            extra.append(fn)
    if not statics: return items

    def walk(x, target):
        if isinstance(x, Node):
            if x.kind == "Impl": target = x.target
            if x.kind == "Field" and isinstance(x.obj, Node) and x.obj.kind == "Name":
                q = target if x.obj.name == "Self" and target else x.obj.name
                if (q, x.name) in statics:
                    return Node("Name", x.line, name=f"{q}.{x.name}")
            for k, v in list(x.__dict__.items()):
                if k not in ("kind", "line"): x.__dict__[k] = walk(v, target)
            return x
        if isinstance(x, list): return [walk(v, target) for v in x]
        if isinstance(x, tuple) and x and isinstance(x[0], str) and x[0] in ("con", "fn", "tuple", "array"): return x
        if isinstance(x, tuple): return tuple(walk(v, target) for v in x)
        return x
    for fn in extra: fn.body = walk(fn.body, fn.static_of)
    items = [walk(it, None) for it in items]
    return items + extra

def with_prelude(items):
    global _PRELUDE_SRC
    if any(getattr(it, "prelude", False) for it in items): return items
    if _PRELUDE_SRC is None:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "prelude.kite"), encoding="utf-8") as f:
            _PRELUDE_SRC = f.read()
    pre = Parser(lex(_PRELUDE_SRC)).program()
    for it in pre:
        it.prelude = True
        for m in getattr(it, "methods", None) or []: m.prelude = True
    return lower_statics(lower_consts(pre + list(items)))

# ───────────────────────── 词法 ─────────────────────────
KEYWORDS = {
    "let", "var", "fn", "type", "enum", "trait", "impl", "match", "if", "else",
    "for", "while", "loop", "break", "continue", "return", "use", "mod", "pub",
    "spawn", "await", "defer", "unsafe", "extern", "as", "in", "is", "self",
    "Self", "true", "false", "const", "where", "dyn", "async",
}
OPS = ["<<=", ">>=", "..=", "|>", "=>", "->", "==", "!=", "<<", ">>", "<=", ">=", "&&", "||", "..",
       "+=", "-=", "*=", "/=", "%=", "|=", "&=", "^=", "::",
       "|", "&", "^", "+", "-", "*", "/", "%", "<", ">", "=", "!", "~", "(", ")", "{", "}", "[", "]",
       ",", ":", ".", "?", ";", "@"]
# 行尾出现这些 token 时，下一行是续行
CONT_END = {"+", "-", "*", "/", "%", "<", ">", "==", "!=", "<=", ">=", "&&", "||", "|", "&", "^", "<<", ">>",
            "=", "+=", "-=", "*=", "/=", "%=", "|=", "&=", "^=", "<<=", ">>=", "|>", "=>", "->", ",", ".", "(", "[",
            "{", ":", "..", "..=", "!", "~"}
# 下一行以这些 token 开头时，视为续行
CONT_START = {"|>", ".", "}", ")", "]", "?", "&&", "||", "else", "where"}


class Tok:
    __slots__ = ("kind", "val", "line", "col", "nl", "pos", "epos")
    def __init__(self, kind, val, line, col):
        self.kind, self.val, self.line, self.col, self.nl = kind, val, line, col, False
        self.pos = self.epos = None     # 源码中的绝对偏移（kite fmt --migrate 用）
    def __repr__(self):
        return f"Tok({self.kind},{self.val!r}@{self.line}:{self.col})"


FMT_SPEC_RE = re.compile(r"(?:(?P<fill>.)?(?P<align>[<>^]))?(?P<zero>0)?(?P<width>[0-9]+)?(?:\.(?P<prec>[0-9]+))?(?P<ty>[xXobe?]?)", re.S)


def split_fmt_spec(src):
    """把插值 {expr:spec} 拆成 (expr, spec)；冒号必须在括号外、字符串和字符外。没有说明时 spec 为 None"""
    depth, i, n = 0, 0, len(src)
    while i < n:
        c = src[i]
        if c in "([{": depth += 1
        elif c in ")]}": depth -= 1
        elif c == '"':
            i += 1
            while i < n and src[i] != '"':
                i += 2 if src[i] == "\\" else 1
        elif c == "'" and i + 2 < n and (src[i + 1] == "\\" or src[i + 2] == "'"):
            j = src.find("'", i + 2 if src[i + 1] != "\\" else i + 3)
            i = j if j > 0 else i
        elif c == ":" and depth == 0:
            return src[:i], src[i + 1:]
        i += 1
    return src, None


def fmt_value(v, spec):
    """格式说明的运行时实现（与 runtime/kite_rt.c 的 b_fmt 一致）"""
    m = FMT_SPEC_RE.fullmatch(spec)
    if m is None: raise KiteError(f"无效的格式说明 {{:{spec}}}")
    fill, align, zero, width, prec, ty = (m.group("fill") or " ", m.group("align"), m.group("zero"),
                                          int(m.group("width") or 0), m.group("prec"), m.group("ty"))
    num = type(v) in (int, float)
    if ty == "?":
        s = show(v, True)
    elif ty in ("x", "X", "o", "b"):
        if type(v) is not int: raise KiteError(f"格式 {{:{spec}}} 需要 Int，实际是 {type_name(v)}")
        s = ("-" if v < 0 else "") + format(abs(v), ty)
    elif ty == "e":
        if not num: raise KiteError(f"格式 {{:{spec}}} 需要数值，实际是 {type_name(v)}")
        s = format(float(v), f".{int(prec) if prec else 6}e")
    elif prec is not None:
        if num: s = format(float(v), f".{int(prec)}f")
        elif type(v) is str: s = v[:int(prec)]
        else: raise KiteError(f"格式 {{:{spec}}} 的精度只能用于数值或 Str，实际是 {type_name(v)}")
    else:
        s = show(v)
    pad = width - len(s)
    if pad <= 0: return s
    if zero and num and not align:
        sign = s[0] if s[:1] in "-+" else ""
        return sign + "0" * pad + s[len(sign):]
    align = align or (">" if num else "<")
    if align == "<": return s + fill * pad
    if align == ">": return fill * pad + s
    return fill * (pad // 2) + s + fill * (pad - pad // 2)


def lex(src):
    toks, i, line, col, n = [], 0, 1, 1, len(src)
    saw_nl = False

    def add(kind, val, l, c):
        nonlocal saw_nl
        t = Tok(kind, val, l, c)
        t.pos = i
        t.nl = saw_nl
        saw_nl = False
        toks.append(t)

    while i < n:
        if toks and toks[-1].epos is None: toks[-1].epos = i
        ch = src[i]
        if ch == "\n":
            saw_nl = True; i += 1; line += 1; col = 1; continue
        if ch in " \t\r":
            i += 1; col += 1; continue
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise KiteError("块注释未闭合", line, col)
            chunk = src[i:j + 2]
            line += chunk.count("\n")
            if "\n" in chunk:
                saw_nl = True
            i = j + 2
            continue
        sl, sc = line, col
        if ch.isdigit():
            j = i
            after_dot = bool(toks) and toks[-1].kind == "OP" and toks[-1].val == "." and not saw_nl
            base = {"x": 16, "o": 8, "b": 2}.get(src[i + 1] if ch == "0" and i + 1 < n else "")
            if base:                                   # 0xff 0o17 0b1010
                j = i + 2
                digs = "0123456789abcdefABCDEF"[:base + (6 if base == 16 else 0)]
                while j < n and (src[j] in digs or src[j] == "_"):
                    j += 1
                text = src[i + 2:j].replace("_", "")
                if not text or j < n and (src[j].isalnum()):
                    raise KiteError(f"非法的数字字面量 {src[i:j + 1]!r}", sl, sc)
                add("INT", int(text, base), sl, sc)
                col += j - i; i = j; continue
            while j < n and (src[j].isdigit() or src[j] == "_"):
                j += 1
            is_float = False
            if j + 1 < n and src[j] == "." and src[j + 1].isdigit() and not after_dot:
                is_float = True
                j += 1
                while j < n and (src[j].isdigit() or src[j] == "_"):
                    j += 1
            if not after_dot and j < n and src[j] in "eE":   # 指数 1e9 2.5E-3
                k = j + 1
                if k < n and src[k] in "+-": k += 1
                if k < n and src[k].isdigit():
                    is_float = True; j = k
                    while j < n and (src[j].isdigit() or src[j] == "_"):
                        j += 1
            text = src[i:j].replace("_", "")
            add("FLOAT" if is_float else "INT", float(text) if is_float else int(text), sl, sc)
            col += j - i; i = j; continue
        if ch == "r" and i + 1 < n and src[i + 1] in '"#':   # 原始字符串 r"…" / r#"…"#
            k = i + 1
            while k < n and src[k] == "#": k += 1
            if k < n and src[k] == '"':
                hashes = k - i - 1
                end = src.find('"' + "#" * hashes, k + 1)
                if end < 0:
                    raise KiteError("原始字符串未闭合", sl, sc)
                body = src[k + 1:end]
                add("STR", [body], sl, sc)
                j = end + 1 + hashes
                line += body.count("\n")
                col = (j - src.rfind("\n", 0, j)) if "\n" in body else col + (j - i)
                i = j; continue
        if ch == "'" and i + 2 < n and (src[i + 1] == "\\" or src[i + 2] == "'") and src[i + 1] not in "\n'":   # 字符字面量 'a' '\n'
            if src[i + 1] == "\\":
                nx = src[i + 2]
                if nx == "u" and src.startswith("{", i + 3):
                    e = src.find("}", i + 4)
                    c = chr(int(src[i + 4:e], 16)); j = e + 1
                else:
                    c = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "0": "\0", "{": "{", "}": "}"}.get(nx)
                    if c is None: raise KiteError(f"未知的转义 \\{nx}", sl, sc)
                    j = i + 3
            else:
                c = src[i + 1]; j = i + 2
            if j >= n or src[j] != "'":
                raise KiteError("字符字面量只能包含一个字符", sl, sc)
            add("STR", [c], sl, sc)
            col += j + 1 - i; i = j + 1; continue
        if ch.isalpha() or ch == "_" or ord(ch) > 127 and ch.isidentifier():
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            add("KW" if word in KEYWORDS else "IDENT", word, sl, sc)
            col += j - i; i = j; continue
        if ch == "'" and i + 1 < n and (src[i + 1].isalpha() or src[i + 1] == "_"):   # 循环标签 'outer
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            if j < n and src[j] == "'":
                raise KiteError("字符字面量只能包含一个字符（字符串请用双引号）", sl, sc)
            add("LABEL", src[i + 1:j], sl, sc)
            col += j - i; i = j; continue
        if ch == '"':
            j, parts, buf = i + 1, [], []
            while True:
                if j >= n:
                    raise KiteError("字符串未闭合", sl, sc)
                c = src[j]
                if c == '"':
                    j += 1; break
                if c == "\\" and src.startswith("u{", j + 1):
                    e = src.find("}", j + 3)
                    buf.append(chr(int(src[j + 3:e], 16))); j = e + 1; continue
                if c == "\\":
                    nx = src[j + 1] if j + 1 < n else ""
                    buf.append({"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "{": "{", "}": "}", "0": "\0"}.get(nx, nx))
                    j += 2; continue
                if c == "{":
                    if src.startswith("{{", j):
                        buf.append("{"); j += 2; continue
                    depth, k = 1, j + 1
                    while k < n and depth:
                        if src[k] == "{": depth += 1
                        elif src[k] == "}": depth -= 1
                        k += 1
                    if depth:
                        raise KiteError("字符串插值未闭合", sl, sc)
                    if buf:
                        parts.append("".join(buf)); buf = []
                    parts.append(("expr", src[j + 1:k - 1], sl))
                    j = k; continue
                if c == "}" and src.startswith("}}", j):
                    buf.append("}"); j += 2; continue
                if c == "\n":
                    line += 1
                buf.append(c); j += 1
            if buf or not parts:
                parts.append("".join(buf))
            add("STR", parts, sl, sc)
            col += j - i; i = j; continue
        for op in OPS:
            if src.startswith(op, i):
                add("OP", op, sl, sc)
                i += len(op); col += len(op)
                break
        else:
            raise KiteError(f"无法识别的字符 {ch!r}", line, col)
    if toks and toks[-1].epos is None: toks[-1].epos = i
    toks.append(Tok("EOF", None, line, col))

    # 插入 NEWLINE（语句分隔），并处理续行规则
    out, stack = [], []
    for t in toks:
        if t.nl and out:
            prev = out[-1]
            top = stack[-1] if stack else "{"
            pv = prev.val if prev.kind in ("OP", "KW") else None
            nv = t.val if t.kind in ("OP", "KW") else None
            if pv in (">", ">>") and _closes_typeapp(out): pv = None    # 行尾的 f.<T> 不是续行
            if pv in (">", ">>") and _alias_line_end(out): pv = None    # type A<T> = List<T> 行尾的 > 不是续行
            if pv == "*" and len(out) >= 2 and out[-2].kind == "OP" and out[-2].val == ".": pv = None   # use m.* 不是续行
            if top == "{" and pv not in CONT_END and nv not in CONT_START and prev.kind != "NEWLINE":
                out.append(Tok("NEWLINE", None, prev.line, prev.col))
        if t.kind == "OP":
            if t.val in "([{":
                stack.append(t.val)
            elif t.val in ")]}" and stack:
                stack.pop()
        out.append(t)
    return out

def _alias_line_end(out):
    """当前行是 type 别名声明，且 = 之后的 < > 已配平"""
    j = len(out) - 1
    while j >= 0 and out[j].kind != "NEWLINE" and not (out[j].kind == "OP" and out[j].val in ("{", "}", ";")):
        j -= 1
    line = out[j + 1:]
    k = 0
    while k < len(line) and (line[k].kind == "KW" and line[k].val == "pub" or
                             line[k].kind == "OP" and line[k].val in ("(", ")") or
                             line[k].kind == "IDENT" and line[k].val in ("pkg", "mod") and k > 0):
        k += 1
    if k >= len(line) or not (line[k].kind == "KW" and line[k].val == "type"): return False
    eq = next((i for i, t in enumerate(line) if t.kind == "OP" and t.val == "="), None)
    if eq is None: return False
    d = 0
    for t in line[eq + 1:]:
        if t.kind != "OP": continue
        if t.val == "<": d += 1
        elif t.val == ">": d -= 1
        elif t.val == ">>": d -= 2
    return d == 0


def _closes_typeapp(out):
    """out 末尾的 > / >> 是否正好闭合一个 .<…>"""
    depth, j = 0, len(out) - 1
    while j >= 0:
        v = out[j].val if out[j].kind == "OP" else None
        if v == ">": depth += 1
        elif v == ">>": depth += 2
        elif v == "<":
            depth -= 1
            if depth == 0: return j > 0 and out[j - 1].kind == "OP" and out[j - 1].val == "."
            if depth < 0: return False
        elif out[j].kind == "NEWLINE" or v in ("{", "}", "=", ";"): return False
        j -= 1
    return False


# ───────────────────────── AST ─────────────────────────
class Node:
    def __init__(self, kind, line, **kw):
        self.kind, self.line = kind, line
        self.__dict__.update(kw)
    def __repr__(self):
        return f"{self.kind}({ {k: v for k, v in self.__dict__.items() if k not in ('kind', 'line')} })"

def writes_any(node):
    """表达式里是否可能给变量赋值（含块、lambda 里的赋值）；原地追加的优化只在不会时启用"""
    st = [node]
    while st:
        n = st.pop()
        if isinstance(n, Node):
            if n.kind == "Assign": return True
            st.extend(v for k, v in n.__dict__.items() if k not in ("kind", "line"))
        elif isinstance(n, (list, tuple)):
            st.extend(n)
        elif isinstance(n, dict):
            st.extend(n.values())
    return False


def erase_typeapps(root):
    """去掉还留在树里的 .<T>（类型检查会就地改写；--no-check 时由这里兜底）"""
    seen, stack = set(), [root]
    while stack:
        x = stack.pop()
        if isinstance(x, Node):
            if id(x) in seen: continue
            seen.add(id(x))
            while x.kind == "TypeApp":
                f = x.fn; x.__dict__.clear(); x.__dict__.update(f.__dict__)
            stack.extend(v for k, v in x.__dict__.items() if k != "_t")
        elif isinstance(x, (list, tuple)):
            stack.extend(x)
        elif isinstance(x, dict):
            stack.extend(x.values())


# ───────────────────────── 语法分析 ─────────────────────────
def _pat_names(p):
    k = p[0]
    if k == "bind": return [p[1]]
    if k == "tuple": return [n for x in p[1] for n in _pat_names(x)]
    if k == "list": return [n for x in p[1] for n in _pat_names(x)] + ([p[2]] if p[2] and p[2] != "_" else []) + \
        [n for x in (p[3] if len(p) > 3 else []) for n in _pat_names(x)]
    if k == "variant" and p[2]: return [n for x in p[2] for n in _pat_names(x)]
    if k == "struct": return [n for _, x in p[2] for n in _pat_names(x)]
    if k == "or": return _pat_names(p[1][0])
    if k == "at": return _pat_names(p[1]) + _pat_names(p[2])
    return []

def use_name(e):
    """use 分组里的一项：Name 或 (Name, 别名) → (原名, 绑定名)"""
    return e if isinstance(e, tuple) else (e, e)


def std_use_entries(it):
    """内建 std 模块的 use 规范化为 ("mod", 模块, 绑定名) / ("member", 模块, 成员, 绑定名) / ("all", 模块)"""
    p = it.path; alias = getattr(it, "alias", None); out = []
    if isinstance(p[1], list):
        for e in p[1]:
            m, b = use_name(e); out.append(("mod", m, b))
    elif len(p) == 2:
        out.append(("mod", p[1], alias or p[1]))
    elif p[2] == "*":
        out.append(("all", p[1]))
    else:
        for e in (p[2] if isinstance(p[2], list) else [(p[2], alias or p[2])]):
            nm, b = use_name(e); out.append(("member", p[1], nm, b))
    return out


def range_ok(p, v):
    """区间模式 (range, lo, hi, inc)：lo / hi 可为 None（开区间）；数值或字符串（按码点比较）"""
    lo, hi, inc = p[1], p[2], p[3]
    probe = lo if lo is not None else hi
    if isinstance(probe, str):
        if not isinstance(v, str): return False
    elif not isinstance(v, (int, float)) or isinstance(v, bool): return False
    if lo is not None and v < lo: return False
    if hi is not None and not (v <= hi if inc else v < hi): return False
    return True


def _unit_tail(block):
    """块末尾补一个 ()，让块的值是 Unit（if let 无 else、while let 的循环体）"""
    return Node("Block", block.line, stmts=block.stmts + [Node("ExprStmt", block.line, expr=Node("Tuple", block.line, items=[]))])

class Parser:
    def __init__(self, toks, legacy=None):
        self.t, self.i = toks, 0
        self.legacy = PARSE_LEGACY if legacy is None else legacy         # 0.48 及以前的优先级（kite fmt --migrate 用），同时记录需要加括号的位置
        self.mig = []                 # [(起始偏移, 结束偏移)]：迁移时给这段源码加括号

    # 工具
    @property
    def cur(self): return self.t[self.i]
    def peek(self, k=1): return self.t[min(self.i + k, len(self.t) - 1)]
    def at(self, kind, val=None, tok=None):
        tok = tok or self.cur
        return tok.kind == kind and (val is None or tok.val == val)
    def at_op(self, v): return self.at("OP", v)
    def at_kw(self, v): return self.at("KW", v)
    def adv(self):
        tok = self.cur; self.i += 1; return tok
    def expect(self, kind, val=None):
        if not self.at(kind, val):
            want = val or kind
            got = self.cur.val if self.cur.val is not None else self.cur.kind
            raise KiteError(f"语法错误: 期望 {want!r}，实际是 {got!r}", self.cur.line, self.cur.col)
        return self.adv()
    def expect_ident(self):
        if self.at("IDENT") or self.at_kw("self") or self.at_kw("Self"):
            return self.adv().val
        raise KiteError(f"语法错误: 期望标识符，实际是 {self.cur.val!r}", self.cur.line, self.cur.col)
    def skip_nl(self):
        while self.at("NEWLINE") or self.at_op(";"):
            self.adv()
    def end_stmt(self):
        if self.at("NEWLINE") or self.at_op(";"):
            self.skip_nl(); return
        if self.at_op("}") or self.at("EOF"):
            return
        raise KiteError(f"语法错误: 语句后出现意外的 {self.cur.val!r}", self.cur.line, self.cur.col)

    # 程序
    def program(self):
        items = []
        self.skip_nl()
        while not self.at("EOF"):
            it = self.item(top=True)
            if it.kind == "Extern":       # extern 块展开成若干无函数体的 Fn（带 .extern 标记）
                items.extend(it.fns)
            else:
                items.append(it)
            self.end_stmt()
        return items

    KNOWN_ATTRS = {"test", "deprecated", "inline", "cold", "allow", "doc", "link"}

    EXTERN_TYPES = ("Int", "Float", "Bool", "Str")

    def extern_block(self):
        """extern ["C"] { fn 签名 … }：调用 C 函数（标量与字符串），调用处必须在 unsafe { … } 里"""
        ln = self.adv().line
        if self.at("STR"):
            t = self.adv(); abi = "".join(p for p in t.val if isinstance(p, str))
            if abi != "C":
                raise KiteError(f"语法错误: 只支持 extern \"C\"，不支持 {abi!r}", t.line, t.col)
        self.expect("OP", "{"); self.skip_nl(); fns = []
        while not self.at_op("}"):
            ats = self.attrs()
            pub = False
            if self.at_kw("pub"):
                self.adv(); pub = True
            if not (self.at_kw("fn") or self.at_kw("unsafe") and self.at("KW", "fn", self.peek())):
                raise KiteError("语法错误: extern 块里只能写 fn 签名", self.cur.line, self.cur.col)
            self._in_extern = True
            try: f = self.fn_decl()
            finally: self._in_extern = False
            if f.body is not None:
                raise KiteError(f"语法错误: extern fn {f.name} 不能有函数体", f.line)
            if f.generics:
                raise KiteError(f"语法错误: extern fn {f.name} 不能有泛型参数", f.line)
            for a in ats:
                if a[0] not in ("doc", "allow", "deprecated"):
                    raise KiteError(f"语法错误: @{a[0]} 不能用在 extern fn 上", a[2])
            f.pub, f.attrs, f.extern, f.cname = pub, ats, [], f.name
            fns.append(f)
            self.end_stmt()
        self.adv()
        return Node("Extern", ln, fns=fns)

    def attrs(self):
        """@name[.name…][(参数, …)]，可以连写多个，之间可以换行"""
        out = []
        while self.at_op("@"):
            at = self.adv(); name = self.expect_ident()
            while self.at_op("."):
                self.adv(); name += "." + self.expect_ident()
            if name not in self.KNOWN_ATTRS:
                raise KiteError(f"语法错误: 未知属性 @{name}（支持: {', '.join('@' + a for a in sorted(self.KNOWN_ATTRS))}）", at.line, at.col)
            args = []
            if self.at_op("("):
                self.adv()
                while not self.at_op(")"):
                    if self.at("IDENT") and self.peek().kind == "OP" and self.peek().val == ":":
                        k = self.adv().val; self.adv(); args.append((k, self.attr_lit()))
                    elif self.at("IDENT"):
                        args.append((self.adv().val, None))
                    else:
                        args.append((None, self.attr_lit()))
                    if self.at_op(","): self.adv()
                    elif not self.at_op(")"): self.expect("OP", ")")
                self.adv()
            out.append((name, args, at.line))
            self.skip_nl()
        return out

    def attr_lit(self):
        t = self.cur
        if t.kind in ("INT", "FLOAT") or t.kind == "KW" and t.val in ("true", "false"):
            self.adv(); return t.val if t.kind != "KW" else t.val == "true"
        if t.kind == "STR" and all(isinstance(p, str) for p in t.val):
            self.adv(); return "".join(t.val)
        raise KiteError("语法错误: 属性参数只能是字面量（整数、浮点、字符串、true/false）或名字", t.line, t.col)

    def item(self, top=False):
        ats = self.attrs()
        node = self.item0(top)
        if node.kind == "Extern":
            libs = []
            for a in ats:
                if a[0] == "link":
                    libs += [v for _, v in a[1] if isinstance(v, str)]
                elif a[0] not in ("doc", "allow"):
                    raise KiteError(f"语法错误: extern 块前只能用 @link / @doc / @allow，不能用 @{a[0]}", a[2])
            for f in node.fns:
                f.extern = libs
                f.pub = f.pub or node.pub
            return node
        if any(a[0] == "link" for a in ats):
            raise KiteError("语法错误: @link 只能用在 extern 块上", ats[0][2])
        if ats:
            if node.kind not in ("Fn", "Struct", "Enum", "Trait", "Impl", "Alias") and \
                    not (node.kind == "Let" and getattr(node, "is_const", False)):
                raise KiteError("语法错误: 属性只能放在声明（fn type enum trait impl const）前面", ats[0][2])
            for a in ats:
                if a[0] in ("test", "inline", "cold") and node.kind != "Fn":
                    raise KiteError(f"语法错误: @{a[0]} 只能用在 fn 上", a[2])
            node.attrs = ats
        return node

    def item0(self, top=False):
        self._top = top
        pub = False
        vis = None
        if self.at_kw("pub"):
            self.adv(); pub = True; vis = "pub"
            if self.at_op("("):  # pub(pkg) / pub(mod)
                self.adv(); t = self.cur
                if self.at_kw("mod"): self.adv(); vis = "mod"
                else:
                    w = self.expect_ident()
                    if w != "pkg":
                        raise KiteError(f"语法错误: 可见性只能是 pub、pub(pkg) 或 pub(mod)，不能写 pub({w})", t.line, t.col)
                    vis = "pkg"
                self.expect("OP", ")")
        node = self.item1()
        node.pub = pub; node.vis = vis
        return node

    def item1(self):
        if self.at_kw("fn") or self.at_kw("async") or self.at_kw("unsafe") and self.at("KW", "fn", self.peek()):
            return self.fn_decl()
        if self.at_kw("type"): return self.type_decl()
        if self.at_kw("enum"): return self.enum_decl()
        if self.at_kw("trait"): return self.trait_decl()
        if self.at_kw("impl"): return self.impl_decl()
        if self.at_kw("use"): return self.use_decl()
        if self.at_kw("mod"):
            ln = self.adv().line
            local = not getattr(self, "_top", False) and not getattr(self, "_in_mod", 0)
            t = self.cur; parts = self.path()
            if not self.at_op("{"):
                if local:
                    raise KiteError("语法错误: 函数体里只能写内联 mod 块 mod m { … }", ln)
                return Node("Mod", ln, name=None, items=None)
            if len(parts) != 1 or not isinstance(parts[0], str):
                raise KiteError("语法错误: 内联 mod 块的名字只能是一个标识符", t.line, t.col)
            # 内联模块 mod m { … }：和单独文件里的模块一样，外面用 m.名字 访问其 pub 项
            self.adv(); self.skip_nl(); items = []
            saved_top = getattr(self, "_top", False)
            self._in_mod = getattr(self, "_in_mod", 0) + 1
            try:
                while not self.at_op("}"):
                    if self.at("EOF"):
                        raise KiteError(f"语法错误: mod {parts[0]} 缺少 }}", ln)
                    self._top = True
                    it = self.item(top=True)
                    if it.kind == "Extern": items.extend(it.fns)
                    else: items.append(it)
                    self.end_stmt()
            finally:
                self._in_mod -= 1; self._top = saved_top
            self.adv()
            return Node("Mod", ln, name=parts[0], items=items)
        if self.at_kw("extern"):
            if not getattr(self, "_top", False):
                raise KiteError("语法错误: extern 块只能写在顶层", self.cur.line, self.cur.col)
            return self.extern_block()
        if self.at_kw("const") or ALLOW_TOP_STMTS or not getattr(self, "_top", False):
            return self.stmt()
        t = self.cur
        self._top = False
        if self.legacy:
            if t.kind == "KW" and t.val in ("let", "var"):
                self.top_lets = getattr(self, "top_lets", []) + [t.line]
            return self.stmt()
        if t.kind == "KW" and t.val in ("let", "var"):
            raise KiteError(f"语法错误: 顶层不能用 {t.val}；全局常量请写 const NAME: T = …，可变状态请放进函数里", t.line, t.col)
        raise KiteError("语法错误: 顶层只允许声明（fn type enum trait impl use mod const），语句请放进 fn main", t.line, t.col)

    def path(self, use=False):
        parts = [self.expect_ident()]
        while self.at_op("."):
            self.adv()
            if self.at_op("{"):
                self.adv(); names = []; self.skip_nl()
                while not self.at_op("}"):
                    nm = self.expect_ident()
                    if use and self.at_kw("as"):
                        self.adv(); nm = (nm, self.expect_ident())
                    names.append(nm); self.skip_nl()
                    if self.at_op(","): self.adv(); self.skip_nl()
                self.expect("OP", "}")
                parts.append(names)
                break
            if use and self.at_op("*"):
                self.adv(); parts.append("*"); break
            parts.append(self.expect_ident())
        return parts

    def use_decl(self):
        ln = self.adv().line
        path = self.path(use=True)
        alias = None
        if self.at_kw("as"):
            t = self.adv()
            if isinstance(path[-1], list) or path[-1] == "*":
                raise KiteError("语法错误: 分组或通配导入不能整体改名（在 { } 里逐项写 as）", t.line, t.col)
            alias = self.expect_ident()
        return Node("Use", ln, path=path, alias=alias)

    def at_gt(self):
        """类型参数列表的 >：把 List<List<Int>> 里的 >> / >>= 拆开"""
        t = self.cur
        if t.kind == "OP" and t.val in (">>", ">>="):
            a = Tok("OP", ">", t.line, t.col); a.nl = t.nl
            b = Tok("OP", t.val[1:], t.line, t.col + 1)
            self.t[self.i:self.i + 1] = [a, b]
        return self.at_op(">")

    def where_clause(self, gens, bounds):
        """where T: A + B, U: C —— 并进泛型约束（目前左边只能是泛型参数）"""
        if not self.at_kw("where"): return
        self.adv()
        while True:
            t = self.cur; name = self.expect_ident()
            if name not in gens:
                raise KiteError(f"语法错误: where 子句里的 {name} 不是泛型参数（目前只支持约束泛型参数）", t.line, t.col)
            self.expect("OP", ":")
            bs = [self.type_()[1]]
            while self.at_op("+"):
                self.adv(); bs.append(self.type_()[1])
            bounds[name] = bounds.get(name, []) + [b for b in bs if b not in bounds.get(name, [])]
            if not self.at_op(","): break
            self.adv(); self.skip_nl()
            if self.at_op("{") or self.at_op("="): break

    def generics(self):
        names = []
        self.last_bounds = {}; self.last_cgens = []
        if self.at_op("<"):
            self.adv()
            while not self.at_gt():
                if self.at_kw("const"):          # 常量泛型参数 const N: Int
                    self.adv(); t = self.cur; n = self.expect_ident(); self.expect("OP", ":")
                    ty = self.type_()
                    if not n[:1].isupper():
                        raise KiteError(f"语法错误: 常量泛型参数名必须以大写字母开头：{n}", t.line, t.col)
                    if ty[0] != "con" or ty[1] != "Int" or ty[2]:
                        raise KiteError(f"语法错误: 常量泛型参数 {n} 目前只支持 Int 类型", t.line, t.col)
                    names.append(n); self.last_cgens.append(n)
                    if self.at_op(","): self.adv()
                    continue
                names.append(self.expect_ident())
                if self.at_op(":"):
                    self.adv(); bs = [self.type_()[1]]
                    while self.at_op("+"):
                        self.adv(); bs.append(self.type_()[1])
                    self.last_bounds[names[-1]] = bs
                if self.at_op(","): self.adv()
            self.adv()
        return names

    def type_(self):
        """类型 AST: ("con", 名字, [参数]) | ("tuple", [..]) | ("fn", [..], 返回) | ("array", t, n)"""
        line = self.cur.line
        if self.at_op("("):
            self.adv(); ts = []
            while not self.at_op(")"):
                ts.append(self.type_())
                if self.at_op(","): self.adv()
            self.adv()
            t = ("con", "Unit", [], line) if not ts else ("tuple", ts, line)
        elif self.at_op("["):
            self.adv(); inner = self.type_(); n = None
            if self.at_op(";"):
                self.adv()
                if self.at("INT"):
                    v = self.adv().val
                    if v < 0: raise KiteError("语法错误: 数组长度不能是负数", line)
                    n = ("con", f"#{v}", [], line)
                else:
                    n = ("con", self.expect_ident(), [], line)
            self.expect("OP", "]")
            t = ("con", "List", [inner], line) if n is None else ("con", "Array", [inner, n], line)
        elif self.at_kw("dyn") or self.at_kw("impl"):     # dyn Show + Eq / impl Show
            kind = self.adv().val; bs = [self.expect_ident()]
            while self.at_op("+"):
                self.adv(); bs.append(self.expect_ident())
            t = (kind, bs, line)
        elif self.at_kw("unsafe") and self.peek().kind == "KW" and self.peek().val == "fn":
            self.adv(); inner = self.type_()        # unsafe fn(…) -> T：只能在 unsafe 里调用的函数值
            return ("unsafe", inner, line)
        elif self.at_kw("fn"):
            self.adv(); self.expect("OP", "("); ts = []
            while not self.at_op(")"):
                ts.append(self.type_())
                if self.at_op(","): self.adv()
            self.adv(); r = ("con", "Unit", [], line)
            if self.at_op("->"):
                self.adv(); r = self.type_()
            t = ("fn", ts, r, line)
        elif self.at("INT"):          # 类型位置的整数：常量泛型实参 Vec<3> / f.<3>()
            t = ("con", f"#{self.adv().val}", [], line)
        else:
            name = self.expect_ident()
            while self.at_op("."):
                self.adv(); name += "." + self.expect_ident()
            args = []
            if self.at_op("<"):
                self.adv()
                while not self.at_gt():
                    args.append(self.type_())
                    if self.at_op(","): self.adv()
                self.adv()
            t = ("con", name, args, line)
        while self.at_op("?"):
            self.adv(); t = ("con", "Option", [t], line)
        return t

    def params(self):
        self.expect("OP", "("); ps = []; self._va = False
        while not self.at_op(")"):
            if self.at_op("..") and self.at("OP", ".", self.peek()):
                # extern 的可变参数 …：必须是最后一项
                t = self.adv(); self.adv(); self._va = True
                if self.at_op(","): self.adv()
                if not self.at_op(")"):
                    raise KiteError("语法错误: ... 只能是最后一个参数", t.line, t.col)
                break
            if self.at_kw("var"): self.adv()
            name = self.expect_ident(); ty = None
            if self.at_op(":"):
                self.adv(); ty = self.type_()
            ps.append((name, ty))
            if self.at_op(","): self.adv()
            elif not self.at_op(")"):
                self.expect("OP", ",")
        self.adv()
        return ps

    def fn_decl(self):
        is_async = is_unsafe = False
        if self.at_kw("async"):
            self.adv(); is_async = True
        if self.at_kw("unsafe"):
            self.adv(); is_unsafe = True
        ln = self.expect("KW", "fn").line
        name = self.expect_ident()
        gens = self.generics(); bounds = self.last_bounds; cgens = list(self.last_cgens)
        ps = self.params(); ret = None; va = self._va
        if va and not getattr(self, "_in_extern", False):
            raise KiteError(f"语法错误: 只有 extern fn 可以有可变参数 ...", ln)
        if self.at_op("->"):
            self.adv(); ret = self.type_()
        bounds = dict(bounds); self.where_clause(gens, bounds)
        ps, ret, gens = _desugar_impl_types(ps, ret, gens, bounds)
        saved, self._async = getattr(self, "_async", False), is_async
        try: body = self.block() if self.at_op("{") else None
        finally: self._async = saved
        if is_async and name != "main":
            # async fn f(…) -> T { body }  ⇒  fn f(…) -> Future<T> { Future { run: () => body } }
            # Future 是惰性的：调用只是打包，.await 时才执行（spawn { … }.await 让多个 Future 并行）
            inner = ret if ret is not None else ("con", "_", [], ln)
            ret = ("con", "Future", [inner], ln)
            if body is not None:
                lam = Node("Lambda", ln, params=[], body=body); lam.ctx = f"async fn {name} 的返回值"
                body = Node("Block", ln, stmts=[Node("ExprStmt", ln, expr=Node(
                    "StructLit", ln, name="Future", fields=[("run", lam)], base=None))])
        node = Node("Fn", ln, name=name, params=ps, ret=ret, body=body, generics=gens, bounds=bounds)
        node.is_async, node.is_unsafe = is_async, is_unsafe; node.cgens = cgens
        if va: node.varargs = True
        return node

    def fields(self, close):
        fs = []
        self.skip_nl()
        while not self.at_op(close):
            self.attrs()
            if self.at_kw("pub"): self.adv()
            name = self.expect_ident(); ty = None
            if self.at_op(":"):
                self.adv(); ty = self.type_()
            fs.append((name, ty))
            if self.at_op(","): self.adv()
            self.skip_nl()
        self.adv()
        return fs

    def type_decl(self):
        ln = self.expect("KW", "type").line
        name = self.expect_ident(); gens = self.generics(); cgens = list(self.last_cgens)
        bounds = dict(self.last_bounds); self.where_clause(gens, bounds)
        self.expect("OP", "=")
        if self.at_op("{"):
            self.adv()
            return Node("Struct", ln, name=name, fields=self.fields("}"), generics=gens, bounds=bounds, cgens=cgens)
        return Node("Alias", ln, name=name, target=self.type_(), generics=gens, bounds=bounds, cgens=cgens)

    def enum_decl(self):
        ln = self.expect("KW", "enum").line
        name = self.expect_ident(); gens = self.generics(); cgens = list(self.last_cgens)
        bounds = dict(self.last_bounds); self.where_clause(gens, bounds)
        self.expect("OP", "{"); self.skip_nl(); variants = []
        while not self.at_op("}"):
            self.attrs()
            vname = self.expect_ident(); fs = None
            if self.at_op("{"):             # 记录变体 Rect { w: Float, h: Float }
                self.adv(); self.skip_nl(); fs = RecFields()
                while not self.at_op("}"):
                    self.attrs()
                    ft = self.cur; fname = self.expect_ident(); self.expect("OP", ":")
                    if any(f == fname for f, _ in fs):
                        raise KiteError(f"语法错误: 变体 {vname} 的字段 {fname} 重复", ft.line, ft.col)
                    fs.append((fname, self.type_()))
                    if self.at_op(","): self.adv()
                    self.skip_nl()
                self.adv()
                if not fs:
                    raise KiteError(f"语法错误: 记录变体 {vname} 至少要有一个字段（无字段请直接写 {vname}）", ln)
            elif self.at_op("("):
                self.adv(); fs = []
                while not self.at_op(")"):
                    if self.at("IDENT") and self.peek().kind == "OP" and self.peek().val == ":":
                        fname = self.adv().val; self.adv(); fs.append((fname, self.type_()))
                    else:
                        fs.append((f"_{len(fs)}", self.type_()))
                    if self.at_op(","): self.adv()
                self.adv()
            variants.append((vname, fs))
            if self.at_op(","): self.adv()
            self.skip_nl()
        self.adv()
        return Node("Enum", ln, name=name, variants=variants, generics=gens, bounds=bounds, cgens=cgens)

    def trait_decl(self):
        ln = self.expect("KW", "trait").line
        name = self.expect_ident(); gens = self.generics(); supers = []
        if self.at_op(":"):        # 超 trait：trait Ord2: Eq + Show
            self.adv(); supers.append(self.expect_ident())
            while self.at_op("+"):
                self.adv(); supers.append(self.expect_ident())
        self.expect("OP", "{"); self.skip_nl(); methods = []; assoc = []; consts = []
        while not self.at_op("}"):
            ats = self.attrs()
            if self.at_kw("type"):            # 关联类型 type Item [: Show + Eq] [= 默认类型]
                at = self.adv(); an = self.expect_ident(); bs = []; dflt = None
                if self.at_op(":"):
                    self.adv(); bs.append(self.expect_ident())
                    while self.at_op("+"):
                        self.adv(); bs.append(self.expect_ident())
                if self.at_op("="):
                    self.adv(); dflt = self.type_()
                if any(a[0] == an for a in assoc):
                    raise KiteError(f"语法错误: 关联类型 {an} 重复", at.line, at.col)
                assoc.append((an, bs, dflt))
            elif self.at_kw("const"):
                consts.append(self.assoc_const(consts, need_value=False))
            else:
                methods.append(self.fn_decl()); methods[-1].attrs = ats
            self.skip_nl()
        self.adv()
        return Node("Trait", ln, name=name, methods=methods, assoc=assoc, supers=supers, consts=consts)

    def assoc_const(self, consts, need_value):
        """trait / impl 里的关联常量 const NAME: T [= expr] → (名字, 类型, 表达式或 None, 行号)"""
        t = self.expect("KW", "const"); n = self.cur; name = self.expect_ident()
        if not name[:1].isupper():
            raise KiteError(f"语法错误: 常量名必须以大写字母开头：{name}", n.line, n.col)
        if any(c[0] == name for c in consts):
            raise KiteError(f"语法错误: 关联常量 {name} 重复", n.line, n.col)
        self.expect("OP", ":"); ty = self.type_(); val = None
        if self.at_op("="):
            self.adv(); self.skip_nl(); val = self.expr()
        elif need_value:
            raise KiteError(f"语法错误: impl 里的关联常量 {name} 必须有初值", n.line, n.col)
        return (name, ty, val, t.line)

    def impl_decl(self):
        ln = self.expect("KW", "impl").line
        gens = self.generics(); ibounds = self.last_bounds; icgens = list(self.last_cgens)
        first = self.type_(); trait = None; target_t = first
        if self.at_kw("for"):
            self.adv(); trait = first[1]; target_t = self.type_()
        target = target_t[1] if target_t[0] == "con" else "Tuple"
        ibounds = dict(ibounds); self.where_clause(gens, ibounds)
        self.expect("OP", "{"); self.skip_nl(); methods = []; assoc = []; consts = []
        saved_tgt = getattr(self, "impl_target", None)
        self.impl_target = target if target_t[0] == "con" else None
        while not self.at_op("}"):
            ats = self.attrs()
            if self.at_kw("pub"): self.adv()
            if self.at_kw("type"):            # type Item = Int
                at = self.adv(); an = self.expect_ident(); self.expect("OP", "=")
                if any(a[0] == an for a in assoc):
                    raise KiteError(f"语法错误: 关联类型 {an} 重复", at.line, at.col)
                assoc.append((an, self.type_()))
            elif self.at_kw("const"):
                consts.append(self.assoc_const(consts, need_value=True))
            else:
                methods.append(self.fn_decl()); methods[-1].attrs = ats
            self.skip_nl()
        self.adv(); self.impl_target = saved_tgt
        return Node("Impl", ln, trait=trait, target=target, target_t=target_t, methods=methods, generics=gens, bounds=ibounds,
                    assoc=assoc, consts=consts, cgens=icgens)

    # 语句
    def stmt(self, label=None):
        tok = self.cur
        if self.at_kw("const"):          # const NAME: T = expr —— 不可变绑定，名字大写、类型必写
            self.adv(); nt = self.cur; name = self.expect_ident()
            if not name[:1].isupper():
                raise KiteError(f"语法错误: const 的名字必须以大写字母开头（{name}）", nt.line, nt.col)
            self.expect("OP", ":"); ty = self.type_()
            self.expect("OP", "="); value = self.expr()
            return Node("Let", tok.line, pat=("bind", name), mutable=False, ty=ty, value=value, is_const=True)
        if self.at_kw("let") or self.at_kw("var"):
            mutable = self.adv().val == "var"
            pat = self.pattern(); ty = None
            if self.at_op(":"):
                self.adv(); ty = self.type_()
            self.expect("OP", "=")
            value = self.expr()
            if self.at_kw("else"):   # let 模式 = e else { 必须跳出 }
                self.adv(); els = self.block()
                names = _pat_names(pat)
                ln = tok.line
                res = Node("Name", ln, name=names[0]) if len(names) == 1 else \
                    Node("Tuple", ln, items=[Node("Name", ln, name=n) for n in names])
                m = Node("Match", ln, subject=value, arms=[(pat, None, res), (("wild",), None, els)])
                m.let_else = els
                bpat = ("bind", names[0]) if len(names) == 1 else ("tuple", [("bind", n) for n in names])
                if not names: bpat = ("wild",)
                return Node("Let", ln, pat=bpat, mutable=mutable, ty=ty if len(names) == 1 else None, value=m)
            return Node("Let", tok.line, pat=pat, mutable=mutable, ty=ty, value=value)
        if self.at_kw("return"):
            self.adv()
            val = None if (self.at("NEWLINE") or self.at_op("}") or self.at_op(";")) else self.expr()
            return Node("Return", tok.line, value=val)
        if tok.kind == "LABEL":
            self.adv(); self.expect("OP", ":")
            if not (self.at_kw("while") or self.at_kw("for") or self.at_kw("loop")):
                raise KiteError(f"语法错误: 标签 '{tok.val} 后面必须是 while / for / loop", self.cur.line, self.cur.col)
            s = self.stmt(label=tok.val)
            s.label = tok.val
            if s.kind == "ExprStmt": s.expr.label = tok.val
            return s
        if self.at_kw("break") or self.at_kw("continue"):
            self.adv()
            lab = None
            if self.at("LABEL") and not self.cur.nl:
                lab = self.adv().val
            val = None
            if tok.val == "break" and not self.cur.nl and not (self.at("NEWLINE") or self.at("EOF") or self.at_op("}")
                                                                or self.at_op(";") or self.at_op(",") or self.at_op(")")):
                val = self.expr()
                # 带值的 break 标记目标 loop，让它成为表达式
                for fr in reversed(getattr(self, "_loopfr", [])):
                    if lab is None or fr[0] == lab:
                        fr[1] = True; break
            return Node("Break" if tok.val == "break" else "Continue", tok.line, label=lab, value=val)
        if self.at_kw("while") and self.peek().kind == "KW" and self.peek().val == "let":
            self.adv(); self.adv(); pat = self.pattern(); self.expect("OP", "=")
            subj = self.expr(no_struct=True); body = _unit_tail(self.loop_block(label)); ln = tok.line
            m = Node("Match", ln, subject=subj, arms=[(pat, None, body),
                                                     (("wild",), None, Node("Block", ln, stmts=[Node("Break", ln)]))])
            return Node("While", ln, cond=None, body=Node("Block", ln, stmts=[Node("ExprStmt", ln, expr=m)]))
        if self.at_kw("while"):
            self.adv(); cond = self.expr(no_struct=True)
            return Node("While", tok.line, cond=cond, body=self.loop_block(label))
        if self.at_kw("loop"):
            self.adv()
            e = self.loop_expr(tok.line, label)
            if e.kind == "Loop": return Node("ExprStmt", tok.line, expr=e)
            return e
        if self.at_kw("for"):
            self.adv(); pat = self.pattern(); self.expect("KW", "in")
            it = self.expr(no_struct=True)
            return Node("For", tok.line, pat=pat, iter=it, body=self.loop_block(label))
        if self.at_kw("defer"):
            self.adv(); return Node("Defer", tok.line, body=self.block() if self.at_op("{") else self.expr())
        if self._is_fn_decl():
            return self.fn_decl()
        e = self.expr()
        if self.at("OP") and self.cur.val in ("=", "+=", "-=", "*=", "/=", "%=", "|=", "&=", "^=", "<<=", ">>="):
            op = self.adv().val
            if e.kind not in ("Name", "Field", "Index"):
                raise KiteError("赋值目标无效", tok.line, tok.col)
            return Node("Assign", tok.line, target=e, op=op, value=self.expr())
        return Node("ExprStmt", tok.line, expr=e)

    def block(self):
        ln = self.expect("OP", "{").line
        stmts = []
        self.skip_nl()
        while not self.at_op("}"):
            stmts.append(self.item())
            self.end_stmt()
        self.adv()
        return Node("Block", ln, stmts=stmts)

    # 表达式（优先级从低到高）
    def expr(self, no_struct=False):
        saved = getattr(self, "no_struct", False)
        self.no_struct = no_struct
        try:
            return self.legacy_pipe() if self.legacy else self.binary(0)
        finally:
            self.no_struct = saved

    def _wrap(self, node):
        """迁移：记录 node 对应的源码区间，之后给它加括号"""
        sp = getattr(node, "_sp", None)
        if sp and self.t[sp[0]].pos is not None and self.t[sp[1] - 1].epos is not None:
            self.mig.append((self.t[sp[0]].pos, self.t[sp[1] - 1].epos))

    def legacy_pipe(self):
        """0.48 的 |>：优先级最低，右侧是完整的二元表达式"""
        s = self.i
        left = self.binary(0)
        while self.at_op("|>"):
            ln = self.adv().line
            right = self.binary(0)
            if (left.kind == "Bin" and left.op in self.LOW_OPS) or left.kind in ("Is", "Match"):
                self._wrap(left)
            if right.kind in ("Bin", "Unary", "Cast", "Is", "Match"):
                self._wrap(right)
            left = Node("Pipe", ln, left=left, right=right)
            left._sp = (s, self.i)
        return left

    def is_conj(self, left, right, ln):
        """x is P && R：P 的绑定在 R 中可见 → match x { P => R, _ => false }"""
        if left.kind == "Is":
            m = Node("Match", ln, subject=left.value, arms=[(left.pat, None, right), (("wild",), None, Node("Lit", ln, value=False))])
            m.is_conj = (left, right)
            return m
        if left.kind == "Bin" and left.op == "&&":
            return Node("Bin", left.line, op="&&", left=left.left, right=self.is_conj(left.right, right, ln))
        return Node("Bin", ln, op="&&", left=left, right=right)

    # | & 是集合的并 / 交（优先级同 Rust：高于比较，低于区间和算术）
    # Int 上 | ^ & 是位运算，<< >> 是移位（优先级同 Rust：& 高于 ^ 高于 |，移位高于区间、低于加减）
    # 优先级（低→高，见 grammar.ebnf）：|| && is ==/!= 比较 |> 区间 | ^ & 移位 +- */% as 一元
    # ==/!= 与比较、区间都不可结合；|> 的右侧只能是后缀表达式（a |> f(b) ≡ f(a, b)）
    BIN = [["||"], ["&&"], ["==", "!="], ["<", ">", "<=", ">="], ["|>"], ["..", "..="], ["|"], ["^"], ["&"],
           ["<<", ">>"], ["+", "-"], ["*", "/", "%"]]
    RANGE_LVL, PIPE_LVL, NONASSOC = 5, 4, (2, 3, 5)
    # 0.48 的表：|> 在最低层单独处理，区间比 | ^ & 绑得紧
    OLD_BIN = [["||"], ["&&"], ["==", "!="], ["<", ">", "<=", ">="], ["|"], ["^"], ["&"], ["..", "..="], ["<<", ">>"],
               ["+", "-"], ["*", "/", "%"]]
    LOW_OPS = {"||", "&&", "==", "!=", "<", ">", "<=", ">="}
    HIGH_OPS = {"..", "..=", "|", "^", "&", "<<", ">>", "+", "-", "*", "/", "%"}

    def binary(self, lvl):
        tbl = self.OLD_BIN if self.legacy else self.BIN
        rlvl = 7 if self.legacy else self.RANGE_LVL
        s0 = self.i
        if lvl == len(tbl):
            e = self.unary()
            while self.at_kw("as"):          # x as Float：比 * / 绑得紧，比一元运算松
                ln = self.adv().line
                e = Node("Cast", ln, value=e, ty=self.type_())
            if self.legacy and not hasattr(e, "_sp"): e._sp = (s0, self.i)
            return e
        if not self.legacy and lvl == self.PIPE_LVL:
            left = self.binary(lvl + 1)
            while self.at_op("|>"):
                ln = self.adv().line
                right = self.postfix(self.primary())
                if self.at("OP") and self.cur.val in self.HIGH_OPS or self.at_kw("as"):
                    t = self.cur
                    raise KiteError(f"语法错误: 管道 |> 的右侧只能是函数或调用，不会吸收后面的 {t.val}；"
                                    f"请写成 (a |> f) {t.val} …", t.line, t.col)
                left = Node("Pipe", ln, left=left, right=right)
            return left
        left = self.binary(lvl + 1)
        while self.at("OP") and self.cur.val in tbl[lvl]:
            tok = self.adv()
            if lvl == rlvl and tok.val == ".." and self.at_op("]") and s0 == getattr(self, "_slice_start", -1):
                right = None   # xs[a..]：只在下标的最外层允许省略右端
            else:
                right = self.binary(lvl + 1)
            if self.legacy and tok.val in ("|", "^", "&"):
                for side in (left, right):     # 0.48 中 a | b..c 是 a | (b..c)，新优先级下要加括号保持原意
                    if side is not None and side.kind == "Bin" and side.op in ("..", "..="): self._wrap(side)
            if tok.val == "&&" and not self.legacy:
                left = self.is_conj(left, right, tok.line)
            else:
                left = Node("Bin", tok.line, op=tok.val, left=left, right=right)
            if lvl == rlvl:  # 区间不结合
                break
            if not self.legacy and lvl in self.NONASSOC and self.at("OP") and self.cur.val in tbl[lvl]:
                t = self.cur
                raise KiteError(f"语法错误: {tok.val} 与 {t.val} 不可连用（比较不可结合）：a < b < c 请写成 a < b && b < c",
                                t.line, t.col)
        if lvl == 2 and self.at_kw("is"):
            ln = self.adv().line
            left = Node("Is", ln, value=left, pat=self.pattern())
        if self.legacy and not hasattr(left, "_sp"): left._sp = (s0, self.i)
        return left

    def loop_block(self, label):
        """while / for 的循环体：也压一层，让 break 找对目标（它们不能带值，由类型检查报错）"""
        self._loopfr = getattr(self, "_loopfr", []) + [[label, False]]
        try: return self.block()
        finally: self._loopfr = self._loopfr[:-1]

    def loop_expr(self, ln, label=None, force=False):
        """loop { … }：循环体里有带值的 break 指向它时是表达式 Loop，否则就是普通的 While(cond=None)"""
        fr = [label, force]
        self._loopfr = getattr(self, "_loopfr", []) + [fr]
        try: body = self.block()
        finally: self._loopfr = self._loopfr[:-1]
        if fr[1]: return Node("Loop", ln, body=body, label=label)
        return Node("While", ln, cond=None, body=body)

    def slice_node(self, obj, lo, op, hi, ln):
        """xs[a..b] → __slice(xs, a, b, 模式)；模式 0 = 不含右端，1 = 含右端，2 = 到末尾"""
        mode = 2 if hi is None else (1 if op == "..=" else 0)
        lit = lambda v: Node("Lit", ln, value=v)
        return Node("Call", ln, fn=Node("Name", ln, name="__slice"),
                    args=[obj, lo if lo is not None else lit(0), hi if hi is not None else lit(0), lit(mode)])

    def unary(self):
        if self.at_op("-") or self.at_op("!") or self.at_op("~"):
            tok = self.adv()
            u = Node("Unary", tok.line, op=tok.val, operand=self.unary())
            u.pos = tok.pos
            return u
        return self.postfix(self.primary())

    def postfix(self, e):
        while True:
            if self.at_op("("):
                ln = self.cur.line
                e = Node("Call", ln, fn=e, args=self.args())
            elif self.at_op("."):
                ln = self.adv().line
                if self.at_kw("await"):
                    at = self.adv()
                    if e.kind not in ("Spawn", "Race") and not getattr(self, "_async", False) and not self.legacy:
                        raise KiteError("语法错误: .await 只能用在 async fn、async 块或 async 闭包里", at.line, at.col)
                    if e.kind in ("Spawn", "Race"):
                        e = Node("Await", ln, value=e)          # 组的每一项若是 Future，由类型检查器逐项 await
                    else:
                        e = Node("Call", ln, fn=Node("Name", ln, name="__await"), args=[e])
                    continue
                if self.at_op("<"):                  # 显式泛型实参 f.<Int>(…)
                    self.adv(); targs = []
                    while not self.at_gt():
                        self.skip_nl()
                        if self.at("IDENT") and self.cur.val == "_":
                            self.adv(); targs.append(None)
                        else:
                            targs.append(self.type_())
                        self.skip_nl()
                        if self.at_op(","): self.adv()
                        elif not self.at_gt(): self.expect("OP", ">")
                    self.adv()
                    if not targs:
                        raise KiteError("语法错误: .<> 里至少要有一个类型", ln)
                    e = Node("TypeApp", ln, fn=e, targs=targs); continue
                if self.at("INT"):
                    e = Node("TupleIndex", ln, obj=e, index=self.adv().val); continue
                if self.at("FLOAT"):  # t.0.1
                    a, b = str(self.adv().val).split(".")
                    e = Node("TupleIndex", ln, obj=Node("TupleIndex", ln, obj=e, index=int(a)), index=int(b)); continue
                nt = self.cur; name = self.expect_ident()
                if (name[:1].isupper() and e.kind == "Name" and e.name[:1].isupper() and self.at_struct_lit()):
                    q = QName(name); q.qual = e.name           # Shape.Rect { w: 1.0 }
                    e = self.struct_lit(nt, q); continue
                if name[:1].isupper() and self.at_struct_lit():
                    # 模块限定的结构体字面量 geom.Point { … } / a.b.T { … }
                    path, x = [], e
                    while x.kind == "Field": path.append(x.name); x = x.obj
                    if x.kind == "Name" and x.name[:1].islower() and all(p[:1].islower() for p in path):
                        qn = ".".join([x.name] + path[::-1] + [name])
                        e = self.struct_lit(nt, qn); continue
                e = Node("Field", ln, obj=e, name=name)
            elif self.at_op("["):
                ln = self.adv().line
                if self.at_op("..") or self.at_op("..="):        # xs[..b] / xs[..=b] / xs[..]
                    op = self.adv().val
                    hi = None if op == ".." and self.at_op("]") else self.binary(self.RANGE_LVL + 1)
                    self.expect("OP", "]")
                    e = self.slice_node(e, None, op, hi, ln); continue
                saved = getattr(self, "_slice_start", -1); self._slice_start = self.i
                try: idx = self.expr()
                finally: self._slice_start = saved
                self.expect("OP", "]")
                if idx.kind == "Bin" and idx.op in ("..", "..="):
                    e = self.slice_node(e, idx.left, idx.op, idx.right, ln); continue
                e = Node("Index", ln, obj=e, index=idx)
            elif self.at_op("?"):
                ln = self.adv().line
                e = Node("Try", ln, value=e)
            else:
                return e

    def args(self):
        self.expect("OP", "("); args = []
        while not self.at_op(")"):
            args.append(self.expr())
            if self.at_op(","): self.adv()
            elif not self.at_op(")"): self.expect("OP", ",")
        self.adv()
        return args

    def is_paren_lambda(self):
        depth, j = 0, self.i
        while j < len(self.t):
            tk = self.t[j]
            if tk.kind == "OP" and tk.val in "([{": depth += 1
            elif tk.kind == "OP" and tk.val in ")]}":
                depth -= 1
                if depth == 0:
                    nx = self.t[j + 1]
                    return nx.kind == "OP" and nx.val == "=>"
            elif tk.kind == "EOF":
                return False
            j += 1
        return False

    def _is_fn_decl(self):
        """语句开头的 [async] [unsafe] fn NAME 是声明；async fn( / async { / unsafe { 是表达式"""
        j = self.i
        if self.t[j].kind == "KW" and self.t[j].val == "async": j += 1
        if self.t[j].kind == "KW" and self.t[j].val == "unsafe": j += 1
        return self.t[j].kind == "KW" and self.t[j].val == "fn" and self.t[j + 1].kind == "IDENT"

    def async_body(self, ln, ctx):
        """async 块 / async 闭包的体：允许 .await，打包成 Future { run: () => body }"""
        saved, self._loopfr = getattr(self, "_loopfr", []), []
        sa, self._async = getattr(self, "_async", False), True
        try: body = self.block() if self.at_op("{") else self.expr()
        finally: self._loopfr = saved; self._async = sa
        lam = Node("Lambda", ln, params=[], body=body); lam.ctx = ctx
        return Node("StructLit", ln, name="Future", fields=[("run", lam)], base=None)

    def async_expr(self):
        """async { … }  |  async x => e  |  async (a, b) => e  |  async fn (a: T) -> R { … }"""
        tok = self.adv()
        if self.at_op("{"):
            return self.async_body(tok.line, "async 块的值")
        if self.at_kw("fn"):
            self.adv(); ps = self.params()
            if self.at_op("->"):
                self.adv(); self.type_()
            if not self.at_op("{"): self.expect("OP", "{")
        elif self.at("IDENT") and self.peek().kind == "OP" and self.peek().val == "=>":
            ps = [(self.adv().val, None)]; self.adv()
        elif self.at_op("(") and self.is_paren_lambda():
            ps = self.params(); self.expect("OP", "=>")
        else:
            raise KiteError("语法错误: async 后面应该是 { 块 }、闭包或 fn", tok.line, tok.col)
        return Node("Lambda", tok.line, params=ps, body=self.async_body(tok.line, "async 闭包的返回值"))

    def lambda_body(self):
        saved, self._loopfr = getattr(self, "_loopfr", []), []   # 闭包是循环标签的边界
        sa, self._async = getattr(self, "_async", False), False   # 普通闭包里不能 .await
        try: return self.block() if self.at_op("{") else self.expr()
        finally: self._loopfr = saved; self._async = sa

    def primary(self):
        tok = self.cur
        if tok.kind == "INT" or tok.kind == "FLOAT":
            self.adv(); return Node("Lit", tok.line, value=tok.val)
        if tok.kind == "STR":
            self.adv()
            parts = []
            for p in tok.val:
                if isinstance(p, tuple):
                    src, spec = split_fmt_spec(p[1])
                    stoks = lex(src)
                    for st in stoks: st.line = tok.line + st.line - 1   # 插值表达式的行号相对字符串所在行
                    sub = Parser(stoks)
                    sub.skip_nl()
                    if sub.cur.kind == "EOF":
                        raise KiteError("语法错误: 空的字符串插值 {}（字面的 { 写成 \\{）", tok.line, tok.col)
                    ex = sub.expr(); sub.skip_nl()
                    if sub.cur.kind != "EOF":
                        raise KiteError(f"语法错误: 字符串插值里表达式后面有多余内容 {sub.cur.val!r}", tok.line, tok.col)
                    if spec is not None:
                        if not FMT_SPEC_RE.fullmatch(spec):
                            raise KiteError(f"语法错误: 无效的格式说明 {spec!r}（[[填充]对齐][0][宽度][.精度][x|X|o|b|e|?]）", tok.line, tok.col)
                        ex = Node("Call", tok.line, fn=Node("Name", tok.line, name="__fmt"),
                                  args=[ex, Node("Lit", tok.line, value=spec)])
                    parts.append(ex)
                else:
                    parts.append(p)
            if len(parts) == 1 and isinstance(parts[0], str):
                return Node("Lit", tok.line, value=parts[0])
            return Node("Interp", tok.line, parts=parts)
        if self.at_kw("true") or self.at_kw("false"):
            self.adv(); return Node("Lit", tok.line, value=tok.val == "true")
        if tok.kind == "IDENT" and self.peek().kind == "OP" and self.peek().val == "=>" and not getattr(self, "in_guard", False):
            name = self.adv().val; self.adv()
            return Node("Lambda", tok.line, params=[(name, None)], body=self.lambda_body())
        if tok.kind == "IDENT" and tok.val == "select" and self.at("OP", "{", self.peek()):
            return self.select_expr()
        if tok.kind == "IDENT" and tok.val == "race" and self.at("OP", "{", self.peek()):
            self.adv(); self.adv(); self.skip_nl(); items = []
            while not self.at_op("}"):
                items.append(self.expr())
                if self.at_op(","): self.adv()
                self.skip_nl()
            self.adv()
            if not items: raise KiteError("语法错误: race 至少要有一项", tok.line)
            return Node("Race", tok.line, items=items)
        if tok.kind == "IDENT" or self.at_kw("self") or self.at_kw("Self"):
            self.adv()
            # 集合字面量: Set{a, b, c}。条件里（no_struct）要求 Set 与 { 紧挨着，否则 { 是代码块
            if (tok.kind == "IDENT" and tok.val == "Set" and self.at_op("{")
                    and (not getattr(self, "no_struct", False) or (self.cur.line == tok.line and self.cur.col == tok.col + 3))):
                self.adv(); self.skip_nl(); items = []
                saved = getattr(self, "no_struct", False); self.no_struct = False
                while not self.at_op("}"):
                    items.append(self.expr())
                    if self.at_op(","): self.adv()
                    elif not self.at_op("}"): self.skip_nl()
                    self.skip_nl()
                self.no_struct = saved
                self.adv()
                return Node("Call", tok.line, fn=Node("Name", tok.line, name="__set_lit"), args=[Node("List", tok.line, items=items)])
            # 结构体字面量: Name { field: value, ... }
            if tok.val[:1].isupper() and self.at_struct_lit():
                return self.struct_lit(tok, tok.val)
            return Node("Name", tok.line, name=tok.val)
        if self.at_op("("):
            return self.paren_expr(tok)
        return self.primary2(tok)

    def at_struct_lit(self):
        return (not getattr(self, "no_struct", False) and self.at_op("{")
                and (self.peek().kind == "IDENT" and self.peek(2).kind == "OP" and self.peek(2).val in (":", ",", "}")
                     or self.peek().kind == "OP" and self.peek().val in ("}", "..")))

    def struct_lit(self, tok, name):
        if True:
                self.adv(); fields = []; base = None
                self.skip_nl()
                while not self.at_op("}"):
                    if self.at_op(".."):          # 结构体更新: Name { f: v, ..base }，..base 必须在最后
                        bt = self.adv(); base = self.expr(); self.skip_nl()
                        if self.at_op(","): self.adv(); self.skip_nl()
                        if not self.at_op("}"):
                            raise KiteError("语法错误: ..base 必须是结构体字面量的最后一项", bt.line, bt.col)
                        break
                    fname = self.expect_ident()
                    if self.at_op(":"):
                        self.adv(); fields.append((fname, self.expr()))
                    else:
                        fields.append((fname, Node("Name", tok.line, name=fname)))
                    if self.at_op(","): self.adv()
                    self.skip_nl()
                self.adv()
                return Node("StructLit", tok.line, name=name, fields=fields, base=base)

    def paren_expr(self, tok):
        if True:
            if self.is_paren_lambda():
                ps = self.params(); self.expect("OP", "=>")
                return Node("Lambda", tok.line, params=ps, body=self.lambda_body())
            self.adv()
            if self.at_op(")"):
                self.adv(); return Node("Tuple", tok.line, items=[])
            first = self.expr()
            if self.at_op(")"):
                self.adv(); return first
            items = [first]
            while self.at_op(","):
                self.adv()
                if self.at_op(")"): break
                items.append(self.expr())
            self.expect("OP", ")")
            return Node("Tuple", tok.line, items=items)

    def primary2(self, tok):
        if self.at_op("["):
            self.adv(); items = []
            while not self.at_op("]"):
                items.append(self.expr())
                if len(items) == 1 and self.at_op(";"):     # [x; n] 重复 n 次（x 只求值一次）
                    self.adv(); cnt = self.expr(); self.expect("OP", "]")
                    return Node("Call", tok.line, fn=Node("Name", tok.line, name="__repeat"), args=[items[0], cnt])
                if self.at_op(","): self.adv()
                elif not self.at_op("]"): self.expect("OP", ",")
            self.adv()
            return Node("List", tok.line, items=items)
        if self.at_op("{"):
            return self.block()
        if self.at_kw("if"):
            return self.if_expr()
        if self.at_kw("match"):
            return self.match_expr()
        if self.at_kw("loop"):
            self.adv(); return self.loop_expr(tok.line, None, force=True)
        if tok.kind == "LABEL" and self.peek().kind == "OP" and self.peek().val == ":":
            self.adv(); self.adv()
            if not self.at_kw("loop"):
                raise KiteError(f"语法错误: 表达式里的标签 '{tok.val} 后面必须是 loop", self.cur.line, self.cur.col)
            self.adv(); return self.loop_expr(tok.line, tok.val, force=True)
        if self.at_kw("spawn"):
            self.adv(); self.expect("OP", "{"); self.skip_nl(); items = []
            while not self.at_op("}"):
                items.append(self.expr())
                if self.at_op(","): self.adv()
                self.skip_nl()
            self.adv()
            return Node("Spawn", tok.line, items=items)
        if self.at_kw("unsafe"):
            self.adv(); b = self.block(); b.unsafe = True; return b
        if self.at_kw("async"):
            return self.async_expr()
        if self.at_kw("fn"):
            self.adv(); ps = self.params()
            if self.at_op("->"):
                self.adv(); self.type_()
            return Node("Lambda", tok.line, params=ps, body=self.lambda_body())
        raise KiteError(f"语法错误: 意外的 {tok.val if tok.val is not None else tok.kind!r}", tok.line, tok.col)

    def if_expr(self):
        ln = self.expect("KW", "if").line
        if self.at_kw("let"):   # if let 模式 = e { .. } else { .. } → match
            self.adv(); pat = self.pattern(); self.expect("OP", "=")
            subj = self.expr(no_struct=True)
            then = self.block(); els = None
            if self.at_kw("else"):
                self.adv()
                els = self.if_expr() if self.at_kw("if") else self.block()
            if els is None:
                then = _unit_tail(then); els = Node("Block", ln, stmts=[])
            elif els.kind != "Block":
                els = Node("Block", ln, stmts=[Node("ExprStmt", ln, expr=els)])
            return Node("Match", ln, subject=subj, arms=[(pat, None, then), (("wild",), None, els)])
        cond = self.expr(no_struct=True)
        if not self.legacy and (cond.kind == "Is" or getattr(cond, "is_conj", None)):
            # if x is P { … } / if x is P && R { … }：P 的绑定在 then 分支可见 → match x { P if R => …, _ => … }
            isn, guard = (cond, None) if cond.kind == "Is" else cond.is_conj
            then = self.block(); els = None
            if self.at_kw("else"):
                self.adv()
                els = self.if_expr() if self.at_kw("if") else self.block()
            if els is None:
                then = _unit_tail(then); els = Node("Block", ln, stmts=[])
            elif els.kind != "Block":
                els = Node("Block", ln, stmts=[Node("ExprStmt", ln, expr=els)])
            return Node("Match", ln, subject=isn.value, arms=[(isn.pat, guard, then), (("wild",), None, els)])
        then = self.block(); els = None
        if self.at_kw("else"):
            self.adv()
            els = self.if_expr() if self.at_kw("if") else self.block()
        return Node("If", ln, cond=cond, then=then, els=els)

    def guard_expr(self):
        """分支守卫：`if 标识符 =>` 里的 `标识符 =>` 不是 lambda"""
        saved = getattr(self, "in_guard", False); self.in_guard = True
        try: return self.expr()
        finally: self.in_guard = saved

    def select_expr(self):
        """select { recv(ch) [as 模式] => e, send(ch, v) => e, after(ms) => e, _ => e }"""
        ln = self.adv().line; self.expect("OP", "{"); self.skip_nl(); arms = []
        while not self.at_op("}"):
            t = self.cur
            if t.kind == "IDENT" and t.val == "_":
                self.adv(); arm = ("default", None, None, None, None)
            elif t.kind == "IDENT" and t.val in ("recv", "send", "after") and self.at("OP", "(", self.peek()):
                self.adv(); self.adv()
                a = self.expr(); b = None
                if t.val == "send":
                    self.expect("OP", ","); b = self.expr()
                self.expect("OP", ")")
                pat = None; guard = None
                if t.val == "recv" and self.at_kw("as"):
                    self.adv(); pat = self.pattern()
                if t.val != "after" and self.at_kw("if"):
                    self.adv(); guard = self.guard_expr()
                arm = (t.val, a, b, pat, guard)
            else:
                raise KiteError("语法错误: select 的分支必须是 recv(通道)、send(通道, 值)、after(毫秒) 或 _", t.line, t.col)
            self.expect("OP", "=>")
            arms.append(arm + (self.expr(),))
            if self.at_op(","): self.adv()
            self.skip_nl()
        self.adv()
        kinds = [a[0] for a in arms]
        if not any(k in ("recv", "send") for k in kinds):
            raise KiteError("语法错误: select 至少要有一个 recv 或 send 分支", ln)
        if kinds.count("after") + kinds.count("default") > 1:
            raise KiteError("语法错误: select 最多只能有一个 after 或 _ 分支（二者不能同时出现）", ln)
        return Node("Select", ln, arms=arms)

    def match_expr(self):
        ln = self.expect("KW", "match").line
        subj = self.expr(no_struct=True)
        self.expect("OP", "{"); self.skip_nl(); arms = []
        while not self.at_op("}"):
            pat = self.pattern(); guard = None
            if self.at_kw("if"):
                self.adv(); guard = self.guard_expr()
            self.expect("OP", "=>")
            body = self.expr()
            arms.append((pat, guard, body))
            if self.at_op(","): self.adv()
            self.skip_nl()
        self.adv()
        return Node("Match", ln, subject=subj, arms=arms)

    # 模式
    def pattern(self):
        p = self.pattern1()
        if not self.at_op("|"): return p
        alts = [p]   # 或模式 A | B | C：每个分支必须绑定同样的变量
        while self.at_op("|"):
            self.adv(); self.skip_nl()
            alts.append(self.pattern1())
        return ("or", alts)

    def pat_lit_start(self, k=0):
        t = self.peek(k) if k else self.cur
        return t.kind in ("INT", "FLOAT", "STR") or t.kind == "OP" and t.val == "-" and self.peek(k + 1).kind in ("INT", "FLOAT")

    def pat_lit(self):
        tok = self.cur
        if self.at_op("-"):
            self.adv(); t = self.adv()
            if t.kind not in ("INT", "FLOAT"): raise KiteError("语法错误: - 后面必须是数字", t.line, t.col)
            return -t.val
        if tok.kind == "STR":
            self.adv()
            if any(not isinstance(x, str) for x in tok.val):
                raise KiteError("语法错误: 模式里的字符串不能有插值", tok.line, tok.col)
            return "".join(tok.val)
        if tok.kind in ("INT", "FLOAT"): return self.adv().val
        raise KiteError(f"语法错误: 期望字面量，实际是 {tok.val!r}", tok.line, tok.col)

    def seq_pats(self, close, allow_bind):
        """( … ) / [ … ] 里的模式序列；.. [NAME] 为剩余项（每个序列至多一个）"""
        pre, post, rest = [], [], None; seen = False
        while not self.at_op(close):
            self.skip_nl()
            if self.at_op("..") and not self.pat_lit_start(1):
                t = self.adv()
                if seen: raise KiteError("语法错误: 一个序列模式里只能有一个 ..", t.line, t.col)
                seen = True
                rest = "_"
                if self.at("IDENT") and self.cur.val != "_":
                    if not allow_bind: raise KiteError("语法错误: 元组 / 变体模式里的 .. 不能绑定名字", t.line, t.col)
                    rest = self.adv().val
                elif self.at("IDENT"): self.adv()
            else:
                (post if seen else pre).append(self.pattern())
            self.skip_nl()
            if self.at_op(","): self.adv()
            elif not self.at_op(close): self.expect("OP", close)
        self.adv()
        return pre, (rest if seen else None), post

    def pattern1(self):
        tok = self.cur
        if tok.kind == "IDENT" and tok.val == "_":
            self.adv(); return ("wild",)
        if self.at_kw("var"):
            self.adv(); n = self.cur
            if n.kind != "IDENT" or n.val == "_" or n.val[:1].isupper():
                raise KiteError("语法错误: var 后面必须是小写的绑定名", n.line, n.col)
            self.adv(); b = ("bind", n.val, True)
            if self.at_op("@"):
                self.adv(); return ("at", b, self.pattern1())
            return b
        if self.at_op("..") or self.at_op("..="):   # ..5 / ..=5
            op = self.adv()
            if not self.pat_lit_start(): raise KiteError("语法错误: 区间模式 .. 后面需要字面量", op.line, op.col)
            return ("range", None, self.pat_lit(), op.val == "..=")
        if self.pat_lit_start():
            lo = self.pat_lit()
            if self.at_op("..="):
                self.adv(); return ("range", lo, self.pat_lit(), True)
            if self.at_op(".."):
                self.adv()
                return ("range", lo, self.pat_lit() if self.pat_lit_start() else None, False)
            return ("lit", lo)
        if self.at_kw("true") or self.at_kw("false"):
            self.adv(); return ("lit", tok.val == "true")
        if self.at_op("("):
            self.adv(); self.skip_nl()
            pre, rest, post = self.seq_pats(")", False)
            if rest is None and len(pre) == 1: return pre[0]
            if rest is None: return ("tuple", pre)
            return ("tuple", pre + [("dots",)] + post)
        if self.at_op("["):
            self.adv()
            pre, rest, post = self.seq_pats("]", True)
            if post: return ("list", pre, rest, post)
            return ("list", pre, rest)
        if tok.kind == "IDENT" or self.at_kw("self") or self.at_kw("Self"):
            if self.at_kw("Self"):
                tgt = getattr(self, "impl_target", None)
                if tgt is None: raise KiteError("语法错误: 模式里的 Self 只能用在 impl 里", tok.line, tok.col)
                self.adv(); name = tgt
            else:
                name = self.adv().val
            while self.at_op(".") and self.peek().kind == "IDENT":
                q = name; self.adv(); name = QName(self.adv().val); name.qual = q   # Shape.Circle → Circle（记住限定名）
            if self.at_op("@"):
                if name[:1].isupper() or isinstance(name, QName) or name in ("self",):
                    raise KiteError("语法错误: @ 左边必须是小写的绑定名", tok.line, tok.col)
                self.adv(); return ("at", ("bind", name), self.pattern1())
            if self.at_op("("):
                self.adv()
                pre, rest, post = self.seq_pats(")", False)
                subs = pre if rest is None else pre + [("dots",)] + post
                return ("variant", name, subs)
            if self.at_op("{") and name[:1].isupper():
                self.adv(); fs = []
                while not self.at_op("}"):
                    if self.at_op(".."):
                        self.adv(); continue
                    f = self.expect_ident()
                    if self.at_op(":"):
                        self.adv(); fs.append((f, self.pattern()))
                    else:
                        fs.append((f, ("bind", f)))
                    if self.at_op(","): self.adv()
                self.adv()
                return ("struct", name, fs)
            if name[:1].isupper():
                return ("variant", name, None)
            return ("bind", name)
        raise KiteError(f"语法错误: 无效的模式 {tok.val!r}", tok.line, tok.col)

def _desugar_impl_types(ps, ret, gens, bounds):
    """参数里的 impl Tr → 匿名泛型参数（带约束 Tr）；返回类型里的 impl Tr → dyn Tr（不透明）"""
    gens = list(gens); n = [0]
    def walk(t, in_ret):
        if t is None or not isinstance(t, tuple): return t
        k = t[0]
        if k == "impl":
            if in_ret: return ("dyn", t[1], t[2])
            n[0] += 1; g = f"impl {' + '.join(t[1])}#{n[0]}"
            gens.append(g); bounds[g] = list(t[1])
            return ("con", g, [], t[2])
        if k == "con": return ("con", t[1], [walk(a, in_ret) for a in t[2]], t[3])
        if k == "tuple": return ("tuple", [walk(a, in_ret) for a in t[1]]) + tuple(t[2:])
        if k == "fn": return ("fn", [walk(a, in_ret) for a in t[1]], walk(t[2], in_ret)) + tuple(t[3:])
        return t
    ps = [(nm, walk(ta, False)) for nm, ta in ps]
    return ps, walk(ret, True), gens


class RecFields(list):
    """记录变体 Rect { w: Float, h: Float } 的字段表（普通变体用 list）；显示成 Rect { w: 1.0, h: 2.0 }"""


class RecNames(tuple):
    """记录变体值的字段名（EnumVal.names）"""


class QName(str):
    """限定的变体名（A.Other）：运行时只比较变体名，类型检查用 .qual 选对枚举"""
    qual = None

# ───────────────────────── 运行时值 ─────────────────────────
class UnitType:
    _inst = None
    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst
    def __repr__(self): return "()"
Unit = UnitType()


class StructVal:
    __slots__ = ("tname", "fields")
    def __init__(self, tname, fields): self.tname, self.fields = tname, fields
    def __eq__(self, o): return isinstance(o, StructVal) and o.tname == self.tname and o.fields == self.fields
    def __hash__(self): return hash((self.tname, tuple(sorted(self.fields.items()))))


class EnumVal:
    __slots__ = ("ename", "variant", "values", "names")
    def __init__(self, ename, variant, values=(), names=None):
        self.ename, self.variant, self.values, self.names = ename, variant, tuple(values), names
    def __eq__(self, o): return isinstance(o, EnumVal) and (o.variant, o.values) == (self.variant, self.values)
    def __hash__(self): return hash((self.variant, self.values))


class EnumType:
    def __init__(self, name): self.name, self.variants = name, {}


class StructType:
    def __init__(self, name, fields): self.name, self.fields = name, fields


class Module:
    def __init__(self, name, members): self.name, self.members = name, members


class KFunc:
    def __init__(self, name, params, body, env, is_expr=False):
        self.name, self.params, self.body, self.env, self.is_expr = name, params, body, env, is_expr


class Builtin:
    def __init__(self, name, fn): self.name, self.fn = name, fn


def _ext_ty(ta):
    """extern 签名里的类型 → Int/Float/Bool/Str/Unit（类型检查器已保证合法）"""
    if ta is None: return "Unit"
    return ta[1] if ta[0] == "con" else "?"


_EXT_LIBS = {}
_CSTRUCTS = {}

def _ct(d):
    """extern 类型描述 → ctypes 类型；结构体描述 ("S", 名字, ((字段, 描述), …)) 生成 ctypes.Structure；
    回调描述 ("F", 参数, 返回) 生成 CFUNCTYPE"""
    import ctypes
    if isinstance(d, tuple) and d[0] == "F":
        if d not in _CSTRUCTS:
            _CSTRUCTS[d] = ctypes.CFUNCTYPE(None if d[2] == "Unit" else _ct(d[2]), *[_ct(p) for p in d[1]])
        return _CSTRUCTS[d]
    if isinstance(d, tuple):
        if d not in _CSTRUCTS:
            _CSTRUCTS[d] = type("kx_" + d[1], (ctypes.Structure,), {"_fields_": [(f, _ct(fd)) for f, fd in d[2]]})
        return _CSTRUCTS[d]
    return {"Int": ctypes.c_int64, "Float": ctypes.c_double, "Bool": ctypes.c_bool,
            "Str": ctypes.c_char_p, "Ptr": ctypes.c_void_p,
            "I8": ctypes.c_int8, "I16": ctypes.c_int16, "I32": ctypes.c_int32, "I64": ctypes.c_int64,
            "U8": ctypes.c_uint8, "U16": ctypes.c_uint16, "U32": ctypes.c_uint32, "U64": ctypes.c_uint64,
            "F32": ctypes.c_float, "F64": ctypes.c_double}[d]

# 窄整数宽度的取值范围（U64 按位传递，不检查）
C_RANGES = {"I8": (-128, 127), "I16": (-32768, 32767), "I32": (-2147483648, 2147483647),
            "U8": (0, 255), "U16": (0, 65535), "U32": (0, 4294967295)}

def _to_c(a, d):
    if isinstance(d, tuple):
        return _ct(d)(*[_to_c(a.fields[f], fd) for f, fd in d[2]])
    if d == "Str": return a.encode("utf-8")
    if d in C_RANGES:
        lo, hi = C_RANGES[d]
        if not lo <= a <= hi: raise KiteError(f"extern: {a} 超出 {d} 的范围")
    if d == "U64" and a < 0: return a + (1 << 64)
    if d in ("F32", "F64"): return float(a)
    return a

def _from_c(r, d):
    if isinstance(d, tuple):
        return StructVal(d[1], {f: _from_c(getattr(r, f), fd) for f, fd in d[2]})
    if d == "Str": return (r or b"").decode("utf-8", "replace")
    if d == "Bool": return bool(r)
    if d == "Ptr": return int(r or 0)
    if d == "U64": r = int(r); return r - (1 << 64) if r >= 1 << 63 else r
    if d in ("Float", "F32", "F64"): return float(r)
    return int(r)


def _ptr_module():
    """std.ptr：裸指针（运行时就是地址整数），读写按字节偏移"""
    import ctypes
    libc = ctypes.CDLL(None)
    libc.malloc.restype = ctypes.c_void_p; libc.malloc.argtypes = [ctypes.c_size_t]
    libc.free.argtypes = [ctypes.c_void_p]; libc.free.restype = None
    def chk(p, what):
        if not p: raise KiteError(f"ptr.{what}: 空指针")
        return p
    def alloc(n):
        if n < 0: raise KiteError("ptr.alloc: 大小不能是负数")
        p = libc.malloc(max(n, 1))
        if not p: raise KiteError("ptr.alloc: 内存不足")
        ctypes.memset(p, 0, max(n, 1))
        return int(p)
    def free(p):
        if p: libc.free(p)
        return Unit
    def wr(ct, p, off, v, what):
        ct.from_address(chk(p, what) + off).value = v; return Unit
    def from_str(st):
        b = st.encode("utf-8"); p = alloc(len(b) + 1)
        ctypes.memmove(p, b, len(b)); return p
    return {"null": lambda: 0, "is_null": lambda p: p == 0, "addr": lambda p: p,
            "offset": lambda p, n: ck(p + n), "from_addr": lambda n: n, "alloc": alloc, "free": free,
            "read_int": lambda p, o: ctypes.c_int64.from_address(chk(p, "read_int") + o).value,
            "write_int": lambda p, o, v: wr(ctypes.c_int64, p, o, v, "write_int"),
            "read_float": lambda p, o: ctypes.c_double.from_address(chk(p, "read_float") + o).value,
            "write_float": lambda p, o, v: wr(ctypes.c_double, p, o, float(v), "write_float"),
            "read_byte": lambda p, o: ctypes.c_uint8.from_address(chk(p, "read_byte") + o).value,
            "write_byte": lambda p, o, v: wr(ctypes.c_uint8, p, o, v & 255, "write_byte"),
            "to_str": lambda p: ctypes.string_at(chk(p, "to_str")).decode("utf-8", "replace"),
            "from_str": from_str}

def make_externs(fn, caller=None):
    """extern fn 本身，加上类型检查器为可变参数调用登记的各个变体 → [(名字, Builtin)]"""
    out = [] if getattr(fn, "varargs", False) else [(fn.name, make_extern(fn, caller))]
    for key, extra in getattr(fn, "_va_variants", {}).items():
        out.append((key, make_extern(fn, caller, extra)))
    return out


def make_extern(fn, caller=None, va=None):
    """extern fn → 用 ctypes 调用 C 函数的内建函数（解释器和字节码共用；原生后端看 .ext 直接生成 C 调用）"""
    import ctypes, ctypes.util
    cname = getattr(fn, "cname", fn.name)
    if getattr(fn, "ext_tys", None):      # 类型检查器算好的描述（含按值传递的结构体）
        ptys, rty = list(fn.ext_tys[0]), fn.ext_tys[1]
    else:
        ptys = [_ext_ty(t) for _, t in fn.params]
        rty = _ext_ty(fn.ret)
    libs = list(getattr(fn, "extern", []) or [])
    handles = []
    for lib in [None, "m"] + libs:
        if lib not in _EXT_LIBS:
            try:
                path = None if lib is None else (ctypes.util.find_library(lib) or lib)
                _EXT_LIBS[lib] = ctypes.CDLL(path)
            except OSError:
                if lib in libs: raise KiteError(f"extern: 找不到库 {lib}", fn.line)
                _EXT_LIBS[lib] = None
        if _EXT_LIBS[lib] is not None: handles.append(_EXT_LIBS[lib])
    cf = None
    for h in reversed(handles):
        try: cf = h[cname]; break        # 下标取符号每次得到新的函数对象（各变体的 argtypes 互不影响）
        except AttributeError: pass
    if cf is None:
        raise KiteError(f"extern: 找不到 C 函数 {cname}", fn.line)
    nfixed = len(ptys)
    if va is not None: ptys = ptys + list(va)
    cf.argtypes = [_ct(t) for t in ptys[:nfixed]]
    cf.restype = None if rty == "Unit" else _ct(rty)
    VA_CT = {"Int": ctypes.c_int64, "Float": ctypes.c_double, "Bool": ctypes.c_int, "Ptr": ctypes.c_void_p}

    def call(*args):
        if len(args) != len(ptys):
            raise KiteError(f"调用 {cname} 参数个数错误")
        err = []
        def cb(f, d):
            # Kite 函数 → C 函数指针（只在这次调用期间有效）；回调里的错误先记下，C 返回后再抛出
            def tramp(*cargs):
                if err: return None if d[2] == "Unit" else 0
                try:
                    r = caller(f, [_from_c(a, p) for a, p in zip(cargs, d[1])])
                    if d[2] == "Unit": return None
                    return float(r) if d[2] == "Float" else bool(r) if d[2] == "Bool" else int(r)
                except BaseException as e:
                    err.append(e); return None if d[2] == "Unit" else 0
            return _ct(d)(tramp)
        cargs = [cb(a, t) if isinstance(t, tuple) and t[0] == "F" else _to_c(a, t) for a, t in zip(args, ptys)]
        for j in range(nfixed, len(cargs)):          # 可变参数：按 C 的默认提升传递
            t = ptys[j]
            cargs[j] = ctypes.c_char_p(cargs[j]) if t == "Str" else VA_CT[t](cargs[j])
        r = cf(*cargs)
        if err: raise err[0]
        return Unit if rty == "Unit" else _from_c(r, rty)
    b = Builtin(fn.name, call)
    b.ext = (cname, ptys, rty, libs, nfixed if va is not None else None)
    return b


class CheckFailed(KiteError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("\n".join(str(e) for e in errors[:20]) + (f"\n…共 {len(errors)} 个错误" if len(errors) > 20 else ""))


class ReturnSig(Exception):
    def __init__(self, v): self.v = v
class BreakSig(Exception):
    def __init__(self, label=None, value=None): self.label, self.value = label, value
class ContinueSig(Exception):
    def __init__(self, label=None): self.label = label


def Ok(v=Unit): return EnumVal("Result", "Ok", (v,))
def Err(v): return EnumVal("Result", "Err", (v,))
def Some(v): return EnumVal("Option", "Some", (v,))
NoneV = EnumVal("Option", "None", ())

_MISSING = object()


def map_key(x):
    """把任意 Kite 值转换成可哈希、与 Kite 相等语义一致的键（List、Map、结构体等可以当 Map 的键，和原生一致）。"""
    t = type(x)
    if t is str or t is int or t is float or t is bool or x is Unit: return x
    if t is list: return ("L", tuple(map_key(e) for e in x))
    if t is tuple: return ("T", tuple(map_key(e) for e in x))
    if t is StructVal: return ("S", x.tname, tuple(sorted((k, map_key(v)) for k, v in x.fields.items())))
    if t is EnumVal: return ("E", x.variant, tuple(map_key(e) for e in x.values))
    if t is PMap: return ("M", frozenset((fk, map_key(v)) for fk, (_, v) in x._root().items()))
    if t is PSet: return ("Z", frozenset(x._m._root().keys()))
    return ("O", id(x))   # 函数、通道等：按身份比较


class PMap:
    """持久化 Map（Python 引擎）：Baker 式“重定根”。最新版本直接持有 dict，旧版本记录与较新版本的差异；
    访问旧版本时把 dict 反向回滚到它。线性使用（m = set(m, k, v)）每次写入 O(1)，旧版本依然可用、值不变。
    插入顺序与 dict 一致。删除走完整复制（回滚删除会打乱顺序）。
    内部 dict 为 map_key(k) → (k, v)，因此任何 Kite 值都能当键。"""
    __slots__ = ("_d", "_k", "_old", "_next")

    def __init__(self, d=None):
        self._d = {} if d is None else d   # 根版本：持有 dict
        self._next = None                  # 非根版本：_k 在较新版本 _next 中的旧值是 _old

    @staticmethod
    def of(pairs):
        d = {}
        for p in pairs:
            if type(p) is not tuple or len(p) != 2: raise KiteError("Map 需要 (键, 值) 列表")
            d[map_key(p[0])] = p
        return PMap(d)

    def _root(self):
        if self._d is not None:
            return self._d
        path = []
        m = self
        while m._d is None:
            path.append(m); m = m._next
        d = m._d
        for x in reversed(path):           # 从最靠近根的版本开始，逐个把根移过来
            nxt = x._next
            cur = d.get(x._k, _MISSING)
            if x._old is _MISSING: del d[x._k]
            else: d[x._k] = x._old
            nxt._d, nxt._k, nxt._old, nxt._next = None, x._k, cur, x
            x._d, x._next = d, None
        return d

    def set(self, k, v):
        d = self._root()
        fk = map_key(k)
        old = d.get(fk, _MISSING)
        if old is not _MISSING and old[1] is v:
            return self
        d[fk] = (k, v)   # 与原生一致：更新时键也换成新给的值（如 1 与 1.0）
        new = PMap(d)
        self._d, self._k, self._old, self._next = None, fk, old, new
        return new

    def remove(self, k):
        d = self._root()
        fk = map_key(k)
        if fk not in d: return self
        c = dict(d); del c[fk]
        return PMap(c)

    def items(self): return list(self._root().values())
    def keys(self): return [k for k, _ in self._root().values()]
    def values(self): return [v for _, v in self._root().values()]
    def __len__(self): return len(self._root())
    def __contains__(self, k): return map_key(k) in self._root()
    def __getitem__(self, k): return self._root()[map_key(k)][1]
    def __iter__(self): return iter(self.keys())
    def __eq__(self, o):
        if not isinstance(o, PMap): return False
        a = {fk: map_key(v) for fk, (_, v) in self._root().items()}
        return a == {fk: map_key(v) for fk, (_, v) in o._root().items()}
    def __hash__(self): return hash(map_key(self))


class PSet:
    """持久化集合：包一层 PMap（值固定为 Unit），遍历顺序为插入顺序，与原生一致。"""
    __slots__ = ("_m",)

    def __init__(self, m=None): self._m = PMap() if m is None else m

    @staticmethod
    def of(xs):
        d = {}
        for x in xs:
            k = map_key(x)
            if k not in d: d[k] = (x, Unit)   # 重复元素保留第一个
        return PSet(PMap(d))

    def insert(self, x):
        if x in self._m: return self
        return PSet(self._m.set(x, Unit))
    def remove(self, x): return PSet(self._m.remove(x))
    def keys(self): return self._m.keys()
    def __len__(self): return len(self._m)
    def __contains__(self, x): return x in self._m
    def __iter__(self): return iter(self._m.keys())
    def __eq__(self, o):
        return isinstance(o, PSet) and self._m._root().keys() == o._m._root().keys()
    def __hash__(self): return hash(map_key(self))


def show(v, quote=False):
    if v is True: return "true"
    if v is False: return "false"
    if isinstance(v, str): return '"' + v.replace('"', '\\"') + '"' if quote else v
    if isinstance(v, float):
        return repr(v) if not v.is_integer() or abs(v) >= 1e16 else f"{v:.1f}"
    if isinstance(v, int): return str(v)
    if isinstance(v, list): return "[" + ", ".join(show(x, True) for x in v) + "]"
    if isinstance(v, tuple): return "(" + ", ".join(show(x, True) for x in v) + ")"
    if isinstance(v, PMap): return "{" + ", ".join(f"{show(k, True)}: {show(x, True)}" for k, x in v.items()) + "}"
    if isinstance(v, PSet): return "Set{" + ", ".join(show(k, True) for k in v.keys()) + "}"
    if isinstance(v, StructVal):
        return f"{v.tname} {{ " + ", ".join(f"{k}: {show(x, True)}" for k, x in v.fields.items()) + " }"
    if isinstance(v, EnumVal):
        if isinstance(v.names, RecNames):
            return v.variant + " { " + ", ".join(f"{n}: {show(x, True)}" for n, x in zip(v.names, v.values)) + " }"
        return v.variant + ("(" + ", ".join(show(x, True) for x in v.values) + ")" if v.values else "")
    if isinstance(v, (KFunc, Builtin)): return f"<fn {v.name}>"
    if isinstance(v, (EnumType, StructType)): return f"<type {v.name}>"
    if isinstance(v, Module): return f"<module {v.name}>"
    if isinstance(v, Chan): return "<chan>"
    return repr(v)


def type_name(v):
    if isinstance(v, bool): return "Bool"
    if isinstance(v, int): return "Int"
    if isinstance(v, float): return "Float"
    if isinstance(v, str): return "Str"
    if isinstance(v, list): return "List"
    if isinstance(v, tuple): return "Tuple"
    if isinstance(v, PMap): return "Map"
    if isinstance(v, PSet): return "Set"
    if isinstance(v, StructVal): return v.tname
    if isinstance(v, EnumVal): return v.ename
    if isinstance(v, (KFunc, Builtin)): return "Fn"
    if isinstance(v, Chan): return "Chan"
    return "Unit"


# ───────────────────────── 任务（结构化并发）─────────────────────────
class Task:
    """spawn 产生的任务。变量记录所属任务；任务结束后，它的变量交还给父任务。"""
    __slots__ = ("parent", "done", "cancelled", "unwinding")
    def __init__(self, parent):
        self.parent, self.done, self.cancelled, self.unwinding = parent, False, False, False

class Cancelled(BaseException):
    """任务被取消时在取消点抛出，沿调用栈展开（途中执行 defer），在任务边界被接住"""

def task_cancelled(t):
    """有效取消状态：从自己往上找，先遇到正在展开（清理中）的任务则不算取消，先遇到被取消的任务则算"""
    while t is not None:
        if t.unwinding: return False
        if t.cancelled: return True
        t = t.parent
    return False

MAIN_TASK = Task(None)
class TS:
    cur = MAIN_TASK
TASK_WRITE_ERR = "spawn 任务不能修改外层变量（该变量属于其他任务）"

def owner_ok(o):
    while o.done: o = o.parent
    return o is TS.cur

def run_task(fn):
    t = Task(TS.cur); saved = TS.cur; TS.cur = t
    try:
        return fn()
    finally:
        t.done = True; TS.cur = saved


# ── select 的降级（树解释器与虚拟机共用）──
# select { 分支... } ⇒ match __select([(种类, 通道, 值), ...], 超时毫秒或 -1, 有无默认) { (i, 绑定) => 分支体, ... }
_sel_n = [0]
def lower_select(e):
    ln = e.line; ops = []; arms = []; timeout = Node("Lit", ln, value=-1); has_default = False
    ci = 0; extra = None
    for kind, a, b, pat, guard, body in e.arms:
        if kind in ("recv", "send"):
            k = Node("Lit", ln, value=0 if kind == "recv" else 1)
            if guard is not None:   # 守卫为假的分支本次不参与（种类 -1）
                k = Node("If", ln, cond=guard, then=Node("Block", ln, stmts=[Node("ExprStmt", ln, expr=k)]),
                         els=Node("Block", ln, stmts=[Node("ExprStmt", ln, expr=Node("Lit", ln, value=-1))]))
            ops.append(Node("Tuple", ln, items=[k, a,
                                                b if b is not None else Node("Tuple", ln, items=[])]))
            arms.append((("tuple", [("lit", ci), pat if pat is not None else ("wild",)]), None, body))
            ci += 1
        else:
            extra = body
            if kind == "after": timeout = a
            else: has_default = True
    if extra is not None:
        arms.append((("wild",), None, extra))
    call = Node("Call", ln, fn=Node("Name", ln, name="__select"),
                args=[Node("List", ln, items=ops), timeout, Node("Lit", ln, value=has_default)])
    return Node("Match", ln, subject=call, arms=arms)

# ── 调度器（树解释器与虚拟机共用）──
# 确定性的协作式调度：任何时刻只有一个任务在跑（持有“接力棒”）。spawn 的各项按顺序在当前线程执行；
# 只有当某项在通道上阻塞时，才为尚未开始的项启动新线程，或把接力棒交给已就绪的任务。
# 所以不用通道的程序完全不创建线程，输出与 0.9 一致；用通道的程序每次运行的输出也完全一样。
# 所有任务都在等待且没有可启动的项时报“死锁”。
import threading, collections
threading.stack_size(256 << 20)   # 任务线程需要深递归，预留 256 MB 虚拟栈
DEADLOCK_ERR = "死锁：所有任务都在等待（通道收发或等待子任务），没有任务能继续"

class Waiter:
    __slots__ = ("ev", "task", "val", "status", "fired", "waiting", "tok")
    def __init__(self):
        self.ev = threading.Event(); self.task = None; self.val = None; self.status = 0
        self.fired = 0; self.waiting = False; self.tok = None

class QN:
    """等待队列里的一项：哪个等待者、它在 select 里的分支号、（发送方）要发的值"""
    __slots__ = ("w", "idx", "val")
    def __init__(self, w, idx, val): self.w, self.idx, self.val = w, idx, val

def _pop_live(q):
    while q:
        n = q.popleft()
        if n.w.waiting: return n
    return None

def _has_live(q):
    while q and not q[0].w.waiting: q.popleft()
    return bool(q)

def _fire(w, idx, status, val=None):
    """唤醒等待者（status 1 成功，2 通道已关闭，3 超时）"""
    w.waiting = False; w.status = status; w.fired = idx; w.val = val
    Sched.ready.append(w)

class Group:
    __slots__ = ("fns", "next", "remaining", "results", "joiner", "parent_task", "tasks", "race", "winner", "error")
    def __init__(self, fns, race=False):
        self.fns = fns; self.next = 0; self.remaining = len(fns)
        self.results = [None] * len(fns); self.joiner = None; self.parent_task = TS.cur
        self.tasks = [Task(TS.cur) for _ in fns]; self.race = race; self.winner = None; self.error = None

class Sched:
    tls = threading.local()
    ready = collections.deque()
    blocked = set()       # 可被取消唤醒的阻塞等待者
    cancels = 0           # 发生过取消（>0 时取消点才需要检查）
    ticks = 0
    timers = []           # (截止时间, 序号, 等待者, 令牌, 分支号) 小顶堆
    pending = []          # 还有未开始项的 Group
    fatal = None
    main = None

def sched_reset():
    Sched.ready = collections.deque(); Sched.pending = []; Sched.fatal = None; Sched.timers = []
    Sched.blocked = set(); Sched.cancels = 0; Sched.ticks = 0
    Sched.main = None; Sched.tls = threading.local()

def _me():
    w = getattr(Sched.tls, "w", None)
    if w is None:
        w = Sched.tls.w = Waiter()
        if Sched.main is None: Sched.main = w
    return w

def _claim():
    while Sched.pending:
        g = Sched.pending[0]
        if g.next < len(g.fns):
            i = g.next; g.next += 1
            if g.next == len(g.fns): Sched.pending.pop(0)
            return g, i
        Sched.pending.pop(0)
    return None, None

def _run_item(g, i):
    t = g.tasks[i]; saved = TS.cur; TS.cur = t
    ok = False
    try:
        if not task_cancelled(t):          # 开始前就被取消的项直接跳过
            g.results[i] = g.fns[i](); ok = True
    except Cancelled:
        pass
    except FatalError:
        raise
    except KiteError as e:      # 结构化错误传播：记下第一个错误，取消兄弟任务，父任务等全部停下后重新抛出
        if g.error is None: g.error = e
        cancel_tasks([x for j, x in enumerate(g.tasks) if j != i])
    finally:
        t.done = True; TS.cur = saved
    if ok and g.race and g.winner is None:
        g.winner = i
        cancel_tasks([x for j, x in enumerate(g.tasks) if j != i])
    g.remaining -= 1
    if g.remaining == 0 and g.joiner is not None:
        Sched.ready.append(g.joiner); g.joiner = None

def _helper(g, i):
    Sched.tls.w = Waiter()
    try:
        while g is not None:
            _run_item(g, i)
            g, i = _claim()
    except BaseException as e:   # 运行时错误是致命的：交给主线程抛出
        Sched.fatal = e if isinstance(e, KiteError) else KiteError(f"任务出错: {e}")
        Sched.main.ev.set(); return
    _pass_baton(None)

import heapq, time as _time
_tseq = [0]
def _fire_timers(force=False):
    """触发到期的定时器；force 时若没有到期的就睡到最早的那个"""
    ts = Sched.timers
    while ts and not (ts[0][2].waiting and ts[0][2].tok is ts[0][3]): heapq.heappop(ts)
    if force and ts:
        d = ts[0][0] - _time.monotonic()
        if d > 0: _time.sleep(d)
    now = _time.monotonic(); fired = False
    while ts and (ts[0][0] <= now or not (ts[0][2].waiting and ts[0][2].tok is ts[0][3])):
        _, _, w, tok, idx = heapq.heappop(ts)
        if w.waiting and w.tok is tok:
            _fire(w, idx, 3); fired = True
    return fired

def add_timer(w, ms, idx):
    tok = object(); w.tok = tok; _tseq[0] += 1
    heapq.heappush(Sched.timers, (_time.monotonic() + max(ms, 0) / 1000.0, _tseq[0], w, tok, idx))

def _pass_baton(me):
    """当前线程让出接力棒：交给就绪任务，或为未开始的项启动线程，或等最早的定时器；都没有就是死锁"""
    if Sched.timers: _fire_timers()
    while True:
        if Sched.ready:
            w = Sched.ready.popleft()
            if w is me: return False      # 自己已经就绪（例如定时器到期）：不用等
            w.ev.set(); return True
        g, i = _claim()
        if g is not None:
            th = threading.Thread(target=_helper, args=(g, i), daemon=True)
            th.start(); return True
        if Sched.timers and _fire_timers(force=True): continue
        break
    err = FatalError(DEADLOCK_ERR)
    if me is None or me is not Sched.main:
        Sched.fatal = err; Sched.main.ev.set()
        if me is None: return True
    raise err

def block(me, cancellable=True):
    """当前任务阻塞（已登记在某个等待队列里），直到被唤醒。可取消的等待在任务被取消时立即返回并抛出 Cancelled"""
    me.task = TS.cur
    if cancellable:
        if Sched.cancels and task_cancelled(TS.cur):
            me.waiting = False; TS.cur.unwinding = True; raise Cancelled()
        Sched.blocked.add(me)
    try:
        if _pass_baton(me):
            me.ev.wait(); me.ev.clear()
    finally:
        Sched.blocked.discard(me)
    if Sched.fatal is not None and me is Sched.main:
        e, Sched.fatal = Sched.fatal, None
        raise e
    TS.cur = me.task
    if cancellable and me.status == 4:
        TS.cur.unwinding = True; raise Cancelled()

def cancel_tasks(ts):
    """取消这些任务（及其所有后代）：被阻塞的立刻唤醒，运行中的在下一个取消点展开"""
    for t in ts: t.cancelled = True
    Sched.cancels += 1
    for w in list(Sched.blocked):
        if w.waiting and task_cancelled(w.task):
            _fire(w, w.fired, 4)

def cancel_point():
    """取消点：函数入口、循环回边。被取消就抛 Cancelled；有定时器时偶尔检查是否到期并让出"""
    t = TS.cur
    if Sched.cancels and task_cancelled(t):
        t.unwinding = True; raise Cancelled()
    if Sched.timers:
        Sched.ticks += 1
        if Sched.ticks & 255 == 0 and Sched.timers[0][0] <= _time.monotonic():
            _fire_timers(); yield_now()

def yield_now():
    """让出一下：有就绪任务就排到它后面；没有但有未开始的项就为它启动线程。select 和落空的 try_recv
    都会调用，这样用 _ 分支轮询的循环不会在协作式调度下饿死其他任务"""
    me = _me(); me.task = TS.cur          # 必须在交出接力棒之前保存
    if Sched.ready:
        nxt = Sched.ready.popleft(); me.waiting = False; Sched.ready.append(me); nxt.ev.set()
    elif Sched.pending:
        g, i = _claim()
        if g is None: return
        me.waiting = False; Sched.ready.append(me)
        threading.Thread(target=_helper, args=(g, i), daemon=True).start()
    else:
        return
    me.ev.wait(); me.ev.clear()
    if Sched.fatal is not None and me is Sched.main:
        e, Sched.fatal = Sched.fatal, None
        raise e
    TS.cur = me.task

def k_sleep(ms):
    if type(ms) is not int: raise KiteError("sleep 需要 Int（毫秒）")
    me = _me(); me.waiting = True
    add_timer(me, ms, 0)
    block(me)
    return Unit

def spawn_all(fns, race=False):
    """结构化并发：按顺序执行各项（可能被通道阻塞而切换），全部完成后返回 Group（结果在 .results）"""
    me = _me()
    g = Group(fns, race)
    if not fns: return g
    g.next = 1
    if len(fns) > 1: Sched.pending.insert(0, g)
    try:
        _run_item(g, 0)
        while g.next < len(fns):           # 父任务继续认领自己的项
            i = g.next; g.next += 1
            if g.next == len(fns) and g in Sched.pending: Sched.pending.remove(g)
            _run_item(g, i)
    finally:
        if g in Sched.pending: Sched.pending.remove(g)
    if g.remaining:
        g.joiner = me; block(me, cancellable=False)
    if g.error is not None:
        raise g.error
    if Sched.cancels and task_cancelled(TS.cur):
        TS.cur.unwinding = True; raise Cancelled()
    return g

def race_all(fns):
    """race：各项并发执行，第一个正常完成的胜出，其余被取消；等所有项都结束后返回 (胜者下标, 结果)"""
    g = spawn_all(fns, race=True)
    return g.winner, g.results[g.winner]


ACTOR_MAILBOX = 64

class Chan:
    """通道：chan() 无缓冲（发送方等到有人接收），chan(n) 有 n 个缓冲位"""
    __slots__ = ("cap", "buf", "closed", "recvq", "sendq")
    def __init__(self, cap=0):
        if type(cap) is not int or cap < 0: raise KiteError("chan 的容量必须是非负整数")
        self.cap = cap; self.buf = collections.deque(); self.closed = False
        self.recvq = collections.deque(); self.sendq = collections.deque()

    def can_recv(self): return bool(self.buf) or _has_live(self.sendq) or self.closed
    def can_send(self):
        if self.closed: raise KiteError("向已关闭的通道发送")
        return _has_live(self.recvq) or len(self.buf) < self.cap

    def send(self, v):
        if self.closed: raise KiteError("向已关闭的通道发送")
        r = _pop_live(self.recvq)
        if r: _fire(r.w, r.idx, 1, v); return
        if len(self.buf) < self.cap:
            self.buf.append(v); return
        me = _me(); me.waiting = True
        self.sendq.append(QN(me, 0, v))
        block(me)
        if me.status == 2: raise KiteError("向已关闭的通道发送")

    def recv(self):
        """返回 (有值, 值)"""
        if self.buf:
            v = self.buf.popleft()
            s = _pop_live(self.sendq)
            if s: self.buf.append(s.val); _fire(s.w, s.idx, 1)
            return True, v
        s = _pop_live(self.sendq)
        if s:
            _fire(s.w, s.idx, 1); return True, s.val
        if self.closed: return False, None
        me = _me(); me.waiting = True
        self.recvq.append(QN(me, 0, None))
        block(me)
        return (True, me.val) if me.status == 1 else (False, None)

    def try_recv(self):
        if self.buf or _has_live(self.sendq): return self.recv()
        yield_now()
        return False, None

    def close(self):
        if self.closed: raise KiteError("通道已经关闭")
        self.closed = True
        while (n := _pop_live(self.recvq)): _fire(n.w, n.idx, 2)
        while (n := _pop_live(self.sendq)): _fire(n.w, n.idx, 2)

    def __iter__(self):
        while True:
            ok, v = self.recv()
            if not ok: return
            yield v


def k_select(ops, timeout, has_default):
    """ops: [(0 收 | 1 发, 通道, 值)]；返回 (分支号, 值)。按书写顺序取第一个就绪的分支；
    都没就绪时：有默认分支返回 (len(ops), ())；否则同时在所有通道上等待（可带超时）"""
    for i, (kind, ch, v) in enumerate(ops):
        if not isinstance(ch, Chan): raise KiteError(f"select 需要通道，实际是 {type_name(ch)}")
        if kind == 0:
            if ch.can_recv():
                ok, x = ch.recv(); yield_now(); return (i, Some(x) if ok else NoneV)
        elif kind == 1 and ch.can_send():
            ch.send(v); yield_now(); return (i, Unit)
    n = len(ops)
    if has_default or timeout == 0:
        yield_now(); return (n, Unit)
    me = _me(); me.waiting = True
    nodes = []
    for i, (kind, ch, v) in enumerate(ops):
        if kind < 0: continue
        q = QN(me, i, v); nodes.append((ch.recvq if kind == 0 else ch.sendq, q)); nodes[-1][0].append(q)
    if timeout > 0: add_timer(me, timeout, n)
    try:
        block(me)
    finally:
        for q, node in nodes:
            try: q.remove(node)
            except ValueError: pass
    i = me.fired
    if me.status == 3: return (n, Unit)
    kind = ops[i][0]
    if kind == 0: return (i, Some(me.val) if me.status == 1 else NoneV)
    if me.status == 2: raise KiteError("向已关闭的通道发送")
    return (i, Unit)


def append_pattern(s):
    """xs = push(xs, e) / xs += e / xs = xs + e 返回 (是否拼接, e)，否则 False"""
    t, v = s.target, s.value
    if t.kind != "Name": return False
    if (s.op == "=" and v.kind == "Call" and v.fn.kind == "Name" and v.fn.name == "push" and len(v.args) == 2
            and v.args[0].kind == "Name" and v.args[0].name == t.name and not writes_any(v.args[1])):
        return (False, v.args[1])
    if s.op == "+=" and not writes_any(v): return (True, v)
    if (s.op == "=" and v.kind == "Bin" and v.op == "+" and v.left.kind == "Name" and v.left.name == t.name
            and not writes_any(v.right)):
        return (True, v.right)
    return False


class Env:
    __slots__ = ("vars", "parent")
    def __init__(self, parent=None): self.vars, self.parent = {}, parent
    def define(self, name, val, mutable=False): self.vars[name] = [val, mutable, TS.cur]
    def find(self, name):
        e = self
        while e:
            if name in e.vars: return e.vars[name]
            e = e.parent
        return None

# ───────────────────────── 解释器 ─────────────────────────
class Interp:
    def __init__(self, typecheck=True):
        sched_reset()
        self.checker = None
        if typecheck:
            sys.modules.setdefault("kite", sys.modules[__name__])
            from typecheck import Checker
            self.checker = Checker()
        self.g = Env()
        self.methods = {}   # 类型名 → {方法名: KFunc}
        self.traits = {}
        self.variants = {}  # 变体名 → (EnumType, fields)
        self.out = sys.stdout
        self._install_builtins()
        self.bpush = self.g.vars["push"][0]

    # 内建
    def _install_builtins(self):
        def call(f, *a): return self.call(f, list(a), None)
        def kprint(*a): print(" ".join(show(x) for x in a), file=self.out); return Unit
        def to_list(x):
            if isinstance(x, (list, tuple)): return list(x)
            if isinstance(x, str): return list(x)
            if isinstance(x, PMap): return x.items()
            if isinstance(x, PSet): return x.keys()
            if isinstance(x, range): return list(x)
            raise KiteError(f"{type_name(x)} 不可迭代")
        def k_reduce(xs, init, f):
            acc = init
            for x in to_list(xs): acc = call(f, acc, x)
            return acc
        def k_sum(xs):
            xs = to_list(xs)
            acc = 0
            for x in xs: acc = ck(acc + x)
            return acc
        def k_get(xs, i):
            if isinstance(xs, PMap): return Some(xs[i]) if i in xs else NoneV
            return Some(xs[i]) if -len(xs) <= i < len(xs) else NoneV
        def k_first(xs):
            xs = to_list(xs); return Some(xs[0]) if xs else NoneV
        def k_last(xs):
            xs = to_list(xs); return Some(xs[-1]) if xs else NoneV
        def k_find(xs, f):
            for x in to_list(xs):
                if call(f, x) is True: return Some(x)
            return NoneV
        def k_parse_int(s):
            try: r = int(s.strip())
            except ValueError: return Err(f"无法解析为整数: {s!r}")
            return Ok(r) if I64_MIN <= r <= I64_MAX else Err(f"无法解析为整数: {s!r}")
        def k_parse_float(s):
            try: return Ok(float(s.strip()))
            except ValueError: return Err(f"无法解析为浮点数: {s!r}")
        def k_unwrap(v):
            if isinstance(v, EnumVal) and v.variant in ("Ok", "Some"): return v.values[0]
            raise KiteError(f"unwrap 失败: {show(v)}")
        def k_unwrap_or(v, d):
            return v.values[0] if isinstance(v, EnumVal) and v.variant in ("Ok", "Some") else d
        def shift_n(n):
            if not 0 <= n < 64: raise KiteError(f"移位位数超出范围: {n}")
            return n
        def k_shl(a, n):
            r = (a << shift_n(n)) & 0xFFFFFFFFFFFFFFFF   # 移出 64 位的部分丢弃（同 C / Rust）
            return r - (1 << 64) if r >= 1 << 63 else r
        def k_shr(a, n): return a >> shift_n(n)          # 算术右移
        def k_slice(x, a, b, mode):
            if not isinstance(x, (str, list)): raise KiteError(f"{type_name(x)} 不支持切片")
            n = len(x)
            i = a + n if a < 0 else a
            j = n if mode == 2 else (b + n if b < 0 else b) + (1 if mode == 1 else 0)
            if not (0 <= i <= j <= n):
                rng = f"{a}.." if mode == 2 else f"{a}{'..=' if mode == 1 else '..'}{b}"
                raise KiteError(f"切片越界: {rng}，长度 {n}")
            return x[i:j]
        def k_panic(msg=""): raise KiteError(f"panic: {show(msg)}")
        def k_assert(c, msg="断言失败"):
            if c is not True: raise KiteError(f"assert: {show(msg)}")
            return Unit
        def chk_chan(c, name):
            if not isinstance(c, Chan): raise KiteError(f"{name} 需要通道，实际是 {type_name(c)}")
            return c
        def k_timeout(ms, f):
            if type(ms) is not int: raise KiteError("timeout 需要 Int（毫秒）")
            w, v = race_all([lambda: k_sleep(ms), lambda: call(f)])
            return Some(v) if w == 1 else NoneV
        def k_send(c, v): chk_chan(c, "send").send(v); return Unit
        # Actor：邮箱（有界通道）+ 独占状态的处理任务，生命周期限定在 body 内（结构化）
        def k_actor(init, handler, body):
            mb = Chan(ACTOR_MAILBOX)
            def loop():
                st = init
                while True:
                    ok, m = mb.recv()
                    if not ok: return st
                    st = call(handler, st, m)
            def run_body():
                try: return call(body, mb)
                finally:
                    if not mb.closed: mb.close()
            st, r = spawn_all([loop, run_body]).results
            return (r, st)
        def restart_log(who, n, mx, msg, raw=True):
            if raw: msg = classify_panic(msg)[1]
            sys.stdout.flush()
            sys.stderr.write(f"{who}：第 {n} 次重启（最多 {mx} 次），原因：{msg}\n"); sys.stderr.flush()
        def k_recover_raw(f):
            try: return Ok(call(f))
            except FatalError: raise
            except KiteError as e:
                k, m = classify_panic(e.msg)
                return Err((k, m, panic_location(e)))
        def k_repanic(raw, loc): raise Reraised(raw, loc)
        def k_restart_log(who, n, mx, msg): restart_log(who, n, mx, msg, raw=False); return Unit
        def k_restart_log_w(who, n, mx, within, wait, msg):
            sys.stdout.flush()
            sys.stderr.write(f"{who}：窗口内第 {n} 次重启（每 {within} 毫秒最多 {mx} 次），等待 {wait} 毫秒，原因：{msg}\n"); sys.stderr.flush()
            return Unit
        def k_supervise(mx, f):
            if type(mx) is not int: raise KiteError("supervise 需要 Int（最多重启次数）")
            n = 0
            while True:
                try: return call(f)
                except FatalError: raise
                except KiteError as e:
                    n += 1
                    if n > mx: raise
                    restart_log("supervise", n, mx, e.msg)
        def k_actor_supervised(init, handler, mx, body):
            if type(mx) is not int: raise KiteError("actor_supervised 需要 Int（最多重启次数）")
            mb = Chan(ACTOR_MAILBOX)
            def loop():
                st, n = init, 0
                while True:
                    ok, m = mb.recv()
                    if not ok: return st
                    try: st = call(handler, st, m)
                    except FatalError: raise
                    except KiteError as e:
                        n += 1
                        if n > mx: raise
                        restart_log("actor", n, mx, e.msg); st = init
            def run_body():
                try: return call(body, mb)
                finally:
                    if not mb.closed: mb.close()
            st, r = spawn_all([loop, run_body]).results
            return (r, st)
        def k_tell(a, m):
            c = chk_chan(a, "tell")
            if c.closed: raise KiteError("actor 已经停止，不能再发消息")
            c.send(m); return Unit
        def k_ask(a, mk):
            c = chk_chan(a, "ask")
            if c.closed: raise KiteError("actor 已经停止，不能再发消息")
            reply = Chan(1)
            c.send(call(mk, reply))
            ok, v = reply.recv()
            if not ok: raise KiteError("ask：actor 关闭了回复通道而没有回复")
            return v
        def k_recv(c):
            ok, v = chk_chan(c, "recv").recv()
            return Some(v) if ok else NoneV
        def k_try_recv(c):
            ok, v = chk_chan(c, "try_recv").try_recv()
            return Some(v) if ok else NoneV
        def k_close(c): chk_chan(c, "close").close(); return Unit
        def k_assert_eq(a, b, msg=None):
            if not self.binop("==", a, b, None):
                extra = f"（{show(msg)}）" if msg is not None else ""
                raise KiteError(f"assert_eq 失败{extra}: 左边 {show(a, True)}，右边 {show(b, True)}")
            return Unit
        def k_assert_ne(a, b, msg=None):
            if self.binop("==", a, b, None):
                extra = f"（{show(msg)}）" if msg is not None else ""
                raise KiteError(f"assert_ne 失败{extra}: 两边都是 {show(a, True)}")
            return Unit
        def k_sort(xs, key=None):
            xs = to_list(xs)
            return sorted(xs, key=(lambda x: call(key, x)) if key else None)
        def k_repeat(x, n):
            if n < 0: raise KiteError(f"[x; n] 的次数不能为负: {n}")
            return [x] * n
        def k_minmax(name, op, a):
            xs = to_list(a[0]) if len(a) == 1 else list(a)
            if not xs: raise KiteError(f"{name}: 空序列")
            return op(xs)
        def k_sort_with(xs, f):
            def c(a, b):
                r = call(f, a, b)
                if type(r) is not int or type(r) is bool: raise KiteError("sort_with 的比较函数必须返回 Int")
                return r
            return sorted(to_list(xs), key=functools.cmp_to_key(c))
        def fs_try(p, act):   # 错误信息统一为 "路径: strerror"（与原生后端一致）
            try: return Ok(act())
            except OSError as e: return Err(f"{p}: {e.strerror}")
        def fs_read(p):
            def act():
                with open(p, encoding="utf-8", newline="") as f: return f.read()
            return fs_try(p, act)
        def fs_put(p, s, mode):
            def act():
                with open(p, mode, encoding="utf-8", newline="") as f: f.write(s)
                return Unit
            return fs_try(p, act)
        def fs_mkdir(p):
            def act():
                os.makedirs(p, exist_ok=True); return Unit
            return fs_try(p, act)
        def fs_remove(p):
            def act():
                if os.path.isdir(p) and not os.path.islink(p): os.rmdir(p)
                else: os.remove(p)
                return Unit
            return fs_try(p, act)
        def fs_rename(a, b):
            def act():
                os.rename(a, b); return Unit
            return fs_try(a, act)
        def read_line():
            line = sys.stdin.readline()
            return Some(line.rstrip("\n")) if line else NoneV
        def k_remove(m, k):
            if not isinstance(m, (PMap, PSet)): raise KiteError("remove 只能用于 Map 或 Set")
            return m.remove(k)
        def need_set(x, f):
            if not isinstance(x, PSet): raise KiteError(f"{f} 需要 Set")
            return x
        def k_insert(s, x): return need_set(s, "insert").insert(x)
        def k_union(a, b):
            need_set(a, "union"); need_set(b, "union")
            m = a._m
            for x in b.keys():
                if x not in m: m = m.set(x, Unit)
            return PSet(m)
        def k_intersect(a, b):
            need_set(a, "intersect"); need_set(b, "intersect")
            return PSet.of([x for x in a.keys() if x in b])
        def k_difference(a, b):
            need_set(a, "difference"); need_set(b, "difference")
            return PSet.of([x for x in a.keys() if x not in b])
        def k_is_subset(a, b):
            need_set(a, "is_subset"); need_set(b, "is_subset")
            return all(x in b for x in a.keys())
        def k_set(m, k, v):
            if isinstance(m, PMap): return m.set(k, v)
            if isinstance(m, list): l = list(m); l[k] = v; return l
            raise KiteError("set 只能用于 List 或 Map")
        B = {
            "print": kprint,
            "len": lambda x: len(x),
            "str": lambda x: show(x),
            "__fmt": fmt_value,
            "__repeat": k_repeat,
            "int": lambda x: f2i(x) if type(x) is float else ck(int(x)),
            "float": lambda x: float(x),
            "type_of": type_name,
            "map": lambda xs, f: [call(f, x) for x in to_list(xs)],
            "par_map": lambda xs, f: spawn_all([lambda x=x: call(f, x) for x in to_list(xs)]).results,
            "__race": lambda fs: race_all([lambda f=f: call(f) for f in fs])[1],
            "timeout": k_timeout,
            "actor": k_actor, "tell": k_tell, "ask": k_ask,
            "__recover_raw": k_recover_raw, "__repanic": k_repanic, "__restart_log": k_restart_log, "__restart_log_w": k_restart_log_w, "supervise": k_supervise, "actor_supervised": k_actor_supervised,
            "chan": lambda n=0: Chan(n),
            "send": k_send, "recv": k_recv, "try_recv": k_try_recv, "close": k_close,
            "__select": k_select, "sleep": k_sleep,
            "now_ms": lambda: int(_time.monotonic() * 1000),
            "filter": lambda xs, f: [x for x in to_list(xs) if call(f, x) is True],
            "each": lambda xs, f: ([call(f, x) for x in to_list(xs)], Unit)[1],
            "reduce": k_reduce, "fold": k_reduce,
            "sum": k_sum,
            "count": lambda xs, f=None: len(to_list(xs)) if f is None else sum(1 for x in to_list(xs) if call(f, x) is True),
            "any": lambda xs, f: any(call(f, x) is True for x in to_list(xs)),
            "all": lambda xs, f: all(call(f, x) is True for x in to_list(xs)),
            "find": k_find,
            "first": k_first, "last": k_last, "get": k_get,
            "take": lambda xs, n: to_list(xs)[:n],
            "skip": lambda xs, n: to_list(xs)[n:],
            "reverse": lambda xs: to_list(xs)[::-1] if not isinstance(xs, str) else xs[::-1],
            "sort": k_sort, "sort_by": k_sort, "sort_with": k_sort_with,
            "zip": lambda a, b: list(zip(to_list(a), to_list(b))),
            "enumerate": lambda xs: list(enumerate(to_list(xs))),
            "push": lambda xs, x: to_list(xs) + [x],
            "concat": lambda a, b: to_list(a) + to_list(b),
            "contains": lambda xs, x: x in xs,
            "join": lambda xs, sep="": sep.join(show(x) for x in to_list(xs)),
            "split": lambda s, sep=None: s.split(sep) if sep else s.split(),
            "trim": lambda s: s.strip(),
            "upper": lambda s: s.upper(), "lower": lambda s: s.lower(),
            "starts_with": lambda s, p: s.startswith(p),
            "ends_with": lambda s, p: s.endswith(p),
            "replace": lambda s, a, b: s.replace(a, b),
            "chars": lambda s: list(s),
            "parse_int": k_parse_int, "parse_float": k_parse_float,
            "remove": k_remove,
            "keys": lambda m: list(m.keys()), "values": lambda m: list(m.values()),
            "set": k_set,
            "Set": lambda xs=None: PSet.of(to_list(xs)) if xs is not None else PSet(),
            "__set_lit": lambda xs: PSet.of(xs), "__set_union": k_union, "__set_intersect": k_intersect,
            "__set_difference": k_difference,
            "insert": k_insert, "union": k_union, "intersect": k_intersect,
            "difference": k_difference, "is_subset": k_is_subset, "to_list": to_list,
            "Map": lambda pairs=None: PMap.of(to_list(pairs)) if pairs is not None else PMap(),
            "min": lambda *a: k_minmax("min", min, a),
            "max": lambda *a: k_minmax("max", max, a),
            "abs": lambda x: ck(abs(x)),
            "Ok": Ok, "Err": Err, "Some": Some,
            "unwrap": k_unwrap, "unwrap_or": k_unwrap_or,
            "is_ok": lambda v: v.variant == "Ok", "is_err": lambda v: v.variant == "Err",
            "is_some": lambda v: v.variant == "Some", "is_none": lambda v: v.variant == "None",
            "panic": k_panic, "assert": k_assert, "assert_eq": k_assert_eq, "assert_ne": k_assert_ne,
            "__kite_test": lambda: os.environ.get("KITE_TEST", ""),
            "__env_get": lambda k: Some(os.environ[k]) if k in os.environ else NoneV,
            "__args": lambda: list(PROG_ARGS),
            "__exit": k_exit, "__run": k_run,
            "__ord": lambda s: ord(s[0]) if s else -1,
            "__chr": lambda c: chr(c) if 0 <= c <= 0x10FFFF and not 0xD800 <= c <= 0xDFFF else "\ufffd",
            "__unix_ms": lambda: int(_time.time() * 1000),
            "__slice": k_slice,
            "__band": lambda a, b: a & b, "__bor": lambda a, b: a | b, "__bxor": lambda a, b: a ^ b,
            "__bnot": lambda a: ~a, "__shl": k_shl, "__shr": k_shr,
            "__str_slice": lambda s, a, b: s[max(a, 0):max(b, max(a, 0))],
            "__str_find": lambda s, p, st: -1 if max(st, 0) > len(s) else s.find(p, max(st, 0)),
            **NET_BUILTINS,
        }
        for k, f in B.items():
            self.g.define(k, Builtin(k, f))
        self.g.define("None", NoneV)
        for name, en in (("Result", ("Ok", "Err")), ("Option", ("Some", "None"))):
            et = EnumType(name)
            for vn in en:
                et.variants[vn] = None
                self.variants[vn] = (et, None)
            self.g.define(name, et)
        self.std = {
            "io": {"print": kprint, "println": kprint, "read_line": read_line},
            "fs": {"read": fs_read, "write": lambda p, s: fs_put(p, s, "w"), "append": lambda p, s: fs_put(p, s, "a"),
                   "exists": os.path.exists, "is_file": os.path.isfile, "is_dir": os.path.isdir,
                   "size": lambda p: fs_try(p, lambda: os.stat(p).st_size),
                   "list": lambda p: fs_try(p, lambda: sorted(os.listdir(p))),
                   "mkdir": fs_mkdir, "remove": fs_remove, "rename": fs_rename},
            "ptr": _ptr_module(),
            "math": _math_module(),
        }

    # 入口
    def run(self, src, call_main=True):
        items = with_prelude(src if isinstance(src, list) else Parser(lex(src)).program())
        if self.checker is not None:
            errs = self.checker.check(items)
            if errs:
                errs.sort(key=lambda e: e.line or 0)
                raise CheckFailed(errs)
        erase_typeapps(items)
        self.declare(items, self.g)
        result = Unit
        for it in items:
            if it.kind not in ("Fn", "Struct", "Enum", "Trait", "Impl", "Alias", "Use", "Mod"):
                result = self.exec(it, self.g)
        main = self.g.find("main")
        if call_main and main and isinstance(main[0], KFunc):
            self.call(main[0], [], None)
        return result

    def declare(self, items, env):
        for it in items:
            k = it.kind
            if k == "Fn":
                if getattr(it, "extern", None) is not None:
                    for nm, b in make_externs(it, lambda f, a, _ln=it.line: self.call(f, a, _ln)):
                        env.define(nm, b)
                else:
                    env.define(it.name, KFunc(it.name, it.params, it.body, env))
            elif k == "Struct":
                env.define(it.name, StructType(it.name, [f for f, _ in it.fields]))
            elif k == "Enum":
                et = EnumType(it.name)
                for vn, fs in it.variants:
                    et.variants[vn] = fs
                    self.variants[vn] = (et, fs)
                    if not fs:
                        env.define(vn, EnumVal(it.name, vn, ()))
                env.define(it.name, et)
            elif k == "Trait":
                self.traits[it.name] = it.methods
            elif k == "Use":
                p = it.path
                if p[0] == "std" and len(p) >= 2:
                    for e in std_use_entries(it):
                        mod = self._std_mod(e[1], it.line)
                        if e[0] == "mod":
                            env.define(e[2], Module(e[1], {k: self._wrap(k, v) for k, v in mod.items()}))
                        elif e[0] == "all":
                            for nm, v in mod.items(): env.define(nm, self._wrap(nm, v))
                        else:
                            env.define(e[3], self._wrap(e[2], mod[e[2]]))
        for it in items:  # impl 在类型定义之后处理
            if it.kind == "Impl":
                tbl = self.methods.setdefault(it.target, {})
                if it.trait and it.trait in self.traits:
                    for m in self.traits[it.trait]:
                        if m.body is not None:
                            tbl.setdefault(m.name, KFunc(m.name, m.params, m.body, env))
                for m in it.methods:
                    tbl[m.name] = KFunc(m.name, m.params, m.body, env)

    def _std_mod(self, name, line):
        if name not in self.std:
            raise KiteError(f"未知标准库模块 std.{name}", line)
        return self.std[name]

    def _wrap(self, name, v):
        return Builtin(name, v) if callable(v) else v

    def _use_std(self, name, env, line):
        mod = self._std_mod(name, line)
        env.define(name, Module(name, {k: self._wrap(k, v) for k, v in mod.items()}))

    # 语句
    def exec_block(self, block, env):
        local = Env(env)
        self.declare([s for s in block.stmts if s.kind in ("Fn", "Struct", "Enum", "Trait", "Impl", "Use")], local)
        result, defers = Unit, []
        try:
            for s in block.stmts:
                if s.kind == "Defer":
                    defers.append(s.body); result = Unit; continue
                result = self.exec(s, local)
        finally:
            for d in reversed(defers):
                self.eval(d, local)
        return result

    def exec(self, s, env):
        k = s.kind
        if k == "ExprStmt":
            return self.eval(s.expr, env)
        if k == "Let":
            v = self.eval(s.value, env)
            if not self.match(s.pat, v, env, s.mutable):
                raise KiteError(f"let 解构失败: {show(v)}", s.line)
            return Unit
        if k == "Assign":
            return self.assign(s, env)
        if k == "Return":
            raise ReturnSig(Unit if s.value is None else self.eval(s.value, env))
        if k == "Break":
            v = getattr(s, "value", None)
            raise BreakSig(getattr(s, "label", None), Unit if v is None else self.eval(v, env))
        if k == "Continue": raise ContinueSig(getattr(s, "label", None))
        if k == "While":
            lab = getattr(s, "label", None)
            while s.cond is None or self.truthy(self.eval(s.cond, env), s.line):
                if Sched.cancels or Sched.timers: cancel_point()
                try: self.exec_block(s.body, env)
                except BreakSig as b:
                    if b.label is not None and b.label != lab: raise
                    break
                except ContinueSig as c:
                    if c.label is not None and c.label != lab: raise
                    continue
            return Unit
        if k == "For":
            it = self.eval(s.iter, env)
            seq = it.items() if isinstance(it, PMap) else it.keys() if isinstance(it, PSet) else it
            for x in seq:
                if Sched.cancels or Sched.timers: cancel_point()
                loop_env = Env(env)
                if not self.match(s.pat, x, loop_env):
                    raise KiteError("for 模式不匹配", s.line)
                try: self.exec_block(s.body, loop_env)
                except BreakSig as b:
                    if b.label is not None and b.label != getattr(s, "label", None): raise
                    break
                except ContinueSig as c:
                    if c.label is not None and c.label != getattr(s, "label", None): raise
                    continue
            return Unit
        if k in ("Fn", "Struct", "Enum", "Trait", "Impl", "Use", "Mod", "Alias"):
            return Unit
        raise KiteError(f"未知语句 {k}", s.line)

    def assign(self, s, env):
        t = s.target
        fast = getattr(s, "_fast", None)
        if fast is None:
            fast = s._fast = append_pattern(s)
        if fast:
            slot = env.find(t.name)
            if slot is not None and slot[1] and owner_ok(slot[2]):
                cat, rhs = fast
                if cat or env.find("push")[0] is self.bpush:
                    x = self.eval(rhs, env)
                    lst = slot[0]
                    if type(lst) is list and sys.getrefcount(lst) == 3 and (not cat or type(x) is list):
                        if cat: lst.extend(x)
                        else: lst.append(x)
                    elif cat:
                        slot[0] = self.binop("+", lst, x, s.line)
                    else:
                        slot[0] = self.bpush.fn(lst, x)
                    return Unit
        val = self.eval(s.value, env)
        # xs[i] = v（xs 是局部 var 且列表没有别的引用）：原地写入
        if s.op == "=" and t.kind == "Index" and t.obj.kind == "Name":
            slot = env.find(t.obj.name)
            if slot is not None and slot[1] and owner_ok(slot[2]):
                lst = slot[0]
                if type(lst) is list:
                    idx = self.eval(t.index, env)
                    if sys.getrefcount(lst) == 3:
                        lst[idx] = val
                    else:
                        cp = list(lst); cp[idx] = val; slot[0] = cp
                    return Unit
                lst = None
        if t.kind == "Name":
            slot = env.find(t.name)
            if slot is None:
                raise KiteError(f"未定义的变量 {t.name}", s.line)
            if not slot[1]:
                raise KiteError(f"不能给不可变绑定 `{t.name}` 赋值（用 var 声明）", s.line)
            if not owner_ok(slot[2]):
                raise KiteError(TASK_WRITE_ERR, s.line)
            slot[0] = self.binop(s.op[0], slot[0], val, s.line) if s.op != "=" else val
            return Unit
        # 字段/下标赋值：要求根变量是 var，按值语义写回
        root = t
        while root.kind in ("Field", "Index"):
            root = root.obj
        if root.kind != "Name":
            raise KiteError("赋值目标无效", s.line)
        slot = env.find(root.name)
        if slot is None or not slot[1]:
            raise KiteError(f"不能修改不可变绑定 `{root.name}`（用 var 声明）", s.line)
        if not owner_ok(slot[2]):
            raise KiteError(TASK_WRITE_ERR, s.line)
        def upd(node, container_val):
            pass
        def set_path(node, new):
            if node.kind == "Name":
                slot[0] = new; return
            parent = self.eval(node.obj, env)
            if node.kind == "Field":
                if not isinstance(parent, StructVal):
                    raise KiteError("只能给结构体字段赋值", s.line)
                nf = dict(parent.fields)
                old = nf.get(node.name)
                nf[node.name] = self.binop(s.op[0], old, new, s.line) if s.op != "=" and node is t else new
                set_path(node.obj, StructVal(parent.tname, nf))
            else:
                idx = self.eval(node.index, env)
                if isinstance(parent, PMap):
                    nv = self.binop(s.op[0], parent[idx], new, s.line) if s.op != "=" and node is t else new
                    set_path(node.obj, parent.set(idx, nv))
                else:
                    cp = list(parent)
                    cp[idx] = self.binop(s.op[0], cp[idx], new, s.line) if s.op != "=" and node is t else new
                    set_path(node.obj, cp)
        set_path(t, val)
        return Unit

    def truthy(self, v, line):
        if not isinstance(v, bool):
            raise KiteError(f"条件必须是 Bool，实际是 {type_name(v)}", line)
        return v

    # 表达式
    def eval(self, e, env):
        k = e.kind
        if k == "Lit": return e.value
        if k == "Name":
            slot = env.find(e.name)
            if slot is None:
                if e.name in self.std:
                    raise KiteError(f"未定义的名字 {e.name}（需要 use std.{e.name}）", e.line)
                raise KiteError(f"未定义的名字 {e.name}", e.line)
            return slot[0]
        if k == "Interp":
            return "".join(p if isinstance(p, str) else show(self.eval(p, env)) for p in e.parts)
        if k == "Bin":
            if e.op == "&&":
                return self.truthy(self.eval(e.left, env), e.line) and self.truthy(self.eval(e.right, env), e.line)
            if e.op == "||":
                return self.truthy(self.eval(e.left, env), e.line) or self.truthy(self.eval(e.right, env), e.line)
            return self.binop(e.op, self.eval(e.left, env), self.eval(e.right, env), e.line)
        if k == "Unary":
            v = self.eval(e.operand, env)
            if e.op == "-":
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise KiteError(f"不能对 {type_name(v)} 取负", e.line)
                if type(v) is int and v == I64_MIN: raise KiteError("整数溢出", e.line)
                return -v
            return not self.truthy(v, e.line)
        if k == "Tuple": return tuple(self.eval(x, env) for x in e.items)
        if k == "List": return [self.eval(x, env) for x in e.items]
        if k == "Block": return self.exec_block(e, env)
        if k == "If":
            if self.truthy(self.eval(e.cond, env), e.line):
                return self.exec_block(e.then, env)
            if e.els is None: return Unit
            return self.eval(e.els, env)
        if k == "Match":
            v = self.eval(e.subject, env)
            for pat, guard, body in e.arms:
                arm_env = Env(env)
                if self.match(pat, v, arm_env) and (guard is None or self.truthy(self.eval(guard, arm_env), e.line)):
                    return self.eval(body, arm_env)
            raise KiteError(f"match 没有覆盖值 {show(v, True)}", e.line)
        if k == "Is":
            return self.match(e.pat, self.eval(e.value, env), Env(env))
        if k == "Lambda":
            return KFunc("<lambda>", e.params, e.body, env, is_expr=e.body.kind != "Block")
        if k == "Call":
            return self.eval_call(e, env)
        if k == "Pipe":
            left = self.eval(e.left, env)
            r = e.right
            if r.kind == "Call":
                return self.eval_call(r, env, pre=[left])
            return self.call(self.eval(r, env), [left], e.line)
        if k == "Field":
            obj = self.eval(e.obj, env)
            return self.get_field(obj, e.name, e.line)
        if k == "TupleIndex":
            obj = self.eval(e.obj, env)
            if isinstance(obj, tuple): return obj[e.index]
            if isinstance(obj, EnumVal): return obj.values[e.index]
            raise KiteError(f"{type_name(obj)} 不能用 .{e.index} 取元素", e.line)
        if k == "Index":
            obj, idx = self.eval(e.obj, env), self.eval(e.index, env)
            try: return obj[idx]
            except (IndexError, KeyError): raise KiteError(f"下标越界: {show(idx, True)}", e.line)
            except TypeError: raise KiteError(f"{type_name(obj)} 不支持下标", e.line)
        if k == "Loop":
            lab = e.label
            while True:
                if Sched.cancels or Sched.timers: cancel_point()
                try: self.exec_block(e.body, env)
                except BreakSig as b:
                    if b.label is not None and b.label != lab: raise
                    return b.value
                except ContinueSig as c:
                    if c.label is not None and c.label != lab: raise
        if k == "Try":
            v = self.eval(e.value, env)
            if not isinstance(v, EnumVal) or v.variant not in ("Ok", "Err", "Some", "None"):
                raise KiteError("? 只能用于 Result 或 Option", e.line)
            if v.variant in ("Ok", "Some"): return v.values[0]
            raise ReturnSig(v)
        if k == "StructLit":
            st = env.find(e.name)
            if st is None or not isinstance(st[0], StructType):
                raise KiteError(f"未知结构体 {e.name}", e.line)
            st = st[0]
            vals = {n: self.eval(x, env) for n, x in e.fields}
            missing = [f for f in st.fields if f not in vals]
            extra = [f for f in vals if f not in st.fields]
            if missing: raise KiteError(f"{e.name} 缺少字段: {', '.join(missing)}", e.line)
            if extra: raise KiteError(f"{e.name} 没有字段: {', '.join(extra)}", e.line)
            return StructVal(e.name, {f: vals[f] for f in st.fields})
        if k == "Select":
            if not hasattr(e, "lowered"): e.lowered = lower_select(e)
            return self.eval(e.lowered, env)
        if k == "Spawn":
            if not e.items: return Unit
            return tuple(spawn_all([lambda x=x: self.eval(x, Env(env)) for x in e.items]).results)
        if k == "Race":
            return race_all([lambda x=x: self.eval(x, Env(env)) for x in e.items])[1]
        if k == "Await":
            return self.eval(e.value, env)
        raise KiteError(f"未知表达式 {k}", e.line)

    def get_field(self, obj, name, line):
        if isinstance(obj, StructVal) and name in obj.fields: return obj.fields[name]
        if isinstance(obj, Module):
            if name in obj.members: return obj.members[name]
            raise KiteError(f"模块 {obj.name} 没有成员 {name}", line)
        if isinstance(obj, EnumType):
            if name not in obj.variants: raise KiteError(f"{obj.name} 没有变体 {name}", line)
            fs = obj.variants[name]
            if not fs:
                return NoneV if name == "None" else EnumVal(obj.name, name, ())
            return self.variant_ctor(obj, name, fs)
        raise KiteError(f"{type_name(obj)} 没有字段 {name}", line)

    def variant_ctor(self, et, vn, fs):
        names = RecNames(f for f, _ in fs) if isinstance(fs, RecFields) else [f for f, _ in fs]
        def ctor(*args):
            if len(args) != len(names):
                raise KiteError(f"{vn} 需要 {len(names)} 个参数，给了 {len(args)} 个")
            return EnumVal(et.name, vn, args, names)
        return Builtin(vn, ctor)

    def eval_call(self, e, env, pre=()):
        args = list(pre) + [self.eval(a, env) for a in e.args]
        fn = e.fn
        if fn.kind == "Field":  # 方法调用 / 模块函数 / UFCS
            obj = self.eval(fn.obj, env)
            if isinstance(obj, (Module, EnumType)):
                return self.call(self.get_field(obj, fn.name, e.line), args, e.line)
            tbl = self.methods.get(type_name(obj), {})
            if fn.name in tbl:
                return self.call(tbl[fn.name], [obj] + args, e.line)
            if isinstance(obj, StructVal) and fn.name in obj.fields:
                return self.call(obj.fields[fn.name], args, e.line)
            slot = env.find(fn.name)
            if slot and isinstance(slot[0], (KFunc, Builtin)):
                return self.call(slot[0], [obj] + args, e.line)
            raise KiteError(f"{type_name(obj)} 没有方法 {fn.name}", e.line)
        if fn.kind == "Name":
            slot = env.find(fn.name)
            if slot is None and fn.name in self.variants:
                et, fs = self.variants[fn.name]
                return self.call(self.variant_ctor(et, fn.name, fs or []), args, e.line)
        return self.call(self.eval(fn, env), args, e.line)

    def call(self, f, args, line):
        if isinstance(f, Builtin):
            try:
                return f.fn(*args)
            except KiteError as err:
                if err.line is None: err.line = line; err.args = (f"{line}: {err.msg}",)
                raise
            except (ReturnSig, BreakSig, ContinueSig):
                raise
            except TypeError as err:
                raise KiteError(f"调用 {f.name} 参数错误: {err}", line)
            except ZeroDivisionError:
                raise KiteError("除以零", line)
        if isinstance(f, KFunc):
            if len(args) != len(f.params):
                raise KiteError(f"{f.name} 需要 {len(f.params)} 个参数，给了 {len(args)} 个", line)
            if Sched.cancels or Sched.timers: cancel_point()
            env = Env(f.env)
            for (name, _), a in zip(f.params, args):
                env.define(name, a)
            try:
                return self.eval(f.body, env) if f.is_expr else self.exec_block(f.body, env)
            except ReturnSig as r:
                return r.v
        if isinstance(f, StructType):
            if len(args) != len(f.fields):
                raise KiteError(f"{f.name} 需要 {len(f.fields)} 个字段", line)
            return StructVal(f.name, dict(zip(f.fields, args)))
        raise KiteError(f"{type_name(f)} 不能被调用", line)

    def binop(self, op, a, b, line):
        try:
            if op in ("==",): return a == b and type(a) == type(b) or (a == b and {type(a), type(b)} <= {int, float})
            if op == "!=": return not self.binop("==", a, b, line)
            if op in ("..", "..="):
                if not (isinstance(a, int) and isinstance(b, int)):
                    raise KiteError("区间两端必须是 Int", line)
                return list(range(a, b + 1 if op == "..=" else b))
            num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
            if op == "+":
                if num(a) and num(b): return ck(a + b, line)
                if isinstance(a, str) and isinstance(b, str): return a + b
                if isinstance(a, list) and isinstance(b, list): return a + b
                raise KiteError(f"不能把 {type_name(a)} 和 {type_name(b)} 相加", line)
            if not (num(a) and num(b)):
                if op in ("<", ">", "<=", ">=") and type(a) == type(b) and isinstance(a, str):
                    pass
                else:
                    raise KiteError(f"运算 {op} 不支持 {type_name(a)} 和 {type_name(b)}", line)
            if op == "-": return ck(a - b, line)
            if op == "*": return ck(a * b, line)
            if op == "/":
                if isinstance(a, int) and isinstance(b, int):
                    q = abs(a) // abs(b)
                    return ck(q if (a >= 0) == (b >= 0) else -q, line)
                return a / b
            if op == "%":
                if isinstance(a, int) and isinstance(b, int):
                    r = abs(a) % abs(b)  # 截断取余（符号随被除数），整数精确，不经过浮点
                    return r if a >= 0 else -r
                return math.fmod(a, b)
            if op == "<": return a < b
            if op == ">": return a > b
            if op == "<=": return a <= b
            if op == ">=": return a >= b
        except ZeroDivisionError:
            raise KiteError("除以零", line)
        raise KiteError(f"未知运算符 {op}", line)

    # 模式匹配
    def match(self, p, v, env, mutable=False):
        t = p[0]
        if t == "wild": return True
        if t == "bind":
            env.define(p[1], v, mutable or len(p) > 2); return True
        if t == "at":
            return self.match(p[2], v, env, mutable) and self.match(p[1], v, env, mutable)
        if t == "lit":
            return v == p[1] and type(v) == type(p[1])
        if t == "range":
            return range_ok(p, v)
        if t == "tuple":
            return isinstance(v, tuple) and len(v) == len(p[1]) and all(self.match(sp, sv, env, mutable) for sp, sv in zip(p[1], v))
        if t == "list":
            items, rest = p[1], p[2]
            if not isinstance(v, list): return False
            post = p[3] if len(p) > 3 else []
            if rest is None and len(v) != len(items): return False
            if len(v) < len(items) + len(post): return False
            if not all(self.match(sp, sv, env, mutable) for sp, sv in zip(items, v)): return False
            tail = len(v) - len(post)
            if not all(self.match(sp, sv, env, mutable) for sp, sv in zip(post, v[tail:])): return False
            if rest and rest != "_": env.define(rest, v[len(items):tail], mutable)
            return True
        if t == "variant":
            name, subs = p[1], p[2]
            if not isinstance(v, EnumVal) or v.variant != name:
                if subs is None and name not in self.variants and isinstance(v, StructVal) and v.tname == name:
                    return True
                return False
            if subs is None: return True
            if len(subs) != len(v.values):
                raise KiteError(f"模式 {name} 需要 {len(v.values)} 个字段")
            return all(self.match(sp, sv, env, mutable) for sp, sv in zip(subs, v.values))
        if t == "or":
            return any(self.match(a, v, env, mutable) for a in p[1])
        if t == "struct":
            name, fs = p[1], p[2]
            if isinstance(v, StructVal) and v.tname == name:
                return all(f in v.fields and self.match(sp, v.fields[f], env, mutable) for f, sp in fs)
            if isinstance(v, EnumVal) and v.variant == name and v.names:
                d = dict(zip(v.names, v.values))
                return all(f in d and self.match(sp, d[f], env, mutable) for f, sp in fs)
            return False
        raise KiteError(f"未知模式 {t}")

# ───────────────────────── CLI / REPL ─────────────────────────
def _vm(typecheck=True):
    sys.modules.setdefault("kite", sys.modules[__name__])
    from vm import VM
    return VM(typecheck=typecheck)


USAGE = """用法:
  python3 kite.py <file.kite>              类型检查 → 编译成字节码 → 虚拟机运行
  python3 kite.py check <file.kite>        只做类型检查
  python3 kite.py dis <file.kite>          打印字节码（反汇编）
  python3 kite.py fmt [-w|--check] <文件…>  格式化（-w 写回，--check 只检查）
  python3 kite.py build <file.kite> [-o 输出] [--emit-c x.c]  编译成本机可执行文件
  python3 kite.py new <名字> [--lib]       创建包（kite.toml + src/）
  python3 kite.py add <包>[@约束]          添加依赖（--path <目录> 为本地路径依赖）
  python3 kite.py get | update             按 kite.lock 安装依赖 / 重新解析到最新兼容版本
  python3 kite.py publish                  发布当前库包到仓库（KITE_REGISTRY）
  python3 kite.py test [过滤词] [--native]  运行 test_ 开头的测试函数（包里扫描 src/ 和 tests/）
  python3 kite.py tree                     显示依赖树
  python3 kite.py run | check | build      在包目录里不写文件名时使用 src/main.kite
  python3 kite.py lsp                      启动语言服务器（编辑器调用）
  python3 kite.py --no-check <file.kite>   跳过类型检查
（编译、运行、检查请用 bin/kitec：sh bootstrap.sh 构建；REPL 和 --tree 已废弃）"""


# ── TCP 原语（与 runtime/kite_rt.c 的 b_tcp_* 一一对应）：套接字非阻塞，"会阻塞" 时返回 None ──
NET = {}   # fd → [socket, 读缓冲, 写缓冲, eof]


def _net_err(what, e):
    import ssl
    if isinstance(e, ssl.SSLError): return Err(f"{what}: {_tls_reason(e)}")
    return Err(f"{what}: {os.strerror(e.errno) if getattr(e, 'errno', None) else e}")


def _net_addr(host, port):
    import socket
    try:
        return socket.getaddrinfo(host or "0.0.0.0", None, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except (socket.gaierror, UnicodeError):
        return None


def k_tcp_listen(host, port):
    import socket
    ip = _net_addr(host, port)
    if ip is None: return Err(f"无法解析主机 {host}")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((ip, port)); s.listen(128)
    except OSError as e:
        s.close(); return _net_err(f"无法监听 {host}:{port}", e)
    s.setblocking(False)
    NET[s.fileno()] = [s, bytearray(), bytearray(), False]
    return Ok(s.fileno())


def k_tcp_port(fd):
    st = NET.get(fd)
    try: return st[0].getsockname()[1] if st else -1
    except OSError: return -1


def k_tcp_accept(fd):
    import socket
    st = NET.get(fd)
    if not st: return Err("accept 失败: Bad file descriptor")
    try:
        c, _ = st[0].accept()
    except (BlockingIOError, InterruptedError, ConnectionAbortedError):
        return Ok(NoneV)
    except OSError as e:
        return _net_err("accept 失败", e)
    c.setblocking(False); c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    NET[c.fileno()] = [c, bytearray(), bytearray(), False]
    return Ok(Some(c.fileno()))


def k_tcp_connect(host, port):
    import socket
    ip = _net_addr(host, port)
    if ip is None: return Err(f"无法解析主机 {host}")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((ip, port))
    except OSError as e:
        s.close(); return _net_err(f"无法连接 {host}:{port}", e)
    s.setblocking(False); s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    NET[s.fileno()] = [s, bytearray(), bytearray(), False]
    return Ok(s.fileno())


def _net_fill(st):
    import ssl
    while not st[3]:
        try:
            d = st[0].recv(65536)
        except (BlockingIOError, InterruptedError, ssl.SSLWantReadError, ssl.SSLWantWriteError):
            return None
        except (ssl.SSLZeroReturnError, ssl.SSLEOFError):
            d = b""
        except OSError as e:
            return e
        if not d: st[3] = True
        else: st[1] += d
    return None


def _dec(b):
    return bytes(b).decode("utf-8", "replace")


def k_tcp_read_line(fd):
    st = NET.get(fd)
    if not st: return Err("连接已关闭")
    for p in (0, 1):
        i = st[1].find(b"\n")
        if i >= 0:
            k = i - 1 if i > 0 and st[1][i - 1] == 13 else i
            line = _dec(st[1][:k]); del st[1][:i + 1]
            return Ok(Some(line))
        if p == 0:
            e = _net_fill(st)
            if e is not None: return _net_err("读取失败", e)
    return Err("连接已关闭") if st[3] else Ok(NoneV)


def k_tcp_read_bytes(fd, n):
    st = NET.get(fd)
    if not st: return Err("连接已关闭")
    n = max(n, 0)
    if len(st[1]) < n:
        e = _net_fill(st)
        if e is not None: return _net_err("读取失败", e)
    if len(st[1]) >= n:
        d = _dec(st[1][:n]); del st[1][:n]
        return Ok(Some(d))
    return Err("连接已关闭") if st[3] else Ok(NoneV)


def k_tcp_read_all(fd):
    st = NET.get(fd)
    if not st: return Err("连接已关闭")
    e = _net_fill(st)
    if e is not None: return _net_err("读取失败", e)
    if not st[3]: return Ok(NoneV)
    d = _dec(st[1]); st[1].clear()
    return Ok(Some(d))


def _net_flush(st):
    import ssl
    tls = len(st) > 4
    while st[2]:
        chunk = st[2][:st[4]] if tls and st[4] else st[2]
        try:
            n = st[0].send(chunk)
        except (BlockingIOError, InterruptedError):
            return Ok(False)
        except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
            st[4] = len(chunk)
            return Ok(False)
        except ssl.SSLError as e:
            return Err(f"写入失败: {_tls_reason(e)}")
        except OSError as e:
            return _net_err("写入失败", e)
        if tls: st[4] = 0
        del st[2][:n]
    return Ok(True)


def k_tcp_write(fd, data):
    st = NET.get(fd)
    if not st: return Err("连接已关闭")
    st[2] += data.encode("utf-8")
    return _net_flush(st)


def k_tcp_flush(fd):
    st = NET.get(fd)
    return _net_flush(st) if st else Err("连接已关闭")


def k_tcp_close(fd):
    st = NET.pop(fd, None)
    if st:
        try: st[0].close()
        except OSError: pass
    return Unit


# ── TLS：在已连接的套接字上包一层 ssl；读写仍走上面的非阻塞缓冲（ssl 的 WANT_READ/WRITE 当作"会阻塞"） ──
TLS_CTX = []    # 服务端上下文，id = 下标 + 1

def _tls_reason(e):
    import ssl
    if isinstance(e, ssl.SSLCertVerificationError):
        return "hostname mismatch" if getattr(e, "verify_code", 0) == 62 else str(e.verify_message)
    r = getattr(e, "reason", None)
    if r: return str(r).lower().replace("_", " ")
    if getattr(e, "errno", None) and not isinstance(e, ssl.SSLError): return os.strerror(e.errno)
    return str(e)


def k_tls_server_ctx(cert, key):
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        ctx.load_cert_chain(cert, key)
    except (OSError, ssl.SSLError) as e:
        return Err(f"无法加载证书 {cert}: {_tls_reason(e)}")
    TLS_CTX.append(ctx)
    return Ok(len(TLS_CTX))


def _tls_wrap(fd, ctx, **kw):
    st = NET.get(fd)
    if not st: return Err("连接已关闭")
    try:
        st[0] = ctx.wrap_socket(st[0], do_handshake_on_connect=False, **kw)
    except (OSError, ValueError) as e:
        return Err(f"TLS 握手失败: {_tls_reason(e)}")
    st.append(0)        # st[4]：上次没写完的 TLS 记录长度（重试时必须原样再写）
    return Ok(Unit)


def k_tls_accept(fd, cid):
    return _tls_wrap(fd, TLS_CTX[cid - 1], server_side=True)


def k_tls_client(fd, host, ca, verify):
    import ssl
    try:
        ctx = ssl.create_default_context(cafile=ca or None)
    except (OSError, ssl.SSLError) as e:
        return Err(f"无法加载 CA {ca}: {_tls_reason(e)}")
    if not verify:
        ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    return _tls_wrap(fd, ctx, server_hostname=host)


def k_tls_handshake(fd):
    import ssl
    st = NET.get(fd)
    if not st: return Err("连接已关闭")
    try:
        st[0].do_handshake()
    except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError):
        return Ok(False)
    except (OSError, ssl.SSLError) as e:
        return Err(f"TLS 握手失败: {_tls_reason(e)}")
    return Ok(True)


NET_BUILTINS = {
    "__tls_server_ctx": k_tls_server_ctx, "__tls_accept": k_tls_accept, "__tls_client": k_tls_client,
    "__tls_handshake": k_tls_handshake,
    "__tcp_listen": k_tcp_listen, "__tcp_port": k_tcp_port, "__tcp_accept": k_tcp_accept,
    "__tcp_connect": k_tcp_connect, "__tcp_read_line": k_tcp_read_line, "__tcp_read_bytes": k_tcp_read_bytes,
    "__tcp_read_all": k_tcp_read_all, "__tcp_write": k_tcp_write, "__tcp_flush": k_tcp_flush,
    "__tcp_close": k_tcp_close, "__byte_len": lambda s: len(s.encode("utf-8")),
    "__bytes": lambda s: list(s.encode("utf-8")),
    "__from_bytes": lambda bs: _from_bytes(bs),
    "__eprint": lambda s: _eprint(s),
    "__fs_stat": lambda p, follow: _fs_stat(p, follow), "__getcwd": lambda: os.getcwd(),
    "__chdir": lambda p: _os_try(p, lambda: os.chdir(p)), "__chmod": lambda p, m: _os_try(p, lambda: os.chmod(p, m)),
    "__env_set": lambda k, v: _env_set(k, v), "__env_unset": lambda k: _env_unset(k),
    "__environ": lambda: sorted(f"{k}={v}" for k, v in os.environ.items()),
    "__hostname": lambda: __import__("socket").gethostname(), "__getpid": lambda: os.getpid(),
    "__read_bytes": lambda p: _read_bytes(p), "__write_bytes": lambda p, bs, app: _write_bytes(p, bs, app),
    "__tz_offset": lambda ms: _tz_offset(ms),
    "__random_bytes": lambda n: list(os.urandom(max(n, 0))), "__unix_ns": lambda: _time.time_ns(),
}


def _os_try(p, act):
    try:
        act(); return Ok(Unit)
    except OSError as e: return Err(f"{p}: {e.strerror}")


def _fs_stat(p, follow):
    import stat as _st
    try: st = os.stat(p) if follow else os.lstat(p)
    except OSError as e: return Err(f"{p}: {e.strerror}")
    m = st.st_mode
    kind = 0 if _st.S_ISREG(m) else 1 if _st.S_ISDIR(m) else 2 if _st.S_ISLNK(m) else 3
    return Ok((st.st_size, st.st_mtime_ns // 1000000, kind, m & 0o7777))


def _env_set(k, v):
    os.environ[k] = v; return Unit


def _env_unset(k):
    os.environ.pop(k, None); return Unit


def _read_bytes(p):
    try:
        with open(p, "rb") as f: return Ok(list(f.read()))
    except OSError as e: return Err(f"{p}: {e.strerror}")


def _write_bytes(p, bs, app):
    if any(type(b) is not int or b < 0 or b > 255 for b in bs): raise KiteError("字节必须在 0 到 255 之间")
    try:
        with open(p, "ab" if app else "wb") as f: f.write(bytes(bs))
        return Ok(Unit)
    except OSError as e: return Err(f"{p}: {e.strerror}")


def _tz_offset(ms):
    import time as _t
    return _t.localtime(ms // 1000).tm_gmtoff


# std.math：浮点函数直接调 C 的 libm，和原生后端逐位一致，并且和 Go 一样按 IEEE 754 处理
# 定义域外得到 nan、溢出得到 ±inf，不报错
_LIBM = None


def _libm():
    global _LIBM
    if _LIBM is None:
        import ctypes, ctypes.util
        lib = ctypes.CDLL(ctypes.util.find_library("m") or "libm.so.6")
        for n in ("sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh", "asinh", "acosh", "atanh",
                  "exp", "exp2", "expm1", "log", "log2", "log10", "log1p", "cbrt", "round", "trunc", "floor", "ceil"):
            getattr(lib, n).restype = ctypes.c_double; getattr(lib, n).argtypes = [ctypes.c_double]
        for n in ("pow", "atan2", "hypot", "fmod", "copysign", "remainder"):
            getattr(lib, n).restype = ctypes.c_double; getattr(lib, n).argtypes = [ctypes.c_double, ctypes.c_double]
        _LIBM = lib
    return _LIBM


def _math_module():
    lib = _libm()
    def num(x, who):
        if type(x) is bool or type(x) not in (int, float): raise KiteError(f"{who} 需要数字")
        return float(x)
    def f1(n):
        fn = getattr(lib, n)
        return lambda x: fn(num(x, n))
    def f2(n, cn=None):
        fn = getattr(lib, cn or n)
        return lambda x, y: fn(num(x, n), num(y, n))
    def to_int(n):
        fn = getattr(lib, n)
        return lambda x: x if type(x) is int else f2i(fn(num(x, n)))
    def mlog(x, b=None):
        r = lib.log(num(x, "log"))
        return r if b is None else r / lib.log(num(b, "log"))
    def gcd(a, b): return math.gcd(a, b)
    def lcm(a, b):
        if a == 0 or b == 0: return 0
        r = abs(a // math.gcd(a, b) * b)
        if r > I64_MAX: raise KiteError("整数溢出")
        return r
    m = {n: f1(n) for n in ("sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh", "asinh", "acosh",
                            "atanh", "exp", "exp2", "expm1", "log2", "log10", "log1p", "cbrt")}
    m.update({"pow": f2("pow"), "atan2": f2("atan2"), "hypot": f2("hypot"), "fmod": f2("fmod"),
              "copysign": f2("copysign"), "remainder": f2("remainder"), "log": mlog,
              "floor": to_int("floor"), "ceil": to_int("ceil"), "round": to_int("round"), "trunc": to_int("trunc"),
              "is_nan": lambda x: num(x, "is_nan") != num(x, "is_nan"),
              "is_inf": lambda x: abs(num(x, "is_inf")) == math.inf,
              "signbit": lambda x: math.copysign(1.0, num(x, "signbit")) < 0,
              "gcd": gcd, "lcm": lcm,
              "pi": math.pi, "e": math.e, "tau": math.tau, "phi": (1 + math.sqrt(5)) / 2, "sqrt2": math.sqrt(2),
              "ln2": math.log(2), "ln10": math.log(10), "inf": math.inf, "nan": math.nan,
              "max_float": sys.float_info.max, "smallest_float": 5e-324,
              "max_int": I64_MAX, "min_int": I64_MIN})
    return m


def _from_bytes(bs):
    """UTF-8 字节 → Str；有不在 0..255 的值或不是合法 UTF-8 时为 None"""
    if any(type(b) is not int or b < 0 or b > 255 for b in bs): return NoneV
    try: return Some(bytes(bs).decode("utf-8"))
    except UnicodeDecodeError: return NoneV


def _eprint(s):
    sys.stdout.flush(); sys.stderr.write(s + "\n"); sys.stderr.flush()
    return Unit


PROG_ARGS = []   # 程序自己的命令行参数（kite run 文件 之后的部分），std.env.args() 返回它


def k_exit(code):
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(code)


def k_run(cmd):
    import subprocess
    sys.stdout.flush()
    try:
        r = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE)
    except OSError:
        return (127, "")
    code = r.returncode if r.returncode >= 0 else 128 - r.returncode
    return (code, r.stdout.decode("utf-8", "replace"))


def main(argv):
    if sys.getrecursionlimit() < 20000:
        sys.setrecursionlimit(20000)
    args = argv[1:]
    if not args:
        print(USAGE); return 2
    if "--tree" in args:
        print("--tree（树遍历解释器）已废弃", file=sys.stderr); return 2
    if args[0] in ("-h", "--help"):
        print(USAGE); return 0
    if args[0] == "fmt":
        sys.modules.setdefault("kite", sys.modules[__name__])
        import fmt
        return fmt.main(args[1:])
    if args[0] == "test":
        sys.modules.setdefault("kite", sys.modules[__name__])
        import testing
        return testing.main(args[1:])
    if args[0] in ("new", "add", "get", "update", "publish", "tree"):
        sys.modules.setdefault("kite", sys.modules[__name__])
        import pkg
        return pkg.main(args[0], args[1:])
    if args[0] == "build":
        sys.modules.setdefault("kite", sys.modules[__name__])
        import native, os
        rest = args[1:]
        out = emit = None
        check = True
        files = []
        while rest:
            a = rest.pop(0)
            if a == "-o" and rest: out = rest.pop(0)
            elif a == "--emit-c" and rest: emit = rest.pop(0)
            elif a == "--no-check": check = False
            else: files.append(a)
        if len(files) > 1:
            print(USAGE); return 2
        try:
            path, pkgname = _entry(files)
            out = out or pkgname or os.path.splitext(os.path.basename(path))[0]
            items = _load(path)
            native.build(items, path, out, emit_c=emit, typecheck=check)
        except CheckFailed as e:
            for err in e.errors[:20]:
                print(format_error(err, path), file=sys.stderr)
            return 1
        except KiteError as e:
            print(format_error(e, files[0] if files else None), file=sys.stderr); return 1
        print(f"已生成 {out}")
        return 0
    if args[0] == "lsp":
        sys.modules.setdefault("kite", sys.modules[__name__])
        import lsp
        lsp.Server().serve(); return 0
    mode, engine, check = "run", "vm", True
    while args and args[0] in ("run", "check", "dis", "kir", "--no-check"):
        a = args.pop(0)
        if a in ("check", "dis", "kir"): mode = a
        elif a == "--no-check": check = False
    path = args[0] if args else None
    PROG_ARGS[:] = args[1:]
    try:
        path, _ = _entry(args[:1])
        items = _load(path)
        if mode == "check":
            sys.modules.setdefault("kite", sys.modules[__name__])
            from typecheck import Checker
            errs = Checker().check(items)
            if errs:
                errs.sort(key=lambda e: e.line or 0)
                raise CheckFailed(errs)
            print(f"{path}: 类型检查通过"); return 0
        if mode == "kir":
            sys.modules.setdefault("kite", sys.modules[__name__])
            import kir
            v = _vm(True); v.compile(items)
            fns, why = kir.collect(v.last_items)
            for n in sorted(fns): print(kir.dump(fns[n])); print()
            if why:
                print("// 走字节码路径的函数：")
                for n in sorted(why): print(f"//   {n}: {why[n]}")
            return 0
        if mode == "dis":
            from vm import disassemble
            v = _vm(check)
            disassemble(v.compile(items), vm=v); return 0
        if engine == "tree":
            Interp(typecheck=check).run(items)
        else:
            _vm(check).run(items)
    except CheckFailed as e:
        for err in e.errors[:20]:
            print(format_error(err, path), file=sys.stderr)
        if len(e.errors) > 20:
            print(f"…共 {len(e.errors)} 个错误", file=sys.stderr)
        return 1
    except KiteError as e:
        print(format_error(e, path), file=sys.stderr); return 1
    except RecursionError:
        print(f"{path}: 递归过深", file=sys.stderr); return 1
    return 0


def _entry(files):
    """命令行没给文件时，在 Kite 包里使用 src/main.kite；返回 (入口文件, 包名或 None)"""
    import os
    if files:
        return files[0], None
    sys.modules.setdefault("kite", sys.modules[__name__])
    import pkg
    root = pkg.find_root(os.getcwd())
    if root is None:
        raise KiteError("没有指定文件，当前目录也不在 Kite 包里（找不到 kite.toml）")
    main = os.path.join(root, "src", "main.kite")
    if not os.path.isfile(main):
        raise KiteError(f"{os.path.relpath(main)} 不存在（库包请用 kite check 或在其他包里依赖它）")
    return os.path.relpath(main), pkg.read_manifest(root)["name"]


def _load(path):
    sys.modules.setdefault("kite", sys.modules[__name__])
    import modules
    SOURCES[:] = [path]
    return modules.load_program(path)


if __name__ == "__main__":
    sys.modules["kite"] = sys.modules["__main__"]
    # 在大栈线程里运行，树遍历解释器处理深度嵌套的数据（如 999 层括号的正则）时不会撞到 Python 的递归上限
    import threading
    threading.stack_size(1 << 29)
    _res = []
    def _run():
        sys.setrecursionlimit(400000)
        _res.append(main(sys.argv))
    _t = threading.Thread(target=_run)
    _t.start(); _t.join()
    sys.exit(_res[0] if _res else 1)
