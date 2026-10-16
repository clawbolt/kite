"""kite fmt —— Kite 代码格式化器

原则
- 保留注释、字符串原文和换行结构（Kite 的换行有语义），只改缩进、空格和空行
- 2 空格缩进；同一行开多个括号只缩进一级；以 |> . && || 开头的续行多缩进一级
- 运算符两侧加空格；范围 .. / ..= 不加；泛型 <T> 与比较 < 自动区分
- 连续空行压成一行；{ 之后、} 之前不留空行；文件以单个换行结尾
- 安全网：格式化前后用正式词法分析器比对 token 序列（含换行标记），不一致就拒绝输出
- 幂等：fmt(fmt(x)) == fmt(x)
"""
from kite import lex, KiteError, OPS, KEYWORDS

INDENT = "  "
BINARY = {"=", "==", "!=", "<=", ">=", "+", "-", "*", "/", "%", "&&", "||", "|>", "=>", "->",
          "+=", "-=", "*=", "/=", "%=", "|=", "&=", "|", "&", "<", ">", "^", "<<", "^=", "<<=", ">>="}
TIGHT = {"..", "..=", "::", "."}
OPENERS, CLOSERS = "([{", ")]}"
CONT_PREFIX = {"|>", ".", "&&", "||"}
# 这些关键字后面跟 ( [ 时要空格（if (a) / for (x, y) in …）
SPACE_KW = {"async", "if", "match", "for", "while", "in", "return", "let", "var", "is", "else", "spawn",
            "await", "defer", "as", "loop", "break", "continue"}


class T:
    __slots__ = ("k", "s", "sp")   # 类型, 原文, 前面是否有空白
    def __init__(self, k, s, sp):
        self.k, self.s, self.sp = k, s, sp
    def __repr__(self): return f"{self.k}:{self.s!r}"


def tokenize(src):
    """返回逻辑行列表；每行是 T 的列表。多行注释/字符串作为一个整体 token"""
    lines, cur, i, n, sp = [], [], 0, len(src), False
    while i < n:
        c = src[i]
        if c == "\n":
            lines.append(cur); cur = []; i += 1; sp = False; continue
        if c in " \t\r":
            sp = True; i += 1; continue
        if src.startswith("//", i):
            j = src.find("\n", i); j = n if j < 0 else j
            cur.append(T("comment", src[i:j].rstrip(), sp)); i = j; sp = False; continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0: raise KiteError("块注释未闭合")
            cur.append(T("comment", src[i:j + 2], sp)); i = j + 2; sp = False; continue
        if c == "r" and i + 1 < n and src[i + 1] in '"#':   # 原始字符串
            k = i + 1
            while k < n and src[k] == "#": k += 1
            if k < n and src[k] == '"':
                h = k - i - 1
                e = src.find('"' + "#" * h, k + 1)
                if e < 0: raise KiteError("原始字符串未闭合")
                cur.append(T("str", src[i:e + 1 + h], sp)); i = e + 1 + h; sp = False; continue
        if c == "'" and i + 2 < n and (src[i + 1] == "\\" or src[i + 2] == "'") and src[i + 1] not in "\n'":   # 字符字面量
            j = src.find("'", i + 2 if src[i + 1] == "\\" and src[i + 2] != "'" else i + 3) if src[i + 1] == "\\" else i + 2
            if src[i + 1] == "\\" and src[i + 2] == "'": j = src.find("'", i + 3)
            cur.append(T("str", src[i:j + 1], sp)); i = j + 1; sp = False; continue
        if c == '"':
            j, depth = i + 1, 0
            while j < n:
                d = src[j]
                if d == "\\": j += 2; continue
                if d == "{": depth += 1
                elif d == "}" and depth: depth -= 1
                elif d == '"' and depth == 0: break
                j += 1
            cur.append(T("str", src[i:j + 1], sp)); i = j + 1; sp = False; continue
        if c.isdigit():
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_" or
                             (src[j] in "+-" and src[j - 1] in "eE" and not src[i:i + 2].lower().startswith("0x")
                              and j + 1 < n and src[j + 1].isdigit()) or
                             (src[j] == "." and j + 1 < n and src[j + 1].isdigit() and "." not in src[i:j]
                              and not (cur and cur[-1].s == "."))):
                j += 1
            cur.append(T("num", src[i:j], sp)); i = j; sp = False; continue
        if c == "'" and i + 1 < n and (src[i + 1].isalpha() or src[i + 1] == "_"):   # 循环标签
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            cur.append(T("id", src[i:j], sp)); i = j; sp = False; continue
        if c.isalpha() or c == "_" or (ord(c) > 127 and c.isidentifier()):
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            w = src[i:j]
            cur.append(T("kw" if w in KEYWORDS else "id", w, sp)); i = j; sp = False; continue
        for op in OPS:
            if src.startswith(op, i):
                if op == ">>":   # 拆成两个 >，让 List<List<Int>> 的泛型识别照常工作；右移在排版时再拼回去
                    cur.append(T("op", ">", sp)); cur.append(T("op", ">", False))
                else:
                    cur.append(T("op", op, sp))
                i += len(op); sp = False; break
        else:
            cur.append(T("other", c, sp)); i += 1; sp = False
    lines.append(cur)
    return lines


