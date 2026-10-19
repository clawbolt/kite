// tests/go/http_write.go：对拍 Go 1.27 的 Request.Write / Response.Write，以及 net.SplitHostPort / JoinHostPort。
// 输入 tests/go/testdata/http/write.txt（生成器 testdata/http/gen_write.go.txt）。正文用 io.NopCloser(bytes.NewReader(b))。
package main

import (
	"bufio"
	"bytes"
	"encoding/hex"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
)

func main() {
	f, _ := os.Open("tests/go/testdata/http/write.txt")
	sc := bufio.NewScanner(f)
	sc.Buffer(nil, 1<<20)
	n := 0
	for sc.Scan() {
		parts := strings.Split(sc.Text(), "|")
		kv := map[string][]string{}
		for _, p := range parts[1:] {
			k, v, _ := strings.Cut(p, "=")
			b, _ := hex.DecodeString(v)
			kv[k] = append(kv[k], string(b))
		}
		one := func(k, d string) string {
			if v, ok := kv[k]; ok {
				return v[0]
			}
			return d
		}
		num := func(k string, d int) int {
			x, _ := strconv.Atoi(one(k, strconv.Itoa(d)))
			return x
		}
		hdr := func(k string) http.Header {
			h := http.Header{}
			for _, s := range kv[k] {
				a, b, _ := strings.Cut(s, ":")
				h.Add(a, b)
			}
			return h
		}
		var body io.ReadCloser
		if v, ok := kv["body"]; ok {
			body = io.NopCloser(bytes.NewReader([]byte(v[0])))
		}
		var te []string
		if v, ok := kv["te"]; ok {
			te = strings.Split(v[0], ",")
		}
		var tr http.Header
		if _, ok := kv["tr"]; ok {
			tr = hdr("tr")
		}
		var buf bytes.Buffer
		var err error
		n++
		if parts[0] == "req" {
			u, perr := url.Parse(one("url", ""))
			if perr != nil {
				fmt.Printf("%d url err %q\n", n, perr.Error())
				continue
			}
			r := &http.Request{Method: one("method", ""), URL: u, Host: one("host", ""), Header: hdr("h"), Body: body,
				ContentLength: int64(num("cl", 0)), TransferEncoding: te, Close: one("close", "") == "1", Trailer: tr}
			err = r.Write(&buf)
		} else {
			r := &http.Response{Status: one("status", ""), StatusCode: num("code", 0), ProtoMajor: num("major", 1),
				ProtoMinor: num("minor", 1), Header: hdr("h"), Body: body, ContentLength: int64(num("cl", 0)),
				TransferEncoding: te, Close: one("close", "") == "1", Uncompressed: one("uncompressed", "") == "1", Trailer: tr}
			if m, ok := kv["reqmethod"]; ok {
				r.Request = &http.Request{Method: m[0]}
			}
			err = r.Write(&buf)
		}
		e := ""
		if err != nil {
			e = err.Error()
		}
		fmt.Printf("%d %s err=%q\n  %q\n", n, parts[0], e, buf.String())
	}
	for _, hp := range []string{"a:80", "a", ":80", "a:", "[::1]:80", "[::1]", "[::1]x:80", "[::1]:80:1", "[::1", "a:b:80",
		"[a]b]:80", "a[b:80", "]a:80", "[[a]:80", "[fe80::1%en0]:443", ""} {
		h, p, err := net.SplitHostPort(hp)
		e := ""
		if err != nil {
			e = err.Error()
		}
		fmt.Printf("split %q: %q %q %q\n", hp, h, p, e)
	}
	for _, hp := range [][2]string{{"a", "80"}, {"::1", "80"}, {"", ""}, {"fe80::1%en0", "1"}} {
		fmt.Printf("join %q\n", net.JoinHostPort(hp[0], hp[1]))
	}
}
