package main

import (
	"archive/zip"
	"bytes"
	"encoding/hex"
	"fmt"
	"hash/crc32"
	"io"
	"io/fs"
	"strings"
	"time"
)

func modStr(t time.Time) string {
	if t.IsZero() {
		return "zero"
	}
	return t.String()
}

func desc(h *zip.FileHeader, withC bool) string {
	cs := fmt.Sprint(h.CompressedSize, " ", h.CompressedSize64)
	if !withC {
		cs = "-"
	}
	return fmt.Sprintf("%s|%s|%v|%d|%d|%d|%d|%d|%d|%d|%s|%d %d|%s|%d|%s|%d|%s",
		h.Name, h.Comment, h.NonUTF8, h.CreatorVersion, h.ReaderVersion, h.Flags, h.Method,
		h.ModifiedTime, h.ModifiedDate, h.CRC32, cs, h.UncompressedSize, h.UncompressedSize64,
		hex.EncodeToString(h.Extra), h.ExternalAttrs, modStr(h.Modified), uint32(h.Mode()), h.Mode().String())
}

func dump(label string, b []byte) {
	fmt.Println(label, len(b), crc32.ChecksumIEEE(b), hex.EncodeToString(b))
}

func readAll(f *zip.File) string {
	rc, err := f.Open()
	if err != nil {
		return "open err " + err.Error()
	}
	b, err := io.ReadAll(rc)
	if err != nil {
		return "read err " + err.Error()
	}
	return "ok " + hex.EncodeToString(b)
}

func readBack(label string, b []byte, withC bool) {
	r, err := zip.NewReader(bytes.NewReader(b), int64(len(b)))
	if err != nil {
		fmt.Println(label, "reader err", err.Error())
		return
	}
	fmt.Println(label, "files", len(r.File), "comment", r.Comment)
	for _, f := range r.File {
		fmt.Println(" ", desc(&f.FileHeader, withC))
		if withC {
			off, err := f.DataOffset()
			fmt.Println("  offset", off, err == nil)
		}
		fmt.Println("  data", readAll(f))
	}
}

func errStr(err error) string {
	if err == nil {
		return "nil"
	}
	return err.Error()
}

var text = []byte(strings.Repeat("The quick brown fox jumps over the lazy dog. ", 40) + "zip!")

