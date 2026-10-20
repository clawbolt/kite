// tests/go/dnsmessage.go：对拍 Go 1.27 golang.org/x/net/dns/dnsmessage（标准库里的 vendor 副本）。
// 一组固定的报文：Pack、Unpack、再 Pack，Builder（开 / 不开压缩）逐条构造，Parser 逐段走（混用 Header + 记录体、Skip、错用），
// 每个截断长度的 Unpack，以及按确定的伪随机序列改字节后的 Unpack / Pack；最后是 EDNS(0)、SVCB 参数和类型名。
// 标准库外的程序不能 import vendor 里的包，所以要和 $GOROOT/src/vendor/golang.org/x/net/dns/dnsmessage 的
// message.go、svcb.go（包名改成 main）放进同一个临时目录一起跑：sh tests/go/dnsmessage_regen.sh
package main

import (
	"encoding/hex"
	"fmt"
	"strings"
)

func nm(s string) Name { return MustNewName(s) }

func rh(name string, class Class, ttl uint32) ResourceHeader {
	return ResourceHeader{Name: nm(name), Class: class, TTL: ttl}
}

func fixtures() []Message {
	var opt ResourceHeader
	opt.SetEDNS0(1232, 0x1234, true)
	long := strings.Repeat("x", 255)
	seg63 := strings.Repeat("a", 63)
	seg64 := strings.Repeat("b", 64)
	name250 := strings.Repeat(seg63+".", 3) + strings.Repeat("c", 61) + "."
	name255 := strings.Repeat(seg63+".", 4)[:255]
	svcb := SVCBResource{Priority: 1, Target: nm("svc.example.com."), Params: []SVCParam{
		{Key: SVCParamALPN, Value: []byte("\x02h2\x02h3")},
		{Key: SVCParamPort, Value: []byte{1, 187}},
		{Key: SVCParamIPv4Hint, Value: []byte{192, 0, 2, 1}},
		{Key: 65000, Value: nil},
	}}
	return []Message{
		{Header: Header{ID: 1, RecursionDesired: true}, Questions: []Question{{nm("example.com."), TypeA, ClassINET}}},
		{
			Header: Header{ID: 0xbeef, Response: true, Authoritative: true, RecursionAvailable: true},
			Questions: []Question{{nm("example.com."), TypeALL, ClassINET}},
			Answers: []Resource{
				{rh("example.com.", ClassINET, 300), &AResource{[4]byte{93, 184, 216, 34}}},
				{rh("example.com.", ClassINET, 300), &NSResource{nm("ns1.example.com.")}},
				{rh("www.example.com.", ClassINET, 60), &CNAMEResource{nm("example.com.")}},
				{rh("example.com.", ClassINET, 3600), &SOAResource{NS: nm("ns1.example.com."), MBox: nm("hostmaster.example.com."), Serial: 2026102001, Refresh: 7200, Retry: 3600, Expire: 1209600, MinTTL: 4294967295}},
				{rh("34.216.184.93.in-addr.arpa.", ClassINET, 86400), &PTRResource{nm("example.com.")}},
				{rh("example.com.", ClassINET, 300), &MXResource{10, nm("mail.example.com.")}},
				{rh("example.com.", ClassINET, 300), &MXResource{20, nm("mail2.example.org.")}},
				{rh("example.com.", ClassINET, 300), &TXTResource{[]string{"v=spf1 -all", "", "a\x00b\xffc"}}},
				{rh("example.com.", ClassINET, 300), &AAAAResource{[16]byte{0x20, 0x01, 0x0d, 0xb8, 15: 1}}},
				{rh("_sip._tcp.example.com.", ClassINET, 300), &SRVResource{1, 5, 5060, nm("sip.example.com.")}},
				{rh("example.com.", ClassINET, 300), &svcb},
				{rh("example.com.", ClassINET, 300), &HTTPSResource{svcb}},
				{rh("example.com.", ClassCHAOS, 7), &UnknownResource{Type: 99, Data: []byte{1, 2, 3}}},
				{rh("example.com.", 9, 0), &UnknownResource{Type: TypeWKS, Data: nil}},
			},
			Authorities: []Resource{{rh("example.com.", ClassINET, 300), &NSResource{nm("ns2.example.net.")}}},
			Additionals: []Resource{
				{rh("ns1.example.com.", ClassINET, 300), &AResource{[4]byte{192, 0, 2, 53}}},
				{opt, &OPTResource{[]Option{{Code: 10, Data: []byte{1, 2, 3, 4, 5, 6, 7, 8}}, {Code: 12, Data: nil}}}},
			},
		},
		{Header: Header{ID: 65535, Response: true, OpCode: 5, Authoritative: true, Truncated: true, RecursionDesired: true, RecursionAvailable: true, AuthenticData: true, CheckingDisabled: true, RCode: RCodeNameError}},
		{
			Header: Header{ID: 3, OpCode: 15, RCode: 15},
			Questions: []Question{
				{nm("a.b.c."), TypeMX, ClassINET}, {nm("b.c."), TypeNS, ClassANY}, {nm("c."), 1000, 77},
				{nm("x.a.b.c."), TypeTXT, ClassINET}, {nm("."), TypeNS, ClassINET}, {nm("A.B.C."), TypeA, ClassINET},
			},
			Answers: []Resource{
				{rh("c.", ClassINET, 1), &CNAMEResource{nm("y.x.a.b.c.")}},
				{rh("b.c.", ClassINET, 1), &SRVResource{0, 0, 0, nm("a.b.c.")}},
				{rh(".", ClassINET, 1), &MXResource{0, nm(".")}},
				{rh(name250, ClassINET, 1), &PTRResource{nm(seg63 + ".b.c.")}},
			},
		},
		{
			Header: Header{ID: 4},
			Answers: []Resource{
				{rh("t.", ClassINET, 0), &TXTResource{[]string{long, "x"}}},
				{rh("t.", ClassINET, 0), &TXTResource{nil}},
				{rh("t.", ClassINET, 0), &TXTResource{[]string{""}}},
				{rh("o.", ClassINET, 0), &OPTResource{nil}},
				{rh("s.", ClassINET, 0), &SVCBResource{Priority: 0, Target: nm(".")}},
			},
		},
		{Header: Header{ID: 5}, Answers: []Resource{{rh("nil.", ClassINET, 0), nil}}},
		{Header: Header{ID: 6}, Questions: []Question{{nm("example.com"), TypeA, ClassINET}}},
		{Header: Header{ID: 7}, Questions: []Question{{nm("ok.example."), TypeA, ClassINET}, {nm(seg64 + ".example."), TypeA, ClassINET}}},
		{Header: Header{ID: 8}, Answers: []Resource{{rh("a..b.", ClassINET, 0), &AResource{}}}},
		{Header: Header{ID: 9}, Answers: []Resource{{rh("t.", ClassINET, 0), &TXTResource{[]string{"ok", long + "y"}}}}},
		{Header: Header{ID: 10}, Answers: []Resource{{rh("s.", ClassINET, 0), &SVCBResource{Target: nm("."), Params: []SVCParam{{Key: 3}, {Key: 3}}}}}},
		{Header: Header{ID: 11}, Answers: []Resource{{rh(name255, ClassINET, 0), &AResource{}}}},
		{Header: Header{ID: 12}, Answers: []Resource{{rh("m.", ClassINET, 0), &MXResource{1, nm("bad")}}}},
		{Header: Header{ID: 13}, Answers: []Resource{{rh("s.", ClassINET, 0), &SOAResource{NS: nm("ns."), MBox: nm("")}}}},
		{Header: Header{ID: 14}, Answers: []Resource{{rh("s.", ClassINET, 0), &SRVResource{Target: nm("t")}}}},
		{Header: Header{ID: 15}, Answers: []Resource{{rh("s.", ClassINET, 0), &HTTPSResource{SVCBResource{Target: nm("u")}}}}},
		{Header: Header{ID: 16}, Questions: []Question{{nm("q.example."), TypeA, ClassINET}}, Answers: []Resource{{rh("bad", ClassINET, 0), &AResource{}}}},
	}
}

