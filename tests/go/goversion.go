package main

import (
	"fmt"
	"go/version"
)

func main() {
	vs := []string{"", "go", "go1", "go1.0", "go1.0.0", "go1.2", "go1.20", "go1.21", "go1.21.0", "go1.21rc1", "go1.21rc2", "go1.21beta1", "go1.21alpha", "go1.22.3", "go1.22.3-custom", "go1.21-bigcorp", "go2", "go2.0", "go01.2", "go1.02", "go1.2.", "go1.21rc", "go1.21rc01", "go1.21RC1", "1.21", "go1.21.0.1", "go10.0", "go1.9.9", "go1.100", "gox", "go1.21.x", "go1.999999999999999999999999"}
	for _, x := range vs {
		fmt.Printf("%q lang=%q valid=%v\n", x, version.Lang(x), version.IsValid(x))
	}
	for _, x := range vs {
		for _, y := range vs {
			fmt.Print(version.Compare(x, y), " ")
		}
		fmt.Println()
	}
}
