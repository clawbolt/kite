#!/bin/sh
# 三阶段自举（不需要 Python）
#   sh tools/kitec_bootstrap.sh [工作目录]                 （默认 /tmp/kitec_boot）
#   sh tools/kitec_bootstrap.sh --update-seed [工作目录]   改了 kitec 源码后刷新种子 bootstrap/kitec.c
#   stage0 = C 编译器编 bootstrap/kitec.c（种子）
#   stage1 = stage0 build kitec/src/main.kite
#   stage2 = stage1 build kitec/src/main.kite
# 要求：stage1.c = stage2.c（不动点），并且种子就是 stage1.c（种子没有过期）；stage2 编出的示例输出与 tests/expected 一致
cd "$(dirname "$0")/.."
update=0
[ "$1" = "--update-seed" ] && { update=1; shift; }
D=${1:-/tmp/kitec_boot}
mkdir -p "$D"
. tools/find_cc.sh
[ -n "$CC" ] || { echo "找不到 C 编译器"; exit 1; }
export KITE_HOME=$(pwd)
$CC -O2 -w -o "$D/stage0" bootstrap/kitec.c -lm -lpthread || { echo "种子编译失败"; exit 1; }
"$D/stage0" build kitec/src/main.kite -o "$D/stage1" --emit-c "$D/stage1.c" >/dev/null || { echo "stage0 编译 kitec 失败"; exit 1; }
"$D/stage1" build kitec/src/main.kite -o "$D/stage2" --emit-c "$D/stage2.c" >/dev/null || { echo "stage1 编译 kitec 失败"; exit 1; }
cmp -s "$D/stage1.c" "$D/stage2.c" || { echo "stage1.c 与 stage2.c 不同（没有达到不动点）"; exit 1; }
if [ $update = 1 ]; then
  cmp -s "$D/stage2.c" bootstrap/kitec.c || { cp "$D/stage2.c" bootstrap/kitec.c; echo "已刷新种子 bootstrap/kitec.c"; }
else
  cmp -s "$D/stage1.c" bootstrap/kitec.c || { echo "种子 bootstrap/kitec.c 过期：运行 sh tools/kitec_bootstrap.sh --update-seed"; exit 1; }
fi
n=0
for f in examples/fizzbuzz.kite examples/closures.kite examples/generics.kite examples/kir.kite examples/struct_update.kite; do
  "$D/stage2" build "$f" -o "$D/prog" >/dev/null
  "$D/prog" > "$D/prog.out" 2>&1 || true
  cmp -s "$D/prog.out" "tests/expected/$(basename "$f" .kite).out" || { echo "stage2 编出的 $f 运行结果和 tests/expected 不同"; exit 1; }
  n=$((n+1))
done
echo "三阶段自举：种子 = stage1.c = stage2.c（$(wc -l < "$D/stage2.c") 行），stage2 编译的 $n 个示例输出一致"