def _generic_spans(toks):
    """标出哪些 < > 是泛型括号"""
    gen = set()
    for i, t in enumerate(toks):
        if t.s != "<" or i == 0 or not (toks[i - 1].k == "id" or toks[i - 1].s in ("impl", ".")):
            continue
        prev = toks[i - 1].s
        before = toks[i - 2].s if i >= 2 else ""
        if not (prev[0].isupper() or prev in ("impl", ".") or before in ("fn", "type", "enum", "trait", "impl")):
            continue
        depth, j, ok = 0, i, False
        while j < len(toks):
            s = toks[j]
            if s.s == "<": depth += 1
            elif s.s == ">":
                depth -= 1
                if depth == 0: ok = True; break
            elif not (s.k == "id" or s.k == "kw" or (s.k == "num" and s.s.isdigit()) or s.s in (",", ":", "+", "(", ")", "[", "]", "->", ".", ";")):
                break
            j += 1
        if ok:
            d, k = 0, i
            while k <= j:
                if toks[k].s in ("<", ">"): gen.add(k)
                k += 1
    return gen


def _unary(prev):
    return prev is None or (prev.k == "op" and prev.s not in (")", "]", "}", "?")) or \
        (prev.k == "kw" and prev.s not in ("self", "Self", "true", "false"))


def _set_braces(toks):
    """集合字面量 Set{…} 的花括号下标：{ 紧贴 Set，括号内侧不留空格（和打印格式一致）。"""
    opens, closes, stack = set(), set(), []
    for i, t in enumerate(toks):
        if t.k != "op": continue
        if t.s == "{":
            is_set = i > 0 and toks[i - 1].k == "id" and toks[i - 1].s == "Set"
            stack.append(is_set)
            if is_set: opens.add(i)
        elif t.s == "}" and stack:
            if stack.pop(): closes.add(i)
    return opens, closes


def format_line(toks):
    gen = _generic_spans(toks)
    sopen, sclose = _set_braces(toks)
    out = []
    for i, t in enumerate(toks):
        if i == 0:
            out.append(t.s); continue
        p = toks[i - 1]
        pp = toks[i - 2] if i >= 2 else None
        s = t.s
        if t.k == "comment":
            space = " " if not t.sp else None   # 保留行尾注释原有的对齐空白
        elif i in sopen or (i - 1) in sopen or i in sclose:
            space = ""
        elif i in gen or (i - 1) in gen:
            # 泛型：List<T>  fn f<T: Ord>(…)  → 括号两侧都不空格，> 之后按普通规则
            if s == "<" or p.s == "<" and (i - 1) in gen or s == ">" and i in gen:
                space = ""
            elif p.s == ">" and (i - 1) in gen:
                space = "" if s in ("(", ">", ",", ")", "]", ".", "?", "[", ";") else " "
                if s == "{" or s in BINARY or t.k in ("id", "kw"): space = " "
            else:
                space = ""
        elif s == ">" and p.s == ">" and not t.sp:
            space = ""   # 右移 >>
        elif s in (",", ";", ":", "?") or s in (")", "]"):
            space = ""
        elif p.s in ("(", "["):
            space = ""
        elif p.s in (",", ";", ":"):
            space = " "
        elif s == ".." and p.s == "{":
            space = " "   # 结构体更新 Cfg { ..base }
        elif s == "}" and p.s == "..":
            space = " "   # 模式 Rect { w, .. }
        elif p.s == ".." and s in ("=>", "|", "=", "if", "{"):
            space = " "   # 开区间模式 100.. => …
        elif s in TIGHT or p.s in TIGHT:
            # [head, ..rest] 与 1..=3 都不加空格；行内的 .method 紧贴
            space = ""
        elif p.s in ("!", "~"):
            space = ""   # ! ~ 在 Kite 中只做一元运算
        elif s in ("!", "~"):
            space = " "
        elif p.s == "-" and _unary(pp):
            space = ""   # 一元负号
        elif s in BINARY or p.s in BINARY:
            space = " "
        elif s in ("(", "["):
            space = " " if p.k == "kw" and p.s in SPACE_KW or p.s == "{" else ""
        elif s == "{":
            space = " "
        elif s == "}":
            space = "" if p.s == "{" else " "
        elif p.s == "{":
            space = " "
        elif p.s == "}":
            space = " "
        elif p.k in ("id", "kw", "num", "str") and t.k in ("id", "kw", "num", "str"):
            space = " "
        else:
            space = " " if t.sp else ""
        if space is None:
            out.append(_orig_gap(t)); out.append(s)
        else:
            out.append(space); out.append(s)
    return "".join(out)


