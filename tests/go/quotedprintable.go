package main

import (
	"bytes"
	"fmt"
	"io"
	"mime/quotedprintable"
	"strings"
)

func L(b []byte) string {
	s := []string{}
	for _, x := range b {
		s = append(s, fmt.Sprint(x))
	}
	return "[" + strings.Join(s, ", ") + "]"
}

func enc(chunks []string, binary bool) string {
	var buf bytes.Buffer
	w := quotedprintable.NewWriter(&buf)
	w.Binary = binary
	for _, c := range chunks {
		w.Write([]byte(c))
	}
	w.Close()
	return buf.String()
}

func dec(s []byte) string {
	b, err := io.ReadAll(quotedprintable.NewReader(bytes.NewReader(s)))
	e := ""
	if err != nil {
		e = err.Error()
	}
	return L(b) + " <" + e + ">"
}

func main() {
	ins := []string{"", "hello", "foo bar", "foo bar ", "foo\tbar\t", "a=b", "café", "日本語テキスト", "line1\nline2\n", "line1\r\nline2\r\n",
		"cr\ronly", "trailing space \nnext", "tab\t\r\nx", strings.Repeat("a", 75), strings.Repeat("a", 76), strings.Repeat("a", 77),
		strings.Repeat("a", 200), strings.Repeat("=", 30), strings.Repeat("ab ", 40), strings.Repeat("x", 73) + "é", strings.Repeat("x", 74) + "=",
		strings.Repeat("x", 75) + " ", strings.Repeat("x", 74) + " \n", "\x00\x01\x7f~!", "Héllo wörld, ça va? ¿Qué tal?\n" + strings.Repeat("ü", 40),
		" ", "\t", "\n", "\r", "\r\n\r\n", "a\n\rb", "end with cr\r"}
	for _, s := range ins {
		e1 := enc([]string{s}, false)
		e2 := enc([]string{s}, true)
		fmt.Printf("%q\n%q\n", e1, e2)
		fmt.Println(dec([]byte(e1)))
		fmt.Println(dec([]byte(e2)))
	}
	fmt.Printf("%q\n", enc([]string{"foo ", "bar", "\r", "\nbaz ", " "}, false))
	fmt.Printf("%q\n", enc([]string{strings.Repeat("y", 70), "zzzzzzzzzz", "\r", "\n"}, false))
	fmt.Printf("%q\n", enc([]string{"a\r", "\nb"}, true))
	decs := []string{"", "hello", "foo=20bar", "foo=3D=3d", "soft=\r\nbreak", "soft=\nbreak", "soft=  \r\nbreak", "soft= x\nbreak",
		"trail   \r\nx", "trail\t\t\nx", "=", "a=", "=A", "=4", "=G0", "=0G", "=\r\n", "a=\rb\n", "a=\nb", "x=\n", "x=", "=xy", "=\n=\n",
		"bad\x01char", "bad\x7fchar", "ok\tchar\r\n", "hi\x80\xffbin", "line1\nline2", "line1\r\nline2\r\n", "a=4\n", "a=4\r\nb",
		"q==3D", "=a=b=c", "x\r\ry", "abc =  \n", "=E6=97=A5=E6=9C=AC", "=e6=97=a5", "a=\t\r\nb", "a= \t", " =", "end =\r\n\r\n",
		"=4=", "==", "a\n=\r\n"}
	for _, s := range decs {
		fmt.Printf("%q %s\n", s, dec([]byte(s)))
	}
	long := strings.Repeat("a", 4095) + "\n" + strings.Repeat("b", 10)
	fmt.Println(len(long), dec([]byte(long))[len(dec([]byte(long)))-30:])
	long2 := strings.Repeat("c", 4096) + "\nxyz"
	r := dec([]byte(long2))
	fmt.Println(len(r), r[len(r)-40:])
	long3 := strings.Repeat("d", 5000)
	r3 := dec([]byte(long3))
	fmt.Println(len(r3), r3[len(r3)-40:])
	rd, _ := io.ReadAll(quotedprintable.NewReader(strings.NewReader("ok=41")))
	_, re := io.ReadAll(quotedprintable.NewReader(strings.NewReader("x=\rz")))
	fmt.Printf("Ok(%s) Err(\"%s\") %s %s\n", L(rd), re.Error(), enc([]string{"caf\u00e9 \u2603"}, false), L([]byte(enc([]string{"\xff\x00="}, false))))
}
