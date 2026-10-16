# 生成 std/go/ast.kite 里的 Node 枚举、fields（供 fprint）和 children（Walk 顺序）
# 用法：python3 tools/gen_go_ast.py > /tmp/ast_gen.kite
SPEC = """
Comment: Slash slash pos | Text text str
CommentGroup: List list list:[]*ast.Comment
Field: Doc doc node | Names names list:[]*ast.Ident | Type typ node | Tag tag node | Comment comment node
FieldList: Opening opening pos | List list list:[]*ast.Field | Closing closing pos
BadExpr: From from pos | To to pos
Ident: NamePos name_pos pos | Name name str | Obj obj obj
Ellipsis: Ellipsis ellipsis pos | Elt elt node
BasicLit: ValuePos value_pos pos | ValueEnd value_end pos | Kind kind tok | Value value str
FuncLit: Type typ node | Body body node
CompositeLit: Type typ node | Lbrace lbrace pos | Elts elts list:[]ast.Expr | Rbrace rbrace pos | Incomplete incomplete bool
ParenExpr: Lparen lparen pos | X x node | Rparen rparen pos
SelectorExpr: X x node | Sel sel node
IndexExpr: X x node | Lbrack lbrack pos | Index index node | Rbrack rbrack pos
IndexListExpr: X x node | Lbrack lbrack pos | Indices indices list:[]ast.Expr | Rbrack rbrack pos
SliceExpr: X x node | Lbrack lbrack pos | Low low node | High high node | Max max node | Slice3 slice3 bool | Rbrack rbrack pos
TypeAssertExpr: X x node | Lparen lparen pos | Type typ node | Rparen rparen pos
CallExpr: Fun fun node | Lparen lparen pos | Args args list:[]ast.Expr | Ellipsis ellipsis pos | Rparen rparen pos
StarExpr: Star star pos | X x node
UnaryExpr: OpPos op_pos pos | Op op tok | X x node
BinaryExpr: X x node | OpPos op_pos pos | Op op tok | Y y node
KeyValueExpr: Key key node | Colon colon pos | Value value node
ArrayType: Lbrack lbrack pos | Len length node | Elt elt node
StructType: Struct struct_pos pos | Fields fields node | Incomplete incomplete bool
FuncType: Func func_pos pos | TypeParams type_params node | Params params node | Results results node
InterfaceType: Interface interface_pos pos | Methods methods node | Incomplete incomplete bool
MapType: Map map_pos pos | Key key node | Value value node
ChanType: Begin begin pos | Arrow arrow pos | Dir dir int | Value value node
BadStmt: From from pos | To to pos
DeclStmt: Decl decl node
EmptyStmt: Semicolon semicolon pos | Implicit implicit bool
LabeledStmt: Label label node | Colon colon pos | Stmt stmt node
ExprStmt: X x node
SendStmt: Chan chan_x node | Arrow arrow pos | Value value node
IncDecStmt: X x node | TokPos tok_pos pos | Tok tok tok
AssignStmt: Lhs lhs list:[]ast.Expr | TokPos tok_pos pos | Tok tok tok | Rhs rhs list:[]ast.Expr
GoStmt: Go go_pos pos | Call call node
DeferStmt: Defer defer_pos pos | Call call node
ReturnStmt: Return return_pos pos | Results results list:[]ast.Expr
BranchStmt: TokPos tok_pos pos | Tok tok tok | Label label node
BlockStmt: Lbrace lbrace pos | List list list:[]ast.Stmt | Rbrace rbrace pos
IfStmt: If if_pos pos | Init init_s node | Cond cond node | Body body node | Else else_s node
CaseClause: Case case_pos pos | List list list:[]ast.Expr | Colon colon pos | Body body list:[]ast.Stmt
SwitchStmt: Switch switch_pos pos | Init init_s node | Tag tag node | Body body node
TypeSwitchStmt: Switch switch_pos pos | Init init_s node | Assign assign node | Body body node
CommClause: Case case_pos pos | Comm comm node | Colon colon pos | Body body list:[]ast.Stmt
SelectStmt: Select select_pos pos | Body body node
ForStmt: For for_pos pos | Init init_s node | Cond cond node | Post post node | Body body node
RangeStmt: For for_pos pos | Key key node | Value value node | TokPos tok_pos pos | Tok tok tok | Range range_pos pos | X x node | Body body node
ImportSpec: Doc doc node | Name name node | Path path node | Comment comment node | EndPos end_pos pos
ValueSpec: Doc doc node | Names names list:[]*ast.Ident | Type typ node | Values values list:[]ast.Expr | Comment comment node
TypeSpec: Doc doc node | Name name node | TypeParams type_params node | Assign assign pos | Type typ node | Comment comment node
BadDecl: From from pos | To to pos
GenDecl: Doc doc node | TokPos tok_pos pos | Tok tok tok | Lparen lparen pos | Specs specs list:[]ast.Spec | Rparen rparen pos
FuncDecl: Doc doc node | Recv recv node | Name name node | Type typ node | Body body node
File: Doc doc node | Package package_pos pos | Name name node | Decls decls list:[]ast.Decl | FileStart file_start pos | FileEnd file_end pos | Scope scope scope | Imports imports list:[]*ast.ImportSpec | Unresolved unresolved unres | Comments comments list:[]*ast.CommentGroup | GoVersion go_version str
"""
KT = {"pos": "Int", "str": "Str", "tok": "Int", "int": "Int", "bool": "Bool", "node": "Node", "obj": "Int", "scope": "Option<Scope>", "unres": "List<Node>"}
rows = []
for line in SPEC.strip().split("\n"):
    name, rest = line.split(":", 1)
    fs = []
    for part in rest.split("|"):
        g, k, t = part.split()
        fs.append((g, k, t))
    if name == "File":
        fs.append(("", "unresolved_set", "bool"))
        fs.append(("", "objects", "objects"))
    rows.append((name, fs))

