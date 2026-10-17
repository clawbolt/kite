#!/bin/sh
# 构建 kitec（不需要 Python）：先用 bootstrap/kitec.c（kitec 编译自己产出的 C，即种子）编出 kitec，再用它从源码重编一次
#   sh bootstrap.sh        生成 bin/kitec；之后 bin/kitec run / build / check …（也可以把 bin 加进 PATH）
# 需要一个 C 编译器：$CC，或 cc / clang / gcc / zig
cd "$(dirname "$0")"
. tools/find_cc.sh
[ -n "$CC" ] || { echo "找不到 C 编译器：请安装 clang / gcc / zig，或设置环境变量 CC"; exit 1; }
mkdir -p bin
$CC -O2 -w -o bin/kitec.seed bootstrap/kitec.c -lm -lpthread || { echo "编译种子失败"; exit 1; }
KITE_HOME=$(pwd) bin/kitec.seed build kitec/src/main.kite -o bin/kitec.new >/dev/null || { echo "kitec 编译自身失败"; exit 1; }
mv bin/kitec.new bin/kitec && rm -f bin/kitec.seed
echo "已生成 bin/kitec"