def _orig_gap(t):
    return t._gap if hasattr(t, "_gap") else " "


def _leading_closers(toks):
    n = 0
    for t in toks:
        if t.s in CLOSERS: n += 1
        else: break
    return n


def format_source(src):
    raw_lines = src.split("\n")
    lines = tokenize(src)
    # 记录行尾注释前的原始空白宽度（保留手工对齐）
    for ln, rl in zip(lines, raw_lines):
        if len(ln) >= 2 and ln[-1].k == "comment" and ln[-1].sp and ln[-1].s.startswith("//"):
            idx = rl.rfind(ln[-1].s)
            gap = len(rl[:idx]) - len(rl[:idx].rstrip()) if idx > 0 else 1
            ln[-1] = _with_gap(ln[-1], max(1, gap))
    out = []
    arm = []     # 与 out 平行：该行是否为 match 分支（直接位于 match { } 内）
    stack = []   # 每个未闭合括号：(所在行号, 是否 match 的大括号)
    blank = 0
    for li, toks in enumerate(lines):
        if not toks:
            blank += 1; continue
        # 计算缩进：先弹出行首的闭括号
        closers = _leading_closers(toks)
        tmp = list(stack)
        for _ in range(closers):
            if tmp: tmp.pop()
        level = len(set(li_ for li_, _ in tmp))
        in_match = bool(tmp) and tmp[-1][1]
        if toks[0].s in CONT_PREFIX:
            level += 1
        if blank and out and not out[-1].rstrip().endswith(("{", "(", "[")) and toks[0].s not in CLOSERS:
            out.append(""); arm.append(False)
        blank = 0
        text = format_line(toks)
        out.append(INDENT * level + text); arm.append(in_match)
        has_match = any(t.k == "kw" and t.s == "match" for t in toks)
        for t in toks:
            if t.s in OPENERS and t.k == "op": stack.append((li, has_match and t.s == "{"))
            elif t.s in CLOSERS and t.k == "op" and stack: stack.pop()
    _align_arms(out, arm)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def _arrow_col(line):
    """行内第一个顶层（不在括号/字符串里）的 => 的位置"""
    depth, i, n = 0, 0, len(line)
    while i < n:
        c = line[i]
        if c == '"':
            j, d = i + 1, 0
            while j < n:
                if line[j] == "\\": j += 2; continue
                if line[j] == "{": d += 1
                elif line[j] == "}" and d: d -= 1
                elif line[j] == '"' and d == 0: break
                j += 1
            i = j + 1; continue
        if line.startswith("//", i): return -1
        if c in "([{": depth += 1
        elif c in ")]}": depth -= 1
        elif depth == 0 and line.startswith(" => ", i - 1 if i else 0) and line.startswith("=>", i):
            return i - 1
        i += 1
    return -1


def _align_arms(out, arm):
    """把同一个 match 里相邻分支的 => 对齐（类似 gofmt 对齐结构体字段）"""
    i = 0
    while i < len(out):
        if not arm[i] or _arrow_col(out[i]) < 0:
            i += 1; continue
        ind = len(out[i]) - len(out[i].lstrip())
        j = i
        while j < len(out) and arm[j] and _arrow_col(out[j]) >= 0 and \
                len(out[j]) - len(out[j].lstrip()) == ind:
            j += 1
        if j - i > 1:
            width = max(_arrow_col(out[k]) for k in range(i, j))
            for k in range(i, j):
                c = _arrow_col(out[k])
                out[k] = out[k][:c] + " " * (width - c) + out[k][c:]
        i = max(j, i + 1)


