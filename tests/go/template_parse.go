package main

import (
	"fmt"
	"sort"
	"strconv"
	"strings"
	"text/template/parse"
)

func f64(f float64) string { return strconv.FormatFloat(f, 'g', -1, 64) }

func dump(n parse.Node, ind string) {
	if n == nil {
		fmt.Printf("%snil\n", ind)
		return
	}
	p := int(n.Position())
	t := int(n.Type())
	switch x := n.(type) {
	case *parse.ListNode:
		if x == nil {
			fmt.Printf("%snil list\n", ind)
			return
		}
		fmt.Printf("%sList@%d t=%d n=%d\n", ind, p, t, len(x.Nodes))
		for _, c := range x.Nodes {
			dump(c, ind+"  ")
		}
	case *parse.TextNode:
		fmt.Printf("%sText@%d t=%d %s\n", ind, p, t, strconv.Quote(string(x.Text)))
	case *parse.CommentNode:
		fmt.Printf("%sComment@%d t=%d %s\n", ind, p, t, strconv.Quote(x.Text))
	case *parse.ActionNode:
		fmt.Printf("%sAction@%d t=%d L%d\n", ind, p, t, x.Line)
		dump(x.Pipe, ind+"  ")
	case *parse.PipeNode:
		if x == nil {
			fmt.Printf("%snil pipe\n", ind)
			return
		}
		fmt.Printf("%sPipe@%d t=%d L%d assign=%t decl=%d cmds=%d\n", ind, p, t, x.Line, x.IsAssign, len(x.Decl), len(x.Cmds))
		for _, d := range x.Decl {
			dump(d, ind+"  ")
		}
		for _, c := range x.Cmds {
			dump(c, ind+"  ")
		}
	case *parse.CommandNode:
		fmt.Printf("%sCommand@%d t=%d args=%d\n", ind, p, t, len(x.Args))
		for _, a := range x.Args {
			dump(a, ind+"  ")
		}
	case *parse.IdentifierNode:
		fmt.Printf("%sIdentifier@%d t=%d %s\n", ind, p, t, x.Ident)
	case *parse.VariableNode:
		fmt.Printf("%sVariable@%d t=%d %q\n", ind, p, t, x.Ident)
	case *parse.DotNode:
		fmt.Printf("%sDot@%d t=%d\n", ind, p, t)
	case *parse.NilNode:
		fmt.Printf("%sNil@%d t=%d\n", ind, p, t)
	case *parse.FieldNode:
		fmt.Printf("%sField@%d t=%d %q\n", ind, p, t, x.Ident)
	case *parse.ChainNode:
		fmt.Printf("%sChain@%d t=%d %q\n", ind, p, t, x.Field)
		dump(x.Node, ind+"  ")
	case *parse.BoolNode:
		fmt.Printf("%sBool@%d t=%d %t\n", ind, p, t, x.True)
	case *parse.NumberNode:
		fmt.Printf("%sNumber@%d t=%d %s int=%t:%d uint=%t:%d float=%t:%s complex=%t:%s,%s\n", ind, p, t, x.Text,
			x.IsInt, x.Int64, x.IsUint, x.Uint64, x.IsFloat, f64(x.Float64), x.IsComplex, f64(real(x.Complex128)), f64(imag(x.Complex128)))
	case *parse.StringNode:
		fmt.Printf("%sString@%d t=%d %s %s\n", ind, p, t, x.Quoted, strconv.Quote(x.Text))
	case *parse.IfNode:
		fmt.Printf("%sIf@%d t=%d L%d\n", ind, p, t, x.Line)
		branch(&x.BranchNode, ind)
	case *parse.RangeNode:
		fmt.Printf("%sRange@%d t=%d L%d\n", ind, p, t, x.Line)
		branch(&x.BranchNode, ind)
	case *parse.WithNode:
		fmt.Printf("%sWith@%d t=%d L%d\n", ind, p, t, x.Line)
		branch(&x.BranchNode, ind)
	case *parse.BreakNode:
		fmt.Printf("%sBreak@%d t=%d L%d\n", ind, p, t, x.Line)
	case *parse.ContinueNode:
		fmt.Printf("%sContinue@%d t=%d L%d\n", ind, p, t, x.Line)
	case *parse.TemplateNode:
		fmt.Printf("%sTemplate@%d t=%d L%d %q\n", ind, p, t, x.Line, x.Name)
		if x.Pipe == nil {
			fmt.Printf("%s  nil pipe\n", ind)
		} else {
			dump(x.Pipe, ind+"  ")
		}
	default:
		fmt.Printf("%s?? %T\n", ind, n)
	}
}

