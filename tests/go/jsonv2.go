package main

import (
	"encoding/hex"
	"encoding/json/jsontext"
	"encoding/json/v2"
	"fmt"
)

var bases = []string{
	`{"a":1}`, `[1,2.5,-3e10,true,false,null]`, `"he\"llo\u00e9\n"`, `{"k":[{},[]],"z":"x"}`, ` 0 `, `-0.5E+3`,
	`{"a" : [ 1 , { "b" : null } ] }`, `"\ud83d\ude00 \ud800"`, `[[[]]]`, `{"":""}`, `"é<&>\u2028"`, `123`, `true`,
	"\"\xff\xfe\"", `{"a":1,"a":2}`, `[1e400]`, `"\/\b\f\r\t"`, "  [\n\t1\r\n]  ", `nul`, `{"x":tru}`,
	`{"b":{"y":1,"x":2},"a~/":[1.0,1e2,12345678901234567,-0,0.0000001]}`, `{"\uffff":1,"😀":2,"a\u0000":3,"\u001F":"\u00e9\u001f/"}`,
	`[{"a":[{"b":{"c":[1,2,{"d":"e"}]}}]}]`,
}

var muts = []byte{' ', '"', ',', '}', ']', 'x', '\\', '0', 0xff, ':', '{'}

func show(in []byte) {
	fmt.Println("IN", hex.EncodeToString(in))
	fmt.Println(" V", jsontext.Value(in).IsValid())
	for i, f := range []func(*jsontext.Value) error{
		func(v *jsontext.Value) error { return v.Compact() },
		func(v *jsontext.Value) error { return v.Indent() },
		func(v *jsontext.Value) error { return v.Indent(jsontext.WithIndentPrefix(" "), jsontext.WithIndent("  ")) },
		func(v *jsontext.Value) error { return v.Canonicalize() },
	} {
		v := jsontext.Value(append([]byte{}, in...))
		if err := f(&v); err != nil {
			fmt.Println("", i, "err", err)
		} else {
			fmt.Println("", i, hex.EncodeToString(v))
		}
	}
	var x any
	if err := json.Unmarshal(in, &x); err != nil {
		fmt.Println(" U err", err)
	} else {
		out, err := json.Marshal(x, json.Deterministic(true))
		fmt.Println(" U", hex.EncodeToString(out), err)
	}
}

func main() {
	for _, b := range bases {
		in := []byte(b)
		show(in)
		for i := 0; i < len(in); i++ {
			show(append(append([]byte{}, in[:i]...), in[i+1:]...))
			for _, m := range muts {
				r := append([]byte{}, in...)
				r[i] = m
				show(r)
			}
		}
		for i := 0; i <= len(in); i += 3 {
			for _, m := range muts {
				show(append(append(append([]byte{}, in[:i]...), m), in[i:]...))
			}
		}
	}
	for _, s := range []string{`{"x":1e999}`, `[1,[2,-1e999]]`, `1e999`, `{"a":{"b":1,"b":2}}`, `{"a":[{"b":1,"c":2,"b":3}]}`} {
		var x any
		fmt.Println(s, json.Unmarshal([]byte(s), &x))
	}
	q, err := jsontext.AppendQuote(nil, "a<\"\x01\u2028\xff")
	fmt.Println(hex.EncodeToString(q), errs(err))
	u, err := jsontext.AppendUnquote(nil, `"a\u00e9\ud83d\ude00\/\ud800x"`)
	fmt.Println(hex.EncodeToString(u), errs(err))
	m, _ := json.Marshal(map[string]any{"\uffff": 1.0, "😀": 2.0, "b": []any{"<&>\u2028", 1e21, nil}}, json.Deterministic(true))
	fmt.Println(string(m))
}

func errs(e error) string {
	if e == nil {
		return ""
	}
	return e.Error()
}
