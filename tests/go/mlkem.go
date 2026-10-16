package main

import (
	"crypto/mlkem"
	"crypto/mlkem/mlkemtest"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
)

func h(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:12]) }

func es(err error) string {
	if err == nil {
		return "<nil>"
	}
	return err.Error()
}

func main() {
	for i := 0; i < 2; i++ {
		seed := make([]byte, 64)
		for j := range seed {
			seed[j] = byte(j*7 + i*13)
		}
		r := make([]byte, 32)
		for j := range r {
			r[j] = byte(j*3 + i)
		}
		{
			dk, err := mlkem.NewDecapsulationKey768(seed)
			ek := dk.EncapsulationKey()
			fmt.Println("768", es(err), h(ek.Bytes()), len(ek.Bytes()), hex.EncodeToString(dk.Bytes()[:8]))
			k, ct, err := mlkemtest.Encapsulate768(ek, r)
			fmt.Println(" ENC", hex.EncodeToString(k), h(ct), len(ct), es(err))
			k2, err := dk.Decapsulate(ct)
			fmt.Println(" DEC", hex.EncodeToString(k2), es(err))
			ct[5] ^= 1
			k3, _ := dk.Decapsulate(ct)
			fmt.Println(" REJ", hex.EncodeToString(k3))
			_, err = dk.Decapsulate(ct[1:])
			fmt.Println(" E", es(err))
			ek2, err := mlkem.NewEncapsulationKey768(ek.Bytes())
			fmt.Println(" EK", es(err), h(ek2.Bytes()))
			b := ek.Bytes()
			b[0], b[1] = 0xff, 0xff
			_, err = mlkem.NewEncapsulationKey768(b)
			fmt.Println(" EKBAD", es(err))
			_, err = mlkem.NewEncapsulationKey768(b[:100])
			fmt.Println(" EKLEN", es(err))
			_, _, err = mlkemtest.Encapsulate768(ek, r[:31])
			fmt.Println(" RLEN", es(err))
		}
		{
			dk, err := mlkem.NewDecapsulationKey1024(seed)
			ek := dk.EncapsulationKey()
			fmt.Println("1024", es(err), h(ek.Bytes()), len(ek.Bytes()))
			k, ct, err := mlkemtest.Encapsulate1024(ek, r)
			fmt.Println(" ENC", hex.EncodeToString(k), h(ct), len(ct), es(err))
			k2, err := dk.Decapsulate(ct)
			fmt.Println(" DEC", hex.EncodeToString(k2), es(err))
			ct[len(ct)-1] ^= 0x80
			k3, _ := dk.Decapsulate(ct)
			fmt.Println(" REJ", hex.EncodeToString(k3))
			_, _, err = mlkemtest.Encapsulate1024(ek, r[:31])
			fmt.Println(" RLEN", es(err))
		}
	}
	_, err := mlkem.NewDecapsulationKey768(make([]byte, 63))
	fmt.Println("SEED", es(err))
}
