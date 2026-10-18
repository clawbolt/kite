package main

import (
	"fmt"
	"image/color"
	"image/color/palette"
)

func show(name string, p []color.Color) {
	fmt.Println(name, len(p))
	for i, c := range p {
		rc := c.(color.RGBA)
		r, g, b, a := c.RGBA()
		fmt.Printf("%d {%d %d %d %d} %d %d %d %d\n", i, rc.R, rc.G, rc.B, rc.A, r, g, b, a)
	}
}

func main() {
	show("plan9", palette.Plan9)
	show("websafe", palette.WebSafe)
	probes := []color.Color{
		color.RGBA{0, 0, 0, 255}, color.RGBA{255, 255, 255, 255}, color.RGBA{10, 200, 30, 255},
		color.RGBA{128, 128, 128, 255}, color.RGBA{10, 200, 30, 128}, color.NRGBA{200, 10, 50, 77},
		color.Gray{99}, color.Gray16{0x1234}, color.YCbCr{80, 200, 30}, color.CMYK{10, 20, 30, 40},
		color.Alpha{0}, color.RGBA{1, 2, 3, 4}, color.RGBA{250, 100, 7, 255},
	}
	for _, pr := range probes {
		p9 := color.Palette(palette.Plan9)
		ws := color.Palette(palette.WebSafe)
		fmt.Println(pr, p9.Index(pr), p9.Convert(pr), ws.Index(pr), ws.Convert(pr))
	}
	// 每个调色板项映射回自己
	ok := true
	for i, c := range palette.WebSafe {
		if color.Palette(palette.WebSafe).Index(c) != i {
			ok = false
		}
	}
	fmt.Println("websafe self", ok)
	n := 0
	for i, c := range palette.Plan9 {
		if color.Palette(palette.Plan9).Index(c) == i {
			n++
		}
	}
	fmt.Println("plan9 self", n)
}
