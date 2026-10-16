package main

import (
	"fmt"
	"net/netip"
	"strings"
)

func L(b []byte) string {
	s := []string{}
	for _, x := range b {
		s = append(s, fmt.Sprint(x))
	}
	return "[" + strings.Join(s, ", ") + "]"
}

func B(b bool) string {
	if b {
		return "true"
	}
	return "false"
}

func props(a netip.Addr) string {
	return strings.Join([]string{B(a.IsValid()), fmt.Sprint(a.BitLen()), B(a.Is4()), B(a.Is6()), B(a.Is4In6()), a.Zone(),
		B(a.IsLoopback()), B(a.IsPrivate()), B(a.IsMulticast()), B(a.IsLinkLocalUnicast()), B(a.IsGlobalUnicast()),
		B(a.IsUnspecified()), B(a.IsLinkLocalMulticast()), B(a.IsInterfaceLocalMulticast())}, " ")
}

func as16(a netip.Addr) []byte { b := a.As16(); return b[:] }

func main() {
	addrs := []string{"0.0.0.0", "127.0.0.1", "10.1.2.3", "172.16.0.1", "172.32.0.1", "192.168.1.1", "169.254.1.1",
		"224.0.0.1", "239.1.2.3", "255.255.255.255", "8.8.8.8", "1.2.3.4",
		"::", "::1", "fe80::1", "fe80::1%eth0", "ff02::1", "ff01::1", "ff05::2", "fc00::1", "fd12:3456::1",
		"2001:db8::68", "2001:DB8:0:0:8:800:200C:417A", "::ffff:1.2.3.4", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
		"::ffff:169.254.3.3", "::ffff:224.0.0.5", "::ffff:0.0.0.0", "::ffff:255.255.255.255%z", "1:2:3:4:5:6:7:8",
		"1:0:0:4:0:0:0:8", "1:0:0:4:5:0:0:8", "0:0:1::", "::2:3:4:5:6:7:8", "1:2:3:4:5:6::", "::1.2.3.4",
		"1:2:3:4:5:6:1.2.3.4", "::ffff:c000:280", "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff", "2001:0db8:85a3:0000:0000:8a2e:0370:7334",
		"fe80::1cc0:3e8c:119f:c2e1%ens18", "fe80::%25", "::%x y",
		// invalid
		"", "1", "1.2.3", "1.2.3.4.5", "01.2.3.4", "1.2.3.256", "1..2.3", ".1.2.3", "1.2.3.", "1.2.3.a", "1.2.3.4%eth0",
		"%eth0", "abc", "::1%", "12345::1", "1:2:3:4:5:6:7:8:9", "1:2:3:4:5:6:7", "1::2::3", ":1:2::", "1:2:3:",
		"1:2:3:4:5:6:7:8::", "::1.2.3", "1:2:3:4:5:6:7:1.2.3.4", "1:2:1.2.3.4", "1:2:3:4:5:6:7:8%", "fe80::1%",
		"g::1", "1:2:3:4:5:6:7:8x", "[::1]", "::ffff:1.2.3.04", "1.2.3.4 ", "1:2:3:4:5::6:7:8", "::1.2.3.4.5",
		"::ffff:1.2.3.4:5", "1.2.3.4\x00", "日本.1.2.3", "fe80::1%\t", "1:2::3:4:5:6:7:8:9", "0x1.2.3.4"}
	for _, s := range addrs {
		a, err := netip.ParseAddr(s)
		if err != nil {
			fmt.Println("ERR", err.Error())
			continue
		}
		fmt.Println(a.String(), a.StringExpanded(), props(a), L(a.AsSlice()), L(as16(a)))
		u := a.Unmap()
		fmt.Println("  unmap", u.String(), "next", a.Next().String(), "prev", a.Prev().String(), "wz", a.WithZone("lo0").String(), a.WithZone("").String())
		if a.Is4() || a.Is4In6() {
			b4 := a.As4()
			fmt.Println("  as4", L(b4[:]))
		}
		for _, bits := range []int{-1, 0, 8, 17, 24, 32, 33, 64, 127, 128, 129} {
			p, err := a.Prefix(bits)
			if err != nil {
				fmt.Println("  prefix", bits, "ERR", err.Error())
			} else {
				fmt.Println("  prefix", bits, p.String(), p.Bits(), B(p.IsValid()), B(p.IsSingleIP()))
			}
		}
	}
	z := netip.Addr{}
	fmt.Println(z.String(), z.StringExpanded(), props(z), L(z.AsSlice()), z.Next().String(), z.Prev().String())
	fmt.Println(netip.IPv6LinkLocalAllNodes(), netip.IPv6LinkLocalAllRouters(), netip.IPv6Loopback(), netip.IPv6Unspecified(), netip.IPv4Unspecified())
	fmt.Println(netip.AddrFrom4([4]byte{192, 0, 2, 1}), netip.AddrFrom16([16]byte{0x20, 0x01, 0x0d, 0xb8, 15: 1}), netip.AddrFrom16([16]byte{10: 0xff, 11: 0xff, 12: 9, 13: 8, 14: 7, 15: 6}))
	if a, ok := netip.AddrFromSlice([]byte{1, 2, 3, 4}); ok {
		fmt.Println("slice", a)
	}
	_, ok := netip.AddrFromSlice([]byte{1, 2, 3})
	fmt.Println("slice3", B(ok))
	// edge next/prev
	for _, s := range []string{"255.255.255.255", "0.0.0.0", "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff", "::", "::ffff:255.255.255.255", "1.2.3.255", "::ff:ffff", "fe80::ffff%eth1"} {
		a := netip.MustParseAddr(s)
		fmt.Println("np", s, a.Next().String(), a.Prev().String())
	}
	// compare
	cs := []string{"1.2.3.4", "1.2.3.5", "::1", "::ffff:1.2.3.4", "fe80::1", "fe80::1%a", "fe80::1%b", "0.0.0.0", "::"}
	for _, x := range cs {
		row := []string{}
		for _, y := range cs {
			a, b := netip.MustParseAddr(x), netip.MustParseAddr(y)
			row = append(row, fmt.Sprint(a.Compare(b)), B(a.Less(b)))
		}
		fmt.Println("cmp", x, strings.Join(row, " "))
	}
	fmt.Println("cmp0", z.Compare(netip.MustParseAddr("0.0.0.0")), netip.MustParseAddr("::").Compare(z))
	// AddrPort
	aps := []string{"1.2.3.4:80", "[::1]:8080", "[fe80::1%eth0]:443", "[::ffff:1.2.3.4]:1", "0.0.0.0:0", "1.2.3.4:65535",
		"1.2.3.4:65536", "1.2.3.4", "1.2.3.4:", ":80", "[::1]80", "[::1:80", "::1:80", "[1.2.3.4]:80", "1.2.3.4:-1", "1.2.3.4:0x10",
		"[]:80", "[:80", "1.2.3.4:+5", "a:b", "1.2.3.400:80", "[fe80::1%]:80", "[::1]:007", "1.2.3.4:080"}
	for _, s := range aps {
		ap, err := netip.ParseAddrPort(s)
		if err != nil {
			fmt.Println("AP ERR", err.Error())
			continue
		}
		fmt.Println("AP", ap.String(), ap.Addr().String(), ap.Port(), B(ap.IsValid()))
	}
	fmt.Println(netip.AddrPortFrom(netip.MustParseAddr("::ffff:1.1.1.1%x"), 9).String(), netip.AddrPort{}.String(), B(netip.AddrPort{}.IsValid()))
	a1, a2 := netip.MustParseAddrPort("1.2.3.4:80"), netip.MustParseAddrPort("1.2.3.4:81")
	fmt.Println("apcmp", a1.Compare(a2), a2.Compare(a1), a1.Compare(a1))
	// Prefix
	pfx := []string{"1.2.3.4/24", "1.2.3.4/32", "1.2.3.4/0", "10.0.0.0/8", "::1/128", "2001:db8::/32", "2001:db8::1/48",
		"::ffff:1.2.3.4/120", "::ffff:1.2.3.4/96", "fe80::1/10", "0.0.0.0/0", "::/0", "1.2.3.4/33", "::1/129", "1.2.3.4",
		"1.2.3.4/", "1.2.3.4/-1", "1.2.3.4/+1", "1.2.3.4/01", "1.2.3.4/0x1", "1.2.3.4/a", "fe80::1%eth0/64", "1.2.3/24",
		"/24", "1.2.3.4/24/", "1.2.3.4/99999999999999999999", "1.2.3.4/1000", "1.2.3.4/ 5", "::1/00", "172.16.5.4/12", "1.2.3.4/7"}
	for _, s := range pfx {
		p, err := netip.ParsePrefix(s)
		if err != nil {
			fmt.Println("PF ERR", err.Error())
			continue
		}
		fmt.Println("PF", p.String(), p.Addr().String(), p.Bits(), B(p.IsValid()), B(p.IsSingleIP()), p.Masked().String())
	}
	tests := []string{"1.2.3.4", "1.2.4.1", "1.3.0.0", "10.200.1.1", "11.0.0.1", "::1", "2001:db8::5", "2001:db9::", "::ffff:1.2.3.4", "fe80::1%eth0", "fe80::1", "0.0.0.0"}
	for _, s := range pfx[:12] {
		p := netip.MustParsePrefix(s)
		row := []string{}
		for _, t := range tests {
			row = append(row, B(p.Contains(netip.MustParseAddr(t))))
		}
		fmt.Println("contains", s, strings.Join(row, " "))
		row = []string{}
		for _, t := range pfx[:12] {
			row = append(row, B(p.Overlaps(netip.MustParsePrefix(t))))
		}
		fmt.Println("overlaps", s, strings.Join(row, " "))
	}
	fmt.Println(netip.PrefixFrom(netip.MustParseAddr("1.2.3.4"), 33).String(), netip.PrefixFrom(netip.MustParseAddr("fe80::1%x"), 64).String(),
		netip.Prefix{}.String(), B(netip.Prefix{}.Contains(netip.MustParseAddr("1.2.3.4"))), B(netip.Prefix{}.Overlaps(netip.MustParsePrefix("::/0"))),
		netip.Prefix{}.Masked().String())
}
