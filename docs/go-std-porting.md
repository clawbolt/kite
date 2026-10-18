# 对齐 Go 标准库：移植须知（给做移植的人看）

工作目录 `/workspace/work/kite`。环境变量：`CC=/workspace/work/zigcc`（任意 C 编译器都行）。
Go 1.27.1 工具链在 `/tmp/go/bin/go`（不持久，丢了就按机器架构重新下载 go1.27.1.linux-arm64/amd64.tar.gz；Go 源码在 `/tmp/go/src`，移植时直接照着读）。注意 1.27 的 encoding/json v1 默认由 v2（jsontext）实现，错误信息以 jsontext 为准。

## 每个包要交付的文件（只动这些，别改 README / run_tests.sh / kitec / 其他人的模块）
- `std/NAME.kite`：模块本身，用 Kite 写。开头注释说明对应哪个 Go 包、和 Go 的差别。
- `tests/go/NAME.go`：Go 测试程序（package main），尽量覆盖所有导出函数和边界情况、错误信息。
- `examples/go_NAME.kite`：同样内容的 Kite 程序。
- `tests/go/NAME.expected`：`cd tests/go && /tmp/go/bin/go run NAME.go > NAME.expected`。
- 验收：`bin/kitec run examples/go_NAME.kite` 的输出和 `.expected` 逐字节相同；
  `bin/kitec fmt --check examples/go_NAME.kite` 通过（不通过就 `bin/kitec fmt -w` 覆盖）；std 文件也要过 `bin/kitec check`。
- 示例最好几秒内跑完（数据量别太大）。

## Kite 语言要点和坑
- 值全部不可变。有状态的 Go 对象：方法返回新值，例如 `fn write(self, b: List<Int>) -> Writer`。
- 只有 64 位有符号 Int，`+ - *` 溢出会 panic。需要回绕的无符号 64 位运算用 `std.math.bits`：
  `bits.wadd wsub wmul shr(逻辑右移) ult ule ucmp udiv urem ustr(无符号十进制) mul64 add64 rotate_left32/64 len64 …`。
  `<<` 按 64 位回绕；`>>` 是算术右移；位取反是 `~`；移位位数必须 0..63。32 位运算自己 `& 0xffffffff`。
- 超过 2^63 的十六进制字面量写不出来，写成对应的负数十进制。
- 字节序列用 `List<Int>`；`__bytes(s)` 取 UTF-8 字节，`__from_bytes(bs) -> Option<Str>`。Str 总是合法 UTF-8，
  下标/长度按字符算；`__str_slice(s, a, b)`、`__str_find(s, sub, start)`（-1 表示没有）、`__ord(c)`、`__chr(n)`、`chars(s)`。
- `set(list, i, v)` / `set(map, k, v)` 返回新的（list 的 set 是 O(n) 拷贝，热循环里尽量用 push 构造）；`push(xs, x)`、`xs[a..b]`、`xs[..n]`、`take(xs, n)`、`reverse`、`fold`、`map`、`filter`、`sort`、`keys`、`values`、`contains`、`remove`。
- Map 字面量：`Map([("a", 1)])`，空的 `Map()`；遍历 `for (k, v) in m`。
- 关键字不能当名字：`is mod in as type match loop ...`。模块里定义的同名函数会遮住全局内建（比如在模块里定义了 `split`，模块内部再调 `split` 就是自己的）。方法名 `abs` 会撞内建，换个名字。
- 字符串字面量里 `{` 是插值，要写 `\{`；插值里面不能再写带引号的字符串。没有 `\v` `\f` 转义，用 `\u{b}` `\u{c}`。字符串里放不了单个 0xff 这样的非法字节，要用字节列表。
- Float 打印格式和 Go 不同（Kite 打印 `1.0`，Go 打印 `1`）。测试里浮点要用同一种格式输出：要么 Kite 侧用模块自己的格式化函数（比如 strconv 风格），要么 Go 侧手写成 Kite 的格式。列表打印：Kite 打印 `[1, 2]`、字符串列表 `["a", "b"]`（不转义），Go 侧要手动拼成同样的样子。Option 打印成 `Some(x)` / `None`，Result 打印成 `Ok(x)` / `Err("...")`。
- 除以零（包括 0.0/0.0）会 panic；NaN/Inf 用 `use std.math` 的 `math.nan` `math.inf`；`math.floor` 返回 Int。`float(i)`、`int(f)` 转换。
- 语句位置的 `loop` 没 break 时类型是 Never，可以放函数结尾；`loop` 带值 `break v` 也可以。
- 方法：`impl T { fn name(self, ...) -> R { ... } }`；静态方法不带 self，`T.name()` 调用。泛型 `fn f<T: Ord>(...)`、`impl<T> Box<T> { ... }`。
- 结构体更新 `T { a: 1, ..old }`；枚举 `pub enum E { A(Int), B }`；`match`；`let Some(x) = e else { return ... }`；`?` 用于 Result/Option。
- 已有模块可以 `use std.xxx`：bits、strings、bytes、utf8、unicode、strconv、math、base64（`base64.std_encoding().encode_to_string(bs)`）、hex、crc32、adler32、flate（`flate.compress(bs, level)` / `flate.decompress(bs)`）、zlib、gzip、sha256、sha1、md5、big（`BigInt`）、sort、errors、time、os、fs、path、json 等。看 `std/*.kite` 开头的注释。
- 参考已经做完的例子：`std/encoding/base32.kite` + `examples/go_base32.kite` + `tests/go/base32.go`，`std/text/tabwriter.kite`，`std/math/big.kite`。

## 有疑问时
- 优先保证“同样输入，输出和 Go 逐字节相同”。实在做不到的（依赖 Go 运行时、指针身份、随机种子等），在模块开头注释里写清楚差别。

## 工作节奏（重要）
- 上一轮好几个人时间用完时还没交付任何东西。顺序一定是：先写出能用的核心 API → 写一份规模适中的 Go 测试 → 三个引擎都通过 → 再扩大覆盖面。随时保持“已交付的部分是通过的”状态。
- 时间快不够时，先把测试缩小到能通过的部分，再交付；不要留下编译不过的文件。
- match 分支里不能直接写 `=> return x`，必须写成 `=> { return x }`。
- 树解释器大约每秒 15～30 万次简单循环，测试数据控制在几 KB。
- 已完成可复用：std/math/big/rat.kite 导出 float64_bits / float64_frombits / frexp / ldexp / pow2；std/strconv.kite 新增 format_float_go(f, "e|E|f|g|G|b|x|X", prec, bitsize)、parse_float_go、quote_go 等（正在由另一个人对拍验证）。
