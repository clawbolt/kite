package main

import (
	"crypto/ecdh"
	"crypto/ecdsa"
	"crypto/ed25519"
	"crypto/mldsa"
	"crypto/rsa"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"math/big"
	"os"
	"strings"
	"time"
)

func readPEM(path string) [][]byte {
	data, err := os.ReadFile(path)
	if err != nil {
		panic(err)
	}
	var out [][]byte
	for {
		var b *pem.Block
		b, data = pem.Decode(data)
		if b == nil {
			return out
		}
		out = append(out, b.Bytes)
	}
}

func tm(t time.Time) string {
	_, off := t.Zone()
	return fmt.Sprintf("%d/%d", t.Unix(), off)
}

func oids(xs []x509.OID) string {
	var s []string
	for _, o := range xs {
		s = append(s, o.String())
	}
	return "[" + strings.Join(s, " ") + "]"
}

func hx(b []byte) string { return hex.EncodeToString(b) }

func hxs(bs [][]byte) string {
	var s []string
	for _, b := range bs {
		s = append(s, hx(b))
	}
	return "[" + strings.Join(s, " ") + "]"
}

func key(k any) string {
	switch k := k.(type) {
	case nil:
		return "nil"
	case *rsa.PublicKey:
		return fmt.Sprintf("RSA n=%s e=%d", k.N.Text(16), k.E)
	case *ecdsa.PublicKey:
		b, _ := k.Bytes()
		return fmt.Sprintf("ECDSA %s %s", k.Curve.Params().Name, hx(b))
	case ed25519.PublicKey:
		return "Ed25519 " + hx(k)
	case *ecdh.PublicKey:
		return "X25519 " + hx(k.Bytes())
	case *mldsa.PublicKey:
		return fmt.Sprintf("MLDSA %s %d", k.Parameters(), len(k.Bytes()))
	}
	return fmt.Sprintf("%T", k)
}

func names(n pkix.Name) string {
	var s []string
	for _, a := range n.Names {
		s = append(s, fmt.Sprintf("%s=%T:%v", a.Type, a.Value, a.Value))
	}
	return "[" + strings.Join(s, "; ") + "]"
}

func exts(es []pkix.Extension) string {
	var s []string
	for _, e := range es {
		s = append(s, fmt.Sprintf("%s/%t/%d", e.Id, e.Critical, len(e.Value)))
	}
	return "[" + strings.Join(s, " ") + "]"
}

