package main
import ("fmt"; "math/rand/v2"; "strconv")
func main() {
  r := rand.New(rand.NewPCG(1, 2))
  line := ""
  for i := 0; i < 5; i++ { line += strconv.FormatUint(r.Uint64(), 10) + " " }
  fmt.Println(line)
  var seed [32]byte
  for i := range seed { seed[i] = byte(i*7 + 3) }
  c := rand.NewChaCha8(seed)
  cs := ""
  for k := 0; k < 140; k++ { x := c.Uint64(); if k%9 == 0 { cs += strconv.FormatUint(x, 10) + " " } }
  fmt.Println(cs)
  for _, g := range []*rand.Rand{rand.New(rand.NewPCG(42, 99)), rand.New(rand.NewChaCha8(seed))} {
    out := ""
    for _, n := range []int{1, 2, 3, 7, 10, 100, 1000, 1 << 40, 0x7fffffffffffffff, 6} {
      a := g.IntN(n); b := g.Int64(); cc := g.Uint32(); d := g.Int32(); e := g.Int32N(1000); f := g.Uint64N(^uint64(4)); h := g.Uint32N(77)
      out += fmt.Sprintf("%d %d %d %d %d %d %d\n", a, b, cc, d, e, f, h)
    }
    f1 := g.Float64(); f2 := g.Float32(); p := g.Perm(10)
    sh := []string{"a", "b", "c", "d", "e"}
    g.Shuffle(len(sh), func(i, j int) { sh[i], sh[j] = sh[j], sh[i] })
    sw := ""
    g.Shuffle(4, func(i, j int) { if sw != "" { sw += ", " }; sw += fmt.Sprintf("(%d, %d)", i, j) })
    q := []string{}
    for _, s := range sh { q = append(q, strconv.Quote(s)) }
    ps := ""
    for i, v := range p { if i > 0 { ps += ", " }; ps += strconv.Itoa(v) }
    qs := ""
    for i, v := range q { if i > 0 { qs += ", " }; qs += v }
    out += fmt.Sprintf("%v %v [%s] [%s] [%s]\n", f1, float64(f2), ps, qs, sw)
    ns := ""
    for i := 0; i < 500; i++ { nv := g.NormFloat64(); ev := g.ExpFloat64(); ns += fmt.Sprintf("%v %v\n", nv, ev) }
    z := rand.NewZipf(g, 1.5, 2.0, 1000)
    zs := ""
    for i := 0; i < 30; i++ { zs += fmt.Sprintf("%d ", z.Uint64()) }
    fmt.Println(out + zs)
    fmt.Println(ns)
  }
  fmt.Println("None")
}
