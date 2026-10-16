package main

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
)

var modes = []big.RoundingMode{big.ToNearestEven, big.ToNearestAway, big.ToZero, big.AwayFromZero, big.ToNegativeInf, big.ToPositiveInf}

func g64(f float64) string { return strconv.FormatFloat(f, 'g', -1, 64) }
func g32(f float32) string { return strconv.FormatFloat(float64(f), 'g', -1, 32) }

func texts(x *big.Float) string {
	parts := []string{}
	for _, f := range []struct {
		c byte
		p int
	}{{'e', -1}, {'e', 0}, {'e', 5}, {'E', 12}, {'f', -1}, {'f', 0}, {'f', 3}, {'g', -1}, {'g', 0}, {'g', 3}, {'G', 17}, {'b', 0}, {'p', 0}, {'x', -1}, {'x', 0}, {'x', 4}, {'q', 2}} {
		parts = append(parts, x.Text(f.c, f.p))
	}
	return strings.Join(parts, " ")
}

func props(x *big.Float) string {
	m := new(big.Float)
	e := x.MantExp(m)
	f64, a64 := x.Float64()
	f32, a32 := x.Float32()
	i64, ai := x.Int64()
	u64, au := x.Uint64()
	bi, ab := x.Int(nil)
	bis := "nil"
	if bi != nil {
		bis = bi.String()
	}
	r, ar := x.Rat(nil)
	rs := "nil"
	if r != nil {
		rs = r.String()
	}
	mt, _ := x.MarshalText()
	return fmt.Sprintf("prec=%d minprec=%d mode=%v acc=%v sign=%d signbit=%v inf=%v int=%v mant=%s exp=%d f64=%s %v f32=%s %v i64=%d %v u64=%d %v int=%s %v rat=%s %v mt=%s str=%s",
		x.Prec(), x.MinPrec(), x.Mode(), x.Acc(), x.Sign(), x.Signbit(), x.IsInf(), x.IsInt(), m.Text('p', 0), e, g64(f64), a64, g32(f32), a32, i64, ai, u64, au, bis, ab, rs, ar, string(mt), x.String())
}

func ar(x *big.Float) string { return x.Text('g', -1) + "/" + x.Text('p', 0) + "/" + x.Acc().String() }

func try(f func() string) string {
	var out string
	func() {
		defer func() {
			if r := recover(); r != nil {
				out = "panic " + r.(big.ErrNaN).Error()
			}
		}()
		out = f()
	}()
	return out
}

