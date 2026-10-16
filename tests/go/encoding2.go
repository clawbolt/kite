package main
import ("encoding/ascii85"; "encoding/pem"; "fmt"; "sort"; "strings"; "unicode/utf16")
func L[T any](b []T) string { s := []string{}; for _, x := range b { s = append(s, fmt.Sprint(x)) }; return "[" + strings.Join(s, ", ") + "]" }
func show(dst []byte, nd, ns int, err error) string { if err != nil { return "err " + err.Error() }; return fmt.Sprintf("ok %s %d", L(dst[:nd]), ns) }
func dec(b []byte, flush bool) string { dst := make([]byte, 4*len(b)+8); nd, ns, err := ascii85.Decode(dst, b, flush); return show(dst, nd, ns, err) }
func showb(b *pem.Block) string {
  if b == nil { return "nil" }
  hs := []string{}; for k, v := range b.Headers { hs = append(hs, fmt.Sprintf("%q", k+"="+v)) }; sort.Strings(hs)
  return fmt.Sprintf("%s [%s] %s", b.Type, strings.Join(hs, ", "), L(b.Bytes))
}
func main() {
  for _, s := range []string{"", "a", "ab", "abc", "abcd", "\x00\x00\x00\x00", "\x00\x00\x00\x00x", "\x00\x00", "Man is distinguished", "你好世界"} {
    dst := make([]byte, ascii85.MaxEncodedLen(len(s))); n := ascii85.Encode(dst, []byte(s)); e := string(dst[:n])
    fmt.Println(e, ascii85.MaxEncodedLen(len(s)), dec([]byte(e), true), dec([]byte(e), false))
  }
  for _, bad := range []string{"9jqo^", "9jqo^B", "9jq o^\nBlb", "zz", "9z", "9jqo~", "uuuuu", "s8W-!", "!", "!!", "ab{", "  "} {
    fmt.Println(bad, dec([]byte(bad), true), dec([]byte(bad), false))
  }
  data := []byte{}
  for i := 0; i < 100; i++ { data = append(data, byte((i*37)%256)) }
  b := &pem.Block{Type: "TEST DATA", Headers: map[string]string{"Proc-Type": "4,ENCRYPTED", "Zeta": "z", "Alpha": "a b"}, Bytes: data}
  enc := string(pem.EncodeToMemory(b)); fmt.Println(enc)
  enc2 := string(pem.EncodeToMemory(&pem.Block{Type: "EMPTY"})); fmt.Println(enc2)
  var sb strings.Builder
  if err := pem.Encode(&sb, &pem.Block{Type: "X", Headers: map[string]string{"a:b": "c"}}); err != nil { fmt.Println("Err(" + fmt.Sprintf("%q", err.Error()) + ")") }
  docs := []string{enc, "junk\n" + enc + "tail", enc2 + enc, "-----BEGIN A-----\nAQID\n-----END A-----", "-----BEGIN A-----\nAQID\n-----END B-----\n", "-----BEGIN A-----\r\nAQ ID\t\r\n-----END A-----  \r\nnext", "-----BEGIN A-----\nAQI*\n-----END A-----\n", "no pem here", "-----BEGIN A----\nAQID\n-----END A-----\n-----BEGIN C-----\nBAU=\n-----END C-----\n", "-----BEGIN A-----\nK: v\n\nAQID\n-----END A-----x\n"}
  for _, d := range docs { blk, rest := pem.Decode([]byte(d)); fmt.Println(showb(blk), "|"+string(rest)+"|") }
  for _, r := range []rune{0, 0x41, 0xd7ff, 0xd800, 0xdfff, 0xe000, 0xffff, 0x10000, 0x1f600, 0x10ffff, 0x110000, -1} {
    r1, r2 := utf16.EncodeRune(r)
    fmt.Printf("%d %v %d (%d, %d) %s\n", r, utf16.IsSurrogate(r), utf16.RuneLen(r), r1, r2, L(utf16.AppendRune(nil, r)))
  }
  fmt.Println(utf16.DecodeRune(0xd83d, 0xde00), utf16.DecodeRune(0xd83d, 0x41))
  u := utf16.Encode([]rune{0x48, 0x1f600, 0xd800, 0x10ffff, 0x110000, 0x4e2d})
  fmt.Println(L(u), L(utf16.Decode(u)), L(utf16.Decode([]uint16{0xd800, 0x41, 0xdc00, 0xd83d, 0xde00, 0xd83d})))
}
