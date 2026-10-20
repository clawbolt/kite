//go:build ignore

// tests/go/dnsclient.go：对拍 Go 1.27 net 包的纯 Go 解析器（dnsconfig_unix.go / dnsclient_unix.go / lookup.go）。
// 这是 net 包的内部测试（要用 dnsReadConfig、nameList、isDomainName 这些未导出函数），重新生成：
//   GO=/path/to/go sh tests/go/dnsclient_regen.sh
// 它把本文件复制成 $GOROOT/src/net/zz_kite_gen_test.go 跑 TestKiteGen，同时写出 testdata/dnsclient/responses.txt
// （假 DNS 服务按 名字|类型|应答十六进制 回答，ID 换成查询的 ID；表里没有的回 REFUSED）。
// 输出里假服务的端口写成 PORT。
package net

import (
	"context"
	"encoding/hex"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"golang.org/x/net/dns/dnsmessage"
)

type kiteResp struct {
	name  string
	typ   dnsmessage.Type
	hdr   dnsmessage.Header
	ans   []dnsmessage.Resource
	addOPT bool
	optTTL uint32
}

func kiteBuild(r kiteResp) []byte {
	b := dnsmessage.NewBuilder(nil, r.hdr)
	b.EnableCompression()
	b.StartQuestions()
	b.Question(dnsmessage.Question{Name: dnsmessage.MustNewName(r.name), Type: r.typ, Class: dnsmessage.ClassINET})
	b.StartAnswers()
	for _, a := range r.ans {
		switch body := a.Body.(type) {
		case *dnsmessage.MXResource:
			b.MXResource(a.Header, *body)
		case *dnsmessage.TXTResource:
			b.TXTResource(a.Header, *body)
		case *dnsmessage.SRVResource:
			b.SRVResource(a.Header, *body)
		case *dnsmessage.NSResource:
			b.NSResource(a.Header, *body)
		case *dnsmessage.CNAMEResource:
			b.CNAMEResource(a.Header, *body)
		case *dnsmessage.AResource:
			b.AResource(a.Header, *body)
		}
	}
	if r.addOPT {
		b.StartAdditionals()
		var h dnsmessage.ResourceHeader
		h.SetEDNS0(1232, 0, false)
		h.TTL = r.optTTL
		b.OPTResource(h, dnsmessage.OPTResource{})
	}
	out, _ := b.Finish()
	return out
}

func kiteRH(name string) dnsmessage.ResourceHeader {
	return dnsmessage.ResourceHeader{Name: dnsmessage.MustNewName(name), Class: dnsmessage.ClassINET, TTL: 60}
}

