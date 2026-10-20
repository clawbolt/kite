// 对拍 Go 1.27 net/rpc（gob 编码）：cd tests/go && go run rpc.go > rpc.expected
package main

import (
	"bufio"
	"bytes"
	"encoding/gob"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/rpc"
	"strings"
)

type Args struct{ A, B int }

type Quotient struct{ Quo, Rem int }

type Arith int

func (t *Arith) Multiply(args *Args, reply *int) error {
	*reply = args.A * args.B
	return nil
}

func (t *Arith) Divide(args Args, quo *Quotient) error {
	if args.B == 0 {
		return errors.New("divide by zero")
	}
	quo.Quo = args.A / args.B
	quo.Rem = args.A % args.B
	return nil
}

func (t *Arith) Sum(xs []int, reply *int) error {
	for _, x := range xs {
		*reply += x
	}
	return nil
}

func (t *Arith) Fail(s string, reply *string) error { return errors.New("") }

type Str int

func (t *Str) Echo(s string, reply *string) error {
	*reply = s + "!"
	return nil
}

func (t *Str) Split(s string, reply *[]string) error {
	*reply = strings.Split(s, ",")
	return nil
}

// 和 net/rpc 内部的 gobServerCodec 一样
type srvCodec struct {
	rwc    io.ReadWriteCloser
	dec    *gob.Decoder
	enc    *gob.Encoder
	encBuf *bufio.Writer
}

func (c *srvCodec) ReadRequestHeader(r *rpc.Request) error { return c.dec.Decode(r) }
func (c *srvCodec) ReadRequestBody(body any) error        { return c.dec.Decode(body) }
func (c *srvCodec) WriteResponse(r *rpc.Response, body any) (err error) {
	if err = c.enc.Encode(r); err != nil {
		return
	}
	if err = c.enc.Encode(body); err != nil {
		return
	}
	return c.encBuf.Flush()
}
func (c *srvCodec) Close() error { return nil }

type rw struct {
	r io.Reader
	w bytes.Buffer
}

func (x *rw) Read(p []byte) (int, error)  { return x.r.Read(p) }
func (x *rw) Write(p []byte) (int, error) { return x.w.Write(p) }
func (x *rw) Close() error                { return nil }

func serve(srv *rpc.Server, input []byte) []byte {
	conn := &rw{r: bytes.NewReader(input)}
	buf := bufio.NewWriter(conn)
	c := &srvCodec{rwc: conn, dec: gob.NewDecoder(conn), enc: gob.NewEncoder(buf), encBuf: buf}
	for {
		err := srv.ServeRequest(c)
		if err == nil {
			continue
		}
		if err == io.EOF || err == io.ErrUnexpectedEOF || strings.HasPrefix(err.Error(), "rpc: server cannot decode request") {
			break
		}
	}
	return conn.w.Bytes()
}

func errs(err error) string {
	if err == nil {
		return "ok"
	}
	return "err " + err.Error()
}

func main() {
	srv := rpc.NewServer()
	srv.Register(new(Arith))
	srv.Register(new(Str))

	// 1. 手工拼请求，服务端处理
	var in bytes.Buffer
	e := gob.NewEncoder(&in)
	req := func(m string, seq uint64, body any) {
		e.Encode(rpc.Request{ServiceMethod: m, Seq: seq})
		e.Encode(body)
	}
	req("Arith.Multiply", 0, Args{7, 8})
	req("Arith.Divide", 1, Args{17, 5})
	req("Arith.Divide", 2, Args{1, 0})
	req("Arith.Sum", 3, []int{1, 2, 3})
	req("Str.Echo", 4, "hi")
	req("Str.Split", 5, "a,b,,c")
	req("NoDot", 6, 1)
	req("Nope.Multiply", 7, Args{})
	req("Arith.Nope", 8, Args{})
	req("Arith.Multiply", 9, "wrong")
	req("Arith.Fail", 10, "x")
	req("Arith.Multiply", 11, Args{-3, 4})
	data := in.Bytes()
	fmt.Println("req", hex.EncodeToString(data))
	out := serve(srv, data)
	fmt.Println("resp", hex.EncodeToString(out))
	for n := 1; n < len(data); n += 37 {
		fmt.Println("trunc", n, hex.EncodeToString(serve(srv, data[:n])))
	}
	fmt.Println("garbage", hex.EncodeToString(serve(srv, []byte{3, 4, 0, 5})))

	// 2. 客户端：先发全部调用，再交给它服务端的应答
	cconn := &rw{r: bytes.NewReader(nil)}
	var pr *io.PipeReader
	var pw *io.PipeWriter
	pr, pw = io.Pipe()
	cconn.r = pr
	cl := rpc.NewClient(cconn)
	var r1, r4 int
	var q2, q3 Quotient
	var s5 string
	var bad int
	c1 := cl.Go("Arith.Multiply", Args{6, 7}, &r1, nil)
	c2 := cl.Go("Arith.Divide", Args{9, 2}, &q2, nil)
	c3 := cl.Go("Arith.Divide", Args{9, 0}, &q3, nil)
	c4 := cl.Go("Arith.Nope", Args{}, &r4, nil)
	c5 := cl.Go("Str.Echo", "yo", &s5, nil)
	c6 := cl.Go("Str.Echo", "z", &bad, nil)
	c7 := cl.Go("Arith.Multiply", Args{1, 1}, &r1, nil)
	sent := append([]byte(nil), cconn.w.Bytes()...)
	fmt.Println("client req", hex.EncodeToString(sent))
	resp := serve(srv, sent)
	fmt.Println("client resp", hex.EncodeToString(resp))
	pw.Write(resp)
	<-c1.Done
	fmt.Println("c1", errs(c1.Error), r1)
	<-c2.Done
	fmt.Println("c2", errs(c2.Error), q2.Quo, q2.Rem)
	<-c3.Done
	fmt.Println("c3", errs(c3.Error))
	<-c4.Done
	fmt.Println("c4", errs(c4.Error))
	<-c5.Done
	fmt.Println("c5", errs(c5.Error), s5)
	<-c6.Done
	fmt.Println("c6", errs(c6.Error))
	pw.Close()
	<-c7.Done
	fmt.Println("c7", errs(c7.Error))
	fmt.Println("after", errs(cl.Call("Arith.Multiply", Args{1, 1}, &r1)))
	fmt.Println("close", errs(cl.Close()), errs(cl.Close()))

	// 3. 应答被截断
	for _, n := range []int{0, 5, 30, 60, 90} {
		c := &rw{}
		pr, pw := io.Pipe()
		c.r = pr
		cl := rpc.NewClient(c)
		var x int
		call := cl.Go("Arith.Multiply", Args{2, 3}, &x, nil)
		r := serve(srv, c.w.Bytes())
		if n > len(r) {
			n = len(r)
		}
		pw.Write(r[:n])
		pw.Close()
		<-call.Done
		fmt.Println("short", n, errs(call.Error), x)
	}
}
