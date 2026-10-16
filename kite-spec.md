# Kite 生产级设计规格 v0.2

## 0. 定位
面向后端服务、命令行工具和数据管道的通用编译型语言。目标：Go 的部署简单 + Rust 的类型安全 + 远低于 Rust 的学习成本。
非目标：内核/嵌入式（不做手动内存管理）、浏览器 DOM 脚本。

## 1. 关键技术决策
| 领域 | 决策 | 理由 |
|---|---|---|
| 内存 | 并发分代 GC + 值类型（struct 栈上分配）+ 逃逸分析 | 安全且好学；值类型压低 GC 压力 |
| 共享可变 | 默认不可变；跨任务只能传不可变数据或 `Chan`/`Actor` | 编译期杜绝数据竞争 |
| 类型 | 静态、名义类型 + 局部双向类型推断（非全局 HM） | 报错信息可读，编译快 |
| 泛型 | 单态化 + 大型泛型可选字典传递 | 性能与二进制体积可调 |
| 错误 | `Result<T,E>` + `?`；`panic` 只用于不可恢复的 bug | 错误路径显式 |
| 空值 | 无 null，`Option<T>`，语法糖 `T?` | 消灭空指针 |
| 并发 | 绿色线程（M:N 调度）+ 结构化并发 + 取消传播 | 无 async 染色问题 |
| 后端 | 自研中间表示 KIR → 调试用 Cranelift，发布用 LLVM | 调试快编译，发布高性能 |
| 产物 | 单个静态链接二进制，支持交叉编译 | 部署简单 |
| 互操作 | C ABI FFI（`extern "C"`），`unsafe` 块隔离 | 复用现有生态 |

## 2. 语法核心
- 关键字（保留 30 个以内）：let var fn type enum trait impl match if else for while loop break continue return use mod pub spawn await defer unsafe extern as in is self Self true false
- 语句块用花括号；换行即分号（有明确的续行规则：行尾是运算符或括号未闭合则续行）。
- 文法以 EBNF 定义（kite/grammar.ebnf，v0.6 为 1.0 目标文法），LL(1) 为主，外加文法 C3/C12 列出的有限前瞻点（为工具链和 IDE 增量解析服务）。

## 3. 类型系统细节
- 基础类型：Int(64位) I8..I64 U8..U64 Float(64) F32 Bool Char Str Bytes
- 复合：struct、enum（带载荷）、元组、数组 [T; N]、List<T>、Map<K,V>、Set<T>
- trait：一致性规则（孤儿规则）防止冲突实现；支持默认方法与关联类型
- 模式匹配：穷尽性检查、守卫、嵌套解构
- 可见性：模块内默认私有，`pub` / `pub(pkg)`（本包可见）/ `pub(mod)`（定义文件所在目录的模块可见，lib.kite 为整个包）

## 4. 运行时
- GC：并发标记、分代，目标 P99 停顿 < 1ms（参考 Go/ZGC）
- 调度器：工作窃取，任务栈可增长
- 取消：父作用域结束或超时，子任务收到取消信号
- 内建可观测性：结构化日志、指标、追踪钩子（OpenTelemetry 兼容）

## 5. 标准库（1.0 范围）
io, fs, net(tcp/udp/tls), http(客户端+服务端, HTTP/1.1+2), json, time, encoding, crypto(仅封装成熟实现), sync, test, log, cli, regex, process, env

## 6. 工具链（一个命令 `kite`）
- kite build / run / test / bench / fmt / lint / doc
- kite get：包管理，语义化版本，锁文件 kite.lock，中心仓库 + 签名校验 + 可复现构建
- kite lsp：语言服务器（补全、跳转、重命名、内联类型）
- 调试：生成 DWARF，支持 gdb/lldb
- 报错标准：每条错误有错误码、源码定位、修复建议

## 7. 兼容性与治理
- 版本：语言按"版次"(edition)演进，旧版次代码永远能编译
- 1.0 后不做破坏性变更；特性走 RFC 流程
- 每个版本都要通过：规范一致性测试集、模糊测试、性能回归基准

## 8. 质量门槛（"生产级"定义）
1. 编译器自举（用 Kite 写 Kite 编译器）
2. 核心测试覆盖 > 90%，持续模糊测试
3. 安全响应流程与 CVE 处理
4. 至少 3 个真实生产项目稳定运行 6 个月
5. 支持平台：Linux x86_64/arm64、macOS arm64、Windows x86_64

## 9. 路线图
| 阶段 | 内容 | 约时 |
|---|---|---|
| M0 | 规范草案 + EBNF + 树遍历解释器原型 | 2–3 个月 |
| M1 | 类型检查器 + KIR + Cranelift 后端 + 最小标准库 | 6 个月 |
| M2 | GC、调度器、fmt、LSP、包管理 | 6 个月 |
| M3 | LLVM 发布后端、http/tls、性能优化、自举 | 6–9 个月 |
| 1.0 | 冻结规范、稳定性承诺 | 约 2–3 年，3–6 人核心团队 |

