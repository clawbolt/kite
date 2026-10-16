package main
import ("cmp"; "fmt"; "maps"; "math"; "slices"; "sort"; "strings"; "strconv")
func kf(x float64) string { if math.IsNaN(x) { return "nan" }; if math.IsInf(x, 1) { return "inf" }; if x == math.Trunc(x) { return strconv.FormatFloat(x, 'f', 1, 64) }; return strconv.FormatFloat(x, 'g', -1, 64) }
func B(b bool) string { if b { return "true" }; return "false" }
func Q(xs []string) string { s := []string{}; for _, x := range xs { s = append(s, "\""+x+"\"") }; return "[" + strings.Join(s, ", ") + "]" }
func I(xs []int) string { s := []string{}; for _, x := range xs { s = append(s, fmt.Sprint(x)) }; return "[" + strings.Join(s, ", ") + "]" }
type P struct{ k string; v int }
func main() {
  a := []int{3, 1, 2, -5, 3}; sort.Ints(a)
  b := []string{"b", "a", "B", ""}; sort.Strings(b)
  f := []float64{2.5, math.NaN(), -1.0, math.Inf(1)}; sort.Float64s(f)
  fs := []string{}; for _, x := range f { fs = append(fs, kf(x)) }
  fmt.Println(I(a), Q(b), "["+strings.Join(fs, ", ")+"]")
  i, ok := sort.Find(5, func(i int) int { return 3 - i })
  fmt.Println(sort.SearchInts([]int{1, 3, 5, 7}, 4), sort.SearchStrings([]string{"a", "c"}, "c"), sort.Search(100, func(i int) bool { return i*i >= 50 }), fmt.Sprintf("(%d, %s)", i, B(ok)))
  ps := []P{{"a", 3}, {"b", 1}, {"c", 3}, {"d", 2}, {"e", 1}}
  sort.SliceStable(ps, func(i, j int) bool { return ps[i].v < ps[j].v })
  pss := []string{}; for _, p := range ps { pss = append(pss, fmt.Sprintf("(\"%s\", %d)", p.k, p.v)) }
  r := []int{5, 4, 9}; sort.Sort(sort.Reverse(sort.IntSlice(r)))
  fmt.Println("["+strings.Join(pss, ", ")+"]", B(sort.SliceIsSorted([]int{1, 2, 2}, func(i, j int) bool { return []int{1, 2, 2}[i] < []int{1, 2, 2}[j] })), B(sort.IntsAreSorted([]int{2, 1})), I(r))
  fmt.Println(cmp.Compare(1, 2), cmp.Compare("b", "a"), cmp.Compare(math.NaN(), 1.0), cmp.Compare(math.NaN(), math.NaN()), B(cmp.Less(math.NaN(), -1.0)), cmp.Or("", "x", "y"), cmp.Or(0, 0))
  m := map[string]int{"a": 1, "b": 2, "c": 3}
  d := maps.Clone(m); maps.DeleteFunc(d, func(k string, v int) bool { return v%2 == 1 })
  c := maps.Clone(m); maps.Copy(c, map[string]int{"z": 26, "a": 0})
  x := map[string]int{}; x["x"] = 1; x["x"] = 2
  fmt.Println(Q(slices.Sorted(maps.Keys(m))), B(maps.Equal(m, maps.Clone(m))), Q(slices.Sorted(maps.Keys(d))), c["a"], len(x), B(maps.EqualFunc(m, map[string]string{"a": "1", "b": "2", "c": "3"}, func(a int, b string) bool { return fmt.Sprint(a) == b })))
}
