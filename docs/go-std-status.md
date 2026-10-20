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
- 第三步（报文写出 + 路由）：`request_write(OutRequest)` / `response_write(OutResponse)` 逐字节同 Go 的 Request.Write / Response.Write
  （User-Agent 默认值、Host 的 punycode 和 zone 处理、Content-Length / chunked / Trailer 选择、GET 类方法空正文探测、
  ContentLength 与正文长度不符的错误和已写出的部分）；`new_serve_mux` / `handle` / `find` = Go 1.22 ServeMux
  （模式解析与错误、冲突检测和冲突说明、方法/主机/通配符/`{$}`/`{x...}` 匹配、HEAD 落到 GET、尾斜杠和 cleanPath 的 307 重定向、405 的 Allow）；
  `redirect_location` = http.Redirect 写的 Location。mux 是不可变值，处理器用登记编号表示；冲突错误没有 "(registered at …)"。
  对拍 tests/go/http_write（163 行）、tests/go/http_mux（620 行，用例取自 Go 的 pattern_test / routing_tree_test / serve_test）。
- std.net：`split_host_port` / `join_host_port`（错误文字同 Go）。
- std.net（2026-10-20）：IP / IPMask / IPNet / HardwareAddr 的纯函数（parse_ip、parse_cidr、cidr_mask、ip_mask、ip_to4/to16、ip_is_*、ipnet_contains/string、mask_size、parse_mac 等，Go 对拍 tests/go/net_ip 2107 行，输入是 Go 测试里的全部字符串字面量）；
  UDP（listen_udp / dial_udp → PacketConn，read_from / write_to）、Unix 流套接字（listen_unix / dial_unix / close_unix）、unixgram、TCP 支持 IPv6、
  DNS（lookup_host / lookup_ip / lookup_addr / lookup_cname / lookup_port，走系统 getaddrinfo；MX/TXT/SRV/NS 见下面 dnsmessage 一条）。行为测试 examples/net_sockets。
- std.net.http.httptest（2026-10-20）：new_request（NewRequest，非法参数返回 Err，文字同 Go 的 panic）、ResponseRecorder（不可变值：write / write_string / write_header / flush / result，含 Content-Type 嗅探、头快照、Trailer 和 "Trailer:" 前缀）、valid_trailer_header；
  Go 对拍 tests/go/httptest 619 行。NewServer/Close 改成结构化的 with_server(handler, body)（行为测试 examples/httptest_server）；NewTLSServer、NewUnstartedServer 未做。
- std.net.http.httputil（2026-10-20）：dump_request / dump_response / dump_request_out（模拟 Transport：校验头和方法、Accept-Encoding: gzip）、
  反向代理改写（single_joining_slash、join_url_path、rewrite_request_url、remove_hop_by_hop_headers、clean_query_params、upgrade_type、
  proxy_request、proxy_response），可运行的 reverse_proxy(target)。Go 对拍 tests/go/httputil 827 行（复用 http/wire.txt、write.txt，加 httputil/proxy.txt）；
  行为测试 examples/httputil_proxy。http.WireResponse 加 trailer_declared，http.request_write_extra。未做：协议升级（101）转发、FlushInterval、ClientConn/ServerConn（已废弃）。
- std.net.smtp（2026-10-20）：Client（不可变值，Wire = 真实 TCP 或脚本应答）、new_client/dial/new_script_client、hello_as/verify/auth/mail/rcpt/data/
  extension/reset/noop/quit/start_tls（真实连接，用新增的 net.start_tls）、send_mail/send_mail_via、plain_auth/cram_md5_auth（Auth = start/next 闭包）。
  Go 对拍 tests/go/smtp 122 行（26 组脚本会话 + 8 次真实 SendMail）；行为测试 examples/smtp_server。顺带修了 textproto.read_response：多行应答首行码不符时错误被后续行覆盖。
  限制：真实连接写出的字节须是合法 UTF-8（Kite Str）。
- std.net.rpc.jsonrpc（2026-10-20）：Server（handle / handle_json 登记 "服务.方法" 闭包，Handler 拿 params[0] 原始 JSON、返回回复原始 JSON）、
  serve_conn / serve_bytes / serve_request；Client（不可变值，dial / new_client / new_script_client，go_call + wait、call_raw、call、close）、
  encode_request / decode_request / decode_response。行为同 Go 的 net/rpc 服务端 / 客户端循环加 jsonrpc 编解码：方法名错误、找不到服务 / 方法、
  params 缺失或不是数组的错误文字，请求解码失败就停，id 原样回送；客户端 "invalid error %v"、"unspecified error"、未完成调用在头解码失败 / EOF 时
  统一报错。Go 对拍 tests/go/jsonrpc 296 行（52 段服务端输入 + 51 段客户端脚本，含大小写不敏感的键、重复键、非法 id、语法错误、截断）；
  行为测试 examples/jsonrpc_tcp。注意 Go 1.27 默认 GOEXPERIMENT=jsonv2，RawMessage 是 jsontext.Value。
  与 Go 的差异：服务端按顺序处理（Go 并发，应答顺序不定）；参数 [] 表示 nil；net/rpc 的 gob 编码、HTTP 隧道、debug 页面不移植（net/rpc 等 encoding/gob）。