func errs(err error) string { return "err: " + err.Error() }

func build(m Message, compress bool) {
	b := NewBuilder(nil, m.Header)
	if compress {
		b.EnableCompression()
	}
	out := func(what string, err error) {
		if err != nil {
			fmt.Printf("  %s %s\n", what, errs(err))
		}
	}
	out("question-early", b.Question(Question{nm("early."), TypeA, ClassINET}))
	out("start-q", b.StartQuestions())
	for _, q := range m.Questions {
		out("q", b.Question(q))
	}
	secs := [][]Resource{m.Answers, m.Authorities, m.Additionals}
	starts := []func() error{b.StartAnswers, b.StartAuthorities, b.StartAdditionals}
	for i, rs := range secs {
		out("start", starts[i]())
		for _, r := range rs {
			var err error
			switch body := r.Body.(type) {
			case *AResource:
				err = b.AResource(r.Header, *body)
			case *NSResource:
				err = b.NSResource(r.Header, *body)
			case *CNAMEResource:
				err = b.CNAMEResource(r.Header, *body)
			case *SOAResource:
				err = b.SOAResource(r.Header, *body)
			case *PTRResource:
				err = b.PTRResource(r.Header, *body)
			case *MXResource:
				err = b.MXResource(r.Header, *body)
			case *TXTResource:
				err = b.TXTResource(r.Header, *body)
			case *AAAAResource:
				err = b.AAAAResource(r.Header, *body)
			case *SRVResource:
				err = b.SRVResource(r.Header, *body)
			case *SVCBResource:
				err = b.SVCBResource(r.Header, *body)
			case *HTTPSResource:
				err = b.HTTPSResource(r.Header, *body)
			case *OPTResource:
				err = b.OPTResource(r.Header, *body)
			case *UnknownResource:
				err = b.UnknownResource(r.Header, *body)
			default:
				continue
			}
			out("r", err)
		}
	}
	out("start-q-late", b.StartQuestions())
	msg, err := b.Finish()
	if err != nil {
		fmt.Printf("  finish %s\n", errs(err))
	} else {
		fmt.Printf("  finish %s\n", hex.EncodeToString(msg))
	}
	out("after-finish", b.StartAdditionals())
	out("r-after-finish", b.AResource(rh("z.", ClassINET, 0), AResource{}))
}

