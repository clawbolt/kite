// tests/go/httputil.go：对拍 Go 1.27 net/http/httputil。
// 输入：testdata/http/wire.txt（DumpRequest / DumpResponse，原始报文）、testdata/http/write.txt（DumpRequestOut）、
// testdata/httputil/proxy.txt（NewSingleHostReverseProxy：每个请求 × 每个目标配默认响应，每个响应配默认请求）。
package main

import (
	"bufio"
	"bytes"
	"encoding/hex"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
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
		q := make([]string, len(h[k]))
		for j, x := range h[k] {
			q[j] = strconv.Quote(x)
		}
		b.WriteString(strconv.Quote(k) + ":[" + strings.Join(q, " ") + "]")
	}
	b.WriteString("}")
	return b.String()
}

func dec(s string) []byte { b, _ := hex.DecodeString(s); return b }

func es(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

type rtFunc func(*http.Request) (*http.Response, error)

func (f rtFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func lines(p string) [][]string {
	f, _ := os.Open(p)
	sc := bufio.NewScanner(f)
	sc.Buffer(nil, 1<<20)
	var out [][]string
	for sc.Scan() {
		out = append(out, strings.Split(sc.Text(), "|"))
	}
	return out
}

func main() {
	log.SetOutput(io.Discard)
	n := 0
	for _, p := range lines("tests/go/testdata/http/wire.txt") {
		n++
		switch p[0] {
		case "req":
			for _, body := range []bool{true, false} {
				r, err := http.ReadRequest(bufio.NewReader(bytes.NewReader(dec(p[1]))))
				if err != nil {
					fmt.Printf("%d req read err %q\n", n, err.Error())
					break
				}
				d, err := httputil.DumpRequest(r, body)
				fmt.Printf("%d req %v err=%q\n  %q\n", n, body, es(err), d)
			}
		case "resp":
			for _, body := range []bool{true, false} {
				m := string(dec(p[1]))
				r, err := http.ReadResponse(bufio.NewReader(bytes.NewReader(dec(p[2]))), &http.Request{Method: m})
				if err != nil {
					fmt.Printf("%d resp read err %q\n", n, err.Error())
					break
				}
				d, err := httputil.DumpResponse(r, body)
				fmt.Printf("%d resp %v err=%q\n  %q\n", n, body, es(err), d)
			}
		}
	}
	n = 0
	for _, parts := range lines("tests/go/testdata/http/write.txt") {
		n++
		if parts[0] != "req" {
			continue
		}
		for _, body := range []bool{true, false} {
			kv := map[string][]string{}
			for _, p := range parts[1:] {
				k, v, _ := strings.Cut(p, "=")
				kv[k] = append(kv[k], string(dec(v)))
			}
			one := func(k, d string) string {
				if v, ok := kv[k]; ok {
					return v[0]
				}
				return d
			}
			h := func(k string) http.Header {
				h := http.Header{}
				for _, s := range kv[k] {
					a, b, _ := strings.Cut(s, ":")
					h.Add(a, b)
				}
				return h
			}
			var bd io.ReadCloser
			if v, ok := kv["body"]; ok {
				bd = io.NopCloser(bytes.NewReader([]byte(v[0])))
			}
			var te []string
			if v, ok := kv["te"]; ok {
				te = strings.Split(v[0], ",")
			}
			var tr http.Header
			if _, ok := kv["tr"]; ok {
				tr = h("tr")
			}
			u, perr := url.Parse(one("url", ""))
			if perr != nil {
				break
			}
			cl, _ := strconv.Atoi(one("cl", "0"))
			r := &http.Request{Method: one("method", ""), URL: u, Host: one("host", ""), Header: h("h"), Body: bd,
				ContentLength: int64(cl), TransferEncoding: te, Close: one("close", "") == "1", Trailer: tr}
			d, err := httputil.DumpRequestOut(r, body)
			e := es(err)
			// Transport 按 map 顺序校验头，多个非法头时报哪个不确定：只比前缀
			if strings.HasPrefix(e, "net/http: invalid header ") {
				e = "net/http: invalid header …"
			}
			fmt.Printf("%d out %v err=%q\n  %q\n", n, body, e, d)
		}
	}
	var reqs, resps, targets [][]byte
	for _, p := range lines("tests/go/testdata/httputil/proxy.txt") {
		switch p[0] {
		case "req":
			reqs = append(reqs, dec(p[1]))
		case "resp":
			resps = append(resps, dec(p[1]))
		case "target":
			targets = append(targets, dec(p[1]))
		}
	}
	proxy := func(rawReq, rawResp, target []byte) {
		tu, _ := url.Parse(string(target))
		rp := httputil.NewSingleHostReverseProxy(tu)
		rp.Transport = rtFunc(func(out *http.Request) (*http.Response, error) {
			b := []byte{}
			if out.Body != nil {
				b, _ = io.ReadAll(out.Body)
			}
			fmt.Printf("  out %s url=%q host=%q close=%v cl=%d body=%q\n  out hdr=%s\n", out.Method, out.URL.String(), out.Host, out.Close,
				out.ContentLength, b, hdr(out.Header))
			return http.ReadResponse(bufio.NewReader(bytes.NewReader(rawResp)), out)
		})
		in, err := http.ReadRequest(bufio.NewReader(bytes.NewReader(rawReq)))
		if err != nil {
			fmt.Printf("  read err %q\n", err.Error())
			return
		}
		in.RemoteAddr = "192.0.2.9:5555"
		rw := httptest.NewRecorder()
		rp.ServeHTTP(rw, in)
		res := rw.Result()
		fmt.Printf("  code=%d\n  header=%s\n  trailer=%s\n  body=%q\n", res.StatusCode, hdr(res.Header), hdr(res.Trailer), rw.Body.String())
	}
	for i, r := range reqs {
		for j, t := range targets {
			fmt.Printf("proxy req %d target %d\n", i, j)
			proxy(r, resps[0], t)
		}
	}
	for i, r := range resps {
		fmt.Printf("proxy resp %d\n", i)
		proxy(reqs[0], r, targets[0])
	}
}
