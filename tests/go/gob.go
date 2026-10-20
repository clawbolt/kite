// 对拍 Go 1.27 encoding/gob：cd tests/go && go run gob.go > gob.expected
package main

import (
	"bytes"
	"encoding/gob"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"reflect"
	"sort"
	"strconv"
	"strings"
)

type Inner struct {
	X int
	Y string
}

type T struct {
	A int
	B string
	C []int
	D map[string]int
	E [2]bool
	F *Inner
	G Inner
	H float32
	I any
	J []Inner
	k int
	L uint8
	M complex128
	N []byte
}

type P struct{ X, Y int }

type Q struct{ Z string }

type Holder struct {
	Name string
	V    any
}

type Node struct {
	V    int
	Next *Node
}

type IntList []int

type Named struct {
	L  IntList
	M  map[string][]int
	SS [][]string
	PP []*P
	A  [3]uint16
}

// GobEncoder
type G struct{ raw []byte }

func (g G) GobEncode() ([]byte, error) { return g.raw, nil }

func (g *G) GobDecode(b []byte) error {
	g.raw = append([]byte(nil), b...)
	return nil
}

type WithG struct {
	A  int
	GG G
	GP *G
}

type T1 struct {
	A int
	B string
	C []int
}

type T2 struct {
	B string
	C int
}

type T3 struct {
	X int
	B string
}

type T4 struct{ X, Y int }

type S8 struct{ A int8 }

type SF struct{ F []float32 }

type U struct{ a, b int }

type Empty struct{}

type PtrSlice struct{ P []*int }

type Box struct{ B any }

// BinaryMarshaler
type Bin struct{ raw []byte }

func (b Bin) MarshalBinary() ([]byte, error) { return b.raw, nil }

func (b *Bin) UnmarshalBinary(x []byte) error {
	b.raw = append([]byte(nil), x...)
	return nil
}

type WithBin struct {
	N int
	B Bin
}

