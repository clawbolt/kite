# Go 1.27 标准库对齐进度

`go list std`（去掉 internal / vendor）共 179 个包。下面是 2026-10-18 的分类。

## 已完成（std/ 下有对应模块，大多带 tests/go 对拍）
archive/*、bufio、bytes、cmp、compress/*、container/*、crypto 下的 aes cipher des dsa ecdh ecdsa ed25519 elliptic hkdf hmac md5 mlkem（含 mlkemtest 的功能） pbkdf2 rand rc4 rsa sha1 sha256 sha3 sha512 subtle、
encoding 下的 ascii85 asn1 base32 base64 binary csv hex json json/jsontext json/v2 pem xml、errors、flag、fmt、
go 下的 ast build/constraint doc/comment format parser printer scanner token version、hash 下的 adler32 crc32 crc64 fnv、html、
image 下的 color draw gif jpeg png、index/suffixarray、log、log/slog、maps、math（内建 + 扩充）、math/big、math/bits、math/cmplx、
math/rand（实现的是 math/rand/v2 的 PCG / ChaCha8）、mime/*、net（部分）、net/http（部分）、net/mail、net/netip、net/textproto、net/url、
os（部分）、path、regexp、regexp/syntax、slices、sort、strconv、strings、text/scanner、text/tabwriter、text/template、time、unicode/*

## 第一批（已完成 2026-10-18）
image/color/palette、path/filepath、go/constant、net/http/cookiejar、crypto/mldsa

## 可以做（纯数据 / 算法，计划后续批次）
- crypto/x509、crypto/x509/pkix、crypto/hpke
- encoding/gob（值用类似 fmt.Arg 的枚举表示）
- debug/elf、debug/dwarf、debug/macho、debug/pe、debug/plan9obj、debug/gosym、debug/buildinfo
- text/template/parse（导出语法树）、html/template（上下文转义）
- go/doc、go/types（很大）
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
