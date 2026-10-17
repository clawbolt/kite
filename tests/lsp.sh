#!/bin/sh
# 语言服务器回放测试（不需要 Python）：tests/lsp/in.jsonl 每行一条消息，按 Content-Length 分帧喂给 kitec lsp，
# 输出的每条消息（JSON 键已排序）逐行和 tests/lsp/want.jsonl 比较。@T@ 代表放示例文件的临时目录。
#   sh tests/lsp.sh [KITEC] [--update]
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
KITEC=${1:-$ROOT/bin/kitec}
[ "$2" = "--update" ] && UPDATE=1 || UPDATE=
export KITE_HOME=${KITE_HOME:-$ROOT}
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
T=$(cd "$T" && pwd -P)
cp "$ROOT"/tests/lsp/files/*.kite "$T"/
: > "$T/in.bin"
while IFS= read -r line; do
  body=$(printf '%s' "$line" | sed "s|@T@|$T|g")
  n=$(printf '%s' "$body" | wc -c | tr -d ' ')
  printf 'Content-Length: %s\r\n\r\n%s' "$n" "$body" >> "$T/in.bin"
done < "$ROOT/tests/lsp/in.jsonl"
if [ -n "$KITE_LSP" ]; then (cd "$T" && $KITE_LSP < in.bin > out.bin); else (cd "$T" && "$KITEC" lsp < in.bin > out.bin); fi
# 每条消息的正文：去掉头部，一条一行，临时目录换回 @T@
tr -d '\r' < "$T/out.bin" | sed 's/Content-Length: [0-9]*/\n/g' | sed '/^$/d' | sed "s|$T|@T@|g" > "$T/got.jsonl"
if [ -n "$UPDATE" ]; then
  cp "$T/got.jsonl" "$ROOT/tests/lsp/want.jsonl"
  echo "已更新 tests/lsp/want.jsonl（$(wc -l < "$T/got.jsonl") 条消息）"
  exit 0
fi
if diff "$ROOT/tests/lsp/want.jsonl" "$T/got.jsonl" > "$T/diff"; then
  echo "PASS lsp 回放（$(wc -l < "$T/got.jsonl") 条消息）"
else
  echo "FAIL lsp 回放"; head -20 "$T/diff"; exit 1
fi
