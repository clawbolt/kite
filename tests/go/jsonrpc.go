// tests/go/jsonrpc.go：对拍 Go 1.27 net/rpc/jsonrpc。
// 输入 tests/go/testdata/jsonrpc/cases.txt，每行一个用例（字段十六进制）：
//   server|输入字节        服务端按顺序处理（ServeRequest 循环，同 ServeCodec 但不并发），打印写出的字节
//   client|调用列表|应答字节  调用列表是 "方法:参数" 以逗号分隔（参数 "-" 表示 nil）；先发出全部调用再放行应答，
//                            打印客户端写出的字节和每个调用的结果
package main

import (
	"bufio"
	"bytes"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/rpc"
	"net/rpc/jsonrpc"
	"os"
	"strconv"
	"strings"
)

type Echo struct{}

func (Echo) Raw(args json.RawMessage, reply *json.RawMessage) error {
	*reply = args
	return nil
}

func (Echo) Fail(args json.RawMessage, reply *json.RawMessage) error {
	var s string
	json.Unmarshal(args, &s)
	return errors.New(s)
}

func (Echo) Bad(args json.RawMessage, reply *json.RawMessage) error {
	*reply = json.RawMessage("{")
	return nil
}

func (Echo) Len(args json.RawMessage, reply *json.RawMessage) error {
	*reply = json.RawMessage(strconv.Itoa(len(args)))
	return nil
}

type rw struct {
	r    io.Reader
	gate chan struct{}
	w    bytes.Buffer
}

func (c *rw) Read(p []byte) (int, error) {
	if c.gate != nil {
		<-c.gate
	}
	return c.r.Read(p)
}

func (c *rw) Write(p []byte) (int, error) { return c.w.Write(p) }
func (c *rw) Close() error                { return nil }

func dec(s string) []byte {
	b, err := hex.DecodeString(s)
	if err != nil {
		panic(err)
	}
	return b
}

func serve(in []byte) []byte {
	srv := rpc.NewServer()
	srv.Register(new(Echo))
	srv.RegisterName("x.y", new(Echo))
	conn := &rw{r: bytes.NewReader(in)}
	codec := jsonrpc.NewServerCodec(conn)
	for {
		err := srv.ServeRequest(codec)
		if err == nil {
			continue
		}
		if err == io.EOF || err == io.ErrUnexpectedEOF || strings.HasPrefix(err.Error(), "rpc: server cannot decode request") {
			break
		}
	}
	return conn.w.Bytes()
}

func main() {
	f, err := os.Open("testdata/jsonrpc/cases.txt")
	if err != nil {
		panic(err)
	}
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	n := 0
	for sc.Scan() {
		line := sc.Text()
		if line == "" {
			continue
		}
		p := strings.Split(line, "|")
		n++
		switch p[0] {
		case "server":
			fmt.Printf("server %d\n", n)
			fmt.Printf("out %q\n", serve(dec(p[1])))
		case "client":
			fmt.Printf("client %d\n", n)
			conn := &rw{r: bytes.NewReader(dec(p[2])), gate: make(chan struct{})}
			client := rpc.NewClientWithCodec(jsonrpc.NewClientCodec(conn))
			var calls []*rpc.Call
			if p[1] != "" {
				for _, c := range strings.Split(p[1], ",") {
					mp := strings.SplitN(c, ":", 2)
					var param any
					if mp[1] != "-" {
						param = json.RawMessage(dec(mp[1]))
					}
					reply := new(json.RawMessage)
					calls = append(calls, client.Go(string(dec(mp[0])), param, reply, make(chan *rpc.Call, 1)))
				}
			}
			out := append([]byte(nil), conn.w.Bytes()...)
			close(conn.gate)
			fmt.Printf("out %q\n", out)
			for i, c := range calls {
				<-c.Done
				if c.Error != nil {
					fmt.Printf("call %d err %q\n", i, c.Error.Error())
				} else {
					fmt.Printf("call %d ok %q\n", i, []byte(*c.Reply.(*json.RawMessage)))
				}
			}
		}
	}
}