func branch(b *parse.BranchNode, ind string) {
	dump(b.Pipe, ind+"  ")
	dump(b.List, ind+"  ")
	if b.ElseList != nil {
		fmt.Printf("%s  else:\n", ind)
		dump(b.ElseList, ind+"  ")
	}
}

var builtins = map[string]any{
	"printf":   fmt.Sprintf,
	"contains": strings.Contains,
}

func show(name, input string, mode parse.Mode, ld, rd string, funcs ...map[string]any) {
	tr := parse.New(name)
	tr.Mode = mode
	set := make(map[string]*parse.Tree)
	tmpl, err := tr.Parse(input, ld, rd, set, funcs...)
	fmt.Printf("== %s %s\n", name, strconv.Quote(input))
	if err != nil {
		fmt.Printf("error: %s\n", err)
		return
	}
	fmt.Printf("string: %s\n", strconv.Quote(tmpl.Root.String()))
	fmt.Printf("name=%s parse_name=%s empty=%t\n", tmpl.Name, tmpl.ParseName, parse.IsEmptyTree(tmpl.Root))
	dump(tmpl.Root, "")
	var names []string
	for k := range set {
		names = append(names, k)
	}
	sort.Strings(names)
	for _, k := range names {
		if k == name {
			continue
		}
		fmt.Printf("tree %q: %s\n", k, strconv.Quote(set[k].Root.String()))
		dump(set[k].Root, "  ")
	}
	if len(tmpl.Root.Nodes) > 0 {
		loc, ctx := tmpl.ErrorContext(tmpl.Root.Nodes[len(tmpl.Root.Nodes)-1])
		fmt.Printf("context: %s %s\n", loc, strconv.Quote(ctx))
	}
}

