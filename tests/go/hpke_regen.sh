#!/bin/sh
# 重新生成 tests/go/hpke.expected：GO=/path/to/go sh tests/go/hpke_regen.sh（HPKE_N 默认 20）
cd "$(dirname "$0")"
GO=${GO:-go}
dst=$($GO env GOROOT)/src/crypto/hpke/zz_kite_gen_test.go
grep -v '^//go:build ignore' hpke.go > "$dst"
HPKE_N=${HPKE_N:-20} $GO test -v -count=1 -run '^TestKiteGen$' crypto/hpke 2>&1 | grep -v -e '^PASS$' -e '^ok  	crypto/hpke' -e '^=== RUN' -e '^--- PASS' -e '^    --- PASS' > hpke.expected
rm -f "$dst"
