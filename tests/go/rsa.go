//go:debug cryptocustomrand=1
//go:debug rsa1024min=1

package main

import (
	"crypto"
	"crypto/rsa"
	"crypto/sha1"
	"crypto/sha256"
	"crypto/sha512"
	"encoding/hex"
	"fmt"
	"math/big"
)

type constReader struct{}

func (constReader) Read(b []byte) (int, error) {
	for i := range b {
		b[i] = 0x5a
	}
	return len(b), nil
}

func es(err error) string {
	if err == nil {
		return "ok"
	}
	return err.Error()
}

func h(b []byte) string { return hex.EncodeToString(b) }

func hx(s string) *big.Int {
	x, _ := new(big.Int).SetString(s, 16)
	return x
}

func key(ps, qs string, e int) (*rsa.PrivateKey, error) {
	p, q := hx(ps), hx(qs)
	n := new(big.Int).Mul(p, q)
	phi := new(big.Int).Mul(new(big.Int).Sub(p, big.NewInt(1)), new(big.Int).Sub(q, big.NewInt(1)))
	d := new(big.Int).ModInverse(big.NewInt(int64(e)), phi)
	k := &rsa.PrivateKey{PublicKey: rsa.PublicKey{N: n, E: e}, D: d, Primes: []*big.Int{p, q}}
	k.Precompute()
	return k, k.Validate()
}

const (
	p1 = "ea5b4ad269640369c2c2819e0ed1ab8192485b83645cf3e3f1de511010b81e03035b5f45485ca704c2cfc61145b4ca60622f58dcb6fabeda56acce02ff9a7099"
	q1 = "d1db38d976d002756561be61d561fbbb30de54da7aff34205c315b421a29f29af6bb40e4909b18930aa18e1fbb1f7fb48ca98d381a46f53bfc4a44ae38e84203"
	p2 = "ec477e46f37eefdf573d2cb3e286ab9dd7c2061174ecd7c316ff86f6241e838f903921327455762dde282a9974a13f282144175f1896269492d7c9a7dff14dcb029d1659ef4e33ce01abc79b34f3342b7920cff21ba6f0a2392a9dcf47409e24901a726a9fdd5c7bb6c83208f154cdb63f122ab5c293d8484c39e52f9ff90e5f"
	q2 = "e1e2cfc9ca036a666d9b6c85460b27faf2f01c4215967e9985df47a5d329fe13448ed6b854044495b5d21e1c74755fb792ab132b7f0a31db270b1e6516986f8aa15e64c7d134822ac65ebc95424202adc7ae0ec375ba15db4911a490a3caa7c6b7db5f2e4725cddbdfa8691d6f249c9e378c6f89df787645bb779c531f6ecc65"
	p3 = "cff7022d6bb066c7d892c3a8e63386544917f04fecf8a27d34569671d85ca3a1"
	q3 = "c2cc2ba6ab8c5f1c139c3e1bbf3e4f52fb67f1bc4f1f1c83ccdfc1df605b9b7d"
)

