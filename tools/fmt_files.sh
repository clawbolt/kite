#!/bin/sh
# 仓库里要求格式化过的 .kite 文件（kitec fmt --check 用）
# 不含：tests/bad_*（故意写错，期望输出带行列号）、tests/warn_*（期望输出带行号）、
#       tests/lsp/（回放测试依赖原始位置）、tests/fmt/messy.kite（格式化器的输入样例）
cd "$(dirname "$0")/.."
find . -name "*.kite" -not -path "./.git/*" -not -path "./tests/lsp/*" -not -name "bad_*.kite" -not -name "warn_*.kite" \
  -not -path "./tests/fmt/messy.kite" | sed 's|^\./||' | sort
