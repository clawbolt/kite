package main

import (
	"bufio"
	"bytes"
	"encoding/hex"
	"fmt"
	"go/ast"
	"go/parser"
	"go/scanner"
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
		fmt.Printf("  %s:\n%s", label, s)
	} else {
		fmt.Printf("  %s: len=%d hash=%d\n", label, len(s), h(s))
	}
}

func errs(err error) {
	if err == nil {
		fmt.Println("  ok")
		return
	}
	list := err.(scanner.ErrorList)
	fmt.Printf("  %d errors: %s\n", len(list), strconv.Quote(list.Error()))
	for _, e := range list {
		fmt.Println("   ", strconv.Quote(e.Error()))
	}
}

func fp(fset *token.FileSet, x any, nn bool) string {
	var buf bytes.Buffer
	if nn {
		ast.Fprint(&buf, fset, x, ast.NotNilFilter)
	} else {
		ast.Fprint(&buf, fset, x, nil)
	}
	return buf.String()
}

func posdump(fset *token.FileSet, f ast.Node) string {
	var sb strings.Builder
	ast.Inspect(f, func(n ast.Node) bool {
		if n == nil {
			sb.WriteString(")")
			return true
		}
		fmt.Fprintf(&sb, "(%T %d %d", n, n.Pos(), n.End())
		return true
	})
	return sb.String()
}

func resdump(f *ast.File) string {
	var sb strings.Builder
	ast.Inspect(f, func(n ast.Node) bool {
		if id, ok := n.(*ast.Ident); ok {
			if id.Obj != nil {
				fmt.Fprintf(&sb, "%s@%d->%s %s %d|", id.Name, id.NamePos, id.Obj.Kind, id.Obj.Name, id.Obj.Pos())
			} else {
				fmt.Fprintf(&sb, "%s@%d->nil|", id.Name, id.NamePos)
			}
		}
		return true
	})
	for _, id := range f.Unresolved {
		fmt.Fprintf(&sb, "U %s@%d|", id.Name, id.NamePos)
	}
	if f.Scope != nil {
		var names []string
		for k := range f.Scope.Objects {
			names = append(names, k)
		}
		// sort
		for i := range names {
			for j := i + 1; j < len(names); j++ {
				if names[j] < names[i] {
					names[i], names[j] = names[j], names[i]
				}
			}
		}
		for _, k := range names {
			o := f.Scope.Objects[k]
			fmt.Fprintf(&sb, "S %s %s %v|", k, o.Kind, o.Data)
		}
	}
	return sb.String()
}

func run(name string, src []byte) {
	// A：注释 + 跳过解析 + 全部错误
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, name, src, parser.ParseComments|parser.SkipObjectResolution|parser.AllErrors)
	fmt.Println("  A goversion=" + strconv.Quote(f.GoVersion))
	errs(err)
	show("A fprint", fp(fset, f, true))
	show("A pos", posdump(fset, f))
	if len(src) < 400 {
		show("A fprint-nil", fp(fset, f, false))
	}
	// B：默认模式（解析标识符，最多 10 个错误）
	fset = token.NewFileSet()
	f, err = parser.ParseFile(fset, name, src, 0)
	errs(err)
	show("B res", resdump(f))
	f.Scope = nil
	show("B fprint", fp(fset, f, true))
	// C：声明错误 + 全部错误
	fset = token.NewFileSet()
	_, err = parser.ParseFile(fset, name, src, parser.DeclarationErrors|parser.AllErrors)
	errs(err)
	// D：只要包名 / 只要 import
	fset = token.NewFileSet()
	f, err = parser.ParseFile(fset, name, src, parser.ImportsOnly|parser.ParseComments)
	errs(err)
	show("D fprint", fp(fset, f, true))
	fset = token.NewFileSet()
	f, err = parser.ParseFile(fset, name, src, parser.PackageClauseOnly)
	errs(err)
	show("E fprint", fp(fset, f, true))
}

func main() {
	file, _ := os.Open("testdata/go_parser.txt")
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
	for _, name := range []string{"scanner.go", "quote.go", "position.go", "walk.go"} {
		b, _ := os.ReadFile("testdata/gosrc/" + name + ".txt")
		fmt.Println("#", name)
		run(name, b)
	}
	exprs := []string{"a + b*c", "x[1:2:3]", "f(a, b...)", "<-chan <-chan int", "<-chan chan<- int", "func(x int) int { return x }",
		"map[string][]int{\"a\": {1}}", "a.(type)", "struct{ x int `tag` }", "[...]int{1,2}", "(", "a +", "x.(T)\n", "1 2", "~int | string",
		"interface{ M(); ~int }", "a[b, c]", "*p++", "a = b", "x = = y", "<-x", "-x.y[z](w)", "T[int]{}"}
	for _, e := range exprs {
		fmt.Println("# expr", strconv.Quote(e))
		x, err := parser.ParseExpr(e)
		errs(err)
		fmt.Print(fp(nil, x, true))
		fset := token.NewFileSet()
		x, err = parser.ParseExprFrom(fset, "e.go", []byte(e), 0)
		show("from", fp(fset, x, false))
	}
}
