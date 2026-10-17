#!/bin/sh
# 模块系统与包管理的端到端测试（不需要 Python）：在临时目录里建仓库、发布、解析、安装、编译运行、kitec test
#   sh tests/packages.sh [KITEC]
cd "$(dirname "$0")/.."
K=${1:-${KITEC:-bin/kitec}}
case $K in /*) ;; *) K=$(pwd)/$K;; esac
export KITE_HOME=$(pwd)
FIX=$(pwd)/tests/fixtures
tmp=$(mktemp -d "${TMPDIR:-/tmp}/kite_pkg_XXXXXX")
trap 'rm -rf "$tmp"' EXIT
export KITE_REGISTRY="$tmp/registry"
ws="$tmp/ws"
cp -r examples/packages "$ws"
fails=0
pass() { echo "PASS 包管理: $1"; }
failm() { echo "FAIL 包管理: $1"; [ -n "$2" ] && echo "$2"; fails=$((fails+1)); }
# 在目录 $1 里运行 kitec 其余参数；输出（stdout+stderr）存进 $out，返回 kitec 的退出码
run() { d=$1; shift; out=$(cd "$d" && "$K" "$@" 2>&1); }
has() { case "$out" in *"$1"*) return 0;; esac; return 1; }
set_version() { sed "s/^version = \".*\"\$/version = \"$2\"/" "$ws/$1/kite.toml" > "$tmp/t.toml" && mv "$tmp/t.toml" "$ws/$1/kite.toml"; }
EXPECTED='== 几何报告 ==
距离: 5.0
- 圆，面积 3.14159
- 正方形，面积 4.0
总面积: 7.14159
app 自己的 helper'

# 语义化版本约束
ok=1; msg=""
for c in "^1.2.3|1.2.3|true" "^1.2.3|1.9.0|true" "^1.2.3|2.0.0|false" "^1.2.3|1.2.2|false" "^0.2.3|0.2.9|true" \
  "^0.2.3|0.3.0|false" "^0.0.3|0.0.4|false" "1.2|1.5.0|true" "~1.2.3|1.2.9|true" "~1.2.3|1.3.0|false" "=1.2.3|1.2.4|false" \
  "*|9.9.9|true" ">=1.0, <1.5|1.4.9|true" ">=1.0, <1.5|1.5.0|false" "^1.0|1.1.0-beta.1|false" \
  ">=2.0.0-beta.1|2.0.0-beta.1|true" "^2.0.0-beta.1|2.0.0|true"; do
  con=${c%%|*}; rest=${c#*|}; v=${rest%%|*}; want=${rest#*|}
  got=$("$K" satisfies "$v" "$con" 2>&1)
  [ "$got" = "$want" ] || { ok=0; msg="$msg $v 满足 $con？期望 $want，实际 $got;"; }
done
[ $ok = 1 ] && pass "语义化版本约束（17 例）" || failm "语义化版本约束" "$msg"

ok=1
for pv in mathx:0.2.0 mathx:0.2.1 mathx:0.3.0 geom:1.0.0 geom:1.1.0 geom:2.0.0-beta.1; do
  p=${pv%%:*}; v=${pv#*:}
  set_version $p $v
  run "$ws/$p" publish || { ok=0; echo "$out"; }
done
[ $ok = 1 ] && pass "发布 6 个版本" || failm "发布 6 个版本"

run "$ws/geom" publish && failm "同一版本不能重复发布" "$out" || { has "不可覆盖" && pass "同一版本不能重复发布" || failm "同一版本不能重复发布" "$out"; }

ok=1
run "$ws/app" get || ok=0
lock=$(cat "$ws/app/kite.lock" 2>/dev/null)
case "$lock" in *'name = "geom"
version = "1.1.0"'*) ;; *) ok=0;; esac
case "$lock" in *'name = "mathx"
version = "0.2.1"'*) ;; *) ok=0;; esac
case "$lock" in *'source = "path+../util"'*) ;; *) ok=0;; esac
case "$lock" in *'checksum = "sha256:'*) ;; *) ok=0;; esac
run "$ws/app" tree || ok=0
has '├── geom 1.1.0
│   └── mathx 0.2.1
└── util 0.1.0 (../util)' || ok=0
[ $ok = 1 ] && pass "语义化版本解析 + 传递依赖 + 路径依赖 + 锁文件" || failm "语义化版本解析" "$lock
$out"

run "$ws/app" run && [ "$out" = "$EXPECTED" ] && pass "kitec run 运行多模块程序" || failm "kitec run 运行多模块程序" "$out"

if run "$ws/app" build -o app_bin && [ "$("$ws/app/app_bin")" = "$EXPECTED" ]; then pass "原生编译多模块程序"; else failm "原生编译多模块程序" "$out"; fi

ok=1
set_version geom 1.2.0; run "$ws/geom" publish || ok=0
run "$ws/app" get || ok=0
case "$(cat "$ws/app/kite.lock")" in *'name = "geom"
version = "1.1.0"'*) ;; *) ok=0; echo "有锁文件时不应自动升级";; esac
run "$ws/app" update || ok=0
case "$(cat "$ws/app/kite.lock")" in *'name = "geom"
version = "1.2.0"'*) ;; *) ok=0;; esac
[ $ok = 1 ] && pass "锁文件保证可复现，update 才升级" || failm "锁文件保证可复现，update 才升级"

# 打包是确定性的：同一份源码发布到两个仓库，tar.gz 逐字节相同
mkdir -p "$tmp/det"; cp -r "$ws/mathx" "$tmp/det/m"
(cd "$tmp/det/m" && KITE_REGISTRY="$tmp/r1" "$K" publish >/dev/null 2>&1 && KITE_REGISTRY="$tmp/r2" "$K" publish >/dev/null 2>&1)
v=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$tmp/det/m/kite.toml" | head -1)
cmp -s "$tmp/r1/pkgs/mathx-$v.tar.gz" "$tmp/r2/pkgs/mathx-$v.tar.gz" && [ -s "$tmp/r1/pkgs/mathx-$v.tar.gz" ] \
  && pass "打包是确定性的" || failm "打包是确定性的"

cp "$ws/app/kite.toml" "$tmp/app.toml"
sed 's/^geom = "^1.0"$/geom = "^1.0"\nmathx = "^0.3"/' "$tmp/app.toml" > "$ws/app/kite.toml"
run "$ws/app" get && st=0 || st=1
cp "$tmp/app.toml" "$ws/app/kite.toml"
[ $st = 1 ] && has "无法为 mathx 选出" && has "要求 ^0.2" && has "app 要求 ^0.3" && pass "版本冲突给出完整的约束来源" || failm "版本冲突给出完整的约束来源" "$out"

ok=1; src="$ws/app/src"
printf 'use cyc_b\npub fn a() -> Int { 1 }\n' > "$src/cyc_a.kite"
printf 'use cyc_a\npub fn b() -> Int { 2 }\n' > "$src/cyc_b.kite"
printf 'pub fn f() -> Int {\n  "x" + 1\n}\n' > "$src/bad_mod.kite"
printf 'use geom\nfn main() { print(geom.helper()) }\n' > "$src/t_private.kite"
printf 'use nothere\nfn main() {}\n' > "$src/t_missing.kite"
printf 'use geom.nope\nfn main() {}\n' > "$src/t_noitem.kite"
printf 'use geom\nfn main() { print(geom.unit()) }\n' > "$src/t_pkgvis.kite"
printf 'use cyc_a\nfn main() { print(cyc_a.a()) }\n' > "$src/t_cycle.kite"
printf 'use bad_mod\nfn main() { print(bad_mod.f()) }\n' > "$src/t_typeerr.kite"
for c in "t_private|geom.helper 是私有的" "t_missing|找不到模块 nothere" "t_noitem|模块 geom 里没有 nope" \
  "t_pkgvis|geom.unit 是 pub(pkg)，只对本包可见" "t_cycle|模块循环引用: cyc_a → cyc_b → cyc_a" "t_typeerr|src/bad_mod.kite:2: 类型错误"; do
  f=${c%%|*}; want=${c#*|}
  if run "$ws/app" "src/$f.kite"; then ok=0; echo "$f 应该失败"; elif ! has "$want"; then ok=0; echo "$f: 期望包含 $want，实际 $out"; fi
done
[ $ok = 1 ] && pass "私有项 / 缺失模块 / 循环引用 / 模块内类型错误定位" || failm "私有项 / 缺失模块 / 循环引用 / 模块内类型错误定位"

ok=1
run "$ws/geom" test && has "结果: 5 通过，0 失败" && has "✓ test_helper" && has "✓ test_describe" || { ok=0; echo "$out"; }
run "$ws/geom" test norm && has "结果: 1 通过" || { ok=0; echo "$out"; }
run "$tmp" test "$FIX/calc_test.kite" && { ok=0; echo "calc_test 应该失败"; }
has "结果: 2 通过，2 失败" && has "calc_test.kite:20: assert_eq 失败（加法）: 左边 4，右边 5" && has "调试输出" && ! has "不应运行" && has "unwrap 失败: None" || { ok=0; echo "$out"; }
[ $ok = 1 ] && pass "kitec test：单元/集成测试、过滤、失败报告" || failm "kitec test"

p="$KITE_REGISTRY/pkgs/mathx-0.2.1.tar.gz"
sz=$(wc -c < "$p"); off=$((sz - 12))
b=$(od -An -tu1 -j $off -N1 "$p" | tr -d ' ')
printf "$(printf '\\%03o' $((b ^ 255)))" | dd of="$p" bs=1 seek=$off conv=notrunc 2>/dev/null
rm -rf "$ws/app/.kite"
run "$ws/app" run && failm "被篡改的包拒绝安装" "$out" || { has "校验和不匹配" && pass "被篡改的包拒绝安装" || failm "被篡改的包拒绝安装" "$out"; }

ok=1
run "$tmp" new hello || ok=0
run "$tmp" new greet --lib || ok=0
run "$tmp/hello" add greet --path ../greet && has "已添加 greet 0.1.0" || { ok=0; echo "$out"; }
printf 'use greet.hello\nfn main() {\n  print(hello("Kite"))\n}\n' > "$tmp/hello/src/main.kite"
run "$tmp/hello" run && [ "$out" = "你好，Kite！" ] || { ok=0; echo "$out"; }
set_version mathx 0.3.1; run "$ws/mathx" publish || ok=0
run "$tmp/hello" add mathx && has "已添加 mathx 0.3.1" || { ok=0; echo "$out"; }
grep -q 'mathx = "^0.3.1"' "$tmp/hello/kite.toml" || ok=0
[ $ok = 1 ] && pass "kitec new / add（路径与仓库）" || failm "kitec new / add（路径与仓库）"
exit $([ $fails = 0 ] && echo 0 || echo 1)
