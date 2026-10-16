package main

import (
	"crypto/ecdh"
	"crypto/sha512"
	"encoding/hex"
	"fmt"
)

func es(err error) string {
	if err == nil {
		return "ok"
	}
	return err.Error()
}

func h(b []byte) string { return hex.EncodeToString(b) }

func main() {
	curves := []ecdh.Curve{ecdh.X25519(), ecdh.P256(), ecdh.P384(), ecdh.P521()}
	sizes := []int{32, 32, 48, 66}
	for ci, c := range curves {
		fmt.Println("CURVE", c)
		size := sizes[ci]
		var keys []*ecdh.PrivateKey
		for i := 0; i < 2; i++ {
			s := sha512.Sum512([]byte{byte(ci), byte(i)})
			b := append(s[:], s[:]...)[:size]
			if ci == 3 {
				b[0] &= 1
			}
			k, err := c.NewPrivateKey(b)
			fmt.Println("PRIV", h(b), es(err))
			if err == nil {
				fmt.Println("PUB", h(k.PublicKey().Bytes()))
				keys = append(keys, k)
			}
		}
		s1, e1 := keys[0].ECDH(keys[1].PublicKey())
		fmt.Println("ECDH", h(s1), es(e1))
		// 私钥边界
		for _, b := range [][]byte{make([]byte, size), make([]byte, size-1), make([]byte, size+1)} {
			_, err := c.NewPrivateKey(b)
			fmt.Println("BADPRIV", len(b), es(err))
		}
		if ci > 0 {
			// n 和 n-1
			var nb []byte
			switch ci {
			case 1:
				nb, _ = hex.DecodeString("ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551")
			case 2:
				nb, _ = hex.DecodeString("ffffffffffffffffffffffffffffffffffffffffffffffffc7634d81f4372ddf581a0db248b0a77aecec196accc52973")
			case 3:
				nb, _ = hex.DecodeString("01fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffa51868783bf2f966b7fcc0148f709a5d03bb5c9b8899c47aebb6fb71e91386409")
			}
			_, err := c.NewPrivateKey(nb)
			fmt.Println("N", es(err))
			nm1 := append([]byte{}, nb...)
			nm1[len(nm1)-1]--
			k, err := c.NewPrivateKey(nm1)
			fmt.Println("N-1", es(err))
			if err == nil {
				fmt.Println("PUB", h(k.PublicKey().Bytes()))
			}
			one := make([]byte, size)
			one[size-1] = 1
			k1, _ := c.NewPrivateKey(one)
			fmt.Println("ONE", h(k1.PublicKey().Bytes()))
			// 公钥校验
			pub := keys[1].PublicKey().Bytes()
			cases := [][]byte{nil, {0}, {4}, append([]byte{2}, pub[1:1+size]...), pub[:len(pub)-1]}
			x := append([]byte{}, pub...)
			x[len(x)-1] ^= 1
			cases = append(cases, x)
			ff := append([]byte{4}, make([]byte, 2*size)...)
			for i := 1; i <= size; i++ {
				ff[i] = 0xff
			}
			cases = append(cases, ff)
			ff2 := append([]byte{}, pub...)
			for i := size + 1; i < len(ff2); i++ {
				ff2[i] = 0xff
			}
			cases = append(cases, ff2)
			for _, b := range cases {
				_, err := c.NewPublicKey(b)
				fmt.Println("BADPUB", h(b), es(err))
			}
		} else {
			for _, s := range []string{
				"0000000000000000000000000000000000000000000000000000000000000000",
				"0100000000000000000000000000000000000000000000000000000000000000",
				"ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
				"e0eb7a7c3b41b8ae1656e3faf19fc46ada098deb9c32b1fd866205165f49b800",
				"0900000000000000000000000000000000000000000000000000000000000080",
				"eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
			} {
				b, _ := hex.DecodeString(s)
				p, err := c.NewPublicKey(b)
				fmt.Println("X", s, es(err))
				if err == nil {
					out, err := keys[0].ECDH(p)
					fmt.Println("XE", h(out), es(err))
				}
			}
			_, err := c.NewPublicKey(make([]byte, 31))
			fmt.Println("X31", es(err))
		}
	}
	// 曲线不一致
	s := sha512.Sum512(nil)
	a, _ := ecdh.X25519().NewPrivateKey(s[:32])
	b, _ := ecdh.P256().NewPrivateKey(s[:32])
	_, err := a.ECDH(b.PublicKey())
	fmt.Println("MIX", es(err))
}
