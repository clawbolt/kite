#!/bin/sh
# Kite 测试（不需要 Python；编译器是 Kite 自举的 kitec）
# 1) 示例：原生可执行文件的输出与 tests/expected/NAME.out 一致，GC 压力模式一致
# 2) GC、并发、运行时报错、可见性、标准库、KIR 等专项
# 3) tests/bad*.kite 必须被类型检查拒绝
# 4) kitec 各层输出与基准一致（tools/kitec_gold.sh），三阶段自举达到不动点（tools/kitec_bootstrap.sh）
# 环境变量：KITE_STRESS_ALL=1（go_* 示例也跑 GC 压力模式，很慢）、KITEC（默认 bin/kitec，不存在时先 sh bootstrap.sh）、KITE_EXAMPLES（只跑这些示例；设为空跳过逐个示例）、
#           KITE_GOLD_MODES（默认 "parse check lower bc kir ck"，设为空跳过）、KITE_NO_BOOT=1（跳过三阶段自举）
cd "$(dirname "$0")"; fail=0
. tools/find_cc.sh
[ -n "$CC" ] || { echo "SKIP 全部测试（未找到 C 编译器）"; exit 1; }
K=${KITEC:-bin/kitec}
case $K in /*) ;; *) K=$(pwd)/$K;; esac
[ -x "$K" ] || sh bootstrap.sh >/dev/null || { echo "FAIL 构建 kitec"; exit 1; }
export KITE_HOME=$(pwd)
T=${TMPDIR:-/tmp}/kite_tests.$$; mkdir -p "$T"
trap 'rm -rf "$T"' EXIT
mb() { sed -n "s/.*$1 \([0-9.]*\) MB.*/\1/p" "$2" | head -1; }
lt() { [ -n "$1" ] && awk -v a="$1" -v b="$2" 'BEGIN { exit !(a < b) }'; }

