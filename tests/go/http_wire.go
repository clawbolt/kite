// tests/go/http_wire.go：对拍 Go 1.27 的 http.ReadRequest / http.ReadResponse / chunked 编解码。
// 输入 tests/go/testdata/http/wire.txt：net/http 测试里 reqTests、badRequestTests、respTests 的原始报文加补充用例
// （生成：把 testdata/http/gen_wire_test.go.txt 放进 $GOROOT/src/net/http 跑 go test -run TestDumpWire）。
package main

import (
	"bufio"
	"bytes"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"net/http/httputil"
	"os"
	"sort"
	"strings"
)

func hdr(h http.Header) string {
	var ks []string
	for k := range h {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	var p []string
	for _, k := range ks {
		p = append(p, fmt.Sprintf("%s:%q", k, h[k]))
	}
	return "{" + strings.Join(p, " ") + "}"
}

func errs(e error) string {
	if e == nil {
		return ""
	}
	return e.Error()
}

func main() {
	f, _ := os.Open("tests/go/testdata/http/wire.txt")
	sc := bufio.NewScanner(f)
	sc.Buffer(nil, 1<<20)
	n := 0
	for sc.Scan() {
		parts := strings.Split(sc.Text(), "|")
		var a [][]byte
		for _, h := range parts[1:] {
			b, _ := hex.DecodeString(h)
			a = append(a, b)
		}
		n++
		switch parts[0] {
		case "req":
			br := bufio.NewReader(bytes.NewReader(a[0]))
			req, err := http.ReadRequest(br)
			if err != nil {
				fmt.Printf("%d req err %q\n", n, err.Error())
				continue
			}
			body, berr := io.ReadAll(req.Body)
			rest := ""
			if berr == nil {
				r, _ := io.ReadAll(br)
				rest = string(r)
			}
			fmt.Printf("%d req %q %q %q %d.%d host=%q uri=%q cl=%d te=%q close=%v\n  h=%s\n  tr=%s\n  body=%q err=%q rest=%q\n",
				n, req.Method, req.URL.String(), req.Proto, req.ProtoMajor, req.ProtoMinor, req.Host, req.RequestURI,
				req.ContentLength, req.TransferEncoding, req.Close, hdr(req.Header), hdr(req.Trailer), body, errs(berr), rest)
		case "resp":
			br := bufio.NewReader(bytes.NewReader(a[1]))
			resp, err := http.ReadResponse(br, &http.Request{Method: string(a[0])})
			if err != nil {
				fmt.Printf("%d resp err %q\n", n, err.Error())
				continue
			}
			body, berr := io.ReadAll(resp.Body)
			rest := ""
			if berr == nil {
				r, _ := io.ReadAll(br)
				rest = string(r)
			}
			fmt.Printf("%d resp %q %d %q %d.%d cl=%d te=%q close=%v\n  h=%s\n  tr=%s\n  body=%q err=%q rest=%q\n",
				n, resp.Status, resp.StatusCode, resp.Proto, resp.ProtoMajor, resp.ProtoMinor,
				resp.ContentLength, resp.TransferEncoding, resp.Close, hdr(resp.Header), hdr(resp.Trailer), body, errs(berr), rest)
		case "chunk":
			br := bufio.NewReader(bytes.NewReader(a[0]))
			body, berr := io.ReadAll(httputil.NewChunkedReader(br))
			rest := ""
			if berr == nil {
				r, _ := io.ReadAll(br)
				rest = string(r)
			}
			fmt.Printf("%d chunk body=%q err=%q rest=%q\n", n, body, errs(berr), rest)
		case "chunkw":
			var buf bytes.Buffer
			w := httputil.NewChunkedWriter(&buf)
			for _, c := range a {
				w.Write(c)
			}
			w.Close()
			fmt.Printf("%d chunkw %q\n", n, buf.String())
		}
	}
}
