#!/bin/sh
# kitec 各层输出的回归测试：每个文件、每种模式的输出 md5 必须和 tests/gold/MODE.md5 相同
# （这些基准最初由 Python 参考实现生成，kitec 与它逐字节一致）
#   sh tools/kitec_gold.sh [KITEC] [模式…]          模式：parse check lower bc kir ck（默认全部）
#   sh tools/kitec_gold.sh --update [KITEC] [模式…]  有意改变输出后重新生成基准
cd "$(dirname "$0")/.."
update=0
[ "$1" = "--update" ] && { update=1; shift; }
K=${1:-bin/kitec}; [ $# -gt 0 ] && shift
[ $# -eq 0 ] && set -- parse check lower bc kir ck
files=$(ls examples/*.kite tests/*.kite $(find std -name "*.kite" | sort) prelude.kite examples/packages/*/src/*.kite kitec/src/*.kite)
mkdir -p tests/gold
st=0
for m in "$@"; do
  tmp=${TMPDIR:-/tmp}/kitec_gold_$m.$$
  : > "$tmp"
  for f in $files; do
    if [ $m = parse ]; then h=$(timeout 120 "$K" parse "$f" 2>&1 | md5sum | cut -c1-32)
    else h=$(timeout 120 "$K" $m "$f" prelude.kite 2>&1 | md5sum | cut -c1-32); fi
    echo "$h $f" >> "$tmp"
  done
  if [ $update = 1 ]; then mv "$tmp" "tests/gold/$m.md5"; echo "$m: 已更新 $(wc -l < tests/gold/$m.md5) 个文件"; continue; fi
  bad=$(diff "tests/gold/$m.md5" "$tmp" | grep '^>' | cut -d' ' -f3)
  n=$(wc -l < "$tmp"); nb=$(echo "$bad" | grep -c .)
  for f in $bad; do echo "不一致: $m $f"; done
  echo "$m: $n 个文件，$nb 个不一致"
  [ "$nb" -eq 0 ] || st=1
  rm -f "$tmp"
done
exit $st