- std.net.dnsmessage（2026-10-20）：x/net/dns/dnsmessage 全部（Name、Header、Question、ResourceHeader、13 种记录体含 SVCB/HTTPS/OPT、
  Message pack / append_pack / unpack、Parser、Builder、EDNS(0)、SVCB 参数、GoString），Parser / Builder 是不可变值，出错时的状态变化同 Go
  （包括 Builder 失败后压缩表保留已登记的后缀、SVCB 键序检查里 previousKey 从不更新的怪癖）。对拍 tests/go/dnsmessage 2063 行
  （17 组报文 × Pack/AppendPack/两种 Builder/Parser 混合走法/每个截断长度/40 次改字节，加 22 段手写的指针与记录体边界）。
  Go 外的程序不能 import vendor 包，重新生成用 tests/go/dnsmessage_regen.sh。差异：Message.Pack 不回写 Type/Length；没有零值 Builder。
- std.net 解析器（同日）：dns_parse_config / dns_read_config（resolv.conf）、dns_name_list（搜索域、ndots、.onion）、is_domain_name、
  Resolver（default_resolver / resolver_with_servers）的 lookup_mx / lookup_txt / lookup_srv / lookup_ns 和同名顶层函数：UDP + EDNS(0)、
  checkHeader（NXDOMAIN、扩展 RCODE、lame referral、server misbehaving）、skipToAnswer、MX 按优先级（同级随机）、SRV 按权重洗牌、
  非法名字过滤、错误文字 "lookup 名字 on 服务器: …"。对拍 tests/go/dnsclient 188 行：Go 的 net 内部测试（tests/go/dnsclient_regen.sh）
  对本机假 DNS 服务查询，Kite 起同样的假服务（testdata/dnsclient/responses.txt）；另含 Go 自带的 14 个 resolv.conf 样例。
  差异：截断的 UDP 应答直接用、不改走 TCP；rotate 不轮换；有非法名字时只返回错误（Go 还返回剩下的记录）。
- std.crypto.hpke（2026-10-21）：RFC 9180 base 模式 + hpke-pq：DHKEM(P-256/P-384/P-521/X25519)、ML-KEM-768/1024、MLKEM768-X25519/P256、
  MLKEM1024-P384；HKDF-SHA256/384/512、SHAKE128/256（一段式）；AES-128/256-GCM、ChaCha20-Poly1305、Export-only；new_kem / new_kdf / new_aead、
  generate_key / new_public_key / new_private_key / derive_key_pair、new_sender / new_recipient / seal / open / export，错误文字同 Go。
  Sender/Recipient 是不可变 Context（seal / open 返回新 Context）；另有 new_sender_derand（同 Go 测试的 setupDerandomizedEncap）。
  对拍 tests/go/hpke 485 行（Go 的 crypto/hpke 内部测试，tests/go/hpke_regen.sh）：两份测试向量（rfc9180 24 组、hpke-pq 13 组，跳过 X448、
  ML-KEM-512、TurboSHAKE 同 Go）、9 种 KEM 的错误路径、ChaCha20/XChaCha20。累积次数默认 20；HPKE_N=1000 时 210 项与向量里的摘要全部一致。
- std.crypto.chacha20poly1305（同日，x/crypto）：new / new_x、seal / open，及 chacha20_block、hchacha20、poly1305_sum。
- std.encoding.gob（2026-10-21）：Encoder / Decoder 线上格式逐字节同 Go：类型描述（wireType）与编号分配顺序、字段增量、零值省略、
  接口值（Register / RegisterName、类型描述在消息中途冲刷）、递归类型、GobEncoder / BinaryMarshaler、Decode(nil)；
  解码按名字匹配字段、compatibleType、整数 / float32 范围检查、dec_helpers 的错误文字、dec.err 的“粘滞”语义。
  没有反射：调用方给出 Type（TInt(位数)…TNamed / TRef / TExt）和 Value；类型编号表是显式传递的 Registry。另有 decode() 按线上类型解出 (Type, Value)。
  对拍 tests/go/gob 1528 行：各种基本 / 复合 / 接口值的编码字节和回读、跨类型解码的错误、截断与逐字节翻转的坏数据（有目标类型和 Decode(nil) 两种）。
  差异：map 按给出顺序编码；Debug 转储没移植；非法 UTF-8 字符串替换成 U+FFFD。

## 可以做（纯数据 / 算法，计划后续批次）
- debug/dwarf、debug/macho、debug/pe、debug/plan9obj
- html/template（上下文转义）
- net/rpc（gob 编码部分，encoding/gob 已有）
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
