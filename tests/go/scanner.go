package main

import (
	"fmt"
	"strconv"
	"strings"
	"text/scanner"
)

func run(name, src string, setup func(s *scanner.Scanner)) {
	fmt.Println("CASE", name)
	var s scanner.Scanner
	s.Init(strings.NewReader(src))
	s.Filename = name
	s.Error = func(s *scanner.Scanner, msg string) {
		fmt.Println("  ERR", s.Pos().String(), s.Pos().Offset, s.Position.String(), msg)
	}
	if setup != nil {
		setup(&s)
	}
	for i := 0; i < 400; i++ {
		tok := s.Scan()
		fmt.Println(" ", scanner.TokenString(tok), strconv.Quote(s.TokenText()), s.Position.String(), s.Offset, s.Pos().String())
		if tok == scanner.EOF {
			break
		}
	}
	fmt.Println("  count", s.ErrorCount)
}

func main() {
	goSrc := "// comment\npackage main\n\nimport \"fmt\"\n\nfunc main() {\n\tx := 0x1F + 0o17 + 0b101 + 1_000 + 3.14e-2 + .5 + 0x1p-2 + 'a' + '\\n'\n\ts := `raw\nstring` + \"esc\\t\\u00e9\\\"\"\n\t/* block\n comment */ fmt.Println(x, s) // trailing\n}\n"
	run("go", goSrc, nil)
	run("comments", goSrc, func(s *scanner.Scanner) { s.Mode = scanner.ScanIdents | scanner.ScanInts | scanner.ScanComments })
	run("idents", goSrc, func(s *scanner.Scanner) { s.Mode = scanner.ScanIdents | scanner.ScanInts })
	run("bad", "0x 0b2 0o9 08 1__0 _1 0x1.8 0b1.0 1e 1p3 0x1e+ 'ab' '' 'x \"unterminated\n `raw /* open", nil)
	run("escapes", `"\q" "\x4" "\u12" "\777" "\U0010FFFF" '\'' "\0" '\400'`, nil)
	run("unicode", "\ufeffhéllo 世界 x₁ ٣٤ αβγ \xff\xfe abc\x00def", nil)
	run("ws", "a\nb\tc  d\r\ne", func(s *scanner.Scanner) { s.Whitespace = 1<<' ' | 1<<'\t' })
	run("ident-rune", "foo-bar $x a.b 9z", func(s *scanner.Scanner) {
		s.IsIdentRune = func(ch rune, i int) bool { return ch == '-' && i > 0 || ch == '$' && i == 0 || ch >= 'a' && ch <= 'z' || ch >= '0' && ch <= '9' && i > 0 }
	})
	run("empty", "", nil)
	run("only-ws", "  \n\n  ", nil)
	run("nums", "1 12 012 0X_1 1.e5 1.5E+10 0.e-1_0 00 0_7 9.9.9", nil)
	// Next / Peek
	var s scanner.Scanner
	s.Init(strings.NewReader("ab\ncé"))
	for {
		p := s.Peek()
		c := s.Next()
		fmt.Println("NEXT", p, c, s.Pos().String(), s.Pos().Offset)
		if c == scanner.EOF {
			break
		}
	}
	for _, r := range []rune{'a', '\n', 0x4e16, -9, 0x110000} {
		fmt.Println("TS", scanner.TokenString(r))
	}
}
