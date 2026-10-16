package main
import ("fmt"; "math/bits")
func main() {
  xs := []uint64{0, 1, 2, 3, 255, 256, 0x12345678, 0x7fffffffffffffff, ^uint64(0), 1 << 63, neg(0x123456789abcdef0), 0xdeadbeef, 0x0102030405060708}
  for _, x := range xs {
    fmt.Println(x, bits.Len64(x), bits.Len32(uint32(x)), bits.Len16(uint16(x)), bits.Len8(uint8(x)), bits.LeadingZeros64(x), bits.LeadingZeros32(uint32(x)), bits.TrailingZeros64(x), bits.TrailingZeros32(uint32(x)), bits.TrailingZeros16(uint16(x)), bits.TrailingZeros8(uint8(x)), bits.OnesCount64(x), bits.OnesCount32(uint32(x)), bits.OnesCount8(uint8(x)))
    fmt.Println(bits.RotateLeft64(x, 13), bits.RotateLeft64(x, -7), bits.RotateLeft32(uint32(x), 5), bits.RotateLeft16(uint16(x), -3), bits.RotateLeft8(uint8(x), 9), bits.Reverse64(x), bits.Reverse32(uint32(x)), bits.Reverse16(uint16(x)), bits.Reverse8(uint8(x)), bits.ReverseBytes64(x), bits.ReverseBytes32(uint32(x)), bits.ReverseBytes16(uint16(x)))
    for _, y := range xs {
      h, l := bits.Mul64(x, y); s, c := bits.Add64(x, y, 1); d, b := bits.Sub64(x, y, 1)
      h3, l3 := bits.Mul32(uint32(x), uint32(y)); s3, c3 := bits.Add32(uint32(x), uint32(y), 1); d3, b3 := bits.Sub32(uint32(x), uint32(y), 0)
      line := fmt.Sprint(h, " ", l, " ", s, " ", c, " ", d, " ", b, " ", h3, " ", l3, " ", s3, " ", c3, " ", d3, " ", b3)
      if y != 0 {
        line += fmt.Sprint(" ", x/y, " ", x%y, " ", bits.Rem64(x, y, y), " ", cmp(x, y))
        if h < y { q, r := bits.Div64(h, x, y); line += fmt.Sprint(" ", q, " ", r) }
        if uint32(y) != 0 { line += fmt.Sprint(" ", bits.Rem32(uint32(x), uint32(x), uint32(y))) }
      }
      fmt.Println(line)
    }
  }
  fmt.Println(bits.UintSize)
}
func cmp(a, b uint64) int { if a == b { return 0 }; if a < b { return -1 }; return 1 }
func neg(x uint64) uint64 { return -x }
