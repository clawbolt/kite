#!/bin/sh
# 自举第 1 步的对拍：kitec（Kite 写的词法 + 语法分析器）对每个文件输出的 AST 必须和 Python 版逐字节相同
#   sh tools/kitec_check.sh KITEC_BIN [文件…]   不给文件时检查全部 examples / tests / std / prelude / kitec 源码
bin=$1; shift
[ $# -eq 0 ] && set -- examples/*.kite tests/*.kite $(find std -name "*.kite" | sort) prelude.kite examples/packages/*/src/*.kite kitec/src/*.kite
ok=0; bad=0
for f in "$@"; do
  "$bin" parse "$f" > /tmp/kitec_a.txt 2>&1
  python3 tools/ast_dump.py "$f" > /tmp/kitec_b.txt 2>&1
  if cmp -s /tmp/kitec_a.txt /tmp/kitec_b.txt; then ok=$((ok+1)); else bad=$((bad+1)); echo "不一致: $f"; fi
done
echo "kitec 对拍：$ok 个一致，$bad 个不一致"
[ $bad -eq 0 ]
