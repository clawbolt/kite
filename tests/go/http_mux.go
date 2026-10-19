// tests/go/http_mux.go：对拍 Go 1.27 ServeMux 的模式解析、冲突检测和路由（Handler / ServeHTTP）。
// 输入 tests/go/testdata/http/mux.txt：pattern_test.go、routing_tree_test.go、serve_test.go 里的模式和请求加补充用例
// （生成：testdata/http/gen_mux_test.go.txt 放进 $GOROOT/src/net/http 跑 go test -run TestDumpMux）。
// 冲突错误里的 "(registered at 文件:行)" 去掉后再比。
package main

import (
	"bufio"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"regexp"
	"strings"
)

var loc = regexp.MustCompile(` \(registered at [^)]*\)`)
var wild = regexp.MustCompile(`\{([^}]*)\}`)

func main() {
	f, _ := os.Open("tests/go/testdata/http/mux.txt")
	sc := bufio.NewScanner(f)
	var mux *http.ServeMux
	n := 0
	for sc.Scan() {
		parts := strings.Split(sc.Text(), "|")
		var a []string
		for _, h := range parts[1:] {
			b, _ := hex.DecodeString(h)
			a = append(a, string(b))
		}
		n++
		switch parts[0] {
		case "new":
			mux = http.NewServeMux()
			fmt.Printf("%d new\n", n)
		case "pat":
			p := a[0]
			var names []string
			for _, m := range wild.FindAllStringSubmatch(p, -1) {
				nm := strings.TrimSuffix(m[1], "...")
				if nm != "$" && nm != "" {
					names = append(names, nm)
				}
			}
			func() {
				defer func() {
					if e := recover(); e != nil {
						fmt.Printf("%d pat %q err %q\n", n, p, loc.ReplaceAllString(fmt.Sprint(e), ""))
					}
				}()
				mux.HandleFunc(p, func(w http.ResponseWriter, r *http.Request) {
					var vs []string
					for _, nm := range names {
						vs = append(vs, nm+"="+r.PathValue(nm))
					}
					fmt.Fprintf(w, "pat=%s vals=%q", r.Pattern, vs)
				})
				fmt.Printf("%d pat %q ok\n", n, p)
			}()
		case "req":
			u, err := url.Parse(a[2])
			if err != nil {
				fmt.Printf("%d req url err %q\n", n, err.Error())
				continue
			}
			r := &http.Request{Method: a[0], Host: a[1], URL: u, Header: http.Header{}}
			_, pat := mux.Handler(r)
			rec := httptest.NewRecorder()
			mux.ServeHTTP(rec, r)
			body := ""
			if rec.Code == 200 {
				body = rec.Body.String()
			}
			fmt.Printf("%d req %s %q %q: %d pat=%q loc=%q allow=%q %s\n", n, a[0], a[1], a[2], rec.Code, pat,
				rec.Header().Get("Location"), rec.Header().Get("Allow"), body)
		}
	}
}
