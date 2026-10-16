package main
import ("fmt";"math/big";"strings";"bytes")
func main(){
 for _, p := range [][2]int64{{7,2},{-7,2},{7,-2},{-7,-2},{5,2},{-5,2},{6,4},{-6,4},{9,3},{0,5},{1,3},{2,3}} {
  line := fmt.Sprint(p[0], " ", p[1])
  for _, m := range []big.RoundingMode{big.Trunc, big.Floor, big.Round, big.Ceil} {
   q, r := new(big.Int).Divide(big.NewInt(p[0]), big.NewInt(p[1]), new(big.Int), m)
   line += " " + q.String() + " " + r.String()
  }
  fmt.Println(line)
 }
 for _, p := range [][2]string{{"a/b/c", "/"}, {"abc", "/"}, {"xx--yy--zz", "--"}, {"", ""}, {"ab", ""}} {
  a, b, f := strings.CutLast(p[0], p[1]); fmt.Printf("[%s] [%s] %v\n", a, b, f)
 }
 a, b, f := bytes.CutLast([]byte{1, 2, 1, 3}, []byte{1}); fmt.Println(strings.ReplaceAll(fmt.Sprint(a), " ", ", "), strings.ReplaceAll(fmt.Sprint(b), " ", ", "), f)
}