func main() {
	r := constReader{}
	k1, err := key(p1, q1, 65537)
	fmt.Println("K1", es(err), h(k1.Precomputed.Dp.Bytes()), h(k1.Precomputed.Qinv.Bytes()))
	pub := &k1.PublicKey
	d := sha256.Sum256([]byte("hello"))
	s1, err := rsa.SignPKCS1v15(nil, k1, crypto.SHA256, d[:])
	fmt.Println("S1", h(s1), es(err))
	fmt.Println("V1", es(rsa.VerifyPKCS1v15(pub, crypto.SHA256, d[:], s1)))
	d2 := d
	d2[0] ^= 1
	fmt.Println("V1b", es(rsa.VerifyPKCS1v15(pub, crypto.SHA256, d2[:], s1)))
	fmt.Println("V1c", es(rsa.VerifyPKCS1v15(pub, crypto.SHA256, d[:], s1[1:])))
	fmt.Println("V1d", es(rsa.VerifyPKCS1v15(pub, crypto.SHA256, d[:10], s1)))
	fmt.Println("V1e", es(rsa.VerifyPKCS1v15(pub, crypto.SHA512, d[:], s1)))
	ff := make([]byte, 128)
	for i := range ff {
		ff[i] = 0xff
	}
	fmt.Println("V1f", es(rsa.VerifyPKCS1v15(pub, crypto.SHA256, d[:], ff)))
	raw := []byte("raw message, no hash")
	s2, err := rsa.SignPKCS1v15(nil, k1, 0, raw)
	fmt.Println("S2", h(s2), es(err))
	fmt.Println("V2", es(rsa.VerifyPKCS1v15(pub, 0, raw, s2)))
	_, err = rsa.SignPKCS1v15(nil, k1, 0, make([]byte, 120))
	fmt.Println("S2b", es(err))
	ds := sha1.Sum([]byte("pss"))
	s3, err := rsa.SignPSS(r, k1, crypto.SHA1, ds[:], nil)
	fmt.Println("S3", h(s3), es(err))
	fmt.Println("V3", es(rsa.VerifyPSS(pub, crypto.SHA1, ds[:], s3, nil)), es(rsa.VerifyPSS(pub, crypto.SHA1, ds[:], s3, &rsa.PSSOptions{SaltLength: 20})))
	d5 := sha512.Sum384([]byte("pss2"))
	s4, err := rsa.SignPSS(r, k1, crypto.SHA384, d5[:], &rsa.PSSOptions{SaltLength: rsa.PSSSaltLengthEqualsHash})
	fmt.Println("S4", h(s4), es(err))
	fmt.Println("V4", es(rsa.VerifyPSS(pub, crypto.SHA384, d5[:], s4, &rsa.PSSOptions{SaltLength: rsa.PSSSaltLengthEqualsHash})), es(rsa.VerifyPSS(pub, crypto.SHA384, d5[:], s4, nil)), es(rsa.VerifyPSS(pub, crypto.SHA384, d5[:], s4, &rsa.PSSOptions{SaltLength: 10})))
	_, err = rsa.SignPSS(r, k1, crypto.SHA1, ds[:], &rsa.PSSOptions{SaltLength: -2})
	fmt.Println("S5", es(err))
	_, err = rsa.SignPSS(r, k1, crypto.SHA512, d[:], nil)
	fmt.Println("S6", es(err))
	// OAEP
	c1, err := rsa.EncryptOAEP(sha256.New(), r, pub, []byte("secret"), []byte("label"))
	fmt.Println("E1", h(c1), es(err))
	m1, err := rsa.DecryptOAEP(sha256.New(), nil, k1, c1, []byte("label"))
	fmt.Println("D1", string(m1), es(err))
	_, err = rsa.DecryptOAEP(sha256.New(), nil, k1, c1, []byte("lab"))
	fmt.Println("D1b", es(err))
	_, err = rsa.EncryptOAEP(sha256.New(), r, pub, make([]byte, 63), nil)
	fmt.Println("E1b", es(err))
	_, err = rsa.DecryptOAEP(sha256.New(), nil, k1, ff, nil)
	fmt.Println("D1c", es(err))
	// v1.5 加密
	c2, err := rsa.EncryptPKCS1v15(r, pub, []byte("v15 message"))
	fmt.Println("E2", h(c2), es(err))
	m2, err := rsa.DecryptPKCS1v15(nil, k1, c2)
	fmt.Println("D2", string(m2), es(err))
	_, err = rsa.EncryptPKCS1v15(r, pub, make([]byte, 118))
	fmt.Println("E2b", es(err))
	// 2048 位：只做公钥运算
	k2, err := key(p2, q2, 3)
	fmt.Println("K2", es(err), k2.N.BitLen())
	c3, err := rsa.EncryptOAEP(sha1.New(), r, &k2.PublicKey, []byte("x"), nil)
	fmt.Println("E3", h(c3), es(err))
	c4, err := rsa.EncryptPKCS1v15(r, &k2.PublicKey, nil)
	fmt.Println("E4", h(c4), es(err))
	fmt.Println("V5", es(rsa.VerifyPKCS1v15(&k2.PublicKey, crypto.SHA256, d[:], s1)))
	// 太小的密钥
	k3, err := key(p3, q3, 65537)
	fmt.Println("K3", es(err))
	_, err = rsa.SignPKCS1v15(nil, k3, crypto.SHA256, d[:])
	fmt.Println("S7", es(err))
	fmt.Println("V6", es(rsa.VerifyPSS(&k3.PublicKey, crypto.SHA256, d[:], s1, nil)))
	// 不一致的密钥
	for i, f := range []func(k *rsa.PrivateKey){
		func(k *rsa.PrivateKey) { k.D = new(big.Int).Add(k.D, big.NewInt(2)) },
		func(k *rsa.PrivateKey) { k.N = new(big.Int).Add(k.N, big.NewInt(2)) },
		func(k *rsa.PrivateKey) { k.E = 4 },
		func(k *rsa.PrivateKey) { k.E = 1 },
	} {
		p, q := hx(p1), hx(q1)
		n := new(big.Int).Mul(p, q)
		phi := new(big.Int).Mul(new(big.Int).Sub(p, big.NewInt(1)), new(big.Int).Sub(q, big.NewInt(1)))
		dd := new(big.Int).ModInverse(big.NewInt(65537), phi)
		k := &rsa.PrivateKey{PublicKey: rsa.PublicKey{N: n, E: 65537}, D: dd, Primes: []*big.Int{p, q}}
		f(k)
		k.Precompute()
		fmt.Println("BAD", i, es(k.Validate()))
	}
}
