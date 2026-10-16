# kitec：用 Kite 写的 Kite 编译器（自举进行中）

自举分四步，每一步都和 Python 版逐字节对拍：

1. **词法 + 语法分析**（已完成，0.83）：`src/lexer.kite`、`src/parser.kite` 逐条移植 kite.py 的 `lex` 和 `Parser`，
   包括所有语法糖（`if let`、`let … else`、`x is P && …`、`async fn`、切片、`[x; n]`、`Set{…}` 等）和全部报错文字。
2. **类型检查器**（第一批已完成，0.84）：`src/tys.kite`、`src/check.kite` 移植 typecheck.py 的核心；`kitec check FILE prelude.kite`
   输出错误和函数签名，与 `tools/tc_dump.py` 逐字节比较（`tools/kitec_tc_check.py`、`tools/kitec_tc_fuzz.py`）。
   0.85 加上 `src/modules.kite`（模块系统），kitec 能检查自己的源码。
   0.86 加上 unsafe、extern、async fn、dyn。
   0.87 加上关联类型、关联常量、常量泛型和定长数组。
   0.88 支持有依赖的包（读 kite.lock；没有锁文件时输出 UNSUP，见 tools/pkg_fixture.sh）。第 2 步完成。
3. C 代码生成（复用 runtime/kite_rt.c）：
   3a（0.89）`kitec lower FILE prelude.kite` 输出类型检查后改写过的语法树，与 `tools/lower_dump.py` 逐字节比较
   （`tools/kitec_lower_check.py`、`tools/kitec_lower_fuzz.py`）。改写用节点编号 `_id` + `C.rw` 表实现。
   3b（0.90）`kitec bc FILE prelude.kite`（src/bc.kite）输出字节码，与 `tools/bc_dump.py` 逐字节比较
   （`tools/kitec_bc_check.py`、`tools/kitec_bc_fuzz.py`）。
   3c（0.91）`kitec c FILE prelude.kite`（src/cgen.kite）输出 C 源码，与 `tools/c_dump.py`（KITE_NO_KIR=1）逐字节比较
   （`tools/kitec_c_check.py`、`tools/kitec_c_fuzz.py`）。
   3d（0.92）`kitec kir FILE prelude.kite`（src/kir.kite、src/kirdump.kite）输出优化后的 KIR，与 `tools/kir_dump.py` 逐字节比较
   （`tools/kitec_kir_check.py`、`tools/kitec_kir_fuzz.py`）；检查器把表达式类型记在 `C.ts`（按 `_id`）。
   `kitec ck FILE prelude.kite` 输出走 KIR 的 C 源码，与 `tools/c_dump.py --kir` 逐字节比较（`tools/kitec_ck_check.py`、`tools/kitec_ck_fuzz.py`）。
4. **不动点**（已完成，0.93）：`kitec build FILE prelude.kite [-o 输出] [--emit-c C 文件] [--no-kir]` 和 `kite build` 走同样的流程（检查 → 字节码 → KIR → C → C 编译器），不依赖 Python。
   C 编译器按 `$CC`、cc、clang、gcc、`python3 -m ziglang cc` 的顺序查找；extern 声明的库自动加 `-l`。
   `sh tools/kitec_bootstrap.sh` 做三阶段自举：stage0 = `python3 kite.py build kitec/src/main.kite`，stage1 = stage0 编译自己，stage2 = stage1 编译自己；
   要求 stage0.c、stage1.c、stage2.c 逐字节相同（约 19 万行），stage2 编译的示例程序与虚拟机输出一致。`run_tests.sh` 会跑它，也会跑第 3 步的全部对拍。

## 语法树

`src/ast.kite` 里的 `V` 和 Python 版一样是“动态”的：节点是 `VN(种类, 行号, 字段表)`，Python 版里的元组（类型、模式、
match 分支）是 `VT`，记录变体的字段表是 `VR`，限定的变体名是 `VQ`，超出 Int 的整数字面量是 `VBig`。

## 用法

```sh
python3 kite.py build kitec/src/main.kite -o kitec_bin
./kitec_bin parse FILE         # 输出语法树（格式见 tools/ast_dump.py）
./kitec_bin lex FILE           # 输出 token
sh tools/kitec_check.sh ./kitec_bin            # 全部源文件对拍
python3 tools/kitec_fuzz.py ./kitec_bin 300 1  # 随机变异对拍（含报错路径）
```

三个引擎都能跑 kitec（解析自己的 parser.kite：native 约 0.1 秒，VM 约 8 秒，tree 约 18 秒，输出一致）。

## 与 Python 版的差别

- 字符分类：非 ASCII 字符用 std.unicode 的表（Python 的 `isalpha` / `isdigit` / `isalnum` / `isupper` 在少数字符上可能不同，例如 Nl 类字母）。
- 不支持 `kite fmt --migrate` 用的旧优先级（legacy）模式和 REPL 的顶层语句。
