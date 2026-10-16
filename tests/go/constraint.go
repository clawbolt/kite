package main

import (
	"fmt"
	"go/build/constraint"
	"strconv"
)

func main() {
	lines := []string{
		"//go:build linux && amd64", "//go:build linux || !cgo", "//go:build !(linux && amd64) || darwin", "//go:build (a || b) && (c || d)",
		"//go:build go1.20", "//go:build go1.21 && !go1.22", "//go:build !go1.5 || go1.18", "//go:build go1", "//go:build gox1.3",
		"//go:build", "//go:build ", "//go:buildlinux", "//go:build linux\n", "//go:build a\nb", "  //go:build x",
		"//go:build !!a", "//go:build a &", "//go:build a | b", "//go:build (a", "//go:build a)", "//go:build ((a) || (b && !c))",
		"//go:build a && ", "//go:build $x", "//go:build héllo && 世界", "//go:build a b", "//go:build !", "//go:build ()",
		"//go:build a.b_c9", "//go:build (a || b", "//go:build (a ||", "//go:build ((a)", "//go:build a && (b || c) && !d",
		"// +build linux darwin", "// +build linux,amd64 !cgo", "// +build", "// +build ", "//+build x", "// +buildx",
		"// +build !!a b", "// +build ! a", "// +build a,!b,c d,e", "// +build $bad ok", "// +build go1.18,!go1.20",
		"// hello", "package main", "",
	}
	tags := map[string]bool{"linux": true, "amd64": true, "cgo": true, "go1.20": true, "a": true, "d": true}
	for _, l := range lines {
		fmt.Printf("%q gobuild=%v plus=%v\n", l, constraint.IsGoBuild(l), constraint.IsPlusBuild(l))
		x, err := constraint.Parse(l)
		if err != nil {
			off := -1
			if se, ok := err.(*constraint.SyntaxError); ok {
				off = se.Offset
			}
			fmt.Println("  err", off, err)
			continue
		}
		fmt.Println("  expr", x.String(), x.Eval(func(t string) bool { return tags[t] }), strconv.Quote(constraint.GoVersion(x)))
		pl, err := constraint.PlusBuildLines(x)
		fmt.Printf("  plus %q %v\n", pl, err)
	}
}
