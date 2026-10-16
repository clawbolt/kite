package main

import (
	"fmt"
	"math"
	"math/bits"
	"math/cmplx"
	"strconv"
)

func fe(x float64) string { return strconv.FormatFloat(x, 'e', 11, 64) }

func cs(z complex128) string { return "(" + fe(real(z)) + ", " + fe(imag(z)) + ")" }

var nz = math.Copysign(0, -1)
var inf = math.Inf(1)
var nan = math.NaN()

// copies of the unexported helpers in math/cmplx/tan.go
func reducePi(x float64) float64 {
	const reduceThreshold float64 = 1 << 30
	if math.Abs(x) < reduceThreshold {
		const (
			PI1 = 3.141592502593994
			PI2 = 1.5099578831723193e-07
			PI3 = 1.0780605716316238e-14
		)
		t := x / math.Pi
		t += 0.5
		t = float64(int64(t))
		return ((x - t*PI1) - t*PI2) - t*PI3
	}
	const (
		mask     = 0x7FF
		shift    = 64 - 11 - 1
		bias     = 1023
		fracMask = 1<<shift - 1
	)
	ix := math.Float64bits(x)
	exp := int(ix>>shift&mask) - bias - shift
	ix &= fracMask
	ix |= 1 << shift
	var mPi = [...]uint64{
		0x0000000000000000, 0x517cc1b727220a94, 0xfe13abe8fa9a6ee0, 0x6db14acc9e21c820,
		0xff28b1d5ef5de2b0, 0xdb92371d2126e970, 0x0324977504e8c90e, 0x7f0ef58e5894d39f,
		0x74411afa975da242, 0x74ce38135a2fbf20, 0x9cc8eb1cc1a99cfa, 0x4e422fc5defc941d,
		0x8ffc4bffef02cc07, 0xf79788c5ad05368f, 0xb69b3f6793e584db, 0xa7a31fb34f2ff516,
		0xba93dd63f5f2f8bd, 0x9e839cfbc5294975, 0x35fdafd88fc6ae84, 0x2b0198237e3db5d5,
	}
	digit, bitshift := uint(exp+64)/64, uint(exp+64)%64
	z0 := (mPi[digit] << bitshift) | (mPi[digit+1] >> (64 - bitshift))
	z1 := (mPi[digit+1] << bitshift) | (mPi[digit+2] >> (64 - bitshift))
	z2 := (mPi[digit+2] << bitshift) | (mPi[digit+3] >> (64 - bitshift))
	z2hi, _ := bits.Mul64(z2, ix)
	z1hi, z1lo := bits.Mul64(z1, ix)
	z0lo := z0 * ix
	lo, c := bits.Add64(z1lo, z2hi, 0)
	hi, _ := bits.Add64(z0lo, z1hi, c)
	lz := uint(bits.LeadingZeros64(hi))
	e := uint64(bias - (lz + 1))
	hi = (hi << (lz + 1)) | (lo >> (64 - (lz + 1)))
	hi >>= 64 - shift
	hi |= e << shift
	x = math.Float64frombits(hi)
	if x > 0.5 {
		x--
	}
	return math.Pi * x
}

func tanSeries(z complex128) float64 {
	const MACHEP = 1.0 / (1 << 53)
	x := math.Abs(2 * real(z))
	y := math.Abs(2 * imag(z))
	x = reducePi(x)
	x = x * x
	y = y * y
	x2 := 1.0
	y2 := 1.0
	f := 1.0
	rn := 0.0
	d := 0.0
	for {
		rn++
		f *= rn
		rn++
		f *= rn
		x2 *= x
		y2 *= y
		t := y2 + x2
		t /= f
		d += t
		rn++
		f *= rn
		rn++
		f *= rn
		x2 *= x
		y2 *= y
		t = y2 - x2
		t /= f
		d += t
		if !(math.Abs(t/d) > MACHEP) {
			break
		}
	}
	return d
}