func show(v reflect.Value) string {
	switch v.Kind() {
	case reflect.Invalid:
		return "nil"
	case reflect.Pointer:
		if v.IsNil() {
			return "nil"
		}
		if v.Type() == reflect.TypeFor[*G]() {
			return "b" + hex.EncodeToString(v.Elem().Field(0).Bytes())
		}
		return show(v.Elem())
	case reflect.Interface:
		if v.IsNil() {
			return "nil"
		}
		return "I(" + show(v.Elem()) + ")"
	case reflect.Bool:
		return strconv.FormatBool(v.Bool())
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		return strconv.FormatInt(v.Int(), 10)
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Uintptr:
		return strconv.FormatUint(v.Uint(), 10)
	case reflect.Float32, reflect.Float64:
		return strconv.FormatFloat(v.Float(), 'g', -1, 64)
	case reflect.Complex64, reflect.Complex128:
		c := v.Complex()
		return "c(" + strconv.FormatFloat(real(c), 'g', -1, 64) + "," + strconv.FormatFloat(imag(c), 'g', -1, 64) + ")"
	case reflect.String:
		return strconv.Quote(strings.ToValidUTF8(v.String(), "\uFFFD"))
	case reflect.Slice, reflect.Array:
		if v.Type().Elem().Kind() == reflect.Uint8 && v.Kind() == reflect.Slice {
			return "b" + hex.EncodeToString(v.Bytes())
		}
		var parts []string
		for i := 0; i < v.Len(); i++ {
			parts = append(parts, show(v.Index(i)))
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case reflect.Map:
		var parts []string
		for _, k := range v.MapKeys() {
			parts = append(parts, show(k)+":"+show(v.MapIndex(k)))
		}
		sort.Strings(parts)
		return "map[" + strings.Join(parts, ", ") + "]"
	case reflect.Struct:
		if v.Type() == reflect.TypeFor[G]() || v.Type() == reflect.TypeFor[Bin]() {
			return "b" + hex.EncodeToString(v.Field(0).Bytes())
		}
		var parts []string
		for i := 0; i < v.NumField(); i++ {
			parts = append(parts, show(v.Field(i)))
		}
		return "{" + strings.Join(parts, ", ") + "}"
	}
	return "?"
}

func errs(err error) string {
	if err == nil {
		return "ok"
	}
	return "err " + err.Error()
}

// 编码（捕获 panic）
func enc(e *gob.Encoder, v any) (err error) {
	defer func() {
		if r := recover(); r != nil {
			err = errors.New(fmt.Sprint(r))
		}
	}()
	return e.Encode(v)
}

// 一个值：编码、十六进制、解码回 out
func round[X any](label string, v X) {
	var buf bytes.Buffer
	e := gob.NewEncoder(&buf)
	err := enc(e, v)
	fmt.Printf("%s enc %s %s\n", label, errs(err), hex.EncodeToString(buf.Bytes()))
	var out X
	d := gob.NewDecoder(bytes.NewReader(buf.Bytes()))
	err = d.Decode(&out)
	if err != nil {
		fmt.Printf("%s dec %s\n", label, errs(err))
		return
	}
	fmt.Printf("%s dec ok %s\n", label, show(reflect.ValueOf(&out).Elem()))
}

// 把 data 解码成 X
func into[X any](label string, data []byte) {
	var out X
	err := gob.NewDecoder(bytes.NewReader(data)).Decode(&out)
	if err != nil {
		fmt.Printf("%s %s\n", label, errs(err))
		return
	}
	fmt.Printf("%s ok %s\n", label, show(reflect.ValueOf(&out).Elem()))
}

func encBytes(v any) []byte {
	var buf bytes.Buffer
	if err := gob.NewEncoder(&buf).Encode(v); err != nil {
		panic(err)
	}
	return buf.Bytes()
}

func main() {
	gob.Register(P{})
	gob.Register(&Q{})
	gob.RegisterName("node", Node{})
	gob.Register(Box{})

	round("int", 7)
	round("int-neg", -1)
	round("int-min", math.MinInt64)
	round("int8", int8(-100))
	round("uint", uint64(1<<63+5))
	round("uint-small", uint(200))
	round("float", 1.5)
	round("float-neg-zero", math.Copysign(0, -1))
	round("float32", float32(0.1))
	round("complex", complex(1.5, -2))
	round("string", "héllo, 世界")
	round("string-empty", "")
	round("bytes", []byte{0, 1, 2, 255})
	round("bool-true", true)
	round("bool-false", false)
	round("slice", []int{1, -2, 300})
	round("slice-empty", []int{})
	round("strings", []string{"a", "", "c"})
	round("array", [3]int{0, 5, 0})
	round("map", map[string]int{"k": 9})
	round("map-empty", map[int]string{})
	round("ptr", &Inner{X: 3, Y: "y"})
	round("inner", Inner{})
	round("struct-zero", T{})
	round("struct", T{
		A: -5, B: "bee", C: []int{1, 2}, D: map[string]int{"one": 1}, E: [2]bool{false, true},
		F: &Inner{X: 1}, G: Inner{Y: "g"}, H: 2.5, I: P{3, 4}, J: []Inner{{X: 7}, {}}, k: 99, L: 200,
		M: complex(0, 1), N: []byte("nn"),
	})
	round("holder-p", Holder{Name: "p", V: P{1, 2}})
	round("holder-qptr", Holder{Name: "q", V: &Q{Z: "zz"}})
	round("holder-int", Holder{V: 42})
	round("holder-nested", Holder{Name: "outer", V: Holder{Name: "inner", V: P{5, 6}}})
	round("holder-slice", Holder{V: []any{1, "s", P{}, nil, []int{1}}})
	round("anys", []any{P{1, 1}, &Q{"q"}, nil, Holder{V: P{2, 2}}})
	round("node", Node{V: 1, Next: &Node{V: 2, Next: &Node{V: 3}}})
	round("holder-node", Holder{V: Node{V: 8}})
	round("named", Named{
		L: IntList{4, 5}, M: map[string][]int{"m": {1}}, SS: [][]string{{"a"}, {}, {"b", "c"}},
		PP: []*P{{1, 2}}, A: [3]uint16{1, 0, 65535},
	})
	round("intlist", IntList{9, 8})
	round("withg", WithG{A: 1, GG: G{raw: []byte{1, 2, 3}}, GP: &G{raw: []byte("p")}})
	round("withg-zero", WithG{})
	round("g", G{raw: []byte{7}})
	round("empty", Empty{})
	round("u", U{1, 2})
	round("ptrslice", PtrSlice{P: []*int{nil}})
	round("holder-unreg", Holder{V: T4{1, 2}})
	round("big-uint8s", []uint8{})
	round("nested-maps", map[string]map[string]int{"a": {"b": 1}})

	round("uintptr", uintptr(77))
	round("complex64", complex64(complex(1, 2)))
	round("i16s", []int16{-1, 32767})
	round("u32arr", [2]uint32{1, 4294967295})
	round("f32s", []float32{0.5, -1})
	round("c64s", []complex64{1, complex(0, -3)})
	round("c128s", []complex128{complex(2, 2)})
	round("bools", []bool{true, false})
	round("uints", []uint{0, 1<<64 - 1})
	round("uptrs", []uintptr{3})
	round("i8s", []int8{-128, 127})
	round("i32s", []int32{-5})
	round("i64s", []int64{1 << 40})
	round("u16s", []uint16{9})
	round("u64s", []uint64{1 << 63})
	round("f64s", []float64{math.Inf(1), math.Inf(-1), 1e-310})
	round("mapstruct", map[int]P{1: {1, 2}})
	round("arrstruct", [2]Inner{{X: 1}, {}})
	round("iface-ints", Holder{V: []int{1, 2}})
	round("iface-map", Holder{V: map[string]int{"a": 1}})
	round("iface-bytes", Holder{V: []byte{1}})
	round("iface-string", Holder{V: "x"})
	round("iface-float", Holder{V: 1.5})
	round("iface-uint8", Holder{V: uint8(3)})
	round("iface-bool", Holder{V: false})
	round("iface-complex", Holder{V: complex64(1)})
	round("iface-strings", Holder{V: []string{"p", "q"}})
	round("box-nested", Holder{Name: "h", V: Box{B: Box{B: P{7, 8}}}})
	round("box-mixed", []Box{{B: P{1, 2}}, {B: &Q{"z"}}, {B: Box{B: 3}}, {}})
	round("withbin", WithBin{N: 2, B: Bin{raw: []byte("bin")}})
	round("bin", Bin{raw: []byte{9, 9}})

	// 同一个编码器连续编码：类型只发一次
	{
		var buf bytes.Buffer
		e := gob.NewEncoder(&buf)
		fmt.Println("multi", errs(enc(e, P{1, 2})), errs(enc(e, P{3, 4})), errs(enc(e, Holder{V: P{5, 6}})), errs(enc(e, 5)))
		fmt.Println("multi", hex.EncodeToString(buf.Bytes()))
		d := gob.NewDecoder(bytes.NewReader(buf.Bytes()))
		var p1, p2 P
		var h Holder
		var n int
		fmt.Println("multi", errs(d.Decode(&p1)), show(reflect.ValueOf(p1)), errs(d.Decode(&p2)), show(reflect.ValueOf(p2)),
			errs(d.Decode(&h)), show(reflect.ValueOf(h)), errs(d.Decode(&n)), n, errs(d.Decode(&n)))
		d = gob.NewDecoder(bytes.NewReader(buf.Bytes()))
		fmt.Println("ignore", errs(d.Decode(nil)), errs(d.Decode(nil)), errs(d.Decode(nil)), errs(d.Decode(&n)), n)
	}

	// 解码到别的类型
	t1 := encBytes(T1{A: 300, B: "b", C: []int{1, 200}})
	into[T1]("t1", t1)
	into[T2]("t2", t1)
	into[T3]("t3", t1)
	into[T4]("t4", t1)
	into[int]("t1-int", t1)
	into[any]("t1-any", t1)
	into[S8]("s8", t1)
	into[struct{ C []int8 }]("c8", t1)
	into[struct{ C []uint8 }]("cbytes", t1)
	into[struct{ C [2]int }]("carray", t1)
	into[struct{ B []byte }]("bbytes", t1)
	into[struct{ A uint }]("auint", t1)
	into[struct{ A *int }]("aptr", t1)
	into[struct{ A **int }]("aptr2", t1)
	into[int8]("int-int8", encBytes(300))
	into[uint8]("uint-uint8", encBytes(uint(256)))
	into[uint16]("uint-uint16", encBytes(uint(65535)))
	into[float32]("float-float32", encBytes(1e300))
	into[float32]("float-float32-ok", encBytes(1e30))
	into[SF]("sf", encBytes(struct{ F []float64 }{[]float64{1, 1e100}}))
	into[string]("int-string", encBytes(1))
	into[[]string]("ints-strings", encBytes([]int{1}))
	into[map[string]int]("map-map", encBytes(map[string]int{"x": 1}))
	into[map[string]int8]("map-int8", encBytes(map[string]int{"x": 1000}))
	into[[2]int]("array-len", encBytes([3]int{1, 2, 3}))
	into[[3]int]("array-ok", encBytes([3]int{1, 2, 3}))
	into[Holder]("holder-p", encBytes(Holder{V: P{1, 2}}))
	into[Inner]("holder-inner", encBytes(Holder{V: P{1, 2}}))
	into[P]("p-from-holder", encBytes(Holder{V: P{1, 2}}))
	into[any]("any-from-int", encBytes(1))
	into[G]("g-from-int", encBytes(1))
	into[WithG]("withg-t1", t1)
	into[Node]("node-node", encBytes(Node{V: 1, Next: &Node{V: 2}}))
	into[Empty]("empty-t1", t1)
	into[struct{ Next int }]("next-int", encBytes(Node{V: 1, Next: &Node{V: 2}}))

	// 坏数据
	into[T1]("empty-stream", nil)
	for n := 1; n < len(t1); n += 3 {
		into[T1](fmt.Sprintf("trunc %d", n), t1[:n])
	}
	for i := 0; i < len(t1); i++ {
		for _, x := range []byte{0x01, 0x80, 0xff} {
			bad := append([]byte(nil), t1...)
			bad[i] ^= x
			into[T1](fmt.Sprintf("flip %d %02x", i, x), bad)
		}
	}
	h := encBytes(Holder{Name: "n", V: P{1, 2}})
	for i := 0; i < len(h); i++ {
		bad := append([]byte(nil), h...)
		bad[i] ^= 0x04
		into[Holder](fmt.Sprintf("hflip %d", i), bad)
	}
	streams := []struct {
		name string
		data []byte
		dec  func([]byte) (string, error)
	}{
		{"holder", encBytes(Holder{Name: "x", V: Box{B: P{1, 2}}}), func(b []byte) (string, error) {
			var x Holder
			err := gob.NewDecoder(bytes.NewReader(b)).Decode(&x)
			return show(reflect.ValueOf(x)), err
		}},
		{"node", encBytes(Node{V: 5, Next: &Node{V: -6}}), func(b []byte) (string, error) {
			var x Node
			err := gob.NewDecoder(bytes.NewReader(b)).Decode(&x)
			return show(reflect.ValueOf(x)), err
		}},
		{"named", encBytes(Named{L: IntList{1}, M: map[string][]int{"k": {2, 3}}, SS: [][]string{{"s"}}, PP: []*P{{4, 5}}, A: [3]uint16{6, 7, 8}}),
			func(b []byte) (string, error) {
				var x Named
				err := gob.NewDecoder(bytes.NewReader(b)).Decode(&x)
				return show(reflect.ValueOf(x)), err
			}},
		{"withg", encBytes(WithG{A: 1, GG: G{raw: []byte{1, 2}}, GP: &G{raw: []byte{3}}}), func(b []byte) (string, error) {
			var x WithG
			err := gob.NewDecoder(bytes.NewReader(b)).Decode(&x)
			return show(reflect.ValueOf(x)), err
		}},
	}
	for _, st := range streams {
		for i := 0; i < len(st.data); i++ {
			for _, x := range []byte{0x01, 0x40} {
				bad := append([]byte(nil), st.data...)
				bad[i] ^= x
				out, err := st.dec(bad)
				ig := gob.NewDecoder(bytes.NewReader(bad)).Decode(nil)
				if err != nil {
					fmt.Printf("%s %d %02x err %s / %s\n", st.name, i, x, err, errs(ig))
				} else {
					fmt.Printf("%s %d %02x ok %s / %s\n", st.name, i, x, out, errs(ig))
				}
			}
		}
	}
	into[int]("bad-uint", []byte{0xf0, 1, 2})
	into[int]("huge-len", []byte{0xfc, 0x7f, 0xff, 0xff, 0xff})
	into[int]("unknown-id", []byte{0x03, 0x7e, 0x00, 0x02})
	into[int]("dup-type", append(append([]byte(nil), t1[:t1[0]+1]...), t1...))
	into[int]("low-type", []byte{0x03, 0x05, 0x01, 0x00})
	into[int]("nonzero-single", []byte{0x03, 0x04, 0x01, 0x02})
	into[int]("extra-after-type", append([]byte{t1[0] + 1}, append(t1[1:t1[0]+1], 0x00)...))
	into[int]("zero-len", []byte{0x00})
}
