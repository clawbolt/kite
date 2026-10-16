"""生成 std/internal/html_entities.kite：HTML5 命名字符引用表（数据取自 Go 的 html/entity.go，见 tools/html_entities.txt）
   每行 "1 名字 码点" 或 "2 名字 码点1 码点2"；按首字母分组成两层 match，查找不需要在运行时建表"""
import collections, os
here = os.path.dirname(os.path.abspath(__file__))
one, two = collections.defaultdict(list), collections.defaultdict(list)
for line in open(os.path.join(here, "html_entities.txt")):
    p = line.split()
    if p[0] == "1": one[p[1][0]].append((p[1], int(p[2])))
    else: two[p[1][0]].append((p[1], int(p[2]), int(p[3])))
out = ["// 由 tools/gen_html_entities.py 生成，不要手改。数据来自 Go 的 html/entity.go（HTML5 命名字符引用）", ""]
out.append("// 名字（含或不含结尾的 ;）对应的码点；没有时返回 0")
out.append("pub fn entity(name: Str) -> Int {")
out.append("  if len(name) == 0 { return 0 }")
out.append("  match __str_slice(name, 0, 1) {")
for c in sorted(one): out.append(f'    "{c}" => e1_{ord(c)}(name),')
out.append("    _ => 0,\n  }\n}\n")
for c in sorted(one):
    out.append(f"fn e1_{ord(c)}(name: Str) -> Int {{\n  match name {{")
    for n, v in one[c]: out.append(f'    "{n}" => {v},')
    out.append("    _ => 0,\n  }\n}\n")
out.append("// 对应两个码点的名字；没有时返回 (0, 0)")
out.append("pub fn entity2(name: Str) -> (Int, Int) {\n  match name {")
for c in sorted(two):
    for n, a, b in two[c]: out.append(f'    "{n}" => ({a}, {b}),')
out.append("    _ => (0, 0),\n  }\n}")
open(os.path.join(here, "..", "std", "internal", "html_entities.kite"), "w").write("\n".join(out) + "\n")
