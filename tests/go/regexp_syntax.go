package main

import (
	"bufio"
	"encoding/hex"
	"fmt"
	"hash/fnv"
	"os"
	"regexp/syntax"
	"strconv"
	"strings"
)

func show(s string) string {
	if len(s) <= 300 {
		return s
	}
	h := fnv.New64a()
	h.Write([]byte(s))
	return fmt.Sprintf("<%d bytes %016x>", len(s), h.Sum64())
}

func main() {
	f, _ := os.Open("testdata/regexp_syntax.txt")
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	n := 0
	for sc.Scan() {
		parts := strings.SplitN(sc.Text(), " ", 2)
		fl, _ := strconv.Atoi(parts[0])
		b, _ := hex.DecodeString(parts[1])
		n++
		fmt.Printf("#%d %d %s\n", n, fl, show(strconv.Quote(string(b))))
		re, err := syntax.Parse(string(b), syntax.Flags(fl))
		if err != nil {
			fmt.Println("ERR", show(strconv.Quote(err.Error())))
			continue
		}
		fmt.Println("S", show(re.String()))
		s := re.Simplify()
		fmt.Println("Z", show(s.String()), re.MaxCap(), strconv.Quote(strings.Join(re.CapNames(), ",")))
		p, _ := syntax.Compile(s)
		pre, comp := p.Prefix()
		fmt.Println("P", show(p.String()), strconv.Quote(pre), comp, p.StartCond(), p.NumCap)
	}
}