var parseTests = [][2]string{
	{"empty", ""},
	{"comment", "{{/*\n\n\n*/}}"},
	{"spaces", " \t\n"},
	{"text", "some text"},
	{"emptyAction", "{{}}"},
	{"field", "{{.X}}"},
	{"simple command", "{{printf}}"},
	{"$ invocation", "{{$}}"},
	{"variable invocation", "{{with $x := 3}}{{$x 23}}{{end}}"},
	{"variable with fields", "{{$.I}}"},
	{"multi-word command", "{{printf `%d` 23}}"},
	{"pipeline", "{{.X|.Y}}"},
	{"pipeline with decl", "{{$x := .X|.Y}}"},
	{"nested pipeline", "{{.X (.Y .Z) (.A | .B .C) (.E)}}"},
	{"field applied to parentheses", "{{(.Y .Z).Field}}"},
	{"simple if", "{{if .X}}hello{{end}}"},
	{"if with else", "{{if .X}}true{{else}}false{{end}}"},
	{"if with else if", "{{if .X}}true{{else if .Y}}false{{end}}"},
	{"if else chain", "+{{if .X}}X{{else if .Y}}Y{{else if .Z}}Z{{end}}+"},
	{"simple range", "{{range .X}}hello{{end}}"},
	{"chained field range", "{{range .X.Y.Z}}hello{{end}}"},
	{"nested range", "{{range .X}}hello{{range .Y}}goodbye{{end}}{{end}}"},
	{"range with else", "{{range .X}}true{{else}}false{{end}}"},
	{"range over pipeline", "{{range .X|.M}}true{{else}}false{{end}}"},
	{"range []int", "{{range .SI}}{{.}}{{end}}"},
	{"range 1 var", "{{range $x := .SI}}{{.}}{{end}}"},
	{"range 2 vars", "{{range $x, $y := .SI}}{{.}}{{end}}"},
	{"range with break", "{{range .SI}}{{.}}{{break}}{{end}}"},
	{"range with continue", "{{range .SI}}{{.}}{{continue}}{{end}}"},
	{"constants", "{{range .SI 1 -3.2i true false 'a' nil}}{{end}}"},
	{"template", "{{template `x`}}"},
	{"template with arg", "{{template `x` .Y}}"},
	{"with", "{{with .X}}hello{{end}}"},
	{"with with else", "{{with .X}}hello{{else}}goodbye{{end}}"},
	{"with with else with", "{{with .X}}hello{{else with .Y}}goodbye{{end}}"},
	{"with else chain", "{{with .X}}X{{else with .Y}}Y{{else with .Z}}Z{{end}}"},
	{"trim left", "x \r\n\t{{- 3}}"},
	{"trim right", "{{3 -}}\n\n\ty"},
	{"trim left and right", "x \r\n\t{{- 3 -}}\n\n\ty"},
	{"trim with extra spaces", "x\n{{-  3   -}}\ny"},
	{"comment trim left", "x \r\n\t{{- /* hi */}}"},
	{"comment trim right", "{{/* hi */ -}}\n\n\ty"},
	{"comment trim left and right", "x \r\n\t{{- /* */ -}}\n\n\ty"},
	{"block definition", `{{block "foo" .}}hello{{end}}`},
	{"newline in assignment", "{{ $x \n := \n 1 \n }}"},
	{"newline in empty action", "{{\n}}"},
	{"newline in pipeline", "{{\n\"x\"\n|\nprintf\n}}"},
	{"newline in comment", "{{/*\nhello\n*/}}"},
	{"newline in comment 2", "{{-\n/*\nhello\n*/\n-}}"},
	{"spaces around continue", "{{range .SI}}{{.}}{{ continue }}{{end}}"},
	{"spaces around break", "{{range .SI}}{{.}}{{ break }}{{end}}"},
	{"unclosed action", "hello{{range"},
	{"unmatched end", "{{end}}"},
	{"unmatched else", "{{else}}"},
	{"unmatched else after if", "{{if .X}}hello{{end}}{{else}}"},
	{"multiple else", "{{if .X}}1{{else}}2{{else}}3{{end}}"},
	{"missing end", "hello{{range .x}}"},
	{"missing end after else", "hello{{range .x}}{{else}}"},
	{"undefined function", "hello{{undefined}}"},
	{"undefined variable", "{{$x}}"},
	{"variable undefined after end", "{{with $x := 4}}{{end}}{{$x}}"},
	{"variable undefined in template", "{{template $v}}"},
	{"declare with field", "{{with $x.Y := 4}}{{end}}"},
	{"template with field ref", "{{template .X}}"},
	{"template with var", "{{template $v}}"},
	{"invalid punctuation", "{{printf 3, 4}}"},
	{"multidecl outside range", "{{with $v, $u := 3}}{{end}}"},
	{"too many decls in range", "{{range $u, $v, $w := 3}}{{end}}"},
	{"dot applied to parentheses", "{{printf (printf .).}}"},
	{"adjacent args", "{{printf 3`x`}}"},
	{"adjacent args with .", "{{printf `x`.}}"},
	{"extra end after if", "{{if .X}}a{{else if .Y}}b{{end}}{{end}}"},
	{"break outside range", "{{range .}}{{end}} {{break}}"},
	{"continue outside range", "{{range .}}{{end}} {{continue}}"},
	{"break in range else", "{{range .}}{{else}}{{break}}{{end}}"},
	{"continue in range else", "{{range .}}{{else}}{{continue}}{{end}}"},
	{"bug0a", "{{$x := 0}}{{$x}}"},
	{"bug0b", "{{$x += 1}}{{$x}}"},
	{"bug0c", "{{$x ! 2}}{{$x}}"},
	{"bug0d", "{{$x % 3}}{{$x}}"},
	{"bug0e", "{{range $x := $y := 3}}{{end}}"},
	{"bug1a", "{{$x:=.}}{{$x!2}}"},
	{"bug1b", "{{$x:=.}}{{$x+2}}"},
	{"bug1c", "{{$x:=.}}{{$x +2}}"},
	{"bug2a", "{{range $x := 0}}{{$x}}{{end}}"},
	{"bug2b", "{{range $x = 0}}{{$x}}{{end}}"},
	{"dot after integer", "{{1.E}}"},
	{"dot after float", "{{0.1.E}}"},
	{"dot after boolean", "{{true.E}}"},
	{"dot after char", "{{'a'.any}}"},
	{"dot after string", `{{"hello".guys}}`},
	{"dot after dot", "{{..E}}"},
	{"dot after nil", "{{nil.E}}"},
	{"wrong pipeline dot", "{{12|.}}"},
	{"wrong pipeline number", "{{.|12|printf}}"},
	{"wrong pipeline string", "{{.|printf|\"error\"}}"},
	{"wrong pipeline char", "{{12|printf|'e'}}"},
	{"wrong pipeline boolean", "{{.|true}}"},
	{"wrong pipeline nil", "{{'c'|nil}}"},
	{"empty pipeline", `{{printf "%d" ( ) }}`},
	{"block definition 2", `{{block "foo"}}hello{{end}}`},
	{"paren nesting normal", "{{ (( 1 )) }}"},
	{"paren nesting deeper", "{{ ((((( 1 ))))) | printf }}"},
	{"paren nesting with other constructs", "{{ if ((( true ))) }}YES{{ end }}"},
	{"unclosed1", "line1\n{{"},
	{"unclosed2", "line1\n{{define `x`}}line2\n{{"},
	{"unclosed3", "line1\n{{\"x\"\n\"y\"\n"},
	{"unclosed4", "{{\n\n\n\n\n"},
	{"var1", "line1\n{{\nx\n}}"},
	{"function", "{{foo}}"},
	{"comment1", "{{/*}}"},
	{"comment2", "{{/*\nhello\n}}"},
	{"comment3", "{{/* x */ .}}"},
	{"lparen", "{{.X (1 2 3}}"},
	{"rparen", "{{.X 1 2 3 ) }}"},
	{"rparen2", "{{(.X 1 2 3"},
	{"space", "{{`x`3}}"},
	{"idchar", "{{a#}}"},
	{"charconst", "{{'a}}"},
	{"stringconst", `{{"a}}`},
	{"rawstringconst", "{{`a}}"},
	{"number", "{{0xi}}"},
	{"multidefine", "{{define `a`}}a{{end}}{{define `a`}}b{{end}}"},
	{"eof", "{{range .X}}"},
	{"variable", "{{$x := 23}}{{with $x.y := 3}}{{$x 23}}{{end}}"},
	{"multidecl", "{{$a,$b,$c := 23}}"},
	{"undefvar", "{{$a}}"},
	{"wrongdot", "{{true.any}}"},
	{"wrongpipeline", "{{12|false}}"},
	{"emptypipeline", `{{ ( ) }}`},
	{"multilinerawstring", "{{ $v := `\n` }} {{"},
	{"rangeundefvar", "{{range $k}}{{end}}"},
	{"rangeundefvars", "{{range $k, $v}}{{end}}"},
	{"rangemissingvalue1", "{{range $k,}}{{end}}"},
	{"rangemissingvalue2", "{{range $k, $v := }}{{end}}"},
	{"rangenotvariable1", "{{range $k, .}}{{end}}"},
	{"rangenotvariable2", "{{range $k, 123 := .}}{{end}}"},
	{"block", `a{{block "inner" .}}bar{{.}}baz{{end}}b`},
	{"defines", "{{define `x`}}X{{.}}{{end}}\nmid\n{{define \"y\"}}Y{{template `x` .}}{{end}}tail"},
	{"empty define then real", "{{define `t`}} {{end}}{{define `t`}}real{{end}}"},
	{"real then empty define", "{{define `t`}}real{{end}}{{define `t`}} {{end}}"},
	{"define bad name", "{{define \"a\\q\"}}x{{end}}"},
	{"define no name", "{{define x}}x{{end}}"},
	{"define else", "{{define `x`}}x{{else}}y{{end}}"},
	{"block else", "{{block `x` .}}x{{else}}y{{end}}"},
	{"unicode text", "héllo {{.Nàme}} 世界 {{printf \"日本\"}}\n{{/* ü */}}x"},
	{"unicode bad char", "{{.X ü}}"},
	{"unicode bad char 2", "{{.X 世}}"},
	{"bad char in action", "{{.X \x01}}"},
	{"bad char after ident", "{{.X€}}"},
	{"strings", "{{printf \"a\\tb\\u00e9\\x41\" `raw\\n` \"\"}}"},
	{"bad string escape", "{{printf \"\\q\"}}"},
	{"chain on var", "{{$x := .}}{{$x.A.B}}{{($x).C}}{{(.X).Y.Z}}"},
	{"chain on paren nil", "{{(nil).X}}"},
	{"chain on ident", "{{printf.X}}"},
	{"colon", "{{$x :- 1}}"},
	{"long item", "{{printf \"abcdefghijklmnop\" | 12345678901234}}"},
	{"long item 2", "{{printf | \"abcdefghijklmnop\"}}"},
	{"keyword in error", "{{if}}"},
	{"else with", "{{with .X}}{{else with .Y}}y{{else}}n{{end}}"},
	{"if with decls", "{{if $a := .X}}{{$a}}{{else if $b := .Y}}{{$a}}{{$b}}{{end}}{{$a}}"},
	{"assign outer", "{{$a := 1}}{{if true}}{{$a = 2}}{{end}}{{$a}}"},
	{"range assign", "{{$i := 0}}{{$v := 0}}{{range $i, $v = .}}{{end}}"},
	{"template pipe decl", "{{template `x` $y := .}}{{$y}}"},
	{"number forms", "{{printf 0 -0 7_3 0b1001001 073 0o73 0x73 0x7_3 -73 +73 1e9 -1.2 1e19 -1e19 0x_1p4 0x_1p-4 4i -1.2+4.2i 073i 0i -1.2+0i -12+0i 13+0i 0xdeadbeef 18446744073709551615 9223372036854775808 -9223372036854775808 1.5e300 .5 1. 0xef}}"},
	{"char forms", "{{printf 'a' '\\n' '\\\\' '\\'' '\\xFF' 'パ' '\\u30d1' '\\U000030d1' '\\377'}}"},
	{"bad number +-2", "{{+-2}}"},
	{"bad number 0x123.", "{{0x123.}}"},
	{"bad number 1e.", "{{1e.}}"},
	{"bad number 1+2.", "{{1+2.}}"},
	{"bad char xx", "{{'xx'}}"},
	{"bad char long", "{{'433937734937734969526500969526500'}}"},
	{"bad number 089", "{{089}}"},
	{"bad number overflow", "{{123456789012345678901234567890}}"},
	{"bad number 1__2", "{{1__2}}"},
	{"bad number 0x", "{{0x}}"},
	{"bad number .", "{{. 1e}}"},
	{"line numbers", "{{printf 1234}}\n{{printf 1234}}\n{{printf 1234}}\n"},
	{"comment no delim", "{{/* x */x}}"},
	{"trim marker no space", "{{-3}}{{3-}}"},
	{"right trim then delim", "{{.X -}}"},
	{"spaces then trim", "{{.X  -}} y"},
	{"pipe after decl", "{{$x := printf | printf}}"},
	{"range comma eof", "{{range $x,"},
	{"paren in template", "{{template `x` (printf .)}}"},
	{"nested parens chain", "{{((.X).Y).Z}}"},
	{"empty command", "{{printf |}}"},
	{"error in nested define", "a{{define `b`}}{{if}}{{end}}"},
	{"multi errors line", "x\ny\n{{if .X}}\n{{else}}\n{{end}}{{end}}"},
}

