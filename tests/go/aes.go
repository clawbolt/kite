package main

import (
	"crypto/aes"
	"encoding/hex"
	"fmt"
)

var seed uint32 = 12345

func rnd() int {
	seed = (seed*1103515245 + 12345) & 0x7fffffff
	return int(seed >> 16)
}

func rbytes(n int) []byte {
	b := make([]byte, n)
	for i := range b {
		b[i] = byte(rnd() & 0xff)
	}
	return b
}

func H(b []byte) string { return hex.EncodeToString(b) }

func D(s string) []byte { b, _ := hex.DecodeString(s); return b }

func tryEnc(name string, f func()) {
	defer func() {
		if r := recover(); r != nil {
			fmt.Println(name, "panic:", r)
		}
	}()
	f()
	fmt.Println(name, "ok")
}

func main() {
	fmt.Println("BlockSize", aes.BlockSize)
	// FIPS-197 附录 C
	pt := D("00112233445566778899aabbccddeeff")
	for _, k := range []string{"000102030405060708090a0b0c0d0e0f", "000102030405060708090a0b0c0d0e0f1011121314151617", "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"} {
		c, err := aes.NewCipher(D(k))
		if err != nil {
			fmt.Println(err)
			continue
		}
		ct := make([]byte, 16)
		c.Encrypt(ct, pt)
		back := make([]byte, 16)
		c.Decrypt(back, ct)
		fmt.Println(len(k)/2, c.BlockSize(), H(ct), H(back))
	}
	// FIPS-197 附录 A 的密钥（AES-128 示例）
	c, _ := aes.NewCipher(D("2b7e151628aed2a6abf7158809cf4f3c"))
	out := make([]byte, 16)
	c.Encrypt(out, D("3243f6a8885a308d313198a2e0370734"))
	fmt.Println("A.1", H(out))
	// 密钥长度错误
	for n := 0; n <= 40; n++ {
		_, err := aes.NewCipher(make([]byte, n))
		if err != nil {
			fmt.Println(n, err)
		} else {
			fmt.Println(n, "ok")
		}
	}
	// 随机密钥与分组
	for _, ks := range []int{16, 24, 32} {
		for i := 0; i < 12; i++ {
			key := rbytes(ks)
			blk := rbytes(16)
			c, _ := aes.NewCipher(key)
			e := make([]byte, 16)
			d := make([]byte, 16)
			c.Encrypt(e, blk)
			c.Decrypt(d, blk)
			b2 := make([]byte, 16)
			c.Decrypt(b2, e)
			fmt.Println(ks, H(key), H(blk), H(e), H(d), b2[0] == blk[0] && H(b2) == H(blk))
		}
	}
	// 迭代加密 200 次（蒙特卡洛风格）
	for _, ks := range []int{16, 24, 32} {
		key := make([]byte, ks)
		for i := range key {
			key[i] = byte(i * 7)
		}
		c, _ := aes.NewCipher(key)
		b := make([]byte, 16)
		for i := 0; i < 200; i++ {
			c.Encrypt(b, b)
		}
		fmt.Println("mc enc", ks, H(b))
		for i := 0; i < 200; i++ {
			c.Decrypt(b, b)
		}
		fmt.Println("mc dec", ks, H(b))
	}
	// 输入多于 16 字节时只处理前 16 字节；不足 16 字节 panic
	c2, _ := aes.NewCipher(make([]byte, 16))
	long := rbytes(20)
	o := make([]byte, 16)
	c2.Encrypt(o, long)
	fmt.Println("long", H(o))
	tryEnc("short enc", func() { c2.Encrypt(make([]byte, 16), make([]byte, 15)) })
	tryEnc("short dec", func() { c2.Decrypt(make([]byte, 16), make([]byte, 3)) })
	tryEnc("full enc", func() { c2.Encrypt(make([]byte, 16), make([]byte, 16)) })
}
