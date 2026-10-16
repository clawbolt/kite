package main

import (
	"crypto/cipher"
	"crypto/des"
	"crypto/rc4"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
)

func h(b []byte) string { return hex.EncodeToString(b) }

func main() {
	for i := 0; i < 12; i++ {
		s := sha256.Sum256([]byte{byte(i)})
		t := sha256.Sum256(s[:])
		c, _ := des.NewCipher(s[:8])
		e := make([]byte, 8)
		c.Encrypt(e, t[:8])
		d := make([]byte, 8)
		c.Decrypt(d, e)
		fmt.Println("DES", h(s[:8]), h(t[:8]), h(e), h(d))
		c3, _ := des.NewTripleDESCipher(append(s[:], t[:]...)[:24])
		c3.Encrypt(e, t[8:16])
		c3.Decrypt(d, e)
		fmt.Println("3DES", h(e), h(d))
		msg := append(s[:], t[:]...)
		cbc := cipher.NewCBCEncrypter(c3, t[16:24])
		out := make([]byte, len(msg))
		cbc.CryptBlocks(out, msg)
		fmt.Println("CBC", h(out))
		r, _ := rc4.NewCipher(s[:1+i*7%32])
		ks := make([]byte, 40+i)
		r.XORKeyStream(ks, ks)
		fmt.Println("RC4", h(ks))
		r.XORKeyStream(ks[:5], msg[:5])
		fmt.Println("RC4b", h(ks[:5]))
	}
	for _, n := range []int{0, 7, 9, 16, 24} {
		_, err := des.NewCipher(make([]byte, n))
		_, err3 := des.NewTripleDESCipher(make([]byte, n))
		fmt.Println("KEY", n, err, err3)
	}
	for _, n := range []int{0, 1, 256, 257} {
		_, err := rc4.NewCipher(make([]byte, n))
		fmt.Println("RC4KEY", n, err)
	}
	// 弱密钥
	c, _ := des.NewCipher([]byte{1, 1, 1, 1, 1, 1, 1, 1})
	e := make([]byte, 8)
	c.Encrypt(e, []byte("weakkey!"))
	c.Encrypt(e, e)
	fmt.Println("WEAK", string(e))
}