func typed(p *Parser, t Type) string {
	var s string
	var err error
	switch t {
	case TypeA:
		var r AResource
		r, err = p.AResource()
		s = r.GoString()
	case TypeNS:
		var r NSResource
		r, err = p.NSResource()
		s = r.GoString()
	case TypeCNAME:
		var r CNAMEResource
		r, err = p.CNAMEResource()
		s = r.GoString()
	case TypeSOA:
		var r SOAResource
		r, err = p.SOAResource()
		s = r.GoString()
	case TypePTR:
		var r PTRResource
		r, err = p.PTRResource()
		s = r.GoString()
	case TypeMX:
		var r MXResource
		r, err = p.MXResource()
		s = r.GoString()
	case TypeTXT:
		var r TXTResource
		r, err = p.TXTResource()
		s = r.GoString()
	case TypeAAAA:
		var r AAAAResource
		r, err = p.AAAAResource()
		s = r.GoString()
	case TypeSRV:
		var r SRVResource
		r, err = p.SRVResource()
		s = r.GoString()
	case TypeSVCB:
		var r SVCBResource
		r, err = p.SVCBResource()
		s = r.GoString()
	case TypeHTTPS:
		var r HTTPSResource
		r, err = p.HTTPSResource()
		s = r.GoString()
	case TypeOPT:
		var r OPTResource
		r, err = p.OPTResource()
		s = r.GoString()
	default:
		var r UnknownResource
		r, err = p.UnknownResource()
		s = r.GoString()
	}
	if err != nil {
		return errs(err)
	}
	return s
}

