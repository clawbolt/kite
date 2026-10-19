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
- crypto/x509（2026-10-19 完成）：第一步——std.crypto.cryptobyte（x/crypto/cryptobyte 读取部分）、std.crypto.x509.pkix、
  std.crypto.x509 的证书 / CRL / PKIX 公钥解析和 OID；tests/go/x509 对 100 张证书（生成器造的 + Go 测试源码里的）、3 个 CRL、
  8 个公钥、约 500 个变异 / 截断输入，2199 行与 Go 逐字节一致。
  第二步完成（2026-10-19）：parse_certificate_request（CSR，含属性、扩展请求、SAN）；PKCS#1 / SEC1（含 P-224，std.crypto.ecdsa 新增 P224 = 0）/ PKCS#8
  私钥（RSA / ECDSA / Ed25519 / X25519 / ML-DSA 种子）的解析和编码、PKCS#1 公钥、marshal_pkix_public_key；签名校验 check_signature /
  check_signature_from / CRL、CSR 校验；rsa.new_private_key_with_precomputation（按 Go fips 的检查顺序）。tests/go/x509_keys 569 行一致。
  与 Go 的差异：多素数 RSA 私钥报错（Kite rsa 只有双素数）。
  第三步完成（2026-10-19）：verify（链构建、路径长度、有效期、EKU、RFC 5280 证书策略图）、名称约束（DNS / IP / 邮箱 / URI，含排除通配父域）、
  verify_hostname（含 HostnameError 文字）、CertPool（不可变，add_cert / add_cert_with_constraint / append_certs_from_pem / equal、
  system_cert_pool 读 SSL_CERT_FILE、SSL_CERT_DIR 和 Linux 常见路径）。tests/go/x509_verify 37 张证书 × 14 组选项 + 主机名表，584 行一致。
  与 Go 的差异：错误是字符串（无 CertificateInvalidError 等类型）；Windows / macOS 平台验证器不移植。
  第四步完成（2026-10-19）：create_certificate、create_certificate_request、create_revocation_list、certificate_create_crl（扩展编码、
  SKID 用 SHA-256 前 20 字节、默认签名算法、全部错误检查）。tests/go/x509_create 94 行一致：RSA PKCS#1 v1.5 / Ed25519 整份 DER 逐字节相同，
  ECDSA / PSS / ML-DSA 比较 TBS 并验签。与 Go 的差异：模板序列号必填（Go 里 nil 时随机生成）；签名随机数由 std.crypto.rand 提供。

## 第三批：网络（进行中）
- net/http 协议层第一步（2026-10-20 完成）：std.net.http 新增 Header（= textproto.MIMEHeader）、canonical_header_key、header_write(_subset)、
  has_token、valid_header_field_name、status_text（全表）、detect_content_type（sniff.go 全部签名）、parse_http_version、parse_time、
  parse_cookie、read_cookies、cookie_valid（Cookie 类型仍在 std.net.http.cookiejar）。tests/go/http_proto 466 行一致
  （输入 tests/go/testdata/http/cases.txt，生成器 gen.go.txt）。与 Go 的差异：Kite 字符串必须是 UTF-8，非法字节只能出现在 detect_content_type 的字节输入里。
- net/url 重写为逐函数移植（2026-10-20）：之前是简化版（主机名转小写、错误文字是中文、没有 RawPath/RawFragment/ForceQuery/OmitHost）。
  现在 URL 加 raw_path、omit_host、force_query、raw_fragment（url.empty() 给出空值），新增 parse_request_uri、redacted、escaped_path、
  escaped_fragment、request_uri、parse_ref、url_join_path、parse_query_all、resolve_path、userinfo_string；错误文字同 Go。
  tests/go/url 704 行一致（输入取自 url_test.go 的各张表，testdata/url/gen_dump_test.go.txt）。差异：解码出非 UTF-8 字节时替换成 U+FFFD。
- net/http 第二步（2026-10-20）：read_request / read_response（WireRequest / WireResponse；正文一并读完，body_err、rest 给出正文错误和剩余字节）、
  chunked_decode / chunked_encode（= httputil.NewChunkedReader / NewChunkedWriter）、header_values_contains_token；Transfer-Encoding、
  Content-Length、Trailer、Connection、Pragma、HEAD / 1xx / 204 / 304 规则同 Go。tests/go/http_wire 369 行一致（输入含 net/http 测试
  reqTests、badRequestTests、respTests 的原始报文，testdata/http/gen_wire_test.go.txt）。
- 计划：Request.Write / Response.Write、ServeMux 模式匹配、net 的 SplitHostPort / ParseCIDR / UDP / Unix / DNS，然后 httptest、httputil、smtp、jsonrpc。

## 可以做（纯数据 / 算法，计划后续批次）
- crypto/hpke
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
