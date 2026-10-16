package main
import ("bytes"; "fmt"; "strings"; "unicode/utf8")
func L[T any](b []T) string { s := []string{}; for _, x := range b { s = append(s, fmt.Sprint(x)) }; return "[" + strings.Join(s, ", ") + "]" }
func LL(bs [][]byte) string { s := []string{}; for _, x := range bs { s = append(s, L(x)) }; return "[" + strings.Join(s, ", ") + "]" }
func B(b bool) string { if b { return "true" }; return "false" }
func sp(r rune) bool { return r == 44 || r == 59 || r == 0xfffd }
func main() {
  ss := [][]byte{[]byte(""), []byte("a"), []byte("a,b,,c,"), []byte("héllo, wörld"), []byte("  \t x y \u00a0\n"), []byte("aaaa"), {0xff, 97, 0xc3, 44, 0xe4, 0xb8, 0x96, 0xed, 0xa0, 0x80, 32, 0xf0, 0x9f, 0x98}, []byte("Go gopher go"), {0xe4, 0xb8}, []byte("straße İx ǆ")}
  for _, s := range ss {
    fmt.Println(L(s), LL(bytes.Split(s, []byte(","))), LL(bytes.Split(s, nil)), LL(bytes.SplitN(s, []byte(","), 2)), LL(bytes.SplitN(s, nil, 3)), LL(bytes.SplitAfter(s, []byte(","))), LL(bytes.SplitAfterN(s, []byte(","), 2)))
    fmt.Println(LL(bytes.Fields(s)), LL(bytes.FieldsFunc(s, sp)), L(bytes.Title(s)), L(bytes.ToUpper(s)), L(bytes.ToLower(s)), L(bytes.ToTitle(s)), L(bytes.Runes(s)))
    fmt.Println(bytes.Index(s, []byte("o")), bytes.LastIndex(s, []byte("o")), bytes.IndexByte(s, 97), bytes.LastIndexByte(s, 97), bytes.IndexAny(s, "lo\ufffd"), bytes.LastIndexAny(s, "lo\ufffd"), bytes.IndexRune(s, 0x77), bytes.IndexRune(s, 0xfffd), bytes.IndexRune(s, 0x4e16), B(bytes.ContainsRune(s, 0x61)), bytes.IndexFunc(s, sp), bytes.LastIndexFunc(s, sp), B(bytes.ContainsAny(s, "")), B(bytes.Contains(s, []byte("a"))))
    fmt.Println(L(bytes.Trim(s, " a,\ufffd")), L(bytes.TrimLeft(s, " a,")), L(bytes.TrimRight(s, " a,\ufffd")), L(bytes.TrimSpace(s)), L(bytes.TrimFunc(s, sp)), bytes.Count(s, []byte("a")), bytes.Count(s, nil), L(bytes.TrimPrefix(s, []byte("a"))), L(bytes.TrimSuffix(s, []byte(","))))
    x1, x2, ok := bytes.Cut(s, []byte(",")); y1, ok2 := bytes.CutPrefix(s, []byte("a")); z1, ok3 := bytes.CutSuffix(s, []byte(","))
    fmt.Println(L(bytes.Replace(s, []byte("a"), []byte("XY"), 2)), L(bytes.Replace(s, nil, []byte("-"), 3)), L(bytes.ReplaceAll(s, nil, []byte("|"))), L(bytes.ReplaceAll(s, []byte("o"), []byte("0"))), "("+L(x1)+", "+L(x2)+", "+B(ok)+")", "("+L(y1)+", "+B(ok2)+")", "("+L(z1)+", "+B(ok3)+")", B(bytes.HasPrefix(s, []byte("a"))), B(bytes.HasSuffix(s, []byte(","))))
    fmt.Println(bytes.Compare(s, []byte("abc")), B(bytes.EqualFold(s, bytes.ToUpper(s))), L(bytes.Map(func(r rune) rune { if r == 97 { return -1 }; return r + 1 }, s)), L(bytes.ToValidUTF8(s, []byte("?"))), B(bytes.Equal(bytes.Repeat(s, 2), append(append([]byte{}, s...), s...))), L(bytes.Join([][]byte{s, s, []byte("z")}, []byte("--"))))
    r1, n1 := utf8.DecodeRune(s); r2, n2 := utf8.DecodeLastRune(s)
    fmt.Printf("(%d, %d) (%d, %d) %s %d %s\n", r1, n1, r2, n2, B(utf8.FullRune(s)), utf8.RuneCount(s), B(utf8.Valid(s)))
  }
  buf := bytes.NewBufferString("line1\nline2\nrest")
  buf.WriteString("!"); buf.WriteByte(65); buf.WriteRune(0x4e16)
  all := buf.String(); n0 := buf.Len()
  c := bytes.NewBuffer(append([]byte{}, buf.Bytes()...))
  l1, e1 := c.ReadString(10); l2, e2 := c.ReadBytes(10); l3, e3 := c.ReadString(10)
  fmt.Println(l1, B(e1 == nil), L(l2), B(e2 == nil), l3, e3, c.Len(), n0, all)
  d := bytes.NewBuffer(append([]byte{}, buf.Bytes()...))
  nx := d.Next(3)
  e := bytes.NewBuffer(append([]byte{}, d.Bytes()...)); bb, _ := e.ReadByte()
  f := bytes.NewBuffer(append([]byte{}, d.Bytes()...)); rr, rs, _ := f.ReadRune()
  g := bytes.NewBuffer(append([]byte{}, d.Bytes()...)); g.Truncate(2)
  fmt.Printf("%s Ok((%d, _)) Ok((%d, %d, _)) %s 0\n", L(nx), bb, rr, rs, g.String())
}
