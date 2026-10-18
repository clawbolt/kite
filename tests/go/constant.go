package main

import (
	"fmt"
	"go/constant"
	"go/token"
	"math"
	"math/big"
	"strconv"
	"strings"
)

func f64(f float64) string { return strconv.FormatFloat(f, 'g', -1, 64) }
func f32(f float32) string { return strconv.FormatFloat(float64(f), 'g', -1, 32) }

func blist(bs []byte) string {
	parts := []string{}
	for _, b := range bs {
		parts = append(parts, strconv.Itoa(int(b)))
	}
	return "[" + strings.Join(parts, ", ") + "]"
}

func show(x constant.Value) string {
	return fmt.Sprintf("%s|%s|%s", x.Kind(), x.String(), x.ExactString())
}

func valType(x constant.Value) string {
	return fmt.Sprintf("%T", constant.Val(x))
}

func numeric(x constant.Value) bool {
	k := x.Kind()
	return k == constant.Int || k == constant.Float || k == constant.Complex || k == constant.Unknown
}

func describe(name string, x constant.Value) {
	fmt.Printf("%s => %s val=%s\n", name, show(x), valType(x))
	k := x.Kind()
	if numeric(x) {
		fmt.Printf("  sign=%d real=%s imag=%s\n", constant.Sign(x), constant.Real(x), constant.Imag(x))
		fmt.Printf("  toInt=%s toFloat=%s toComplex=%s\n", show(constant.ToInt(x)), show(constant.ToFloat(x)), show(constant.ToComplex(x)))
	}
	if k == constant.Int || k == constant.Float || k == constant.Unknown {
		fmt.Printf("  num=%s denom=%s imag*=%s\n", show(constant.Num(x)), show(constant.Denom(x)), show(constant.MakeImag(x)))
		f, e := constant.Float64Val(x)
		g, e2 := constant.Float32Val(x)
		fmt.Printf("  f64=%s %v f32=%s %v\n", f64(f), e, f32(g), e2)
	}
	if k == constant.Int || k == constant.Unknown {
		i, e := constant.Int64Val(x)
		u, e2 := constant.Uint64Val(x)
		fmt.Printf("  i64=%d %v u64=%d %v bitlen=%d\n", i, e, u, e2, constant.BitLen(x))
	}
	if k == constant.Int {
		bs := constant.Bytes(x)
		fmt.Printf("  bytes=%s back=%s\n", blist(bs), show(constant.MakeFromBytes(bs)))
	}
	if k == constant.Bool || k == constant.Unknown {
		fmt.Printf("  bool=%v\n", constant.BoolVal(x))
	}
	if k == constant.String || k == constant.Unknown {
		fmt.Printf("  str=%q len=%d\n", constant.StringVal(x), constant.StringLen(x))
	}
}

type lit struct {
	tok token.Token
	s   string
}

