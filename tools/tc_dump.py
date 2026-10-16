"""运行 Python 版类型检查器，输出规范文本（自举第 2 步对拍用：kitec check 的输出必须逐字节相同）

    python3 tools/tc_dump.py FILE

经模块系统加载（与 kite check 相同）；加载出错输出 "L 行 消息"，被导入文件有语法错误输出 SKIP parse。
输出：每个错误一行 "E 行 消息"（按行号稳定排序），然后每个顶层函数一行 "F 名字: 签名"。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kite
sys.modules.setdefault("kite", kite)
from kite import Parser, lex, KiteError
import typecheck
from typecheck import Checker, STD_SIGS, show_t


def single_file(items):
    for it in items:
        if it.kind == "Mod": return False
        if it.kind == "Use":
            p = it.path
            if p[0] != "std" or len(p) < 2: return False
            mods = [kite.use_name(e)[0] for e in p[1]] if isinstance(p[1], list) else [p[1]]
            if any(m not in STD_SIGS for m in mods): return False
    return True


def main(path):
    import modules
    modules._LM_COUNTER[0] = 0     # 每个文件从头编号（模糊测试在同一进程里连续调用）
    try:
        Parser(lex(open(path, encoding="utf-8").read())).program()
    except KiteError:
        print("SKIP parse"); return
    try:
        items = modules.load_program(path)
    except KiteError as e:
        if getattr(e, "loc", None): print("SKIP parse")
        else: print(f"L {e.line or 0} {getattr(e, 'msg', str(e))}")
        return
    except Exception as e:
        print("SKIP " + type(e).__name__); return
    user_fns = [it for it in items if it.kind == "Fn" and (it.line or 0) < kite.LINE_BASE]
    c = Checker(); c.quiet = True
    try:
        errs = c.check(items)
    except KiteError as e:
        errs = [e]
    for e in sorted(errs, key=lambda e: e.line or 0):
        print(f"E {e.line or 0} {getattr(e, 'msg', str(e))}")
    for fn in user_fns:
        sig = getattr(fn, "_sig", None)
        print(f"F {fn.name}: {show_t(sig) if sig is not None else '~'}")


if __name__ == "__main__":
    main(sys.argv[1])
