// tests/go/httptest.go：对拍 Go 1.27 net/http/httptest 的 NewRequest 和 ResponseRecorder。
// 输入 tests/go/testdata/httptest/req.txt（方法 目标 正文，十六进制；"-" 表示 nil 正文）和 rec.txt（记录器操作脚本）。
package main

import (
	"bufio"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"sort"
	"strconv"
	"strings"
)

func hdr(h http.Header) string {
	if h == nil {
		return "nil"
	}
	ks := make([]string, 0, len(h))
	for k := range h {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	var b strings.Builder
	b.WriteString("{")
	for i, k := range ks {
		if i > 0 {
			b.WriteString(" ")
		}
		b.WriteString(strconv.Quote(k) + ":" + qlist(h[k]))
	}
	b.WriteString("}")
	return b.String()
}

func qlist(xs []string) string {
	q := make([]string, len(xs))
	for i, x := range xs {
		q[i] = strconv.Quote(x)
	}
	return "[" + strings.Join(q, " ") + "]"
}

func dec(s string) string { b, _ := hex.DecodeString(s); return string(b) }

func main() {
	f, _ := os.Open("tests/go/testdata/httptest/req.txt")
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		p := strings.Split(sc.Text(), " ")
		m, t := dec(p[0]), dec(p[1])
		func() {
			defer func() {
				if e := recover(); e != nil {
					fmt.Printf("%q %q panic %q\n", m, t, fmt.Sprint(e))
				}
			}()
			var r *http.Request
			body := ""
			if p[2] == "-" {
				r = httptest.NewRequest(m, t, nil)
			} else {
				body = dec(p[2][1:])
				r = httptest.NewRequest(m, t, strings.NewReader(body))
			}
			sn := ""
			if r.TLS != nil {
				sn = r.TLS.ServerName
			}
			fmt.Printf("%q %q %q url=%q host=%q uri=%q %s %d.%d cl=%d close=%v remote=%q tls=%v sn=%q hdr=%s\n", m, t, r.Method, r.URL.String(), r.Host,
				r.RequestURI, r.Proto, r.ProtoMajor, r.ProtoMinor, r.ContentLength, r.Close, r.RemoteAddr, r.TLS != nil, sn, hdr(r.Header))
		}()
	}
	data, _ := os.ReadFile("tests/go/testdata/httptest/rec.txt")
	for i, block := range strings.Split(strings.TrimSuffix(string(data), "\n"), "\n\n") {
		rw := httptest.NewRecorder()
		fmt.Printf("== %d\n", i)
		for _, line := range strings.Split(block, "\n") {
			op, arg, _ := strings.Cut(line, " ")
			func() {
				defer func() {
					if e := recover(); e != nil {
						fmt.Printf("  %s panic %q\n", op, fmt.Sprint(e))
					}
				}()
				switch op {
				case "H", "A":
					k, v, _ := strings.Cut(arg, " ")
					if op == "H" {
						rw.Header().Set(k, v)
					} else {
						rw.Header().Add(k, v)
					}
				case "D":
					rw.Header().Del(arg)
				case "W", "S":
					var n int
					var err error
					if op == "W" {
						b, _ := hex.DecodeString(arg)
						n, err = rw.Write(b)
					} else {
						n, err = rw.WriteString(arg)
					}
					es := ""
					if err != nil {
						es = err.Error()
					}
					fmt.Printf("  %s n=%d err=%q\n", op, n, es)
				case "C":
					c, _ := strconv.Atoi(arg)
					rw.WriteHeader(c)
				case "F":
					rw.Flush()
				case "R":
					res := rw.Result()
					fmt.Printf("  code=%d flushed=%v status=%q %s %d.%d cl=%d\n  header=%s\n  trailer=%s\n  body=%q\n", rw.Code, rw.Flushed, res.Status,
						res.Proto, res.ProtoMajor, res.ProtoMinor, res.ContentLength, hdr(res.Header), hdr(res.Trailer), rw.Body.String())
				}
			}()
		}
	}
}
