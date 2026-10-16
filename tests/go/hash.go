package main
import ("crypto/hmac"; "crypto/md5"; "crypto/sha1"; "crypto/sha256"; "crypto/sha512"; "crypto/subtle"; "fmt"; "hash/adler32"; "hash/crc32"; "hash/crc64"; "strings"; "hash")
func L(b []byte) string { s := []string{}; for _, x := range b { s = append(s, fmt.Sprint(x)) }; return "[" + strings.Join(s, ", ") + "]" }
func hm(h func() hash.Hash, k, m []byte) []byte { mac := hmac.New(h, k); mac.Write(m); return mac.Sum(nil) }
func b2s(b bool) string { if b { return "true" }; return "false" }
func main() {
  inputs := []string{"", "a", "abc", "message digest", "The quick brown fox jumps over the lazy dog", "你好，世界"}
  big := ""
  for i := 0; i < 300; i++ { big += fmt.Sprintf("%d,", i) }
  inputs = append(inputs, big)
  iso := crc64.MakeTable(crc64.ISO); ecma := crc64.MakeTable(crc64.ECMA)
  cast := crc32.MakeTable(crc32.Castagnoli); koop := crc32.MakeTable(crc32.Koopman)
  for _, s := range inputs {
    b := []byte(s)
    fmt.Printf("%x %x %x\n", md5.Sum(b), sha1.Sum(b), sha256.Sum224(b))
    fmt.Printf("%x %x\n", sha512.Sum512(b), sha512.Sum384(b))
    fmt.Printf("%x %x\n", sha512.Sum512_224(b), sha512.Sum512_256(b))
    fmt.Println(int64(crc64.Checksum(b, iso)), int64(crc64.Checksum(b, ecma)), crc32.ChecksumIEEE(b), crc32.Checksum(b, cast), crc32.Checksum(b, koop), adler32.Checksum(b))
    m1 := hm(md5.New, []byte("key"), b); m2 := hm(sha512.New, []byte(big), b); m3 := hm(sha1.New, b, []byte("msg"))
    fmt.Println(L(m1), L(m2), L(m3), b2s(hmac.Equal(m1, m1)), b2s(hmac.Equal(m1, m3)))
  }
  fmt.Println(int64(crc64.Update(crc64.Checksum([]byte("hello "), iso), iso, []byte("world"))), int64(crc64.Checksum([]byte("hello world"), iso)))
  fmt.Println(subtle.ConstantTimeCompare([]byte{1, 2, 3}, []byte{1, 2, 3}), subtle.ConstantTimeCompare([]byte{1, 2, 3}, []byte{1, 2, 4}), subtle.ConstantTimeCompare([]byte{1}, []byte{1, 2}))
  fmt.Println(subtle.ConstantTimeSelect(1, 5, 9), subtle.ConstantTimeSelect(0, 5, 9), subtle.ConstantTimeByteEq(7, 7), subtle.ConstantTimeByteEq(7, 8), subtle.ConstantTimeEq(-3, -3), subtle.ConstantTimeEq(1, 2))
  x := []byte{1, 2}; subtle.ConstantTimeCopy(1, x, []byte{3, 4}); y := []byte{1, 2}; subtle.ConstantTimeCopy(0, y, []byte{3, 4})
  d := make([]byte, 2); subtle.XORBytes(d, []byte{255, 15, 1}, []byte{15, 15})
  fmt.Println(L(x), L(y), L(d))
  for _, p := range [][2]int{{0, 0}, {1, 2}, {2, 1}, {5, 5}, {-1, 3}, {3, -1}, {2147483647, -2147483648}} { fmt.Println(subtle.ConstantTimeLessOrEq(p[0], p[1])) }
}
