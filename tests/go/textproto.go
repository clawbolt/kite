package main

import (
	"bufio"
	"bytes"
	"fmt"
	"net/textproto"
	"sort"
	"strconv"
	"strings"
)

func rd(s string) *textproto.Reader { return textproto.NewReader(bufio.NewReader(strings.NewReader(s))) }

func es(err error) string {
	if err == nil {
		return "<nil>"
	}
	return err.Error()
}

func hdr(m textproto.MIMEHeader) string {
	var ks []string
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	var out []string
	for _, k := range ks {
		var vs []string
		for _, v := range m[k] {
			vs = append(vs, strconv.Quote(v))
		}
		out = append(out, k+"="+strings.Join(vs, ","))
	}
	return strings.Join(out, " ")
}

func main() {
	headers := []string{
		"Content-Type: text/plain\r\nX-Foo: bar\r\n baz\r\n\tqux\r\nx-foo: second\r\n\r\nbody",
		"my-key: Value 1  \r\nLong-key: Even \n Longer Value\r\nmy-Key: Value 2\r\n\n",
		" leading: space\r\n\r\n",
		"Foo\r\n\r\n",
		"bad key: v\r\n\r\n",
		"Foo: bar\r\n",
		"Foo: bar\x01baz\r\n\r\n",
		"Foo: caf\xc3\xa9\r\n\r\n",
		"Content-type: x\r\nCONTENT-LENGTH: 5\r\naccept-encoding: gzip\r\nx-imforwards: a\r\nUser-agent: k\r\n\r\n",
		"",
		"\r\n",
		": empty key\r\n\r\n",
		"Key:\r\n\r\n",
		"A: 1\r\n  \r\nB: 2\r\n\r\n",
	}
	for _, h := range headers {
		r := rd(h)
		m, err := r.ReadMIMEHeader()
		rest, _ := r.ReadLine()
		fmt.Printf("H %q -> %s | %s | %q\n", h, hdr(m), es(err), rest)
	}
	r := rd("line one\r\nline two\nline three\r\r\n\r\nlast")
	for i := 0; i < 6; i++ {
		l, err := r.ReadLine()
		fmt.Printf("L %q %s\n", l, es(err))
	}
	r = rd("first\r\n  cont1\r\n\tcont2\r\nnext\r\n\r\nend \t ")
	for i := 0; i < 5; i++ {
		l, err := r.ReadContinuedLine()
		fmt.Printf("C %q %s\n", l, es(err))
	}
	for _, s := range []string{"220 ready\r\n", "220-multi\r\n220-line\r\n220 end\r\n", "250 ok\r\n", "2x0 bad\r\n", "22\r\n", "099 low\r\n", "500 fail\r\n", "220-a\r\nnot code\r\n221 other\r\n220 done\r\n", "220-a\r\n"} {
		for _, exp := range []int{0, 2, 22, 220} {
			c, m, err := rd(s).ReadResponse(exp)
			c2, m2, err2 := rd(s).ReadCodeLine(exp)
			fmt.Printf("R %q %d %d %q %s | %d %q %s\n", s, exp, c, m, es(err), c2, m2, es(err2))
		}
	}
	for _, s := range []string{"dotlines\r\n..dot\r\n.\r\nafter", "a\r\nb\n.\nrest", "no end\r\n", ".\r\n", "x\r\n..\r\n.\r\n"} {
		v, err := rd(s).ReadDotLines()
		b, err2 := rd(s).ReadDotBytes()
		fmt.Printf("D %q %q %s | %q %s\n", s, v, es(err), b, es(err2))
	}
	for _, s := range []string{"", "hello", "line\n.dot\r\nend\r", "a\r\n", "\n\n", ".\n", "x\ry", "end\r\n."} {
		var buf bytes.Buffer
		w := textproto.NewWriter(bufio.NewWriter(&buf))
		d := w.DotWriter()
		d.Write([]byte(s))
		d.Close()
		fmt.Printf("W %q %q\n", s, buf.String())
	}
	for _, k := range []string{"content-type", "CONTENT-TYPE", "Content-Type", "x-a-b-c", "foo bar", "foo_bar", "é", "", "-a", "a--b", "Accept-encoding", "ü-key", "MIME-version"} {
		fmt.Printf("K %q %q\n", k, textproto.CanonicalMIMEHeaderKey(k))
	}
	for _, s := range []string{"  x \r\n", "\t\n", "a b", ""} {
		fmt.Printf("T %q %q\n", s, textproto.TrimString(s))
	}
	h := textproto.MIMEHeader{}
	h.Add("x-a", "1")
	h.Add("X-A", "2")
	h.Set("y-b", "3")
	h.Set("Y-B", "4")
	h.Add("z", "5")
	h.Del("Z")
	fmt.Println("M", hdr(h), h.Get("x-a"), h.Values("X-a"), h.Get("nope"))
}
