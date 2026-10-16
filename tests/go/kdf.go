package main

import (
	"crypto/hkdf"
	"crypto/pbkdf2"
	"crypto/sha1"
	"crypto/sha256"
	"crypto/sha3"
	"crypto/sha512"
	"encoding/hex"
	"fmt"
)

func H(b []byte) string { return hex.EncodeToString(b) }

func main() {
	msgs := [][]byte{{}, []byte("abc"), []byte("The quick brown fox jumps over the lazy dog")}
	var big []byte
	for i := 0; i < 500; i++ { big = append(big, byte(i*7)) }
	msgs = append(msgs, big)
	for _, m := range msgs {
		a, b, c, d := sha3.Sum224(m), sha3.Sum256(m), sha3.Sum384(m), sha3.Sum512(m)
		fmt.Println(len(m), H(a[:]), H(b[:]), H(c[:]), H(d[:]))
		fmt.Println(H(sha3.SumSHAKE128(m, 17)), H(sha3.SumSHAKE256(m, 200)))
	}
	h := sha3.New256(); h.Write([]byte("ab")); h.Write([]byte("c")); fmt.Println(H(h.Sum([]byte{9})), h.Size(), h.BlockSize())
	h.Reset(); fmt.Println(H(h.Sum(nil)))
	s := sha3.NewSHAKE128(); s.Write([]byte("x")); p := make([]byte, 100); s.Read(p[:30]); s.Read(p[30:]); fmt.Println(H(p))
	for _, ns := range [][2]string{{"", ""}, {"", "Email Signature"}, {"KMAC", "my app"}, {"N", ""}} {
		c1 := sha3.NewCSHAKE128([]byte(ns[0]), []byte(ns[1])); c1.Write([]byte{0, 1, 2, 3})
		c2 := sha3.NewCSHAKE256([]byte(ns[0]), []byte(ns[1])); c2.Write(big)
		o1, o2 := make([]byte, 32), make([]byte, 64); c1.Read(o1); c2.Read(o2)
		fmt.Println(H(o1), H(o2))
	}
	// HKDF RFC 5869 用例 1 和其他
	ikm, _ := hex.DecodeString("0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b")
	salt, _ := hex.DecodeString("000102030405060708090a0b0c")
	prk, _ := hkdf.Extract(sha256.New, ikm, salt)
	okm, _ := hkdf.Expand(sha256.New, prk, "\xf0\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9", 42)
	fmt.Println(H(prk), H(okm))
	prk2, _ := hkdf.Extract(sha1.New, ikm, nil)
	k2, _ := hkdf.Key(sha512.New, ikm, nil, "", 100)
	fmt.Println(H(prk2), H(k2))
	_, err := hkdf.Key(sha256.New, ikm, salt, "x", 255*32+1)
	k3, err3 := hkdf.Key(sha256.New, ikm, salt, "x", 255*32)
	fmt.Println(err, len(k3), err3)
	_, err = hkdf.Expand(sha1.New, prk2, "", 255*20+1)
	fmt.Println(err)
	// PBKDF2 RFC 6070
	for _, it := range []int{1, 2, 4096} {
		k, _ := pbkdf2.Key(sha1.New, "password", []byte("salt"), it, 20)
		fmt.Println(it, H(k))
	}
	k, _ := pbkdf2.Key(sha1.New, "passwordPASSWORDpassword", []byte("saltSALTsaltSALTsaltSALTsaltSALTsalt"), 50, 25)
	fmt.Println(H(k))
	k, _ = pbkdf2.Key(sha256.New, "pass\x00word", []byte("sa\x00lt"), 100, 70)
	fmt.Println(H(k))
	_, err = pbkdf2.Key(sha256.New, "p", nil, 1, 0)
	fmt.Println(err)
}
