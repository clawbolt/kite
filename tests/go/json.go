package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
)

var bases = []string{
	`{"a":1}`, `[1,2.5,-3e10,true,false,null]`, `"he\"llo\u00e9\n"`, `{"k":[{},[]],"z":"x"}`, ` 0 `, `-0.5E+3`,
	`{"a" : [ 1 , { "b" : null } ] }`, `"\ud83d\ude00 \ud800"`, `[[[]]]`, `{"":""}`, `"é<&>\u2028"`, `123`, `true`,
	"\"\xff\xfe\"", `{"a":1,"a":2}`, `[1e400]`, `"\/\b\f\r\t"`, "  [\n\t1\r\n]  ", `nul`, `{"x":tru}`,
}

var muts = []byte{' ', '"', ',', '}', ']', 'x', '\\', '0', 0xff}

func show(in []byte) {
	fmt.Println("IN", hex.EncodeToString(in))
	var v any
	err := json.Unmarshal(in, &v)
	if se, ok := err.(*json.SyntaxError); ok {
		fmt.Println(" U syn", se.Offset, se.Error())
	} else if err != nil {
		fmt.Println(" U err", err)
	} else {
		out, err := json.Marshal(v)
		fmt.Println(" U ok", hex.EncodeToString(out), err)
	}
	fmt.Println(" V", json.Valid(in))
	var c bytes.Buffer
	if err := json.Compact(&c, in); err != nil {
		fmt.Println(" C err", err.(*json.SyntaxError).Offset, err)
	} else {
		fmt.Println(" C", hex.EncodeToString(c.Bytes()))
	}
	var d bytes.Buffer
	if err := json.Indent(&d, in, "> ", "\t."); err != nil {
		fmt.Println(" I err", err.(*json.SyntaxError).Offset, err)
	} else {
		fmt.Println(" I", hex.EncodeToString(d.Bytes()))
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
	var mi bytes.Buffer
	json.Indent(&mi, []byte(`{"a":[1,{"b":[]}],"c":{}}`), "", "  ")
	fmt.Println(mi.String())
	var hb bytes.Buffer
	json.HTMLEscape(&hb, []byte("<a href=\"x&y\">\u2028\u2029</a>"))
	fmt.Println(hb.String())
	o, _ := json.MarshalIndent(map[string]any{"b": []any{1.0, "s", nil, true}, "a": map[string]any{}, "c": []any{}, "d": 1e21, "e": 1e-7, "f": 123456789.0, "g": 0.1}, "#", " ")
	fmt.Println(string(o))
	for _, x := range []string{`{"a":{"b":[1,1e999]}}`, `{"x":1e999}`, `[1e999,]`, `{"a":1e999,"a":1}`, `-1e999`, `[0.1e-400, 4.9e-324, 1e308, 123456789012345678901]`, `{"\u00e9":["\u2028",""]}`} {
		var v any
		err := json.Unmarshal([]byte(x), &v)
		out, _ := json.Marshal(v)
		if err != nil { out = []byte("-") }
		fmt.Println(x, "|", err, "|", string(out))
	}
	_ = strings.ToLower
}
