package main

import (
	"crypto"
	"crypto/ecdh"
	"crypto/ecdsa"
	"crypto/ed25519"
	"crypto/mldsa"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/asn1"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"math/big"
	"net"
	"net/url"
	"os"
	"time"
)

func must[T any](v T, err error) T {
	if err != nil {
		panic(err)
	}
	return v
}

func loadKeys() []any {
	data := must(os.ReadFile("tests/go/testdata/x509/keys.pem"))
	var out []any
	for {
		var b *pem.Block
		b, data = pem.Decode(data)
		if b == nil {
			return out
		}
		switch b.Type {
		case "RSA PRIVATE KEY":
			out = append(out, must(x509.ParsePKCS1PrivateKey(b.Bytes)))
		case "EC PRIVATE KEY":
			out = append(out, must(x509.ParseECPrivateKey(b.Bytes)))
		default:
			out = append(out, must(x509.ParsePKCS8PrivateKey(b.Bytes)))
		}
	}
}

func pub(k any) any {
	switch k := k.(type) {
	case *ecdh.PrivateKey:
		return k.PublicKey()
	default:
		return k.(crypto.Signer).Public()
	}
}

// 确定性签名（RSA PKCS#1 v1.5、Ed25519）打印整份 DER，否则只打印 TBS
func deterministic(priv any, algo x509.SignatureAlgorithm) bool {
	switch priv.(type) {
	case *rsa.PrivateKey:
		return algo != x509.SHA256WithRSAPSS && algo != x509.SHA384WithRSAPSS && algo != x509.SHA512WithRSAPSS
	case ed25519.PrivateKey:
		return true
	}
	return false
}

var nb = time.Date(2021, 3, 4, 5, 6, 7, 0, time.UTC)
var na = time.Date(2031, 3, 4, 5, 6, 7, 0, time.UTC)

func oid(xs ...uint64) x509.OID { return must(x509.OIDFromInts(xs)) }

func cert(name string, t *x509.Certificate, parent *x509.Certificate, pk any, priv any) *x509.Certificate {
	if parent == nil {
		parent = t
	}
	der, err := x509.CreateCertificate(rand.Reader, t, parent, pk, priv)
	if err != nil {
		fmt.Printf("cert %s: ERR %s\n", name, err)
		return nil
	}
	c, err := x509.ParseCertificate(der)
	if err != nil {
		fmt.Printf("cert %s: PARSE ERR %s\n", name, err)
		return nil
	}
	if deterministic(priv, t.SignatureAlgorithm) {
		fmt.Printf("cert %s: der %s\n", name, hex.EncodeToString(der))
	} else {
		fmt.Printf("cert %s: tbs %s\n", name, hex.EncodeToString(c.RawTBSCertificate))
	}
	p := parent
	if parent == t {
		p = c
	}
	fmt.Printf("  algo %s check %v\n", c.SignatureAlgorithm, c.CheckSignatureFrom(p))
	return c
}

func csr(name string, t *x509.CertificateRequest, priv any) {
	der, err := x509.CreateCertificateRequest(rand.Reader, t, priv)
	if err != nil {
		fmt.Printf("csr %s: ERR %s\n", name, err)
		return
	}
	c := must(x509.ParseCertificateRequest(der))
	if deterministic(priv, t.SignatureAlgorithm) {
		fmt.Printf("csr %s: der %s\n", name, hex.EncodeToString(der))
	} else {
		fmt.Printf("csr %s: tbs %s\n", name, hex.EncodeToString(c.RawTBSCertificateRequest))
	}
	fmt.Printf("  algo %s check %v\n", c.SignatureAlgorithm, c.CheckSignature())
}

