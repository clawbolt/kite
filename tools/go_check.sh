#!/bin/sh
# 对齐 Go 标准库的对拍：examples/go_NAME.kite 在虚拟机上的输出必须和 tests/go/NAME.expected（真正的 Go 跑 tests/go/NAME.go 的输出）逐字节相同
#   sh tools/go_check.sh [NAME…]      不给名字时检查全部
#   有 Go 工具链时可以用 GO=/path/to/go sh tools/go_check.sh --regen 重新生成 .expected
cd "$(dirname "$0")/.."
regen=0
[ "$1" = "--regen" ] && { regen=1; shift; }
[ $# -eq 0 ] && set -- $(ls tests/go/*.go | sed 's|tests/go/||; s|\.go$||')
ok=0; bad=0
for n in "$@"; do
  if [ $regen = 1 ]; then (cd tests/go && ${GO:-go} run "$n.go" > "$n.expected" 2>&1); fi
  python3 kite.py "examples/go_$n.kite" > /tmp/kite_go_$n.out 2>&1
  if cmp -s /tmp/kite_go_$n.out "tests/go/$n.expected"; then ok=$((ok+1)); else bad=$((bad+1)); echo "不一致: $n"; fi
done
echo "Go 对拍：$ok 个一致，$bad 个不一致"
[ $bad -eq 0 ]