EXAMPLES=${KITE_EXAMPLES-examples/*.kite}
for f in $EXAMPLES; do
  case "$f" in *bench*|*gc_churn*) continue;; esac
  n=$(basename "$f" .kite)
  # KITE_GC_STRESS=1：每次分配都回收并给死对象投毒，误回收会立刻暴露。
  # go_* 示例（Go 标准库移植）在压力模式下要跑几分钟到十几分钟，默认只跑普通模式；KITE_STRESS_ALL=1 时全部跑
  stress=1; case "$n" in go_*) [ "$KITE_STRESS_ALL" = 1 ] || stress=0;; esac
  if "$K" build "$f" -o "$T/prog" >/dev/null 2>"$T/build.err" \
     && "$T/prog" > "$T/out" 2>&1 && cmp -s "$T/out" "tests/expected/$n.out" \
     && { [ $stress = 0 ] || { KITE_GC_STRESS=1 "$T/prog" > "$T/stress" 2>&1 && cmp -s "$T/stress" "tests/expected/$n.out"; }; }; then
    [ $stress = 1 ] && echo "PASS $f (输出与 tests/expected 一致，GC 压力模式一致)" || echo "PASS $f (输出与 tests/expected 一致)"
  else
    echo "FAIL $f"; cat "$T/build.err"; fail=1
  fi
done
# GC：产生约 460 MB 垃圾，峰值堆必须保持在 64 MB 以内
"$K" build examples/gc_churn.kite -o "$T/churn" >/dev/null 2>&1 \
  && KITE_GC_STATS=1 "$T/churn" > "$T/churn.out" 2>"$T/churn.err" \
  && cmp -s "$T/churn.out" tests/gc_churn.expected && lt "$(mb 峰值堆 "$T/churn.err")" 64 \
  && echo "PASS examples/gc_churn.kite (GC：$(sed 's/\[gc\] //' "$T/churn.err"))" \
  || { echo "FAIL examples/gc_churn.kite (GC)"; fail=1; }
# GC：中等大小对象（4 KB 字符串、列表元素区）走尺寸分级区块，峰值堆必须保持在 64 MB 以内
"$K" build tests/gc_mid.kite -o "$T/gcmid" >/dev/null 2>&1 \
  && KITE_GC_STATS=1 "$T/gcmid" > "$T/gcmid.out" 2>"$T/gcmid.err" \
  && cmp -s "$T/gcmid.out" tests/gc_mid.expected && lt "$(mb 峰值堆 "$T/gcmid.err")" 64 \
  && echo "PASS tests/gc_mid.kite (GC：$(sed 's/\[gc\] //' "$T/gcmid.err"))" \
  || { echo "FAIL tests/gc_mid.kite (GC)"; fail=1; }
# push / 列表与字符串拼接：共享尾部，均摊 O(1)，累计分配必须保持在 256 MB 以内
"$K" build tests/push_big.kite -o "$T/pushbig" >/dev/null 2>&1 \
  && KITE_GC_STATS=1 "$T/pushbig" > "$T/pushbig.out" 2>"$T/pushbig.err" \
  && cmp -s "$T/pushbig.out" tests/push_big.expected && lt "$(mb 累计分配 "$T/pushbig.err")" 256 \
  && echo "PASS tests/push_big.kite (push：$(sed 's/\[gc\] //' "$T/pushbig.err"))" \
  || { echo "FAIL tests/push_big.kite (push)"; fail=1; }
# list_share / str_share / concurrency：多线程 + GC 压力下反复跑，结果稳定
for ex in list_share str_share concurrency; do
  "$K" build examples/$ex.kite -o "$T/mt" >/dev/null 2>&1
  ok=1
  for i in 1 2 3 4 5; do
    KITE_THREADS=4 KITE_GC_STRESS=1 "$T/mt" > "$T/mt.out" 2>&1 && cmp -s "$T/mt.out" tests/expected/$ex.out || ok=0
  done
  [ $ok = 1 ] && echo "PASS examples/$ex.kite (4 线程 + GC 压力 5 次一致)" || { echo "FAIL examples/$ex.kite (多线程)"; fail=1; }
done
"$K" build examples/bench_parallel.kite -o "$T/bp" >/dev/null 2>&1 \
  && KITE_THREADS=2 "$T/bp" > "$T/bp.out" 2>&1 && cmp -s "$T/bp.out" tests/bench_parallel.expected \
  && echo "PASS examples/bench_parallel.kite (并行 + 多线程 GC)" || { echo "FAIL examples/bench_parallel.kite"; fail=1; }
# 数据竞争：通过闭包在任务里写外层变量，必须在编译时报错
ok=1
"$K" run tests/spawn_race.kite > "$T/race.out" 2>"$T/race.err" && ok=0
grep -q "spawn_race.kite:4: spawn 任务不能修改外层变量" "$T/race.err" || ok=0
[ $ok = 1 ] && echo "PASS tests/spawn_race.kite (拒绝跨任务写变量)" || { echo "FAIL tests/spawn_race.kite"; cat "$T/race.err"; fail=1; }
# 运行时报错：死锁检测、关闭后发送、panic、越界、溢出……分别用 1 和 4 个线程，报错位置和信息一致
for t in chan_deadlock:[0-9]*:死锁 select_deadlock:[0-9]*:死锁 chan_closed:5:向已关闭的通道发送 actor_stopped:4:actor.已经停止 task_panic:10:panic:.坏了 repanic:2:下标越界 slice_oob:3:切片越界:.1\.\.5，长度.2 shift_oob:4:移位位数超出范围:.64 overflow:11:整数溢出; do
  name=${t%%:*}; rest=${t#*:}; line=${rest%%:*}; msg=${rest#*:}
  ok=1
  "$K" build tests/$name.kite -o "$T/$name" >/dev/null 2>&1 || ok=0
  for th in 1 4; do
    if KITE_THREADS=$th timeout 20 "$T/$name" > "$T/$name.out" 2>"$T/$name.err"; then ok=0; fi
    grep -q "不应执行" "$T/$name.out" && ok=0
    grep -q "$name.kite:$line: $msg" "$T/$name.err" || ok=0
  done
  [ $ok = 1 ] && echo "PASS tests/$name.kite (1 / 4 线程一致报错: $msg)" || { echo "FAIL tests/$name.kite"; cat "$T/$name.err"; fail=1; }
done
# use … as / 通配导入（用户模块）：私有名字不会被通配导入
ok=1
[ "$(cd tests/use_alias && "$K" run main.kite 2>&1)" = "$(printf '25 0 4.0 3.0\nel\n[1,2]')" ] || ok=0
(cd tests/use_alias && "$K" check private.kite >/dev/null 2>&1) && ok=0
[ $ok = 1 ] && echo "PASS use … as / 通配导入" || { echo "FAIL use … as / 通配导入"; fail=1; }
# 内联模块 mod m { … }：跨文件访问 pub mod、私有子模块 / 私有项报错
ok=1
[ "$(cd tests/inline_mod && "$K" run cross.kite 2>&1)" = "hi! yo! 2" ] || ok=0
(cd tests/inline_mod && "$K" check bad.kite 2>&1 | grep -q "是私有") || ok=0
(cd tests/inline_mod && "$K" check bad_cross.kite 2>&1 | grep -q "util.hidden 是私有模块") || ok=0
[ $ok = 1 ] && echo "PASS 内联 mod 块" || { echo "FAIL 内联 mod 块"; fail=1; }
# pub(mod) / pub(pkg)：同目录模块可用，目录外不可用（通配导入也跳过）
ok=1
[ "$(cd tests/vis && "$K" run main.kite 2>&1)" = "GET /index /index via client 2" ] || ok=0
(cd tests/vis && "$K" check bad.kite 2>&1 | grep -q "pub(mod)，只对所在目录的模块可见") || ok=0
(cd tests/vis && "$K" check bad2.kite >/dev/null 2>&1) && ok=0
[ $ok = 1 ] && echo "PASS pub(mod) 可见性" || { echo "FAIL pub(mod) 可见性"; fail=1; }
[ "$("$K" run tests/migrate/want.kite)" = "$(printf '3 false\n4')" ] && echo "PASS tests/migrate/want.kite（迁移后的写法）" || { echo "FAIL tests/migrate/want.kite"; fail=1; }
for f in tests/bad*.kite; do
  "$K" check "$f" >/dev/null 2>&1 && { echo "FAIL $f (应报错)"; fail=1; } || echo "PASS $f (正确拒绝)"
done
# 原生 HAMT：人为制造哈希冲突（全冲突 / 3 位哈希），结果必须与正常哈希一致
if "$K" build examples/maps.kite -o "$T/maps" --emit-c "$T/maps.c" >/dev/null 2>&1; then
  want=$("$T/maps" | md5sum); ok=1
  for M in 0 7; do
    $CC -O2 -w -DKITE_HMASK=$M "$T/maps.c" -o "$T/maps_$M" -lm -lpthread && [ "$("$T/maps_$M" | md5sum)" = "$want" ] || ok=0
  done
  [ $ok = 1 ] && echo "PASS 原生 HAMT 哈希冲突路径" || { echo "FAIL 原生 HAMT 哈希冲突路径"; fail=1; }
else echo "FAIL 原生 HAMT（编译 examples/maps.kite）"; fail=1; fi
# KIR：kitec kir 能列出不装箱的函数；开启与关闭 KIR（KITE_NO_KIR=1）的原生结果一致；KIR 函数里的溢出报对行号
out=$("$K" kir examples/kir.kite)
echo "$out" | grep -q "^fn gcd " && echo "$out" | grep -q "^fn collatz " && echo "$out" | grep -q "^fn main " \
  && "$K" kir examples/select.kite | grep -q "^fn main " && ! "$K" kir examples/closures.kite | grep -q "^why " \
  && echo "PASS kitec kir 输出中间表示" || { echo "FAIL kitec kir"; fail=1; }
if "$K" build examples/kir.kite -o "$T/kir1" >/dev/null 2>&1 && KITE_NO_KIR=1 "$K" build examples/kir.kite -o "$T/kir0" >/dev/null 2>&1; then
  [ "$("$T/kir1")" = "$("$T/kir0")" ] && "$T/kir1" | cmp -s - tests/expected/kir.out \
    && echo "PASS KIR 原生输出与字节码路径一致" || { echo "FAIL KIR 原生输出"; fail=1; }
  "$K" build tests/kir_overflow.kite -o "$T/kirof" >/dev/null 2>&1
  "$T/kirof" > "$T/kirof.out" 2>"$T/kirof.err"
  grep -q "kir_overflow.kite:2: 整数溢出" "$T/kirof.err" && [ "$(cat "$T/kirof.out")" = "4611686018427387904" ] \
    && echo "PASS KIR 整数溢出报错行号" || { echo "FAIL KIR 整数溢出"; cat "$T/kirof.err"; fail=1; }
else echo "FAIL KIR（编译 examples/kir.kite）"; fail=1; fi
# KIR 第二阶段：装箱值（字符串、列表、结构体、枚举、模式、for、?、lambda 捕获）全部走 KIR，结果一致
kb=$("$K" kir tests/kir_boxed.kite)
if echo "$kb" | grep -q "^why "; then echo "FAIL KIR 装箱值覆盖"; echo "$kb" | grep "^why "; fail=1
elif "$K" build tests/kir_boxed.kite -o "$T/kb1" >/dev/null 2>&1 && KITE_NO_KIR=1 "$K" build tests/kir_boxed.kite -o "$T/kb0" >/dev/null 2>&1 \
  && [ "$("$T/kb1")" = "$("$T/kb0")" ] && "$T/kb1" | cmp -s - tests/kir_boxed.expected; then
  echo "PASS KIR 装箱值：原生输出与字节码路径一致"
else echo "FAIL KIR 装箱值输出"; fail=1; fi
# 标准库模块（std/*.kite）：输出固定；命令行参数和环境变量；std 模块走 KIR
cat > "$T/args.kite" <<'EOF2'
use std.env
fn main() { print(env.args(), env.get_or("KITE_T_VAR", "-")) }
EOF2
if "$K" run examples/stdmods.kite | cmp -s - tests/stdmods.expected \
  && "$K" run examples/http.kite | cmp -s - tests/http.expected \
  && "$K" run examples/https.kite | cmp -s - tests/https.expected \
  && "$K" run examples/sets.kite | cmp -s - tests/sets.expected \
  && ! "$K" kir examples/https.kite | grep -q "^why " \
  && ! "$K" kir examples/http.kite | grep -q "^why " \
  && ! "$K" kir examples/stdmods.kite | grep -q "^why " \
  && [ "$(KITE_T_VAR=v "$K" run "$T/args.kite" a "b c")" = '["a", "b c"] v' ] \
  && "$K" build "$T/args.kite" -o "$T/args" >/dev/null 2>&1 \
  && [ "$(KITE_T_VAR=v "$T/args" a "b c")" = '["a", "b c"] v' ]; then
  echo "PASS 标准库模块 json / strings / time / env / process / net / http / https"
else echo "FAIL 标准库模块"; fail=1; fi
if [ "$("$K" run tests/local_kstd.kite)" = "abab!" ] && ! "$K" kir tests/local_kstd.kite | grep -q "^why "; then
  echo "PASS 函数体内 use Kite 标准库模块"
else echo "FAIL 函数体内 use Kite 标准库模块"; fail=1; fi
# 对齐 Go 标准库：examples/go_*.kite 的输出必须和真正的 Go 跑 tests/go/*.go 的结果（tests/go/*.expected）逐字节相同
if KITEC=$K sh tools/go_check.sh > "$T/go.out" 2>&1; then
  echo "PASS Go 标准库对拍（$(tail -1 "$T/go.out")）"
else
  echo "FAIL Go 标准库对拍"; cat "$T/go.out"; fail=1
fi
# kitec 各层输出（AST、检查结果、检查后语法树、字节码、KIR、C）与基准一致
GOLD_MODES=${KITE_GOLD_MODES-parse check lower bc kir ck}
if [ -z "$GOLD_MODES" ]; then echo "SKIP kitec 各层输出基准（KITE_GOLD_MODES 为空）"
elif sh tools/kitec_gold.sh "$K" $GOLD_MODES > "$T/gold.out" 2>&1; then
  echo "PASS kitec 各层输出与基准一致（$(tr '\n' ' ' < "$T/gold.out")）"
else
  echo "FAIL kitec 各层输出"; grep -v "0 个不一致" "$T/gold.out" | head -20; fail=1
fi
# 三阶段自举：种子 → stage1 → stage2，C 逐字节相同
if [ "$KITE_NO_BOOT" = 1 ]; then echo "SKIP kitec 三阶段自举（KITE_NO_BOOT=1）"
elif sh tools/kitec_bootstrap.sh "$T/boot" > "$T/boot.out" 2>&1; then
  echo "PASS kitec 三阶段自举（$(tail -1 "$T/boot.out")）"
else
  echo "FAIL kitec 三阶段自举"; tail -3 "$T/boot.out"; fail=1
fi
# kitec test / fmt / 警告 / 包管理（已移植到 Kite，不需要 Python）
printf '@test\nfn checks_add() {\n  assert(1 + 1 == 2)\n}\n\nfn main() {}\n' > "$T/attr_test.kite"
"$K" test "$T/attr_test.kite" 2>&1 | grep -q "1 通过，0 失败" && echo "PASS @test 标记的测试函数" || { echo "FAIL @test"; fail=1; }
"$K" run tests/warn_deprecated.kite 2>&1 >/dev/null | grep -q "warn_deprecated.kite:5: 警告: old_parse 已弃用：改用 parse_int" && echo "PASS @deprecated 警告" || { echo "FAIL @deprecated"; fail=1; }
if "$K" fmt tests/fmt/messy.kite | diff -q - tests/fmt/want.kite >/dev/null && "$K" fmt tests/fmt/want.kite | diff -q - tests/fmt/want.kite >/dev/null; then
  echo "PASS kitec fmt 规范化缩进、空格、空行并对齐 match 分支（幂等）"
else echo "FAIL kitec fmt"; fail=1; fi
idem=0
for f in examples/*.kite; do
  "$K" fmt "$f" > "$T/f1.kite" 2>/dev/null && "$K" fmt "$T/f1.kite" | cmp -s - "$T/f1.kite" || { echo "  fmt 不幂等: $f"; idem=1; }
done
[ $idem = 0 ] && echo "PASS kitec fmt 对全部示例幂等" || { echo "FAIL kitec fmt 幂等"; fail=1; }
sh tests/packages.sh "$K" || fail=1
sh tests/lsp.sh "$K" || fail=1
exit $fail