func main() {
	utc := time.Date(2023, 5, 17, 13, 45, 31, 0, time.UTC)
	plus2 := time.Date(2021, 12, 31, 23, 59, 58, 0, time.FixedZone("", 2*3600))
	nepal := time.Date(2010, 2, 3, 4, 5, 7, 0, time.FixedZone("", 5*3600+45*60))
	minus := time.Date(1999, 7, 4, 1, 2, 3, 0, time.FixedZone("", -7*3600))

	// 1. 单个 Store 文件
	{
		var buf bytes.Buffer
		w := zip.NewWriter(&buf)
		fw, _ := w.CreateHeader(&zip.FileHeader{Name: "hello.txt"})
		fw.Write([]byte("hello world\n"))
		w.Close()
		dump("store1", buf.Bytes())
		readBack("store1", buf.Bytes(), true)
	}

	// 2. 各种头部
	{
		var buf bytes.Buffer
		w := zip.NewWriter(&buf)
		var hs []*zip.FileHeader
		add := func(h *zip.FileHeader, data string) {
			fw, err := w.CreateHeader(h)
			if err != nil {
				fmt.Println("create err", err)
				return
			}
			hs = append(hs, h)
			if data != "" {
				_, err = fw.Write([]byte(data[:len(data)/2]))
				_, err = fw.Write([]byte(data[len(data)/2:]))
				if err != nil {
					fmt.Println("write err", err)
				}
			}
		}
		add(&zip.FileHeader{Name: "a.txt", Modified: utc}, "alpha")
		add(&zip.FileHeader{Name: "b/plus2.txt", Modified: plus2, Comment: "two hours"}, "bravo bravo")
		add(&zip.FileHeader{Name: "nepal.bin", Modified: nepal}, "\x00\x01\x02\xff")
		add(&zip.FileHeader{Name: "minus.txt", Modified: minus}, "m")
		add(&zip.FileHeader{Name: "dir/"}, "")
		add(&zip.FileHeader{Name: "dir2/", Modified: utc, Flags: 0x8, Method: zip.Deflate, UncompressedSize64: 7}, "")
		add(&zip.FileHeader{Name: "héllo/世界.txt"}, "unicode")
		add(&zip.FileHeader{Name: "back\\slash.txt"}, "bs")
		add(&zip.FileHeader{Name: "tilde~.txt"}, "t")
		add(&zip.FileHeader{Name: "plain.txt", Comment: "commént"}, "c")
		add(&zip.FileHeader{Name: "nonutf8-é.txt", NonUTF8: true, Flags: 0x800}, "n")
		add(&zip.FileHeader{Name: "forced.txt", Flags: 0x800}, "f")
		add(&zip.FileHeader{Name: "ctrl\t.txt"}, "")
		add(&zip.FileHeader{Name: "creator.txt", CreatorVersion: 0x0b33, ReaderVersion: 45, ExternalAttrs: 0x21}, "x")
		h := &zip.FileHeader{Name: "mode.sh"}
		h.SetMode(0755)
		add(h, "#!/bin/sh\n")
		h = &zip.FileHeader{Name: "ro.txt"}
		h.SetMode(0444)
		add(h, "readonly")
		h = &zip.FileHeader{Name: "sub/"}
		h.SetMode(fs.ModeDir | 0750)
		add(h, "")
		h = &zip.FileHeader{Name: "link"}
		h.SetMode(fs.ModeSymlink | 0777)
		add(h, "target")
		h = &zip.FileHeader{Name: "special"}
		h.SetMode(fs.ModeSetuid | fs.ModeSetgid | fs.ModeSticky | fs.ModeNamedPipe | 0640)
		add(h, "")
		h = &zip.FileHeader{Name: "settime.txt"}
		h.SetModTime(plus2)
		add(h, "st")
		add(&zip.FileHeader{Name: "extra.txt", Extra: []byte{0xfe, 0xca, 2, 0, 9, 8}, Modified: utc}, "e")
		add(&zip.FileHeader{Name: "empty.txt"}, "")
		add(&zip.FileHeader{Name: "bad.txt", Method: 99}, "z")
		fmt.Println("comment err", errStr(w.SetComment(strings.Repeat("x", 70000))))
		fmt.Println("comment", errStr(w.SetComment("archive comment ✓")))
		d3 := &zip.FileHeader{Name: "d3/"}
		hs = append(hs, d3)
		fw, err := w.CreateHeader(d3)
		_, err2 := fw.Write([]byte("no"))
		_, err3 := fw.Write(nil)
		fmt.Println("dir write", errStr(err), errStr(err2), errStr(err3))
		fmt.Println("close", errStr(w.Close()))
		for _, h := range hs {
			fmt.Println("hdr", desc(h, true))
		}
		dump("store2", buf.Bytes())
		readBack("store2", buf.Bytes(), true)
	}

	// 3. CreateRaw / SetOffset / 不支持的方法
	{
		var buf bytes.Buffer
		w := zip.NewWriter(&buf)
		w.SetOffset(100)
		data := []byte("raw data here")
		fw, _ := w.CreateRaw(&zip.FileHeader{Name: "raw1.txt", CRC32: crc32.ChecksumIEEE(data), CompressedSize64: uint64(len(data)), UncompressedSize64: uint64(len(data))})
		fw.Write(data)
		fw, _ = w.CreateRaw(&zip.FileHeader{Name: "raw2.txt", Flags: 8, CRC32: crc32.ChecksumIEEE(data), CompressedSize64: uint64(len(data)), UncompressedSize64: uint64(len(data))})
		fw.Write(data)
		fw, _ = w.CreateRaw(&zip.FileHeader{Name: "weird.bin", Method: 12, CRC32: 1234, CompressedSize64: 3, UncompressedSize64: 9})
		fw.Write([]byte{1, 2, 3})
		fw, _ = w.CreateRaw(&zip.FileHeader{Name: "badcrc.txt", CRC32: 77, CompressedSize64: 4, UncompressedSize64: 4})
		fw.Write([]byte("abcd"))
		fw, _ = w.CreateRaw(&zip.FileHeader{Name: "short.txt", CompressedSize64: 4, UncompressedSize64: 6})
		fw.Write([]byte("abcd"))
		fw, _ = w.CreateRaw(&zip.FileHeader{Name: "long.txt", CompressedSize64: 4, UncompressedSize64: 2})
		fw.Write([]byte("abcd"))
		fw, _ = w.CreateRaw(&zip.FileHeader{Name: "rawdir/", UncompressedSize64: 5})
		w.Close()
		dump("raw", buf.Bytes())
		// 前面补 100 字节，正好对上 SetOffset
		full := append(bytes.Repeat([]byte{0}, 100), buf.Bytes()...)
		readBack("raw+pad", full, true)
		// 不补：靠 baseOffset 修正
		readBack("raw", buf.Bytes(), true)
	}

	// 4. Deflate（Create）：比较解压结果
	{
		var buf bytes.Buffer
		w := zip.NewWriter(&buf)
		fw, _ := w.Create("text.txt")
		fw.Write(text)
		fw, _ = w.Create("empty")
		fw, _ = w.Create("folder/")
		fw, _ = w.Create("bytes.bin")
		bs := make([]byte, 300)
		for i := range bs {
			bs[i] = byte(i * 7 % 256)
		}
		fw.Write(bs)
		fw, _ = w.CreateHeader(&zip.FileHeader{Name: "time.txt", Method: zip.Deflate, Modified: nepal, Comment: "deflated"})
		fw.Write([]byte("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaab"))
		w.SetComment("deflate archive")
		w.Close()
		readBack("deflate", buf.Bytes(), false)
	}

	// 5. 读 Go 写的 Deflate 归档（Kite 侧内嵌字节）
	{
		var buf bytes.Buffer
		w := zip.NewWriter(&buf)
		fw, _ := w.Create("fox.txt")
		fw.Write(text)
		fw, _ = w.CreateHeader(&zip.FileHeader{Name: "dir/nested.txt", Method: zip.Deflate, Modified: utc})
		fw.Write([]byte("nested file contents, nested file contents, nested!"))
		fw, _ = w.CreateHeader(&zip.FileHeader{Name: "stored.txt", Modified: minus})
		fw.Write([]byte("stored"))
		fw, _ = w.Create("dir/")
		w.SetComment("go deflate")
		w.Close()
		b := buf.Bytes()
		dump("godeflate", b)
		readBack("godeflate", b, true)
		r, _ := zip.NewReader(bytes.NewReader(b), int64(len(b)))
		for _, f := range r.File {
			rr, _ := f.OpenRaw()
			raw, _ := io.ReadAll(rr)
			fmt.Println("raw", f.Name, f.ModTime().String(), f.FileInfo().Size(), hex.EncodeToString(raw))
		}
		// 改坏 CRC（中央目录里 fox.txt 的 CRC）
		c := append([]byte(nil), b...)
		i := bytes.Index(c, []byte("PK\x01\x02"))
		c[i+16] ^= 0xff
		readBack("badcrc", c, true)
	}

	// 6. 读取错误
	{
		var buf bytes.Buffer
		w := zip.NewWriter(&buf)
		fw, _ := w.CreateHeader(&zip.FileHeader{Name: "x.txt"})
		fw.Write([]byte("xyz"))
		w.SetComment(strings.Repeat("C", 1500))
		w.Close()
		b := buf.Bytes()
		readBack("longcomment", b, true)
		readBack("notzip", []byte("hello, this is not a zip file at all"), true)
		readBack("empty", []byte{}, true)
		readBack("trunc", b[:len(b)-1], true)
		// 数据字节改坏 -> checksum error
		c := append([]byte(nil), b...)
		c[30+5] = 'Q'
		readBack("baddata", c, true)
		// 数据描述符里的 CRC 改坏
		c = append([]byte(nil), b...)
		c[30+5+3+4] ^= 1
		readBack("baddesc", c, true)
		// 本地文件头签名改坏
		c = append([]byte(nil), b...)
		c[0] = 'X'
		readBack("badlocal", c, true)
		// 前面加垃圾
		c = append([]byte("JUNKJUNK"), b...)
		readBack("junk", c, true)
		// 中央目录签名改坏
		c = append([]byte(nil), b...)
		i := bytes.Index(c, []byte("PK\x01\x02"))
		c[i] = 'X'
		readBack("baddir", c, true)
	}
}