out = []
out.append("pub enum Node {\n  Nil")
for name, fs in rows:
    parts = []
    for g, k, t in fs:
        kt = "List<Node>" if t.startswith("list:") else ("List<Object>" if t == "objects" else KT[t])
        parts.append(f"{k}: {kt}")
    out.append(f"  {name} {{ " + ", ".join(parts) + " }")
out.append("}\n")

# 类型名
out.append("pub fn type_name(n: Node) -> Str {\n  match n {\n    Nil => \"nil\",")
for name, fs in rows:
    out.append(f"    {name} {{ .. }} => \"{name}\",")
out.append("  }\n}\n")

# fields：给 fprint 用
out.append("fn fields(n: Node) -> List<FV> {\n  match n {\n    Nil => [],")
for name, fs in rows:
    binds = ", ".join(k for g, k, t in fs if g)
    items = []
    for g, k, t in fs:
        if not g: continue
        if t == "pos": items.append(f'FPos("{g}", {k})')
        elif t == "str": items.append(f'FStr("{g}", {k})')
        elif t == "tok": items.append(f'FTok("{g}", {k})')
        elif t == "int": items.append(f'FInt("{g}", {k})')
        elif t == "bool": items.append(f'FBool("{g}", {k})')
        elif t == "node": items.append(f'FNode("{g}", {k})')
        elif t == "obj": items.append(f'FObj("{g}", {k})')
        elif t == "scope": items.append(f'FScope("{g}", {k})')
        elif t == "unres": items.append(f'FList("{g}", {k}, "[]*ast.Ident", unresolved_set)')
        elif t.startswith("list:"): items.append(f'FList("{g}", {k}, "{t[5:]}", len({k}) > 0)')
    extra = ", unresolved_set" if name == "File" else ""
    out.append(f"    {name} {{ {binds}{extra}, .. }} => [" + ", ".join(items) + "],")
out.append("  }\n}\n")

# children：Walk 的顺序
out.append("// Walk 访问子节点的顺序（File 只走 Doc、Name、Decls）\npub fn children(n: Node) -> List<Node> {\n  match n {\n    Nil => [],")
for name, fs in rows:
    sel = [(k, t) for g, k, t in fs if t == "node" or t.startswith("list:")]
    if name == "File": sel = [("doc", "node"), ("name", "node"), ("decls", "list:")]
    if not sel:
        out.append(f"    {name} {{ .. }} => [],"); continue
    binds = ", ".join(k for k, t in sel)
    expr = " + ".join((f"nn({k})" if t == "node" else k) for k, t in sel)
    out.append(f"    {name} {{ {binds}, .. }} => {expr},")
out.append("  }\n}\n")

# map_children：对每个节点字段和节点列表字段应用 f，重建同一个变体
out.append("// 对每个子节点字段（含列表）应用 f，重建同一个变体\npub fn map_children(n: Node, f: fn(Node) -> Node) -> Node {\n  match n {\n    Nil => Nil,")
for name, fs in rows:
    binds = ", ".join(k for g, k, t in fs)
    args = []
    for g, k, t in fs:
        if t == "node": args.append(f"{k}: f({k})")
        elif t.startswith("list:") or t == "unres": args.append(f"{k}: map({k}, f)")
        else: args.append(f"{k}: {k}")
    out.append(f"    {name} {{ {binds} }} => Node.{name} {{ " + ", ".join(args) + " },")
out.append("  }\n}\n")
print("\n".join(out))