func main() {
	lits := []lit{
		{token.INT, "0"}, {token.INT, "42"}, {token.INT, "0x7fffffffffffffff"},
		{token.INT, "0x8000000000000000"}, {token.INT, "0xffffffffffffffff"},
		{token.INT, "123456789012345678901234567890"}, {token.INT, "0b1011"}, {token.INT, "0o777"},
		{token.INT, "0777"}, {token.INT, "1_000_000"}, {token.INT, "12a"}, {token.INT, "_1"},
		{token.FLOAT, "1.5"}, {token.FLOAT, "0.1"}, {token.FLOAT, "1e10"}, {token.FLOAT, "1e-10"},
		{token.FLOAT, "3.14159265358979323846264338327950288419716939937510582097494459"},
		{token.FLOAT, "1e100"}, {token.FLOAT, "1e400"}, {token.FLOAT, "1e-400"}, {token.FLOAT, "1e1000"},
		{token.FLOAT, "1e5000"}, {token.FLOAT, "-1e5000"}, {token.FLOAT, "1e-5000"}, {token.FLOAT, "0x1p-2"},
		{token.FLOAT, "0x1.8p10"}, {token.FLOAT, "1_0.2_5"}, {token.FLOAT, "1e"}, {token.FLOAT, "2.0"},
		{token.FLOAT, "123456789.0"}, {token.FLOAT, "1234567.5"}, {token.FLOAT, "0.000001"},
		{token.FLOAT, "1e-6000000000"}, {token.FLOAT, "1e9999999"},
		{token.FLOAT, "1e-40"}, {token.FLOAT, "1.4e-45"}, {token.FLOAT, "7e-46"}, {token.FLOAT, "3.4028235e38"},
		{token.FLOAT, "3.4028236e38"}, {token.FLOAT, "1e39"}, {token.FLOAT, "-1e-5000"}, {token.FLOAT, "-2.5e-1300"},
		{token.FLOAT, "0.33333333333333333333333333333333333333"}, {token.FLOAT, "16777217"}, {token.FLOAT, "0x1.fffffep127"},
		{token.IMAG, "2i"}, {token.IMAG, "1.5i"}, {token.IMAG, "0x10i"}, {token.IMAG, "1e1000i"}, {token.IMAG, "i"},
		{token.CHAR, "'a'"}, {token.CHAR, "'\\n'"}, {token.CHAR, "'\\u00e9'"}, {token.CHAR, "'世'"},
		{token.CHAR, "'\\x41'"}, {token.CHAR, "'ab'"}, {token.CHAR, "''"}, {token.CHAR, "'\\q'"},
		{token.STRING, `"hello"`}, {token.STRING, "`raw\\n`"}, {token.STRING, `"tab\there"`},
		{token.STRING, `"\u4e16\u754c"`}, {token.STRING, `"bad`}, {token.STRING, `""`},
	}
	for _, l := range lits {
		describe(fmt.Sprintf("lit %s %s", l.tok, l.s), constant.MakeFromLiteral(l.s, l.tok, 0))
	}

	// factories
	describe("unknown", constant.MakeUnknown())
	describe("true", constant.MakeBool(true))
	describe("false", constant.MakeBool(false))
	describe("str", constant.MakeString("go \"constant\"\n"))
	describe("long", constant.MakeString(strings.Repeat("abcdefghij", 10)))
	describe("long-uni", constant.MakeString(strings.Repeat("世界", 40)))
	describe("str70", constant.MakeString(strings.Repeat("x", 70)))
	describe("str71", constant.MakeString(strings.Repeat("x", 71)))
	describe("minint", constant.MakeInt64(math.MinInt64))
	describe("maxint", constant.MakeInt64(math.MaxInt64))
	describe("-5", constant.MakeInt64(-5))
	describe("u64max", constant.MakeUint64(math.MaxUint64))
	describe("u64 1<<63", constant.MakeUint64(1<<63))
	describe("u64 7", constant.MakeUint64(7))
	for _, f := range []float64{0, math.Copysign(0, -1), 1, -2.5, 0.1, 1.0 / 3, 1e300, 1e-300, 5e-324, 1e20, 123456.75, math.MaxFloat64, math.Inf(1), math.NaN(), 16777217, 3.4e38, 3.5e38, 1e-46} {
		describe("f64 "+f64(f), constant.MakeFloat64(f))
	}
	describe("make bigint", constant.Make(new(big.Int).Lsh(big.NewInt(3), 100)))
	describe("make bigint small", constant.Make(big.NewInt(-77)))
	describe("make rat", constant.Make(big.NewRat(-22, 7)))
	describe("make rat int", constant.Make(big.NewRat(10, 5)))
	describe("make float", constant.Make(big.NewFloat(1.25)))
	describe("make float0", constant.Make(big.NewFloat(0)))
	describe("make float inf", constant.Make(new(big.Float).SetInf(true)))
	describe("make float pi", constant.Make(new(big.Float).SetPrec(200).SetFloat64(math.Pi)))
	describe("bytes", constant.MakeFromBytes([]byte{1, 2, 3, 0, 0}))
	describe("bytes empty", constant.MakeFromBytes(nil))
	describe("bytes big", constant.MakeFromBytes([]byte{0, 0, 0, 0, 0, 0, 0, 0, 1}))
	describe("bytes 2^63", constant.MakeFromBytes([]byte{0, 0, 0, 0, 0, 0, 0, 0x80}))

	// complex
	c1 := constant.BinaryOp(constant.MakeInt64(3), token.ADD, constant.MakeImag(constant.MakeInt64(4)))
	c2 := constant.BinaryOp(constant.MakeFromLiteral("1.5", token.FLOAT, 0), token.SUB, constant.MakeImag(constant.MakeFromLiteral("0.5", token.FLOAT, 0)))
	describe("c1", c1)
	describe("c2", c2)
	describe("c1*c2", constant.BinaryOp(c1, token.MUL, c2))
	describe("c1/c2", constant.BinaryOp(c1, token.QUO, c2))
	describe("c1-c1", constant.BinaryOp(c1, token.SUB, c1))
	describe("-c1", constant.UnaryOp(token.SUB, c1, 0))
	describe("+c1", constant.UnaryOp(token.ADD, c1, 0))
	describe("c3", constant.BinaryOp(constant.MakeFloat64(2.5), token.ADD, constant.MakeImag(constant.MakeInt64(0))))

	// binary ops on numbers
	nums := []constant.Value{
		constant.MakeInt64(0), constant.MakeInt64(7), constant.MakeInt64(-3),
		constant.MakeInt64(math.MaxInt64), constant.MakeInt64(math.MinInt64),
		constant.MakeInt64(1 << 40), constant.MakeInt64(-(1 << 62) - 5),
		constant.MakeFromLiteral("123456789012345678901234567890", token.INT, 0),
		constant.MakeFromLiteral("-98765432109876543210", token.INT, 0),
		constant.MakeFromLiteral("2.5", token.FLOAT, 0),
		constant.MakeFromLiteral("-0.1", token.FLOAT, 0),
		constant.MakeFromLiteral("1e5000", token.FLOAT, 0),
		constant.MakeFromLiteral("1e-3000", token.FLOAT, 0),
		c1,
		constant.MakeUnknown(),
	}
	intOps := []token.Token{token.ADD, token.SUB, token.MUL, token.QUO, token.QUO_ASSIGN, token.REM, token.AND, token.OR, token.XOR, token.AND_NOT}
	fltOps := []token.Token{token.ADD, token.SUB, token.MUL, token.QUO}
	cmpOps := []token.Token{token.EQL, token.NEQ, token.LSS, token.LEQ, token.GTR, token.GEQ}
	for _, x := range nums {
		for _, y := range nums {
			bothInt := x.Kind() == constant.Int && y.Kind() == constant.Int
			anyCx := x.Kind() == constant.Complex || y.Kind() == constant.Complex
			ops := fltOps
			if bothInt {
				ops = intOps
			}
			for _, op := range ops {
				if (op == token.QUO || op == token.QUO_ASSIGN || op == token.REM) && constant.Sign(y) == 0 {
					continue
				}
				fmt.Printf("%s %s %s = %s\n", x, op, y, show(constant.BinaryOp(x, op, y)))
			}
			if anyCx {
				fmt.Printf("%s == %s: %v, != %v\n", x, y, constant.Compare(x, token.EQL, y), constant.Compare(x, token.NEQ, y))
			} else {
				res := []string{}
				for _, op := range cmpOps {
					res = append(res, fmt.Sprintf("%s:%v", op, constant.Compare(x, op, y)))
				}
				fmt.Printf("cmp %s %s: %s\n", x, y, strings.Join(res, " "))
			}
		}
	}

	// 1/3 stays a fraction; integer division forced
	third := constant.BinaryOp(constant.MakeInt64(1), token.QUO, constant.MakeInt64(3))
	describe("1/3", third)
	describe("1/3*3", constant.BinaryOp(third, token.MUL, constant.MakeInt64(3)))
	describe("10 QUO_ASSIGN 3", constant.BinaryOp(constant.MakeInt64(10), token.QUO_ASSIGN, constant.MakeInt64(3)))
	describe("-7 % 2", constant.BinaryOp(constant.MakeInt64(-7), token.REM, constant.MakeInt64(2)))
	describe("min / -1", constant.BinaryOp(constant.MakeInt64(math.MinInt64), token.QUO_ASSIGN, constant.MakeInt64(-1)))
	big1 := constant.MakeFromLiteral("1e5000", token.FLOAT, 0)
	describe("big/3", constant.BinaryOp(big1, token.QUO, constant.MakeInt64(3)))
	describe("big-big", constant.BinaryOp(big1, token.SUB, big1))
	describe("big*big", constant.BinaryOp(big1, token.MUL, constant.MakeFromLiteral("1e400000000", token.FLOAT, 0)))
	pi := constant.MakeFromLiteral("3.14159265358979323846264338327950288419716939937510582097494459", token.FLOAT, 0)
	sq := constant.BinaryOp(pi, token.MUL, pi)
	describe("pi*pi", sq)
	// ToInt rounding tolerance
	x3 := constant.BinaryOp(constant.MakeFromLiteral("1e5000", token.FLOAT, 0), token.QUO, constant.MakeFromLiteral("1e4999", token.FLOAT, 0))
	describe("1e5000/1e4999", x3)
	tenth := constant.BinaryOp(constant.MakeFromLiteral("1e-3000", token.FLOAT, 0), token.MUL, constant.MakeFromLiteral("1e3000", token.FLOAT, 0))
	describe("1e-3000*1e3000", tenth)

	// strings
	s1 := constant.MakeString("foo")
	s2 := constant.MakeString("bar")
	s3 := constant.BinaryOp(constant.BinaryOp(s1, token.ADD, s2), token.ADD, constant.MakeString("世界"))
	describe("concat", s3)
	for _, op := range cmpOps {
		fmt.Printf("str %s: %v %v %v\n", op, constant.Compare(s1, op, s2), constant.Compare(s1, op, s1), constant.Compare(s2, op, s3))
	}
	b1, b2 := constant.MakeBool(true), constant.MakeBool(false)
	fmt.Println("bool", constant.BinaryOp(b1, token.LAND, b2), constant.BinaryOp(b1, token.LOR, b2),
		constant.Compare(b1, token.EQL, b2), constant.Compare(b1, token.NEQ, b2), constant.UnaryOp(token.NOT, b2, 0))
	fmt.Println("unknown ops", constant.BinaryOp(b1, token.LAND, constant.MakeUnknown()), constant.Compare(constant.MakeUnknown(), token.EQL, b1),
		constant.UnaryOp(token.NOT, constant.MakeUnknown(), 0), constant.UnaryOp(token.XOR, constant.MakeUnknown(), 0))

	// unary
	uvals := []constant.Value{constant.MakeInt64(0), constant.MakeInt64(5), constant.MakeInt64(-1), constant.MakeInt64(math.MinInt64), constant.MakeInt64(math.MaxInt64),
		constant.MakeFromLiteral("123456789012345678901234567890", token.INT, 0), constant.MakeUint64(math.MaxUint64)}
	for _, v := range uvals {
		fmt.Printf("unary %s: -=%s +=%s ^=%s ^8=%s ^64=%s\n", v, show(constant.UnaryOp(token.SUB, v, 0)), show(constant.UnaryOp(token.ADD, v, 0)),
			show(constant.UnaryOp(token.XOR, v, 0)), show(constant.UnaryOp(token.XOR, v, 8)), show(constant.UnaryOp(token.XOR, v, 64)))
	}
	for _, v := range []constant.Value{constant.MakeFromLiteral("2.5", token.FLOAT, 0), big1, constant.MakeFloat64(1e300)} {
		fmt.Printf("unary %s: -=%s +=%s\n", v, show(constant.UnaryOp(token.SUB, v, 0)), show(constant.UnaryOp(token.ADD, v, 0)))
	}

	// shifts
	svals := []constant.Value{constant.MakeInt64(1), constant.MakeInt64(-5), constant.MakeInt64(math.MaxInt64), constant.MakeInt64(math.MinInt64),
		constant.MakeFromLiteral("123456789012345678901234567890", token.INT, 0), constant.MakeFromLiteral("-123456789012345678901234567890", token.INT, 0), constant.MakeUnknown()}
	for _, v := range svals {
		for _, s := range []uint{0, 1, 3, 62, 63, 64, 100} {
			fmt.Printf("shift %s %d: << %s >> %s\n", v, s, show(constant.Shift(v, token.SHL, s)), show(constant.Shift(v, token.SHR, s)))
		}
	}

	// kinds
	for k := constant.Unknown; k <= constant.Complex; k++ {
		fmt.Print(k, " ")
	}
	fmt.Println()
}
