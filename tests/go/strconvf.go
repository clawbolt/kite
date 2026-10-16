package main

import (
	"fmt"
	"math"
	"strconv"
	"strings"
	"unicode/utf8"
)

func L(b []byte) string {
	s := []string{}
	for _, x := range b {
		s = append(s, fmt.Sprint(x))
	}
	return "[" + strings.Join(s, ", ") + "]"
}

func B(b bool) string {
	if b {
		return "true"
	}
	return "false"
}

func H(f float64) string {
	if f != f {
		return "nan"
	}
	return strconv.FormatUint(math.Float64bits(f), 16)
}

func fl(f float64, bs int) string {
	if bs == 64 {
		return H(f)
	}
	return strconv.FormatFloat(f, 'g', -1, bs) + " " + H(f)
}

func main() {
	vals := []float64{0.0, math.Copysign(0, -1), 1.0, -1.0, 0.1, 0.2, 0.3, 0.30000000000000004, 0.5, 1.5, 2.5, 0.125, 0.0625, 1e21, 1e20, 1e-7, 1e-6, 1e-5, 1e23, 123456789.0, 1234567890123456789.0, 5e-324, 2.2250738585072014e-308, 2.225073858507201e-308, 1.7976931348623157e308, 3.4028234663852886e38, 3.4028235677973366e38, 1.401298464324817e-45, 7e-46, 1.1754943508222875e-38, 16777216.0, 16777217.0, 9007199254740992.0, 9007199254740993.0, 1024.0, 0.0009765625, 1.2676506002282294e30, 7.888609052210118e-31, 4294967296.0, 1e100, 1e-100, 100.0, 1000000.0, 1e15, 1e16, 1e10, 3.141592653589793, -2.718281828459045, 999999.9999, 0.000123456, 6.02214076e23, -273.15, 0.95, 0.05, 1.005, 2.675, 9.5, 99.5, 0.9999999999999999, 123.456, math.NaN(), math.Inf(1), math.Inf(-1)}
	precs := []int{-1, 0, 1, 3, 17, 30}
	extra := []int{2, 6, 16, 21}
	for _, v := range vals {
		fmt.Println("v", H(v), strconv.FormatUint(uint64(math.Float32bits(float32(v))), 16))
		for _, c := range "eEfgGbxX" {
			parts := []string{}
			for _, p := range precs {
				parts = append(parts, strconv.FormatFloat(v, byte(c), p, 64))
			}
			fmt.Println(64, string(c), strings.Join(parts, " "))
		}
		p32 := []string{}
		for _, c := range "efgbx" {
			for _, p := range []int{-1, 3} {
				p32 = append(p32, strconv.FormatFloat(v, byte(c), p, 32))
			}
		}
		fmt.Println(32, strings.Join(p32, " "))
		if v != v || math.Abs(v) > 1e30 || (v != 0 && math.Abs(v) < 1e-30) {
			continue
		}
		parts := []string{}
		for _, c := range "egf" {
			for _, p := range extra {
				parts = append(parts, strconv.FormatFloat(v, byte(c), p, 64))
			}
		}
		fmt.Println("x", strings.Join(parts, " "))
	}
	fmt.Println(strconv.FormatFloat(3, 'q', -1, 64), strconv.FormatFloat(1.5, 'v', 2, 64))
	parses := []string{"0", "-0", "+0", "1", "-1", "1.5", ".5", "5.", ".", "", "x", "-", "+", "1e", "1e+", "1e-", "1e5", "1E-5", "1e400", "-1e400", "1e-400", "-1e-400", "inf", "+Inf", "-inf", "-infinity", "INFINITY", "infin", "infinityx", "Inf1", "nan", "NaN", "NAN", "-nan", "+nan", "nanx", "0x1p-2", "0X1.8P+1", "0x1.fffffffffffffp1023", "0x1.fffffffffffff8p1023", "0x1p1024", "-0x1p1024", "0x1p-1074", "0x1p-1075", "0x1.8p-1074", "0x1p-2000", "0x1", "0x", "0x.8p1", "0x1.p0", "0xp1", "0x1_0p0", "0x_1p0", "0x1p+1_0", "0xABCDEFp-10", "0x123456789abcdef0123p0", "1_000.5", "1__0", "_1", "1_", "1_e5", "1e1_0", "0_1", "0b101", "0o7", "1.2.3", "--1", "+-1", " 1", "1 ", "1e10000000", "0e99999", "0.1", "0.30000000000000004", "4.9406564584124654e-324", "2.4703282292062327e-324", "2.4703282292062328e-324", "1.7976931348623157e308", "1.7976931348623158e308", "1.7976931348623159e308", "179769313486231580793728971405301e276", "123456789012345678901234567890", "3.4028235e38", "3.4028236e38", "3.5e38", "1e-46", "7.006492321624085e-46", "7.006492321624086e-46", "1.1754943508222875e-38", "0x1.fffffep127", "0x1.ffffffp127", "0x1p128", "0x1p-149", "0x1p-150", "16777217", "100000000000000016777215", "0.111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111", "10000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000e-400", "0.0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000001e330", "999999999999999999999999999999.999999999999999999999999999999", "2.2250738585072011e-308", "2.2250738585072012e-308", "a\U00000009b", "\U00000661", "1\U000000e9", "1e+308", "1e-0", "000000.000001"}
	for _, s := range parses {
		for _, bs := range []int{64, 32} {
			f, err := strconv.ParseFloat(s, bs)
			e := ""
			if err != nil {
				e = err.Error()
			}
			fmt.Println("p", bs, strconv.Quote(s), fl(f, bs), e)
		}
	}
	qs := []string{"", "hello", "a\"b\\c", "\U00000007\U00000008\U0000000c\U0000000a\U0000000d\U00000009\U0000000b", "\U00000000\U00000001\U0000001f\U0000007f", "\U000065e5\U0000672c\U00008a9e", "\U000000a0nbsp", "\U00002028", "\U0000feffbom", "\U0000263a", "\U0001f600", "\U000000adshy", "e\U00000301", "`back`", "\U0000200bzw", "\U00003000", "'single'", "\U000e0001", "\U0010ffff", "\U00000378", "\U0000e000", "\U00001680\U00002000\U0000200a\U0000202f\U0000205f", "tab\U00000009here", "\U00000080\U0000009f", "\U000000ff\U00000100", "\U0001d11e", "{x}"}
	for _, s := range qs {
		fmt.Println("q", strconv.Quote(s), strconv.QuoteToASCII(s), strconv.QuoteToGraphic(s), B(strconv.CanBackquote(s)))
	}
	qb := [][]byte{{255}, {97, 255, 98}, {192, 128}, {237, 160, 128}, {226, 130}, {244, 144, 128, 128}, {230, 151, 165, 254}}
	for _, b := range qb {
		s := string(b)
		fmt.Println("qb", L(b), strconv.Quote(s), strconv.QuoteToASCII(s), strconv.QuoteToGraphic(s))
	}
	runes := []rune{0, 7, 9, 10, 31, 32, 39, 34, 92, 65, 96, 126, 127, 128, 159, 160, 173, 255, 256, 888, 768, 5760, 8192, 8203, 8232, 12288, 9786, 55296, 57343, 65279, 65533, 65535, 65536, 128512, 917505, 1114111, 1114112, -1, 2147483647, 57344, 119070}
	for _, r := range runes {
		fmt.Println("r", r, strconv.QuoteRune(r), strconv.QuoteRuneToASCII(r), strconv.QuoteRuneToGraphic(r), B(strconv.IsPrint(r)), B(strconv.IsGraphic(r)))
	}
	unq := []string{"\"abc\"", "'a'", "'\\n'", "''", "'ab'", "'\\''", "'\"'", "'\\\"'", "\"\\x41\\101\\u0041\\U00000041\"", "\"\\xff\"", "\"\\377\\400\"", "\"\\u00e9\\U0001F600\"", "\"\\ud800\"", "\"\\U00110000\"", "\"\\q\"", "\"a\U0000000ab\"", "`a\U0000000ab`", "`a\U0000000d\U0000000ab`", "`a`b`", "\"abc", "abc\"", "\"", "`", "\"\\a\\b\\f\\n\\r\\t\\v\\\\\\\"\"", "'\\a'", "\"\\'\"", "\"'\"", "\"\U000065e5\U0000672c\"", "'\U000065e5'", "'\U0001f600'", "\"abc\"def\"", "\"\\x4\"", "\"\\u12\"", "\"\\8\"", "\"\\0\"", "\"\\00\"", "\"\\000\"", "x", "", "'\\u263a'", "'\\xff'", "\"\\\"", "\"\\\\\"", "`\\n`"}
	for _, s := range unq {
		b, err := strconv.Unquote(s)
		if err != nil {
			fmt.Println("u", strconv.Quote(s), "err", err.Error(), "err", err.Error())
		} else if !utf8.ValidString(b) {
			fmt.Println("u", strconv.Quote(s), "ok", L([]byte(b)), "err invalid syntax")
		} else {
			fmt.Println("u", strconv.Quote(s), "ok", L([]byte(b)), "ok", strconv.Quote(b))
		}
	}
	uc := []string{"\\n rest", "a", "\U000065e5x", "\\x41z", "\\u263a", "\\'", "\\'", "\\\"", "\\\"", "'", "\"", "\"", "\\377", "\\400", "\\U0001F600!", "\\", "", "\\z", "`", "\\`"}
	ucq := []byte{34, 34, 34, 34, 39, 39, 34, 34, 39, 39, 34, 39, 34, 34, 96, 34, 34, 34, 96, 96}
	for i, s := range uc {
		v, mb, tail, err := strconv.UnquoteChar(s, ucq[i])
		if err != nil {
			fmt.Println("uc", strconv.Quote(s), ucq[i], "err", err.Error())
		} else {
			fmt.Println("uc", strconv.Quote(s), ucq[i], v, B(mb), strconv.Quote(tail))
		}
	}
	qp := []string{"\"abc\"rest", "'a'b", "`raw`x", "\"\\\"q\\\"\"tail", "\"unterminated", "'ab'", "nope", "\"a\U0000000ab\"", "\"\"", "``", "`\U0000000d`z"}
	for _, s := range qp {
		p, err := strconv.QuotedPrefix(s)
		if err != nil {
			fmt.Println("qp", strconv.Quote(s), "err", err.Error())
		} else {
			fmt.Println("qp", strconv.Quote(s), "ok", strconv.Quote(p))
		}
	}
	bq := []string{"", "abc", "a`b", "a\U00000009b", "a\U0000000ab", "\U0000feff", "\U000065e5\U0000672c", "\U0000007f", "\U000000a0"}
	for _, s := range bq {
		fmt.Println("bq", strconv.Quote(s), B(strconv.CanBackquote(s)))
	}
	us := []uint64{0, 1, 255, ^uint64(0), ^uint64(1), 1 << 63, 9223372036854775807, ^uint64(999)}
	for _, u := range us {
		fmt.Println("fu", strconv.FormatUint(u, 2), strconv.FormatUint(u, 8), strconv.FormatUint(u, 10), strconv.FormatUint(u, 16), strconv.FormatUint(u, 36))
	}
	for _, v := range vals {
		f32 := float64(float32(v))
		fmt.Println("f32", H(f32), H(math.Float64frombits(math.Float64bits(v))))
	}
}
