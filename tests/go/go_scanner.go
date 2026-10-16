package main

import (
	"bufio"
	"encoding/hex"
	"fmt"
	"go/scanner"
	"go/token"
	"os"
	"strconv"
	"strings"
)

func q(s string) string { return strconv.Quote(strings.ToValidUTF8(s, "\uFFFD")) }

func scanAll(fset *token.FileSet, name string, src []byte, mode scanner.Mode, verbose bool) {
	file := fset.AddFile(name, fset.Base(), len(src))
	var errs scanner.ErrorList
	var s scanner.Scanner
	s.Init(file, src, func(pos token.Position, msg string) { errs.Add(pos, msg) }, mode)
	n := 0
	h := 0
	for {
		pos, tok, lit := s.Scan()
		n++
		p := fset.Position(pos)
		line := fmt.Sprintf("%d %s %s %s end=%d", file.Offset(pos), p, tok, q(lit), s.End())
		if verbose {
			fmt.Println(" ", line)
		} else {
			for _, c := range []byte(line) {
				h = (h*31 + int(c)) % 1000000007
			}
		}
		if tok == token.EOF {
			break
		}
	}
	fmt.Printf("  tokens=%d hash=%d errors=%d lines=%d\n", n, h, s.ErrorCount, file.LineCount())
	for _, e := range errs {
		fmt.Println("  E", q(e.Error()))
	}
	errs.RemoveMultiples()
	fmt.Println("  list:", q(errs.Error()))
	fmt.Println("  unadj:", fset.PositionFor(file.Pos(len(src)), false), fset.Position(file.Pos(len(src))))
}

func main() {
	// token
	for t := token.Token(0); t < 92; t++ {
		fmt.Print(t, " ", t.Precedence(), t.IsLiteral(), t.IsOperator(), t.IsKeyword(), token.Lookup(t.String()) == t, "|")
	}
	fmt.Println()
	for _, s := range []string{"", "x", "_", "x1", "1x", "αβ", "ä١", "func", "Foo", "foo", "Ünü", "a b", "_9"} {
		fmt.Print(token.IsIdentifier(s), token.IsKeyword(s), token.IsExported(s), " ")
	}
	fmt.Println()
	fset := token.NewFileSet()
	f1 := fset.AddFile("a.go", -1, 30)
	f1.SetLinesForContent([]byte("ab\ncd\n\nefg\nhijklmnop\nqrstuvwxyz\n"))
	f2 := fset.AddFile("b.go", fset.Base()+5, 10)
	f2.AddLine(4)
	f2.AddLine(2)
	f2.AddLine(8)
	f1.AddLineColumnInfo(8, "x.go", 50, 3)
	f1.AddLineInfo(14, "", 7)
	fmt.Println(f1, f2, fset.Base(), f1.LineCount(), f1.Lines(), f2.Lines())
	for p := 0; p <= fset.Base(); p++ {
		if f := fset.File(token.Pos(p)); f != nil {
			fmt.Print(p, "=", f.Name(), ":", fset.Position(token.Pos(p)), "/", fset.PositionFor(token.Pos(p), false), " ")
		} else {
			fmt.Print(p, "=nil ")
		}
	}
	fmt.Println()
	fmt.Println(f1.SetLines([]int{0, 5, 3}), f1.SetLines([]int{0, 5, 30}), f1.SetLines([]int{0, 5, 9}), f1.Lines(), f1.LineStart(3))
	f1.MergeLine(1)
	fmt.Println(f1.Lines(), f1.Position(f1.Pos(7)), f1.Pos(100), f1.Offset(0), token.Position{}, token.Position{Filename: "z"})

	// scanner
	fs2 := token.NewFileSet()
	file, _ := os.Open("testdata/go_scanner.txt")
	sc := bufio.NewScanner(file)
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	k := 0
	for sc.Scan() {
		parts := strings.SplitN(sc.Text(), " ", 2)
		b, _ := hex.DecodeString(parts[1])
		k++
		fmt.Printf("#%d %s %s\n", k, parts[0], q(string(b)))
		scanAll(fs2, parts[0], b, scanner.ScanComments, true)
		scanAll(fs2, parts[0], b, 0, len(b) < 200)
	}
	for _, name := range []string{"scanner.go", "quote.go", "position.go", "walk.go"} {
		b, _ := os.ReadFile("testdata/gosrc/" + name + ".txt")
		fmt.Println("#", name)
		scanAll(fs2, name, b, scanner.ScanComments, false)
		scanAll(fs2, name, b, 0, false)
	}
}