func dumpCert(c *x509.Certificate) {
	fmt.Printf("  version=%d serial=%s sigalg=%s pkalg=%s\n", c.Version, c.SerialNumber.Text(16), c.SignatureAlgorithm, c.PublicKeyAlgorithm)
	fmt.Printf("  subject=%s\n  issuer=%s\n", c.Subject, c.Issuer)
	fmt.Printf("  subject.names=%s\n", names(c.Subject))
	fmt.Printf("  cn=%q o=%q ou=%q c=%q l=%q st=%q street=%q pc=%q sn=%q\n", c.Subject.CommonName, c.Subject.Organization,
		c.Subject.OrganizationalUnit, c.Subject.Country, c.Subject.Locality, c.Subject.Province, c.Subject.StreetAddress,
		c.Subject.PostalCode, c.Subject.SerialNumber)
	fmt.Printf("  validity=%s..%s\n", tm(c.NotBefore), tm(c.NotAfter))
	fmt.Printf("  key=%s\n", key(c.PublicKey))
	fmt.Printf("  ku=%d eku=%v unknown=%v bc=%t ca=%t mpl=%d mplz=%t\n", c.KeyUsage, c.ExtKeyUsage, c.UnknownExtKeyUsage,
		c.BasicConstraintsValid, c.IsCA, c.MaxPathLen, c.MaxPathLenZero)
	fmt.Printf("  skid=%s akid=%s\n", hx(c.SubjectKeyId), hx(c.AuthorityKeyId))
	fmt.Printf("  ocsp=%q issuers=%q crl=%q\n", c.OCSPServer, c.IssuingCertificateURL, c.CRLDistributionPoints)
	var ips [][]byte
	for _, ip := range c.IPAddresses {
		ips = append(ips, ip)
	}
	var us []string
	for _, u := range c.URIs {
		us = append(us, u.String())
	}
	fmt.Printf("  dns=%q email=%q ip=%s uri=%q\n", c.DNSNames, c.EmailAddresses, hxs(ips), us)
	var pr, er []string
	for _, n := range c.PermittedIPRanges {
		pr = append(pr, hx(n.IP)+"/"+hx(n.Mask))
	}
	for _, n := range c.ExcludedIPRanges {
		er = append(er, hx(n.IP)+"/"+hx(n.Mask))
	}
	fmt.Printf("  nc crit=%t pdns=%q xdns=%q pip=%q xip=%q pem=%q xem=%q puri=%q xuri=%q\n", c.PermittedDNSDomainsCritical,
		c.PermittedDNSDomains, c.ExcludedDNSDomains, pr, er, c.PermittedEmailAddresses, c.ExcludedEmailAddresses,
		c.PermittedURIDomains, c.ExcludedURIDomains)
	var pm []string
	for _, m := range c.PolicyMappings {
		pm = append(pm, m.IssuerDomainPolicy.String()+">"+m.SubjectDomainPolicy.String())
	}
	fmt.Printf("  policies=%s ids=%v maps=%v iap=%d/%t ipm=%d/%t rep=%d/%t\n", oids(c.Policies), c.PolicyIdentifiers, pm,
		c.InhibitAnyPolicy, c.InhibitAnyPolicyZero, c.InhibitPolicyMapping, c.InhibitPolicyMappingZero,
		c.RequireExplicitPolicy, c.RequireExplicitPolicyZero)
	fmt.Printf("  exts=%s unhandled=%v\n", exts(c.Extensions), c.UnhandledCriticalExtensions)
	fmt.Printf("  raw=%d tbs=%d spki=%d subj=%d iss=%d sigai=%d sig=%s\n", len(c.Raw), len(c.RawTBSCertificate),
		len(c.RawSubjectPublicKeyInfo), len(c.RawSubject), len(c.RawIssuer), len(c.RawSignatureAlgorithm), hx(c.Signature[:min(8, len(c.Signature))]))
}

var seed uint32 = 12345

func rnd() int {
	seed = (seed*1103515245 + 12345) & 0x7fffffff
	return int(seed >> 8)
}

