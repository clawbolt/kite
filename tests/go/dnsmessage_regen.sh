#!/bin/sh
# 重新生成 tests/go/dnsmessage.expected：GO=/path/to/go sh tests/go/dnsmessage_regen.sh
cd "$(dirname "$0")"
GO=${GO:-go}
src=$($GO env GOROOT)/src/vendor/golang.org/x/net/dns/dnsmessage
d=$(mktemp -d)
for f in message.go svcb.go; do sed 's/^package dnsmessage$/package main/' "$src/$f" > "$d/$f"; done
cp dnsmessage.go "$d/main.go"
(cd "$d" && GO111MODULE=off $GO run .) > dnsmessage.expected 2>&1
rm -rf "$d"