**原型进度（1.00）**：M0 完成；M1 已完成类型检查器（泛型、trait 约束）、KIR 第一到三阶段（标量不装箱；装箱值、模式、for、闭包、方法、defer、spawn、select、局部 fn 与局部类型、trait 默认方法，示例无回退；后端定为 KIR → C）、字节码虚拟机和原生后端、最小标准库（std.encoding.json / strings / time / env / process / net / http（含 keep-alive），用 Kite 编写）；0.60 补齐模式（@ 绑定、var 绑定、开区间与字符区间、常量模式、Self 模式、序列任意位置的 ..）、use … as / 通配导入、超 trait；0.61 关联常量、type / enum 上的约束与 where、pub(pkg) / pub(mod) 可见性检查；0.62 常量泛型（const N: Int）与定长数组类型 [T; N]；0.63 内联模块 mod m { … }、unsafe fn(…) 函数类型；0.64 内联模块里的 super；0.65 泛型参数上的关联常量 T.N；0.66 类型别名上的约束检查；0.67 函数体里的 mod；0.68 extern Ptr 与按值传递的 C 结构体（std.ptr）；0.69 extern C 回调；0.70 extern 窄 C 宽度与可变参数；0.71 TLS（std.net / std.net.http 支持 https）；0.72 LSP 跨文件跳转与重命名；0.73 GC 改进（中等对象尺寸分级、无指针对象与值数组的精确扫描、区块复用）；0.74 均摊 O(1) 的 push 与列表拼接（原生后端共享元素区尾部）；0.75 均摊 O(1) 的字符串拼接 s + t；0.76 LSP 查找所有引用与文档内高亮；0.77 LSP 签名提示；0.78 对齐 Go 标准库的第一批模块（strconv、slices、path、hex、base64、csv、sha256、crc32、fnv、flag、log、heap、utf8，strings 扩充）；0.79 第二批（math 补全并改为 IEEE 语义、os、time 日期与布局、bufio、regexp）；0.80 第三批（unicode、regexp 的 \p 类、url、binary）；0.81 第四批（flate、gzip、zlib、template）；0.82 tree/VM 原地追加（push 均摊 O(1)）、deflate 动态 Huffman；0.83 自举第 1 步：kitec（Kite 写的词法 + 语法分析器，与 Python 版 AST 逐字节一致）；0.84 自举第 2 步第一批：kitec check（Kite 写的类型检查器核心，报错与签名和 Python 版逐字节一致）；0.85 kitec 模块系统（多文件程序、std 源码模块、内联与函数体里的 mod），kitec 能检查自己的源码；0.86 kitec 检查器支持 unsafe、extern（含可变参数、结构体与回调）、async fn、dyn；0.87 加上关联类型、关联常量、常量泛型与定长数组，单文件程序已全部覆盖；0.88 支持有依赖的包（读 kite.lock），类型检查器移植完成；0.89 自举第 3c 步：kitec c 产出与 Python 版（KITE_NO_KIR=1）逐字节相同的 C 源码（native.Gen 移植）；第 3a 步：kitec lower 产出与 Python 版逐字节相同的检查后语法树；0.92 自举第 3d 步：kitec kir / kitec ck 移植 KIR，走 KIR 的 C 源码与 Python 版逐字节相同，kitec 编译自己两轮 C 相同；0.93 自举第 4 步：kitec build 不依赖 Python 直接产出可执行文件，三阶段自举（stage0 → stage1 → stage2）三份 C 逐字节相同，接入 run_tests；0.94 对齐 Go 标准库第五批（bits、rand/v2、md5、sha1、sha512、crc64、adler32、hmac、subtle、base32、ascii85、pem、utf16、html、bytes、sort、cmp、maps、errors、container/list、ring、tabwriter、math/big；strings / base64 / utf8 / crc32 / sha256 补全），用真正的 Go 1.23 逐字节对拍；0.95 第六批（fmt、strconv 浮点与 quote、big.Rat、big.Float、cmplx、mime、quotedprintable、netip、mail、lzw、bzip2、tar、zip、png、aes、cipher、xml）
（字节码 → C → 可执行文件，作为 Cranelift 之前的过渡后端）；M2 已完成 fmt、LSP（含 VS Code 扩展）、
第一阶段 GC（保守式标记-清除，多线程 stop-the-world）、结构化并发（spawn / par_map，原生后端真并行）、通道（Chan，含死锁检测）、select（多路收发、守卫、超时、默认分支）、结构化取消（race、timeout、defer 展开）、Actor（actor/tell/ask，结构化生命周期）、错误恢复与监督（recover、supervise、supervise_if、actor_supervised、结构化错误传播、带类型的 Panic 值、重启时间窗口与指数退避）、
模块系统（pub 可见性、子模块、循环检测）和包管理（kite.toml / kite.lock、语义化版本解析、
目录仓库、sha256 校验、确定性打包）和测试框架（kite test，三种引擎）；取消句柄、工作窃取调度、分代/并发 GC、
远程仓库与包签名未开始。
