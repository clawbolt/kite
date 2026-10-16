//go:debug cryptocustomrand=1

package main

import (
	"crypto/dsa"
	"crypto/sha1"
	"crypto/sha256"
	"fmt"
	"math/big"
)

type stream struct{ ctr int }

func (s *stream) Read(b []byte) (int, error) {
	for i := range b {
		h := sha256.Sum256([]byte{byte(s.ctr), byte(s.ctr >> 8), 9})
		b[i] = h[0]
		s.ctr++
	}
	return len(b), nil
}

type constReader struct{}

func (constReader) Read(b []byte) (int, error) {
	for i := range b {
		b[i] = 0x5a
	}
	return len(b), nil
}

func hx(s string) *big.Int { x, _ := new(big.Int).SetString(s, 16); return x }

func main() {
	params := dsa.Parameters{
		P: hx("e576237369c0e332c4296e5f77e4d6855542a20e19c73d2ba58d505a8a57f049cf55921dd67c9dc37cf1f970dcc75e413118b8c0d3474e34b0b9f4a00747b2b5e8a1316f47d65d480646f9c31b6b4593029f5c91ba28ad875a7802a8ebb41cf0b309e3bb857c9ec81b6e67bd45b84215b851ab77a9a34e57b0bcfb9ec21dd0fd"),
		Q: hx("9949cc6bbdb0045399577d68dde986ba8f288c7b"),
		G: hx("2482c8bd44acc857b7d21602ecb4d2f528a204625f3ca04c96010915327e16713e58a42257c832d2d706f9189803d9760703a0fd92a2043aac3d38fdcc10e00125a5f556784a4c811b77ee2f3763d69c6fe6b5e82be7624b1cf1fd5514cc41496a60e04b6ed4f4889ade530f4e1d77c701d459c6aafca0b3a91d33e0561106d2"),
	}
	var keys []*dsa.PrivateKey
	for i := 0; i < 2; i++ {
		var k dsa.PrivateKey
		k.Parameters = params
		err := dsa.GenerateKey(&k, &stream{ctr: i * 1000})
		fmt.Printf("KEY %v %x %x\n", err, k.X, k.Y)
		keys = append(keys, &k)
	}
	h1 := sha1.Sum([]byte("msg"))
	h2 := sha256.Sum256([]byte("msg"))
	for i, h := range [][]byte{h1[:], h2[:]} {
		k := keys[i]
		r, s, err := dsa.Sign(constReader{}, k, h)
		fmt.Printf("SIG %x %x %v\n", r, s, err)
		fmt.Println("V", dsa.Verify(&k.PublicKey, h, r, s), dsa.Verify(&keys[1-i].PublicKey, h, r, s), dsa.Verify(&k.PublicKey, h[1:], r, s))
		fmt.Println("VB", dsa.Verify(&k.PublicKey, h, big.NewInt(0), s), dsa.Verify(&k.PublicKey, h, r, params.Q), dsa.Verify(&k.PublicKey, h, new(big.Int).Add(r, params.Q), s), dsa.Verify(&k.PublicKey, h, r, new(big.Int).Sub(params.Q, s)))
	}
	var e dsa.PrivateKey
	fmt.Println("E1", dsa.GenerateKey(&e, &stream{}))
	bad := *keys[0]
	bad.X = big.NewInt(0)
	_, _, err := dsa.Sign(constReader{}, &bad, h1[:])
	fmt.Println("E2", err)
	odd := *keys[0]
	odd.Parameters.Q = big.NewInt(0x7fff)
	_, _, err = dsa.Sign(constReader{}, &odd, h1[:])
	fmt.Println("E3", err)
}