func main() {
	fs := []float64{0, math.Copysign(0, -1), 1, -1, 0.1, 1.5, -2.75, 1e300, 1e-300, 5e-324, math.MaxFloat64, 123456.789, 0.5, 1 << 62, -(1 << 63), 1 << 64, 3.0e10, 1e23, math.Inf(1), math.Inf(-1)}
	for _, f := range fs {
		x := big.NewFloat(f)
		fmt.Println(texts(x))
		fmt.Println(props(x))
	}
	// precision and rounding modes
	third := new(big.Float).SetPrec(200).Quo(big.NewFloat(1), big.NewFloat(3))
	vals := []*big.Float{third, new(big.Float).Neg(third), big.NewFloat(2.5), big.NewFloat(-2.5), big.NewFloat(3.5), big.NewFloat(1.0e-310), big.NewFloat(0.1), big.NewFloat(1023.75)}
	for _, v := range vals {
		for _, p := range []uint{1, 2, 5, 24, 53} {
			line := fmt.Sprintf("p%d", p)
			for _, md := range modes {
				y := new(big.Float).Copy(v).SetMode(md).SetPrec(p)
				line += " " + y.Text('p', 0) + "/" + y.Acc().String()
			}
			fmt.Println(line)
		}
		y := new(big.Float).Copy(v).SetPrec(0)
		fmt.Println("p0", ar(y), y.Prec())
		fmt.Println(props(v))
	}
	// arithmetic
	ops := []*big.Float{big.NewFloat(0), big.NewFloat(math.Copysign(0, -1)), big.NewFloat(1), big.NewFloat(-3), big.NewFloat(0.1), big.NewFloat(1e20), big.NewFloat(-7.25e-5), third, big.NewFloat(math.Inf(1)), big.NewFloat(math.Inf(-1))}
	for _, a := range ops {
		for _, b := range ops {
			line := ""
			line += try(func() string { return ar(new(big.Float).Add(a, b)) }) + " "
			line += try(func() string { return ar(new(big.Float).Sub(a, b)) }) + " "
			line += try(func() string { return ar(new(big.Float).Mul(a, b)) }) + " "
			line += try(func() string { return ar(new(big.Float).Quo(a, b)) }) + " "
			line += fmt.Sprint(a.Cmp(b))
			fmt.Println(line)
		}
	}
	xs := []*big.Float{big.NewFloat(1), big.NewFloat(-3), big.NewFloat(0.1), big.NewFloat(1e20), big.NewFloat(-7.25e-5), third}
	for _, md := range modes {
		for _, p := range []uint{9} {
			line := fmt.Sprintf("%v %d", md, p)
			for i, a := range xs {
				b := xs[(i+1)%len(xs)]
				z := func() *big.Float { return new(big.Float).SetPrec(p).SetMode(md) }
				line += " " + ar(z().Add(a, b)) + " " + ar(z().Sub(a, b)) + " " + ar(z().Mul(a, b)) + " " + ar(z().Quo(a, b))
			}
			fmt.Println(line)
		}
	}
	// exact cancellation with ToNegativeInf
	fmt.Println(ar(new(big.Float).SetMode(big.ToNegativeInf).Sub(big.NewFloat(1), big.NewFloat(1))), ar(new(big.Float).Sub(big.NewFloat(1), big.NewFloat(1))), ar(new(big.Float).SetMode(big.ToNegativeInf).Add(big.NewFloat(1), big.NewFloat(-1))))
	// parse
	strs := []string{"0", "-0", "+0", "1", "-1.5", "0.1", ".5", "5.", "1e10", "1E-10", "-1.25e+3", "123456789012345678901234567890", "3.14159265358979323846264338327950288419716939937510",
		"1e1000", "-1e-1000", "0x1p-2", "0x1.8p1", "0X.8p+3", "0b101.1", "0o17", "0x_1_0p0", "1_000.5", "Inf", "-Inf", "+inf", "inf", "1p10", "0x10", "0b1e3",
		"", "-", "abc", "1.2.3", "1e", "1e+", "0x", "1__0", "_1", "1_", "1e99999999999999999999", "1x", "1.5 ", "--1", "1e2147483647", "1p2147483647", "0x1p-2147483700", "1.5p-3", "1e-30", "2.2250738585072011e-308", "4.9406564584124654e-324"}
	for _, s := range strs {
		f, b, err := new(big.Float).Parse(s, 0)
		if err != nil {
			fmt.Println(s, "err", err)
			continue
		}
		fmt.Println(s, b, ar(f), f.Prec(), f.Text('g', 25))
	}
	for _, base := range []int{2, 8, 10, 16} {
		for _, s := range []string{"101", "-17.5", "ff.8", "1e3", "1p3", "0x10", "777"} {
			for _, p := range []uint{0, 10, 100} {
				f, b, err := big.ParseFloat(s, base, p, big.ToZero)
				if err != nil {
					fmt.Println(base, s, p, "err", err)
					continue
				}
				fmt.Println(base, s, p, b, ar(f), f.Prec(), f.Mode())
			}
		}
	}
	for _, s := range []string{"1.5", "x", "-2e-3"} {
		f, ok := new(big.Float).SetString(s)
		if ok {
			fmt.Println("setstring", s, ar(f))
		} else {
			fmt.Println("setstring", s, "fail")
		}
		var z big.Float
		err := z.UnmarshalText([]byte(s))
		fmt.Println("unmarshal", s, err, z.Text('g', -1))
	}
	// sqrt
	for _, v := range []float64{0, math.Copysign(0, -1), 1, 2, 3, 0.5, 0.25, 1e-10, 12345.678, 1e300, math.Inf(1)} {
		x := big.NewFloat(v)
		line := g64(v)
		for _, p := range []uint{0, 10, 53, 100, 300} {
			z := new(big.Float).SetPrec(p).Sqrt(x)
			line += " " + z.Text('g', -1) + "/" + z.Acc().String() + "/" + fmt.Sprint(z.Prec())
		}
		fmt.Println(line)
	}
	fmt.Println(new(big.Float).SetPrec(64).Sqrt(new(big.Float).SetMode(big.ToZero).SetFloat64(2)).Mode(), try(func() string { return new(big.Float).Sqrt(big.NewFloat(-1)).String() }))
	// conversions
	bigs := []string{"0", "1", "-1", "12345678901234567890123", "-340282366920938463463374607431768211456", "9223372036854775807", "-9223372036854775808", "18446744073709551615", "18446744073709551616"}
	for _, s := range bigs {
		i, _ := new(big.Int).SetString(s, 10)
		for _, p := range []uint{0, 8} {
			x := new(big.Float).SetPrec(p).SetInt(i)
			fmt.Println("int", s, p, ar(x), x.Prec(), texts(x))
			fmt.Println(props(x))
		}
	}
	for _, r := range [][2]int64{{1, 3}, {-22, 7}, {5, 1}, {0, 9}, {1, 1 << 40}, {355, 113}} {
		q := big.NewRat(r[0], r[1])
		for _, p := range []uint{0, 10} {
			x := new(big.Float).SetPrec(p).SetRat(q)
			fmt.Println("rat", q, p, ar(x), x.Prec())
		}
	}
	for _, n := range []int64{0, 1, -1, 255, -1 << 63, 1<<63 - 1, 123456789} {
		for _, p := range []uint{0, 4, 64} {
			x := new(big.Float).SetPrec(p).SetInt64(n)
			y := new(big.Float).SetPrec(p).SetUint64(uint64(n))
			fmt.Println("i64", n, p, ar(x), x.Prec(), ar(y), y.Prec())
		}
	}
	m := big.NewFloat(0.75)
	for _, e := range []int{0, 1, -1, 100, -2000} {
		z := new(big.Float).SetMantExp(m, e)
		fmt.Println("mantexp", e, ar(z), z.Prec())
	}
	fmt.Println(ar(new(big.Float).SetInf(true)), ar(new(big.Float).SetInf(false)), new(big.Float).SetInf(true).Prec(), ar(new(big.Float).Abs(big.NewFloat(-2))), ar(new(big.Float).SetPrec(2).Neg(big.NewFloat(7))))
	fmt.Println(try(func() string { return big.NewFloat(math.NaN()).String() }), try(func() string { return new(big.Float).SetFloat64(math.NaN()).String() }))
	// float64 edge rounding
	for _, s := range []string{"0x1p-1075", "0x1.0000001p-1075", "0x1p-1076", "0x1.8p-1074", "0x1.fffffffffffff8p1023", "0x1.fffffffffffff7p1023", "0x1p1024", "-0x1p-1075", "0x1p-150", "0x1.000001p-150", "0x1.ffffffp127", "0x1.fffffefp127", "0x1.8p-149", "1e-46"} {
		x, _, _ := new(big.Float).SetPrec(200).Parse(s, 0)
		f64, a64 := x.Float64()
		f32, a32 := x.Float32()
		fmt.Println(s, g64(f64), a64, g32(f32), a32)
	}
	fmt.Println(big.ToNearestEven, big.ToNearestAway, big.ToZero, big.AwayFromZero, big.ToNegativeInf, big.ToPositiveInf, big.Below, big.Exact, big.Above, big.MaxExp, big.MinExp, big.MaxPrec)
}
