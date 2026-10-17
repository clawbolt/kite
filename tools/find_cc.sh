# 用 . tools/find_cc.sh 引入：设置 CC 为可用的 C 编译器（$CC，然后 cc / clang / gcc / zig cc）；找不到时 CC 为空
if [ -z "$CC" ]; then
  for c in cc clang gcc; do command -v $c >/dev/null 2>&1 && { CC=$c; break; }; done
  [ -z "$CC" ] && command -v zig >/dev/null 2>&1 && CC="zig cc -mcpu=baseline"
fi
export CC
