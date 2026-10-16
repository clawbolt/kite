#!/bin/sh
# 1) 示例在虚拟机上类型检查并运行成功
# 2) 差分测试：虚拟机与树遍历解释器输出必须完全一致
# 3) tests/bad*.kite 必须被类型检查拒绝
# 4) 格式化器与语言服务器测试
# 5) 原生后端：每个示例编译成可执行文件，输出与虚拟机一致
cd "$(dirname "$0")"; fail=0
# KITE_EXAMPLES="" 时跳过逐个示例的两轮循环（tools/par_examples.py 可以并行跑这部分）
EXAMPLES=${KITE_EXAMPLES-examples/*.kite}
for f in $EXAMPLES; do
  case "$f" in *bench*|*gc_churn*) continue;; esac
  if python3 kite.py "$f" > /tmp/kite_vm.out 2>&1 && python3 kite.py --tree "$f" > /tmp/kite_tree.out 2>&1 \
     && cmp -s /tmp/kite_vm.out /tmp/kite_tree.out; then
    echo "PASS $f (VM 与解释器输出一致)"
  else
    echo "FAIL $f"; fail=1
  fi
done
# 原生后端：编译成可执行文件，输出必须与虚拟机一致（没有 C 编译器时跳过）
if python3 -c "import sys; sys.path.insert(0, '.'); import kite; sys.modules['kite'] = kite; import native; sys.exit(0 if native.find_cc() else 1)"; then
  for f in $EXAMPLES; do
    case "$f" in *bench_native*|*bench_parallel*|*gc_churn*) continue;; esac
    # KITE_GC_STRESS=1：每次分配都回收并给死对象投毒，误回收会立刻暴露
    if python3 kite.py build "$f" -o /tmp/kite_native_bin >/dev/null 2>/tmp/kite_build.err \
       && /tmp/kite_native_bin > /tmp/kite_nat.out 2>&1 && python3 kite.py "$f" > /tmp/kite_vm.out 2>&1 \
       && cmp -s /tmp/kite_nat.out /tmp/kite_vm.out \
       && KITE_GC_STRESS=1 /tmp/kite_native_bin > /tmp/kite_stress.out 2>&1 && cmp -s /tmp/kite_stress.out /tmp/kite_vm.out; then
      echo "PASS $f (原生可执行文件与虚拟机输出一致，GC 压力模式一致)"
    else
      echo "FAIL $f (原生)"; cat /tmp/kite_build.err; fail=1
    fi
  done
  # GC：产生约 460 MB 垃圾，峰值堆必须保持在 64 MB 以内
  python3 kite.py build examples/gc_churn.kite -o /tmp/kite_churn >/dev/null 2>&1 \
    && KITE_GC_STATS=1 /tmp/kite_churn > /tmp/kite_churn.out 2>/tmp/kite_churn.err \
    && cmp -s /tmp/kite_churn.out tests/gc_churn.expected \
    && python3 -c "import re,sys; m=re.search(r'峰值堆 ([0-9.]+) MB', open('/tmp/kite_churn.err').read()); sys.exit(0 if m and float(m.group(1)) < 64 else 1)" \
    && echo "PASS examples/gc_churn.kite (GC：$(sed 's/\[gc\] //' /tmp/kite_churn.err))" \
    || { echo "FAIL examples/gc_churn.kite (GC)"; fail=1; }
  # GC：中等大小对象（4 KB 字符串、列表元素区）走尺寸分级区块，峰值堆必须保持在 64 MB 以内
  python3 kite.py build tests/gc_mid.kite -o /tmp/kite_gcmid >/dev/null 2>&1 \
    && KITE_GC_STATS=1 /tmp/kite_gcmid > /tmp/kite_gcmid.out 2>/tmp/kite_gcmid.err \
    && cmp -s /tmp/kite_gcmid.out tests/gc_mid.expected \
    && python3 -c "import re,sys; m=re.search(r'峰值堆 ([0-9.]+) MB', open('/tmp/kite_gcmid.err').read()); sys.exit(0 if m and float(m.group(1)) < 64 else 1)" \
    && echo "PASS tests/gc_mid.kite (GC：$(sed 's/\[gc\] //' /tmp/kite_gcmid.err))" \
    || { echo "FAIL tests/gc_mid.kite (GC)"; fail=1; }
  # push / 列表与字符串拼接：共享尾部，均摊 O(1)，累计分配必须保持在 256 MB 以内
  python3 kite.py build tests/push_big.kite -o /tmp/kite_pushbig >/dev/null 2>&1 \
    && KITE_GC_STATS=1 /tmp/kite_pushbig > /tmp/kite_pushbig.out 2>/tmp/kite_pushbig.err \
    && cmp -s /tmp/kite_pushbig.out tests/push_big.expected \
    && python3 -c "import re,sys; m=re.search(r'累计分配 ([0-9.]+) MB', open('/tmp/kite_pushbig.err').read()); sys.exit(0 if m and float(m.group(1)) < 256 else 1)" \
    && echo "PASS tests/push_big.kite (push：$(sed 's/\[gc\] //' /tmp/kite_pushbig.err))" \
    || { echo "FAIL tests/push_big.kite (push)"; fail=1; }
  # list_share / str_share：多线程 + GC 压力下共享尾部的抢占必须稳定
  for ex in list_share str_share; do
    python3 kite.py build examples/$ex.kite -o /tmp/kite_ls >/dev/null 2>&1 && python3 kite.py examples/$ex.kite > /tmp/kite_ls.ref 2>&1
    ls_ok=1
    for i in 1 2 3 4 5; do
      KITE_THREADS=4 KITE_GC_STRESS=1 /tmp/kite_ls > /tmp/kite_ls.out 2>&1 && cmp -s /tmp/kite_ls.out /tmp/kite_ls.ref || ls_ok=0
    done
    [ $ls_ok = 1 ] && echo "PASS examples/$ex.kite (多线程 + GC 压力 5 次一致)" || { echo "FAIL examples/$ex.kite (多线程)"; fail=1; }
  done
  # 并发：多线程 + GC 压力反复跑，检查结果稳定
  python3 kite.py build examples/concurrency.kite -o /tmp/kite_conc >/dev/null 2>&1 && python3 kite.py examples/concurrency.kite > /tmp/kite_conc.ref 2>&1
  conc_ok=1
  for i in 1 2 3 4 5; do
    KITE_THREADS=4 KITE_GC_STRESS=1 /tmp/kite_conc > /tmp/kite_conc.out 2>&1 && cmp -s /tmp/kite_conc.out /tmp/kite_conc.ref || conc_ok=0
  done
  [ $conc_ok = 1 ] && echo "PASS examples/concurrency.kite (4 线程 + GC 压力模式 ×5 结果一致)" || { echo "FAIL examples/concurrency.kite (多线程)"; fail=1; }
  python3 kite.py build examples/bench_parallel.kite -o /tmp/kite_bp >/dev/null 2>&1 \
    && KITE_THREADS=2 /tmp/kite_bp > /tmp/kite_bp.out 2>&1 && cmp -s /tmp/kite_bp.out tests/bench_parallel.expected \
    && echo "PASS examples/bench_parallel.kite (并行 + 多线程 GC)" || { echo "FAIL examples/bench_parallel.kite"; fail=1; }
  # 数据竞争：通过闭包在任务里写外层变量，三个引擎都必须在同一处报同样的错
  race_ok=1
  python3 kite.py build tests/spawn_race.kite -o /tmp/kite_race >/dev/null 2>&1 || race_ok=0
  for run in "python3 kite.py tests/spawn_race.kite" "python3 kite.py --tree tests/spawn_race.kite" "/tmp/kite_race"; do
    if $run > /tmp/kite_race.out 2>/tmp/kite_race.err; then race_ok=0; fi
    grep -q "^任务外修改可以: 1$" /tmp/kite_race.out && ! grep -q "不应执行" /tmp/kite_race.out || race_ok=0
    grep -q "spawn_race.kite:4: spawn 任务不能修改外层变量" /tmp/kite_race.err || race_ok=0
  done
  [ $race_ok = 1 ] && echo "PASS tests/spawn_race.kite (三个引擎都拒绝跨任务写变量)" || { echo "FAIL tests/spawn_race.kite"; fail=1; }
  # 通道：死锁检测与关闭后发送，三个引擎（原生分别用 1 和 4 个线程）报同样的错
  for t in chan_deadlock:[0-9]*:死锁 select_deadlock:[0-9]*:死锁 chan_closed:5:向已关闭的通道发送 actor_stopped:4:actor.已经停止 task_panic:10:panic:.坏了 repanic:2:下标越界 slice_oob:3:切片越界:.1\.\.5，长度.2 shift_oob:4:移位位数超出范围:.64 overflow:11:整数溢出; do
    name=${t%%:*}; rest=${t#*:}; line=${rest%%:*}; msg=${rest#*:}
    ok=1
    python3 kite.py build tests/$name.kite -o /tmp/kite_$name >/dev/null 2>&1 || ok=0
    for run in "python3 kite.py tests/$name.kite" "python3 kite.py --tree tests/$name.kite" "env KITE_THREADS=1 /tmp/kite_$name" "env KITE_THREADS=4 /tmp/kite_$name"; do
      if timeout 20 $run > /tmp/kite_$name.out 2>/tmp/kite_$name.err; then ok=0; fi
      grep -q "不应执行" /tmp/kite_$name.out && ok=0
      grep -q "$name.kite:$line: $msg" /tmp/kite_$name.err || ok=0
    done
    [ $ok = 1 ] && echo "PASS tests/$name.kite (三个引擎一致报错: $msg)" || { echo "FAIL tests/$name.kite"; cat /tmp/kite_$name.err; fail=1; }
  done
else
  echo "SKIP 原生后端测试（未找到 C 编译器）"
fi
printf '@test\nfn checks_add() {\n  assert(1 + 1 == 2)\n}\n\nfn main() {}\n' > /tmp/kite_attr_test.kite
python3 kite.py test /tmp/kite_attr_test.kite 2>&1 | grep -q "1 通过，0 失败" && echo "PASS @test 标记的测试函数" || { echo "FAIL @test"; fail=1; }
python3 kite.py tests/warn_deprecated.kite 2>&1 >/dev/null | grep -q "warn_deprecated.kite:5: 警告: old_parse 已弃用：改用 parse_int" && echo "PASS @deprecated 警告" || { echo "FAIL @deprecated"; fail=1; }
# use … as / 通配导入（用户模块）：三引擎输出一致，私有名字不会被通配导入
want=$(printf '25 0 4.0 3.0\nel\n[1,2]')
ok=1
for eng in "" "--tree"; do got=$(cd tests/use_alias && python3 ../../kite.py $eng main.kite 2>&1); [ "$got" = "$want" ] || { ok=0; echo "$got"; }; done
(cd tests/use_alias && python3 ../../kite.py check private.kite >/dev/null 2>&1) && ok=0
[ $ok = 1 ] && echo "PASS use … as / 通配导入" || { echo "FAIL use … as / 通配导入"; fail=1; }
# 内联模块 mod m { … }：跨文件访问 pub mod、私有子模块 / 私有项报错
ok=1
[ "$(cd tests/inline_mod && python3 ../../kite.py cross.kite 2>&1)" = "hi! yo! 2" ] || ok=0
(cd tests/inline_mod && python3 ../../kite.py check bad.kite 2>&1 | grep -q "是私有") || ok=0
(cd tests/inline_mod && python3 ../../kite.py check bad_cross.kite 2>&1 | grep -q "util.hidden 是私有模块") || ok=0
[ $ok = 1 ] && echo "PASS 内联 mod 块" || { echo "FAIL 内联 mod 块"; fail=1; }
# pub(mod) / pub(pkg)：同目录模块可用，目录外不可用（通配导入也跳过）
ok=1
[ "$(cd tests/vis && python3 ../../kite.py main.kite 2>&1)" = "GET /index /index via client 2" ] || ok=0
(cd tests/vis && python3 ../../kite.py check bad.kite 2>&1 | grep -q "pub(mod)，只对所在目录的模块可见") || ok=0
(cd tests/vis && python3 ../../kite.py check bad2.kite >/dev/null 2>&1) && ok=0
[ $ok = 1 ] && echo "PASS pub(mod) 可见性" || { echo "FAIL pub(mod) 可见性"; fail=1; }
for f in tests/bad*.kite; do
  python3 kite.py check "$f" >/dev/null 2>&1 && { echo "FAIL $f (应报错)"; fail=1; } || echo "PASS $f (正确拒绝)"
done
python3 tests/test_pmap.py || fail=1
# 原生 HAMT：人为制造哈希冲突（全冲突 / 3 位哈希），结果必须与正常哈希一致
if python3 kite.py build examples/maps.kite -o /tmp/kite_maps --emit-c /tmp/kite_maps.c >/dev/null 2>&1; then
  CCMD=$(python3 -c "import native; print(' '.join(native.find_cc()))" 2>/dev/null)
  want=$(/tmp/kite_maps | md5sum); ok=1
  for M in 0 7; do
    $CCMD -O2 -w -DKITE_HMASK=$M /tmp/kite_maps.c -o /tmp/kite_maps_$M -lm -lpthread && [ "$(/tmp/kite_maps_$M | md5sum)" = "$want" ] || ok=0
  done
  [ $ok = 1 ] && echo "PASS 原生 HAMT 哈希冲突路径" || { echo "FAIL 原生 HAMT 哈希冲突路径"; fail=1; }
fi
# KIR：kite kir 能列出不装箱的函数；开启与关闭 KIR（KITE_NO_KIR=1）的原生结果一致；KIR 函数里的溢出报对行号
out=$(python3 kite.py kir examples/kir.kite)
echo "$out" | grep -q "^fn gcd(" && echo "$out" | grep -q "^fn collatz(" && echo "$out" | grep -q "^fn main(" \
  && python3 kite.py kir examples/select.kite | grep -q "^fn main(" && ! python3 kite.py kir examples/closures.kite | grep -q "走字节码路径" \
  && echo "PASS kite kir 输出中间表示" || { echo "FAIL kite kir"; fail=1; }
if python3 kite.py build examples/kir.kite -o /tmp/kite_kir1 >/dev/null 2>&1 && KITE_NO_KIR=1 python3 kite.py build examples/kir.kite -o /tmp/kite_kir0 >/dev/null 2>&1; then
  [ "$(/tmp/kite_kir1)" = "$(/tmp/kite_kir0)" ] && [ "$(/tmp/kite_kir1)" = "$(python3 kite.py examples/kir.kite)" ] \
    && echo "PASS KIR 原生输出与字节码路径一致" || { echo "FAIL KIR 原生输出"; fail=1; }
  python3 kite.py build tests/kir_overflow.kite -o /tmp/kite_kirof >/dev/null 2>&1
  /tmp/kite_kirof > /tmp/kite_kirof.out 2>/tmp/kite_kirof.err
  grep -q "kir_overflow.kite:2: 整数溢出" /tmp/kite_kirof.err && [ "$(cat /tmp/kite_kirof.out)" = "4611686018427387904" ] \
    && echo "PASS KIR 整数溢出报错行号" || { echo "FAIL KIR 整数溢出"; cat /tmp/kite_kirof.err; fail=1; }
fi
# KIR 第二阶段：装箱值（字符串、列表、结构体、枚举、模式、for、?、lambda 捕获）全部走 KIR，三种结果一致
kb=$(python3 kite.py kir tests/kir_boxed.kite)
if echo "$kb" | grep -q "走字节码"; then echo "FAIL KIR 装箱值覆盖"; echo "$kb" | grep -A9 "走字节码"; fail=1
elif python3 kite.py build tests/kir_boxed.kite -o /tmp/kite_kb1 >/dev/null 2>&1 && KITE_NO_KIR=1 python3 kite.py build tests/kir_boxed.kite -o /tmp/kite_kb0 >/dev/null 2>&1 \
  && [ "$(/tmp/kite_kb1)" = "$(/tmp/kite_kb0)" ] && [ "$(/tmp/kite_kb1)" = "$(python3 kite.py tests/kir_boxed.kite)" ]; then
  echo "PASS KIR 装箱值：原生输出与字节码路径一致"
else echo "FAIL KIR 装箱值输出"; fail=1; fi
# 标准库模块（std/*.kite）：输出固定；命令行参数在虚拟机、解释器、原生程序里一致；std 模块走 KIR
# tree / VM 的原地追加优化：结果不能被别名观察到，20 万次 push 必须在 20 秒内完成（O(n) 时要几分钟）
if timeout 20 python3 kite.py tests/push_alias.kite | cmp -s - tests/push_alias.expected \
  && timeout 20 python3 kite.py --tree tests/push_alias.kite | cmp -s - tests/push_alias.expected; then
  echo "PASS tests/push_alias.kite (tree/VM 原地追加)"
else
  echo "FAIL tests/push_alias.kite (tree/VM 原地追加)"; fail=1
fi
# 自举第 1 步：kitec（Kite 写的词法 + 语法分析器）对全部源文件输出的 AST 与 Python 版逐字节相同；随机变异覆盖报错路径；
# VM / tree 跑 kitec 解析 lexer.kite 的结果也一致
if python3 kite.py build kitec/src/main.kite -o /tmp/kite_kitec >/dev/null 2>&1 \
  && sh tools/kitec_check.sh /tmp/kite_kitec > /tmp/kite_kitec.out 2>&1 \
  && python3 tools/kitec_fuzz.py /tmp/kite_kitec 150 1 > /tmp/kite_kitec_fz.out 2>&1 \
  && python3 tools/ast_dump.py kitec/src/lexer.kite > /tmp/kite_kitec_lx.txt \
  && python3 kite.py kitec/src/main.kite parse kitec/src/lexer.kite | cmp -s - /tmp/kite_kitec_lx.txt \
  && python3 kite.py --tree kitec/src/main.kite parse kitec/src/lexer.kite | cmp -s - /tmp/kite_kitec_lx.txt; then
  echo "PASS kitec 语法分析对拍（$(tail -1 /tmp/kite_kitec.out)；$(tail -1 /tmp/kite_kitec_fz.out)）"
else
  echo "FAIL kitec 语法分析对拍"; tail -3 /tmp/kite_kitec.out /tmp/kite_kitec_fz.out 2>/dev/null; fail=1
fi
# 自举第 2 步：kitec check（Kite 写的类型检查器）的报错与函数签名和 Python 版逐字节相同；随机变异覆盖报错路径；VM / tree 一致
if [ -x /tmp/kite_kitec ] \
  && python3 tools/kitec_tc_check.py /tmp/kite_kitec > /tmp/kite_kitec_tc.out 2>&1 \
  && python3 tools/kitec_tc_fuzz.py /tmp/kite_kitec 100 1 > /tmp/kite_kitec_tcfz.out 2>&1 \
  && python3 tools/tc_dump.py tests/bad.kite > /tmp/kite_kitec_tcb.txt \
  && python3 kite.py kitec/src/main.kite check tests/bad.kite prelude.kite | cmp -s - /tmp/kite_kitec_tcb.txt \
  && python3 kite.py --tree kitec/src/main.kite check tests/bad.kite prelude.kite | cmp -s - /tmp/kite_kitec_tcb.txt; then
  echo "PASS kitec 类型检查对拍（$(tail -1 /tmp/kite_kitec_tc.out)；$(tail -1 /tmp/kite_kitec_tcfz.out)）"
else
  echo "FAIL kitec 类型检查对拍"; tail -3 /tmp/kite_kitec_tc.out /tmp/kite_kitec_tcfz.out 2>/dev/null; fail=1
fi
# 自举第 3 步：kitec 的检查后语法树（3a）、字节码（3b）、不走 KIR 的 C（3c）、KIR（3d）和走 KIR 的 C 都与 Python 版逐字节相同
if [ -x /tmp/kite_kitec ] \
  && python3 tools/kitec_lower_check.py /tmp/kite_kitec > /tmp/kite_kitec_lw.out 2>&1 \
  && python3 tools/kitec_bc_check.py /tmp/kite_kitec > /tmp/kite_kitec_bc.out 2>&1 \
  && python3 tools/kitec_c_check.py /tmp/kite_kitec > /tmp/kite_kitec_c.out 2>&1 \
  && python3 tools/kitec_kir_check.py /tmp/kite_kitec > /tmp/kite_kitec_kir.out 2>&1 \
  && python3 tools/kitec_ck_check.py /tmp/kite_kitec > /tmp/kite_kitec_ck.out 2>&1 \
  && python3 tools/kitec_kir_fuzz.py /tmp/kite_kitec 60 1 > /tmp/kite_kitec_kirfz.out 2>&1; then
  echo "PASS kitec 代码生成对拍（$(tail -1 /tmp/kite_kitec_ck.out)；$(tail -1 /tmp/kite_kitec_kirfz.out)）"
else
  echo "FAIL kitec 代码生成对拍"; tail -2 /tmp/kite_kitec_lw.out /tmp/kite_kitec_bc.out /tmp/kite_kitec_c.out /tmp/kite_kitec_kir.out /tmp/kite_kitec_ck.out /tmp/kite_kitec_kirfz.out 2>/dev/null; fail=1
fi
# 自举第 4 步：三阶段自举（stage0 = Python 版编出的 kitec，stage1 / stage2 = kitec build 自己），三份 C 逐字节相同
if sh tools/kitec_bootstrap.sh /tmp/kite_kitec_boot > /tmp/kite_kitec_boot.out 2>&1; then
  echo "PASS kitec 三阶段自举（$(tail -1 /tmp/kite_kitec_boot.out)）"
else
  echo "FAIL kitec 三阶段自举"; tail -3 /tmp/kite_kitec_boot.out; fail=1
fi
cat > /tmp/kite_args.kite <<'EOF2'
use std.env
fn main() { print(env.args(), env.get_or("KITE_T_VAR", "-")) }
EOF2
if python3 kite.py examples/stdmods.kite | cmp -s - tests/stdmods.expected \
  && python3 kite.py examples/http.kite | cmp -s - tests/http.expected \
  && python3 kite.py examples/https.kite | cmp -s - tests/https.expected \
  && ! python3 kite.py kir examples/https.kite | grep -q "走字节码" \
  && python3 kite.py examples/sets.kite | cmp -s - tests/sets.expected \
  && ! python3 kite.py kir examples/http.kite | grep -q "走字节码" \
  && [ "$(KITE_T_VAR=v python3 kite.py /tmp/kite_args.kite a "b c")" = '["a", "b c"] v' ] \
  && [ "$(KITE_T_VAR=v python3 kite.py --tree /tmp/kite_args.kite a "b c")" = '["a", "b c"] v' ] \
  && python3 kite.py build /tmp/kite_args.kite -o /tmp/kite_args >/dev/null 2>&1 \
  && [ "$(KITE_T_VAR=v /tmp/kite_args a "b c")" = '["a", "b c"] v' ] \
  && ! python3 kite.py kir examples/stdmods.kite | grep -q "走字节码"; then
  echo "PASS 标准库模块 json / strings / time / env / process / net / http / https"
else echo "FAIL 标准库模块"; fail=1; fi
if [ "$(python3 kite.py tests/local_kstd.kite)" = "abab!" ] && [ "$(python3 kite.py --tree tests/local_kstd.kite)" = "abab!" ] \
  && python3 kite.py build tests/local_kstd.kite -o /tmp/kite_lk >/dev/null 2>&1 && [ "$(/tmp/kite_lk)" = "abab!" ] \
  && ! python3 kite.py kir tests/local_kstd.kite | grep -q "走字节码"; then
  echo "PASS 函数体内 use Kite 标准库模块"
else echo "FAIL 函数体内 use Kite 标准库模块"; fail=1; fi
if python3 kite.py fmt --migrate tests/migrate/old.kite 2>/dev/null | diff -q - tests/migrate/want.kite >/dev/null \
  && python3 kite.py fmt --migrate tests/migrate/old_prec.kite 2>/dev/null | diff -q - tests/migrate/want_prec.kite >/dev/null \
  && [ "$(python3 kite.py tests/migrate/want.kite)" = "3 false
4" ]; then
  echo "PASS kite fmt --migrate（! → ~，按旧优先级加括号）"
else echo "FAIL kite fmt --migrate"; fail=1; fi
# 对齐 Go 标准库：examples/go_*.kite 的输出必须和真正的 Go 跑 tests/go/*.go 的结果（tests/go/*.expected）逐字节相同
if sh tools/go_check.sh > /tmp/kite_go_check.out 2>&1; then
  echo "PASS Go 标准库对拍（$(tail -1 /tmp/kite_go_check.out)）"
else
  echo "FAIL Go 标准库对拍"; cat /tmp/kite_go_check.out; fail=1
fi
python3 tests/test_tooling.py || fail=1
python3 tests/test_packages.py || fail=1
exit $fail
