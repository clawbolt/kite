package main
import ("fmt"; "strings"; "text/tabwriter"; "unicode/utf8")
func run(minw, tabw, pad int, ch byte, flags uint, text string) {
  var sb strings.Builder
  w := tabwriter.NewWriter(&sb, minw, tabw, pad, ch, flags)
  w.Write([]byte(text)); w.Flush()
  fmt.Println("<" + strings.ToValidUTF8(sb.String(), "\ufffd") + ">")
}
func main() {
  _ = utf8.RuneError
  t1 := "a\tb\tc\naaa\tbbbbb\tc\nx\t\ty\n\nlong cell here\tz\n"
  t2 := "名前\t年齢\t都市\n太郎\t25\t東京\nBob\t300\tNYC\n"
  t3 := "a\tb\vc\fd\te\n\tindent\tx\n\t\tdouble\n"
  t4 := "<b>bold</b>\t&amp;\tx\n\xffesc\taped\xff\ty\nplain\tz\n"
  t5 := "1\t2\t\t3\n11\t22\t\t33\n\t\t\t\nend"
  for _, t := range []string{t1, t2, t3, t4, t5} {
    run(0, 8, 1, ' ', 0, t)
    run(5, 0, 2, '.', tabwriter.AlignRight, t)
    run(0, 4, 0, '\t', tabwriter.TabIndent, t)
    run(2, 8, 1, '-', tabwriter.Debug|tabwriter.DiscardEmptyColumns, t)
    run(0, 8, 1, ' ', tabwriter.FilterHTML|tabwriter.StripEscape, t)
    run(0, 8, 1, ' ', tabwriter.FilterHTML|tabwriter.AlignRight|tabwriter.Debug, t)
  }
  var sb strings.Builder
  w := tabwriter.NewWriter(&sb, 0, 8, 1, ' ', 0)
  w.Write([]byte("a\tb\n")); w.Write([]byte("ccc\t")); w.Write([]byte("d\nshort\n"))
  fmt.Println("[" + sb.String() + "]")
  sb.Reset()
  w.Write([]byte("x\ty\n")); w.Flush()
  fmt.Println("[" + sb.String() + "]")
}
