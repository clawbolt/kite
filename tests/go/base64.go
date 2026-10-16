package main
import ("encoding/base64"; "fmt"; "strings")
func L(b []byte) string { s := []string{}; for _, x := range b { s = append(s, fmt.Sprint(x)) }; return "[" + strings.Join(s, ", ") + "]" }
func show(b []byte, err error) string { if err != nil { return "err " + err.Error() }; return "ok " + L(b) }
func main() {
  encs := []*base64.Encoding{base64.StdEncoding, base64.URLEncoding, base64.RawStdEncoding, base64.RawURLEncoding, base64.StdEncoding.Strict(), base64.RawStdEncoding.Strict(), base64.StdEncoding.WithPadding('*')}
  ins := []string{"", "f", "fo", "foo", "foob", "fooba", "foobar", "你好世界!?>", "\x00\x01\u00ff~~~"}
  for _, e := range encs {
    for _, s := range ins {
      t := e.EncodeToString([]byte(s))
      fmt.Println(t, e.EncodedLen(len(s)), e.DecodedLen(len(t)), show(e.DecodeString(t)))
    }
    for _, bad := range []string{"Zm9v", "Zg==", "Zg=", "Zg", "Zh==", "Zm9=", "Zm8=", "Zm8", "Z===", "====", "Zg==Zg==", "Zm9vYmFy\nZm9v", "Zm9v\r\n", "Zg=\n=", "Zg=\n", "Zg==\n\nx", "Zm9v!", "Zm9v_-", "Zm9v+/", "Zg**", "Zg*", "Z", "Zm9vY", "Zm9vYm", "Zm9vYmE", "\n\nZm9v\n"} {
      fmt.Println(bad, show(e.DecodeString(bad)))
    }
  }
}
