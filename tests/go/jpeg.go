package main

import (
	"bytes"
	"fmt"
	"image"
	"image/color"
	"image/gif"
	"image/jpeg"
	"os"
)

func sum(b []byte) int {
	s := 0
	for i, v := range b {
		s = (s*31 + int(v) + i) % 1000000007
	}
	return s
}

func desc(m image.Image) string {
	switch m := m.(type) {
	case *image.Gray:
		return fmt.Sprint("Gray ", m.Rect, " ", m.Stride, " ", len(m.Pix), " ", sum(m.Pix))
	case *image.YCbCr:
		return fmt.Sprint("YCbCr ", m.Rect, " ", m.SubsampleRatio, " ", m.YStride, " ", m.CStride, " ", len(m.Y), " ", len(m.Cb), " ", sum(m.Y), " ", sum(m.Cb), " ", sum(m.Cr))
	case *image.RGBA:
		return fmt.Sprint("RGBA ", m.Rect, " ", m.Stride, " ", len(m.Pix), " ", sum(m.Pix))
	case *image.CMYK:
		return fmt.Sprint("CMYK ", m.Rect, " ", m.Stride, " ", len(m.Pix), " ", sum(m.Pix))
	case *image.Paletted:
		return fmt.Sprint("Paletted ", m.Rect, " ", m.Stride, " ", len(m.Pix), " ", sum(m.Pix), " ", len(m.Palette))
	}
	return "?"
}

func model(m color.Model) string {
	switch m {
	case color.GrayModel:
		return "Gray"
	case color.YCbCrModel:
		return "YCbCr"
	case color.RGBAModel:
		return "RGBA"
	case color.CMYKModel:
		return "CMYK"
	}
	return "?"
}

func main() {
	files := []string{"video-001.221212", "video-001.cmyk", "video-001", "video-001.progressive", "video-001.progressive.truncated",
		"video-001.q50.121121", "video-001.q50.211211", "video-001.q50.221122", "video-001.q50.222112", "video-001.q50.410", "video-001.q50.410.progressive",
		"video-001.q50.411", "video-001.q50.411.progressive", "video-001.q50.420", "video-001.q50.420.progressive", "video-001.q50.422", "video-001.q50.422.progressive",
		"video-001.q50.440", "video-001.q50.440.progressive", "video-001.q50.444", "video-001.q50.444.progressive", "video-001.restart2", "video-001.rgb",
		"video-001.separate.dc.progression", "video-001.separate.dc.progression.progressive", "video-005.gray", "video-005.gray.q50.2x2",
		"video-005.gray.q50.2x2.progressive", "video-005.gray.q50", "video-005.gray.q50.progressive"}
	var keep image.Image
	var keepGray image.Image
	for _, f := range files {
		bs, _ := os.ReadFile("testdata/" + f + ".jpeg")
		m, err := jpeg.Decode(bytes.NewReader(bs))
		cfg, cerr := jpeg.DecodeConfig(bytes.NewReader(bs))
		if err != nil {
			fmt.Println(f, err, model(cfg.ColorModel), cfg.Width, cfg.Height, cerr)
			continue
		}
		fmt.Println(f, desc(m), model(cfg.ColorModel), cfg.Width, cfg.Height, cerr)
		if f == "video-001.q50.420" {
			keep = m
		}
		if f == "video-005.gray.q50" {
			keepGray = m
		}
	}
	// corruption
	bs, _ := os.ReadFile("testdata/video-001.q50.420.jpeg")
	for _, n := range []int{0, 1, 2, 3, 10, 100, 300, 600, 700, 1000, 2000, 3000, 3400} {
		_, err := jpeg.Decode(bytes.NewReader(bs[:n]))
		fmt.Println("trunc", n, err)
	}
	for _, i := range []int{2, 3, 5, 20, 25, 90, 160, 165, 170, 200, 400, 600, 610, 620, 630, 1500, 3000} {
		b := append([]byte(nil), bs...)
		b[i] ^= 0xa5
		m, err := jpeg.Decode(bytes.NewReader(b))
		if err != nil {
			fmt.Println("flip", i, err)
		} else {
			fmt.Println("flip", i, desc(m))
		}
	}
	// encode
	for _, q := range []int{1, 30, 75, 100} {
		var buf bytes.Buffer
		jpeg.Encode(&buf, keep, &jpeg.Options{Quality: q})
		fmt.Println("enc", q, buf.Len(), sum(buf.Bytes()))
		m, err := jpeg.Decode(bytes.NewReader(buf.Bytes()))
		fmt.Println(err, desc(m))
	}
	var buf bytes.Buffer
	jpeg.Encode(&buf, keepGray, nil)
	fmt.Println("gray", buf.Len(), sum(buf.Bytes()))
	rgba := image.NewRGBA(image.Rect(3, 5, 40, 31))
	for i := range rgba.Pix {
		rgba.Pix[i] = uint8(i*i/7 + i)
	}
	buf.Reset()
	jpeg.Encode(&buf, rgba, nil)
	fmt.Println("rgba", buf.Len(), sum(buf.Bytes()), buf.Bytes()[600:640])
	pal := image.NewPaletted(image.Rect(0, 0, 20, 9), color.Palette{color.RGBA{255, 0, 0, 255}, color.RGBA{0, 0, 255, 255}, color.RGBA{30, 200, 40, 255}})
	for i := range pal.Pix {
		pal.Pix[i] = uint8(i % 3)
	}
	buf.Reset()
	jpeg.Encode(&buf, pal, &jpeg.Options{Quality: 90})
	fmt.Println("pal", buf.Len(), sum(buf.Bytes()))
	// gif files
	for _, f := range []string{"triangle-001", "video-001.5bpp", "video-001", "video-001.interlaced", "video-005.gray"} {
		bs, _ := os.ReadFile("testdata/" + f + ".gif")
		g, err := gif.DecodeAll(bytes.NewReader(bs))
		if err != nil {
			fmt.Println(f, err)
			continue
		}
		fmt.Println(f, len(g.Image), g.LoopCount, g.Delay, g.Disposal, g.Config.Width, g.Config.Height, g.BackgroundIndex, desc(g.Image[0]))
	}
}
