// tests/go/smtp.go：对拍 Go 1.27 net/smtp。
// 输入 testdata/smtp/cases.txt：case 块用假连接（服务器应答预先给定，记录客户端写出的字节）跑 Client 方法；
// send 行在本机起一个 TCP 服务（一次写出全部应答，再读到连接关闭），调用真正的 smtp.SendMail。
package main

import (
	"bufio"
	"bytes"
	"encoding/hex"
	"fmt"
	"io"
	"net"
	"net/smtp"
	"os"
	"strings"
	"time"
)

type faker struct {
	io.Reader
	io.Writer
}

func (f faker) Close() error                     { return nil }
func (f faker) LocalAddr() net.Addr              { return nil }
func (f faker) RemoteAddr() net.Addr             { return nil }
func (f faker) SetDeadline(time.Time) error      { return nil }
func (f faker) SetReadDeadline(time.Time) error  { return nil }
func (f faker) SetWriteDeadline(time.Time) error { return nil }

func dec(s string) string { b, _ := hex.DecodeString(s); return string(b) }

func es(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

func main() {
	f, _ := os.Open("tests/go/testdata/smtp/cases.txt")
	sc := bufio.NewScanner(f)
	sc.Buffer(nil, 1<<20)
	n := 0
	var c *smtp.Client
	var out *bytes.Buffer
	for sc.Scan() {
		p := strings.Split(sc.Text(), "|")
		switch p[0] {
		case "case":
			n++
			out = &bytes.Buffer{}
			var err error
			c, err = smtp.NewClient(faker{strings.NewReader(dec(p[2])), out}, dec(p[1]))
			fmt.Printf("case %d new err=%q\n", n, es(err))
		case "end":
			fmt.Printf("  out=%q\n", out.String())
			c = nil
		case "send":
			n++
			ln, _ := net.Listen("tcp", "127.0.0.1:0")
			got := make(chan []byte)
			go func() {
				conn, err := ln.Accept()
				if err != nil {
					return
				}
				conn.Write([]byte(dec(p[1])))
				b, _ := io.ReadAll(conn)
				conn.Close()
				got <- b
			}()
			var a smtp.Auth
			switch p[2] {
			case "plain":
				a = smtp.PlainAuth("", "user", "pass", "127.0.0.1")
			case "cram":
				a = smtp.CRAMMD5Auth("user", "secret")
			}
			to := strings.Split(dec(p[4]), "\x00")
			err := smtp.SendMail(ln.Addr().String(), a, dec(p[3]), to, []byte(dec(p[5])))
			var b []byte
			if strings.Contains(es(err), "CR or LF") {
				b = nil
			} else {
				b = <-got
			}
			ln.Close()
			fmt.Printf("send %d err=%q\n  out=%q\n", n, es(err), b)
		default:
			if c == nil {
				continue
			}
			args := make([]string, len(p)-1)
			for i := range args {
				args[i] = dec(p[i+1])
			}
			var err error
			switch p[0] {
			case "hello":
				err = c.Hello(args[0])
			case "verify":
				err = c.Verify(args[0])
			case "mail":
				err = c.Mail(args[0])
			case "rcpt":
				err = c.Rcpt(args[0])
			case "data":
				var w io.WriteCloser
				w, err = c.Data()
				if err == nil {
					w.Write([]byte(args[0]))
					err = w.Close()
				}
			case "reset":
				err = c.Reset()
			case "noop":
				err = c.Noop()
			case "quit":
				err = c.Quit()
			case "ext":
				ok, param := c.Extension(args[0])
				fmt.Printf("  ext %s %v %q\n", args[0], ok, param)
				continue
			case "plain":
				err = c.Auth(smtp.PlainAuth(args[0], args[1], args[2], args[3]))
			case "cram":
				err = c.Auth(smtp.CRAMMD5Auth(args[0], args[1]))
			}
			fmt.Printf("  %s err=%q\n", p[0], es(err))
		}
	}
}
