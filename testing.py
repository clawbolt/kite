"""kite test：发现并运行测试

约定
- 测试是顶层的、无参数的、名字以 test_ 开头的函数
- 在包里不写参数时，扫描 src/ 和 tests/ 下所有 .kite 文件：
  src/ 里的测试和被测代码在同一个模块，可以测私有函数；
  tests/ 里的文件用 `use 包名` 引用本包（只能访问 pub 项），也可以引用依赖
- 也可以直接给文件或目录：kite test a.kite tests/
- 断言：assert(条件[, 消息])、assert_eq(左, 右[, 消息])、assert_ne(左, 右[, 消息])、panic(消息)

每个测试在全新的运行时里执行（顶层 let 重新求值，测试之间互不影响）；
测试的输出被捕获，只在失败时显示。

kite test [过滤词] [文件/目录...] [--tree | --native] [--no-check] [-v]
    过滤词     只运行名字（或 “文件::名字”）包含它的测试
    --tree     用树遍历解释器
    --native   编译成原生程序运行（每个文件编译一次，每个测试一个进程）
    -v         通过的测试也显示输出
"""
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import time

import kite
from kite import KiteError, CheckFailed, format_error, Parser, lex

TEST_RE = re.compile(r"^\s*(fn\s+test_\w+\s*\(\s*\)|@test\b)", re.M)


def collect_files(targets):
    files = []
    for t in targets:
        if os.path.isdir(t):
            for d, dirs, fs in os.walk(t):
                dirs[:] = sorted(x for x in dirs if not x.startswith("."))
                files += [os.path.join(d, f) for f in sorted(fs) if f.endswith(".kite")]
        else:
            files.append(t)
    out = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                if TEST_RE.search(fh.read()): out.append(f)
        except OSError as e:
            raise KiteError(f"无法读取 {f}: {e.strerror}")
    return out


def test_names(items):
    out = []
    for it in items:
        if it.kind != "Fn" or "." in it.name: continue
        marked = any(a[0] == "test" for a in getattr(it, "attrs", []))
        if marked and (it.params or it.generics):
            raise kite.KiteError(f"@test 函数 {it.name} 不能有参数或泛型参数", it.line)
        if marked or it.name.startswith("test_") and not it.params:
            out.append(it.name)
    return out


class Outcome:
    def __init__(self, name, ok, secs, err=None, output=""):
        self.name, self.ok, self.secs, self.err, self.output = name, ok, secs, err, output


def run_vm(items, name, engine):
    buf = io.StringIO()
    t0 = time.perf_counter()
    try:
        with contextlib.redirect_stdout(buf):
            if engine == "tree":
                it = kite.Interp(typecheck=False)
                it.run(items, call_main=False)
                it.call(it.g.find(name)[0], [], None)
            else:
                import vm as V
                v = V.VM(typecheck=False)
                v.run(items, call_main=False)
                v.execute(v.globals[name], [])
        return Outcome(name, True, time.perf_counter() - t0, output=buf.getvalue())
    except KiteError as e:
        return Outcome(name, False, time.perf_counter() - t0, format_error(e), buf.getvalue())
    except RecursionError:
        return Outcome(name, False, time.perf_counter() - t0, "递归过深", buf.getvalue())


def run_native(items, names, path, workdir, typed=False):
    import native
    items = [it for it in items if not (it.kind == "Fn" and it.name == "main")]
    branches = " else ".join(f'if t == "{n}" {{ {n}() }}' for n in names)
    toks = lex(f"fn main() {{\n  let t = __kite_test()\n  {branches}\n}}\n")
    items = items + Parser(toks).program()
    exe = os.path.join(workdir, re.sub(r"\W", "_", path))
    native.build(items, path, exe, typecheck=False, typed=typed)
    out = []
    for n in names:
        t0 = time.perf_counter()
        try:
            r = subprocess.run([exe], env=dict(os.environ, KITE_TEST=n), capture_output=True, text=True, timeout=120)
            err = r.stderr.strip().splitlines()
            ok = r.returncode == 0
            out.append(Outcome(n, ok, time.perf_counter() - t0, None if ok else (err[-1] if err else f"退出码 {r.returncode}"),
                               r.stdout))
        except subprocess.TimeoutExpired as e:
            out.append(Outcome(n, False, time.perf_counter() - t0, "超时（120 秒）", e.stdout or ""))
    return out


