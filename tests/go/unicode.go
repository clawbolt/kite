package main

import (
	"fmt"
	"sort"
	"unicode"
)

func b(x bool) int { if x { return 1 }; return 0 }

func stat(name string, t *unicode.RangeTable) {
	n, cnt, lo, hi := 0, 0, -1, -1
	start := -1
	for c := 0; c <= unicode.MaxRune+1; c++ {
		ok := c <= unicode.MaxRune && unicode.Is(t, rune(c))
		if ok && start < 0 { start = c }
		if !ok && start >= 0 {
			n++; cnt += c - start
			if lo < 0 { lo = start }
			hi = c - 1; start = -1
		}
	}
	fmt.Println(name, n, cnt, lo, hi)
}

func keys(m map[string]*unicode.RangeTable) []string {
	var k []string
	for x := range m { k = append(k, x) }
	sort.Strings(k)
	return k
}

func main() {
	fmt.Println(unicode.Version)
	for _, k := range keys(unicode.Categories) { stat("cat", unicode.Categories[k]); fmt.Println(" ", k) }
	for _, k := range keys(unicode.Scripts) { stat("scr", unicode.Scripts[k]); fmt.Println(" ", k) }
	for _, k := range keys(unicode.Properties) { stat("prop", unicode.Properties[k]); fmt.Println(" ", k) }
	for c := 0; c <= unicode.MaxRune; c += 1 {
		if c >= 0xD800 && c <= 0xDFFF { continue }
		if !(c < 0x3000 || c%61 == 0 || (c >= 0x10400 && c < 0x10500) || (c >= 0x1E900 && c < 0x1E960) || (c >= 0xA640 && c < 0xA800)) { continue }
		r := rune(c)
		fmt.Println(c, b(unicode.IsLetter(r)), b(unicode.IsUpper(r)), b(unicode.IsLower(r)), b(unicode.IsTitle(r)), b(unicode.IsDigit(r)), b(unicode.IsNumber(r)),
			b(unicode.IsMark(r)), b(unicode.IsPunct(r)), b(unicode.IsSymbol(r)), b(unicode.IsSpace(r)), b(unicode.IsControl(r)), b(unicode.IsGraphic(r)), b(unicode.IsPrint(r)),
			int(unicode.ToUpper(r)), int(unicode.ToLower(r)), int(unicode.ToTitle(r)))
	}
}
