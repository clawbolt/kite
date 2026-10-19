# Go 1.27 标准库对齐进度

`go list std`（去掉 internal / vendor）共 179 个包。下面是 2026-10-18 的分类，2026-10-19 加了分层原则。

## 分层原则（2026-10-19）
Go 标准库里有一部分是给 Go 语言自己用的（解析、类型检查、文档、调试信息）。这类包在 Kite 里的对应物是“处理 Kite 代码的库”，不是照搬 Go 的 API。每个包归入四类之一：

1. **通用语义，直接对标 Go**：算法、编解码、文件格式、协议、密码学（debug/elf、crypto/x509、hpke、encoding/*、compress/*、text/template/parse …）。行为与 Go 逐字节对拍，API 可按 Kite 的不可变值模型改造。
2. **Go 专属，保留兼容层**：std.go.*（ast、scanner、token、parser、printer、format、constant、version、build/constraint、doc/comment）。已和 Go 对拍，留给处理 Go 源码的工具用（例如 Go→Kite 迁移）。修 bug 可以，不再往里加 Go 专属的新包。
3. **Kite 原生 API**：std.kite.*，对标 Go 的 go/* 但描述的是 Kite 自己。源码就是 kitec 的前端（kitec 反过来 use 它们），只有一份实现；验收靠 kitec 自己的 gold / 自举 / lsp 回放，不跟 Go 对拍。
4. **不移植**：见文末。

| Go | Kite 原生 | 状态 |
|---|---|---|
| go/token | std.kite.token | 完成（2026-10-19） |
| go/scanner | std.kite.scanner | 完成（kitec 的词法分析） |
| go/ast | std.kite.ast | 完成（kitec 的强类型语法树，类型检查后带 Node.t） |
| go/parser | std.kite.parser | 完成（kitec 的语法分析） |
| go/format、go/printer | std.kite.format | 完成（kitec fmt 的格式化器，规则见 docs/fmt.md） |
| go/types | std.kite.types | 完成（2026-10-19；检查器 std.kite.check、模块加载 std.kite.modules 也移入标准库） |
| go/doc | std.kite.doc | 完成（2026-10-19；text / markdown 输出） |
| go/version | std.kite.version | 完成（2026-10-19；Kite 包的语义化版本与约束，kitec 包管理用的同一份） |
| go/constant | — | 不需要：Kite 常量就是普通值 |
| debug/gosym、debug/buildinfo | — | 不移植：描述的是 Go 二进制 |

## 已完成（std/ 下有对应模块，大多带 tests/go 对拍）
archive/*、bufio、bytes、cmp、compress/*、container/*、crypto 下的 aes cipher des dsa ecdh ecdsa ed25519 elliptic hkdf hmac md5 mlkem（含 mlkemtest 的功能） pbkdf2 rand rc4 rsa sha1 sha256 sha3 sha512 subtle、
encoding 下的 ascii85 asn1 base32 base64 binary csv hex json json/jsontext json/v2 pem xml、errors、flag、fmt、
go 下的 ast build/constraint doc/comment format parser printer scanner token version、hash 下的 adler32 crc32 crc64 fnv、html、
image 下的 color draw gif jpeg png、index/suffixarray、log、log/slog、maps、math（内建 + 扩充）、math/big、math/bits、math/cmplx、
math/rand（实现的是 math/rand/v2 的 PCG / ChaCha8）、mime/*、net（部分）、net/http（部分）、net/mail、net/netip、net/textproto、net/url、
os（部分）、path、regexp、regexp/syntax、slices、sort、strconv、strings、text/scanner、text/tabwriter、text/template、time、unicode/*

## 第一批（已完成 2026-10-18）
image/color/palette、path/filepath、go/constant、net/http/cookiejar、crypto/mldsa

## 第二批（进行中）
- debug/elf（2026-10-19 完成；tests/go/testdata/elf 里 27 个 Go 测试 ELF；不含 DWARF() / 重定位 / zstd）
- text/template/parse（2026-10-19 完成；std.text.template.parse，1540 行对拍输出与 Go 逐字节一致；出错时不返回部分树集）

## 可以做（纯数据 / 算法，计划后续批次）
- crypto/x509、crypto/x509/pkix、crypto/hpke
- encoding/gob（值用类似 fmt.Arg 的枚举表示）
- debug/dwarf、debug/macho、debug/pe、debug/plan9obj
- html/template（上下文转义）
- net/http/httptest（ResponseRecorder）、net/http/httputil（Dump*、反向代理的纯逻辑部分）、net/smtp（协议层）、net/rpc/jsonrpc（编解码）
- io/fs（ValidPath / Glob / WalkDir 的纯逻辑）、testing/fstest（MapFS）、testing/iotest
- hash/maphash（种子随机，只能对拍确定性的部分）、expvar、time/tzdata（时区数据）
- os/user（读 /etc/passwd、/etc/group）、os/exec（借 system）

## 不移植（语言或运行时层面的，Kite 用自己的机制）
- 运行时：runtime 及其子包、reflect、unsafe、plugin、syscall、unique、weak、structs、embed
- 并发：sync、sync/atomic、context（Kite 有自己的 actor / chan）
- 只有接口定义的包：crypto、crypto/fips140、encoding、hash、io、io/ioutil、iter
- 需要真实系统设施：os/signal、log/syslog、crypto/tls、net/http/cgi、net/http/fcgi、net/http/pprof、net/http/httptrace、runtime/pprof
- 依赖反射的测试工具：testing、testing/quick、testing/slogtest、testing/synctest、testing/cryptotest
- database/sql、database/sql/driver（只有驱动接口，没有内建驱动）
- go/importer、go/build（需要真实 Go 工具链和文件系统布局）
- go/types、go/doc、debug/gosym、debug/buildinfo（Go 自描述；Kite 对应物走 std.kite.*，见分层原则）
