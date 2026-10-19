// tests/go/url.go：对拍 Go 1.27 的 net/url。输入 tests/go/testdata/url/cases.txt 来自 url_test.go 的各张表
// （生成方法：把 net/url 复制到本地包，加 testdata/url/gen_dump_test.go.txt 这个测试文件跑 go test -run TestDump）。
// resolvePath 没有导出，这里照 url.go 抄了一份。
package main

import (
	"bufio"
	"bytes"
	"encoding/hex"
	"fmt"
	"net/url"
	"os"
	"strings"
)

func resolvePath(base, ref string) string {
	var full string
	if ref == "" {
		full = base
	} else if ref[0] != '/' {
		i := strings.LastIndex(base, "/")
		full = base[:i+1] + ref
	} else {
		full = ref
	}
	if full == "" {
		return ""
	}
	dst := make([]byte, 0, len(full)+1)
	dst = append(dst, '/')
	elem := ""
	remaining := full
	found := true
	first := true
	for found {
		elem, remaining, found = strings.Cut(remaining, "/")
		switch elem {
		case ".":
			first = false
			continue
		case "..":
			if i := bytes.LastIndexByte(dst[1:], '/'); i >= 0 {
				dst = dst[:i+1]
			} else {
				dst = dst[:1]
			}
			first = len(dst) == 1
		default:
			if !first {
				dst = append(dst, '/')
			}
			dst = append(dst, elem...)
			first = false
		}
	}
	if elem == "." || elem == ".." {
		dst = append(dst, '/')
	}
	if len(dst) > 1 && dst[1] == '/' {
		return string(dst[1:])
	}
	return string(dst)
}

func show(u *url.URL, err error) string {
	if err != nil {
		return "err " + fmt.Sprintf("%q", err.Error())
	}
	user := "nil"
	if u.User != nil {
		p, ok := u.User.Password()
		user = fmt.Sprintf("%q:%q:%v", u.User.Username(), p, ok)
	}
	return fmt.Sprintf("scheme=%q opaque=%q user=%s host=%q path=%q rawpath=%q omit=%v force=%v rawq=%q frag=%q rawfrag=%q\n  str=%q red=%q requri=%q epath=%q efrag=%q hn=%q port=%q q=%q",
		u.Scheme, u.Opaque, user, u.Host, u.Path, u.RawPath, u.OmitHost, u.ForceQuery, u.RawQuery, u.Fragment, u.RawFragment,
		u.String(), u.Redacted(), u.RequestURI(), u.EscapedPath(), u.EscapedFragment(), u.Hostname(), u.Port(), u.Query().Encode())
}

func res(s string, err error) string {
	if err != nil {
		return "err " + fmt.Sprintf("%q", err.Error())
	}
	return fmt.Sprintf("%q", s)
}

func main() {
	f, _ := os.Open("tests/go/testdata/url/cases.txt")
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		parts := strings.Split(sc.Text(), "|")
		op := parts[0]
		var a []string
		for _, h := range parts[1:] {
			b, _ := hex.DecodeString(h)
			a = append(a, string(b))
		}
		var r string
		switch op {
		case "parse":
			r = show(url.Parse(a[0]))
		case "request":
			r = show(url.ParseRequestURI(a[0]))
		case "unescape":
			r = res(url.QueryUnescape(a[0])) + " " + res(url.PathUnescape(a[0]))
		case "escape":
			r = fmt.Sprintf("%q %q", url.QueryEscape(a[0]), url.PathEscape(a[0]))
		case "rpath":
			r = fmt.Sprintf("%q", resolvePath(a[0], a[1]))
		case "resolve":
			b, err := url.Parse(a[0])
			if err != nil {
				r = "base err"
				break
			}
			u, err := b.Parse(a[1])
			if err != nil {
				r = res("", err)
			} else {
				r = show(u, nil)
			}
		case "query":
			v, err := url.ParseQuery(a[0])
			e := ""
			if err != nil {
				e = err.Error()
			}
			r = fmt.Sprintf("%q %q", v.Encode(), e)
		case "join":
			r = res(url.JoinPath(a[0], a[1:]...))
		case "hostport":
			u := &url.URL{Host: a[0]}
			r = fmt.Sprintf("%q %q", u.Hostname(), u.Port())
		}
		fmt.Printf("%s %q: %s\n", op, a, r)
	}
}
