package main

import (
	"encoding/asn1"
	"encoding/hex"
	"fmt"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"
)

type S1 struct {
	A int64
	B string
	C []byte
}

type S2 struct {
	A int                   `asn1:"optional,default:7"`
	B bool                  `asn1:"optional,explicit,tag:0"`
	C asn1.ObjectIdentifier `asn1:"tag:1,optional"`
	D time.Time             `asn1:"generalized"`
}

type IntSET []int

type S3 struct {
	Raw asn1.RawContent
	X   IntSET
	Y   asn1.RawValue
}

type S4 struct {
	E asn1.Enumerated `asn1:"explicit,tag:5"`
	F asn1.Flag       `asn1:"optional,explicit,tag:2"`
	G []string        `asn1:"omitempty"`
	H int32           `asn1:"application,tag:3,optional"`
	I *big.Int        `asn1:"private,tag:40"`
}

var addr = regexp.MustCompile(`0x[0-9a-f]+`)

func es(err error) string { return addr.ReplaceAllString(err.Error(), "0xADDR") }

func tm(t time.Time) string {
	_, off := t.Zone()
	return fmt.Sprintf("T(%d,%d,%d)", t.Unix(), t.Nanosecond(), off)
}

func bs(b asn1.BitString) string { return fmt.Sprintf("B(%s/%d)", hex.EncodeToString(b.Bytes), b.BitLength) }

func rv(r asn1.RawValue) string {
	return fmt.Sprintf("R(%d,%d,%v,%s,%s)", r.Class, r.Tag, r.IsCompound, hex.EncodeToString(r.Bytes), hex.EncodeToString(r.FullBytes))
}

func bi(b *big.Int) string {
	if b == nil {
		return "nil"
	}
	return b.String()
}

func anyS(x any) string {
	switch v := x.(type) {
	case nil:
		return "nil"
	case int64:
		return fmt.Sprintf("int64:%d", v)
	case bool:
		return fmt.Sprintf("bool:%v", v)
	case string:
		return "string:" + strconv.Quote(v)
	case []byte:
		return "bytes:" + hex.EncodeToString(v)
	case asn1.BitString:
		return bs(v)
	case asn1.ObjectIdentifier:
		return "oid:" + v.String()
	case time.Time:
		return tm(v)
	}
	return "?"
}

func ints[T ~int | ~int32](x []T) string {
	var s []string
	for _, v := range x {
		s = append(s, strconv.Itoa(int(v)))
	}
	return "[" + strings.Join(s, " ") + "]"
}

func strs(x []string) string {
	var s []string
	for _, v := range x {
		s = append(s, strconv.Quote(v))
	}
	return "[" + strings.Join(s, " ") + "]"
}

func pr(tag string, rest []byte, err error, f func() string) {
	if err != nil {
		fmt.Println("", tag, "err", es(err))
	} else {
		fmt.Println("", tag, f(), hex.EncodeToString(rest))
	}
}