func main() {
	generic := []complex128{
		complex(1, 2), complex(-3.5, 0.25), complex(0.5, -0.75), complex(2, -3), complex(-1, -1),
		complex(0.3, 0.4), complex(1.5, 0), complex(-2, 0), complex(0, 1.5), complex(0, -0.5),
		complex(0.25, 0), complex(10, 5), complex(-0.7, 3.2), complex(1e-5, 2e-5), complex(-6, -2.5),
		complex(0.8, 0.1), complex(3, 0.001), complex(-0.2, -1.7),
	}
	special := []complex128{
		complex(0, 0), complex(nz, 0), complex(0, nz), complex(nz, nz),
		complex(inf, 0), complex(-inf, 0), complex(inf, nz), complex(0, inf), complex(0, -inf), complex(nz, inf),
		complex(inf, inf), complex(-inf, -inf), complex(inf, -inf), complex(-inf, inf),
		complex(nan, 0), complex(0, nan), complex(nan, nan), complex(inf, nan), complex(-inf, nan),
		complex(nan, inf), complex(nan, -inf), complex(1, inf), complex(-1, -inf), complex(1, nan), complex(nan, 1),
		complex(inf, 1), complex(-inf, 1), complex(inf, -2), complex(-inf, -2), complex(0.5, 0), complex(0, 0.5),
		complex(nz, 0.5), complex(0.5, nz), complex(1, 0), complex(-1, 0), complex(0, 1), complex(0, -1),
	}
	type fn struct {
		name string
		f    func(complex128) complex128
	}
	fns := []fn{
		{"Sqrt", cmplx.Sqrt}, {"Exp", cmplx.Exp}, {"Log", cmplx.Log}, {"Log10", cmplx.Log10}, {"Conj", cmplx.Conj},
		{"Sin", cmplx.Sin}, {"Cos", cmplx.Cos}, {"Tan", cmplx.Tan}, {"Cot", cmplx.Cot},
		{"Sinh", cmplx.Sinh}, {"Cosh", cmplx.Cosh}, {"Tanh", cmplx.Tanh},
		{"Asin", cmplx.Asin}, {"Acos", cmplx.Acos}, {"Atan", cmplx.Atan},
		{"Asinh", cmplx.Asinh}, {"Acosh", cmplx.Acosh}, {"Atanh", cmplx.Atanh},
	}
	all := append(append([]complex128{}, generic...), special...)
	for _, z := range all {
		r, th := cmplx.Polar(z)
		fmt.Println("z", cs(z), "abs", fe(cmplx.Abs(z)), "phase", fe(cmplx.Phase(z)), "polar", fe(r), fe(th),
			"isinf", cmplx.IsInf(z), "isnan", cmplx.IsNaN(z))
		for _, f := range fns {
			fmt.Println("  "+f.name, cs(f.f(z)))
		}
	}
	// arithmetic
	ops := []complex128{
		complex(1, 2), complex(-3.5, 0.25), complex(0.5, -0.75), complex(0, 0), complex(nz, 0), complex(4, 0),
		complex(0, -2), complex(inf, 1), complex(1, inf), complex(inf, inf), complex(nan, 1), complex(nan, nan),
		complex(1e300, 1e300), complex(1e-300, 2e-300), complex(-inf, 0), complex(3, nan),
	}
	// a op a is skipped: with FMA fusion (arm64) Go's a*a and a/a differ from unfused results
	for i, a := range ops {
		for j, b := range ops {
			if i == j {
				continue
			}
			fmt.Println(cs(a), cs(b), "add", cs(a+b), "sub", cs(a-b), "mul", cs(a*b), "div", cs(a/b))
		}
	}
	// Pow
	pows := []complex128{complex(0, 0), complex(nz, 0), complex(1, 2), complex(-2, 0), complex(0.5, -1.5), complex(3, 0),
		complex(0, 1), complex(-1, 0), complex(2, 0.5), complex(0, -2), complex(nan, 0), complex(inf, 0), complex(-0.5, 0)}
	for _, a := range pows {
		for _, b := range pows {
			fmt.Println("Pow", cs(a), cs(b), cs(cmplx.Pow(a, b)))
		}
	}
	// Rect
	for _, p := range [][2]float64{{1, 0}, {2, 1}, {3, -2.5}, {0.5, 3}, {0, 1}, {inf, 0}, {1, inf}, {nan, 1}, {-2, 0.75}, {1e10, 1e-3}} {
		fmt.Println("Rect", fe(p[0]), fe(p[1]), cs(cmplx.Rect(p[0], p[1])))
	}
	// near poles: exercise tanSeries inside Tan / Cot
	for _, z := range []complex128{complex(1.5707963267948966, 0.001), complex(1.57, -0.002), complex(0.001, 0.002),
		complex(-0.003, 0.0005), complex(3.1415, 0.01), complex(-1.5707, 0.05)} {
		fmt.Println("pole", cs(z), "Tan", cs(cmplx.Tan(z)), "Cot", cs(cmplx.Cot(z)), "series", fe(tanSeries(z)))
	}
	// reducePi
	for _, x := range []float64{0, 0.5, 3, 100, -7, 12345.678, 1073741823, 1073741824, 1e10, 3e15, 1.2345e20, 4.5e30,
		1e100, 1.5e300, 1.7976931348623157e308, 9.223372036854776e18, 1.4411518807585587e17} {
		fmt.Println("reducePi", fe(x), fe(reducePi(x)))
	}
	for _, z := range []complex128{complex(1e10, 0.01), complex(4.5e30, 0.1), complex(1e100, 0.3)} {
		fmt.Println("series", cs(z), fe(tanSeries(z)))
	}
	// Inf / NaN / String
	fmt.Println(cs(cmplx.Inf()), cs(cmplx.NaN()), cmplx.IsInf(cmplx.Inf()), cmplx.IsNaN(cmplx.NaN()), cmplx.IsNaN(complex(inf, nan)))
	for _, z := range []complex128{complex(1, 2), complex(-1.5, nz), complex(nz, 0), complex(1e21, 1e-7), complex(123456789, 0.1),
		complex(inf, inf), complex(-inf, nan), complex(nan, -inf), complex(nan, nan), complex(0.1, -0.2), complex(5e-324, 1e6),
		complex(100000, 1e5+0.5), cmplx.Conj(complex(3, 4))} {
		fmt.Println(fmt.Sprint(z))
	}
	fmt.Println(complex(1, 2) == complex(1, 2), complex(0, 0) == complex(nz, nz), complex(nan, 0) == complex(nan, 0))
}
