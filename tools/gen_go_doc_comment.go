// 生成 tests/go/testdata/go_doc_comment.txt：每行 "名字 十六进制输入"
//   输入：go/doc/comment/testdata/*.txt 的 input 段、若干标准库文件里的注释（ast.CommentGroup.Text()）、手写边界用例
//   cd tools && GOROOT=… go run gen_go_doc_comment.go > ../tests/go/testdata/go_doc_comment.txt
package main

import (
	"encoding/hex"
	"fmt"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
)

var stdFiles = []string{
	"strings/strings.go", "fmt/doc.go", "go/ast/ast.go", "net/http/doc.go", "regexp/syntax/doc.go",
	"go/doc/comment/parse.go", "go/doc/comment/print.go", "encoding/json/encode.go", "time/format.go",
	"text/template/doc.go", "sort/sort.go", "os/file.go", "go/doc/comment/doc.go", "net/url/url.go",
}

var hand = []string{
	"", "\n", "   \n\t\n", "Hello.", "Hello, world.\n", "  indented para\n  second line\n",
	"Text.\n\nOld Heading\n\nMore text.", "Text.\n\nOld Heading:\n\nMore.", "Text.\n\nIt's Fine\n\nMore.",
	"Text.\n\nIts' Bad\n\nMore.", "Text.\n\nVersion 1.2\n\nMore.", "Text.\n\nEnds. With dot\n\nMore.",
	"Text.\n\nÜber Heading\n\nMore.", "Text.\n\nGo° Heading\n\nMore.", "Text.\n\nTrailing 9\n\nMore.",
	"Text.\n\nlower heading\n\nMore.", "Text.\n\nHeading Last\n\n\tcode", "Text.\n\nHeading End\n",
	"# Heading\n\nText.", "#\tTab heading", "# ", "#", "#x", "# #", "Para\n# Not heading",
	"[Go]: https://go.dev\n[Go]: https://golang.org\nUse [Go].", "Use [Go] and [Unused].\n\n[Go]: https://go.dev\n[Unused]: http://x.y\n[Go]: ftp://dup",
	"[a[b] and [ab]\n\n[ab]: http://ab.example", "[x]: bad://x", "[x]:http://x", "[x]: http://", "[x]:\thttp://tab.example  ",
	"[math.Sin] [io.Reader] [bytes.Buffer.Write] [*bytes.Buffer] [Name] [Recv.Name] [math] [nosuch] [nosuch.Name]",
	"x[math.Sin] [math.Sin]y ([math.Sin]). [encoding/json] [encoding/json.Marshal] [example.com/x.Y] [a//b] [/a] [a/] [-a/b] [.a/b]",
	"map[ast.Expr]TypeAndValue and [os.File.Close], [ fmt ] [fmt ] [fmt.]",
	"See https://go.dev/doc. And http://example.com/a(b)c) and https://x.y/[z]. ftp://h.x/p?q=1! mailto://a@b.c, gopher://x",
	"nntp://news.example/x file:///etc http://[::1]:8080/x https://.bad http://a.,b ahttp://x.y http://x.y/{a}b} http://x.y/~u%20v_w",
	"``quoted'' and ```not``` and a````b `` x ''", "ab``````cd````e", "x```y``z", "''''``````",
	"Text.\n- Not a list.\n - Here is the list.\n     • Using multiple bullets.\n          * Indentation does not matter.\n     + Lots of bullets.\nMore text.",
	"Text.\n\n 1. one\n 2) two\n 10. ten\n - bullet\n\n 3. three\n\nAfter.", "List:\n\n  - a\n\n  - b\n    continued\n\n  - c",
	"  1.x not\n  2. \n  3.", "Para\n1. not indented\n2. list\n  wrapped", "func main() {\n\tfmt.Println()\n}", "Run this:\n\tgo build\n}",
	"Para ends in brace {\n\tbody\n}", "Para ends in backslash \\\n\tmore", "\tcode\n\n\tmore code\n\nText\n\n\t\tdeeper\n\tless",
	"Mixed\r\nCRLF\r\n\r\n\tcode\r\n", "Unicode: café, naïve, 日本語 [日本] ``ü'' — ok.\n\n  • item ü\n  • item 2",
	"\u00a0nbsp line\n\u2003em", "A\u0085B\n\nX\u00a0\n", "1. Start\n2. Unindented list\n   continued\n\nAfter.",
	"- a\n- b\n\tcode after", "x\n  - item\n\n\n  - item2\n\n\n\n", "[]: http://empty\n[]", "[x]: http://x\nnot a def",
	"Hello [x] world.\n\n  - item [x]\n  - [Go]\n\n[x]: http://x\n[Go]: https://go.dev",
}

func main() {
	root := runtime.GOROOT()
	tx, _ := filepath.Glob(filepath.Join(root, "src/go/doc/comment/testdata/*.txt"))
	sort.Strings(tx)
	for _, f := range tx {
		b, err := os.ReadFile(f)
		if err != nil {
			panic(err)
		}
		s := string(b)
		i := strings.Index(s, "-- input --\n")
		if i < 0 {
			panic(f)
		}
		s = s[i+len("-- input --\n"):]
		if j := strings.Index(s, "\n-- "); j >= 0 {
			s = s[:j+1]
		}
		fmt.Println("txtar/"+filepath.Base(f), hex.EncodeToString([]byte(s)))
	}
	for _, name := range stdFiles {
		fset := token.NewFileSet()
		f, err := parser.ParseFile(fset, filepath.Join(root, "src", name), nil, parser.ParseComments)
		if err != nil {
			panic(err)
		}
		for k, g := range f.Comments {
			fmt.Printf("%s#%d %s\n", name, k, hex.EncodeToString([]byte(g.Text())))
		}
	}
	for k, s := range hand {
		fmt.Printf("hand#%d %s\n", k, hex.EncodeToString([]byte(s)))
	}
}
