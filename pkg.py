"""Kite 包管理：kite new / add / get / update / publish / tree

清单 kite.toml：
    [package]
    name = "app"
    version = "0.1.0"

    [dependencies]
    geom = "^1.2"                 # 从仓库取，语义化版本约束
    util = { path = "../util" }   # 本地路径依赖

锁文件 kite.lock：精确记录每个包的版本、来源和 sha256 校验和，提交到版本库即可复现构建。

仓库：目录形式（KITE_REGISTRY 环境变量，默认 ~/.kite/registry）
    index/<名字>.json   {"versions": {"1.2.0": {"sha256": "...", "deps": {...}, "file": "pkgs/geom-1.2.0.tar.gz"}}}
    pkgs/<名字>-<版本>.tar.gz
发布的版本不可覆盖；打包是确定性的（固定顺序、时间戳、属主），相同源码得到相同校验和。
安装位置：<项目>/.kite/deps/<名字>-<版本>/，安装时校验 sha256，被篡改的包拒绝使用。

版本约束（与 Cargo 一致）：
    "1.2.3" 或 "^1.2.3"  >=1.2.3 <2.0.0（0.x 时：^0.2.3 → <0.3.0，^0.0.3 → <0.0.4）
    "~1.2.3"            >=1.2.3 <1.3.0
    "=1.2.3"  ">=1.0"  "<2.0"  ">=1.0, <1.5"  "*"
解析：每个包只选一个版本；在满足所有约束的版本里取最高；锁文件里已有且仍满足约束的版本优先（可复现）。
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import tomllib

from kite import KiteError

LOCK_NAME = "kite.lock"
MANIFEST = "kite.toml"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class PkgError(KiteError):
    pass


def say(msg):
    print(msg, file=sys.stderr)


# ───────────────────────── 语义化版本 ─────────────────────────
def parse_version(s):
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?", s.strip())
    if not m:
        raise PkgError(f"无效的版本号 {s!r}（应为 主.次.修订，例如 1.2.0）")
    pre = m.group(4)
    # 预发布版本排在正式版之前
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if pre else 1, pre or "")


def vstr(v):
    return f"{v[0]}.{v[1]}.{v[2]}" + (f"-{v[4]}" if v[4] else "")


def _partial(s):
    parts = s.strip().split(".")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise PkgError(f"无效的版本约束 {s!r}")
    nums = [int(p) for p in parts]
    return nums + [0] * (3 - len(nums)), len(nums)


def parse_constraint(c):
    """返回 [(运算符, 版本元组)]，全部满足才算匹配"""
    c = c.strip()
    if c in ("*", ""):
        return []
    out = []
    for part in c.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\^|~|=|>=|<=|>|<)?\s*([0-9][0-9A-Za-z.\-]*)", part)
        if not m:
            raise PkgError(f"无效的版本约束 {part!r}")
        op, ver = m.group(1) or "^", m.group(2)
        if "-" in ver:
            pv = parse_version(ver); nums, n = list(pv[:3]), 3
            lo = pv
        else:
            nums, n = _partial(ver)
            lo = (nums[0], nums[1], nums[2], 0, "")
        if op == "^":
            if nums[0] > 0 or n == 1: hi = (nums[0] + 1, 0, 0, 0, "")
            elif nums[1] > 0 or n == 2: hi = (0, nums[1] + 1, 0, 0, "")
            else: hi = (0, 0, nums[2] + 1, 0, "")
            out += [(">=", lo), ("<", hi)]
        elif op == "~":
            hi = (nums[0] + 1, 0, 0, 0, "") if n == 1 else (nums[0], nums[1] + 1, 0, 0, "")
            out += [(">=", lo), ("<", hi)]
        elif op == "=":
            if n < 3: raise PkgError(f"= 约束需要完整版本号: {part!r}")
            v = parse_version(ver)
            out.append(("=", v))
        else:
            out.append((op, lo if op in (">=", "<") or lo[4] else (nums[0], nums[1], nums[2], 1, "")))
    return out


def satisfies(v, cons):
    for op, b in cons:
        if op == "=" and v != b: return False
        if op == ">=" and v < b: return False
        if op == ">" and v <= b: return False
        if op == "<" and v >= b: return False
        if op == "<=" and v > b: return False
    # 预发布版本只在约束明确写了预发布时才匹配
    if v[3] == 0 and not any(b[3] == 0 and b[4] for _, b in cons):
        return False
    return True


# ───────────────────────── 清单与锁文件 ─────────────────────────
def find_root(start):
    d = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(d, MANIFEST)): return d
        parent = os.path.dirname(d)
        if parent == d: return None
        d = parent


def read_manifest(root):
    path = os.path.join(root, MANIFEST)
    try:
        with open(path, "rb") as f: data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise PkgError(f"{path}: TOML 格式错误: {e}")
    pk = data.get("package") or {}
    name, ver = pk.get("name"), pk.get("version", "0.1.0")
    if not name or not NAME_RE.match(name):
        raise PkgError(f"{path}: [package] name 必须是小写字母开头的标识符")
    parse_version(ver)
    deps = {}
    for dn, spec in (data.get("dependencies") or {}).items():
        if not NAME_RE.match(dn): raise PkgError(f"{path}: 无效的依赖名 {dn!r}")
        if isinstance(spec, str):
            parse_constraint(spec); deps[dn] = {"version": spec}
        elif isinstance(spec, dict) and ("path" in spec or "version" in spec):
            if "version" in spec: parse_constraint(spec["version"])
            deps[dn] = dict(spec)
        else:
            raise PkgError(f"{path}: 依赖 {dn} 的写法不对（应为 \"^1.0\" 或 {{ path = \"...\" }}）")
    return {"name": name, "version": ver, "deps": deps, "desc": pk.get("description", "")}


def toml_str(s):
    return json.dumps(s, ensure_ascii=False)


def write_manifest(root, m):
    lines = ["[package]", f"name = {toml_str(m['name'])}", f"version = {toml_str(m['version'])}"]
    if m.get("desc"): lines.append(f"description = {toml_str(m['desc'])}")
    lines += ["", "[dependencies]"]
    for dn in sorted(m["deps"]):
        spec = m["deps"][dn]
        if "path" in spec: lines.append(f"{dn} = {{ path = {toml_str(spec['path'])} }}")
        else: lines.append(f"{dn} = {toml_str(spec['version'])}")
    with open(os.path.join(root, MANIFEST), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def read_lock(root):
    path = os.path.join(root, LOCK_NAME)
    if not os.path.exists(path): return None
    with open(path, "rb") as f: data = tomllib.load(f)
    return {p["name"]: p for p in data.get("package", [])}


def write_lock(root, pkgs):
    lines = ["# 由 kite get 自动生成，请勿手改；提交到版本库以保证可复现构建", "version = 1"]
    for name in sorted(pkgs):
        p = pkgs[name]
        lines += ["", "[[package]]", f"name = {toml_str(name)}", f"version = {toml_str(p['version'])}",
                  f"source = {toml_str(p['source'])}"]
        if p.get("checksum"): lines.append(f"checksum = {toml_str(p['checksum'])}")
        lines.append("dependencies = [" + ", ".join(toml_str(d) for d in sorted(p.get("dependencies", []))) + "]")
    with open(os.path.join(root, LOCK_NAME), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ───────────────────────── 仓库 ─────────────────────────
def registry_dir():
    return os.path.abspath(os.environ.get("KITE_REGISTRY") or os.path.join(os.path.expanduser("~"), ".kite", "registry"))


def registry_index(name):
    path = os.path.join(registry_dir(), "index", name + ".json")
    if not os.path.exists(path): return None
    with open(path, encoding="utf-8") as f: return json.load(f)


def pack(root):
    """确定性打包：kite.toml + src/**，固定顺序/时间戳/属主"""
    files = [MANIFEST]
    for d, dirs, fs in os.walk(os.path.join(root, "src")):
        dirs.sort()
        for f in sorted(fs):
            if f.endswith(".kite"):
                files.append(os.path.relpath(os.path.join(d, f), root))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for rel in files:
            with open(os.path.join(root, rel), "rb") as f: data = f.read()
            ti = tarfile.TarInfo(rel.replace(os.sep, "/"))
            ti.size, ti.mtime, ti.mode, ti.uid, ti.gid, ti.uname, ti.gname = len(data), 0, 0o644, 0, 0, "", ""
            tar.addfile(ti, io.BytesIO(data))
    import gzip
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz: gz.write(buf.getvalue())
    return out.getvalue(), files


def publish(root):
    m = read_manifest(root)
    for dn, spec in m["deps"].items():
        if "path" in spec:
            raise PkgError(f"发布前请把路径依赖 {dn} 换成仓库版本（仓库里的包不能依赖本地路径）")
    lib = os.path.join(root, "src", "lib.kite")
    if not os.path.isfile(lib):
        raise PkgError("库包需要 src/lib.kite")
    # 发布前做一次完整的类型检查（包括依赖）
    ensure_installed(root, quiet=True)
    import modules
    from typecheck import Checker
    from kite import CheckFailed
    items = modules.load_program(lib)
    errs = Checker().check(items)
    if errs: raise CheckFailed(errs)
    data, files = pack(root)
    reg = registry_dir()
    os.makedirs(os.path.join(reg, "index"), exist_ok=True)
    os.makedirs(os.path.join(reg, "pkgs"), exist_ok=True)
    idx = registry_index(m["name"]) or {"name": m["name"], "versions": {}}
    if m["version"] in idx["versions"]:
        raise PkgError(f"{m['name']} {m['version']} 已经发布过，版本不可覆盖（请提升版本号）")
    rel = f"pkgs/{m['name']}-{m['version']}.tar.gz"
    with open(os.path.join(reg, rel), "wb") as f: f.write(data)
    idx["versions"][m["version"]] = {
        "sha256": hashlib.sha256(data).hexdigest(), "file": rel,
        "deps": {dn: spec["version"] for dn, spec in m["deps"].items()},
    }
    with open(os.path.join(reg, "index", m["name"] + ".json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2, sort_keys=True)
    return m, len(files), hashlib.sha256(data).hexdigest()


# ───────────────────────── 解析 ─────────────────────────
def resolve(root, prefer_lock=True):
    """返回 {名字: {"version", "source", "checksum", "dependencies", "dir"?}}"""
    m = read_manifest(root)
    lock = (read_lock(root) or {}) if prefer_lock else {}
    selected = {}            # 名字 → 版本字符串（仓库包）
    for _ in range(100):
        cons = {}            # 名字 → [(约束字符串, 来源)]
        result = {}
        seen = set()

        def visit(owner, deps, base_dir):
            for dn, spec in sorted(deps.items()):
                if "path" in spec:
                    pdir = os.path.normpath(os.path.join(base_dir, spec["path"]))
                    if not os.path.isfile(os.path.join(pdir, MANIFEST)):
                        raise PkgError(f"{owner} 的路径依赖 {dn} 不存在: {pdir}")
                    pm = read_manifest(pdir)
                    if pm["name"] != dn:
                        raise PkgError(f"路径依赖 {dn} 的 kite.toml 里名字是 {pm['name']}")
                    prev = result.get(dn)
                    if prev and prev.get("dir") != pdir and prev["source"] != "registry":
                        raise PkgError(f"依赖 {dn} 指向了两个不同的路径")
                    result[dn] = {"version": pm["version"], "source": "path+" + os.path.relpath(pdir, root),
                                  "dir": pdir, "dependencies": sorted(pm["deps"])}
                    if ("path", dn) not in seen:
                        seen.add(("path", dn)); visit(dn, pm["deps"], pdir)
                else:
                    cons.setdefault(dn, []).append((spec["version"], owner))
                    if dn in selected:
                        ver = selected[dn]
                        info = registry_index(dn)["versions"][ver]
                        result[dn] = {"version": ver, "source": "registry", "checksum": "sha256:" + info["sha256"],
                                      "dependencies": sorted(info["deps"])}
                        if (dn, ver) not in seen:
                            seen.add((dn, ver)); visit(f"{dn} {ver}", {k: {"version": v} for k, v in info["deps"].items()}, None)

        visit(m["name"], m["deps"], root)
        changed = False
        failure = None
        for dn, cs in cons.items():
            idx = registry_index(dn)
            if idx is None:
                raise PkgError(f"仓库里找不到包 {dn}（仓库: {registry_dir()}）")
            parsed = [parse_constraint(c) for c, _ in cs]
            ok = [v for v in idx["versions"] if all(satisfies(parse_version(v), p) for p in parsed)]
            if not ok:
                detail = "；".join(f"{o} 要求 {c}" for c, o in cs)
                have = ", ".join(sorted(idx["versions"], key=parse_version))
                failure = failure or PkgError(f"无法为 {dn} 选出满足所有约束的版本：{detail}（现有版本: {have}）")
                continue
            locked = lock.get(dn, {}).get("version")
            best = locked if locked in ok and lock.get(dn, {}).get("source") == "registry" else max(ok, key=parse_version)
            if selected.get(dn) != best:
                selected[dn] = best; changed = True
        for dn in list(selected):
            if dn not in cons:
                del selected[dn]; changed = True
        if not changed:
            if failure: raise failure
            return result
    raise PkgError("依赖解析没有收敛（约束互相冲突）")


def lock_stale(root):
    """清单里的直接依赖与锁文件不一致时返回 True"""
    lock = read_lock(root)
    if lock is None: return True
    m = read_manifest(root)
    for dn, spec in m["deps"].items():
        if dn not in lock: return True
        e = lock[dn]
        if "path" in spec:
            if not e["source"].startswith("path+"): return True
        else:
            if e["source"] != "registry": return True
            if not satisfies(parse_version(e["version"]), parse_constraint(spec["version"])): return True
    return False


def dep_dir(root, name, entry):
    if entry["source"].startswith("path+"):
        return os.path.normpath(os.path.join(root, entry["source"][5:]))
    return os.path.join(root, ".kite", "deps", f"{name}-{entry['version']}")


def install(root, pkgs, quiet=False):
    for name, e in sorted(pkgs.items()):
        if e["source"] != "registry": continue
        dst = dep_dir(root, name, e)
        stamp = os.path.join(dst, ".checksum")
        if os.path.exists(stamp):
            with open(stamp) as f:
                if f.read().strip() == e["checksum"]: continue
        idx = registry_index(name)
        info = idx and idx["versions"].get(e["version"])
        if not info:
            raise PkgError(f"仓库里找不到 {name} {e['version']}（锁文件指向的版本被删除了？）")
        with open(os.path.join(registry_dir(), info["file"]), "rb") as f: data = f.read()
        got = "sha256:" + hashlib.sha256(data).hexdigest()
        if got != e["checksum"] or got != "sha256:" + info["sha256"]:
            raise PkgError(f"{name} {e['version']} 校验和不匹配，包可能被篡改：期望 {e['checksum']}，实际 {got}")
        if os.path.exists(dst): shutil.rmtree(dst)
        os.makedirs(dst)
        import gzip
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(data))) as tar:
            for mem in tar.getmembers():
                if mem.name.startswith("/") or ".." in mem.name.split("/") or not mem.isfile():
                    raise PkgError(f"{name} 的包里有非法路径 {mem.name}")
            tar.extractall(dst, filter="data")
        with open(stamp, "w") as f: f.write(e["checksum"])
        if not quiet: say(f"  安装 {name} {e['version']}")


def get(root, update=False, quiet=False):
    pkgs = resolve(root, prefer_lock=not update)
    write_lock(root, {n: {k: v for k, v in e.items() if k != "dir"} for n, e in pkgs.items()})
    install(root, pkgs, quiet)
    return pkgs


def ensure_installed(root, quiet=True):
    """运行/构建前调用：锁文件缺失或过期时重新解析，缺的包补装，校验和不对时报错"""
    if not read_manifest(root)["deps"]:
        return {}
    if lock_stale(root):
        return get(root, quiet=quiet)
    lock = read_lock(root)
    install(root, lock, quiet)
    return lock


def dependency_packages(root, Package):
    """给模块加载器用：名字 → Package（所有传递依赖共享同一张表，每个包一个版本）"""
    lock = ensure_installed(root)
    table = {}
    for name, e in lock.items():
        d = dep_dir(root, name, e)
        table[name] = Package(name, d, os.path.join(d, "src"), name)
    for p in table.values():
        p.deps = table
    return table


# ───────────────────────── 命令 ─────────────────────────
def cmd_new(args):
    lib = "--lib" in args
    names = [a for a in args if not a.startswith("--")]
    if len(names) != 1:
        say("用法: kite new <名字> [--lib]"); return 2
    name = names[0]
    if not NAME_RE.match(name):
        say("包名必须是小写字母开头，只含小写字母、数字、下划线"); return 2
    if os.path.exists(name):
        say(f"{name} 已存在"); return 1
    os.makedirs(os.path.join(name, "src"))
    write_manifest(name, {"name": name, "version": "0.1.0", "deps": {}})
    if lib:
        body = "// 库的公开接口：只有 pub 项能被使用者访问\npub fn hello(who: Str) -> Str {\n  \"你好，{who}！\"\n}\n"
        fn = "lib.kite"
    else:
        body = "fn main() {\n  print(\"你好，Kite！\")\n}\n"
        fn = "main.kite"
    with open(os.path.join(name, "src", fn), "w", encoding="utf-8") as f: f.write(body)
    with open(os.path.join(name, ".gitignore"), "w") as f: f.write(".kite/\n")
    print(f"已创建{'库' if lib else '程序'}包 {name}")
    return 0


def need_root():
    root = find_root(os.getcwd())
    if root is None:
        raise PkgError("当前目录不在 Kite 包里（找不到 kite.toml，可以用 kite new 创建）")
    return root


def cmd_add(args):
    root = need_root()
    m = read_manifest(root)
    if not args:
        say("用法: kite add <名字>[@约束] | kite add <名字> --path <目录>"); return 2
    if "--path" in args:
        i = args.index("--path")
        name, path = args[0], args[i + 1]
        pm = read_manifest(os.path.join(root, path) if not os.path.isabs(path) else path)
        if pm["name"] != name: raise PkgError(f"{path} 里的包名是 {pm['name']}，不是 {name}")
        m["deps"][name] = {"path": path}
    else:
        name, _, cons = args[0].partition("@")
        idx = registry_index(name)
        if idx is None: raise PkgError(f"仓库里找不到包 {name}")
        if not cons:
            stable = [v for v in idx["versions"] if parse_version(v)[3] == 1] or list(idx["versions"])
            cons = "^" + max(stable, key=parse_version)
        parse_constraint(cons)
        m["deps"][name] = {"version": cons}
    write_manifest(root, m)
    pkgs = get(root)
    e = pkgs[name]
    print(f"已添加 {name} {e['version']}")
    return 0


def cmd_get(args, update=False):
    root = need_root()
    pkgs = get(root, update=update)
    print(f"{'已更新' if update else '已安装'} {len(pkgs)} 个依赖，锁文件 {LOCK_NAME} 已写入")
    return 0


def cmd_publish(args):
    root = need_root()
    m, n, sha = publish(root)
    print(f"已发布 {m['name']} {m['version']}（{n} 个文件，sha256 {sha[:16]}…）到 {registry_dir()}")
    return 0


def cmd_tree(args):
    root = need_root()
    m = read_manifest(root)
    lock = ensure_installed(root)
    print(f"{m['name']} {m['version']}")
    def show(names, prefix, path):
        names = sorted(names)
        for i, dn in enumerate(names):
            e = lock[dn]
            last = i == len(names) - 1
            src = "" if e["source"] == "registry" else f" ({e['source'][5:]})"
            cyc = " (循环)" if dn in path else ""
            print(f"{prefix}{'└── ' if last else '├── '}{dn} {e['version']}{src}{cyc}")
            if not cyc:
                show(e.get("dependencies", []), prefix + ("    " if last else "│   "), path | {dn})
    show(m["deps"], "", set())
    return 0


def main(cmd, args):
    try:
        if cmd == "new": return cmd_new(args)
        if cmd == "add": return cmd_add(args)
        if cmd == "get": return cmd_get(args)
        if cmd == "update": return cmd_get(args, update=True)
        if cmd == "publish": return cmd_publish(args)
        if cmd == "tree": return cmd_tree(args)
    except KiteError as e:
        from kite import CheckFailed
        if isinstance(e, CheckFailed):
            import kite
            for err in e.errors[:20]: say(kite.format_error(err))
        else:
            say(f"错误: {e.msg}")
        return 1
    return 2
