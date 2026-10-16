package main

import (
	"fmt"
	"math"
	"math/big"
)

func B(b bool) string {
	if b {
		return "true"
	}
	return "false"
}

func F(f float64) int64 { return int64(math.Float64bits(f)) }

func show(x *big.Rat) string {
	f, exact := x.Float64()
	n, ex := x.FloatPrec()
	return fmt.Sprintf("%s %s %s %s %s %s %s %s %d %d %s %d %s", x.String(), x.RatString(), x.FloatString(0), x.FloatString(3), x.FloatString(10), x.Num().String(), x.Denom().String(), B(x.IsInt()), x.Sign(), F(f), B(exact), n, B(ex))
}

func main() {
	strs := []string{"0", "1", "-1", "3/4", "-6/8", "+5/10", "0x10/0b11", "-0o17/011", "1_000/3", "1/0", "1/-2", "1/+2", "/2", "2/", "1/2/3", "1.25", "-1.25", ".5", "5.", "1e3", "1E-3", "1.5e+2", "-0.001e-2", "0x1p-2", "0x1.8p1", "0b1.01", "0o7.4", "0x.8", "0X1P4", "1_234.5_6", "1__2", "_1", "1_", "1.2.3", "1e", "1e+", "1p3", "0b102", "abc", "", "+", "-", ".", "-.", "1e1000001", "1e-1000001", "00012", "0012.5", "12e-1_0", "1e9999999999999999999", "0.1", "0.3", "123456789012345678901234567890.123456789", "-7/3", "22/7", "355/113", "1/3", "2/3", "5/2", "-5/2", "7/2", "-1/1000000", "999999/1000000", "1e-400", "1e-320", "4.9406564584124654e-324", "2.4703282292062327e-324", "2.4703282292062328e-324", "1.7976931348623157e308", "1.7976931348623159e308", "1e309", "9007199254740993", "9007199254740995", "0.1e1", "1/10", "-0", "0/5", "0e10", "0x"}
	var rs []*big.Rat
	for _, s := range strs {
		r, ok := new(big.Rat).SetString(s)
		if !ok {
			fmt.Println(s, "nil")
			continue
		}
		fmt.Println(s, show(r))
		rs = append(rs, r)
	}
	small := []*big.Rat{big.NewRat(0, 1), big.NewRat(1, 2), big.NewRat(-3, 4), big.NewRat(7, -3), big.NewRat(-10, -4), big.NewRat(5, 1), big.NewRat(-1, 7), big.NewRat(math.MaxInt64, 3), big.NewRat(math.MinInt64, 1), big.NewRat(math.MinInt64, -2)}
	for _, x := range small {
		fmt.Println(show(x), show(new(big.Rat).Neg(x)), show(new(big.Rat).Abs(x)))
		if x.Sign() != 0 {
			fmt.Println(show(new(big.Rat).Inv(x)))
		}
		for _, y := range small {
			line := fmt.Sprintf("%s %s %s %d", new(big.Rat).Add(x, y).String(), new(big.Rat).Sub(x, y).String(), new(big.Rat).Mul(x, y).String(), x.Cmp(y))
			if y.Sign() != 0 {
				line += " " + new(big.Rat).Quo(x, y).String()
			}
			fmt.Println(line)
		}
	}
	for _, f := range []float64{0, 1, -1, 0.1, -2.5, 1e100, 1e-100, 5e-324, math.MaxFloat64, math.SmallestNonzeroFloat64 * 3, 2.2250738585072014e-308, 123456.789, math.Copysign(0, -1), math.Inf(1), math.Inf(-1), math.NaN()} {
		r := new(big.Rat).SetFloat64(f)
		if r == nil {
			fmt.Println("nil")
			continue
		}
		fmt.Println(show(r))
	}
	a, _ := new(big.Int).SetString("123456789012345678901234567890", 10)
	b, _ := new(big.Int).SetString("-987654321098765432109876543210", 10)
	fmt.Println(show(new(big.Rat).SetFrac(a, b)), show(new(big.Rat).SetFrac(b, a)), show(new(big.Rat).SetInt(a)), show(new(big.Rat).SetInt64(-42)), show(new(big.Rat).SetUint64(math.MaxUint64)))
	// 级数：1 + 1/2 + ... + 1/30
	h := new(big.Rat)
	for i := int64(1); i <= 30; i++ {
		h.Add(h, big.NewRat(1, i))
	}
	fmt.Println(show(h))
	// 连分数逼近 sqrt(2)
	c := big.NewRat(1, 1)
	for i := 0; i < 20; i++ {
		c = new(big.Rat).Add(big.NewRat(1, 1), new(big.Rat).Inv(new(big.Rat).Add(big.NewRat(1, 1), c)))
	}
	fmt.Println(show(c))
	for _, p := range []int{0, 1, 2, 5, 20} {
		fmt.Println(big.NewRat(2, 3).FloatString(p), big.NewRat(-2, 3).FloatString(p), big.NewRat(1, 2).FloatString(p), big.NewRat(-1, 2).FloatString(p), big.NewRat(-5, 1000).FloatString(p), big.NewRat(99999, 100000).FloatString(p), big.NewRat(-99999, 100000).FloatString(p), big.NewRat(1, 3).FloatString(p))
	}
}
