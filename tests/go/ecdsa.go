package main

import (
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/sha1"
	"crypto/sha256"
	"crypto/sha512"
	"encoding/hex"
	"fmt"
	"math/big"
)

func es(err error) string {
	if err == nil {
		return "ok"
	}
	return err.Error()
}

func h(b []byte) string { return hex.EncodeToString(b) }

func digest(hf crypto.Hash, m []byte) []byte {
	switch hf {
	case crypto.SHA1:
		s := sha1.Sum(m)
		return s[:]
	case crypto.SHA224:
		s := sha256.Sum224(m)
		return s[:]
	case crypto.SHA256:
		s := sha256.Sum256(m)
		return s[:]
	case crypto.SHA384:
		s := sha512.Sum384(m)
		return s[:]
	}
	s := sha512.Sum512(m)
	return s[:]
}

func main() {
	curves := []elliptic.Curve{elliptic.P256(), elliptic.P384(), elliptic.P521()}
	sizes := []int{32, 48, 66}
	hashes := [][]crypto.Hash{{crypto.SHA256, crypto.SHA1, crypto.SHA512}, {crypto.SHA384, crypto.SHA224, crypto.SHA256}, {crypto.SHA512, crypto.SHA384, crypto.SHA1}}
	for ci, c := range curves {
		fmt.Println("CURVE", c.Params().Name)
		size := sizes[ci]
		n := c.Params().N
		var keys []*ecdsa.PrivateKey
		for i := 0; i < 2; i++ {
			s := sha512.Sum512([]byte{byte(ci), byte(i), 7})
			b := append(s[:], s[:]...)[:size]
			if ci == 2 {
				b[0] &= 1
			}
			k, err := ecdsa.ParseRawPrivateKey(c, b)
			fmt.Println("PRIV", h(b), es(err))
			pb, _ := k.PublicKey.Bytes()
			fmt.Println("PUB", h(pb))
			keys = append(keys, k)
		}
		for i, hf := range hashes[ci] {
			k := keys[i%2]
			d := digest(hf, []byte(fmt.Sprintf("message %d", i)))
			sig, err := k.Sign(nil, d, hf)
			fmt.Println("SIG", h(sig), es(err))
			fmt.Println("V", ecdsa.VerifyASN1(&k.PublicKey, d, sig))
			if i == 0 {
				fmt.Println("VO", ecdsa.VerifyASN1(&keys[1].PublicKey, d, sig))
				d2 := append([]byte{}, d...)
				d2[0] ^= 1
				fmt.Println("VH", ecdsa.VerifyASN1(&k.PublicKey, d2, sig))
				fmt.Println("VE", ecdsa.VerifyASN1(&k.PublicKey, nil, sig))
				// 高 S
				var inner struct{ R, S *big.Int }
				r, s := parseRS(sig)
				inner.R, inner.S = r, new(big.Int).Sub(n, s)
				hs := encode(inner.R, inner.S)
				fmt.Println("HS", h(hs), ecdsa.VerifyASN1(&k.PublicKey, d, hs))
				fmt.Println("RN", ecdsa.VerifyASN1(&k.PublicKey, d, encode(new(big.Int).Add(r, n), s)))
				fmt.Println("R0", ecdsa.VerifyASN1(&k.PublicKey, d, encode(big.NewInt(0), s)))
				fmt.Println("TR", ecdsa.VerifyASN1(&k.PublicKey, d, append(append([]byte{}, sig...), 0)))
				bad := append([]byte{}, sig...)
				bad[1]++
				fmt.Println("BL", ecdsa.VerifyASN1(&k.PublicKey, d, bad))
				nz := append([]byte{0x30, sig[1] + 1, 2, sig[3] + 1, 0}, sig[4:]...)
				fmt.Println("NZ", ecdsa.VerifyASN1(&k.PublicKey, d, nz))
			}
		}
		k := keys[0]
		_, err := k.Sign(nil, make([]byte, 32), crypto.Hash(0))
		fmt.Println("E0", es(err))
		_, err = k.Sign(nil, make([]byte, 31), crypto.SHA256)
		fmt.Println("E1", es(err))
		// 原始私钥
		nb := n.FillBytes(make([]byte, size))
		n1 := new(big.Int).Add(n, big.NewInt(1)).FillBytes(make([]byte, size))
		for _, b := range [][]byte{make([]byte, size), nb, n1, make([]byte, size-1)} {
			_, err := ecdsa.ParseRawPrivateKey(c, b)
			fmt.Println("RAW", h(b), es(err))
		}
		pb, _ := keys[1].PublicKey.Bytes()
		x := append([]byte{}, pb...)
		x[len(x)-1] ^= 1
		for _, b := range [][]byte{nil, {0}, {2, 1}, pb[:len(pb)-1], x} {
			_, err := ecdsa.ParseUncompressedPublicKey(c, b)
			fmt.Println("UPK", h(b), es(err))
		}
		p, err := ecdsa.ParseUncompressedPublicKey(c, pb)
		fmt.Println("UPK2", es(err), p.Equal(&keys[1].PublicKey))
	}
}

func parseRS(sig []byte) (*big.Int, *big.Int) {
	// SEQUENCE { INTEGER r, INTEGER s }（长度都小于 128 或用一个长度字节）
	i := 2
	if sig[1]&0x80 != 0 {
		i = 3
	}
	rl := int(sig[i+1])
	r := new(big.Int).SetBytes(sig[i+2 : i+2+rl])
	j := i + 2 + rl
	sl := int(sig[j+1])
	s := new(big.Int).SetBytes(sig[j+2 : j+2+sl])
	return r, s
}

func asn1Int(x *big.Int) []byte {
	b := x.Bytes()
	if len(b) == 0 || b[0]&0x80 != 0 {
		b = append([]byte{0}, b...)
	}
	return append([]byte{2, byte(len(b))}, b...)
}

func encode(r, s *big.Int) []byte {
	body := append(asn1Int(r), asn1Int(s)...)
	if len(body) < 128 {
		return append([]byte{0x30, byte(len(body))}, body...)
	}
	return append([]byte{0x30, 0x81, byte(len(body))}, body...)
}
