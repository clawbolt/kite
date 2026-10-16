package main
import ("encoding/base32"; "fmt"; "strings")
func L(b []byte) string { s := []string{}; for _, x := range b { s = append(s, fmt.Sprint(x)) }; return "[" + strings.Join(s, ", ") + "]" }
func show(b []byte, err error) string { if err != nil { return "err " + err.Error() }; return "ok " + L(b) }
func main() {
  encs := []*base32.Encoding{base32.StdEncoding, base32.HexEncoding, base32.StdEncoding.WithPadding(base32.NoPadding), base32.HexEncoding.WithPadding('*')}
  ins := []string{"", "f", "fo", "foo", "foob", "fooba", "foobar", "你好世界!", "\x00\x01\u00ff"}
  for _, e := range encs {
    for _, s := range ins {
      t := e.EncodeToString([]byte(s))
      fmt.Println(t, e.EncodedLen(len(s)), e.DecodedLen(len(t)), show(e.DecodeString(t)))
    }
    for _, bad := range []string{"MZXW6===", "MZXW6YQ=", "MZXW6Y==", "MZ======", "M=======", "MZX=====", "MZXW6YTB\nOI======", "MZXW6YTBOI=====", "MZXW6YTBOI=X====", "MZXW6YTBOI======MZ", "1ZXW6===", "MZXW6", "MZXW6YT", "mzxw", "CPNMU===", "CPNMU", "CPNMUOJ1", "CPNMU*", "M*******", "MZXW6YTB\r\nMZXW6YTB"} {
      fmt.Println(bad, show(e.DecodeString(bad)))
    }
  }
}
