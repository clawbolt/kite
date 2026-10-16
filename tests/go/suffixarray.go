package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"index/suffixarray"
	"strings"
)

func main() {
	texts := []string{"", "a", "banana", "mississippi", "abracadabra abracadabra", strings.Repeat("ab", 50) + "x", "the quick brown fox jumps over the lazy dog; the dog sleeps"}
	var bin []byte
	for i := 0; i < 400; i++ {
		s := sha256.Sum256([]byte{byte(i)})
		bin = append(bin, s[0]&3)
	}
	texts = append(texts, string(bin))
	for _, t := range texts {
		x := suffixarray.New([]byte(t))
		fmt.Printf("TEXT %q\n", t)
		for _, q := range []string{"", "a", "an", "ana", "s", "ssi", "abra", "the", "dog", "zz", "ab", "\x01\x02", "\x00\x00\x00"} {
			for _, n := range []int{-1, 0, 1, 3} {
				fmt.Print(x.Lookup([]byte(q), n), " ")
			}
			fmt.Println()
		}
		var buf bytes.Buffer
		x.Write(&buf)
		b := buf.Bytes()
		sum := sha256.Sum256(b)
		fmt.Println("WRITE", len(b), hex.EncodeToString(sum[:8]))
		y := suffixarray.New(nil)
		fmt.Println("READ", y.Read(bytes.NewReader(b)), string(y.Bytes()) == t)
		for _, cut := range []int{0, 5, 10, len(b) - 1} {
			if cut < 0 || cut >= len(b) {
				continue
			}
			z := suffixarray.New(nil)
			fmt.Println("CUT", cut, z.Read(bytes.NewReader(b[:cut])))
		}
	}
	// 大于一个缓冲区的序列化
	big := make([]byte, 9000)
	for i := range big {
		big[i] = byte(i * 7 % 251)
	}
	x := suffixarray.New(big)
	var buf bytes.Buffer
	x.Write(&buf)
	sum := sha256.Sum256(buf.Bytes())
	fmt.Println("BIG", buf.Len(), hex.EncodeToString(sum[:]), x.Lookup([]byte{7, 14}, -1))
}
