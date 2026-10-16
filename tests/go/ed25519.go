package main

import (
	"crypto"
	"crypto/ed25519"
	"crypto/sha256"
	"crypto/sha512"
	"encoding/hex"
	"fmt"
	"strings"
)

func try(f func() string) (s string) {
	defer func() {
		if r := recover(); r != nil {
			s = fmt.Sprint("panic: ", r)
		}
	}()
	return f()
}

func es(err error) string {
	if err == nil {
		return "ok"
	}
	return err.Error()
}

func main() {
	msgs := [][]byte{nil, []byte("abc"), []byte(strings.Repeat("kite", 300))}
	var privs []ed25519.PrivateKey
	for i := 0; i < 6; i++ {
		seed := sha256.Sum256([]byte{byte(i)})
		priv := ed25519.NewKeyFromSeed(seed[:])
		privs = append(privs, priv)
		fmt.Println("KEY", hex.EncodeToString(priv))
	}
	for i, priv := range privs {
		pub := priv.Public().(ed25519.PublicKey)
		msg := msgs[i%3]
		sig := ed25519.Sign(priv, msg)
		fmt.Println("SIG", hex.EncodeToString(sig))
		fmt.Println("V", ed25519.Verify(pub, msg, sig), ed25519.Verify(pub, append([]byte{1}, msg...), sig))
		// 变异
		for _, j := range []int{0, 31, 32, 63} {
			s2 := append([]byte{}, sig...)
			s2[j] ^= 1 << uint(i%8)
			fmt.Println("M", j, ed25519.Verify(pub, msg, s2))
		}
		other := privs[(i+1)%len(privs)].Public().(ed25519.PublicKey)
		fmt.Println("W", ed25519.Verify(other, msg, sig))
	}
	// S + L：不规范的 S
	{
		priv := privs[0]
		sig := ed25519.Sign(priv, nil)
		l := []byte{0xed, 0xd3, 0xf5, 0x5c, 0x1a, 0x63, 0x12, 0x58, 0xd6, 0x9c, 0xf7, 0xa2, 0xde, 0xf9, 0xde, 0x14, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x10}
		s2 := append([]byte{}, sig...)
		c := 0
		for k := 0; k < 32; k++ {
			v := int(s2[32+k]) + int(l[k]) + c
			s2[32+k] = byte(v)
			c = v >> 8
		}
		fmt.Println("SL", hex.EncodeToString(s2), es(ed25519.VerifyWithOptions(priv.Public().(ed25519.PublicKey), nil, s2, &ed25519.Options{})))
	}
	// 选项
	ph := sha512.Sum512([]byte("message"))
	for i, o := range []*ed25519.Options{
		{Hash: crypto.SHA512},
		{Hash: crypto.SHA512, Context: "ctx"},
		{Context: "foo"},
		{Hash: crypto.SHA256},
		{Context: strings.Repeat("x", 256)},
		{Hash: crypto.SHA512, Context: strings.Repeat("y", 256)},
	} {
		priv := privs[i%len(privs)]
		pub := priv.Public().(ed25519.PublicKey)
		msg := ph[:]
		if o.Hash == 0 {
			msg = []byte("message!!!!")
		}
		sig, err := priv.Sign(nil, msg, o)
		fmt.Println("O", i, hex.EncodeToString(sig), es(err))
		if err == nil {
			fmt.Println("OV", es(ed25519.VerifyWithOptions(pub, msg, sig, o)), es(ed25519.VerifyWithOptions(pub, msg, sig, &ed25519.Options{})))
			fmt.Println("OV2", es(ed25519.VerifyWithOptions(pub, msg[:10], sig, o)))
		}
	}
	// 错误
	fmt.Println(try(func() string { return hex.EncodeToString(ed25519.NewKeyFromSeed(make([]byte, 31))) }))
	fmt.Println(try(func() string { return hex.EncodeToString(ed25519.Sign(make([]byte, 63), nil)) }))
	fmt.Println(try(func() string {
		_, err := ed25519.PrivateKey(make([]byte, 10)).Sign(nil, nil, crypto.Hash(0))
		return es(err)
	}))
	fmt.Println(try(func() string { return es(ed25519.VerifyWithOptions(make([]byte, 31), nil, nil, &ed25519.Options{})) }))
	pub0 := privs[0].Public().(ed25519.PublicKey)
	fmt.Println(es(ed25519.VerifyWithOptions(pub0, nil, make([]byte, 63), &ed25519.Options{})))
	sig0 := ed25519.Sign(privs[0], nil)
	s3 := append([]byte{}, sig0...)
	s3[63] |= 0x20
	fmt.Println(es(ed25519.VerifyWithOptions(pub0, nil, s3, &ed25519.Options{})))
	// 解不出点的公钥、非规范公钥
	for _, h := range []string{
		"0200000000000000000000000000000000000000000000000000000000000000",
		"0100000000000000000000000000000000000000000000000000000000000080",
		"edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
		"eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
		"ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
	} {
		pk, _ := hex.DecodeString(h)
		fmt.Println("PK", h, es(ed25519.VerifyWithOptions(pk, nil, sig0, &ed25519.Options{})))
	}
	// 私钥里放错的公钥：签名用私钥里的公钥
	bad := append(append([]byte{}, privs[0][:32]...), privs[1][32:]...)
	fmt.Println("BAD", hex.EncodeToString(ed25519.Sign(bad, []byte("x"))))
}
