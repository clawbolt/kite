// tests/go/http_proto.go：对拍 Go 1.27 net/http 的协议层纯函数（DetectContentType、CanonicalHeaderKey、
// hasToken、ParseHTTPVersion、ParseTime、ParseCookie、readCookies、Cookie.Valid、Header.Write、StatusText）。
// 输入是 tests/go/testdata/http/cases.txt（生成器 testdata/http/gen.go.txt，取自 sniff_test.go / header_test.go 的表）。
// hasToken 和 readCookies 没有导出，这里照 header.go / cookie.go 抄了一份。
package main

import (
	"bufio"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/textproto"
	"os"
	"strconv"
	"strings"
	"time"
)

func hasToken(v, token string) bool {
	if len(token) > len(v) || token == "" {
		return false
	}
	if v == token {
		return true
	}
	for sp := 0; sp <= len(v)-len(token); sp++ {
		if b := v[sp]; b != token[0] && b|0x20 != token[0] {
			continue
		}
		if sp > 0 && !isTokenBoundary(v[sp-1]) {
			continue
		}
		if endPos := sp + len(token); endPos != len(v) && !isTokenBoundary(v[endPos]) {
			continue
		}
		if strings.EqualFold(v[sp:sp+len(token)], token) {
			return true
		}
	}
	return false
}

func isTokenBoundary(b byte) bool { return b == ' ' || b == ',' || b == '\t' }

func cookies(cs []*http.Cookie) string {
	var p []string
	for _, c := range cs {
		p = append(p, fmt.Sprintf("%q=%q/%v", c.Name, c.Value, c.Quoted))
	}
	return "[" + strings.Join(p, " ") + "]"
}

func main() {
	f, _ := os.Open("tests/go/testdata/http/cases.txt")
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		parts := strings.Split(sc.Text(), "|")
		op := parts[0]
		var a []string
		for _, h := range parts[1:] {
			b, _ := hex.DecodeString(h)
			a = append(a, string(b))
		}
		var res string
		switch op {
		case "sniff":
			res = http.DetectContentType([]byte(a[0]))
		case "canon":
			res = strconv.Quote(http.CanonicalHeaderKey(a[0]))
		case "token":
			res = fmt.Sprint(hasToken(a[0], a[1]))
		case "ver":
			ma, mi, ok := http.ParseHTTPVersion(a[0])
			res = fmt.Sprint(ma, mi, ok)
		case "time":
			t, err := http.ParseTime(a[0])
			if err != nil {
				res = "err"
			} else {
				res = fmt.Sprint(t.UnixMilli())
			}
		case "pcookie":
			cs, err := http.ParseCookie(a[0])
			if err != nil {
				res = "error: " + err.Error()
			} else {
				res = cookies(cs)
			}
		case "rcookie":
			h := http.Header{"Cookie": strings.Split(a[1], "\x00")}
			r := &http.Request{Header: h}
			if a[0] == "" {
				res = cookies(r.Cookies())
			} else {
				res = cookies(r.CookiesNamed(a[0]))
			}
		case "valid":
			c := &http.Cookie{Name: a[0], Value: a[1], Path: a[2], Domain: a[3], Partitioned: strings.Contains(a[4], "p"), Secure: strings.Contains(a[4], "s")}
			if a[5] != "" {
				y, _ := strconv.Atoi(a[5])
				c.Expires = time.Date(y, 1, 1, 0, 0, 0, 0, time.UTC)
			}
			if err := c.Valid(); err != nil {
				res = err.Error()
			} else {
				res = "ok"
			}
		case "hwrite":
			h := http.Header{}
			if a[0] != "" {
				kv := strings.Split(a[0], "\x00")
				for i := 0; i+1 < len(kv); i += 2 {
					k := kv[i]
					if k == "x-lower" {
						h[k] = append(h[k], kv[i+1]) // 不规范化的键
					} else {
						h.Add(k, kv[i+1])
					}
				}
			}
			var sb strings.Builder
			h.Write(&sb)
			res = strconv.Quote(sb.String())
			_ = textproto.MIMEHeader(h)
		case "status":
			n, _ := strconv.Atoi(a[0])
			res = strconv.Quote(http.StatusText(n))
		}
		fmt.Println(op+":", res)
	}
}