func walk(msg []byte) {
	var p Parser
	pr := func(what, s string) { fmt.Printf("  %s %s\n", what, s) }
	if _, err := p.Answer(); err != nil {
		pr("answer-before-start", errs(err))
	}
	h, err := p.Start(msg)
	if err != nil {
		pr("start", errs(err))
		return
	}
	pr("start", h.GoString())
	if _, err := p.AnswerHeader(); err != nil {
		pr("answer-header-early", errs(err))
	}
	for i := 0; ; i++ {
		var err error
		if i%2 == 0 {
			var q Question
			q, err = p.Question()
			if err == nil {
				pr("q", q.GoString())
			}
		} else {
			err = p.SkipQuestion()
			if err == nil {
				pr("skip-q", "ok")
			}
		}
		if err != nil {
			pr("q-end", errs(err))
			break
		}
	}
	for sec := 0; sec < 3; sec++ {
		hdr := []func() (ResourceHeader, error){p.AnswerHeader, p.AuthorityHeader, p.AdditionalHeader}[sec]
		res := []func() (Resource, error){p.Answer, p.Authority, p.Additional}[sec]
		skip := []func() error{p.SkipAnswer, p.SkipAuthority, p.SkipAdditional}[sec]
		for i := 0; ; i++ {
			var err error
			switch i % 4 {
			case 0:
				var h ResourceHeader
				h, err = hdr()
				if err == nil {
					pr("hdr", h.GoString())
					pr("wrong-body", typed(&p, h.Type+1000))
					pr("body", typed(&p, h.Type))
					pr("body-again", typed(&p, h.Type))
				}
			case 1:
				var r Resource
				r, err = res()
				if err == nil {
					pr("res", r.GoString())
				}
			case 2:
				var h ResourceHeader
				h, err = hdr()
				if err == nil {
					pr("hdr", h.GoString())
					err = skip()
					if err == nil {
						pr("skip-after-hdr", "ok")
					}
				}
			case 3:
				err = skip()
				if err == nil {
					pr("skip", "ok")
				}
			}
			if err != nil {
				pr("sec-end", errs(err))
				break
			}
		}
	}
	if _, err := p.Question(); err != nil {
		pr("q-late", errs(err))
	}
	if err := p.SkipAllAnswers(); err != nil {
		pr("skip-all-late", errs(err))
	}
}

func unpackPack(b []byte) string {
	var m Message
	if err := m.Unpack(b); err != nil {
		return errs(err)
	}
	s := m.GoString()
	out, err := m.Pack()
	if err != nil {
		return s + " | pack " + errs(err)
	}
	return s + " | pack " + hex.EncodeToString(out)
}