func main() {
	for _, t := range parseTests {
		show(t[0], t[1], 0, "", "", builtins)
	}
	// comments mode
	for _, in := range []string{"{{/*\n\n\n*/}}", "x \r\n\t{{- /* hi */}}", "{{/* hi */ -}}\n\n\ty", "x \r\n\t{{- /* */ -}}\n\n\ty", "{{define `x`}}{{/* c */}}{{end}}{{/* d */}}"} {
		show("comments", in, parse.ParseComments, "", "")
	}
	// custom delims
	for _, in := range []string{`[[.X]]`, `[[/* a comment */]]`, `[[if .X]]y[[else]]z[[end]]`, `[[range .X]][[break]][[continue]][[end]]`, `[[with .X]]y[[end]]`, `[[template "name" .]]`, "{{.X}} [[- .Y -]] {{.Z}}", "[[.X]"} {
		show("delims", in, parse.ParseComments, "[[", "]]")
	}
	show("delims2", "<%if .X%>a<%- else -%>b<%end%>", 0, "<%", "%>")
	show("delims3", "${.X}}", 0, "$", "}")
	// skip func check
	show("skip func check", "{{fn 1 2}}", parse.SkipFuncCheck, "", "")
	show("skip func check 2", "{{if fn}}{{fn | g}}{{end}}", parse.SkipFuncCheck, "", "")
	// break / continue as functions
	bf := map[string]any{"break": func(in any) any { return in }}
	show("break func", "{{range .X}}{{break 20}}{{end}}", 0, "", "", bf)
	show("break no func", "{{range .X}}{{break 20}}{{end}}", 0, "", "", map[string]any{})
	cf := map[string]any{"continue": func(in any) any { return in }, "nilfn": nil}
	show("continue func", "{{continue 1}}{{range .}}{{continue}}{{end}}", 0, "", "", cf, nil)
	show("nil func", "{{nilfn}}", 0, "", "", cf)
	show("two maps", "{{a}}{{b}}", 0, "", "", map[string]any{"a": 1}, map[string]any{"b": 2})
	// Parse (package function)
	set, err := parse.Parse("top", "{{define `a`}}A{{end}}top{{template `a`}}", "", "", builtins)
	var names []string
	for k := range set {
		names = append(names, k)
	}
	sort.Strings(names)
	fmt.Println("Parse:", names, err)
	for _, k := range names {
		fmt.Printf("  %s: %q %s\n", k, set[k].Root.String(), set[k].ParseName)
	}
	set, err = parse.Parse("bad", "{{define `a`}}A{{end}}{{.X", "", "")
	fmt.Println("Parse bad:", err)
	// reuse a tree set: second Parse into the same set
	ts := make(map[string]*parse.Tree)
	t1, err1 := parse.New("one").Parse("{{define `shared`}}S1{{end}}one", "", "", ts)
	t2, err2 := parse.New("two").Parse("{{define `shared`}}S2{{end}}two", "", "", ts)
	fmt.Println(t1 != nil, err1, t2 != nil, err2)
	t3, err3 := parse.New("three").Parse("{{define `x`}} {{end}}three", "", "", ts)
	fmt.Println(t3 != nil, err3, ts["shared"].Root.String(), len(ts))
	// IsEmptyTree
	fmt.Println(parse.IsEmptyTree(nil))
	for _, in := range []string{``, `hello`, " \t\n \t\n", "{{/* comment */}}", `{{define "x"}}something{{end}}`,
		"{{define `x`}}something{{end}}\n\n{{define `y`}}something{{end}}\n\n", "{{define `x`}}something{{end}}\nx\n{{define `y`}}something{{end}}\ny\n",
		"{{define `x`}}something{{end}}{{if 3}}foo{{end}}", "{{.}}", "{{range .}}{{end}}", "{{with .}}{{end}}", "{{template `x`}}", "\u00a0\u2003\v\f", "\u200b"} {
		tr, err := parse.New("root").Parse(in, "", "", make(map[string]*parse.Tree), nil)
		fmt.Printf("empty %q: %t %v\n", in, parse.IsEmptyTree(tr.Root), err)
	}
	// ErrorContext
	tr, _ := parse.New("ctx").Parse("ab\ncd{{if .X}}\n  {{.Y | printf \"%d\"}}{{end}}\n{{$z := 1}}", "", "", make(map[string]*parse.Tree), builtins)
	var walk func(n parse.Node)
	walk = func(n parse.Node) {
		loc, ctx := tr.ErrorContext(n)
		fmt.Printf("ctx %s %q\n", loc, ctx)
		switch x := n.(type) {
		case *parse.ListNode:
			for _, c := range x.Nodes {
				walk(c)
			}
		case *parse.IfNode:
			walk(x.Pipe)
			walk(x.List)
		case *parse.ActionNode:
			walk(x.Pipe)
		case *parse.PipeNode:
			for _, d := range x.Decl {
				walk(d)
			}
			for _, c := range x.Cmds {
				walk(c)
				for _, a := range c.Args {
					walk(a)
				}
			}
		}
	}
	walk(tr.Root)
	// Copy
	cp := tr.Copy()
	fmt.Println(cp.Name, cp.ParseName, cp.Root.String() == tr.Root.String())
	// NodeType constants
	fmt.Println(parse.NodeText, parse.NodeAction, parse.NodeBool, parse.NodeChain, parse.NodeCommand, parse.NodeDot,
		parse.NodeField, parse.NodeIdentifier, parse.NodeIf, parse.NodeList, parse.NodeNil, parse.NodeNumber, parse.NodePipe,
		parse.NodeRange, parse.NodeString, parse.NodeTemplate, parse.NodeVariable, parse.NodeWith, parse.NodeComment,
		parse.NodeBreak, parse.NodeContinue, parse.ParseComments, parse.SkipFuncCheck)
	// NewIdentifier
	id := parse.NewIdentifier("foo").SetPos(7)
	fmt.Println(id.String(), id.Position(), id.Type())
	// big text
	var sb strings.Builder
	for i := 0; i < 200; i++ {
		fmt.Fprintf(&sb, "line %d {{if .A%d}}{{.B | printf \"%%d\" %d}}{{else}}x{{end}}\n", i, i, i)
	}
	tb, err := parse.New("big").Parse(sb.String(), "", "", make(map[string]*parse.Tree), builtins)
	fmt.Println(err, len(tb.Root.Nodes), tb.Root.String() == sb.String())
	last := tb.Root.Nodes[len(tb.Root.Nodes)-2].(*parse.IfNode)
	fmt.Println(last.Line, last.Position(), last.Pipe.Line)
}
