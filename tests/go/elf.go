package main

import (
	"bytes"
	"debug/elf"
	"fmt"
	"hash/crc32"
	"os"
	"sort"
	"strings"
)

func bo(b fmt.Stringer) string { return b.String() }

func dump(name string, f *elf.File) {
	fmt.Printf("hdr %s %s %s %s %d %s %s %s %d\n", f.Class, f.Data, f.Version, f.OSABI, f.ABIVersion,
		bo(f.ByteOrder), f.Type, f.Machine, f.Entry)
	fmt.Printf("gohdr %#v %#v %#v %#v\n", f.Class, f.Type, f.Machine, f.OSABI)
	for i, s := range f.Sections {
		fmt.Printf("sec %d %q %s %s %d %d %d %d %d %d %d %d\n", i, s.Name, s.Type, s.Flags, s.Addr, s.Offset,
			s.Size, s.Link, s.Info, s.Addralign, s.Entsize, s.FileSize)
	}
	for i, s := range f.Sections {
		d, err := s.Data()
		if err != nil {
			fmt.Printf("data %d err %s\n", i, err)
		} else {
			fmt.Printf("data %d %d %08x\n", i, len(d), crc32.ChecksumIEEE(d))
		}
	}
	for i, p := range f.Progs {
		fmt.Printf("prog %d %s %s %d %d %d %d %d %d\n", i, p.Type, p.Flags, p.Off, p.Vaddr, p.Paddr, p.Filesz,
			p.Memsz, p.Align)
	}
	syms, err := f.Symbols()
	if err != nil {
		fmt.Println("symbols err", err)
	}
	for i, s := range syms {
		fmt.Printf("sym %d %q %d %d %s %s %s %s %d %d\n", i, s.Name, s.Info, s.Other, elf.ST_BIND(s.Info),
			elf.ST_TYPE(s.Info), elf.ST_VISIBILITY(s.Other), s.Section, s.Value, s.Size)
	}
	dsyms, err := f.DynamicSymbols()
	if err != nil {
		fmt.Println("dynsyms err", err)
	}
	for i, s := range dsyms {
		fmt.Printf("dsym %d %q %d %d %s %d %d %t %d %t %d %q %q\n", i, s.Name, s.Info, s.Other, s.Section,
			s.Value, s.Size, s.HasVersion, s.VersionIndex, s.VersionIndex.IsHidden(), s.VersionIndex.Index(),
			s.Version, s.Library)
	}
	imps, err := f.ImportedSymbols()
	if err != nil {
		fmt.Println("imported err", err)
	}
	for _, s := range imps {
		fmt.Printf("imp %q %q %q\n", s.Name, s.Version, s.Library)
	}
	libs, err := f.ImportedLibraries()
	fmt.Printf("libs %s %v\n", qs(libs), err)
	for _, t := range []elf.DynTag{elf.DT_SONAME, elf.DT_RPATH, elf.DT_RUNPATH, elf.DT_FLAGS} {
		ss, err := f.DynString(t)
		fmt.Printf("dynstr %s %s %v\n", t, qs(ss), err)
	}
	for _, t := range []elf.DynTag{elf.DT_FLAGS, elf.DT_FLAGS_1, elf.DT_STRSZ, elf.DT_SYMENT} {
		vs, err := f.DynValue(t)
		fmt.Printf("dynval %s %v %v\n", t, vs, err)
	}
	vers, err := f.DynamicVersions()
	if err != nil {
		fmt.Println("vers err", err)
	}
	for _, v := range vers {
		fmt.Printf("ver %q %d %d %s\n", v.Name, v.Index, v.Flags, qs(v.Deps))
	}
	needs, err := f.DynamicVersionNeeds()
	if err != nil {
		fmt.Println("needs err", err)
	}
	for _, n := range needs {
		fmt.Printf("need %q\n", n.Name)
		for _, d := range n.Needs {
			fmt.Printf("  dep %d %d %q\n", d.Flags, d.Index, d.Dep)
		}
	}
	if s := f.Section(".text"); s != nil {
		fmt.Println("text", s.Size)
	} else {
		fmt.Println("text none")
	}
	if s := f.SectionByType(elf.SHT_STRTAB); s != nil {
		fmt.Printf("strtab %q\n", s.Name)
	}
}