func main() {
	certs := readPEM("tests/go/testdata/x509/certs.pem")
	for i, der := range certs {
		fmt.Printf("cert %d (%d bytes)\n", i, len(der))
		c, err := x509.ParseCertificate(der)
		if err != nil {
			fmt.Println("  error:", err)
			continue
		}
		dumpCert(c)
	}
	// ParseCertificates：拼接
	all := append(append([]byte{}, certs[0]...), certs[1]...)
	cs, err := x509.ParseCertificates(all)
	fmt.Println("ParseCertificates:", len(cs), err)
	_, err = x509.ParseCertificate(all)
	fmt.Println("trailing:", err)
	// 变异
	for i, der := range certs {
		if i%3 != 0 {
			continue
		}
		for k := 0; k < 12; k++ {
			m := append([]byte{}, der...)
			p := rnd() % len(m)
			v := rnd() % 256
			m[p] = byte(v)
			c, err := x509.ParseCertificate(m)
			if err != nil {
				fmt.Printf("mut %d.%d @%d=%d: %v\n", i, k, p, v, err)
			} else {
				fmt.Printf("mut %d.%d @%d=%d: ok %s %s %d %s\n", i, k, p, v, c.SerialNumber.Text(16), c.Subject, len(c.Extensions), key(c.PublicKey)[:min(20, len(key(c.PublicKey)))])
			}
		}
		for _, l := range []int{0, 1, 2, 10, len(der) / 2, len(der) - 1} {
			_, err := x509.ParseCertificate(der[:l])
			fmt.Printf("trunc %d.%d: %v\n", i, l, err)
		}
	}
	// CRL
	for i, der := range readPEM("tests/go/testdata/x509/crls.pem") {
		rl, err := x509.ParseRevocationList(der)
		if err != nil {
			fmt.Println("crl", i, "error:", err)
			continue
		}
		num := "nil"
		if rl.Number != nil {
			num = rl.Number.Text(16)
		}
		fmt.Printf("crl %d issuer=%s sigalg=%s this=%s next=%s num=%s akid=%s exts=%s raw=%d/%d/%d/%d sig=%d\n", i, rl.Issuer,
			rl.SignatureAlgorithm, tm(rl.ThisUpdate), tm(rl.NextUpdate), num, hx(rl.AuthorityKeyId), exts(rl.Extensions),
			len(rl.Raw), len(rl.RawTBSRevocationList), len(rl.RawIssuer), len(rl.RawSignatureAlgorithm), len(rl.Signature))
		for _, e := range rl.RevokedCertificateEntries {
			fmt.Printf("  entry serial=%s time=%s reason=%d exts=%s raw=%d\n", e.SerialNumber.Text(16), tm(e.RevocationTime),
				e.ReasonCode, exts(e.Extensions), len(e.Raw))
		}
		fmt.Println("  revoked", len(rl.RevokedCertificates))
		for k := 0; k < 20; k++ {
			m := append([]byte{}, der...)
			p := rnd() % len(m)
			v := rnd() % 256
			m[p] = byte(v)
			r2, err := x509.ParseRevocationList(m)
			if err != nil {
				fmt.Printf("  mut %d @%d=%d: %v\n", k, p, v, err)
			} else {
				fmt.Printf("  mut %d @%d=%d: ok %d\n", k, p, v, len(r2.RevokedCertificateEntries))
			}
		}
	}
	// 公钥
	for i, der := range readPEM("tests/go/testdata/x509/pubkeys.pem") {
		k, err := x509.ParsePKIXPublicKey(der)
		fmt.Printf("pkix %d: %s %v\n", i, key(k), err)
	}
	for _, s := range []string{"", "3000", "300d06092a864886f70d0101010500", "3003020101", "30050603550403"} {
		b, _ := hex.DecodeString(s)
		k, err := x509.ParsePKIXPublicKey(b)
		fmt.Printf("pkix %q: %s %v\n", s, key(k), err)
	}
	// OID
	for _, s := range []string{"1.2.3", "0.39", "0.40", "1.39.5", "2.999.1", "2.18446744073709551616.1", "3.1", "1", "1.", ".1", "1..2",
		"1.2.a", "1.2.340282366920938463463374607431768211456", "2.5.29.32.0", "00.01.002"} {
		o, err := x509.ParseOID(s)
		if err != nil {
			fmt.Printf("oid %q: %v\n", s, err)
			continue
		}
		b, _ := o.MarshalBinary()
		fmt.Printf("oid %q: %s %s\n", s, o.String(), hx(b))
	}
	for _, xs := range [][]uint64{{1, 2, 3}, {2, 100, 1 << 63}, {0, 39}, {1, 40}, {3, 0}, {2}, {2, 18446744073709551615}} {
		o, err := x509.OIDFromInts(xs)
		if err != nil {
			fmt.Println("ints", xs, err)
			continue
		}
		fmt.Println("ints", xs, o.String(), o.EqualASN1OID([]int{1, 2, 3}))
	}
	for _, h := range []string{"2a03", "8001", "2a80", "", "2a8f", "883703", "ffffffffffffffffff01"} {
		b, _ := hex.DecodeString(h)
		var o x509.OID
		err := o.UnmarshalBinary(b)
		fmt.Printf("der %s: %v %s\n", h, err, o.String())
	}
	_ = big.NewInt
}
