"""模块系统与包管理的端到端测试：在临时目录里建仓库、发布、解析、安装、在三个引擎上运行"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
KITE = os.path.join(os.path.dirname(HERE), "kite.py")
EXAMPLES = os.path.join(os.path.dirname(HERE), "examples", "packages")
fails = 0


def run(args, cwd, env, ok=True):
    r = subprocess.run([sys.executable, KITE] + args, cwd=cwd, env=env, capture_output=True, text=True)
    if ok and r.returncode != 0:
        raise AssertionError(f"kite {' '.join(args)} 失败:\n{r.stdout}{r.stderr}")
    if not ok and r.returncode == 0:
        raise AssertionError(f"kite {' '.join(args)} 应该失败:\n{r.stdout}")
    return r.stdout + r.stderr


def check(name, fn):
    global fails
    try:
        fn(); print(f"PASS 包管理: {name}")
    except AssertionError as e:
        fails += 1; print(f"FAIL 包管理: {name}\n{e}")


def set_version(d, v):
    p = os.path.join(d, "kite.toml")
    s = open(p, encoding="utf-8").read()
    import re
    s = re.sub(r'^version = ".*"$', f'version = "{v}"', s, count=1, flags=re.M)
    open(p, "w", encoding="utf-8").write(s)


EXPECTED = """== 几何报告 ==
距离: 5.0
- 圆，面积 3.14159
- 正方形，面积 4.0
总面积: 7.14159
app 自己的 helper
"""


def semver():
    sys.path.insert(0, os.path.dirname(HERE))
    import kite
    sys.modules.setdefault("kite", kite)
    from pkg import parse_constraint as C, parse_version as V, satisfies as S
    cases = [
        ("^1.2.3", "1.2.3", True), ("^1.2.3", "1.9.0", True), ("^1.2.3", "2.0.0", False), ("^1.2.3", "1.2.2", False),
        ("^0.2.3", "0.2.9", True), ("^0.2.3", "0.3.0", False), ("^0.0.3", "0.0.4", False), ("1.2", "1.5.0", True),
        ("~1.2.3", "1.2.9", True), ("~1.2.3", "1.3.0", False), ("=1.2.3", "1.2.4", False), ("*", "9.9.9", True),
        (">=1.0, <1.5", "1.4.9", True), (">=1.0, <1.5", "1.5.0", False), ("^1.0", "1.1.0-beta.1", False),
        (">=2.0.0-beta.1", "2.0.0-beta.1", True), ("^2.0.0-beta.1", "2.0.0", True),
    ]
    for c, v, want in cases:
        got = S(V(v), C(c))
        assert got == want, f"{v} 满足 {c}？期望 {want}，实际 {got}"


def main():
    check("语义化版本约束（17 例）", semver)
    tmp = tempfile.mkdtemp(prefix="kite_pkg_")
    try:
        ws = os.path.join(tmp, "ws")
        shutil.copytree(EXAMPLES, ws)
        env = dict(os.environ, KITE_REGISTRY=os.path.join(tmp, "registry"))
        D = lambda n: os.path.join(ws, n)

        def publish_all():
            set_version(D("mathx"), "0.2.0"); run(["publish"], D("mathx"), env)
            set_version(D("mathx"), "0.2.1"); run(["publish"], D("mathx"), env)
            set_version(D("mathx"), "0.3.0"); run(["publish"], D("mathx"), env)
            set_version(D("geom"), "1.0.0"); run(["publish"], D("geom"), env)
            set_version(D("geom"), "1.1.0"); run(["publish"], D("geom"), env)
            set_version(D("geom"), "2.0.0-beta.1"); run(["publish"], D("geom"), env)
        check("发布 6 个版本", publish_all)

        def republish():
            out = run(["publish"], D("geom"), env, ok=False)
            assert "不可覆盖" in out, out
        check("同一版本不能重复发布", republish)

        def resolve():
            run(["get"], D("app"), env)
            lock = open(os.path.join(D("app"), "kite.lock"), encoding="utf-8").read()
            # ^1.0 选最高的 1.1.0（不选预发布 2.0.0-beta.1）；geom 要求 mathx ^0.2 → 0.2.1（不是 0.3.0）
            assert 'name = "geom"\nversion = "1.1.0"' in lock, lock
            assert 'name = "mathx"\nversion = "0.2.1"' in lock, lock
            assert 'source = "path+../util"' in lock, lock
            assert "checksum = \"sha256:" in lock
            tree = run(["tree"], D("app"), env)
            assert "├── geom 1.1.0\n│   └── mathx 0.2.1\n└── util 0.1.0 (../util)" in tree, tree
        check("语义化版本解析 + 传递依赖 + 路径依赖 + 锁文件", resolve)

        def engines():
            for args in (["run"], ["--tree", "run"]):
                out = run(args, D("app"), env)
                assert out == EXPECTED, f"{args}:\n{out}"
        check("虚拟机与树解释器运行多模块程序", engines)

        def native():
            try:
                run(["build", "-o", "app_bin"], D("app"), env)
            except AssertionError as e:
                if "找不到 C 编译器" in str(e): print("  （跳过：没有 C 编译器）"); return
                raise
            r = subprocess.run([os.path.join(D("app"), "app_bin")], capture_output=True, text=True)
            assert r.stdout == EXPECTED, r.stdout + r.stderr
        check("原生编译多模块程序", native)

        def reproducible():
            set_version(D("geom"), "1.2.0"); run(["publish"], D("geom"), env)
            run(["get"], D("app"), env)
            lock = open(os.path.join(D("app"), "kite.lock"), encoding="utf-8").read()
            assert 'name = "geom"\nversion = "1.1.0"' in lock, "有锁文件时不应自动升级"
            run(["update"], D("app"), env)
            lock = open(os.path.join(D("app"), "kite.lock"), encoding="utf-8").read()
            assert 'name = "geom"\nversion = "1.2.0"' in lock, lock
        check("锁文件保证可复现，update 才升级", reproducible)

        def deterministic():
            sys.path.insert(0, os.path.dirname(HERE))
            import kite  # noqa
            sys.modules.setdefault("kite", kite)
            import pkg
            a, _ = pkg.pack(D("mathx")); b, _ = pkg.pack(D("mathx"))
            assert a == b
        check("打包是确定性的", deterministic)

        def conflict():
            app = D("app")
            orig = open(os.path.join(app, "kite.toml"), encoding="utf-8").read()
            open(os.path.join(app, "kite.toml"), "w", encoding="utf-8").write(orig.replace('geom = "^1.0"', 'geom = "^1.0"\nmathx = "^0.3"'))
            out = run(["get"], app, env, ok=False)
            open(os.path.join(app, "kite.toml"), "w", encoding="utf-8").write(orig)
            assert "无法为 mathx 选出" in out and "要求 ^0.2" in out and "app 要求 ^0.3" in out, out
        check("版本冲突给出完整的约束来源", conflict)

        def errors():
            src = D("app") + "/src"
            cases = {
                "t_private.kite": ("use geom\nfn main() { print(geom.helper()) }\n", "geom.helper 是私有的"),
                "t_missing.kite": ("use nothere\nfn main() {}\n", "找不到模块 nothere"),
                "t_noitem.kite": ("use geom.nope\nfn main() {}\n", "模块 geom 里没有 nope"),
                "t_pkgvis.kite": ("use geom\nfn main() { print(geom.unit()) }\n", "geom.unit 是 pub(pkg)，只对本包可见"),
            }
            open(os.path.join(src, "cyc_a.kite"), "w").write("use cyc_b\npub fn a() -> Int { 1 }\n")
            open(os.path.join(src, "cyc_b.kite"), "w").write("use cyc_a\npub fn b() -> Int { 2 }\n")
            cases["t_cycle.kite"] = ("use cyc_a\nfn main() { print(cyc_a.a()) }\n", "模块循环引用: cyc_a → cyc_b → cyc_a")
            open(os.path.join(src, "bad_mod.kite"), "w").write("pub fn f() -> Int {\n  \"x\" + 1\n}\n")
            cases["t_typeerr.kite"] = ("use bad_mod\nfn main() { print(bad_mod.f()) }\n", "src/bad_mod.kite:2: 类型错误")
            for fn, (code, want) in cases.items():
                open(os.path.join(src, fn), "w").write(code)
                out = run([os.path.join("src", fn)], D("app"), env, ok=False)
                assert want in out, f"{fn}: 期望包含 {want!r}，实际 {out!r}"
        check("私有项 / 缺失模块 / 循环引用 / 模块内类型错误定位", errors)

        def kite_test():
            for extra in ([], ["--tree"], ["--native"]):
                if extra == ["--native"] and not shutil.which("cc") and not os.environ.get("CC"):
                    try: import ziglang  # noqa
                    except ImportError: continue
                out = run(["test"] + extra, D("geom"), env)
                assert "结果: 5 通过，0 失败" in out and "✓ test_helper" in out and "✓ test_describe" in out, out
            out = run(["test", "norm"], D("geom"), env)
            assert "结果: 1 通过" in out, out
            fx = os.path.join(HERE, "fixtures", "calc_test.kite")
            for extra in ([], ["--tree"], ["--native"]):
                out = run(["test", fx] + extra, tmp, env, ok=False)
                assert "结果: 2 通过，2 失败" in out, out
                assert "calc_test.kite:20: assert_eq 失败（加法）: 左边 4，右边 5" in out, out
                assert "调试输出" in out and "不应运行" not in out, out
                assert "unwrap 失败: None" in out, out
        check("kite test：单元/集成测试、过滤、失败报告、三种引擎", kite_test)

        def tamper():
            reg = env["KITE_REGISTRY"]
            p = os.path.join(reg, "pkgs", "mathx-0.2.1.tar.gz")
            data = bytearray(open(p, "rb").read()); data[-12] ^= 0xFF
            open(p, "wb").write(bytes(data))
            shutil.rmtree(os.path.join(D("app"), ".kite"))
            out = run(["run"], D("app"), env, ok=False)
            assert "校验和不匹配" in out, out
        check("被篡改的包拒绝安装", tamper)

        def new_add():
            run(["new", "hello"], tmp, env)
            run(["new", "greet", "--lib"], tmp, env)
            h = os.path.join(tmp, "hello")
            out = run(["add", "greet", "--path", "../greet"], h, env)
            assert "已添加 greet 0.1.0" in out, out
            open(os.path.join(h, "src", "main.kite"), "w").write("use greet.hello\nfn main() {\n  print(hello(\"Kite\"))\n}\n")
            out = run(["run"], h, env)
            assert out == "你好，Kite！\n", out
            set_version(D("mathx"), "0.3.1"); run(["publish"], D("mathx"), env)
            out = run(["add", "mathx"], h, env)   # 默认约束 ^最新正式版
            assert "已添加 mathx 0.3.1" in out, out
            assert 'mathx = "^0.3.1"' in open(os.path.join(h, "kite.toml")).read()
        check("kite new / add（路径与仓库）", new_add)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