func qs(ss []string) string {
	var p []string
	for _, s := range ss {
		p = append(p, fmt.Sprintf("%q", s))
	}
	return "[" + strings.Join(p, " ") + "]"
}

func main() {
	ents, _ := os.ReadDir("testdata/elf")
	var names []string
	for _, e := range ents {
		names = append(names, e.Name())
	}
	sort.Strings(names)
	var first []byte
	for _, n := range names {
		fmt.Println("==", n)
		f, err := elf.Open("testdata/elf/" + n)
		if err != nil {
			fmt.Println("err", err)
			continue
		}
		dump(n, f)
	}
	// 常量名字
	fmt.Println(elf.SectionFlag(elf.SHF_WRITE|elf.SHF_ALLOC|0x100000), elf.SectionFlag(0), elf.ProgFlag(7),
		elf.ProgFlag(0x10), elf.Machine(9999), elf.Machine(0x1234), elf.R_X86_64(1), elf.R_AARCH64(1029),
		elf.DynTag(0x6ffffffe), elf.SymBind(13), elf.NType(1), elf.DynFlag(0x1f), elf.DynFlag1(0x80000001))
	fmt.Printf("%#v %#v %#v %#v %#v\n", elf.SectionFlag(3), elf.SectionFlag(0x40000003), elf.R_X86_64(1),
		elf.Machine(9999), elf.CompressionType(2))
	fmt.Println(elf.R_SYM64(0x1234500000007), elf.R_TYPE64(0x1234500000007), elf.R_INFO(5, 7),
		elf.R_SYM32(0x1207), elf.R_TYPE32(0x1207), elf.R_INFO32(0x12, 7), elf.ST_INFO(1, 2))
	// 损坏的输入
	first, _ = os.ReadFile("testdata/elf/gcc-amd64-linux-exec")
	f32, _ := os.ReadFile("testdata/elf/gcc-386-freebsd-exec")
	bad := func(tag string, b []byte) {
		_, err := elf.NewFile(bytes.NewReader(b))
		fmt.Println("bad", tag, err)
	}
	mod := func(src []byte, i int, v byte) []byte {
		c := append([]byte(nil), src...)
		c[i] = v
		return c
	}
	for _, n := range []int{0, 3, 15, 16, 40, 63, 64, 100, 2000, 6000} {
		bad(fmt.Sprint("trunc", n), first[:n])
		if n <= len(f32) {
			bad(fmt.Sprint("trunc32-", n), f32[:n])
		}
	}
	bad("magic", mod(first, 1, 'X'))
	bad("class", mod(first, 4, 3))
	bad("data", mod(first, 5, 9))
	bad("version", mod(first, 6, 2))
	bad("version2", mod(first, 20, 2))
	bad("shstrndx", mod(mod(first, 62, 0xff), 63, 0x00))
	bad("phentsize", mod(first, 54, 3))
	bad("shentsize", mod(first, 58, 3))
	bad("shoff0", mod(mod(mod(mod(first, 40, 0), 41, 0), 42, 0), 43, 0))
	bad("shoffneg", mod(first, 47, 0x80))
	bad("phoffneg", mod(first, 39, 0x80))
	sh := int(uint64(first[40]) | uint64(first[41])<<8 | uint64(first[42])<<16)
	bad("secname", mod(mod(first, sh+64*3, 0xff), sh+64*3+1, 0xff))
	bad("sectype0", mod(mod(first, 60, 0), 61, 0))
	bad("strtabtype", mod(first, sh+64*int(first[62])+4, 1))
	bad("secoffneg", mod(first, sh+64*2+31, 0x80))
}
