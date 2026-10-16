package main
import ("container/list"; "container/ring"; "fmt"; "strings")
func V(l *list.List) string { s := []string{}; for e := l.Front(); e != nil; e = e.Next() { s = append(s, fmt.Sprint(e.Value)) }; return "[" + strings.Join(s, ", ") + "]" }
func R(r *ring.Ring) string { s := []string{}; r.Do(func(x any) { s = append(s, fmt.Sprint(x)) }); return "[" + strings.Join(s, ", ") + "]" }
func mk(xs ...int) *ring.Ring { r := ring.New(len(xs)); for _, x := range xs { r.Value = x; r = r.Next() }; return r }
func cp(l *list.List) *list.List { n := list.New(); for e := l.Front(); e != nil; e = e.Next() { n.PushBack(e.Value) }; return n }
func main() {
  l := list.New()
  e4 := l.PushBack(4); e1 := l.PushFront(1); e3 := l.InsertBefore(3, e4); e2 := l.InsertAfter(2, e1)
  fmt.Println(V(l), l.Len())
  l.MoveToBack(e1); fmt.Println(V(l))
  l.MoveToFront(e4); l.MoveBefore(e3, e4); l.MoveAfter(e2, e3); fmt.Println(V(l))
  v := l.Remove(e3)
  fmt.Printf("%s Some(%v) None %v true\n", V(l), v, l.Front().Next().Value)
  rev := []string{}; for e := l.Back(); e != nil; e = e.Prev() { rev = append(rev, fmt.Sprint(e.Value)) }
  a := cp(l); x := list.New(); x.PushBack(7); x.PushBack(8); a.PushBackList(x)
  b := cp(l); b.PushFrontList(x)
  c := cp(l); for e := c.Front(); e != nil; e = e.Next() { if e.Value == 2 { e.Value = 20 } }
  fmt.Println("["+strings.Join(rev, ", ")+"]", V(a), V(b), V(c), 0)
  r := mk(1, 2, 3, 4, 5)
  fmt.Println(R(r), R(r.Move(2)), r.Move(-1).Value, r.Next().Next().Prev().Value, r.Len())
  r1 := mk(1, 2, 3, 4, 5); r1.Link(mk(10, 20)); 
  r2 := mk(1, 2, 3, 4, 5).Move(3); r2.Link(mk(10, 20))
  fmt.Println(R(r1), R(r2))
  r3 := mk(1, 2, 3, 4, 5); rm := r3.Unlink(2)
  r4 := mk(1, 2, 3, 4, 5); n5 := r4.Unlink(5)
  r5 := mk(1, 2, 3, 4, 5); r5.Unlink(7)
  _ = n5; fmt.Println(R(r3), R(rm), R(r5))
}
