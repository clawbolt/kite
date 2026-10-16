package main

import (
	"bufio"
	"bytes"
	"encoding/hex"
	"fmt"
	"go/ast"
	"go/format"
	"go/parser"
	"go/printer"
	"go/token"
	"os"
	"strconv"
	"strings"
)

var full = os.Getenv("FULL") != ""

func h(s string) int {
	x := 0
	for _, c := range []byte(s) {
		x = (x*31 + int(c)) % 1000000007
	}
	return x
}

func show(label, s string) {
	if full {
		fmt.Printf("  %s:\n%s\n", label, s)
	} else {
		fmt.Printf("  %s: len=%d hash=%d\n", label, len(s), h(s))
	}
}

func pr(cfg printer.Config, src []byte) string {
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, "a.go", src, parser.ParseComments|parser.SkipObjectResolution)
	if err != nil {
		return "parse error"
	}
	var buf bytes.Buffer
	if err := cfg.Fprint(&buf, fset, f); err != nil {
		return "ERR " + err.Error()
	}
	return buf.String()
}

func run(name string, src []byte) {
	out, err := format.Source(src)
	if err != nil {
		fmt.Println("  source error " + strconv.Quote(err.Error()))
	} else {
		show("source", string(out))
	}
	show("fprint", pr(printer.Config{Tabwidth: 8}, src))
	show("raw", pr(printer.Config{Mode: printer.RawFormat}, src))
	if len(src) < 8000 {
		show("spaces4", pr(printer.Config{Mode: printer.UseSpaces | printer.SourcePos, Tabwidth: 4, Indent: 1}, src))
		show("tabs", pr(printer.Config{Mode: printer.TabIndent, Tabwidth: 2}, src))
		fset := token.NewFileSet()
		f, err := parser.ParseFile(fset, "a.go", src, parser.ParseComments|parser.SkipObjectResolution)
		if err == nil {
			var buf bytes.Buffer
			if err := format.Node(&buf, fset, f); err != nil {
				fmt.Println("  node error " + strconv.Quote(err.Error()))
			} else {
				show("node", buf.String())
			}
			ast.SortImports(fset, f)
			var b2 bytes.Buffer
			printer.Fprint(&b2, fset, f)
			show("sorted", b2.String())
			// 每个顶层声明单独打印
			var parts []string
			for _, d := range f.Decls {
				var b3 bytes.Buffer
				printer.Fprint(&b3, fset, d)
				parts = append(parts, b3.String())
			}
			show("decls", strings.Join(parts, "\n--\n"))
		}
	}
}

func main() {
	file, _ := os.Open("testdata/go_printer.txt")
	sc := bufio.NewScanner(file)
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	k := 0
	for sc.Scan() {
		parts := strings.SplitN(sc.Text(), " ", 2)
		b, _ := hex.DecodeString(parts[1])
		k++
		fmt.Printf("#%d %s\n", k, parts[0])
		run(parts[0], b)
	}
	exprs := []string{"a+b*c", "(a+b)*c", "x[i:j:k]", "f(a, b...)", "func(x int) int { return x*2 }", "struct{x, y int; z string `json:\"z\"`}",
		"map[string][]*T{\"a\": nil}", "<-chan <-chan int", "a.(type)", "x.f(1)(2)[3]", "-x + ^y &^ z", "a<<1 | b>>2", "*p", "&T{1, 2}",
		"interface{ M(int) string; ~int | ~string }", "0X1P-2", "0XABCp+3", "0O17", "0B101", "1E9", "0001i", "T[int, string]{}", "[...]int{1, 2, 3}"}
	for _, e := range exprs {
		fmt.Println("# expr " + strconv.Quote(e))
		x, err := parser.ParseExpr(e)
		if err != nil {
			fmt.Println("  error")
			continue
		}
		var buf bytes.Buffer
		printer.Fprint(&buf, token.NewFileSet(), x)
		fmt.Println("  " + strconv.Quote(buf.String()))
		var b2 bytes.Buffer
		format.Node(&b2, token.NewFileSet(), x)
		fmt.Println("  " + strconv.Quote(b2.String()))
	}
}