class _GapT(T):
    __slots__ = ("_gap",)


def _with_gap(t, gap):
    g = _GapT(t.k, t.s, True); g._gap = " " * gap
    return g


def _sig(src):
    def norm(t):
        if t.kind == "STR":   # 插值片段带行号，比较时去掉
            return (t.kind, repr([p[:2] if isinstance(p, tuple) else p for p in t.val]), t.nl)
        return (t.kind, repr(t.val), t.nl)
    out = []
    for t in lex(src):
        n = norm(t)
        if t.kind == "OP" and t.val == ">>":    # 泛型里 > > 和 >> 等价（格式化会把 List<List<T> > 收紧）
            out += [(t.kind, repr(">"), False), (t.kind, repr(">"), t.nl)]
        else:
            out.append(n)
    return out


def fmt(src):
    """格式化；若会改变 token 序列则抛出 KiteError（不应发生）"""
    res = format_source(src)
    try:
        before = _sig(src)
    except KiteError:
        raise
    if _sig(res) != before:
        raise KiteError("格式化器内部错误：输出改变了程序含义，已放弃（请报告此文件）")
    return res


def migrate(path, src):
    """把 0.48 及以前的代码改写成文法 v0.6 的写法，返回 (新源码, 改动说明列表)。
    1) 按旧优先级解析，凡是新优先级下含义会变的子表达式都加括号（|> 两侧、区间作 | ^ & 的操作数）；
    2) 按旧语义做类型检查，找出作用在 Int 上的 !，改成 ~。"""
    import kite
    from kite import Parser, LINE_BASE
    p = Parser(lex(src), legacy=True)
    p.program()
    spans = sorted(set(p.mig))
    bnot, notes = [], []
    old = kite.PARSE_LEGACY
    kite.PARSE_LEGACY = True
    try:
        import modules
        from typecheck import Checker
        items = modules.load_program(path, src)
        ck = Checker(); ck.migrate_bnot = []
        errs = ck.check(items)
        if errs:
            notes.append(f"旧语义下类型检查未通过（{errs[0].msg}），没有改写 Int 上的 !")
        else:
            seen = set()
            for pos in ck.migrate_bnot:
                if pos not in seen and src[pos] == "!": seen.add(pos); bnot.append(pos)
    finally:
        kite.PARSE_LEGACY = old
    out = list(src)
    for pos in bnot: out[pos] = "~"
    ins = [(a, 1, "(") for a, b in spans] + [(b, 0, ")") for a, b in spans]
    for pos, _, s in sorted(ins, reverse=True):
        out.insert(pos, s)
    if spans: notes.append(f"加括号 {len(spans)} 处（|> / 区间的新优先级）")
    if bnot: notes.append(f"Int 上的 ! → ~ {len(bnot)} 处")
    for ln in getattr(p, "top_lets", []):
        notes.append(f"第 {ln} 行：顶层 let/var 需要手工改写（常量改成 const NAME: T = …，可变状态移进函数）")
    return "".join(out), notes


def main(args):
    import sys
    if "--migrate" in args:
        bad = 0
        for path in [a for a in args if not a.startswith("-")]:
            with open(path, encoding="utf-8") as f:
                src = f.read()
            try:
                res, notes = migrate(path, src)
            except KiteError as e:
                print(f"{path}:{e}", file=sys.stderr); bad = 1; continue
            if "-w" in args:
                if res != src:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(res)
                print(f"{path}: " + ("；".join(notes) if notes else "无需迁移"))
            else:
                sys.stdout.write(res)
                for n in notes: print(f"{path}: {n}", file=sys.stderr)
        return bad
    write = "-w" in args
    check = "--check" in args
    files = [a for a in args if not a.startswith("-")]
    if not files:
        src = sys.stdin.read()
        sys.stdout.write(fmt(src)); return 0
    bad = 0
    for path in files:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        try:
            res = fmt(src)
        except KiteError as e:
            print(f"{path}:{e}", file=sys.stderr); bad = 1; continue
        if check:
            if res != src:
                print(f"{path}: 需要格式化"); bad = 1
        elif write:
            if res != src:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(res)
                print(f"已格式化 {path}")
        else:
            sys.stdout.write(res)
    return bad
