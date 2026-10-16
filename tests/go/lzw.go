package main

import (
	"bytes"
	"compress/lzw"
	"encoding/hex"
	"fmt"
	"hash/crc32"
	"io"
)

func gen(n int, seed uint32, mask byte) []byte {
	out := make([]byte, n)
	s := seed
	for i := 0; i < n; i++ {
		s = (s*1103515245 + 12345) & 0x7fffffff
		out[i] = byte(s>>16) & mask
	}
	return out
}

func text(n int) []byte {
	words := []string{"the ", "quick ", "brown ", "fox ", "jumps ", "over ", "lazy ", "dog ", "\n"}
	var b []byte
	s := uint32(7)
	for len(b) < n {
		s = (s*1103515245 + 12345) & 0x7fffffff
		b = append(b, words[(s>>16)%9]...)
	}
	return b[:n]
}

func comp(data []byte, order lzw.Order, lw int, chunk int) ([]byte, error) {
	var buf bytes.Buffer
	w := lzw.NewWriter(&buf, order, lw)
	if chunk <= 0 {
		chunk = len(data) + 1
	}
	for i := 0; i < len(data); i += chunk {
		j := i + chunk
		if j > len(data) {
			j = len(data)
		}
		if _, err := w.Write(data[i:j]); err != nil {
			return nil, err
		}
	}
	if err := w.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

func readAll(b []byte, order lzw.Order, lw int) ([]byte, string) {
	r := lzw.NewReader(bytes.NewReader(b), order, lw)
	out, err := io.ReadAll(r)
	es := ""
	if err != nil {
		es = err.Error()
	}
	return out, es
}

func show(b []byte) string {
	if len(b) <= 48 {
		return hex.EncodeToString(b)
	}
	return fmt.Sprintf("len=%d crc=%d head=%s", len(b), crc32.ChecksumIEEE(b), hex.EncodeToString(b[:16]))
}

func main() {
	orders := []lzw.Order{lzw.LSB, lzw.MSB}
	for _, o := range orders {
		for lw := 2; lw <= 8; lw++ {
			mask := byte(1<<lw - 1)
			ins := [][]byte{{}, {0}, {1, 1, 1, 1, 1, 1, 1, 1}, gen(40, uint32(lw), mask), gen(700, 99, mask&3)}
			if lw == 8 {
				ins = append(ins, []byte("TOBEORNOTTOBEORTOBEORNOT#"), text(600))
			}
			for _, in := range ins {
				c, err := comp(in, o, lw, 0)
				if err != nil {
					fmt.Println("err", err)
					continue
				}
				d, e := readAll(c, o, lw)
				fmt.Println(o, lw, len(in), show(c), bytes.Equal(d, in), e)
			}
		}
	}
	// 码表用完（清表）
	for _, o := range orders {
		big := gen(9000, 5, 255)
		c, _ := comp(big, o, 8, 0)
		d, e := readAll(c, o, 8)
		fmt.Println("big8", o, show(c), bytes.Equal(d, big), e)
		big2 := gen(12000, 3, 3)
		c2, _ := comp(big2, o, 2, 0)
		c3, _ := comp(big2, o, 2, 777)
		d2, e2 := readAll(c2, o, 2)
		fmt.Println("big2", o, show(c2), bytes.Equal(c2, c3), bytes.Equal(d2, big2), e2)
		t := text(5000)
		c4, _ := comp(t, o, 8, 0)
		c5, _ := comp(t, o, 8, 13)
		d4, e4 := readAll(c4, o, 8)
		fmt.Println("text", o, show(c4), bytes.Equal(c4, c5), bytes.Equal(d4, t), e4)
	}
	// 写入端的错误
	_, err := comp([]byte{1, 2, 3}, lzw.LSB, 1, 0)
	fmt.Println(err)
	_, err = comp([]byte{1, 2, 3}, lzw.LSB, 9, 0)
	fmt.Println(err)
	_, err = comp([]byte{1, 2, 3}, lzw.Order(2), 8, 0)
	fmt.Println(err)
	_, err = comp([]byte{1, 2, 4}, lzw.MSB, 2, 0)
	fmt.Println(err)
	{
		var buf bytes.Buffer
		w := lzw.NewWriter(&buf, lzw.LSB, 8)
		n, err := w.Write(nil)
		fmt.Println(n, err)
		n, err = w.Write([]byte("ab"))
		fmt.Println(n, err)
		fmt.Println(w.Close())
		fmt.Println(w.Close())
		n, err = w.Write([]byte("cd"))
		fmt.Println(n, err, hex.EncodeToString(buf.Bytes()))
	}
	{
		var buf bytes.Buffer
		w := lzw.NewWriter(&buf, lzw.MSB, 3)
		_, err := w.Write([]byte{9})
		fmt.Println(err)
		_, err = w.Write([]byte{1})
		fmt.Println(err)
		fmt.Println(w.Close())
	}
	// 读取端的错误
	good, _ := comp([]byte("TOBEORNOTTOBEORTOBEORNOT"), lzw.LSB, 8, 0)
	for _, n := range []int{0, 1, 2, 5, len(good) - 1} {
		d, e := readAll(good[:n], lzw.LSB, 8)
		fmt.Printf("trunc %d %q %s\n", n, d, e)
	}
	bads := [][]byte{{0xff, 0xff, 0xff}, {0x00, 0xff, 0x0f}, {0x41, 0x0c, 0x0c, 0x0c, 0xff, 0xff}, {0x00, 0x01}, gen(30, 1, 255)}
	for _, b := range bads {
		for _, o := range orders {
			for _, lw := range []int{2, 7, 8} {
				d, e := readAll(b, o, lw)
				fmt.Printf("bad %s %d %d %s %s\n", hex.EncodeToString(b), o, lw, hex.EncodeToString(d), e)
			}
		}
	}
	_, e := readAll(good, lzw.LSB, 1)
	fmt.Println(e)
	_, e = readAll(good, lzw.Order(5), 8)
	fmt.Println(e)
	// 手工构造：码 == hi 的 KwKwK 情况
	d, e := readAll([]byte{0x80, 0x00, 0x08, 0x02, 0x04, 0x01}, lzw.MSB, 7)
	fmt.Println(hex.EncodeToString(d), e)
}
