// 对拍 Kite 的 std/xml 与 Go 的 encoding/xml（由生成脚本产出，示例 examples/go_xml.kite 用同一份数据）
package main

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"strings"

	"encoding/xml"
)

func show(b []byte) string {
	var sb strings.Builder
	for _, c := range b {
		switch {
		case c == '\\':
			sb.WriteString("\\\\")
		case c == '"':
			sb.WriteString("\\\"")
		case c == '\n':
			sb.WriteString("\\n")
		case c == '\t':
			sb.WriteString("\\t")
		case c == '\r':
			sb.WriteString("\\r")
		case c >= 0x20 && c < 0x7f:
			sb.WriteByte(c)
		default:
			sb.WriteString(fmt.Sprintf("\\x%02x", c))
		}
	}
	return "\"" + sb.String() + "\""
}

func nm(n xml.Name) string {
	if n.Space == "" {
		return n.Local
	}
	return "{" + n.Space + "}" + n.Local
}

func tokStr(t xml.Token) string {
	switch v := t.(type) {
	case xml.StartElement:
		s := "S <" + nm(v.Name)
		for _, a := range v.Attr {
			s += " " + nm(a.Name) + "=" + show([]byte(a.Value))
		}
		return s + ">"
	case xml.EndElement:
		return "E </" + nm(v.Name) + ">"
	case xml.CharData:
		return "C " + show(v)
	case xml.Comment:
		return "M " + show(v)
	case xml.ProcInst:
		return "P " + v.Target + " " + show(v.Inst)
	case xml.Directive:
		return "D " + show(v)
	}
	return "?"
}

func errStr(err error) string {
	if err == io.EOF {
		return "EOF"
	}
	return "ERR " + show([]byte(err.Error()))
}

func runDoc(d *xml.Decoder, raw bool) {
	for i := 0; i < 300; i++ {
		var t xml.Token
		var err error
		if raw {
			t, err = d.RawToken()
		} else {
			t, err = d.Token()
		}
		if err != nil {
			fmt.Println("  " + errStr(err))
			if raw {
				_, err = d.RawToken()
			} else {
				_, err = d.Token()
			}
			fmt.Println("  again " + errStr(err))
			break
		}
		fmt.Println("  " + tokStr(t))
	}
	line, col := d.InputPos()
	fmt.Println("  pos", d.InputOffset(), line, col)
}

func step(buf *bytes.Buffer, err error) {
	if err != nil {
		fmt.Println("  err " + show([]byte(err.Error())) + fmt.Sprintf(" %d", buf.Len()))
	} else {
		fmt.Printf("  ok %d\n", buf.Len())
	}
}

// ---- 元素树（与 Kite 的 xml.parse / xml.marshal 同样的规则）----

type Node struct {
	Kind int // 0 元素 1 文本 2 注释
	El   *Element
	Text string
	Data []byte
}

type Element struct {
	Name     xml.Name
	Attr     []xml.Attr
	Children []Node
}

func (e *Element) text() string {
	s := ""
	for _, c := range e.Children {
		if c.Kind == 1 {
			s += c.Text
		}
	}
	return s
}

func (e *Element) find(local string) *Element {
	for _, c := range e.Children {
		if c.Kind == 0 && c.El.Name.Local == local {
			return c.El
		}
	}
	return nil
}

func (e *Element) findAll(local string) []*Element {
	var out []*Element
	for _, c := range e.Children {
		if c.Kind == 0 && c.El.Name.Local == local {
			out = append(out, c.El)
		}
	}
	return out
}

func (e *Element) attrValue(local string) (string, bool) {
	for _, a := range e.Attr {
		if a.Name.Local == local {
			return a.Value, true
		}
	}
	return "", false
}

func parseChildren(d *xml.Decoder, se xml.StartElement) (*Element, error) {
	el := &Element{Name: se.Name, Attr: se.Attr}
	for {
		t, err := d.Token()
		if err != nil {
			return nil, err
		}
		switch v := t.(type) {
		case xml.StartElement:
			c, err := parseChildren(d, v.Copy())
			if err != nil {
				return nil, err
			}
			el.Children = append(el.Children, Node{Kind: 0, El: c})
		case xml.EndElement:
			return el, nil
		case xml.CharData:
			n := len(el.Children)
			if n > 0 && el.Children[n-1].Kind == 1 {
				el.Children[n-1].Text += string(v)
			} else {
				el.Children = append(el.Children, Node{Kind: 1, Text: string(v)})
			}
		case xml.Comment:
			el.Children = append(el.Children, Node{Kind: 2, Data: bytes.Clone(v)})
		}
	}
}

func parseTree(src string) (*Element, error) {
	d := xml.NewDecoder(strings.NewReader(src))
	var root *Element
	for {
		t, err := d.Token()
		if err != nil {
			if err == io.EOF {
				if root != nil {
					return root, nil
				}
				return nil, errors.New("xml: no root element")
			}
			return nil, err
		}
		if se, ok := t.(xml.StartElement); ok {
			el, err := parseChildren(d, se.Copy())
			if err != nil {
				return nil, err
			}
			if root == nil {
				root = el
			}
		}
	}
}

func encodeElement(e *xml.Encoder, el *Element) error {
	if err := e.EncodeToken(xml.StartElement{Name: el.Name, Attr: el.Attr}); err != nil {
		return err
	}
	for _, c := range el.Children {
		var err error
		switch c.Kind {
		case 0:
			err = encodeElement(e, c.El)
		case 1:
			err = e.EncodeToken(xml.CharData(c.Text))
		case 2:
			err = e.EncodeToken(xml.Comment(c.Data))
		}
		if err != nil {
			return err
		}
	}
	return e.EncodeToken(xml.EndElement{Name: el.Name})
}

func marshalIndent(el *Element, prefix, indent string) (string, error) {
	var buf bytes.Buffer
	e := xml.NewEncoder(&buf)
	e.Indent(prefix, indent)
	if err := encodeElement(e, el); err != nil {
		return "", err
	}
	e.Flush()
	return buf.String(), nil
}

func latin1(charset string, r io.Reader) (io.Reader, error) {
	if !strings.EqualFold(charset, "latin1") {
		return nil, errors.New("unsupported charset " + charset)
	}
	b, _ := io.ReadAll(r)
	var out []byte
	for _, c := range b {
		if c < 0x80 {
			out = append(out, c)
		} else {
			out = append(out, 0xc0|c>>6, 0x80|c&0x3f)
		}
	}
	return bytes.NewReader(out), nil
}

