package main

import (
	"bytes"
	"fmt"
	"io"
	"mime/multipart"
	"net/textproto"
	"sort"
	"strconv"
	"strings"
)

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

func parts(name, body, boundary string, raw bool) {
	fmt.Printf("CASE %s %q\n", name, body)
	r := multipart.NewReader(strings.NewReader(body), boundary)
	for i := 0; i < 10; i++ {
		var p *multipart.Part
		var err error
		if raw {
			p, err = r.NextRawPart()
		} else {
			p, err = r.NextPart()
		}
		if err != nil {
			fmt.Println("  END", es(err))
			return
		}
		data, rerr := io.ReadAll(p)
		fmt.Printf("  PART %s | name=%q file=%q | %q %s\n", hdr(p.Header), p.FormName(), p.FileName(), data, es(rerr))
	}
}

func main() {
	b := "MyBoundary"
	parts("basic", "--MyBoundary\r\nContent-Disposition: form-data; name=\"a\"\r\n\r\nvalue a\r\n--MyBoundary\r\nContent-Disposition: form-data; name=\"f\"; filename=\"../dir/x.txt\"\r\nContent-Type: text/plain\r\n\r\nfile\r\ncontent\r\n--MyBoundary--\r\n", b, false)
	parts("preamble", "This is a preamble\r\n\r\n--MyBoundary\r\nX: 1\r\n\r\nbody one\r\n--MyBoundary  \r\nX: 2\r\n\r\n--MyBoundary-not\r\nstill body\r\n--MyBoundary--   \r\nepilogue", b, false)
	parts("lf", "--MyBoundary\nA: b\n\nline1\nline2\n--MyBoundary\n\nsecond\n--MyBoundary--\n", b, false)
	parts("qp", "--MyBoundary\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\ncaf=C3=A9 =\r\nsoft\r\n--MyBoundary--\r\n", b, false)
	parts("qp-raw", "--MyBoundary\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\ncaf=C3=A9 =\r\nsoft\r\n--MyBoundary--\r\n", b, true)
	parts("unterminated", "--MyBoundary\r\nA: 1\r\n\r\nno end here", b, false)
	parts("nofinal", "--MyBoundary\r\nA: 1\r\n\r\nx\r\n--MyBoundary\r\n", b, false)
	parts("garbage", "--MyBoundary\r\nA: 1\r\n\r\nx\r\n--MyBoundary\r\nB: 2\r\n\r\ny\r\n--MyBoundary--\r\n", b, false)
	parts("badheader", "--MyBoundary\r\nBad Header\r\n\r\nx\r\n--MyBoundary--\r\n", b, false)
	parts("empty", "", b, false)
	parts("onlyfinal", "--MyBoundary--", b, false)
	parts("emptyboundary", "--\r\n", "", false)
	parts("expect", "--MyBoundary\r\nA: 1\r\n\r\nx\r\n--MyBoundary--junk\r\n", b, false)
	parts("disp", "--MyBoundary\r\nContent-Disposition: attachment; filename=\"a/b/c.bin\"\r\n\r\n1\r\n--MyBoundary\r\nContent-Disposition: form-data; name=\"x\"; filename=\"\"\r\n\r\n2\r\n--MyBoundary\r\nContent-Disposition: form-data; name=x;;\r\n\r\n3\r\n--MyBoundary--\r\n", b, false)

	// Writer
	var buf bytes.Buffer
	w := multipart.NewWriter(&buf)
	fmt.Println("SB", es(w.SetBoundary("my boundary")), es(w.SetBoundary("")), es(w.SetBoundary("bad|char")), es(w.SetBoundary("trail ")), es(w.SetBoundary(strings.Repeat("x", 71))))
	w.SetBoundary("abc:def")
	fmt.Println("CT", w.FormDataContentType(), w.Boundary())
	w.WriteField("name", "Kite")
	w.WriteField("we\"ird\r\nname", "v2")
	fw, _ := w.CreateFormFile("upload", "dir/hello.txt")
	fw.Write([]byte("hello\r\nworld"))
	h := textproto.MIMEHeader{}
	h.Set("X-Custom", "1")
	h.Add("A-First", "z")
	h.Add("A-First", "y")
	pw, _ := w.CreatePart(h)
	pw.Write([]byte("custom"))
	w.Close()
	fmt.Printf("OUT %q\n", buf.String())
	fmt.Println("SB2", es(w.SetBoundary("again")))
	r := multipart.NewReader(bytes.NewReader(buf.Bytes()), "abc:def")
	f, err := r.ReadForm(1 << 20)
	fmt.Println("FORM", es(err))
	if err == nil {
		var ks []string
		for k := range f.Value {
			ks = append(ks, k)
		}
		sort.Strings(ks)
		for _, k := range ks {
			fmt.Printf("  V %q %q\n", k, f.Value[k])
		}
		for k, fhs := range f.File {
			for _, fh := range fhs {
				ff, _ := fh.Open()
				d, _ := io.ReadAll(ff)
				fmt.Printf("  F %q %q %d %q %s\n", k, fh.Filename, fh.Size, d, hdr(fh.Header))
			}
		}
	}
	fmt.Println("FD", multipart.FileContentDisposition("a\\b", "c\"d"))
}
