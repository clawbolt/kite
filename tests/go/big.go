package main
import ("fmt"; "math/big"; "strings")
func s(x *big.Int) string { return x.String() }
func os(x *big.Int) string { if x == nil { return "nil" }; return x.String() }
func B(b bool) string { if b { return "true" }; return "false" }
func L(b []byte) string { s := []string{}; for _, x := range b { s = append(s, fmt.Sprint(x)) }; return "[" + strings.Join(s, ", ") + "]" }
func n(v int64) *big.Int { return big.NewInt(v) }
func main() {
  strs := []string{"0", "1", "-1", "7", "-12", "1000000007", "-4294967296", "1073741824", "123456789012345678901234567890", "-98765432109876543210987654321098765432", "340282366920938463463374607431768211457", "-18446744073709551616", "9223372036854775807", "-9223372036854775808", "99999999999999999999999999999999999999999999999999"}
  xs := []*big.Int{}
  for _, t := range strs { x, _ := new(big.Int).SetString(t, 10); xs = append(xs, x) }
  for _, x := range xs {
    fmt.Println(s(x), x.Text(2), x.Text(16), x.Text(36), x.Text(62), x.BitLen(), x.TrailingZeroBits(), x.Sign(), B(x.IsInt64()), x.Int64(), B(x.IsUint64()), s(new(big.Int).Not(x)), s(new(big.Int).Lsh(x, 37)), s(new(big.Int).Rsh(x, 5)), s(new(big.Int).Rsh(x, 70)), x.Bit(3), x.Bit(100), s(new(big.Int).SetBit(x, 64, 1)), s(new(big.Int).SetBit(x, 0, 0)), L(x.Bytes()))
    if x.Sign() >= 0 { fmt.Println(s(new(big.Int).Sqrt(x)), B(x.ProbablyPrime(10)), B(x.ProbablyPrime(0))) }
    for _, y := range xs {
      line := fmt.Sprintf("%s %s %s %d %d %s %s %s %s", s(new(big.Int).Add(x, y)), s(new(big.Int).Sub(x, y)), s(new(big.Int).Mul(x, y)), x.Cmp(y), x.CmpAbs(y), s(new(big.Int).And(x, y)), s(new(big.Int).Or(x, y)), s(new(big.Int).Xor(x, y)), s(new(big.Int).AndNot(x, y)))
      if y.Sign() != 0 {
        q, r := new(big.Int).QuoRem(x, y, new(big.Int)); d, m := new(big.Int).DivMod(x, y, new(big.Int))
        line += fmt.Sprintf(" %s %s %s %s %s", s(q), s(r), s(d), s(m), os(new(big.Int).ModInverse(x, y)))
      }
      a, b := new(big.Int), new(big.Int)
      g := new(big.Int).GCD(a, b, x, y)
      line += fmt.Sprintf(" %s %s %s", s(g), s(a), s(b))
      fmt.Println(line)
    }
    fmt.Println(os(new(big.Int).Exp(x, n(5), nil)), os(new(big.Int).Exp(x, n(65537), n(1000000007))), os(new(big.Int).Exp(x, n(-3), n(1000000007))), os(new(big.Int).Exp(x, n(12), n(-97))), os(new(big.Int).Exp(x, n(-2), nil)))
  }
  for _, t := range []string{"0x_1F", "0b1011", "0o17", "017", "0", "-0x10", "+42", "1_000_000", "1__0", "_1", "1_", "0x", "zz", "Zz", "0_7", "08", ""} {
    p := func(b int) string { v, ok := new(big.Int).SetString(t, b); if !ok { return "nil" }; return v.String() }
    fmt.Println(t, p(0), p(36), p(62), p(16))
  }
  u := new(big.Int).SetUint64(^uint64(0))
  fmt.Println(s(new(big.Int).Binomial(100, 50)), s(new(big.Int).MulRange(1, 30)), s(new(big.Int).MulRange(-3, 3)), s(new(big.Int).MulRange(-5, -2)), s(new(big.Int).SetBytes([]byte{1, 0, 255})), s(u))
  p := new(big.Int).Sub(new(big.Int).Lsh(n(1), 127), n(1))
  q, _ := new(big.Int).SetString("170141183460469231731687303715884105727", 10)
  fmt.Println(B(p.ProbablyPrime(20)), B(new(big.Int).Add(p, n(2)).ProbablyPrime(20)), B(n(561).ProbablyPrime(5)), B(n(3215031751).ProbablyPrime(0)), B(q.ProbablyPrime(1)))
}
