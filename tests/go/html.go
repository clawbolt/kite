package main
import ("fmt"; "html")
func main() {
  cases := []string{"", "plain", "a<b>c&d'e\"f", "&amp;&lt;&gt;&quot;&#39;&#34;", "&amp", "&ampx", "&amp;x", "&notit;", "&notin;", "&noti", "&copy2018", "&#65;&#x42;&#X43;&#x;&#;&#", "&#128;&#x9f;&#0;&#xD800;&#1114112;&#99999999999;", "&#12345", "&#x1F600;", "&NotEqualTilde;", "&nvlt;", "&acE;x", "&bogus;", "&&&", "&", "a&", "&;", "&#x41g", "&lt3", "&ltt;", "中&eacute;文&Eacute", "&AMP;&Amp;", "&thetasym;&ThickSpace;", "&#9;&#10;"}
  for _, c := range cases { fmt.Println(html.EscapeString(c), "|", html.UnescapeString(c), "|", html.UnescapeString(html.EscapeString(c)) == c) }
}
