package main

import (
	"crypto/x509"
	"encoding/pem"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"
)

func load() []*x509.Certificate {
	data, err := os.ReadFile("tests/go/testdata/x509/verify.pem")
	if err != nil {
		panic(err)
	}
	var out []*x509.Certificate
	for {
		var b *pem.Block
		b, data = pem.Decode(data)
		if b == nil {
			return out
		}
		c, err := x509.ParseCertificate(b.Bytes)
		if err != nil {
			panic(err)
		}
		out = append(out, c)
	}
}

func pool(cs []*x509.Certificate, idx ...int) *x509.CertPool {
	p := x509.NewCertPool()
	for _, i := range idx {
		p.AddCert(cs[i])
	}
	return p
}

func show(chains [][]*x509.Certificate, err error) string {
	if err != nil {
		return "ERR " + err.Error()
	}
	var parts []string
	for _, ch := range chains {
		var names []string
		for _, c := range ch {
			names = append(names, c.Subject.CommonName)
		}
		parts = append(parts, strings.Join(names, " > "))
	}
	return "OK " + strings.Join(parts, " | ")
}

func main() {
	cs := load()
	at := time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC)
	inter := pool(cs, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)
	roots := pool(cs, 0, 1)
	for i, c := range cs {
		fmt.Printf("cert %d %q\n", i, c.Subject.CommonName)
		base := x509.VerifyOptions{Roots: roots, Intermediates: inter, CurrentTime: at}
		fmt.Println("  A", show(c.Verify(base)))
		o := base
		if len(c.DNSNames) > 0 {
			o.DNSName = c.DNSNames[0]
		} else {
			o.DNSName = "www.example.com"
		}
		fmt.Println("  B", show(c.Verify(o)))
		o = base
		o.Roots = pool(cs, 0)
		fmt.Println("  C", show(c.Verify(o)))
		o = base
		o.Roots = pool(cs, 1)
		o.Intermediates = nil
		fmt.Println("  D", show(c.Verify(o)))
		o = base
		o.KeyUsages = []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}
		fmt.Println("  E", show(c.Verify(o)))
		o.KeyUsages = []x509.ExtKeyUsage{x509.ExtKeyUsageAny}
		fmt.Println("  F", show(c.Verify(o)))
		o.KeyUsages = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageCodeSigning}
		fmt.Println("  F2", show(c.Verify(o)))
		o = base
		o.CurrentTime = time.Date(2019, 6, 1, 12, 0, 0, 0, time.UTC)
		fmt.Println("  G", show(c.Verify(o)))
		o.CurrentTime = time.Date(2041, 6, 1, 12, 0, 0, 0, time.FixedZone("x", 5*3600+1800))
		fmt.Println("  G2", show(c.Verify(o)))
		o = base
		o.CertificatePolicies = []x509.OID{must(x509.OIDFromInts([]uint64{1, 2, 3, 40}))}
		fmt.Println("  H", show(c.Verify(o)))
		o.CertificatePolicies = []x509.OID{must(x509.OIDFromInts([]uint64{1, 2, 3, 77}))}
		fmt.Println("  I", show(c.Verify(o)))
		o = base
		o.Roots = x509.NewCertPool()
		fmt.Println("  J", show(c.Verify(o)))
		o = base
		rp := x509.NewCertPool()
		rp.AddCertWithConstraint(cs[0], func(chain []*x509.Certificate) error {
			if len(chain) > 1 {
				return errors.New("chain too long for root one")
			}
			return nil
		})
		rp.AddCert(cs[1])
		o.Roots = rp
		fmt.Println("  K", show(c.Verify(o)))
		o = base
		o.Roots = pool(cs, 0, 1, 3)
		fmt.Println("  L", show(c.Verify(o)))
	}
	hosts := []string{"www.example.com", "WWW.EXAMPLE.COM.", "foo.wild.example.com", "a.b.wild.example.com", "10.1.2.3",
		"[::1]", "::1", "192.0.2.1", "legacy.example.com", "", "*.example.com", "x.Example.com", "10.1.2.4",
		"fe80::1%eth0", "bad host", "nc.many.example.com", "nz.many.example.com", ".", "example.com", "*.wild.example.com",
		"::ffff:192.0.2.1", "multi.example.com."}
	for i, c := range cs[12:] {
		var rs []string
		for _, h := range hosts {
			err := c.VerifyHostname(h)
			if err == nil {
				rs = append(rs, "ok")
			} else {
				rs = append(rs, err.Error())
			}
		}
		fmt.Printf("host %d: %s\n", i+12, strings.Join(rs, " ; "))
	}
	p := x509.NewCertPool()
	data, _ := os.ReadFile("tests/go/testdata/x509/verify.pem")
	fmt.Println("append", p.AppendCertsFromPEM(data), len(p.Subjects()))
	fmt.Println("append2", p.AppendCertsFromPEM(data), len(p.Subjects()))
	fmt.Println("append3", p.AppendCertsFromPEM([]byte("junk")), len(p.Subjects()))
	fmt.Println("equal", p.Equal(pool(cs, 0)), pool(cs, 0, 1).Equal(pool(cs, 1, 0, 1)), x509.NewCertPool().Equal(x509.NewCertPool()))
}

func must[T any](v T, err error) T {
	if err != nil {
		panic(err)
	}
	return v
}
