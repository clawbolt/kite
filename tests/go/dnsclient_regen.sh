#!/bin/sh
# 重新生成 tests/go/dnsclient.expected 和 testdata/dnsclient/responses.txt：GO=/path/to/go sh tests/go/dnsclient_regen.sh
cd "$(dirname "$0")"
GO=${GO:-go}
dst=$($GO env GOROOT)/src/net/zz_kite_gen_test.go
grep -v '^//go:build ignore' dnsclient.go > "$dst"
KITE_RESP=$(pwd)/testdata/dnsclient/responses.txt $GO test -v -count=1 -run '^TestKiteGen$' net 2>&1 | grep -v -e '^PASS$' -e '^ok  	net' -e '^=== RUN' -e '^--- PASS' | sed '/^Socket statistical information:/,$d' > dnsclient.expected
rm -f "$dst"
