package main
import ("fmt"; "strings")
func sp(c rune) bool { return c == ',' || c == ';' }
func Q(xs []string) string { s := []string{}; for _, x := range xs { s = append(s, "\""+x+"\"") }; return "[" + strings.Join(s, ", ") + "]" }
func B(b bool) string { if b { return "true" }; return "false" }
func T(s string, ok bool) string { return "(\"" + s + "\", " + B(ok) + ")" }
// char index conversion for non-ascii
func ci(s string, bi int) int { if bi < 0 { return -1 }; return len([]rune(s[:bi])) }
func main() {
  ss := []string{"", "a", "abc", "a,b,,c,", ",", "héllo, wörld", "  \t lead and trail \u00a0\n", "aaaa", "Go gopher go", "ǆungla ǉubav", "straße İstanbul", "hello-world_foo bar.baz 3rd"}
  for _, s := range ss {
    fmt.Println(Q(strings.Split(s, ",")), Q(strings.Split(s, "")), Q(strings.SplitN(s, ",", 2)), Q(strings.SplitN(s, "", 3)), Q(strings.SplitAfter(s, ",")), Q(strings.SplitAfterN(s, ",", 2)))
    fmt.Println(Q(strings.Fields(s)), Q(strings.FieldsFunc(s, sp)), strings.Title(s), strings.ToUpper(s), strings.ToLower(s), strings.ToTitle(s))
    fmt.Println(ci(s, strings.Index(s, "o")), ci(s, strings.LastIndex(s, "o")), ci(s, strings.IndexAny(s, "lo")), ci(s, strings.LastIndexAny(s, "lo")), ci(s, strings.IndexRune(s, 0x77)), B(strings.ContainsRune(s, 0x61)), ci(s, strings.IndexFunc(s, sp)), ci(s, strings.LastIndexFunc(s, sp)), B(strings.ContainsFunc(s, sp)))
    fmt.Println("["+strings.Trim(s, " a,")+"]", "["+strings.TrimLeft(s, " a,")+"]", "["+strings.TrimRight(s, " a,")+"]", "["+strings.TrimSpace(s)+"]", "["+strings.TrimFunc(s, sp)+"]", strings.Count(s, "a"), strings.Count(s, ""))
    a, ok1 := strings.CutPrefix(s, "a"); c, ok2 := strings.CutSuffix(s, ",")
    fmt.Println(strings.Replace(s, "a", "XY", 2), strings.Replace(s, "", "-", 3), strings.ReplaceAll(s, "", "|"), strings.ReplaceAll(s, "o", "0"), T(a, ok1), T(c, ok2), B(strings.HasPrefix(s, "a")), B(strings.HasSuffix(s, ",")))
    fmt.Println(strings.Compare(s, "abc"), B(strings.EqualFold(s, strings.ToUpper(s))), strings.Map(func(r rune) rune { if r == 97 { return -1 }; return r + 1 }, s))
  }
  fmt.Println(B(strings.EqualFold("Go", "GO")), B(strings.EqualFold("σ", "Σ")), B(strings.EqualFold("ǅ", "ǆ")), B(strings.EqualFold("a", "b")))
  r := strings.NewReplacer("a", "1", "aa", "2", "b", "3", "", "_")
  r2 := strings.NewReplacer("<", "&lt;", ">", "&gt;", "&", "&amp;")
  r3 := strings.NewReplacer("aaa", "3", "aa", "2", "a", "1")
  r4 := strings.NewReplacer("", "X")
  r5 := strings.NewReplacer("a", "A", "a", "B")
  for _, s := range []string{"", "a", "aab", "aaaa", "<a href=\"x\">&</a>", "xyz", "baaab"} {
    fmt.Println(r.Replace(s), r2.Replace(s), r3.Replace(s), r4.Replace(s), r5.Replace(s))
  }
  var b strings.Builder
  b.WriteString("héllo"); b.WriteRune(0x4e16); b.WriteString("!")
  l := b.Len(); st := b.String(); b.Reset()
  fmt.Println(st, l, b.Len())
}
