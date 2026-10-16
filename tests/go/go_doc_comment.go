package main

import (
	"bufio"
	"encoding/hex"
	"fmt"
	"go/doc/comment"
	"os"
	"strconv"
	"strings"
)

func texts(ts []comment.Text) string {
	var b strings.Builder
	for _, t := range ts {
		switch t := t.(type) {
		case comment.Plain:
			b.WriteString("p<" + string(t) + ">")
		case comment.Italic:
			b.WriteString("i<" + string(t) + ">")
		case *comment.Link:
			fmt.Fprintf(&b, "a<%v,%s;%s>", t.Auto, t.URL, texts(t.Text))
		case *comment.DocLink:
			fmt.Fprintf(&b, "d<%s,%s,%s;%s>", t.ImportPath, t.Recv, t.Name, texts(t.Text))
		}
	}
	return b.String()
}

func blocks(bs []comment.Block) string {
	var b strings.Builder
	for _, x := range bs {
		switch x := x.(type) {
		case *comment.Paragraph:
			b.WriteString("P(" + texts(x.Text) + ")")
		case *comment.Heading:
			b.WriteString("H(" + texts(x.Text) + ")")
		case *comment.Code:
			b.WriteString("C(" + x.Text + ")")
		case *comment.List:
			fmt.Fprintf(&b, "L(%v,%v,%v,%v", x.ForceBlankBefore, x.ForceBlankBetween, x.BlankBefore(), x.BlankBetween())
			for _, it := range x.Items {
				b.WriteString(" #" + it.Number + ":" + blocks(it.Content))
			}
			b.WriteString(")")
		}
	}
	return b.String()
}

func dump(d *comment.Doc) string {
	s := blocks(d.Content)
	for _, l := range d.Links {
		s += fmt.Sprintf("[%s|%s|%v]", l.Text, l.URL, l.Used)
	}
	return s
}

func main() {
	f, err := os.Open("testdata/go_doc_comment.txt")
	if err != nil {
		panic(err)
	}
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	for sc.Scan() {
		name, h, _ := strings.Cut(sc.Text(), " ")
		b, err := hex.DecodeString(h)
		if err != nil {
			panic(err)
		}
		d := new(comment.Parser).Parse(string(b))
		fmt.Println(name, strconv.Quote(string((&comment.Printer{}).Comment(d))))
		fmt.Println("  D", strconv.Quote(dump(d)))
	}
}
