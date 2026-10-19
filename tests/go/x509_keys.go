package main

import (
	"bytes"
	"crypto/ecdh"
	"crypto/ecdsa"
	"crypto/ed25519"
	"crypto/mldsa"
	"crypto/rsa"
	"crypto/x509"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"os"
	"strings"
)

type blk struct {
	typ string
	der []byte
}

func readPEM(path string) []blk {
	data, err := os.ReadFile(path)
	if err != nil {
		panic(err)
	}
	var out []blk
	for {
		var b *pem.Block
		b, data = pem.Decode(data)
		if b == nil {
			return out
		}
		out = append(out, blk{b.Type, b.Bytes})
	}
}

func hx(b []byte) string { return hex.EncodeToString(b) }

func pre(s string) string {
	if len(s) > 16 {
		return s[:16]
	}
	return s
}

func priv(k any) string {
	switch k := k.(type) {
	case *rsa.PrivateKey:
		return fmt.Sprintf("RSA n=%s e=%d d=%s p=%s q=%s dp=%s dq=%s qinv=%s", pre(k.N.Text(16)), k.E, pre(k.D.Text(16)),
			pre(k.Primes[0].Text(16)), pre(k.Primes[1].Text(16)), pre(k.Precomputed.Dp.Text(16)), pre(k.Precomputed.Dq.Text(16)),
			pre(k.Precomputed.Qinv.Text(16)))
	case *ecdsa.PrivateKey:
		d, _ := k.Bytes()
		q, _ := k.PublicKey.Bytes()
		return fmt.Sprintf("ECDSA %s d=%s q=%s", k.Curve.Params().Name, hx(d), pre(hx(q)))
	case ed25519.PrivateKey:
		return "Ed25519 " + hx(k)
	case *ecdh.PrivateKey:
		return fmt.Sprintf("ECDH %s %s %s", k.Curve(), hx(k.Bytes()), hx(k.PublicKey().Bytes()))
	case *mldsa.PrivateKey:
		return fmt.Sprintf("MLDSA %s %s", k.PublicKey().Parameters(), hx(k.Bytes()))
	case nil:
		return "nil"
	}
	return fmt.Sprintf("%T", k)
}

func pk(k any, err error) string {
	if err != nil {
		return "nil"
	}
	return priv(k)
}

func er(err error) string {
	if err == nil {
		return "<nil>"
	}
	return err.Error()
}

var seed uint32 = 777

func rnd() int {
	seed = (seed*1103515245 + 12345) & 0x7fffffff
	return int(seed >> 8)
}