func crl(name string, t *x509.RevocationList, issuer *x509.Certificate, priv any) {
	der, err := x509.CreateRevocationList(rand.Reader, t, issuer, priv.(crypto.Signer))
	if err != nil {
		fmt.Printf("crl %s: ERR %s\n", name, err)
		return
	}
	c := must(x509.ParseRevocationList(der))
	if deterministic(priv, t.SignatureAlgorithm) {
		fmt.Printf("crl %s: der %s\n", name, hex.EncodeToString(der))
	} else {
		fmt.Printf("crl %s: tbs %s\n", name, hex.EncodeToString(c.RawTBSRevocationList))
	}
	fmt.Printf("  algo %s check %v\n", c.SignatureAlgorithm, c.CheckSignatureFrom(issuer))
}

func main() {
	ks := loadKeys()
	rsaK, rsa2 := ks[0], ks[1]
	p224, p256, p384, p521 := ks[2], ks[3], ks[4], ks[5]
	edK, xK, mlK := ks[11], ks[12], ks[13]
	for i, k := range []any{p224, p256, p384, p521} {
		fmt.Println("curve", i, k.(*ecdsa.PrivateKey).Curve.Params().Name)
	}
	_ = mldsa.MLDSA44

	full := pkix.Name{Country: []string{"US", "CA"}, Organization: []string{"Kite Create"}, OrganizationalUnit: []string{"Unit"},
		Locality: []string{"Town"}, Province: []string{"State"}, StreetAddress: []string{"1 Road"}, PostalCode: []string{"12345"},
		SerialNumber: "42", CommonName: "Create Root",
		ExtraNames: []pkix.AttributeTypeAndValue{{Type: asn1.ObjectIdentifier{1, 2, 840, 113549, 1, 9, 1}, Value: "root@example.com"}}}
	root := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: full, NotBefore: nb, NotAfter: na, IsCA: true,
		BasicConstraintsValid: true, MaxPathLen: 2, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageCRLSign | x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageOCSPSigning}, UnknownExtKeyUsage: []asn1.ObjectIdentifier{{1, 2, 3, 4}},
		OCSPServer: []string{"http://ocsp.example.com"}, IssuingCertificateURL: []string{"http://ca.example.com/ca.crt", "ldap://x"},
		CRLDistributionPoints: []string{"http://crl.example.com/a.crl", "http://crl2.example.com/b.crl"},
		Policies:              []x509.OID{oid(2, 23, 140, 1, 2, 1), oid(1, 3, 6, 1, 4, 1, 99999, 1)},
		PermittedDNSDomainsCritical: true, PermittedDNSDomains: []string{"example.com", ".example.org"}, ExcludedDNSDomains: []string{"bad.example.com"},
		PermittedIPRanges: []*net.IPNet{{IP: net.IP{192, 168, 0, 0}, Mask: net.CIDRMask(16, 32)}, {IP: net.ParseIP("2001:db8::"), Mask: net.CIDRMask(32, 128)}},
		ExcludedIPRanges:  []*net.IPNet{{IP: net.IP{192, 168, 1, 7}, Mask: net.CIDRMask(24, 32)}},
		PermittedEmailAddresses: []string{"example.com"}, ExcludedEmailAddresses: []string{"x@example.com"},
		PermittedURIDomains: []string{".example.com"}, ExcludedURIDomains: []string{"evil.example.com"},
		ExtraExtensions: []pkix.Extension{{Id: asn1.ObjectIdentifier{1, 2, 3, 4, 5}, Critical: false, Value: []byte{4, 2, 0xab, 0xcd}}}}
	rc := cert("root", root, nil, pub(rsaK), rsaK)

	u := must(url.Parse("https://www.example.com/a?b=c"))
	leaf := &x509.Certificate{SerialNumber: big.NewInt(1000), Subject: pkix.Name{CommonName: "leaf", Organization: []string{"Ünïcode Org"}},
		NotBefore: nb, NotAfter: na, KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		DNSNames: []string{"www.example.com", "*.example.com"}, EmailAddresses: []string{"a@example.com"},
		IPAddresses: []net.IP{net.IP{10, 0, 0, 1}, net.ParseIP("2001:db8::1"), net.ParseIP("192.0.2.9")}, URIs: []*url.URL{u},
		SignatureAlgorithm: x509.SHA384WithRSA}
	cert("leaf", leaf, rc, pub(p256), rsaK)
	empty := &x509.Certificate{SerialNumber: new(big.Int).Lsh(big.NewInt(1), 150), NotBefore: nb, NotAfter: na, DNSNames: []string{"empty.example.com"},
		SignatureAlgorithm: x509.SHA512WithRSA, BasicConstraintsValid: true, MaxPathLen: -1}
	cert("empty-subject", empty, rc, pub(rsa2), rsaK)
	sha1c := &x509.Certificate{SerialNumber: big.NewInt(0), Subject: pkix.Name{CommonName: "sha1"}, NotBefore: time.Date(1949, 12, 31, 23, 59, 59, 0, time.UTC),
		NotAfter: time.Date(2050, 1, 1, 0, 0, 0, 0, time.FixedZone("x", 3600)), SignatureAlgorithm: x509.SHA1WithRSA, SubjectKeyId: []byte{1, 2, 3},
		AuthorityKeyId: []byte{9, 9}}
	cert("sha1-times", sha1c, rc, pub(rsa2), rsaK)
	self := &x509.Certificate{SerialNumber: big.NewInt(77), Subject: pkix.Name{CommonName: "self"}, NotBefore: nb, NotAfter: na,
		AuthorityKeyId: []byte{5, 5, 5}, RawSubject: []byte{0x30, 0x0f, 0x31, 0x0d, 0x30, 0x0b, 0x06, 0x03, 0x55, 0x04, 0x03, 0x0c, 0x04, 0x72, 0x61, 0x77, 0x21}}
	cert("raw-subject", self, nil, pub(rsaK), rsaK)
	edRoot := &x509.Certificate{SerialNumber: big.NewInt(5), Subject: pkix.Name{CommonName: "Ed Root"}, NotBefore: nb, NotAfter: na, IsCA: true,
		BasicConstraintsValid: true, MaxPathLenZero: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageCRLSign}
	ec := cert("ed-root", edRoot, nil, pub(edK), edK)
	cert("ed-leaf-mldsa", &x509.Certificate{SerialNumber: big.NewInt(6), Subject: pkix.Name{CommonName: "pq"}, NotBefore: nb, NotAfter: na}, ec, pub(mlK), edK)
	cert("ed-leaf-x25519", &x509.Certificate{SerialNumber: big.NewInt(7), NotBefore: nb, NotAfter: na}, ec, pub(xK), edK)
	pss := &x509.Certificate{SerialNumber: big.NewInt(8), Subject: pkix.Name{CommonName: "pss"}, NotBefore: nb, NotAfter: na, SignatureAlgorithm: x509.SHA256WithRSAPSS}
	cert("pss256", pss, rc, pub(p384), rsaK)
	pss.SignatureAlgorithm = x509.SHA512WithRSAPSS
	cert("pss512", pss, rc, pub(p384), rsaK)
	for i, k := range []any{p224, p256, p384, p521} {
		t := &x509.Certificate{SerialNumber: big.NewInt(int64(20 + i)), Subject: pkix.Name{CommonName: fmt.Sprintf("ec %d", i)}, NotBefore: nb, NotAfter: na,
			IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
		cert(fmt.Sprintf("ec%d", i), t, nil, pub(k), k)
	}
	cert("ecdsa-sha512", &x509.Certificate{SerialNumber: big.NewInt(30), NotBefore: nb, NotAfter: na, SignatureAlgorithm: x509.ECDSAWithSHA512, Subject: pkix.Name{CommonName: "e"}}, nil, pub(p256), p256)
	cert("mldsa", &x509.Certificate{SerialNumber: big.NewInt(31), Subject: pkix.Name{CommonName: "ml"}, NotBefore: nb, NotAfter: na, IsCA: true, BasicConstraintsValid: true}, nil, pub(mlK), mlK)
	// 错误
	bad := func(name string, mod func(t *x509.Certificate), pk, priv any, parent *x509.Certificate) {
		t := &x509.Certificate{SerialNumber: big.NewInt(99), Subject: pkix.Name{CommonName: "bad"}, NotBefore: nb, NotAfter: na}
		mod(t)
		cert(name, t, parent, pk, priv)
	}
	bad("neg-serial", func(t *x509.Certificate) { t.SerialNumber = big.NewInt(-5) }, pub(rsaK), rsaK, nil)
	bad("maxpath-2", func(t *x509.Certificate) { t.BasicConstraintsValid = true; t.IsCA = true; t.MaxPathLen = -2 }, pub(rsaK), rsaK, nil)
	bad("nonca-maxpath", func(t *x509.Certificate) { t.BasicConstraintsValid = true; t.MaxPathLen = 1 }, pub(rsaK), rsaK, nil)
	bad("nonca-zero", func(t *x509.Certificate) { t.BasicConstraintsValid = true; t.MaxPathLenZero = true }, pub(rsaK), rsaK, nil)
	bad("algo-mismatch", func(t *x509.Certificate) { t.SignatureAlgorithm = x509.ECDSAWithSHA256 }, pub(rsaK), rsaK, nil)
	bad("md5", func(t *x509.Certificate) { t.SignatureAlgorithm = x509.MD5WithRSA }, pub(rsaK), rsaK, nil)
	bad("unknown-algo", func(t *x509.Certificate) { t.SignatureAlgorithm = 100 }, pub(rsaK), rsaK, nil)
	bad("dsa-algo", func(t *x509.Certificate) { t.SignatureAlgorithm = x509.DSAWithSHA256 }, pub(rsaK), rsaK, nil)
	bad("mldsa-params", func(t *x509.Certificate) { t.SignatureAlgorithm = x509.MLDSA65 }, pub(mlK), mlK, nil)
	bad("x25519-signer", func(t *x509.Certificate) {}, pub(xK), xK, nil)
	bad("wrong-parent-key", func(t *x509.Certificate) {}, pub(p256), rsa2, rc)
	bad("ia5", func(t *x509.Certificate) { t.DNSNames = []string{"é.example.com"} }, pub(rsaK), rsaK, nil)
	bad("ia5-email", func(t *x509.Certificate) { t.EmailAddresses = []string{"ü@x"} }, pub(rsaK), rsaK, nil)
	bad("ia5-constraint", func(t *x509.Certificate) { t.ExcludedDNSDomains = []string{"ö.com"} }, pub(rsaK), rsaK, nil)
	bad("unknown-eku", func(t *x509.Certificate) { t.ExtKeyUsage = []x509.ExtKeyUsage{99} }, pub(rsaK), rsaK, nil)
	bad("empty-policy", func(t *x509.Certificate) { t.Policies = []x509.OID{{}} }, pub(rsaK), rsaK, nil)
	bad("mapped-v6mask", func(t *x509.Certificate) {
		t.PermittedIPRanges = []*net.IPNet{{IP: net.ParseIP("::ffff:10.0.0.0"), Mask: net.CIDRMask(104, 128)}}
	}, pub(rsaK), rsaK, nil)
	bad("v4-in-16", func(t *x509.Certificate) {
		t.PermittedIPRanges = []*net.IPNet{{IP: net.ParseIP("10.0.0.0"), Mask: net.CIDRMask(8, 32)}}
	}, pub(rsaK), rsaK, nil)
	bad("extra-overrides", func(t *x509.Certificate) {
		t.KeyUsage = x509.KeyUsageDigitalSignature | x509.KeyUsageDecipherOnly
		t.DNSNames = []string{"x.example.com"}
		t.ExtraExtensions = []pkix.Extension{{Id: asn1.ObjectIdentifier{2, 5, 29, 17}, Critical: true, Value: []byte{0x30, 0}}}
	}, pub(rsaK), rsaK, nil)
	bad("ku-encipher", func(t *x509.Certificate) { t.KeyUsage = x509.KeyUsageEncipherOnly | x509.KeyUsageKeyAgreement }, pub(rsaK), rsaK, nil)

	// CSR
	csr("csr-rsa", &x509.CertificateRequest{Subject: full, DNSNames: []string{"a.example.com"}, IPAddresses: []net.IP{net.IP{1, 2, 3, 4}},
		ExtraExtensions: []pkix.Extension{{Id: asn1.ObjectIdentifier{1, 2, 3}, Critical: true, Value: []byte{5, 0}}}}, rsaK)
	csr("csr-attrs", &x509.CertificateRequest{Subject: pkix.Name{CommonName: "attrs"}, EmailAddresses: []string{"e@example.com"},
		Attributes: []pkix.AttributeTypeAndValueSET{
			{Type: asn1.ObjectIdentifier{1, 2, 840, 113549, 1, 9, 7}, Value: [][]pkix.AttributeTypeAndValue{{{Type: asn1.ObjectIdentifier{1, 2, 840, 113549, 1, 9, 7}, Value: "secret"}}}},
			{Type: asn1.ObjectIdentifier{1, 2, 840, 113549, 1, 9, 14}, Value: [][]pkix.AttributeTypeAndValue{{{Type: asn1.ObjectIdentifier{2, 5, 29, 15}, Value: []byte{3, 2, 7, 0x80}}, {Type: asn1.ObjectIdentifier{2, 5, 29, 17}, Value: []byte{0x30, 0}}}}},
		}, SignatureAlgorithm: x509.SHA256WithRSA}, rsa2)
	csr("csr-plain-attr", &x509.CertificateRequest{Subject: pkix.Name{CommonName: "plain"},
		Attributes: []pkix.AttributeTypeAndValueSET{{Type: asn1.ObjectIdentifier{1, 2, 840, 113549, 1, 9, 7}, Value: [][]pkix.AttributeTypeAndValue{{{Type: asn1.ObjectIdentifier{1, 2, 840, 113549, 1, 9, 7}, Value: "pw"}}}}}}, edK)
	csr("csr-ed", &x509.CertificateRequest{RawSubject: []byte{0x30, 0}, URIs: []*url.URL{u}}, edK)
	csr("csr-ec", &x509.CertificateRequest{Subject: pkix.Name{CommonName: "ec csr"}, SignatureAlgorithm: x509.ECDSAWithSHA384}, p521)
	csr("csr-pss", &x509.CertificateRequest{Subject: pkix.Name{CommonName: "pss csr"}, SignatureAlgorithm: x509.SHA384WithRSAPSS}, rsaK)
	csr("csr-ml", &x509.CertificateRequest{Subject: pkix.Name{CommonName: "ml csr"}}, mlK)
	csr("csr-x25519", &x509.CertificateRequest{Subject: pkix.Name{CommonName: "x"}}, xK)
	csr("csr-mismatch", &x509.CertificateRequest{Subject: pkix.Name{CommonName: "x"}, SignatureAlgorithm: x509.PureEd25519}, rsaK)

	// CRL
	t1 := time.Date(2024, 5, 6, 7, 8, 9, 0, time.UTC)
	t2 := time.Date(2024, 6, 6, 7, 8, 9, 0, time.FixedZone("y", -7*3600))
	entries := []x509.RevocationListEntry{
		{SerialNumber: big.NewInt(1000), RevocationTime: t1, ReasonCode: 1},
		{SerialNumber: big.NewInt(1001), RevocationTime: t1.Add(time.Hour), ExtraExtensions: []pkix.Extension{{Id: asn1.ObjectIdentifier{2, 5, 29, 24}, Value: []byte{0x18, 0x0f, '2', '0', '2', '4', '0', '1', '0', '1', '0', '0', '0', '0', '0', '0', 'Z'}}}},
		{SerialNumber: new(big.Int).Lsh(big.NewInt(1), 100), RevocationTime: time.Date(2060, 1, 1, 0, 0, 0, 0, time.UTC)},
	}
	crl("crl-rsa", &x509.RevocationList{Number: big.NewInt(12), ThisUpdate: t1, NextUpdate: t2, RevokedCertificateEntries: entries,
		ExtraExtensions: []pkix.Extension{{Id: asn1.ObjectIdentifier{1, 2, 3, 9}, Value: []byte{5, 0}}}}, rc, rsaK)
	crl("crl-empty", &x509.RevocationList{Number: big.NewInt(0), ThisUpdate: t1, NextUpdate: t2, SignatureAlgorithm: x509.SHA512WithRSA}, rc, rsaK)
	crl("crl-old", &x509.RevocationList{Number: big.NewInt(3), ThisUpdate: t1, NextUpdate: t2,
		RevokedCertificates: []pkix.RevokedCertificate{{SerialNumber: big.NewInt(5), RevocationTime: t2}}}, rc, rsaK)
	crl("crl-ed", &x509.RevocationList{Number: big.NewInt(255), ThisUpdate: t1, NextUpdate: t2, RevokedCertificateEntries: entries[:1]}, ec, edK)
	crl("crl-noCRLSign", &x509.RevocationList{Number: big.NewInt(1), ThisUpdate: t1, NextUpdate: t2}, &x509.Certificate{KeyUsage: x509.KeyUsageCertSign, SubjectKeyId: []byte{1}}, rsaK)
	crl("crl-noSKID", &x509.RevocationList{Number: big.NewInt(1), ThisUpdate: t1, NextUpdate: t2}, &x509.Certificate{KeyUsage: x509.KeyUsageCRLSign}, rsaK)
	crl("crl-order", &x509.RevocationList{Number: big.NewInt(1), ThisUpdate: t2, NextUpdate: t1}, rc, rsaK)
	crl("crl-nonumber", &x509.RevocationList{ThisUpdate: t1, NextUpdate: t2}, rc, rsaK)
	crl("crl-bignum", &x509.RevocationList{Number: new(big.Int).Lsh(big.NewInt(1), 159), ThisUpdate: t1, NextUpdate: t2}, rc, rsaK)
	crl("crl-bignum-ok", &x509.RevocationList{Number: new(big.Int).Lsh(big.NewInt(1), 158), ThisUpdate: t1, NextUpdate: t2}, rc, rsaK)
	crl("crl-reason-extra", &x509.RevocationList{Number: big.NewInt(1), ThisUpdate: t1, NextUpdate: t2, RevokedCertificateEntries: []x509.RevocationListEntry{{SerialNumber: big.NewInt(1), RevocationTime: t1,
		ExtraExtensions: []pkix.Extension{{Id: asn1.ObjectIdentifier{2, 5, 29, 21}, Value: []byte{10, 1, 1}}}}}}, rc, rsaK)
	crl("crl-zero-time", &x509.RevocationList{Number: big.NewInt(1), ThisUpdate: t1, NextUpdate: t2, RevokedCertificateEntries: []x509.RevocationListEntry{{SerialNumber: big.NewInt(1)}}}, rc, rsaK)
	crl("crl-ec", &x509.RevocationList{Number: big.NewInt(1), ThisUpdate: t1, NextUpdate: t2}, rc, p256)

	old, err := rc.CreateCRL(rand.Reader, rsaK, []pkix.RevokedCertificate{{SerialNumber: big.NewInt(9), RevocationTime: t2}}, t1, t2)
	fmt.Printf("createcrl %x %v\n", old, err)
	old, err = rc.CreateCRL(rand.Reader, rsaK, nil, t1, t2)
	fmt.Printf("createcrl-empty %x %v\n", old, err)
}
