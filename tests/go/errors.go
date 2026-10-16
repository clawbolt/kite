package main
import ("errors"; "fmt")
func B(b bool) string { if b { return "true" }; return "false" }
func main() {
  a := errors.New("not found")
  b := fmt.Errorf("open config: %w", a)
  c := fmt.Errorf("loading: %w", b)
  other := errors.New("timeout")
  j := errors.Join(c, nil, other)
  fmt.Println(a.Error(), "|", b.Error(), "|", c.Error())
  fmt.Println(j.Error())
  fmt.Println(B(errors.Is(c, a)), B(errors.Is(c, other)), B(errors.Is(j, a)), B(errors.Is(j, other)), B(errors.Is(a, b)))
  fmt.Println(B(errors.Unwrap(b) == a), B(errors.Unwrap(a) == nil), B(errors.Unwrap(j) == nil), len(j.(interface{ Unwrap() []error }).Unwrap()), B(errors.Join(nil, nil) == nil))
  var t error = a
  fmt.Println(B(t == a))
}
