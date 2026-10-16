#!/bin/sh
# 自举第 4 步：三阶段自举，检查不动点
#   sh tools/kitec_bootstrap.sh [工作目录]        （默认 /tmp/kitec_boot）
#
#   stage0 = python3 kite.py build kitec/src/main.kite    （Python 版编译器编出的 kitec）
#   stage1 = stage0 build kitec/src/main.kite             （kitec 编译自己）
#   stage2 = stage1 build kitec/src/main.kite             （自己编出来的 kitec 再编译自己）
# 要求：stage0.c、stage1.c、stage2.c 逐字节相同（Python 版和 kitec 产出同样的 C，且 kitec 达到不动点），
# 并且 stage2 编出来的程序运行结果和字节码虚拟机一致。
set -e
cd "$(dirname "$0")/.."
D=${1:-/tmp/kitec_boot}
mkdir -p "$D"
python3 kite.py build kitec/src/main.kite -o "$D/stage0" --emit-c "$D/stage0.c" >/dev/null
"$D/stage0" build kitec/src/main.kite prelude.kite -o "$D/stage1" --emit-c "$D/stage1.c" >/dev/null
"$D/stage1" build kitec/src/main.kite prelude.kite -o "$D/stage2" --emit-c "$D/stage2.c" >/dev/null
cmp "$D/stage0.c" "$D/stage1.c" || { echo "stage0.c 与 stage1.c 不同（kitec 和 Python 版产出的 C 不一致）"; exit 1; }
cmp "$D/stage1.c" "$D/stage2.c" || { echo "stage1.c 与 stage2.c 不同（没有达到不动点）"; exit 1; }
n=0
for f in examples/fizzbuzz.kite examples/closures.kite examples/generics.kite examples/kir.kite examples/struct_update.kite; do
  "$D/stage2" build "$f" prelude.kite -o "$D/prog" >/dev/null
  "$D/prog" > "$D/prog.out" 2>&1 || true
  python3 kite.py "$f" > "$D/vm.out" 2>&1 || true
  cmp -s "$D/prog.out" "$D/vm.out" || { echo "stage2 编出的 $f 运行结果和虚拟机不同"; exit 1; }
  n=$((n+1))
done
echo "三阶段自举：stage0.c = stage1.c = stage2.c（$(wc -l < "$D/stage2.c") 行），stage2 编译的 $n 个示例与虚拟机输出一致"
