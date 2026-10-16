package main

import (
	"bytes"
	"fmt"
	"image"
	"image/color"
	"image/color/palette"
	"image/draw"
	"image/gif"
)

func sum(b []byte) int {
	s := 0
	for i, v := range b {
		s = (s*31 + int(v) + i) % 1000000007
	}
	return s
}

func main() {
	// color
	cs := []color.Color{color.RGBA{10, 200, 30, 128}, color.NRGBA{200, 10, 50, 77}, color.Gray{99}, color.Gray16{0x1234},
		color.YCbCr{80, 200, 30}, color.NYCbCrA{color.YCbCr{20, 100, 220}, 150}, color.CMYK{10, 20, 30, 40}, color.Alpha{77}, color.NRGBA64{0x1111, 0x8888, 0xffff, 0x7777}}
	models := []color.Model{color.RGBAModel, color.RGBA64Model, color.NRGBAModel, color.NRGBA64Model, color.AlphaModel, color.Alpha16Model, color.GrayModel, color.Gray16Model, color.YCbCrModel, color.NYCbCrAModel, color.CMYKModel}
	for _, c := range cs {
		r, g, b, a := c.RGBA()
		fmt.Print(r, g, b, a, ":")
		for _, m := range models {
			fmt.Print(" ", m.Convert(c))
		}
		fmt.Println()
	}
	for i := 0; i < 256; i += 37 {
		y, cb, cr := color.RGBToYCbCr(uint8(i), uint8(255-i), uint8(i*7))
		r, g, b := color.YCbCrToRGB(uint8(i), uint8(i*3), uint8(255-i))
		c, m, yy, k := color.RGBToCMYK(uint8(i), uint8(i/2), uint8(255-i))
		r2, g2, b2 := color.CMYKToRGB(uint8(i), 30, uint8(i/3), 60)
		fmt.Println(y, cb, cr, r, g, b, c, m, yy, k, r2, g2, b2)
	}
	fmt.Println(color.Palette(palette.Plan9).Index(color.RGBA{123, 45, 67, 255}), color.Palette(palette.WebSafe).Convert(color.NRGBA{123, 45, 67, 200}))
	// geom
	r1 := image.Rect(5, 8, -2, 1)
	r2 := image.Rect(0, 0, 4, 4)
	fmt.Println(r1, r1.Intersect(r2), r1.Union(r2), r1.Inset(2), r1.Inset(5), r1.Overlaps(r2), r2.In(r1), r1.Size(), image.Pt(-7, 13).Mod(r2), image.Pt(3, 4).Div(2), r1.Add(image.Pt(1, 1)).Eq(r1), image.Rect(3, 3, 3, 9).Eq(image.Rectangle{}))
	// images
	ims := []draw.Image{image.NewRGBA(image.Rect(1, 2, 6, 6)), image.NewRGBA64(image.Rect(1, 2, 6, 6)), image.NewNRGBA(image.Rect(1, 2, 6, 6)), image.NewNRGBA64(image.Rect(1, 2, 6, 6)),
		image.NewAlpha(image.Rect(1, 2, 6, 6)), image.NewAlpha16(image.Rect(1, 2, 6, 6)), image.NewGray(image.Rect(1, 2, 6, 6)), image.NewGray16(image.Rect(1, 2, 6, 6)), image.NewCMYK(image.Rect(1, 2, 6, 6)), image.NewPaletted(image.Rect(1, 2, 6, 6), palette.WebSafe)}
	for _, im := range ims {
		im.Set(2, 3, color.NRGBA{200, 100, 50, 180})
		im.Set(5, 5, color.YCbCr{30, 140, 90})
		im.Set(0, 0, color.White)
		fmt.Println(im.At(2, 3), im.At(5, 5), im.At(9, 9))
		type op interface{ Opaque() bool }
		fmt.Println(im.(op).Opaque())
	}
	yc := image.NewYCbCr(image.Rect(1, 1, 8, 6), image.YCbCrSubsampleRatio420)
	for i := range yc.Y {
		yc.Y[i] = uint8(i * 7)
	}
	for i := range yc.Cb {
		yc.Cb[i] = uint8(i * 13)
		yc.Cr[i] = uint8(255 - i*11)
	}
	fmt.Println(len(yc.Y), len(yc.Cb), yc.CStride, yc.At(4, 3), yc.COffset(7, 5), yc.SubsampleRatio)
	// draw
	dst := image.NewRGBA(image.Rect(0, 0, 8, 8))
	draw.Draw(dst, dst.Bounds(), image.NewUniform(color.RGBA{0, 0, 255, 255}), image.Point{}, draw.Src)
	draw.Draw(dst, image.Rect(2, 2, 7, 7), yc, image.Pt(1, 1), draw.Over)
	src := image.NewNRGBA(image.Rect(0, 0, 4, 4))
	for i := range src.Pix {
		src.Pix[i] = uint8(i * 17)
	}
	mask := image.NewAlpha(image.Rect(0, 0, 8, 8))
	for i := range mask.Pix {
		mask.Pix[i] = uint8(i * 5)
	}
	draw.DrawMask(dst, image.Rect(-1, 3, 5, 9), src, image.Pt(0, 0), mask, image.Pt(1, 1), draw.Over)
	draw.DrawMask(dst, image.Rect(4, 0, 8, 3), src, image.Pt(1, 0), mask, image.Pt(0, 0), draw.Src)
	fmt.Println(sum(dst.Pix), dst.Pix[:40])
	g16 := image.NewGray16(image.Rect(0, 0, 5, 5))
	draw.Draw(g16, g16.Bounds(), dst, image.Pt(2, 2), draw.Over)
	fmt.Println(g16.Pix)
	pal := image.NewPaletted(image.Rect(0, 0, 8, 8), palette.Plan9)
	draw.Draw(pal, pal.Bounds(), dst, image.Point{}, draw.Src)
	fmt.Println(sum(pal.Pix))
	draw.FloydSteinberg.Draw(pal, pal.Bounds(), dst, image.Point{})
	fmt.Println(pal.Pix)
	// gif
	var buf bytes.Buffer
	gif.Encode(&buf, dst, nil)
	enc := buf.Bytes()
	fmt.Println(len(enc), sum(enc))
	m, err := gif.Decode(bytes.NewReader(enc))
	fmt.Println(err, m.Bounds(), sum(m.(*image.Paletted).Pix))
	buf.Reset()
	gif.Encode(&buf, dst, &gif.Options{NumColors: 16})
	fmt.Println(buf.Len(), sum(buf.Bytes()))
	// animation
	p1 := image.NewPaletted(image.Rect(0, 0, 10, 6), color.Palette{color.RGBA{0, 0, 0, 255}, color.RGBA{255, 0, 0, 255}, color.RGBA{0, 0, 0, 0}})
	p2 := image.NewPaletted(image.Rect(2, 1, 9, 5), color.Palette{color.RGBA{0, 0, 0, 255}, color.RGBA{0, 255, 0, 255}, color.RGBA{9, 9, 9, 255}, color.RGBA{1, 1, 1, 255}, color.RGBA{2, 2, 2, 255}})
	for i := range p1.Pix {
		p1.Pix[i] = uint8(i % 3)
	}
	for i := range p2.Pix {
		p2.Pix[i] = uint8(i * i % 5)
	}
	buf.Reset()
	err = gif.EncodeAll(&buf, &gif.GIF{Image: []*image.Paletted{p1, p2}, Delay: []int{10, 20}, LoopCount: 3, Disposal: []byte{1, 2}, Config: image.Config{ColorModel: p1.Palette, Width: 10, Height: 6}})
	anim := buf.Bytes()
	fmt.Println(err, anim)
	g, err := gif.DecodeAll(bytes.NewReader(anim))
	fmt.Println(err, len(g.Image), g.Delay, g.LoopCount, g.Disposal, g.Config.Width, g.Config.Height, g.BackgroundIndex, len(g.Config.ColorModel.(color.Palette)))
	for _, im := range g.Image {
		fmt.Println(im.Rect, im.Pix, im.Palette)
	}
	cfg, err := gif.DecodeConfig(bytes.NewReader(anim))
	fmt.Println(cfg.Width, cfg.Height, err)
	// errors
	for _, n := range []int{0, 5, 13, 20, 30, 40, 45, 50, 60, 70, 75, 80, len(anim) - 3, len(anim) - 1} {
		_, err := gif.DecodeAll(bytes.NewReader(anim[:n]))
		fmt.Println(n, err)
	}
	bad := append([]byte(nil), anim...)
	bad[0] = 'X'
	_, err = gif.Decode(bytes.NewReader(bad))
	fmt.Println(err)
	for _, i := range []int{36, 60, 61, 62, 63, 70, 71} {
		bad := append([]byte(nil), anim...)
		bad[i] ^= 0x5a
		_, err = gif.DecodeAll(bytes.NewReader(bad))
		fmt.Println(i, err)
	}
	// interlaced hand-made: 1x9 image
	il := []byte("GIF89a\x01\x00\x09\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff\x2c\x00\x00\x00\x00\x01\x00\x09\x00\x40\x02")
	cmp, _ := lzwc([]byte{1, 0, 1, 1, 0, 0, 1, 0, 1})
	il = append(il, byte(len(cmp)))
	il = append(il, cmp...)
	il = append(il, 0, 0x3b)
	im, err := gif.Decode(bytes.NewReader(il))
	fmt.Println(err, im.(*image.Paletted).Pix)
}