func show(in []byte) {
	fmt.Println("IN", hex.EncodeToString(in))
	{
		var v int64
		r, e := asn1.Unmarshal(in, &v)
		pr("i64", r, e, func() string { return strconv.FormatInt(v, 10) })
	}
	{
		var v int32
		r, e := asn1.Unmarshal(in, &v)
		pr("i32", r, e, func() string { return strconv.Itoa(int(v)) })
	}
	{
		var v *big.Int
		r, e := asn1.Unmarshal(in, &v)
		pr("big", r, e, func() string { return bi(v) })
	}
	{
		var v bool
		r, e := asn1.Unmarshal(in, &v)
		pr("bool", r, e, func() string { return fmt.Sprint(v) })
	}
	{
		var v asn1.Enumerated
		r, e := asn1.Unmarshal(in, &v)
		pr("enum", r, e, func() string { return strconv.Itoa(int(v)) })
	}
	{
		var v asn1.BitString
		r, e := asn1.Unmarshal(in, &v)
		pr("bits", r, e, func() string { return bs(v) })
	}
	{
		var v asn1.ObjectIdentifier
		r, e := asn1.Unmarshal(in, &v)
		pr("oid", r, e, func() string { return v.String() })
	}
	{
		var v string
		r, e := asn1.Unmarshal(in, &v)
		pr("str", r, e, func() string { return strconv.Quote(v) })
	}
	{
		var v []byte
		r, e := asn1.Unmarshal(in, &v)
		pr("bytes", r, e, func() string { return hex.EncodeToString(v) })
	}
	{
		var v time.Time
		r, e := asn1.Unmarshal(in, &v)
		pr("time", r, e, func() string { return tm(v) })
	}
	{
		var v asn1.RawValue
		r, e := asn1.Unmarshal(in, &v)
		pr("raw", r, e, func() string { return rv(v) })
	}
	{
		var v any
		r, e := asn1.Unmarshal(in, &v)
		pr("any", r, e, func() string { return anyS(v) })
	}
	{
		var v []int
		r, e := asn1.Unmarshal(in, &v)
		pr("ints", r, e, func() string { return ints(v) })
	}
	{
		var v S1
		r, e := asn1.Unmarshal(in, &v)
		pr("s1", r, e, func() string { return fmt.Sprintf("%d %q %s", v.A, v.B, hex.EncodeToString(v.C)) })
	}
	{
		var v S2
		r, e := asn1.Unmarshal(in, &v)
		pr("s2", r, e, func() string { return fmt.Sprintf("%d %v %s %s", v.A, v.B, v.C.String(), tm(v.D)) })
	}
	{
		var v S3
		r, e := asn1.Unmarshal(in, &v)
		pr("s3", r, e, func() string { return fmt.Sprintf("%s %s %s", hex.EncodeToString(v.Raw), ints(v.X), rv(v.Y)) })
	}
	{
		var v S4
		r, e := asn1.Unmarshal(in, &v)
		pr("s4", r, e, func() string {
			return fmt.Sprintf("%d %v %s %d %s", v.E, v.F, strs(v.G), v.H, bi(v.I))
		})
	}
}

func m(x any, params string) []byte {
	b, err := asn1.MarshalWithParams(x, params)
	if err != nil {
		fmt.Println("M err", es(err))
		return nil
	}
	fmt.Println("M", hex.EncodeToString(b))
	return b
}