func TestKiteGen(t *testing.T) {
	ok := dnsmessage.Header{Response: true, RecursionDesired: true, RecursionAvailable: true}
	mx := func(p uint16, h string) dnsmessage.Resource {
		return dnsmessage.Resource{Header: kiteRH("mx.test."), Body: &dnsmessage.MXResource{Pref: p, MX: dnsmessage.MustNewName(h)}}
	}
	resps := []kiteResp{
		{name: "mx.test.", typ: dnsmessage.TypeMX, hdr: ok, ans: []dnsmessage.Resource{
			{Header: kiteRH("mx.test."), Body: &dnsmessage.CNAMEResource{CNAME: dnsmessage.MustNewName("mx.test.")}},
			mx(30, "c.mx.test."), mx(10, "a.mx.test."), mx(20, "b.mx.test."),
		}},
		{name: "badmx.test.", typ: dnsmessage.TypeMX, hdr: ok, ans: []dnsmessage.Resource{mx(1, "ok.test."), mx(2, "bad!name.test."), mx(3, "192.0.2.1.")}},
		{name: "ipmx.test.", typ: dnsmessage.TypeMX, hdr: ok, ans: []dnsmessage.Resource{mx(2, "192.0.2.1."), mx(1, "x.test.")}},
		{name: "txt.test.", typ: dnsmessage.TypeTXT, hdr: ok, ans: []dnsmessage.Resource{
			{Header: kiteRH("txt.test."), Body: &dnsmessage.TXTResource{TXT: []string{"v=spf1 ", "include:x -all"}}},
			{Header: kiteRH("txt.test."), Body: &dnsmessage.AResource{A: [4]byte{1, 2, 3, 4}}},
			{Header: kiteRH("txt.test."), Body: &dnsmessage.TXTResource{TXT: []string{"hello"}}},
			{Header: kiteRH("txt.test."), Body: &dnsmessage.TXTResource{TXT: []string{""}}},
		}},
		{name: "_sip._tcp.srv.test.", typ: dnsmessage.TypeSRV, hdr: ok, ans: []dnsmessage.Resource{
			{Header: kiteRH("_sip._tcp.srv.test."), Body: &dnsmessage.SRVResource{Priority: 2, Weight: 0, Port: 5062, Target: dnsmessage.MustNewName("c.srv.test.")}},
			{Header: kiteRH("_sip._tcp.srv.test."), Body: &dnsmessage.SRVResource{Priority: 1, Weight: 0, Port: 5060, Target: dnsmessage.MustNewName("a.srv.test.")}},
			{Header: kiteRH("_sip._tcp.srv.test."), Body: &dnsmessage.SRVResource{Priority: 3, Weight: 0, Port: 5063, Target: dnsmessage.MustNewName("d.srv.test.")}},
		}},
		{name: "plain.srv.test.", typ: dnsmessage.TypeSRV, hdr: ok, ans: []dnsmessage.Resource{
			{Header: kiteRH("Plain.SRV.test."), Body: &dnsmessage.SRVResource{Priority: 0, Weight: 0, Port: 1, Target: dnsmessage.MustNewName("bad_target!.")}},
		}},
		{name: "ns.test.", typ: dnsmessage.TypeNS, hdr: ok, ans: []dnsmessage.Resource{
			{Header: kiteRH("ns.test."), Body: &dnsmessage.NSResource{NS: dnsmessage.MustNewName("ns1.test.")}},
			{Header: kiteRH("ns.test."), Body: &dnsmessage.NSResource{NS: dnsmessage.MustNewName("ns2.test.")}},
		}},
		{name: "nx.test.", typ: dnsmessage.TypeMX, hdr: dnsmessage.Header{Response: true, RecursionAvailable: true, RCode: dnsmessage.RCodeNameError}},
		{name: "fail.test.", typ: dnsmessage.TypeMX, hdr: dnsmessage.Header{Response: true, RecursionAvailable: true, RCode: dnsmessage.RCodeServerFailure}},
		{name: "lame.test.", typ: dnsmessage.TypeMX, hdr: dnsmessage.Header{Response: true}},
		{name: "empty.test.", typ: dnsmessage.TypeMX, hdr: ok},
		{name: "ext.test.", typ: dnsmessage.TypeMX, hdr: ok, addOPT: true, optTTL: 1 << 24},
		{name: "extnx.test.", typ: dnsmessage.TypeMX, hdr: dnsmessage.Header{Response: true, RCode: 3}, addOPT: true, optTTL: 0},
		{name: "host.search.test.", typ: dnsmessage.TypeTXT, hdr: ok, ans: []dnsmessage.Resource{
			{Header: kiteRH("host.search.test."), Body: &dnsmessage.TXTResource{TXT: []string{"found via search"}}},
		}},
		{name: "a.b.", typ: dnsmessage.TypeTXT, hdr: ok, ans: []dnsmessage.Resource{
			{Header: kiteRH("a.b."), Body: &dnsmessage.TXTResource{TXT: []string{"absolute first"}}},
		}},
	}
	var lines []string
	for _, r := range resps {
		lines = append(lines, fmt.Sprintf("%s|%d|%s", r.name, r.typ, hex.EncodeToString(kiteBuild(r))))
	}
	out := os.Getenv("KITE_RESP")
	os.WriteFile(out, []byte(strings.Join(lines, "\n")+"\n"), 0o644)
	table := map[string][]byte{}
	for _, r := range resps {
		table[r.name+"|"+r.typ.String()] = kiteBuild(r)
	}

	pc, err := ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer pc.Close()
	go func() {
		buf := make([]byte, 2048)
		for {
			n, addr, err := pc.ReadFrom(buf)
			if err != nil {
				return
			}
			q := append([]byte(nil), buf[:n]...)
			var p dnsmessage.Parser
			if _, err := p.Start(q); err != nil {
				continue
			}
			qq, err := p.Question()
			if err != nil {
				continue
			}
			resp, ok := table[strings.ToLower(qq.Name.String())+"|"+qq.Type.String()]
			if ok {
				resp = append([]byte(nil), resp...)
				resp[0], resp[1] = q[0], q[1]
			} else {
				resp = append([]byte(nil), q...)
				resp[2], resp[3] = 0x81, 0x85
			}
			pc.WriteTo(resp, addr)
		}
	}()
	server := pc.LocalAddr().String()
	port := server[strings.LastIndex(server, ":")+1:]
	norm := func(s string) string { return strings.ReplaceAll(s, ":"+port, ":PORT") }

	resolvConf.initOnce.Do(resolvConf.init)
	old := resolvConf.dnsConfig.Load()
	defer resolvConf.dnsConfig.Store(old)
	resolvConf.dnsConfig.Store(&dnsConfig{servers: []string{server}, search: []string{"search.test."}, ndots: 1, timeout: 2 * time.Second, attempts: 1, noReload: true})
	r := &Resolver{PreferGo: true}
	ctx := context.Background()
	pr := func(what string, v any, err error) {
		s := ""
		if err != nil {
			s = "err " + err.Error()
		} else {
			s = fmt.Sprintf("ok %v", v)
		}
		fmt.Println(what, norm(s))
	}
	fmtMX := func(ms []*MX) string {
		var p []string
		for _, m := range ms {
			p = append(p, fmt.Sprintf("%s/%d", m.Host, m.Pref))
		}
		return "[" + strings.Join(p, " ") + "]"
	}
	for _, n := range []string{"mx.test", "mx.test.", "MX.Test.", "badmx.test.", "ipmx.test.", "nx.test.", "fail.test.", "lame.test.", "empty.test.",
		"ext.test.", "extnx.test.", "txt.test.", "bad..name", "nothere.test.", "x.onion.", ""} {
		ms, err := r.LookupMX(ctx, n)
		pr("mx "+n, fmtMX(ms), err)
	}
	for _, n := range []string{"txt.test.", "host", "a.b", "a.b.", "nothere", "mx.test."} {
		ts, err := r.LookupTXT(ctx, n)
		pr("txt "+n, fmt.Sprintf("%q", ts), err)
	}
	for _, c := range [][3]string{{"sip", "tcp", "srv.test."}, {"", "", "plain.srv.test."}, {"xmpp", "tcp", "srv.test."}} {
		cname, srvs, err := r.LookupSRV(ctx, c[0], c[1], c[2])
		var p []string
		for _, s := range srvs {
			p = append(p, fmt.Sprintf("%s:%d/%d/%d", s.Target, s.Port, s.Priority, s.Weight))
		}
		pr("srv "+strings.Join(c[:], ","), cname+" ["+strings.Join(p, " ")+"]", err)
	}
	for _, n := range []string{"ns.test.", "nx.test."} {
		ns, err := r.LookupNS(ctx, n)
		var p []string
		for _, x := range ns {
			p = append(p, x.Host)
		}
		pr("ns "+n, "["+strings.Join(p, " ")+"]", err)
	}

	// resolv.conf 解析（主机名固定为 host.domain.local，同 Go 的 TestDNSReadConfig）
	origGetHostname := getHostname
	defer func() { getHostname = origGetHostname }()
	getHostname = func() (string, error) { return "host.domain.local", nil }
	files := []string{"domain-resolv.conf", "empty-resolv.conf", "freebsd-usevc-resolv.conf", "invalid-ndots-resolv.conf",
		"large-ndots-resolv.conf", "linux-use-vc-resolv.conf", "negative-ndots-resolv.conf", "openbsd-resolv.conf",
		"openbsd-tcp-resolv.conf", "resolv.conf", "search-resolv.conf", "search-single-dot-resolv.conf",
		"single-request-reopen-resolv.conf", "single-request-resolv.conf"}
	for _, f := range files {
		c := dnsReadConfig("testdata/" + f)
		fmt.Printf("conf %s servers=%q search=%q ndots=%d timeout=%d attempts=%d rotate=%v single=%v tcp=%v ad=%v noreload=%v unknown=%v lookup=%q\n",
			f, c.servers, c.search, c.ndots, c.timeout.Milliseconds(), c.attempts, c.rotate, c.singleRequest, c.useTCP, c.trustAD, c.noReload, c.unknownOpt, c.lookup)
		for _, n := range []string{"foo", "foo.bar", "foo.bar.", "a.b.c.d.e", "x.onion", "x.onion.", strings.Repeat("a", 253), strings.Repeat("a", 254), strings.Repeat("a", 253) + "."} {
			fmt.Printf("  names %d %q\n", len(n), c.nameList(n))
		}
	}
	for _, n := range []string{".", "", "a", "1", "123.456", "a-b.c", "-a.b", "a-.b", "a..b", "a.b.", "a_b", "x!y", strings.Repeat("a", 63) + ".b", strings.Repeat("a", 64) + ".b",
		strings.Repeat("a.", 126) + "b", strings.Repeat("a.", 127), strings.Repeat("a.", 126) + "bc", "a.-b", "0.0", "a.1", "é.com"} {
		fmt.Printf("domain %q %v\n", n, isDomainName(n))
	}
}
