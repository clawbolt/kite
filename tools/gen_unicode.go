// 生成 std/internal/unicode_tables.kite：直接取 Go 标准库 unicode 包的分类、文字和简单大小写映射表（与 Go 逐码点一致）。
// 用法：go run tools/gen_unicode.go > std/internal/unicode_tables.kite
package main

import (
	"fmt"
	"os"
	"sort"
	"strings"
	"unicode"
)

func b36(n int) string { return strings.ToLower(fmt.Sprintf("%s", itoa36(n))) }
func itoa36(n int) string {
	const d = "0123456789abcdefghijklmnopqrstuvwxyz"
	s := ""
	for {
		s = string(d[n%36]) + s
		n /= 36
		if n == 0 {
			return s
		}
	}
}
func sb36(n int) string {
	if n < 0 {
		return "-" + b36(-n)
	}
	return b36(n)
}

func ranges(t *unicode.RangeTable) [][2]int {
	var out [][2]int
	start := -1
	for c := 0; c <= unicode.MaxRune+1; c++ {
		ok := c <= unicode.MaxRune && unicode.Is(t, rune(c))
		if ok && start < 0 {
			start = c
		}
		if !ok && start >= 0 {
			out = append(out, [2]int{start, c - 1})
			start = -1
		}
	}
	return out
}

func enc(rs [][2]int) string {
	var out []string
	prev := -1
	for _, r := range rs {
		out = append(out, b36(r[0]-prev-1), b36(r[1]-r[0]))
		prev = r[1]
	}
	return strings.Join(out, ",")
}

func m(c int) (int, int) {
	return int(unicode.ToUpper(rune(c))) - c, int(unicode.ToLower(rune(c))) - c
}

func main() {
	w := os.Stdout
	var cats []string
	for k := range unicode.Categories {
		cats = append(cats, k)
	}
	sort.Strings(cats)
	var scripts []string
	for k := range unicode.Scripts {
		scripts = append(scripts, k)
	}
	sort.Strings(scripts)
	var props []string
	for k := range unicode.Properties {
		props = append(props, k)
	}
	sort.Strings(props)
	fmt.Fprintf(w, "// 由 tools/gen_unicode.go 用 Go 的 unicode 包生成（Unicode %s），不要手改\n", unicode.Version)
	fmt.Fprintf(w, "// 编码：base36，逗号分隔，交替为“与上一段的间隔、长度 - 1”；大小写表每段 4 项：间隔、长度 - 1、到大写的差、到小写的差\n\n")
	fmt.Fprintf(w, "pub const UNICODE_VERSION: Str = %q\n\n", unicode.Version)
	fmt.Fprintf(w, "pub fn category_data(name: Str) -> Str {\n")
	first := true
	emit := func(name string, t *unicode.RangeTable) {
		kw := "} else if"
		if first {
			kw = "if"
			first = false
		}
		fmt.Fprintf(w, "  %s name == %q {\n    \"%s\"\n", kw, name, enc(ranges(t)))
	}
	for _, c := range cats {
		emit(c, unicode.Categories[c])
	}
	for _, s := range scripts {
		emit(s, unicode.Scripts[s])
	}
	for _, p := range props {
		emit(p, unicode.Properties[p])
	}
	fmt.Fprintf(w, "  } else {\n    \"\"\n  }\n}\n\n")
	q := func(xs []string) string {
		var o []string
		for _, x := range xs {
			o = append(o, fmt.Sprintf("%q", x))
		}
		return strings.Join(o, ", ")
	}
	fmt.Fprintf(w, "pub fn category_names() -> List<Str> { [%s] }\n\n", q(cats))
	fmt.Fprintf(w, "pub fn script_names() -> List<Str> { [%s] }\n\n", q(scripts))
	fmt.Fprintf(w, "pub fn property_names() -> List<Str> { [%s] }\n\n", q(props))
	// 大小写：(lo, hi, 到大写的差, 到小写的差)；999999 表示大写、小写交替
	var out []string
	prev := -1
	add := func(a, b, du, dl int) {
		out = append(out, b36(a-prev-1), b36(b-a), sb36(du), sb36(dl))
		prev = b
	}
	for c := 0; c <= unicode.MaxRune; {
		du, dl := m(c)
		if du == 0 && dl == 0 {
			c++
			continue
		}
		if du == 0 && dl == 1 {
			u2, l2 := m(c + 1)
			if u2 == -1 && l2 == 0 {
				e := c
				for {
					a1, a2 := m(e + 2)
					b1, b2 := m(e + 3)
					if a1 == 0 && a2 == 1 && b1 == -1 && b2 == 0 {
						e += 2
					} else {
						break
					}
				}
				add(c, e+1, 999999, 999999)
				c = e + 2
				continue
			}
		}
		e := c
		for e+1 <= unicode.MaxRune {
			x, y := m(e + 1)
			if x != du || y != dl {
				break
			}
			e++
		}
		add(c, e, du, dl)
		c = e + 1
	}
	fmt.Fprintf(w, "pub fn case_data() -> Str { \"%s\" }\n", strings.Join(out, ","))
	// 标题大写与大写不同的码点：(码点, 标题大写)
	var tt []string
	for c := 0; c <= unicode.MaxRune; c++ {
		if t := int(unicode.ToTitle(rune(c))); t != int(unicode.ToUpper(rune(c))) {
			tt = append(tt, b36(c), b36(t))
		}
	}
	fmt.Fprintf(w, "\npub fn title_data() -> Str { \"%s\" }\n", strings.Join(tt, ","))
}