func main() {
	var certs []*x509.Certificate
	for _, b := range readPEM("tests/go/testdata/x509/certs.pem") {
		c, err := x509.ParseCertificate(b.der)
		if err == nil {
			certs = append(certs, c)
		}
	}
	root := certs[0]
	for i, c := range certs {
		fmt.Printf("sig %d: root=%s self=%s prev=%s\n", i, er(c.CheckSignatureFrom(root)), er(c.CheckSignatureFrom(c)),
			er(c.CheckSignatureFrom(certs[(i+len(certs)-1)%len(certs)])))
		fmt.Printf("  check=%s\n", er(c.CheckSignature(c.SignatureAlgorithm, c.RawTBSCertificate, c.Signature)))
	}
	// 签名被改动
	for i, c := range certs[:12] {
		s := append([]byte{}, c.Signature...)
		s[len(s)/2] ^= 1
		fmt.Printf("badsig %d: %s\n", i, er(c.CheckSignature(c.SignatureAlgorithm, c.RawTBSCertificate, s)))
		fmt.Printf("badtbs %d: %s\n", i, er(c.CheckSignature(c.SignatureAlgorithm, c.RawTBSCertificate[1:], c.Signature)))
	}
	for i, b := range readPEM("tests/go/testdata/x509/crls.pem") {
		rl, err := x509.ParseRevocationList(b.der)
		if err != nil {
			continue
		}
		fmt.Printf("crl %d: %s %s\n", i, er(rl.CheckSignatureFrom(root)), er(rl.CheckSignatureFrom(certs[1])))
	}
	// CSR
	for i, b := range readPEM("tests/go/testdata/x509/csrs.pem") {
		r, err := x509.ParseCertificateRequest(b.der)
		if err != nil {
			fmt.Println("csr", i, err)
			continue
		}
		var attrs []string
		for _, a := range r.Attributes {
			var vs []string
			for _, s := range a.Value {
				for _, v := range s {
					vs = append(vs, fmt.Sprintf("%s=%v", v.Type, v.Value))
				}
			}
			attrs = append(attrs, a.Type.String()+":"+strings.Join(vs, ","))
		}
		var exts []string
		for _, e := range r.Extensions {
			exts = append(exts, fmt.Sprintf("%s/%t/%s", e.Id, e.Critical, hx(e.Value)))
		}
		var ips, uris []string
		for _, ip := range r.IPAddresses {
			ips = append(ips, hx(ip))
		}
		for _, u := range r.URIs {
			uris = append(uris, u.String())
		}
		fmt.Printf("csr %d: v=%d subj=%s sigalg=%s pkalg=%s attrs=%q exts=%q dns=%q email=%q ip=%q uri=%q raw=%d/%d/%d/%d/%d sig=%d check=%s\n",
			i, r.Version, r.Subject, r.SignatureAlgorithm, r.PublicKeyAlgorithm, attrs, exts, r.DNSNames, r.EmailAddresses, ips, uris,
			len(r.Raw), len(r.RawTBSCertificateRequest), len(r.RawSubjectPublicKeyInfo), len(r.RawSubject), len(r.RawSignatureAlgorithm),
			len(r.Signature), er(r.CheckSignature()))
		for k := 0; k < 15; k++ {
			m := append([]byte{}, b.der...)
			p := rnd() % len(m)
			v := rnd() % 256
			m[p] = byte(v)
			r2, err := x509.ParseCertificateRequest(m)
			if err != nil {
				fmt.Printf("  mut %d @%d=%d: %v\n", k, p, v, err)
			} else {
				fmt.Printf("  mut %d @%d=%d: ok %s %d %s\n", k, p, v, r2.Subject, len(r2.Extensions), er(r2.CheckSignature()))
			}
		}
	}
	// 私钥
	for i, b := range readPEM("tests/go/testdata/x509/keys.pem") {
		k1, e1 := x509.ParsePKCS1PrivateKey(b.der)
		k2, e2 := x509.ParseECPrivateKey(b.der)
		k8, e8 := x509.ParsePKCS8PrivateKey(b.der)
		fmt.Printf("key %d %s:\n  pkcs1=%s %s\n  ec=%s %s\n  pkcs8=%s %s\n", i, b.typ, pk(k1, e1), er(e1), pk(k2, e2), er(e2), pk(k8, e8), er(e8))
		if e1 == nil {
			fmt.Println("  re1", bytes.Equal(x509.MarshalPKCS1PrivateKey(k1), b.der))
		}
		if e2 == nil {
			out, err := x509.MarshalECPrivateKey(k2)
			fmt.Println("  re2", bytes.Equal(out, b.der), er(err))
		}
		if e8 == nil {
			out, err := x509.MarshalPKCS8PrivateKey(k8)
			fmt.Println("  re8", bytes.Equal(out, b.der), er(err))
		}
		for k := 0; k < 10; k++ {
			m := append([]byte{}, b.der...)
			p := rnd() % len(m)
			v := rnd() % 256
			m[p] = byte(v)
			var r any
			var err error
			switch b.typ {
			case "RSA PRIVATE KEY":
				r, err = x509.ParsePKCS1PrivateKey(m)
			case "EC PRIVATE KEY":
				r, err = x509.ParseECPrivateKey(m)
			default:
				r, err = x509.ParsePKCS8PrivateKey(m)
			}
			s := pk(r, err)
			if err != nil {
				s = err.Error()
			}
			fmt.Printf("  mut %d @%d=%d: %s\n", k, p, v, s)
		}
	}
	// 公钥往返
	for i, b := range readPEM("tests/go/testdata/x509/pubkeys.pem") {
		if b.typ == "RSA PUBLIC KEY" {
			k, err := x509.ParsePKCS1PublicKey(b.der)
			fmt.Println("pkcs1pub", i, er(err), err == nil && bytes.Equal(x509.MarshalPKCS1PublicKey(k), b.der))
			_, err = x509.ParsePKIXPublicKey(b.der)
			fmt.Println("  as pkix:", er(err))
			continue
		}
		k, err := x509.ParsePKIXPublicKey(b.der)
		out, err2 := x509.MarshalPKIXPublicKey(k)
		fmt.Println("pkix", i, er(err), bytes.Equal(out, b.der), er(err2))
		_, err = x509.ParsePKCS1PublicKey(b.der)
		fmt.Println("  as pkcs1:", er(err))
	}
	for _, h := range []string{"", "3000", "300602010102010", "3006020100020101", "30060201010201ff", "3008020101020500ffffffff", "300602010102010300"} {
		b, _ := hex.DecodeString(h)
		_, err := x509.ParsePKCS1PublicKey(b)
		fmt.Printf("pkcs1pub %q: %s\n", h, er(err))
	}
}