func main() {
	t1 := time.Date(2024, 2, 29, 13, 4, 5, 0, time.UTC)
	t2 := time.Date(1949, 12, 31, 23, 59, 59, 0, time.FixedZone("", -5*3600-30*60))
	t3 := time.Date(2051, 1, 2, 3, 4, 5, 123000000, time.FixedZone("", 3600))
	var bases [][]byte
	add := func(b []byte) {
		if b != nil {
			bases = append(bases, b)
		}
	}
	add(m(int64(0), ""))
	add(m(int64(-129), ""))
	add(m(int64(1<<40+5), ""))
	add(m(big.NewInt(-1), ""))
	n, _ := new(big.Int).SetString("-123456789012345678901234567890", 10)
	add(m(n, ""))
	n2, _ := new(big.Int).SetString("ff00000000000000000000", 16)
	add(m(n2, ""))
	add(m(true, ""))
	add(m(asn1.Enumerated(3), ""))
	add(m(asn1.BitString{Bytes: []byte{0xa0, 0x80}, BitLength: 9}, ""))
	add(m(asn1.ObjectIdentifier{1, 2, 840, 113549, 1, 1, 11}, ""))
	add(m(asn1.ObjectIdentifier{2, 999, 3}, ""))
	add(m("Hello World", ""))
	add(m("héllo", ""))
	add(m("a*b", ""))
	add(m("a&b", ""))
	add(m("abc", "ia5"))
	add(m("123 45", "numeric"))
	add(m("x", "utf8"))
	add(m([]byte{1, 2, 3}, ""))
	add(m(t1, ""))
	add(m(t2, ""))
	add(m(t3, ""))
	add(m(t1, "generalized"))
	add(m(time.Date(2024, 1, 2, 3, 4, 5, 500000000, time.UTC), "generalized"))
	add(m([]int{1, -2, 300}, ""))
	add(m(IntSET{300, 1, 2}, ""))
	add(m([]string{"a", "b"}, "set"))
	add(m(S1{A: 5, B: "hi", C: []byte{9}}, ""))
	add(m(S2{A: 7, B: true, C: asn1.ObjectIdentifier{1, 3}, D: t1}, ""))
	add(m(S2{A: 8, D: t2}, ""))
	add(m(S3{X: IntSET{3, 1}, Y: asn1.RawValue{Class: 2, Tag: 7, Bytes: []byte{1}}}, ""))
	add(m(S4{E: 2, F: true, G: []string{"x"}, H: 9, I: big.NewInt(1000)}, ""))
	add(m(S4{E: 1, I: big.NewInt(-5)}, ""))
	add(m(asn1.RawValue{Tag: 5}, ""))
	add(m(asn1.RawValue{Class: 1, Tag: 100, IsCompound: true, Bytes: []byte{5, 0}}, ""))
	add(m(int64(5), "explicit,tag:3"))
	add(m(int64(5), "tag:31"))
	add(m(int64(5), "application,tag:2"))
	add(m(int64(5), "private,explicit,tag:200"))
	add(m(asn1.Flag(true), "explicit,tag:2"))
	add(m(make([]byte, 200), ""))
	add([]byte{0x30, 0x80, 0, 0})
	add([]byte{0x1f, 0x1e, 0})
	add([]byte{0x13, 0x02, 'a', '\''})
	add([]byte{0x14, 0x02, 0xe9, 'a'})
	add([]byte{0x1e, 0x04, 0, 'a', 0xd8, 0})
	add([]byte{0x1e, 0x04, 0, 'a', 0, 0})
	add([]byte{0x17, 0x0d, '9', '9', '0', '2', '2', '9', '1', '2', '3', '4', '5', '6', 'Z'})
	add([]byte{0x17, 0x11, '5', '0', '0', '1', '0', '1', '0', '0', '0', '0', '+', '2', '4', '0', '0', '0', '0'})
	add([]byte{0x18, 0x0f, '2', '0', '2', '0', '1', '3', '0', '1', '0', '0', '0', '0', '0', '0', 'Z'})
	add([]byte{0x18, 0x13, '2', '0', '2', '0', '0', '1', '0', '1', '0', '0', '0', '0', '0', '0', '.', '1', '2', '0', 'Z'})
	add([]byte{0x17, 0x0b, '2', '0', '0', '1', '0', '1', '0', '0', '0', '0', 'Z'})
	add([]byte{0x02, 0x84, 0x00, 0x00, 0x00, 0x01, 0x05})
	add([]byte{0x02, 0x81, 0x01, 0x05})
	add([]byte{0x06, 0x03, 0x80, 0x01, 0x02})
	add([]byte{0x06, 0x06, 0x8f, 0xff, 0xff, 0xff, 0x7f, 0x01})
	add([]byte{0x05, 0x00, 0x05, 0x00})

	m(asn1.ObjectIdentifier{3, 1}, "")
	m(asn1.ObjectIdentifier{1}, "")
	m("a&b", "printable")
	m("é", "ia5")
	m("1a", "numeric")
	m(time.Date(10000, 1, 1, 0, 0, 0, 0, time.UTC), "")
	m(time.Date(2000, 1, 1, 0, 0, 0, 0, time.UTC), "utc,generalized")
	m(int64(1), "utf8")
	m(int64(1), "generalized")
	m(int64(1), "set")
	m([]int{}, "")
	m([]int{5}, "set")
	m(S2{}, "")
	m(S4{}, "")
	m((*big.Int)(nil), "")
	m(asn1.RawValue{FullBytes: []byte{1, 2, 3}}, "")
	m(int64(0), "optional")
	m(int64(7), "optional,default:7")
	m(int64(8), "optional,default:7")
	m("", "optional")
	m(asn1.BitString{Bytes: []byte{0xff}, BitLength: 8}, "")
	m(asn1.BitString{}, "")
	m(time.Time{}, "")
	m(asn1.Enumerated(-1), "tag:0")

	muts := []byte{0x00, 0x01, 0x05, 0x13, 0x30, 0x7f, 0x80, 0x81, 0xff, '+', 'Z', '5'}
	for _, b := range bases {
		show(b)
		for i := 0; i < len(b) && i < 24; i++ {
			show(append(append([]byte{}, b[:i]...), b[i+1:]...))
			for _, mm := range muts {
				r := append([]byte{}, b...)
				r[i] = mm
				show(r)
			}
		}
		show(append(append([]byte{}, b...), 0x05, 0x00))
	}
}
