package main

import (
	"crypto/elliptic"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"math/big"
)

type stream struct{ ctr int }

func (s *stream) Read(b []byte) (int, error) {
	for i := range b {
		h := sha256.Sum256([]byte{byte(s.ctr), byte(s.ctr >> 8)})
		b[i] = h[0]
		s.ctr++
	}
	return len(b), nil
}

func try(f func() string) (s string) {
	defer func() {
		if r := recover(); r != nil {
			s = fmt.Sprint("panic: ", r)
		}
	}()
	return f()
}

func pt(x, y *big.Int) string {
	if x == nil {
		return "nil"
	}
	return x.Text(16) + "," + y.Text(16)
}

func main() {
	p := elliptic.P256().Params()
	generic := &elliptic.CurveParams{P: p.P, N: p.N, B: p.B, Gx: p.Gx, Gy: p.Gy, BitSize: 256, Name: "generic-256"}
	toy := &elliptic.CurveParams{P: big.NewInt(1009), N: big.NewInt(1), B: big.NewInt(7), Gx: big.NewInt(1), Gy: big.NewInt(1), BitSize: 10, Name: "toy"}
	curves := []elliptic.Curve{elliptic.P224(), elliptic.P256(), elliptic.P384(), elliptic.P521(), generic}
	for ci, c := range curves {
		pr := c.Params()
		fmt.Println("CURVE", pr.Name, pr.BitSize)
		ks := [][]byte{{1}, {2}, {0, 3}, {0xde, 0xad, 0xbe, 0xef}}
		if ci < 4 {
			ks = append(ks, pr.N.Bytes(), new(big.Int).Sub(pr.N, big.NewInt(1)).Bytes(), append([]byte{1}, pr.N.Bytes()...))
		}
		var pts [][2]*big.Int
		for _, k := range ks {
			x, y := c.ScalarBaseMult(k)
			fmt.Println(" SBM", hex.EncodeToString(k), pt(x, y), c.IsOnCurve(x, y))
			pts = append(pts, [2]*big.Int{x, y})
		}
		g := pts[0]
		x2, y2 := c.Double(g[0], g[1])
		fmt.Println(" DBL", pt(x2, y2))
		x3, y3 := c.Add(g[0], g[1], x2, y2)
		fmt.Println(" ADD", pt(x3, y3))
		x4, y4 := c.Add(g[0], g[1], g[0], g[1])
		fmt.Println(" ADDSELF", pt(x4, y4))
		neg := new(big.Int).Sub(pr.P, g[1])
		x5, y5 := c.Add(g[0], g[1], g[0], neg)
		fmt.Println(" ADDNEG", pt(x5, y5))
		x6, y6 := c.Add(big.NewInt(0), big.NewInt(0), g[0], g[1])
		fmt.Println(" ADDINF", pt(x6, y6))
		x7, y7 := c.ScalarMult(x2, y2, []byte{5})
		fmt.Println(" SM", pt(x7, y7))
		m := elliptic.Marshal(c, x3, y3)
		mc := elliptic.MarshalCompressed(c, x3, y3)
		fmt.Println(" M", hex.EncodeToString(m), hex.EncodeToString(mc))
		ux, uy := elliptic.Unmarshal(c, m)
		cx, cy := elliptic.UnmarshalCompressed(c, mc)
		fmt.Println(" U", pt(ux, uy), pt(cx, cy))
		bad := append([]byte{}, m...)
		bad[len(bad)-1] ^= 1
		ux, _ = elliptic.Unmarshal(c, bad)
		fmt.Println(" UBAD", ux == nil, func() bool { x, _ := elliptic.Unmarshal(c, m[:len(m)-1]); return x == nil }(), func() bool { x, _ := elliptic.UnmarshalCompressed(c, append([]byte{4}, mc[1:]...)); return x == nil }())
		mc2 := append([]byte{}, mc...)
		mc2[0] ^= 1
		cx, cy = elliptic.UnmarshalCompressed(c, mc2)
		fmt.Println(" UFLIP", pt(cx, cy))
		fmt.Println(" ON", c.IsOnCurve(big.NewInt(0), big.NewInt(0)), c.IsOnCurve(g[0], neg), c.IsOnCurve(g[0], big.NewInt(1)), c.IsOnCurve(new(big.Int).Neg(g[0]), g[1]), c.IsOnCurve(new(big.Int).Add(g[0], pr.P), g[1]))
		fmt.Println(" P1", try(func() string { x, y := c.Add(g[0], big.NewInt(1), g[0], g[1]); return pt(x, y) }))
		fmt.Println(" P2", try(func() string { x, y := c.Double(g[0], big.NewInt(1)); return pt(x, y) }))
		fmt.Println(" P3", try(func() string { x, y := c.ScalarMult(g[0], big.NewInt(1), []byte{2}); return pt(x, y) }))
		fmt.Println(" P4", try(func() string { return hex.EncodeToString(elliptic.Marshal(c, g[0], big.NewInt(1))) }))
		fmt.Println(" P5", hex.EncodeToString(elliptic.Marshal(c, big.NewInt(0), big.NewInt(0))))
		if ci != 4 {
			priv, x, y, err := elliptic.GenerateKey(c, &stream{ctr: ci * 100})
			fmt.Println(" GEN", hex.EncodeToString(priv), pt(x, y), err)
		}
	}
	fmt.Println("TOY", toy.IsOnCurve(big.NewInt(1), big.NewInt(1)))
	for xv := int64(0); xv < 12; xv++ {
		cx, cy := elliptic.UnmarshalCompressed(toy, []byte{2, 0, byte(xv)})
		fmt.Println(" TC", xv, pt(cx, cy))
	}
}
