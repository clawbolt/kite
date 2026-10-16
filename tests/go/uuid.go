package main

import (
	"crypto/rand"
	"fmt"
	"math/big"
	"uuid"
)

func main() {
	for _, s := range []string{
		"f81d4fae-7dec-11d0-a765-00a0c91e6bf6", "F81D4FAE-7DEC-11D0-A765-00A0C91E6BF6", "{f81d4fae-7dec-11d0-a765-00a0c91e6bf6}",
		"urn:uuid:f81d4fae-7dec-11d0-a765-00a0c91e6bf6", "URN:UUID:f81d4fae-7dec-11d0-a765-00a0c91e6bf6", "f81d4fae7dec11d0a76500a0c91e6bf6",
		"f81d4fae-7dec-11d0-a765-00a0c91e6bf", "f81d4fae-7dec-11d0-a765_00a0c91e6bf6", "g81d4fae-7dec-11d0-a765-00a0c91e6bf6",
		"(f81d4fae-7dec-11d0-a765-00a0c91e6bf6)", "{f81d4fae-7dec-11d0-a765-00a0c91e6bf6", "", "00000000-0000-0000-0000-000000000000",
		"ffffffff-ffff-ffff-ffff-ffffffffffff", "f81d4fae7dec11d0a76500a0c91e6bfz", "+81d4fae-7dec-11d0-a765-00a0c91e6bf6",
		"f81d4fae-7dec-11d0-a765-00a0c91e6bf6 ", "xxxf81d4fae-7dec-11d0-a765-00a0c91e6bf6",
	} {
		u, err := uuid.Parse(s)
		fmt.Printf("%q %s %v\n", s, u.String(), err)
	}
	a, b := uuid.MustParse("00000000-0000-0000-0000-000000000001"), uuid.MustParse("00000000-0000-0000-0000-000000000002")
	fmt.Println(a.Compare(b), b.Compare(a), a.Compare(a), a == a, a == b, uuid.Nil().String(), uuid.Max().String(), uuid.Nil().Compare(uuid.Max()))
	t, _ := a.AppendText([]byte("id="))
	fmt.Println(string(t))
	v4, v7 := uuid.New(), uuid.NewV7()
	fmt.Println(v4[6]>>4, v4[8]>>6, v7[6]>>4, v7[8]>>6, v4 != uuid.NewV4())
	prev := uuid.NewV7()
	ok := true
	for i := 0; i < 200; i++ {
		n := uuid.NewV7()
		if prev.Compare(n) >= 0 { ok = false }
		prev = n
	}
	fmt.Println(ok)
	fmt.Println(len(rand.Text()), len(make([]byte, 0)))
	m := big.NewInt(1000)
	okInt := true
	for i := 0; i < 100; i++ {
		x, _ := rand.Int(rand.Reader, m)
		if x.Sign() < 0 || x.Cmp(m) >= 0 { okInt = false }
	}
	fmt.Println(okInt)
}
