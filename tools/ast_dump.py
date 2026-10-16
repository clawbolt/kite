"""把 Kite 源文件解析成 AST，输出规范文本（自举对拍用：kitec 的输出必须逐字节相同）

    python3 tools/ast_dump.py FILE

格式：Node {Kind@行 键=值 …}（键排序） 列表 [a b] 记录变体字段表 R[…] 元组 <a b> 字典 D{k:v …}（插入顺序）
      字符串 "…"  限定名 Q"名"/"限定"  整数 12  浮点 f1.5  布尔 T/F  None ~
语法错误输出一行：ERR 行:列 消息（列未知时为 -）
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kite
from kite import Node, Parser, lex, KiteError, RecFields, QName


def q(s):
    out = ['"']
    for c in s:
        if c == '"': out.append('\\"')
        elif c == "\\": out.append("\\\\")
        elif c == "\n": out.append("\\n")
        elif c == "\t": out.append("\\t")
        elif c == "\r": out.append("\\r")
        elif ord(c) < 32 or ord(c) == 127: out.append("\\u{%x}" % ord(c))
        else: out.append(c)
    out.append('"')
    return "".join(out)


def dump(v, out):
    if v is None: out.append("~")
    elif v is True: out.append("T")
    elif v is False: out.append("F")
    elif isinstance(v, QName): out.append("Q" + q(str(v)) + "/" + (q(v.qual) if v.qual is not None else "~"))
    elif isinstance(v, str): out.append(q(v))
    elif isinstance(v, int): out.append(str(v))
    elif isinstance(v, float): out.append("f" + repr(v))
    elif isinstance(v, Node):
        out.append("{" + v.kind + "@" + str(v.line))
        for k in sorted(x for x in v.__dict__ if x not in ("kind", "line")):
            out.append(" " + k + "=")
            dump(v.__dict__[k], out)
        out.append("}")
    elif isinstance(v, RecFields) or isinstance(v, list):
        out.append("R[" if isinstance(v, RecFields) else "[")
        for i, x in enumerate(v):
            if i: out.append(" ")
            dump(x, out)
        out.append("]")
    elif isinstance(v, tuple):
        out.append("<")
        for i, x in enumerate(v):
            if i: out.append(" ")
            dump(x, out)
        out.append(">")
    elif isinstance(v, dict):
        out.append("D{")
        for i, (k, x) in enumerate(v.items()):
            if i: out.append(" ")
            out.append(q(k) + ":")
            dump(x, out)
        out.append("}")
    else:
        raise TypeError(f"无法输出 {type(v).__name__}: {v!r}")


def main():
    sys.setrecursionlimit(100000)
    src = open(sys.argv[1], encoding="utf-8").read()
    try:
        items = Parser(lex(src)).program()
    except KiteError as e:
        print(f"ERR {e.line if e.line is not None else '-'}:{e.col if e.col is not None else '-'} {e.msg}")
        return
    for it in items:
        out = []
        dump(it, out)
        print("".join(out))


if __name__ == "__main__":
    main()
