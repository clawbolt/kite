// tests/go/net_ip.go：对拍 Go 1.27 net 的 ParseIP / IP 方法 / ParseCIDR / IPNet / IPMask / ParseMAC / SplitHostPort。
// 输入 tests/go/testdata/net/strings.txt（Go 测试里的全部字符串字面量，生成器 gen_strings.go.txt）；非 UTF-8 的跳过。
package main

import (
	"bufio"
	"encoding/hex"
	"fmt"
	"net"
	"os"
	"unicode/utf8"
)

func b2i(b bool) int {
	if b {
		return 1
	}
	return 0
}

func ipLine(ip net.IP) string {
	return fmt.Sprintf("%s to4=%s to16=%s flags=%d%d%d%d%d%d%d%d dm=%s m24=%s m64=%s",
		ip.String(), ip.To4().String(), ip.To16().String(),
		b2i(ip.IsUnspecified()), b2i(ip.IsLoopback()), b2i(ip.IsPrivate()), b2i(ip.IsMulticast()),
		b2i(ip.IsInterfaceLocalMulticast()), b2i(ip.IsLinkLocalMulticast()), b2i(ip.IsLinkLocalUnicast()),
		b2i(ip.IsGlobalUnicast()),
		ip.DefaultMask().String(), ip.Mask(net.CIDRMask(24, 32)).String(), ip.Mask(net.CIDRMask(64, 128)).String())
}

func main() {
	f, _ := os.Open("tests/go/testdata/net/strings.txt")
	sc := bufio.NewScanner(f)
	probes := []net.IP{net.ParseIP("10.1.2.3"), net.ParseIP("192.168.0.1"), net.ParseIP("2001:db8::1"), net.IPv4(10, 1, 2, 3).To4(), net.ParseIP("::ffff:10.0.0.1")}
	for sc.Scan() {
		b, _ := hex.DecodeString(sc.Text())
		s := string(b)
		if !utf8.ValidString(s) {
			continue
		}
		fmt.Printf("%q\n", s)
		if ip := net.ParseIP(s); ip != nil {
			fmt.Printf("  ip %s\n", ipLine(ip))
		}
		ip, n, err := net.ParseCIDR(s)
		if err != nil {
			fmt.Printf("  cidr err %q\n", err.Error())
		} else {
			ones, bits := n.Mask.Size()
			c := ""
			for _, p := range probes {
				c += fmt.Sprint(b2i(n.Contains(p)))
			}
			fmt.Printf("  cidr %s net=%s mask=%s size=%d/%d contains=%s\n", ip, n, n.Mask, ones, bits, c)
		}
		if hw, err := net.ParseMAC(s); err == nil {
			fmt.Printf("  mac %s\n", hw)
		} else if len(s) >= 12 {
			fmt.Printf("  mac err %q\n", err.Error())
		}
		if h, p, err := net.SplitHostPort(s); err == nil {
			fmt.Printf("  split %q %q join=%q\n", h, p, net.JoinHostPort(h, p))
		}
	}
	for _, m := range []net.IPMask{net.IPv4Mask(255, 255, 0, 0), net.IPv4Mask(255, 0, 255, 0), net.CIDRMask(0, 32), net.CIDRMask(33, 32),
		net.CIDRMask(65, 128), net.CIDRMask(5, 64), net.IPMask{255, 255, 255}, nil} {
		o, b := m.Size()
		fmt.Printf("mask %s %d/%d\n", m.String(), o, b)
	}
	for _, n := range []net.IPNet{{IP: net.IPv4(1, 2, 3, 4), Mask: net.IPv4Mask(255, 0, 255, 0)}, {IP: net.IPv4(1, 2, 3, 4).To4(), Mask: net.CIDRMask(120, 128)},
		{IP: net.ParseIP("2001::1"), Mask: net.CIDRMask(8, 32)}, {IP: net.IP{1, 2, 3}, Mask: net.CIDRMask(8, 32)}, {}} {
		fmt.Printf("net %s %d\n", n.String(), b2i(n.Contains(net.ParseIP("1.2.3.4"))))
	}
	fmt.Println(net.IP{1, 2, 3}.String(), net.IP(nil).String(), net.ParseIP("1.2.3.4").Equal(net.IPv4(1, 2, 3, 4).To4()),
		net.HardwareAddr{1, 0xab, 0xff}.String())
}