def fmt_time(s):
    return f"{s * 1000:.1f}ms" if s < 1 else f"{s:.2f}s"


def main(args):
    engine, check, verbose = "vm", True, False
    rest = []
    for a in args:
        if a == "--tree": engine = "tree"
        elif a == "--native": engine = "native"
        elif a == "--no-check": check = False
        elif a == "-v": verbose = True
        elif a in ("-h", "--help"): print(__doc__.strip()); return 0
        else: rest.append(a)
    targets = [a for a in rest if os.path.exists(a)]
    filters = [a for a in rest if not os.path.exists(a)]
    if not targets:
        import pkg
        root = pkg.find_root(os.getcwd())
        if root is None:
            print("用法: kite test [过滤词] [文件/目录...]（不在 Kite 包里时需要给出文件）", file=sys.stderr)
            return 2
        targets = [os.path.relpath(os.path.join(root, d)) for d in ("src", "tests") if os.path.isdir(os.path.join(root, d))]
    try:
        files = collect_files(targets)
    except KiteError as e:
        print(f"错误: {e.msg}", file=sys.stderr); return 1

    t_start = time.perf_counter()
    passed = failed = 0
    failures = []
    workdir = tempfile.mkdtemp(prefix="kite_test_") if engine == "native" else None
    for path in files:
        try:
            import modules
            kite.SOURCES[:] = [path]
            items = modules.load_program(path)
            names = [n for n in test_names(items)
                     if not filters or any(f in n or f in f"{path}::{n}" for f in filters)]
            if not names: continue
            if check:
                from typecheck import Checker
                errs = Checker().check(items)
                if errs:
                    errs.sort(key=lambda e: e.line or 0)
                    raise CheckFailed(errs)
        except CheckFailed as e:
            print(f"{path}")
            for err in e.errors[:10]: print(f"  ✗ {format_error(err, path)}")
            failed += 1; failures.append(f"{path}（类型检查失败）"); continue
        except KiteError as e:
            print(f"{path}\n  ✗ {format_error(e, path)}")
            failed += 1; failures.append(f"{path}（加载失败）"); continue
        print(path)
        if engine == "native":
            try:
                results = run_native(items, names, path, workdir, typed=check)
            except KiteError as e:
                print(f"  ✗ 编译失败: {e.msg}")
                failed += len(names); failures += [f"{path}::{n}" for n in names]; continue
        else:
            results = []
            for n in names:
                results.append(run_vm(fresh(path), n, engine))
        for r in results:
            if r.ok:
                passed += 1
                print(f"  ✓ {r.name} ({fmt_time(r.secs)})")
                if verbose and r.output: print(indent(r.output))
            else:
                failed += 1; failures.append(f"{path}::{r.name}")
                print(f"  ✗ {r.name} ({fmt_time(r.secs)})")
                print(f"      {r.err}")
                if r.output:
                    print("      ── 输出 ──"); print(indent(r.output))
    total = passed + failed
    secs = time.perf_counter() - t_start
    if total == 0:
        print("没有找到测试（测试是名字以 test_ 开头、没有参数的顶层函数）")
        return 0
    print()
    if failures:
        print("失败的测试:")
        for f in failures: print(f"  {f}")
    print(f"结果: {passed} 通过，{failed} 失败（用时 {secs:.2f}s）")
    return 1 if failed else 0


def fresh(path):
    """每个测试重新加载 AST：后端会在节点上缓存信息，重新加载保证测试之间完全隔离"""
    import modules
    kite.SOURCES[:] = [path]
    return modules.load_program(path)


def indent(s):
    return "\n".join("      " + line for line in s.rstrip("\n").split("\n"))