func main() {
	seed := uint64(12345)
	rnd := func(n int) int {
		seed = (seed*1103515245 + 12345) % 2147483648
		return int(seed % uint64(n))
	}
	for i, m := range fixtures() {
		fmt.Printf("fixture %d\n", i)
		b, err := m.Pack()
		if err != nil {
			fmt.Printf("  pack %s\n", errs(err))
		} else {
			fmt.Printf("  pack %s\n", hex.EncodeToString(b))
		}
		ab, err := m.AppendPack([]byte{0xaa, 0xbb})
		if err != nil {
			fmt.Printf("  append-pack %s\n", errs(err))
		} else {
			fmt.Printf("  append-pack %s\n", hex.EncodeToString(ab))
		}
		build(m, false)
		build(m, true)
		if err != nil {
			continue
		}
		fmt.Printf("  unpack %s\n", unpackPack(b))
		walk(b)
		for l := 0; l < len(b); l++ {
			var m2 Message
			if err := m2.Unpack(b[:l]); err != nil {
				fmt.Printf("  trunc %d %s\n", l, errs(err))
			} else {
				fmt.Printf("  trunc %d ok\n", l)
			}
		}
		for k := 0; k < 40; k++ {
			c := append([]byte(nil), b...)
			n := 1 + rnd(3)
			var desc []string
			for j := 0; j < n; j++ {
				pos := rnd(len(c))
				v := rnd(256)
				c[pos] = byte(v)
				desc = append(desc, fmt.Sprintf("%d=%d", pos, v))
			}
			fmt.Printf("  mut %s %s\n", strings.Join(desc, ","), unpackPack(c))
		}
	}
	// 压缩指针的各种形状
	ptrs := []string{
		"0000000000010000000000000000c00c00010001",
		"000000000001000000000000c00c00010001",
		"000000000001000000000000c00000010001",
		"0000000000010000000000000161c00c00010001",
		"000000000001000000000000c0",
		"00000000000100000000000080000001",
		"00000000000100000000000040000001",
		"000000000001000000000000026162",
		"000000000001000000000000022e2e0000010001",
		"0000000000010000000000003f" + strings.Repeat("61", 63) + "3f" + strings.Repeat("62", 63) + "3f" + strings.Repeat("63", 63) + "3f" + strings.Repeat("64", 63) + "0000010001",
		"0000000000010000000000003f" + strings.Repeat("61", 63) + "3f" + strings.Repeat("62", 63) + "3f" + strings.Repeat("63", 63) + "3d" + strings.Repeat("64", 61) + "0000010001",
		"000000000001000000000000c00e0000c00c0001",
		"000000000000000100000000000010000100000000000501ff0000",
		"000000000000000100000000000010000100000000000301ff",
		"000000000000000100000000000041000100000000000a0001000000000000000000",
		"000000000000000100000000000041000100000000000a0001000000010001000000",
		"000000000000000100000000000040000100000000000b00010000010000000000",
		"000000000000000100000000000040000100000000000500010000000002",
		"000000000000000100000000000029000100000000000a000a0004010203",
		"00000000000000010000000000002900010000000000080001000201020003",
		"000000000000000100000000000006000100000000000300",
		"00000000000000010000000000000f000100000000000300",
	}
	for _, h := range ptrs {
		b, _ := hex.DecodeString(h)
		fmt.Printf("raw %s %s\n", h, unpackPack(b))
	}
	var e ResourceHeader
	for _, c := range [][3]int{{512, 0, 0}, {4096, 0xfff, 1}, {70000, 16, 1}, {0, 0x10000 + 0x20, 0}} {
		e.SetEDNS0(c[0], RCode(c[1]), c[2] == 1)
		fmt.Printf("edns %s %v %d %d\n", e.GoString(), e.DNSSECAllowed(), e.ExtendedRCode(3), e.ExtendedRCode(0))
	}
	e.TTL = 0x01010000
	fmt.Printf("edns-ver %v %d\n", e.DNSSECAllowed(), e.ExtendedRCode(3))
	s := SVCBResource{Target: nm(".")}
	s.SetParam(5, []byte{5})
	s.SetParam(1, []byte{1})
	s.SetParam(9, nil)
	s.SetParam(5, []byte{55})
	v, ok := s.GetParam(5)
	fmt.Printf("svcb %s %v %v\n", s.GoString(), v, ok)
	v, ok = s.GetParam(4)
	fmt.Printf("svcb-get4 %v %v\n", v, ok)
	fmt.Printf("svcb-del %v %v %s\n", s.DeleteParam(1), s.DeleteParam(2), s.GoString())
	for _, t := range []int{0, 1, 2, 5, 6, 11, 12, 13, 14, 15, 16, 28, 33, 41, 64, 65, 252, 255, 256, 65535} {
		fmt.Printf("type %d %s %s %s %s %s %s %s\n", t, Type(t).String(), Type(t).GoString(), Class(t).String(), Class(t).GoString(),
			RCode(t).String(), RCode(t).GoString(), SVCParamKey(t).GoString())
	}
	for _, k := range []int{0, 3, 9, 10} {
		fmt.Printf("key %s\n", SVCParamKey(k).String())
	}
	_, err := NewName(strings.Repeat("a", 256))
	fmt.Printf("newname-256 %v\n", err)
	n, err := NewName(strings.Repeat("a", 255))
	fmt.Printf("newname-255 %d %v %q\n", len(n.String()), err, MustNewName("a\x00.b").String())
}