func main() {
	fmt.Println("== Token")
	fmt.Println("doc 0")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("\n<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE html PUBLIC \"-//W3C//DTD XHTML 1.0 Transitional//EN\"\n  \"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd\">\n<body xmlns:foo=\"ns1\" xmlns=\"ns2\" xmlns:tag=\"ns3\" \r\n\t  >\n  <hello lang=\"en\">World &lt;&gt;&apos;&quot; &#x767d;&#40300;\xe7\xbf\x94</hello>\n  <query>&\xe4\xbd\x95; &is-it;</query>\n  <goodbye />\n  <outer foo:attr=\"value\" xmlns:tag=\"ns4\">\n    <inner/>\n  </outer>\n  <tag:name>\n    <![CDATA[Some text here.]]>\n  </tag:name>\n</body><!-- missing final newline -->"))), false)
	fmt.Println("doc 1")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("\n<!DOCTYPE [<!-- a comment --><!ENTITY rdf \"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">]>\n<!DOCTYPE [<!ENTITY go \"Golang\"><!-- a comment-->]>\n<!DOCTYPE <!-> <!> <!----> <!-->--> <!--->--> [<!ENTITY go \"Golang\"><!-- a comment-->]>\n"))), false)
	fmt.Println("doc 2")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("\n<tag>non&entity</tag>\n<tag>&unknown;entity</tag>\n<tag>&#123</tag>\n<tag>&#zzz;</tag>\n<tag>&\xe3\x81\xaa\xe3\x81\xbe\xe3\x81\x883;</tag>\n<tag>&lt-gt;</tag>\n<tag>&;</tag>\n<tag>&0a;</tag>\n"))), false)
	fmt.Println("doc 3")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<"))), false)
	fmt.Println("doc 4")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t"))), false)
	fmt.Println("doc 5")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t "))), false)
	fmt.Println("doc 6")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t/"))), false)
	fmt.Println("doc 7")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!"))), false)
	fmt.Println("doc 8")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!-"))), false)
	fmt.Println("doc 9")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!--"))), false)
	fmt.Println("doc 10")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!--c-"))), false)
	fmt.Println("doc 11")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!--c--"))), false)
	fmt.Println("doc 12")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!d"))), false)
	fmt.Println("doc 13")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t></"))), false)
	fmt.Println("doc 14")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t></t"))), false)
	fmt.Println("doc 15")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?"))), false)
	fmt.Println("doc 16")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?p"))), false)
	fmt.Println("doc 17")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a"))), false)
	fmt.Println("doc 18")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a="))), false)
	fmt.Println("doc 19")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a='"))), false)
	fmt.Println("doc 20")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a=''"))), false)
	fmt.Println("doc 21")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t/><!["))), false)
	fmt.Println("doc 22")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t/><![C"))), false)
	fmt.Println("doc 23")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t/><![CDATA[d"))), false)
	fmt.Println("doc 24")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t/><![CDATA[d]"))), false)
	fmt.Println("doc 25")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t/><![CDATA[d]]"))), false)
	fmt.Println("doc 26")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<>"))), false)
	fmt.Println("doc 27")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t/a"))), false)
	fmt.Println("doc 28")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<0 />"))), false)
	fmt.Println("doc 29")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?0 >"))), false)
	fmt.Println("doc 30")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("</0>"))), false)
	fmt.Println("doc 31")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t 0=''>"))), false)
	fmt.Println("doc 32")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a='&'>"))), false)
	fmt.Println("doc 33")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a='<'>"))), false)
	fmt.Println("doc 34")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t>&nbspc;</t>"))), false)
	fmt.Println("doc 35")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a>"))), false)
	fmt.Println("doc 36")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a=>"))), false)
	fmt.Println("doc 37")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a=v>"))), false)
	fmt.Println("doc 38")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t></e>"))), false)
	fmt.Println("doc 39")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t></>"))), false)
	fmt.Println("doc 40")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t></t!"))), false)
	fmt.Println("doc 41")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t>cdata]]></t>"))), false)
	fmt.Println("doc 42")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!0 >"))), false)
	fmt.Println("doc 43")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<![CDATA[d]]>"))), false)
	fmt.Println("doc 44")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("\x12<doc/>"))), false)
	fmt.Println("doc 45")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?xml version=\"1.0\"?>\x0b<doc/>"))), false)
	fmt.Println("doc 46")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("\xef\xbf\xbe<doc/>"))), false)
	fmt.Println("doc 47")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?xml version=\"1.0\"?><doc>\r\n<hiya/>\x07<toots/></doc>"))), false)
	fmt.Println("doc 48")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?xml version=\"1.0\"?><doc \x12='value'>what's up</doc>"))), false)
	fmt.Println("doc 49")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<doc>&abc\x01;</doc>"))), false)
	fmt.Println("doc 50")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<doc>&\x01;</doc>"))), false)
	fmt.Println("doc 51")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<doc>&\xef\xbf\xbe;</doc>"))), false)
	fmt.Println("doc 52")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<doc>&hello;</doc>"))), false)
	fmt.Println("doc 53")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>\n<b>\n</a>"))), false)
	fmt.Println("doc 54")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("</a>"))), false)
	fmt.Println("doc 55")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a></a></a>"))), false)
	fmt.Println("doc 56")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a:b xmlns:a='x'></c:b>"))), false)
	fmt.Println("doc 57")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a:b xmlns:a='x'></b>"))), false)
	fmt.Println("doc 58")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<b></a:b>"))), false)
	fmt.Println("doc 59")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a b='1' b='2'/>"))), false)
	fmt.Println("doc 60")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a\n\nb\n=\n'x\ny'\n/>"))), false)
	fmt.Println("doc 61")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a b=\"x\ty\r\nz\rw\"/>"))), false)
	fmt.Println("doc 62")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>&#0;</a>"))), false)
	fmt.Println("doc 63")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>&#xD800;</a>"))), false)
	fmt.Println("doc 64")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>&#x110000;</a>"))), false)
	fmt.Println("doc 65")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>&#1114111;</a>"))), false)
	fmt.Println("doc 66")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>&#99999999999999999999999;</a>"))), false)
	fmt.Println("doc 67")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>&#;</a>"))), false)
	fmt.Println("doc 68")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>&#x;</a>"))), false)
	fmt.Println("doc 69")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>&#65</a>"))), false)
	fmt.Println("doc 70")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>&#x4G;</a>"))), false)
	fmt.Println("doc 71")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>&amp</a>"))), false)
	fmt.Println("doc 72")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>&lt;&gt;&amp;&apos;&quot;</a>"))), false)
	fmt.Println("doc 73")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>&#000065;&#x0041;&#X41;</a>"))), false)
	fmt.Println("doc 74")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?xml version='1.1'?><a/>"))), false)
	fmt.Println("doc 75")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?xml version=\"2\" encoding=\"utf-8\"?>"))), false)
	fmt.Println("doc 76")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?xml encoding='latin1'?><a/>"))), false)
	fmt.Println("doc 77")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?xml version='1.0' encoding='UTF-8'?><a/>"))), false)
	fmt.Println("doc 78")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?xml version='1.0' encoding='uTf-8'?><a/>"))), false)
	fmt.Println("doc 79")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?xml-stylesheet href='a.css'?><a/>"))), false)
	fmt.Println("doc 80")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?pi?>"))), false)
	fmt.Println("doc 81")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?pi   data with ?> ?>"))), false)
	fmt.Println("doc 82")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?pi data"))), false)
	fmt.Println("doc 83")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?pi x?"))), false)
	fmt.Println("doc 84")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!-- c -- d -->"))), false)
	fmt.Println("doc 85")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!--a--->"))), false)
	fmt.Println("doc 86")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!---->"))), false)
	fmt.Println("doc 87")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!-x-->"))), false)
	fmt.Println("doc 88")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<![CDATA[a]]b]]]>"))), false)
	fmt.Println("doc 89")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<![CDATA[<&>]]>"))), false)
	fmt.Println("doc 90")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<![CDATX["))), false)
	fmt.Println("doc 91")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>]]></a>"))), false)
	fmt.Println("doc 92")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>]]</a>"))), false)
	fmt.Println("doc 93")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>]>]]&gt;</a>"))), false)
	fmt.Println("doc 94")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>x\r\ny\rz\n\r</a>"))), false)
	fmt.Println("doc 95")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!DOCTYPE a [<!ENTITY e 'v'>]><a/>"))), false)
	fmt.Println("doc 96")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!DOCTYPE a 'x>y' \"q>\" >"))), false)
	fmt.Println("doc 97")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!DOCTYPE a <b <c>> >"))), false)
	fmt.Println("doc 98")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!DOCTYPE <!-- x > y -->>"))), false)
	fmt.Println("doc 99")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!DOCTYPE a <!-x>"))), false)
	fmt.Println("doc 100")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!DOCTYPE a"))), false)
	fmt.Println("doc 101")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!DOCTYPE a <!-- "))), false)
	fmt.Println("doc 102")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!DOCTYPE a '"))), false)
	fmt.Println("doc 103")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a:b:c/>"))), false)
	fmt.Println("doc 104")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<:a/>"))), false)
	fmt.Println("doc 105")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a:/>"))), false)
	fmt.Println("doc 106")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<x:a xmlns:x='u' x:y:z='1'/>"))), false)
	fmt.Println("doc 107")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a b:='1'/>"))), false)
	fmt.Println("doc 108")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a :b='1'/>"))), false)
	fmt.Println("doc 109")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<\xc3\xa9/>"))), false)
	fmt.Println("doc 110")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a \xc3\xa9='\xc3\xa9'>\xc3\xa9</a>"))), false)
	fmt.Println("doc 111")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a-b.c_d1/>"))), false)
	fmt.Println("doc 112")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<-a/>"))), false)
	fmt.Println("doc 113")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<.a/>"))), false)
	fmt.Println("doc 114")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<1a/>"))), false)
	fmt.Println("doc 115")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a\xc2\xb7b/>"))), false)
	fmt.Println("doc 116")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a\xcc\x80/>"))), false)
	fmt.Println("doc 117")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<\xe4\xb8\x80/>"))), false)
	fmt.Println("doc 118")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<\xe3\x80\x87x/>"))), false)
	fmt.Println("doc 119")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a\xe2\x80\xa8/>"))), false)
	fmt.Println("doc 120")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a/ >"))), false)
	fmt.Println("doc 121")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a / >"))), false)
	fmt.Println("doc 122")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a b='1'c='2'/>"))), false)
	fmt.Println("doc 123")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a b = '1' />"))), false)
	fmt.Println("doc 124")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>\t text \t</a>"))), false)
	fmt.Println("doc 125")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("text only"))), false)
	fmt.Println("doc 126")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte(""))), false)
	fmt.Println("doc 127")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("  \n "))), false)
	fmt.Println("doc 128")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a xmlns='u1'><b xmlns:p='u2' p:c='1' c='2'><p:d/></b><p:e/></a>"))), false)
	fmt.Println("doc 129")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a xmlns:p='u1'><p:b xmlns:p='u2'><p:c/></p:b><p:d/></a>"))), false)
	fmt.Println("doc 130")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a xml:lang='en' xmlns:xml='bogus'><xml:b/></a>"))), false)
	fmt.Println("doc 131")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<x:a/>"))), false)
	fmt.Println("doc 132")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<xmlns:a/>"))), false)
	fmt.Println("doc 133")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a xmlns:='u'/>"))), false)
	fmt.Println("doc 134")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a xmlns='u'><b xmlns=''><c/></b><d/></a>"))), false)
	fmt.Println("doc 135")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a xmlns='&amp;&#60;'/>"))), false)
	fmt.Println("doc 136")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<p:a xmlns:p='u'></p:a>"))), false)
	fmt.Println("doc 137")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a><b></b></a><c/>tail"))), false)
	fmt.Println("doc 138")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>\xff</a>"))), false)
	fmt.Println("doc 139")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a b='\xc3'/>"))), false)
	fmt.Println("doc 140")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!--\xff-->"))), false)
	fmt.Println("doc 141")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?pi \xfe?>"))), false)
	fmt.Println("doc 142")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!DOCTYPE \xff>"))), false)
	fmt.Println("doc 143")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>\xed\xa0\x80</a>"))), false)
	fmt.Println("doc 144")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>\xef\xbf\xbf</a>"))), false)
	fmt.Println("doc 145")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a>\x00</a>"))), false)
	fmt.Println("== RawToken")
	fmt.Println("raw 0")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("\n<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE html PUBLIC \"-//W3C//DTD XHTML 1.0 Transitional//EN\"\n  \"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd\">\n<body xmlns:foo=\"ns1\" xmlns=\"ns2\" xmlns:tag=\"ns3\" \r\n\t  >\n  <hello lang=\"en\">World &lt;&gt;&apos;&quot; &#x767d;&#40300;\xe7\xbf\x94</hello>\n  <query>&\xe4\xbd\x95; &is-it;</query>\n  <goodbye />\n  <outer foo:attr=\"value\" xmlns:tag=\"ns4\">\n    <inner/>\n  </outer>\n  <tag:name>\n    <![CDATA[Some text here.]]>\n  </tag:name>\n</body><!-- missing final newline -->"))), true)
	fmt.Println("raw 1")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("\n<!DOCTYPE [<!-- a comment --><!ENTITY rdf \"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">]>\n<!DOCTYPE [<!ENTITY go \"Golang\"><!-- a comment-->]>\n<!DOCTYPE <!-> <!> <!----> <!-->--> <!--->--> [<!ENTITY go \"Golang\"><!-- a comment-->]>\n"))), true)
	fmt.Println("raw 2")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("\n<tag>non&entity</tag>\n<tag>&unknown;entity</tag>\n<tag>&#123</tag>\n<tag>&#zzz;</tag>\n<tag>&\xe3\x81\xaa\xe3\x81\xbe\xe3\x81\x883;</tag>\n<tag>&lt-gt;</tag>\n<tag>&;</tag>\n<tag>&0a;</tag>\n"))), true)
	fmt.Println("raw 3")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<a></b><c><d/></e>"))), true)
	fmt.Println("raw 4")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("</x></y>"))), true)
	fmt.Println("raw 5")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<p:a xmlns:p='u'><p:b/></q:a>"))), true)
	fmt.Println("raw 6")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<"))), true)
	fmt.Println("raw 7")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t"))), true)
	fmt.Println("raw 8")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t "))), true)
	fmt.Println("raw 9")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t/"))), true)
	fmt.Println("raw 10")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!"))), true)
	fmt.Println("raw 11")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!-"))), true)
	fmt.Println("raw 12")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!--"))), true)
	fmt.Println("raw 13")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!--c-"))), true)
	fmt.Println("raw 14")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!--c--"))), true)
	fmt.Println("raw 15")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!d"))), true)
	fmt.Println("raw 16")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t></"))), true)
	fmt.Println("raw 17")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t></t"))), true)
	fmt.Println("raw 18")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?"))), true)
	fmt.Println("raw 19")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?p"))), true)
	fmt.Println("raw 20")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a"))), true)
	fmt.Println("raw 21")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a="))), true)
	fmt.Println("raw 22")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a='"))), true)
	fmt.Println("raw 23")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a=''"))), true)
	fmt.Println("raw 24")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t/><!["))), true)
	fmt.Println("raw 25")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t/><![C"))), true)
	fmt.Println("raw 26")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t/><![CDATA[d"))), true)
	fmt.Println("raw 27")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t/><![CDATA[d]"))), true)
	fmt.Println("raw 28")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t/><![CDATA[d]]"))), true)
	fmt.Println("raw 29")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<>"))), true)
	fmt.Println("raw 30")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t/a"))), true)
	fmt.Println("raw 31")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<0 />"))), true)
	fmt.Println("raw 32")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<?0 >"))), true)
	fmt.Println("raw 33")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("</0>"))), true)
	fmt.Println("raw 34")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t 0=''>"))), true)
	fmt.Println("raw 35")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a='&'>"))), true)
	fmt.Println("raw 36")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a='<'>"))), true)
	fmt.Println("raw 37")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t>&nbspc;</t>"))), true)
	fmt.Println("raw 38")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a>"))), true)
	fmt.Println("raw 39")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a=>"))), true)
	fmt.Println("raw 40")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t a=v>"))), true)
	fmt.Println("raw 41")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t></e>"))), true)
	fmt.Println("raw 42")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t></>"))), true)
	fmt.Println("raw 43")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t></t!"))), true)
	fmt.Println("raw 44")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<t>cdata]]></t>"))), true)
	fmt.Println("raw 45")
	runDoc(xml.NewDecoder(bytes.NewReader([]byte("<!0 >"))), true)
	fmt.Println("== entity map")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("\n<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE html PUBLIC \"-//W3C//DTD XHTML 1.0 Transitional//EN\"\n  \"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd\">\n<body xmlns:foo=\"ns1\" xmlns=\"ns2\" xmlns:tag=\"ns3\" \r\n\t  >\n  <hello lang=\"en\">World &lt;&gt;&apos;&quot; &#x767d;&#40300;\xe7\xbf\x94</hello>\n  <query>&\xe4\xbd\x95; &is-it;</query>\n  <goodbye />\n  <outer foo:attr=\"value\" xmlns:tag=\"ns4\">\n    <inner/>\n  </outer>\n  <tag:name>\n    <![CDATA[Some text here.]]>\n  </tag:name>\n</body><!-- missing final newline -->")))
		d.Entity = map[string]string{"何": "What", "is-it": "is it?"}
		runDoc(d, false)
	}
	fmt.Println("== non-strict")
	fmt.Println("ns 0 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("\n<tag>non&entity</tag>\n<tag>&unknown;entity</tag>\n<tag>&#123</tag>\n<tag>&#zzz;</tag>\n<tag>&\xe3\x81\xaa\xe3\x81\xbe\xe3\x81\x883;</tag>\n<tag>&lt-gt;</tag>\n<tag>&;</tag>\n<tag>&0a;</tag>\n")))
		d.Strict = false
		runDoc(d, false)
	}
	fmt.Println("ns 0 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("\n<tag>non&entity</tag>\n<tag>&unknown;entity</tag>\n<tag>&#123</tag>\n<tag>&#zzz;</tag>\n<tag>&\xe3\x81\xaa\xe3\x81\xbe\xe3\x81\x883;</tag>\n<tag>&lt-gt;</tag>\n<tag>&;</tag>\n<tag>&0a;</tag>\n")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
	}
	fmt.Println("ns 0 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("\n<tag>non&entity</tag>\n<tag>&unknown;entity</tag>\n<tag>&#123</tag>\n<tag>&#zzz;</tag>\n<tag>&\xe3\x81\xaa\xe3\x81\xbe\xe3\x81\x883;</tag>\n<tag>&lt-gt;</tag>\n<tag>&;</tag>\n<tag>&0a;</tag>\n")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, true)
	}
	fmt.Println("ns 1 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<p>Hello<br>World&nbsp;&copy; &eacute;t&eacute; &euro;<img src=x alt>done</p>")))
		d.Strict = false
		runDoc(d, false)
	}
	fmt.Println("ns 1 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<p>Hello<br>World&nbsp;&copy; &eacute;t&eacute; &euro;<img src=x alt>done</p>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
	}
	fmt.Println("ns 1 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<p>Hello<br>World&nbsp;&copy; &eacute;t&eacute; &euro;<img src=x alt>done</p>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, true)
	}
	fmt.Println("ns 2 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<html><body><p>one<p>two</body></html>")))
		d.Strict = false
		runDoc(d, false)
	}
	fmt.Println("ns 2 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<html><body><p>one<p>two</body></html>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
	}
	fmt.Println("ns 2 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<html><body><p>one<p>two</body></html>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, true)
	}
	fmt.Println("ns 3 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a><b></a>")))
		d.Strict = false
		runDoc(d, false)
	}
	fmt.Println("ns 3 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a><b></a>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
	}
	fmt.Println("ns 3 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a><b></a>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, true)
	}
	fmt.Println("ns 4 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a><b><c></a>more")))
		d.Strict = false
		runDoc(d, false)
	}
	fmt.Println("ns 4 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a><b><c></a>more")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
	}
	fmt.Println("ns 4 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a><b><c></a>more")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, true)
	}
	fmt.Println("ns 5 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<A x=1 y>t</a>")))
		d.Strict = false
		runDoc(d, false)
	}
	fmt.Println("ns 5 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<A x=1 y>t</a>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
	}
	fmt.Println("ns 5 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<A x=1 y>t</a>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, true)
	}
	fmt.Println("ns 6 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a x=1/>")))
		d.Strict = false
		runDoc(d, false)
	}
	fmt.Println("ns 6 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a x=1/>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
	}
	fmt.Println("ns 6 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a x=1/>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, true)
	}
	fmt.Println("ns 7 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a x=>")))
		d.Strict = false
		runDoc(d, false)
	}
	fmt.Println("ns 7 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a x=>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
	}
	fmt.Println("ns 7 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a x=>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, true)
	}
	fmt.Println("ns 8 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a x='1' y=b-c:d_e>z</a>")))
		d.Strict = false
		runDoc(d, false)
	}
	fmt.Println("ns 8 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a x='1' y=b-c:d_e>z</a>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
	}
	fmt.Println("ns 8 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a x='1' y=b-c:d_e>z</a>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, true)
	}
	fmt.Println("ns 9 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<ul><li>1<li>2<LI>3</ul>")))
		d.Strict = false
		runDoc(d, false)
	}
	fmt.Println("ns 9 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<ul><li>1<li>2<LI>3</ul>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
	}
	fmt.Println("ns 9 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<ul><li>1<li>2<LI>3</ul>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, true)
	}
	fmt.Println("ns 10 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<input type=text><br/><hr></hr>end")))
		d.Strict = false
		runDoc(d, false)
	}
	fmt.Println("ns 10 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<input type=text><br/><hr></hr>end")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
	}
	fmt.Println("ns 10 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<input type=text><br/><hr></hr>end")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, true)
	}
	fmt.Println("ns 11 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a>&foo; &#zz; &#12 &amp</a>")))
		d.Strict = false
		runDoc(d, false)
	}
	fmt.Println("ns 11 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a>&foo; &#zz; &#12 &amp</a>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
	}
	fmt.Println("ns 11 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a>&foo; &#zz; &#12 &amp</a>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, true)
	}
	fmt.Println("ns 12 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<p>x<BR>y</P>")))
		d.Strict = false
		runDoc(d, false)
	}
	fmt.Println("ns 12 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<p>x<BR>y</P>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
	}
	fmt.Println("ns 12 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<p>x<BR>y</P>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, true)
	}
	fmt.Println("ns 13 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a><b></b>")))
		d.Strict = false
		runDoc(d, false)
	}
	fmt.Println("ns 13 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a><b></b>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
	}
	fmt.Println("ns 13 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a><b></b>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, true)
	}
	fmt.Println("ns 14 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("</a>")))
		d.Strict = false
		runDoc(d, false)
	}
	fmt.Println("ns 14 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("</a>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
	}
	fmt.Println("ns 14 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("</a>")))
		d.Strict = false
		d.AutoClose = xml.HTMLAutoClose
		d.Entity = xml.HTMLEntity
		runDoc(d, true)
	}
	fmt.Println("== html entity strict")
	{
		d := xml.NewDecoder(strings.NewReader("<p>&nbsp;&hearts;&Omega;&bogus;</p>"))
		d.Entity = xml.HTMLEntity
		runDoc(d, false)
		fmt.Println(len(xml.HTMLEntity), len(xml.HTMLAutoClose))
	}
	fmt.Println("defspace 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<a xmlns:p='u'><b/><p:c x='1' p:y='2'/><d xmlns='v'><e/></d></a>")))
		d.DefaultSpace = "dflt"
		runDoc(d, false)
	}
	fmt.Println("defspace 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<x:a/>")))
		d.DefaultSpace = "dflt"
		runDoc(d, false)
	}
	fmt.Println("charset 0")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<?xml version='1.0' encoding='latin1'?><a b='\xe9'>caf\xe9</a>")))
		d.CharsetReader = latin1
		runDoc(d, false)
	}
	fmt.Println("charset 1")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<?xml encoding='ebcdic'?><a/>")))
		d.CharsetReader = latin1
		runDoc(d, false)
	}
	fmt.Println("charset 2")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<?xml encoding=\"utf-8\"?><a>\xc3\xa9</a>")))
		d.CharsetReader = latin1
		runDoc(d, false)
	}
	fmt.Println("charset 3")
	{
		d := xml.NewDecoder(bytes.NewReader([]byte("<?xml version='1.0' encoding='LATIN1' standalone='yes'?>\n<r>\xff\n</r>")))
		d.CharsetReader = latin1
		runDoc(d, false)
	}
	fmt.Println("== Encoder")
	fmt.Println("enc ns")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "space", Local: "local"}, Attr: []xml.Attr{}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc noname")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "space", Local: ""}, Attr: []xml.Attr{}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc endnoname")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "space", Local: ""}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc chardata")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.CharData("foo")))
		step(&buf, e.EncodeToken(xml.CharData(" \t\n\r<>&'\"x")))
		step(&buf, e.EncodeToken(xml.CharData("a\x01b\xef\xbf\xbe")))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc comment")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.Comment("foo")))
		step(&buf, e.EncodeToken(xml.Comment("foo-->")))
		step(&buf, e.EncodeToken(xml.Comment("a--b")))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc procinst")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.ProcInst{Target: "Target", Inst: []byte("Instruction")}))
		step(&buf, e.EncodeToken(xml.ProcInst{Target: "", Inst: []byte("Instruction")}))
		step(&buf, e.EncodeToken(xml.ProcInst{Target: "T", Inst: []byte("x?>")}))
		step(&buf, e.EncodeToken(xml.ProcInst{Target: "T", Inst: []byte("")}))
		step(&buf, e.EncodeToken(xml.ProcInst{Target: "1x", Inst: []byte("a")}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc directive")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.Directive("foo")))
		step(&buf, e.EncodeToken(xml.Directive("DOCTYPE doc [ <!ELEMENT doc '>'> <!-- com>ment --> ]")))
		step(&buf, e.EncodeToken(xml.Directive("foo>")))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc dirs")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.Directive("<>")))
		step(&buf, e.EncodeToken(xml.Directive("< < > >")))
		step(&buf, e.EncodeToken(xml.Directive("<!DOCTYPE '<' '>' '>' <!--nothing-->>")))
		step(&buf, e.EncodeToken(xml.Directive("<!DOCTYPE doc [ <!ELEMENT doc \"ANY> '<' <!E\" LEMENT '>' doc ANY> ]>")))
		step(&buf, e.EncodeToken(xml.Directive("<!DOCTYPE doc <!-- just>>>> a < comment --> [ <!ITEM anything> ] >")))
		step(&buf, e.EncodeToken(xml.Directive("<")))
		step(&buf, e.EncodeToken(xml.Directive(">")))
		step(&buf, e.EncodeToken(xml.Directive("<!--")))
		step(&buf, e.EncodeToken(xml.Directive("-->")))
		step(&buf, e.EncodeToken(xml.Directive("< > > < < >")))
		step(&buf, e.EncodeToken(xml.Directive("<!dummy <!-- > -->")))
		step(&buf, e.EncodeToken(xml.Directive("<!DOCTYPE doc '>")))
		step(&buf, e.EncodeToken(xml.Directive("<!DOCTYPE doc '>'")))
		step(&buf, e.EncodeToken(xml.Directive("<!DOCTYPE doc <!--comment>")))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc endnostart")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "foo", Local: "bar"}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc mismatch")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "foo"}, Attr: []xml.Attr{}}))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "", Local: "bar"}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc mismatchns")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "space", Local: "foo"}, Attr: []xml.Attr{}}))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "another", Local: "foo"}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc explicitns")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "space", Local: "local"}, Attr: []xml.Attr{{Name: xml.Name{Space: "xmlns", Local: "x"}, Value: "space"}, {Name: xml.Name{Space: "space", Local: "foo"}, Value: "value"}}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc collide")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "space", Local: "local"}, Attr: []xml.Attr{{Name: xml.Name{Space: "xmlns", Local: "x"}, Value: "space"}, {Name: xml.Name{Space: "space", Local: "foo"}, Value: "value"}, {Name: xml.Name{Space: "x", Local: "bar"}, Value: "other"}}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc prevns")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "local"}, Attr: []xml.Attr{{Name: xml.Name{Space: "xmlns", Local: "x"}, Value: "space"}}}))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "space", Local: "foo"}, Attr: []xml.Attr{{Name: xml.Name{Space: "space", Local: "x"}, Value: "y"}}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc nested")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "foo"}, Attr: []xml.Attr{{Name: xml.Name{Space: "xmlns", Local: "x"}, Value: "space1"}}}))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "foo"}, Attr: []xml.Attr{{Name: xml.Name{Space: "xmlns", Local: "x"}, Value: "space2"}}}))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "foo"}, Attr: []xml.Attr{{Name: xml.Name{Space: "space1", Local: "a"}, Value: "space1 value"}, {Name: xml.Name{Space: "space2", Local: "b"}, Value: "space2 value"}}}))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "", Local: "foo"}}))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "", Local: "foo"}}))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "foo"}, Attr: []xml.Attr{{Name: xml.Name{Space: "space1", Local: "a"}, Value: "space1 value"}, {Name: xml.Name{Space: "space2", Local: "b"}, Value: "space2 value"}}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc several")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "space", Local: "foo"}, Attr: []xml.Attr{{Name: xml.Name{Space: "xmlns", Local: "a"}, Value: "space"}, {Name: xml.Name{Space: "xmlns", Local: "b"}, Value: "space"}, {Name: xml.Name{Space: "space", Local: "x"}, Value: "value"}}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc redefine")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "foo"}, Attr: []xml.Attr{{Name: xml.Name{Space: "foo", Local: "xmlns"}, Value: "space"}}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc xmlxmlns")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "space", Local: "foo"}, Attr: []xml.Attr{{Name: xml.Name{Space: "xml", Local: "xmlns"}, Value: "space"}}}))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "space", Local: "foo"}, Attr: []xml.Attr{{Name: xml.Name{Space: "http://www.w3.org/XML/1998/namespace", Local: "xmlns"}, Value: "space"}}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc emptyattr")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "foo"}, Attr: []xml.Attr{{Name: xml.Name{Space: "", Local: ""}, Value: "value"}, {Name: xml.Name{Space: "xmlns", Local: "foo"}, Value: ""}}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc badurl")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "/34", Local: "foo"}, Attr: []xml.Attr{{Name: xml.Name{Space: "/34", Local: "x"}, Value: "value"}, {Name: xml.Name{Space: "a/b/", Local: "y"}, Value: "1"}, {Name: xml.Name{Space: "u:v", Local: "z"}, Value: "2"}, {Name: xml.Name{Space: "http://x/1abc", Local: "w"}, Value: "3"}}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc reserved")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "foo"}, Attr: []xml.Attr{{Name: xml.Name{Space: "http://www.w3.org/2001/xmlSchema-instance", Local: "nil"}, Value: "true"}, {Name: xml.Name{Space: "http://www.w3.org/2001/XMLSchema-instance", Local: "nil"}, Value: "true"}, {Name: xml.Name{Space: "http://w/XmL", Local: "a"}, Value: "1"}, {Name: xml.Name{Space: "http://w/xm", Local: "b"}, Value: "2"}}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc seq")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "r"}, Attr: []xml.Attr{{Name: xml.Name{Space: "http://a/p", Local: "x"}, Value: "1"}, {Name: xml.Name{Space: "http://b/p", Local: "y"}, Value: "2"}, {Name: xml.Name{Space: "http://c/p", Local: "z"}, Value: "3"}, {Name: xml.Name{Space: "p", Local: "w"}, Value: "4"}}}))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "", Local: "r"}}))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "r"}, Attr: []xml.Attr{{Name: xml.Name{Space: "http://d/p", Local: "x"}, Value: "1"}}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc attrescape")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "sp&ce\"", Local: "a"}, Attr: []xml.Attr{{Name: xml.Name{Space: "", Local: "v"}, Value: "<\"&'\t\n\r>"}}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc indent")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "  ")
		step(&buf, e.EncodeToken(xml.ProcInst{Target: "xml", Inst: []byte("version=\"1.0\"")}))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "a"}, Attr: []xml.Attr{}}))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "b"}, Attr: []xml.Attr{}}))
		step(&buf, e.EncodeToken(xml.CharData("text")))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "", Local: "b"}}))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "c"}, Attr: []xml.Attr{}}))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "", Local: "c"}}))
		step(&buf, e.EncodeToken(xml.Comment("note")))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "d"}, Attr: []xml.Attr{}}))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "e"}, Attr: []xml.Attr{}}))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "", Local: "e"}}))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "", Local: "d"}}))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "", Local: "a"}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc prefix")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent(">>", "\t")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "a"}, Attr: []xml.Attr{{Name: xml.Name{Space: "", Local: "x"}, Value: "1"}}}))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "u", Local: "b"}, Attr: []xml.Attr{}}))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "u", Local: "b"}}))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "", Local: "a"}}))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "z"}, Attr: []xml.Attr{}}))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "", Local: "z"}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc prefixonly")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("#", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "a"}, Attr: []xml.Attr{}}))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "b"}, Attr: []xml.Attr{}}))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "", Local: "b"}}))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "", Local: "a"}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc xmlprocinst")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.ProcInst{Target: "xml", Inst: []byte("Instruction")}))
		step(&buf, e.EncodeToken(xml.ProcInst{Target: "Target", Inst: []byte("Instruction")}))
		step(&buf, e.EncodeToken(xml.ProcInst{Target: "xml", Inst: []byte("Instruction")}))
		step(&buf, e.Flush())
		step(&buf, e.EncodeToken(xml.ProcInst{Target: "xml", Inst: []byte("again")}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc close")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "a"}, Attr: []xml.Attr{}}))
		step(&buf, e.Close())
		step(&buf, e.EncodeToken(xml.CharData("x")))
		step(&buf, e.Close())
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc closeok")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "a"}, Attr: []xml.Attr{}}))
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "", Local: "a"}}))
		step(&buf, e.Close())
		step(&buf, e.EncodeToken(xml.EndElement{Name: xml.Name{Space: "", Local: "a"}}))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "b"}, Attr: []xml.Attr{}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc bigflush")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.CharData("yyyyyyyyyy")))
		step(&buf, e.EncodeToken(xml.CharData(strings.Repeat("x", 5000))))
		step(&buf, e.EncodeToken(xml.CharData(strings.Repeat("z", 4090))))
		step(&buf, e.EncodeToken(xml.CharData("wwwwwwwwwwwwwwwwwwww")))
		step(&buf, e.EncodeToken(xml.ProcInst{Target: "xml", Inst: []byte("late")}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("enc bigdirect")
	{
		var buf bytes.Buffer
		e := xml.NewEncoder(&buf)
		e.Indent("", "")
		step(&buf, e.EncodeToken(xml.CharData(strings.Repeat("x", 5000))))
		step(&buf, e.EncodeToken(xml.ProcInst{Target: "xml", Inst: []byte("ok")}))
		step(&buf, e.EncodeToken(xml.StartElement{Name: xml.Name{Space: "", Local: "a"}, Attr: []xml.Attr{}}))
		e.Flush()
		fmt.Println("  out " + show(buf.Bytes()))
	}
	fmt.Println("== roundtrip")
	{
		var out bytes.Buffer
		d := xml.NewDecoder(bytes.NewReader([]byte("\n<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE html PUBLIC \"-//W3C//DTD XHTML 1.0 Transitional//EN\"\n  \"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd\">\n<body xmlns:foo=\"ns1\" xmlns=\"ns2\" xmlns:tag=\"ns3\" \r\n\t  >\n  <hello lang=\"en\">World &lt;&gt;&apos;&quot; &#x767d;&#40300;\xe7\xbf\x94</hello>\n  <query>q</query>\n  <goodbye />\n  <outer foo:attr=\"value\" xmlns:tag=\"ns4\">\n    <inner/>\n  </outer>\n  <tag:name>\n    <![CDATA[Some text here.]]>\n  </tag:name>\n</body><!-- missing final newline -->")))
		e := xml.NewEncoder(&out)
		for {
			t, err := d.Token()
			if err != nil {
				fmt.Println("  " + errStr(err))
				break
			}
			if err := e.EncodeToken(t); err != nil {
				fmt.Println("  enc " + err.Error())
			}
		}
		e.Flush()
		fmt.Println("  " + show(out.Bytes()))
	}
	fmt.Println("== escape")
	{
		var b1, b2 bytes.Buffer
		xml.EscapeText(&b1, []byte(""))
		xml.Escape(&b2, []byte(""))
		fmt.Println("  " + show(b1.Bytes()) + " " + show(b2.Bytes()))
	}
	{
		var b1, b2 bytes.Buffer
		xml.EscapeText(&b1, []byte("plain"))
		xml.Escape(&b2, []byte("plain"))
		fmt.Println("  " + show(b1.Bytes()) + " " + show(b2.Bytes()))
	}
	{
		var b1, b2 bytes.Buffer
		xml.EscapeText(&b1, []byte("<a href=\"x\">&'</a>"))
		xml.Escape(&b2, []byte("<a href=\"x\">&'</a>"))
		fmt.Println("  " + show(b1.Bytes()) + " " + show(b2.Bytes()))
	}
	{
		var b1, b2 bytes.Buffer
		xml.EscapeText(&b1, []byte("\t\n\r"))
		xml.Escape(&b2, []byte("\t\n\r"))
		fmt.Println("  " + show(b1.Bytes()) + " " + show(b2.Bytes()))
	}
	{
		var b1, b2 bytes.Buffer
		xml.EscapeText(&b1, []byte("\x00\x01\x1f"))
		xml.Escape(&b2, []byte("\x00\x01\x1f"))
		fmt.Println("  " + show(b1.Bytes()) + " " + show(b2.Bytes()))
	}
	{
		var b1, b2 bytes.Buffer
		xml.EscapeText(&b1, []byte("\xed\x9f\xbf\xee\x80\x80\xef\xbf\xbd\xef\xbf\xbe\xef\xbf\xbf"))
		xml.Escape(&b2, []byte("\xed\x9f\xbf\xee\x80\x80\xef\xbf\xbd\xef\xbf\xbe\xef\xbf\xbf"))
		fmt.Println("  " + show(b1.Bytes()) + " " + show(b2.Bytes()))
	}
	{
		var b1, b2 bytes.Buffer
		xml.EscapeText(&b1, []byte("\xc3\xa9\xf0\x9f\x98\x80"))
		xml.Escape(&b2, []byte("\xc3\xa9\xf0\x9f\x98\x80"))
		fmt.Println("  " + show(b1.Bytes()) + " " + show(b2.Bytes()))
	}
	{
		var b1, b2 bytes.Buffer
		xml.EscapeText(&b1, []byte("\xff"))
		xml.Escape(&b2, []byte("\xff"))
		fmt.Println("  " + show(b1.Bytes()) + " " + show(b2.Bytes()))
	}
	{
		var b1, b2 bytes.Buffer
		xml.EscapeText(&b1, []byte("a\xc3"))
		xml.Escape(&b2, []byte("a\xc3"))
		fmt.Println("  " + show(b1.Bytes()) + " " + show(b2.Bytes()))
	}
	{
		var b1, b2 bytes.Buffer
		xml.EscapeText(&b1, []byte("\xed\xa0\x80"))
		xml.Escape(&b2, []byte("\xed\xa0\x80"))
		fmt.Println("  " + show(b1.Bytes()) + " " + show(b2.Bytes()))
	}
	fmt.Println("== tree")
	fmt.Println("tree 0")
	{
		el, err := parseTree("<root a='1'>hello <b>bold</b> world<!--c--><i x='y'/><![CDATA[cd&]]> end</root>")
		if err != nil {
			fmt.Println("  error " + show([]byte(err.Error())))
		} else {
			s, _ := marshalIndent(el, "", "")
			fmt.Println("  " + show([]byte(s)))
			s, _ = marshalIndent(el, "", "  ")
			fmt.Println("  " + show([]byte(s)))
			fmt.Println("  text " + show([]byte(el.text())) + fmt.Sprintf(" kids %d", len(el.Children)))
			for _, k := range []string{"b", "item", "c", "zz"} {
				if f := el.find(k); f != nil {
					fmt.Println("  find " + k + " found " + nm(f.Name) + " " + show([]byte(f.text())))
				} else {
					fmt.Println("  find " + k + " none")
				}
				fmt.Printf("  all %s %d\n", k, len(el.findAll(k)))
			}
			for _, k := range []string{"a", "x", "xmlns"} {
				v, ok := el.attrValue(k)
				fmt.Printf("  attr %s %v %s\n", k, ok, show([]byte(v)))
			}
		}
	}
	fmt.Println("tree 1")
	{
		el, err := parseTree("<?xml version='1.0'?>\n<!-- pre -->\n<list xmlns='urn:x' xmlns:q='urn:q'>\n  <item q:id='1'>one</item>\n  <item q:id='2'>two &amp; more</item>\n</list>\n")
		if err != nil {
			fmt.Println("  error " + show([]byte(err.Error())))
		} else {
			s, _ := marshalIndent(el, "", "")
			fmt.Println("  " + show([]byte(s)))
			s, _ = marshalIndent(el, "", "  ")
			fmt.Println("  " + show([]byte(s)))
			fmt.Println("  text " + show([]byte(el.text())) + fmt.Sprintf(" kids %d", len(el.Children)))
			for _, k := range []string{"b", "item", "c", "zz"} {
				if f := el.find(k); f != nil {
					fmt.Println("  find " + k + " found " + nm(f.Name) + " " + show([]byte(f.text())))
				} else {
					fmt.Println("  find " + k + " none")
				}
				fmt.Printf("  all %s %d\n", k, len(el.findAll(k)))
			}
			for _, k := range []string{"a", "x", "xmlns"} {
				v, ok := el.attrValue(k)
				fmt.Printf("  attr %s %v %s\n", k, ok, show([]byte(v)))
			}
		}
	}
	fmt.Println("tree 2")
	{
		el, err := parseTree("<a><b><c/></b></a>")
		if err != nil {
			fmt.Println("  error " + show([]byte(err.Error())))
		} else {
			s, _ := marshalIndent(el, "", "")
			fmt.Println("  " + show([]byte(s)))
			s, _ = marshalIndent(el, "", "  ")
			fmt.Println("  " + show([]byte(s)))
			fmt.Println("  text " + show([]byte(el.text())) + fmt.Sprintf(" kids %d", len(el.Children)))
			for _, k := range []string{"b", "item", "c", "zz"} {
				if f := el.find(k); f != nil {
					fmt.Println("  find " + k + " found " + nm(f.Name) + " " + show([]byte(f.text())))
				} else {
					fmt.Println("  find " + k + " none")
				}
				fmt.Printf("  all %s %d\n", k, len(el.findAll(k)))
			}
			for _, k := range []string{"a", "x", "xmlns"} {
				v, ok := el.attrValue(k)
				fmt.Printf("  attr %s %v %s\n", k, ok, show([]byte(v)))
			}
		}
	}
	fmt.Println("tree 3")
	{
		el, err := parseTree("<a>unclosed")
		if err != nil {
			fmt.Println("  error " + show([]byte(err.Error())))
		} else {
			s, _ := marshalIndent(el, "", "")
			fmt.Println("  " + show([]byte(s)))
			s, _ = marshalIndent(el, "", "  ")
			fmt.Println("  " + show([]byte(s)))
			fmt.Println("  text " + show([]byte(el.text())) + fmt.Sprintf(" kids %d", len(el.Children)))
			for _, k := range []string{"b", "item", "c", "zz"} {
				if f := el.find(k); f != nil {
					fmt.Println("  find " + k + " found " + nm(f.Name) + " " + show([]byte(f.text())))
				} else {
					fmt.Println("  find " + k + " none")
				}
				fmt.Printf("  all %s %d\n", k, len(el.findAll(k)))
			}
			for _, k := range []string{"a", "x", "xmlns"} {
				v, ok := el.attrValue(k)
				fmt.Printf("  attr %s %v %s\n", k, ok, show([]byte(v)))
			}
		}
	}
	fmt.Println("tree 4")
	{
		el, err := parseTree("<!-- only -->")
		if err != nil {
			fmt.Println("  error " + show([]byte(err.Error())))
		} else {
			s, _ := marshalIndent(el, "", "")
			fmt.Println("  " + show([]byte(s)))
			s, _ = marshalIndent(el, "", "  ")
			fmt.Println("  " + show([]byte(s)))
			fmt.Println("  text " + show([]byte(el.text())) + fmt.Sprintf(" kids %d", len(el.Children)))
			for _, k := range []string{"b", "item", "c", "zz"} {
				if f := el.find(k); f != nil {
					fmt.Println("  find " + k + " found " + nm(f.Name) + " " + show([]byte(f.text())))
				} else {
					fmt.Println("  find " + k + " none")
				}
				fmt.Printf("  all %s %d\n", k, len(el.findAll(k)))
			}
			for _, k := range []string{"a", "x", "xmlns"} {
				v, ok := el.attrValue(k)
				fmt.Printf("  attr %s %v %s\n", k, ok, show([]byte(v)))
			}
		}
	}
	fmt.Println("tree 5")
	{
		el, err := parseTree("<a></b>")
		if err != nil {
			fmt.Println("  error " + show([]byte(err.Error())))
		} else {
			s, _ := marshalIndent(el, "", "")
			fmt.Println("  " + show([]byte(s)))
			s, _ = marshalIndent(el, "", "  ")
			fmt.Println("  " + show([]byte(s)))
			fmt.Println("  text " + show([]byte(el.text())) + fmt.Sprintf(" kids %d", len(el.Children)))
			for _, k := range []string{"b", "item", "c", "zz"} {
				if f := el.find(k); f != nil {
					fmt.Println("  find " + k + " found " + nm(f.Name) + " " + show([]byte(f.text())))
				} else {
					fmt.Println("  find " + k + " none")
				}
				fmt.Printf("  all %s %d\n", k, len(el.findAll(k)))
			}
			for _, k := range []string{"a", "x", "xmlns"} {
				v, ok := el.attrValue(k)
				fmt.Printf("  attr %s %v %s\n", k, ok, show([]byte(v)))
			}
		}
	}
	fmt.Println("tree 6")
	{
		el, err := parseTree("<a/><b>second</b>")
		if err != nil {
			fmt.Println("  error " + show([]byte(err.Error())))
		} else {
			s, _ := marshalIndent(el, "", "")
			fmt.Println("  " + show([]byte(s)))
			s, _ = marshalIndent(el, "", "  ")
			fmt.Println("  " + show([]byte(s)))
			fmt.Println("  text " + show([]byte(el.text())) + fmt.Sprintf(" kids %d", len(el.Children)))
			for _, k := range []string{"b", "item", "c", "zz"} {
				if f := el.find(k); f != nil {
					fmt.Println("  find " + k + " found " + nm(f.Name) + " " + show([]byte(f.text())))
				} else {
					fmt.Println("  find " + k + " none")
				}
				fmt.Printf("  all %s %d\n", k, len(el.findAll(k)))
			}
			for _, k := range []string{"a", "x", "xmlns"} {
				v, ok := el.attrValue(k)
				fmt.Printf("  attr %s %v %s\n", k, ok, show([]byte(v)))
			}
		}
	}
	fmt.Println("tree 7")
	{
		el, err := parseTree("<x>&lt;tag&gt; \"q\" 'a'\ttab\nnl</x>")
		if err != nil {
			fmt.Println("  error " + show([]byte(err.Error())))
		} else {
			s, _ := marshalIndent(el, "", "")
			fmt.Println("  " + show([]byte(s)))
			s, _ = marshalIndent(el, "", "  ")
			fmt.Println("  " + show([]byte(s)))
			fmt.Println("  text " + show([]byte(el.text())) + fmt.Sprintf(" kids %d", len(el.Children)))
			for _, k := range []string{"b", "item", "c", "zz"} {
				if f := el.find(k); f != nil {
					fmt.Println("  find " + k + " found " + nm(f.Name) + " " + show([]byte(f.text())))
				} else {
					fmt.Println("  find " + k + " none")
				}
				fmt.Printf("  all %s %d\n", k, len(el.findAll(k)))
			}
			for _, k := range []string{"a", "x", "xmlns"} {
				v, ok := el.attrValue(k)
				fmt.Printf("  attr %s %v %s\n", k, ok, show([]byte(v)))
			}
		}
	}
	fmt.Println("== build")
	{
		el := &Element{Name: xml.Name{Local: "doc"}, Attr: []xml.Attr{{Name: xml.Name{Local: "v"}, Value: "1 < 2"}}, Children: []Node{
			{Kind: 0, El: &Element{Name: xml.Name{Space: "urn:a", Local: "x"}, Attr: []xml.Attr{{Name: xml.Name{Space: "urn:b", Local: "y"}, Value: "q"}}, Children: []Node{{Kind: 1, Text: "t&t"}}}},
			{Kind: 2, Data: []byte(" hi ")},
			{Kind: 0, El: &Element{Name: xml.Name{Local: "empty"}}},
			{Kind: 1, Text: "tail"},
		}}
		s, err := marshalIndent(el, "", "")
		fmt.Println("  "+show([]byte(s)), err)
		s, err = marshalIndent(el, "> ", "\t")
		fmt.Println("  "+show([]byte(s)), err)
		bad := &Element{Name: xml.Name{Local: "a"}, Children: []Node{{Kind: 2, Data: []byte("x-->")}}}
		_, err = marshalIndent(bad, "", "")
		fmt.Println("  " + err.Error())
		bad2 := &Element{Name: xml.Name{Local: ""}}
		_, err = marshalIndent(bad2, "", "")
		fmt.Println("  " + err.Error())
	}
}
