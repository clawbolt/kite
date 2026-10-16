"""Kite 静态类型检查器 (M1)

- 基于合一（union-find）的局部类型推断；函数签名可省略类型，由函数体推断
- 泛型函数 / 泛型结构体 / 泛型枚举，调用处自动实例化
- 双向检查：lambda 参数类型从期望的函数类型推出，如 map(xs, x => x.len())
- 不允许隐式数值转换（Int 与 Float 不能混算）
- match 穷尽性检查（枚举、Bool、其他类型需兜底分支）
- let 不可变绑定的赋值在编译期报错
- 未能推断的数值类型默认为 Int
"""
from kite import KiteError, lex, Parser, Node


class TypeErr(KiteError):
    pass


# ───────────────────────── 类型表示 ─────────────────────────
class TVar:
    _n = 0
    __slots__ = ("id", "ref")
    def __init__(self):
        TVar._n += 1; self.id = TVar._n; self.ref = None


class TCon:
    __slots__ = ("name", "args")
    def __init__(self, name, args=()):
        self.name, self.args = name, tuple(args)


def _mentions_self(ta):
    if isinstance(ta, tuple):
        if len(ta) > 1 and ta[1] == "Self": return True
        return any(_mentions_self(x) for x in ta[1:])
    if isinstance(ta, list): return any(_mentions_self(x) for x in ta)
    return False


class TGen:  # 签名中的泛型参数，实例化时替换
    __slots__ = ("name", "bounds", "is_const")
    def __init__(self, name, bounds=(), is_const=False): self.name, self.bounds, self.is_const = name, tuple(bounds), is_const


class TRigid(TCon):  # 函数体内的泛型参数：不透明类型，只能用其 trait 约束提供的能力
    __slots__ = ("bounds",)
    def __init__(self, name, bounds=()):
        super().__init__(name); self.bounds = tuple(bounds)


class TDyn(TRigid):  # dyn Tr：任何实现了 Tr 的类型都能当作它用；只能用 Tr 提供的能力
    __slots__ = ()


OP_METHODS = {"+": "add", "-": "sub", "*": "mul", "/": "div", "%": "rem",
              "<": "cmp", ">": "cmp", "<=": "cmp", ">=": "cmp"}
OP_TRAITS = {"+": "Add", "-": "Sub", "*": "Mul", "/": "Div", "%": "Rem", "neg": "Neg",
             "<": "Cmp", ">": "Cmp", "<=": "Cmp", ">=": "Cmp"}
BUILTIN_TRAITS = {"Eq", "Ord", "Show", "Num", "Hash"}
BUILTIN_IMPLS = {
    "Num": {"Int", "Float"},
    "Ord": {"Int", "Float", "Str"},
    "Eq": {"Int", "Float", "Str", "Bool", "Unit", "List", "Tuple", "Option", "Result", "Map", "Set"},
    "Hash": {"Int", "Str", "Bool", "Tuple"},
}
# 约束隐含: Ord ⇒ Eq, Num ⇒ Ord
IMPLIED = {"Ord": {"Eq"}, "Num": {"Ord", "Eq"}}


INT, FLOAT, STR, BOOL, UNIT = TCon("Int"), TCon("Float"), TCon("Str"), TCon("Bool"), TCon("Unit")
ANY, NEVER = TCon("Any"), TCon("Never")
PRIMS = {"Int": INT, "Float": FLOAT, "Str": STR, "Bool": BOOL, "Unit": UNIT, "Char": STR,
         "Bytes": TCon("Bytes"), "Ptr": TCon("Ptr"), "Any": ANY, "Never": NEVER,
         "I8": INT, "I16": INT, "I32": INT, "I64": INT, "U8": INT, "U16": INT, "U32": INT, "U64": INT,
         "F32": FLOAT, "F64": FLOAT}
# 内建泛型类型及其参数个数
BUILTIN_GENERIC = {"List": 1, "Map": 2, "Set": 1, "Option": 1, "Result": 2, "Chan": 1, "Actor": 1}


def fn_t(params, ret): return TCon("fn", list(params) + [ret])
def list_t(t): return TCon("List", [t])
def opt_t(t): return TCon("Option", [t])
def res_t(t, e): return TCon("Result", [t, e])


_PROJ = [None]   # 关联类型投影的规约函数（Checker 安装）


def prune(t):
    while True:
        while isinstance(t, TVar) and t.ref is not None:
            t = t.ref
        # 关联类型投影 T.Item 表示成 TCon("@Item", [T])：T 已知时规约成具体类型
        if type(t) is TCon and t.name[:1] == "@" and _PROJ[0] is not None:
            r = _PROJ[0](t)
            if r is not t:
                t = r; continue
        return t


def is_proj(t): return type(t) is TCon and t.name[:1] == "@"


def show_t(t, names=None):
    names = {} if names is None else names
    t = prune(t)
    if isinstance(t, TVar):
        if t.id not in names:
            names[t.id] = "?" + "abcdefghijklmnopqrstuvwxyz"[len(names) % 26]
        return names[t.id]
    if isinstance(t, TGen):
        return t.name
    if t.name[:1] == "@":
        return f"{show_t(t.args[0], names)}.{t.name[1:]}"
    if t.name == "fn":
        ps = ", ".join(show_t(a, names) for a in t.args[:-1])
        return f"fn({ps}) -> {show_t(t.args[-1], names)}"
    if t.name == "UnsafeFn" and len(t.args) == 1:
        return "unsafe " + show_t(t.args[0], names)
    if t.name == "Tuple":
        return "(" + ", ".join(show_t(a, names) for a in t.args) + ")"
    if t.name == "Array" and len(t.args) == 2:
        return f"[{show_t(t.args[0], names)}; {show_t(t.args[1], names)}]"
    if t.name[:1] == "#":
        return t.name[1:]
    if t.args:
        return t.name + "<" + ", ".join(show_t(a, names) for a in t.args) + ">"
    return t.name


def occurs(v, t):
    t = prune(t)
    if t is v: return True
    return isinstance(t, TCon) and any(occurs(v, a) for a in t.args)


def subst(t, m):
    t = prune(t)
    if isinstance(t, TGen):
        return m.get(t.name, t)
    if isinstance(t, TCon) and t.args:
        return TCon(t.name, [subst(a, m) for a in t.args])
    return t


def gen_objs(t, acc=None):
    acc = {} if acc is None else acc
    t = prune(t)
    if isinstance(t, TGen):
        acc.setdefault(t.name, t)
    elif isinstance(t, TCon):
        for a in t.args: gen_objs(a, acc)
    return acc


def gens_in(t, acc=None):
    acc = [] if acc is None else acc
    t = prune(t)
    if isinstance(t, TGen):
        if t.name not in acc: acc.append(t.name)
    elif isinstance(t, TCon):
        for a in t.args: gens_in(a, acc)
    return acc


def instantiate(t):
    gs = gens_in(t)
    return subst(t, {g: TVar() for g in gs}) if gs else t


# ───────────────────────── 内建函数签名 ─────────────────────────
VARIADIC = "variadic"
BUILTIN_SIGS = {
    "print": VARIADIC,
    "len": {"List": "fn(List<a>) -> Int", "Str": "fn(Str) -> Int", "Map": "fn(Map<k, v>) -> Int", "Set": "fn(Set<a>) -> Int"}, "str": "fn(Any) -> Str", "type_of": "fn(Any) -> Str",
    "int": "fn(Any) -> Int", "float": "fn(Any) -> Float",
    "map": "fn(List<a>, fn(a) -> b) -> List<b>",
    "filter": "fn(List<a>, fn(a) -> Bool) -> List<a>",
    "par_map": "fn(List<a>, fn(a) -> b) -> List<b>",
    "each": "fn(List<a>, fn(a) -> b) -> Unit",
    "reduce": "fn(List<a>, b, fn(b, a) -> b) -> b",
    "fold": "fn(List<a>, b, fn(b, a) -> b) -> b",
    "sum": "fn(List<a>) -> a",
    "count": {"#1": "fn(List<a>) -> Int", "#2": "fn(List<a>, fn(a) -> Bool) -> Int"},
    "any": "fn(List<a>, fn(a) -> Bool) -> Bool",
    "all": "fn(List<a>, fn(a) -> Bool) -> Bool",
    "find": "fn(List<a>, fn(a) -> Bool) -> Option<a>",
    "first": "fn(List<a>) -> Option<a>", "last": "fn(List<a>) -> Option<a>",
    "get": {"List": "fn(List<a>, Int) -> Option<a>", "Map": "fn(Map<k, v>, k) -> Option<v>"},
    "take": "fn(List<a>, Int) -> List<a>", "skip": "fn(List<a>, Int) -> List<a>",
    "reverse": {"List": "fn(List<a>) -> List<a>", "Str": "fn(Str) -> Str"},
    "sort": "fn(List<a>) -> List<a>",
    "sort_by": "fn(List<a>, fn(a) -> b) -> List<a>",
    "sort_with": "fn(List<a>, fn(a, a) -> Int) -> List<a>",
    "__fmt": "fn(Any, Str) -> Str",
    "__repeat": "fn(a, Int) -> List<a>",
    "zip": "fn(List<a>, List<b>) -> List<(a, b)>",
    "enumerate": "fn(List<a>) -> List<(Int, a)>",
    "push": "fn(List<a>, a) -> List<a>",
    "concat": "fn(List<a>, List<a>) -> List<a>",
    "contains": {"List": "fn(List<a>, a) -> Bool", "Str": "fn(Str, Str) -> Bool", "Map": "fn(Map<k, v>, k) -> Bool", "Set": "fn(Set<a>, a) -> Bool"},
    "join": {"#1": "fn(List<a>) -> Str", "#2": "fn(List<a>, Str) -> Str"},
    "split": {"#1": "fn(Str) -> List<Str>", "#2": "fn(Str, Str) -> List<Str>"},
    "trim": "fn(Str) -> Str", "upper": "fn(Str) -> Str", "lower": "fn(Str) -> Str",
    "starts_with": "fn(Str, Str) -> Bool", "ends_with": "fn(Str, Str) -> Bool",
    "replace": "fn(Str, Str, Str) -> Str", "chars": "fn(Str) -> List<Str>",
    "parse_int": "fn(Str) -> Result<Int, Str>", "parse_float": "fn(Str) -> Result<Float, Str>",
    "keys": "fn(Map<k, v>) -> List<k>", "remove": {"Map": "fn(Map<k, v>, k) -> Map<k, v>", "Set": "fn(Set<a>, a) -> Set<a>"}, "values": "fn(Map<k, v>) -> List<v>",
    "set": {"List": "fn(List<a>, Int, a) -> List<a>", "Map": "fn(Map<k, v>, k, v) -> Map<k, v>"},
    "Set": {"#0": "fn() -> Set<a>", "#1": "fn(List<a>) -> Set<a>"},
    "insert": "fn(Set<a>, a) -> Set<a>", "union": "fn(Set<a>, Set<a>) -> Set<a>",
    "intersect": "fn(Set<a>, Set<a>) -> Set<a>", "difference": "fn(Set<a>, Set<a>) -> Set<a>",
    "is_subset": "fn(Set<a>, Set<a>) -> Bool",
    "__band": "fn(Int, Int) -> Int", "__bor": "fn(Int, Int) -> Int", "__bxor": "fn(Int, Int) -> Int",
    "__shl": "fn(Int, Int) -> Int", "__shr": "fn(Int, Int) -> Int", "__bnot": "fn(Int) -> Int",
    "__set_lit": "fn(List<a>) -> Set<a>", "__set_union": "fn(Set<a>, Set<a>) -> Set<a>",
    "__set_intersect": "fn(Set<a>, Set<a>) -> Set<a>", "__set_difference": "fn(Set<a>, Set<a>) -> Set<a>",
    "to_list": {"List": "fn(List<a>) -> List<a>", "Set": "fn(Set<a>) -> List<a>", "Map": "fn(Map<k, v>) -> List<(k, v)>", "Str": "fn(Str) -> List<Str>"},
    "Map": {"#0": "fn() -> Map<k, v>", "#1": "fn(List<(k, v)>) -> Map<k, v>"},
    "min": {"#1": "fn(List<a>) -> a", "#2": "fn(a, a) -> a"},
    "max": {"#1": "fn(List<a>) -> a", "#2": "fn(a, a) -> a"},
    "abs": "fn(a) -> a",
    "unwrap": {"Result": "fn(Result<a, e>) -> a", "Option": "fn(Option<a>) -> a"},
    "unwrap_or": {"Result": "fn(Result<a, e>, a) -> a", "Option": "fn(Option<a>, a) -> a"},
    "is_ok": "fn(Result<a, e>) -> Bool", "is_err": "fn(Result<a, e>) -> Bool",
    "is_some": "fn(Option<a>) -> Bool", "is_none": "fn(Option<a>) -> Bool",
    "panic": {"#0": "fn() -> Never", "#1": "fn(Any) -> Never"},
    "assert": {"#1": "fn(Bool) -> Unit", "#2": "fn(Bool, Str) -> Unit"},
    "assert_eq": {"#2": "fn(a, a) -> Unit", "#3": "fn(a, a, Str) -> Unit"},
    "__kite_test": "fn() -> Str",
    "chan": {"#0": "fn() -> Chan<a>", "#1": "fn(Int) -> Chan<a>"},
    "send": "fn(Chan<a>, a) -> Unit", "recv": "fn(Chan<a>) -> Option<a>",
    "try_recv": "fn(Chan<a>) -> Option<a>", "close": "fn(Chan<a>) -> Unit",
    "sleep": "fn(Int) -> Unit", "now_ms": "fn() -> Int",
    "timeout": "fn(Int, fn() -> a) -> Option<a>",
    "actor": "fn(s, fn(s, m) -> s, fn(Actor<m>) -> r) -> (r, s)",
    "__recover_raw": "fn(fn() -> a) -> Result<a, (Int, Str, Str)>", "__repanic": "fn(Str, Str) -> Never",
    "__restart_log": "fn(Str, Int, Int, Str) -> Unit", "__restart_log_w": "fn(Str, Int, Int, Int, Int, Str) -> Unit", "supervise": "fn(Int, fn() -> a) -> a",
    "actor_supervised": "fn(s, fn(s, m) -> s, Int, fn(Actor<m>) -> r) -> (r, s)",
    "tell": "fn(Actor<m>, m) -> Unit", "ask": "fn(Actor<m>, fn(Chan<r>) -> m) -> r",
    "assert_ne": {"#2": "fn(a, a) -> Unit", "#3": "fn(a, a, Str) -> Unit"},
    # 标准库（std/*.kite）用的底层原语
    "__env_get": "fn(Str) -> Option<Str>", "__args": "fn() -> List<Str>", "__exit": "fn(Int) -> Never",
    "__run": "fn(Str) -> (Int, Str)", "__ord": "fn(Str) -> Int", "__chr": "fn(Int) -> Str",
    "__slice": {"List": "fn(List<a>, Int, Int, Int) -> List<a>", "Str": "fn(Str, Int, Int, Int) -> Str"},
    "__unix_ms": "fn() -> Int", "__str_slice": "fn(Str, Int, Int) -> Str", "__str_find": "fn(Str, Str, Int) -> Int",
    "__tcp_listen": "fn(Str, Int) -> Result<Int, Str>", "__tcp_port": "fn(Int) -> Int",
    "__tcp_accept": "fn(Int) -> Result<Option<Int>, Str>", "__tcp_connect": "fn(Str, Int) -> Result<Int, Str>",
    "__tcp_read_line": "fn(Int) -> Result<Option<Str>, Str>", "__tcp_read_bytes": "fn(Int, Int) -> Result<Option<Str>, Str>",
    "__tcp_read_all": "fn(Int) -> Result<Option<Str>, Str>", "__tcp_write": "fn(Int, Str) -> Result<Bool, Str>",
    "__tls_server_ctx": "fn(Str, Str) -> Result<Int, Str>", "__tls_accept": "fn(Int, Int) -> Result<Unit, Str>",
    "__tls_client": "fn(Int, Str, Str, Bool) -> Result<Unit, Str>", "__tls_handshake": "fn(Int) -> Result<Bool, Str>",
    "__tcp_flush": "fn(Int) -> Result<Bool, Str>", "__tcp_close": "fn(Int) -> Unit", "__byte_len": "fn(Str) -> Int",
    "__bytes": "fn(Str) -> List<Int>", "__from_bytes": "fn(List<Int>) -> Option<Str>", "__eprint": "fn(Str) -> Unit",
    "__fs_stat": "fn(Str, Bool) -> Result<(Int, Int, Int, Int), Str>", "__getcwd": "fn() -> Str",
    "__chdir": "fn(Str) -> Result<Unit, Str>", "__chmod": "fn(Str, Int) -> Result<Unit, Str>",
    "__env_set": "fn(Str, Str) -> Unit", "__env_unset": "fn(Str) -> Unit", "__environ": "fn() -> List<Str>",
    "__hostname": "fn() -> Str", "__getpid": "fn() -> Int", "__read_bytes": "fn(Str) -> Result<List<Int>, Str>",
    "__write_bytes": "fn(Str, List<Int>, Bool) -> Result<Unit, Str>", "__tz_offset": "fn(Int) -> Int", "__random_bytes": "fn(Int) -> List<Int>", "__unix_ns": "fn() -> Int",
}
# 需要数值约束的内建返回值
NUMERIC_BUILTINS = {"sum", "abs"}
STD_SIGS = {
    "io": {"print": VARIADIC, "println": VARIADIC, "read_line": "fn() -> Option<Str>"},
    "fs": {"read": "fn(Str) -> Result<Str, Str>", "write": "fn(Str, Str) -> Result<Unit, Str>",
           "append": "fn(Str, Str) -> Result<Unit, Str>", "exists": "fn(Str) -> Bool", "is_file": "fn(Str) -> Bool",
           "is_dir": "fn(Str) -> Bool", "size": "fn(Str) -> Result<Int, Str>", "list": "fn(Str) -> Result<List<Str>, Str>",
           "mkdir": "fn(Str) -> Result<Unit, Str>", "remove": "fn(Str) -> Result<Unit, Str>",
           "rename": "fn(Str, Str) -> Result<Unit, Str>"},
    "ptr": {"null": "fn() -> Ptr", "is_null": "fn(Ptr) -> Bool", "addr": "fn(Ptr) -> Int",
            "offset": "fn(Ptr, Int) -> Ptr", "from_addr": "fn(Int) -> Ptr",
            "alloc": "fn(Int) -> Ptr", "free": "fn(Ptr) -> Unit",
            "read_int": "fn(Ptr, Int) -> Int", "write_int": "fn(Ptr, Int, Int) -> Unit",
            "read_float": "fn(Ptr, Int) -> Float", "write_float": "fn(Ptr, Int, Float) -> Unit",
            "read_byte": "fn(Ptr, Int) -> Int", "write_byte": "fn(Ptr, Int, Int) -> Unit",
            "to_str": "fn(Ptr) -> Str", "from_str": "fn(Str) -> Ptr"},
    "math": dict({n: "fn(Float) -> Float" for n in ("sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh",
                                                   "tanh", "asinh", "acosh", "atanh", "exp", "exp2", "expm1", "log2",
                                                   "log10", "log1p", "cbrt", "log")},
                 **{n: "fn(Float, Float) -> Float" for n in ("pow", "atan2", "hypot", "fmod", "copysign", "remainder")},
                 **{n: "fn(Float) -> Int" for n in ("floor", "ceil", "round", "trunc")},
                 **{n: "fn(Float) -> Bool" for n in ("is_nan", "is_inf", "signbit")},
                 **{n: "Float" for n in ("pi", "e", "tau", "phi", "sqrt2", "ln2", "ln10", "inf", "nan", "max_float",
                                         "smallest_float")},
                 gcd="fn(Int, Int) -> Int", lcm="fn(Int, Int) -> Int", max_int="Int", min_int="Int"),
}


# std.ptr 里需要 unsafe 的函数（读写裸内存、分配释放、整数转指针）
UNSAFE_STD = {("ptr", n) for n in ("from_addr", "alloc", "free", "read_int", "write_int", "read_float",
                                   "write_float", "read_byte", "write_byte", "to_str", "from_str")}
EXT_PRIMS = ("Int", "Float", "Bool", "Str", "Ptr")
# extern 里较窄的 C 宽度：在 Kite 里就是 Int / Float，跨边界时检查范围并转换
NARROW_C = {"I8", "I16", "I32", "I64", "U8", "U16", "U32", "U64", "F32", "F64"}

def narrow_c(ta):
    """extern 签名里写的 I32 / U8 / F32 … → 宽度描述；否则 None"""
    if isinstance(ta, tuple) and len(ta) >= 2 and ta[0] == "con" and ta[1] in NARROW_C and not ta[2]:
        return ta[1]
    return None


SPAWN_RET = object()   # ret_stack 上的标记：当前在 spawn 任务里


def const_literal(e):
    """const 的初值若是（可带负号的）字面量就返回它的值，否则 None"""
    if e.kind == "Lit" and isinstance(e.value, (bool, int, float, str)): return e.value
    if e.kind == "Unary" and e.op == "-" and e.operand.kind == "Lit" and isinstance(e.operand.value, (int, float)) \
            and not isinstance(e.operand.value, bool):
        return -e.operand.value
    return None


class Scope:
    __slots__ = ("vars", "parent", "task")
    def __init__(self, parent=None): self.vars, self.parent, self.task = {}, parent, False
    def define(self, name, t, mutable=False, scheme=False): self.vars[name] = (t, mutable, scheme)
    def find(self, name):
        s = self
        while s:
            if name in s.vars: return s.vars[name]
            s = s.parent
        return None


DECL_KINDS = ("Fn", "Struct", "Enum", "Trait", "Impl", "Alias", "Use", "Mod")


def snapshot_types(body):
    """收集语法树里每个节点当前的 _t：[(节点, 类型)]"""
    out, stack = [], [body]
    while stack:
        x = stack.pop()
        if isinstance(x, (list, tuple)): stack.extend(x); continue
        if not hasattr(x, "kind") or not hasattr(x, "__dict__"): continue
        if "_t" in x.__dict__: out.append((x, x._t))
        for k, v in x.__dict__.items():
            if k not in ("kind", "line", "_t", "_tsnap", "_sigs", "_sig"): stack.append(v)
    return out


class Checker:
    def __init__(self):
        self.locals, self._in_prelude = [], False
        self.g = Scope()
        self.structs = {}   # 名字 → (泛型参数, [(字段, 类型AST)])
        self.enums = {}     # 名字 → (泛型参数, {变体: [(字段, 类型AST)] 或 None})
        self.variant_owner = {}
        self.variant_alts = {}   # 变体名 → 拥有它的所有枚举（同名变体按期望类型选）
        self.aliases = {}
        self.traits = {}
        self.supers = {}    # trait → 直接超 trait
        self.type_bounds = {}   # 类型名 → {泛型参数: [trait]}（type / enum 上的 <T: B> 与 where）
        self.type_cgens = {}    # 类型名 → 常量泛型参数名集合
        self.sig_cgens = {}     # id(签名) → 常量泛型参数名（按隐藏参数顺序）
        self.tc_info = {}       # 隐藏参数名 __tc_T_N → (T, N, 类型标注)：泛型参数上的关联常量 T.N
        self.cg_sites = []      # (调用节点, [常量实参类型], 所在函数的常量参数, 行)
        self.cg_fns = []        # 带常量泛型参数的函数 / 方法节点（检查完补上隐藏参数）
        self.cg_ctx = []        # 函数体检查时的常量泛型参数 {名字: TRigid}
        self._cg_node = None
        self._cg_keep = []      # 让 sig_cgens 里的签名对象一直存活（按 id 记录，防止 id 被复用）
        self.methods = {}   # 类型名 → {方法名: 签名(含 TGen)}
        self.impls = set()  # (类型名, trait)
        self.errors = []
        self.constraints = []  # (类型, 种类, 行号)
        self.ord_sites = []
        self.ret_stack = []
        self.loop_depth = 0
        self.labels = []
        self._sig_cache = {}
        self.trait_assoc = {}     # trait → {关联类型名: (约束, 默认类型 AST)}
        self.impl_assoc = {}      # (目标类型名, 关联类型名) → (impl 节点, 类型 AST)
        self._rigid_proj = {}
        self.deferred = []
        self.deprecated = {}      # id(函数签名) → (名字, 说明)
        self.fn_gens = {}         # id(函数签名) → (签名, 泛型参数名按声明顺序)
        self.warnings = []        # (行号, 文本)
        self.va_sigs = {}         # id(签名) → 可变参数 extern fn 节点（只能直接调用）
        self.externs = {}         # id(签名) → extern fn 名字；使用处必须在 unsafe { … } 里
        self.unsafe_depth = 0
        self.unsafe_methods = set()   # (类型名, 方法名)
        _PROJ[0] = self.norm_proj
        self._install()

    def inst(self, t, line):
        objs = gen_objs(t)
        cga = self.sig_cgens.get(id(t))
        if not objs:
            if cga: self._pending_cg = self.cg_vals(cga, {})
            return t
        m = {}
        for name, g in objs.items():
            v = TVar(); m[name] = v
            for b in g.bounds:
                self.constraints.append((v, ("impl", b), line))
        if cga: self._pending_cg = self.cg_vals(cga, m)
        return subst(t, m)

    def cg_vals(self, cga, m):
        """隐藏实参的待求解值：常量泛型参数是类型变量；T.N 是 ("tc", T 的类型变量, N)"""
        out = []
        for n in cga:
            tc = self.tc_info.get(n)
            out.append(("tc", m.get(tc[0]) or TVar(), tc[1]) if tc else (m.get(n) or TVar()))
        return out

    def take_cg(self, node, name, line):
        """调用了带常量泛型参数的函数：记下调用点，类型求解后补上隐藏实参"""
        vals, self._pending_cg = getattr(self, "_pending_cg", None), None
        if not vals: return
        if node is None:
            raise TypeErr(f"类型错误: {name} 带常量泛型参数，这里请写成调用形式 {name}(…)", line)
        self.cg_sites.append((node, vals, dict(self.cg_ctx[-1]) if self.cg_ctx else {}, line, name))

    def solve_cg_sites(self):
        bad = {getattr(e, "line", None) for e in self.errors}
        for node, vals, ctx, line, name in self.cg_sites:
            if line in bad: continue
            extra = []
            for v in vals:
                if isinstance(v, tuple):          # T.N：T 求解为具体类型 → 调用 "类型.N"()；仍是调用者的泛型 → 转交调用者的隐藏参数
                    _, tv, cn = v
                    p = prune(tv)
                    hn = f"__tc_{p.name}_{cn}" if isinstance(p, TRigid) else None
                    if hn and hn in ctx:
                        a = Node("Name", line, name=hn)
                    elif isinstance(p, TCon) and not isinstance(p, TRigid):
                        a = Node("Call", line, fn=Node("Name", line, name=f"{p.name}.{cn}"), args=[])
                    else:
                        self.errors.append(TypeErr(f"类型错误: 无法确定 {name} 的类型参数，取不到它的关联常量 {cn}", line)); break
                    extra.append(a); continue
                p = prune(v)
                if isinstance(p, TCon) and p.name[:1] == "#":
                    a = Node("Lit", line, value=int(p.name[1:]))
                elif isinstance(p, TRigid) and p.name in ctx:
                    a = Node("Name", line, name=p.name)
                elif isinstance(p, TVar):
                    self.errors.append(TypeErr(f"类型错误: 无法推断 {name} 的常量泛型参数（标注数组长度，或用 {name}.<…>(…) 显式指定）", line)); break
                else:
                    self.errors.append(TypeErr(f"类型错误: {name} 的常量泛型参数必须是整数，实际是 {show_t(p)}", line)); break
                a._t = INT; extra.append(a)
            else:
                node.args = list(node.args) + extra
        self.cg_sites = []
        for fn in self.cg_fns:
            if getattr(fn, "_cg_added", False): continue
            fn._cg_added = True
            fn.params = list(fn.params) + [(n, self.tc_info[n][2] if n in self.tc_info else ("con", "Int", [], fn.line))
                                           for n in fn._cgens_all]
            fn._cg_ptys = [self.conv(self.tc_info[n][2], {}) if n in self.tc_info else INT for n in fn._cgens_all]

    def assoc_owner(self, bounds, item):
        """在约束（含隐含）里找声明了关联类型 item 的 trait"""
        return next((b for b in self.closure(bounds) if item in self.trait_assoc.get(b, {})), None)

    def norm_proj(self, t):
        base = prune(t.args[0]); item = t.name[1:]
        if isinstance(base, TRigid):
            tr = self.assoc_owner(base.bounds, item)
            if tr is None: return t
            key = f"{base.name}.{item}"
            if key not in self._rigid_proj:
                self._rigid_proj[key] = TRigid(key, self.trait_assoc[tr][item][0])
            return self._rigid_proj[key]
        if isinstance(base, TCon) and not is_proj(base):
            if base.name in ("Any", "Never"): return ANY
            hit = self.impl_assoc.get((base.name, item))
            if hit is None: return t
            it, ta = hit
            gm = {g: TVar() for g in it.generics}
            self.unify(self.conv(it.target_t, gm), base, it.line)
            gm["Self"] = base
            return prune(self.conv(ta, gm))
        return t

    def closure(self, bounds):
        """约束连同全部（传递的）超 trait，保持顺序"""
        out = []
        def add(b):
            if b in out: return
            out.append(b)
            for sb in self.supers.get(b, ()): add(sb)
        for b in bounds: add(b)
        return out

    def has_bound(self, t, trait):
        bs = set(self.closure(t.bounds))
        for b in list(bs):
            bs |= IMPLIED.get(b, set())
        return trait in bs

    def implements(self, t, trait):
        t = prune(t)
        if isinstance(t, TVar) or isinstance(t, TGen):
            return True
        if isinstance(t, TRigid):
            return self.has_bound(t, trait)
        if trait == "Show" or t.name in ("Any", "Never"):
            return True
        if t.name in BUILTIN_IMPLS.get(trait, ()):
            return True
        return (t.name, trait) in self.impls

    # 内建
    def parse_sig(self, s):
        p = Parser(lex(s))
        return self.conv(p.type_(), {}, sig_vars=True)

    def builtin_type(self, sig):
        if sig == VARIADIC or isinstance(sig, dict):
            return ("builtin", sig)
        key = sig
        if key not in self._sig_cache:
            self._sig_cache[key] = self.parse_sig(sig)
        return self._sig_cache[key]

    def _install(self):
        self.enums["Option"] = (["T"], {"Some": [("_0", ("con", "T", [], 0))], "None": None})
        self.enums["Result"] = (["T", "E"], {"Ok": [("_0", ("con", "T", [], 0))], "Err": [("_0", ("con", "E", [], 0))]})
        for en in ("Option", "Result"):
            for vn in self.enums[en][1]:
                self.variant_owner[vn] = en
                self.g.define(vn, self.variant_ctor_type(en, vn), scheme=True)
        for name, sig in BUILTIN_SIGS.items():
            self.g.define(name, self.builtin_type(sig), scheme=True)
        self._ord_builtin = {n: self.g.vars[n] for n in ("sort", "min", "max")}

    # 类型 AST → 类型
    def _cg_rigid_names(self):
        return self.cg_ctx[-1] if getattr(self, "cg_ctx", None) else {}

    def conv(self, ta, gmap, sig_vars=False, line=None, const_ok=False, want_const=False):
        if ta is None:
            return TVar()
        kind = ta[0]
        if kind == "tuple":
            return TCon("Tuple", [self.conv(x, gmap, sig_vars) for x in ta[1]])
        if kind == "fn":
            return fn_t([self.conv(x, gmap, sig_vars) for x in ta[1]], self.conv(ta[2], gmap, sig_vars))
        if kind == "unsafe":
            return TCon("UnsafeFn", [self.conv(ta[1], gmap, sig_vars)])
        if kind == "impl":
            raise TypeErr(f"类型错误: impl {' + '.join(ta[1])} 只能写在函数参数或返回类型里", ta[2])
        if kind == "dyn":
            for b in ta[1]:
                if b not in BUILTIN_TRAITS and b not in self.traits:
                    raise TypeErr(f"类型错误: 未知 trait {b}", ta[2])
            return TDyn("dyn " + " + ".join(ta[1]), ta[1])
        _, name, args, ln = ta
        if name[:1] == "#":
            if not const_ok:
                raise TypeErr(f"类型错误: 这里需要类型，{name[1:]} 是常量（只能用作常量泛型实参或数组长度）", ln)
            return TCon(name)
        if name == "Array" and len(args) == 2:
            return TCon("Array", [self.conv(args[0], gmap, sig_vars), self.conv(args[1], gmap, sig_vars, const_ok=True, want_const=True)])
        if name in gmap:
            g = gmap[name]
            if isinstance(g, TVar): return g
            isc = getattr(g, "is_const", False) or (isinstance(g, TRigid) and g.name in self._cg_rigid_names())
            if isc and not const_ok:
                raise TypeErr(f"类型错误: {name} 是常量泛型参数，不能当类型用", ln)
            if want_const and not isc:
                raise TypeErr(f"类型错误: 数组长度 {name} 必须是整数或常量泛型参数", ln)
            return g
        if want_const:
            raise TypeErr(f"类型错误: 数组长度 {name} 必须是整数或常量泛型参数（const {name}: Int）", ln)
        if name == "_" and not args:
            return TVar()
        if "." in name and name.split(".", 1)[0] in gmap and "." not in name.split(".", 1)[1]:
            head, item = name.split(".", 1)
            base = gmap[head]; pb = prune(base)
            bs = getattr(pb, "bounds", None)
            if head == "Self" and "Self" in gmap and getattr(self, "_cur_trait", None):
                bs = [self._cur_trait]
            if bs is not None and self.assoc_owner(list(bs), item) is None:
                raise TypeErr(f"类型错误: {head} 的约束里没有关联类型 {item}", ln)
            return TCon("@" + item, [base])
        if sig_vars and len(name) == 1 and name.islower():
            return TGen(name)
        if name in PRIMS:
            return PRIMS[name]
        if name == "Self" and "Self" in gmap:
            return gmap["Self"]
        if name in self.aliases:
            gens, target = self.aliases[name]
            if args and len(args) != len(gens):
                raise TypeErr(f"类型错误: 类型别名 {name} 需要 {len(gens)} 个类型参数，给了 {len(args)} 个", ln)
            sub = dict(gmap)
            tb = self.type_bounds.get(name, {})
            for g, a in zip(gens, args):
                at = self.conv(a, gmap, sig_vars); sub[g] = at
                for b in tb.get(g, []):
                    # 别名上的约束：type Sorted<T: Ord> = …，Sorted<Foo> 要求 Foo: Ord
                    pt = prune(at)
                    if isinstance(pt, (TRigid, TGen)) and not getattr(pt, "is_const", False):
                        if b not in self.closure(pt.bounds or []):
                            raise TypeErr(f"类型错误: {name}<{show_t(pt)}> 要求 {pt.name}: {b}（给 {pt.name} 加上约束 {b}）", ln)
                    elif not isinstance(pt, (TGen, TVar)) and not gen_objs(pt):
                        self.constraints.append((pt, ("impl", b), ln))
            return self.conv(target, sub, sig_vars)
        cg = self.type_cgens.get(name)
        if cg is not None and (name in self.structs or name in self.enums):
            gl = (self.structs.get(name) or self.enums.get(name))[0]
            for g, a in zip(gl, args):
                if g in cg and a[0] == "con" and a[1][:1] != "#" and a[1] not in gmap:
                    raise TypeErr(f"类型错误: {name} 的常量泛型参数 {g} 需要整数，实际是 {a[1]}", ln)
                if g not in cg and a[0] == "con" and a[1][:1] == "#":
                    raise TypeErr(f"类型错误: {name} 的泛型参数 {g} 需要类型，实际是常量 {a[1][1:]}", ln)
            cargs = [self.conv(a, gmap, sig_vars, const_ok=g in cg, want_const=g in cg) for g, a in zip(gl, args)] \
                + [self.conv(a, gmap, sig_vars) for a in args[len(gl):]]
        else:
            cargs = [self.conv(a, gmap, sig_vars) for a in args]
        if name in BUILTIN_GENERIC:
            n = BUILTIN_GENERIC[name]
            if len(cargs) != n:
                raise TypeErr(f"类型错误: {name} 需要 {n} 个类型参数，给了 {len(cargs)} 个", ln)
            return TCon(name, cargs)
        if name in self.structs or name in self.enums:
            n = len((self.structs.get(name) or self.enums.get(name))[0])
            if len(cargs) not in (0, n):
                raise TypeErr(f"类型错误: {name} 需要 {n} 个类型参数，给了 {len(cargs)} 个", ln)
            if not cargs and n:
                cargs = [TVar() for _ in range(n)]
            return TCon(name, cargs)
        raise TypeErr(f"类型错误: 未知类型 {name}", ln)

    def variant_ctor_type(self, en, vn):
        gens, variants = self.enums[en]
        tb = self.type_bounds.get(en, {}); cg = self.type_cgens.get(en, ())
        gm = {g: TGen(g, tb.get(g, []), g in cg) for g in gens}
        res = TCon(en, [gm[g] for g in gens])
        fs = variants[vn]
        if not fs:
            return res
        return fn_t([self.conv(t, gm) for _, t in fs], res)

    # 合一
    def unify(self, expected, actual, line, ctx=""):
        a, b = prune(expected), prune(actual)
        if a is b: return
        if isinstance(a, TCon) and a.name in ("Any", "Never"): return
        if isinstance(b, TCon) and b.name in ("Any", "Never"): return
        if isinstance(a, TVar) or isinstance(b, TVar):
            v, t = (a, b) if isinstance(a, TVar) else (b, a)
            if occurs(v, t):
                raise TypeErr(f"类型错误: 无限类型 {show_t(v)} = {show_t(t)}", line)
            v.ref = t
            return
        if is_proj(a) or is_proj(b):
            if is_proj(a) and is_proj(b) and a.name == b.name and prune(a.args[0]) is prune(b.args[0]): return
            self.deferred.append((expected, actual, line, ctx))   # 投影还没法规约：等类型确定后再合一
            return
        if isinstance(a, TDyn) and isinstance(b, TDyn) and a.name != b.name and set(self.closure(b.bounds)) >= set(a.bounds):
            return   # dyn 向上转换：dyn B → dyn A（A 是 B 的超 trait 或其子集）
        if isinstance(a, TDyn) and not (isinstance(b, TDyn) and set(self.closure(b.bounds)) >= set(a.bounds)):
            # 隐式转换为 dyn：实际类型必须实现全部 trait
            if isinstance(b, TGen): self._mismatch(expected, actual, line, ctx)
            for tr in a.bounds:
                if not self.implements(b, tr):
                    raise TypeErr(f"类型错误: {ctx + '：' if ctx else ''}{show_t(b)} 没有实现 {tr}，不能当作 {a.name}", line)
            return
        if isinstance(a, TGen) or isinstance(b, TGen):
            if isinstance(a, TGen) and isinstance(b, TGen) and a.name == b.name: return
            self._mismatch(expected, actual, line, ctx)
        if a.name == "UnsafeFn" and b.name == "fn" and not isinstance(b, TRigid):
            # 普通函数可以当 unsafe fn 用（反过来不行）
            try: self.unify(a.args[0], b, line, ctx)
            except TypeErr: self._mismatch(expected, actual, line, ctx)
            return
        if a.name == "fn" and b.name == "UnsafeFn" and not isinstance(a, TRigid):
            raise TypeErr(f"类型错误: {ctx + '：' if ctx else ''}期望 {show_t(a)}，实际是 {show_t(b)}（unsafe fn 不能当普通函数用）", line)
        if a.name == "List" and b.name == "Array" and len(a.args) == 1 and len(b.args) == 2 \
                and not isinstance(a, TRigid) and not isinstance(b, TRigid):
            # 定长数组可以当作列表用（运行时就是列表）；反过来不行，长度未知
            try: self.unify(a.args[0], b.args[0], line, ctx)
            except TypeErr: self._mismatch(expected, actual, line, ctx)
            return
        if a.name == "Array" and b.name == "List" and not isinstance(a, TRigid) and not isinstance(b, TRigid):
            raise TypeErr(f"类型错误: {ctx + '：' if ctx else ''}期望 {show_t(a)}，实际是 {show_t(b)}"
                          "（列表的长度编译期未知：直接传数组字面量，或给变量标注 [T; n]）", line)
        if a.name != b.name or len(a.args) != len(b.args):
            self._mismatch(expected, actual, line, ctx)
        try:
            for x, y in zip(a.args, b.args):
                self.unify(x, y, line, ctx)
        except TypeErr:
            self._mismatch(expected, actual, line, ctx)

    def _mismatch(self, e, a, line, ctx):
        names = {}
        msg = f"类型错误: {ctx + '：' if ctx else ''}期望 {show_t(e, names)}，实际是 {show_t(a, names)}"
        raise TypeErr(msg, line)

    def head(self, t):
        t = prune(t)
        if isinstance(t, TCon) and t.name == "Array" and len(t.args) == 2 and not isinstance(t, TRigid): return "List"
        return t.name if isinstance(t, TCon) else None

    # 入口
    def rec(self, name, t, line, kind):
        """记录局部绑定（供 LSP 悬停显示类型）；预导入里的不记录"""
        if self._in_prelude or not line or name == "_": return
        self.locals.append((line, name, t, kind))

    def check(self, items):
        from kite import with_prelude
        items = with_prelude(items)
        self.locals = []
        self._in_prelude = False
        self.errors = []
        self.constraints = []
        self.ord_sites = []
        self.const_lits = {}
        self.declare(items, self.g, top=True)
        bodies = []
        for it in items:
            if it.kind == "Fn":
                bodies.append(("fn", it, self.g))
            elif it.kind == "Impl":
                bodies.append(("impl", it, self.g))
        # 先检查顶层语句（可能定义全局 let），再检查函数体
        for it in items:
            if it.kind not in DECL_KINDS:
                self.guard(lambda it=it: self.stmt(it, self.g))
        for kind, it, sc in bodies:
            if kind == "fn":
                self.guard(lambda it=it, sc=sc: self.check_fn_body(it, sc))
            else:
                self.check_impl_bodies(it, sc)
        self.solve_constraints()
        self.solve_ord_sites()
        self.solve_cg_sites()
        if self.warnings and not getattr(self, "quiet", False):
            import sys
            from kite import format_error, KiteError
            for ln, msg in sorted(self.warnings):
                print(format_error(KiteError(msg, ln)), file=sys.stderr)
        return self.errors

    def guard(self, f):
        try:
            f()
        except TypeErr as e:
            self.errors.append(e)
        except KiteError as e:
            self.errors.append(e)

    def _local(self, name, sc):
        s = sc
        while s is not None and s is not self.g:
            if name in s.vars: return True
            s = s.parent
        return False

    def solve_constraints(self):
        for _ in range(4):
            todo, self.deferred = self.deferred, []
            for e, a, line, ctx in todo:
                try: self.unify(e, a, line, ctx)
                except TypeErr as err: self.errors.append(err)
            if not self.deferred or len(self.deferred) == len(todo): break
        for e, a, line, ctx in self.deferred:
            self.errors.append(TypeErr(f"类型错误: {ctx + '：' if ctx else ''}无法确定 {show_t(e)} 和 {show_t(a)} 是否一致，请加类型标注", line))
        self.deferred = []
        for t, kind, line in self.constraints:
            t = prune(t)
            if isinstance(kind, tuple) and kind[0] == "variant":
                _, vn, alts = kind
                if isinstance(t, TCon) and t.name in alts: continue
                en = alts[-1]
                gens = self.enums[en][0]
                try: self.unify(t, TCon(en, [TVar() for _ in gens]), line, f"变体 {vn}")
                except TypeErr as err: self.errors.append(err)
                continue
            if isinstance(kind, tuple):  # ("impl", trait)
                if not self.implements(t, kind[1]):
                    hint = f"（给泛型参数加约束 {show_t(t)}: {kind[1]}）" if isinstance(t, TRigid) else f"（需要 impl {kind[1]} for {show_t(t)}）"
                    self.errors.append(TypeErr(f"类型错误: {show_t(t)} 没有实现 {kind[1]}{hint}", line))
                continue
            if isinstance(t, TRigid):
                need = {"num": "Num", "add": "Num", "ord": "Ord"}[kind]
                if not self.has_bound(t, need):
                    what = {"num": "数值运算", "add": "+ 运算", "ord": "大小比较"}[kind]
                    alt = {"add": "Add", "ord": "Cmp"}.get(kind, "Sub/Mul/Div/Rem/Neg")
                    self.errors.append(TypeErr(f"类型错误: 泛型 {t.name} 不能做 {what}，请加约束 {t.name}: {need}（或运算符 trait {t.name}: {alt}）", line))
                continue
            if isinstance(t, TVar):
                t.ref = INT  # 数值默认 Int
                t = INT
            if isinstance(t, TCon) and t.name in ("Any", "Never"):
                continue
            ok = {
                "num": ("Int", "Float"),
                "add": ("Int", "Float", "Str", "List"),
                "ord": ("Int", "Float", "Str"),
            }[kind]
            name = t.name if isinstance(t, TCon) else None
            if name not in ok:
                what = {"num": "数值运算", "add": "+ 运算", "ord": "大小比较"}[kind]
                self.errors.append(TypeErr(f"类型错误: {what}不支持类型 {show_t(t)}", line))
        self.constraints = []

    # 声明
    def declare(self, items, scope, top=False):
        for it in items:
            if it.kind in ("Struct", "Enum", "Alias") and getattr(it, "bounds", None):
                self.type_bounds[it.name] = it.bounds
            if it.kind in ("Struct", "Enum") and getattr(it, "cgens", None):
                self.type_cgens[it.name] = set(it.cgens)
            if it.kind == "Struct":
                self.structs[it.name] = (it.generics, it.fields)
            elif it.kind == "Enum":
                self.enums[it.name] = (it.generics, {vn: fs for vn, fs in it.variants})
            elif it.kind == "Alias":
                self.aliases[it.name] = (it.generics, it.target)
            elif it.kind == "Trait":
                self.traits[it.name] = it.methods
                self.supers[it.name] = list(getattr(it, "supers", []) or [])
                self.trait_assoc[it.name] = {an: (bs, d) for an, bs, d in getattr(it, "assoc", [])}
        for it in items:
            try:
                if it.kind in ("Struct", "Enum", "Alias"):
                    for g, bs in (getattr(it, "bounds", None) or {}).items():
                        for b in bs:
                            if b not in BUILTIN_TRAITS and b not in self.traits:
                                raise TypeErr(f"类型错误: 未知 trait {b}", it.line)
                if it.kind == "Struct":
                    gm = {g: TGen(g, self.type_bounds.get(it.name, {}).get(g, []), g in (it.cgens or ())) for g in it.generics}
                    for _, ft in it.fields:
                        self.conv(ft, gm)
                    st = TCon(it.name, [gm[g] for g in it.generics])
                    scope.define(it.name, fn_t([self.conv(ft, gm) for _, ft in it.fields], st), scheme=True)
                elif it.kind == "Enum":
                    for vn, fs in it.variants:
                        self.variant_owner[vn] = it.name
                        alts = self.variant_alts.setdefault(vn, [])
                        if it.name not in alts: alts.append(it.name)
                        scope.define(vn, self.variant_ctor_type(it.name, vn), scheme=True)
                elif it.kind == "Fn":
                    it._sig = self.fn_sig(it, {})
                    if it.generics:
                        cga = self.sig_cgens.get(id(it._sig))
                        it._sig = TCon(it._sig.name, it._sig.args)     # 每个函数一个独立对象，按 id 记泛型参数顺序
                        if cga: self.sig_cgens[id(it._sig)] = cga; self._cg_keep.append(it._sig)
                        self.fn_gens[id(it._sig)] = (it._sig, list(it.generics))
                    scope.define(it.name, it._sig, scheme=True)
                    if getattr(it, "extern", None) is not None:
                        descs = []
                        asts = [p[1] for p in it.params] + [it.ret]
                        for (pn, pt), pa in zip(list(zip([p[0] for p in it.params], it._sig.args[:-1])) + [("返回值", it._sig.args[-1])], asts):
                            h = narrow_c(pa) or self.head(prune(pt))
                            d = h if h in EXT_PRIMS or h in NARROW_C or (pn == "返回值" and h == "Unit") else self.c_struct_desc(prune(pt))
                            if d is None and pn != "返回值": d = self.c_callback_desc(prune(pt))
                            if d is None:
                                raise TypeErr(f"类型错误: extern fn {it.name} 的{'参数 ' + pn + ' ' if pn != '返回值' else '返回值'}"
                                              f"不能是 {show_t(prune(pt))}（只支持 Int、Float、Bool、Str、Ptr{'、Unit' if pn == '返回值' else ''}，"
                                              f"或字段都是 Int/Float/Bool/Ptr 的结构体{'' if pn == '返回值' else '，或参数和返回值都是 Int/Float/Bool/Ptr 的 fn 回调'}）", it.line)
                            descs.append(d)
                        it.ext_tys = (descs[:-1], descs[-1])
                        self.externs[id(it._sig)] = ("extern 函数", it.name)
                        if getattr(it, "varargs", False):
                            self.va_sigs[id(it._sig)] = it; self._cg_keep.append(it._sig)
                    elif getattr(it, "is_unsafe", False):
                        self.externs[id(it._sig)] = ("unsafe 函数", it.name)
                    for an, aargs, _ in getattr(it, "attrs", []):
                        if an == "deprecated":
                            msg = next((v for k2, v in aargs if isinstance(v, str)), None)
                            self.deprecated[id(it._sig)] = (it.name, msg)
                elif it.kind == "Use":
                    self.use(it, scope)
            except TypeErr as e:
                self.errors.append(e)
        for it in items:
            if it.kind == "Impl":
                self.guard(lambda it=it: self.declare_impl(it))
        self.check_supers(items)

    def check_supers(self, items):
        """超 trait 必须存在、不能成环；impl B for X 要求 X 也实现了 B 的每个超 trait"""
        for it in items:
            if it.kind != "Trait": continue
            for sb in self.supers.get(it.name, []):
                if sb not in self.traits and sb not in BUILTIN_TRAITS:
                    self.errors.append(TypeErr(f"类型错误: trait {it.name} 的超 trait {sb} 不存在", it.line))
            seen, stack = set(), list(self.supers.get(it.name, []))
            while stack:
                b = stack.pop()
                if b == it.name:
                    self.errors.append(TypeErr(f"类型错误: trait {it.name} 的超 trait 构成了循环", it.line)); break
                if b in seen: continue
                seen.add(b); stack.extend(self.supers.get(b, []))
        for it in items:
            if it.kind != "Impl" or not it.trait: continue
            for sb in self.closure([it.trait])[1:]:
                if sb in BUILTIN_TRAITS and (sb == "Show" or it.target in BUILTIN_IMPLS.get(sb, ())):
                    continue
                if (it.target, sb) not in self.impls:
                    self.errors.append(TypeErr(f"类型错误: impl {it.trait} for {it.target} 要求 {it.target} 也实现超 trait {sb}", it.line))

    def use(self, it, scope):
        p = it.path
        if p[0] != "std" or len(p) < 2:
            return
        def mod(name):
            if name not in STD_SIGS:
                raise TypeErr(f"类型错误: 未知标准库模块 std.{name}", it.line)
            return name
        from kite import std_use_entries
        for e in std_use_entries(it):
            m = mod(e[1])
            if e[0] == "mod":
                scope.define(e[2], TCon("Module:" + m))
            elif e[0] == "all":
                for nm, sig in STD_SIGS[m].items():
                    bt = self.builtin_type(sig); scope.define(nm, bt, scheme=True)
                    if (m, nm) in UNSAFE_STD: self.externs[id(bt)] = ("unsafe 函数", f"{m}.{nm}"); self._cg_keep.append(bt)
            else:
                if e[2] not in STD_SIGS[m]:
                    raise TypeErr(f"类型错误: std.{m} 没有 {e[2]}", it.line)
                bt = self.builtin_type(STD_SIGS[m][e[2]]); scope.define(e[3], bt, scheme=True)
                if (m, e[2]) in UNSAFE_STD: self.externs[id(bt)] = ("unsafe 函数", f"{m}.{e[2]}"); self._cg_keep.append(bt)

    def va_call(self, e, sig, sc):
        """调用可变参数 extern：固定参数照常检查；多出来的参数按实际类型（Int/Float/Bool/Str/Ptr）
        生成一个变体名 f__va_<类型…>，引擎为每个变体生成带 … 原型的 C 调用"""
        fn = self.va_sigs[id(sig)]
        self._va_ok = True
        try: self.lookup(e.fn.name, sc, e.line)      # unsafe 检查
        finally: self._va_ok = False
        fixed = sig.args[:-1]
        if len(e.args) < len(fixed):
            raise TypeErr(f"类型错误: {fn.name} 至少需要 {len(fixed)} 个参数，给了 {len(e.args)} 个", e.line)
        for a, pt in zip(e.args, fixed):
            self.unify(pt, self.expr(a, sc, pt), a.line, f"{fn.name} 的参数")
        extra = []
        for a in e.args[len(fixed):]:
            at = prune(self.expr(a, sc)); h = self.head(at)
            if h not in EXT_PRIMS:
                raise TypeErr(f"类型错误: {fn.name} 的可变参数不能是 {show_t(at)}（只支持 Int、Float、Bool、Str、Ptr）", a.line)
            extra.append(h)
        key = f"{e.fn.name}__va_{'_'.join(extra)}"
        vs = fn.__dict__.setdefault("_va_variants", {})
        vs[key] = tuple(extra)
        e.fn._va_base = e.fn.name; e.fn.name = key
        return sig.args[-1]

    def c_callback_desc(self, t):
        """extern 参数里的 C 回调：fn(Int/Float/Bool/Ptr…) -> Int/Float/Bool/Ptr/Unit → ("F", 参数描述, 返回描述)"""
        if self.head(t) != "fn": return None
        ps, r = [self.head(prune(a)) for a in t.args[:-1]], self.head(prune(t.args[-1]))
        cb = ("Int", "Float", "Bool", "Ptr")
        if all(p in cb for p in ps) and (r in cb or r == "Unit"):
            return ("F", tuple(ps), r)
        return None

    def c_struct_desc(self, t, depth=0):
        """可以按值传给 C 的结构体：非泛型，字段都是 Int/Float/Bool/Ptr 或这样的结构体 → ("S", 名字, ((字段, 描述), …))"""
        h = self.head(t)
        if h not in self.structs or depth > 8: return None
        gens, fields = self.structs[h]
        if gens: return None
        out = []
        for fname, fa in fields:
            ft = prune(self.conv(fa, {}))
            fh = narrow_c(fa) or self.head(ft)
            d = fh if fh in ("Int", "Float", "Bool", "Ptr") or fh in NARROW_C else self.c_struct_desc(ft, depth + 1)
            if d is None: return None
            out.append((fname, d))
        return ("S", h, tuple(out)) if out else None

    def fn_sig(self, fn, gm, self_t=None):
        gm = dict(gm)
        bounds = getattr(fn, "bounds", {}) or {}
        for g in fn.generics:
            for b in bounds.get(g, []):
                if b not in BUILTIN_TRAITS and b not in self.traits:
                    raise TypeErr(f"类型错误: 未知 trait {b}", fn.line)
            gm[g] = TGen(g, bounds.get(g, []), g in (getattr(fn, "cgens", None) or ()))
        ps = []
        for name, ta in fn.params:
            if name == "self" and ta is None:
                if self_t is None:
                    raise TypeErr("类型错误: self 只能出现在 impl 方法里", fn.line)
                ps.append(self_t)
            else:
                ps.append(self.conv(ta, gm))
        ret = self.conv(fn.ret, gm) if fn.ret is not None else (UNIT if getattr(fn, "extern", None) is not None else TVar())
        sig = fn_t(ps, ret)
        own = list(getattr(fn, "cgens", None) or ())
        outer = [] if gm.get("#nocg") else [n for n, g in gm.items() if isinstance(g, TGen) and g.is_const and n not in own]
        cga = outer + own
        if not gm.get("#nocg"):
            # 泛型参数上的关联常量 T.N：每个 (T, 约束 trait 的常量 N) 一个隐藏参数，由调用点按 T 的实际类型传入
            import kite as _k
            for n, g in gm.items():
                if not isinstance(g, TGen) or g.is_const or not g.bounds: continue
                for tr in self.closure(g.bounds):
                    for c in _k.TRAIT_CONSTS.get(tr, []):
                        hn = f"__tc_{n}_{c[0]}"
                        if hn not in cga and not _mentions_self(c[1]):
                            cga.append(hn); self.tc_info[hn] = (n, c[0], c[1])
        if cga:
            if getattr(fn, "_cgens_all", None) not in (None, cga) and fn not in self.cg_fns:
                pass
            fn._cgens_all = cga
            self.sig_cgens[id(sig)] = cga; self._cg_keep.append(sig)
            if fn not in self.cg_fns: self.cg_fns.append(fn)
        return sig

    def declare_impl(self, it):
        ib = getattr(it, "bounds", {}) or {}      # impl<T: Ord> / impl<T> … where T: Ord
        for g in it.generics:
            for bnd in ib.get(g, []):
                if bnd not in BUILTIN_TRAITS and bnd not in self.traits:
                    raise TypeErr(f"类型错误: 未知 trait {bnd}", it.line)
        ib = {g: list(bs) for g, bs in ib.items()}
        tb = self.type_bounds.get(it.target, {})
        if tb and it.target_t[0] == "con":
            # 隐含约束：type Box<T: Show> 的 impl<T> Box<T> 里 T 自动带 Show
            tgens = (self.structs.get(it.target) or self.enums.get(it.target) or ([], None))[0]
            for tg, arg in zip(tgens, it.target_t[2]):
                if arg[0] == "con" and arg[1] in it.generics and not arg[2]:
                    for b in tb.get(tg, []):
                        if b not in ib.setdefault(arg[1], []): ib[arg[1]].append(b)
            it.bounds = ib
        icg = getattr(it, "cgens", None) or ()
        gm = {g: TGen(g, ib.get(g, []), g in icg) for g in it.generics}
        if it.trait and icg: gm["#nocg"] = True     # trait impl 的方法不接收隐藏的常量参数（动态分派时传不了）
        target = it.target
        if target not in self.structs and target not in self.enums and target not in PRIMS and target not in BUILTIN_GENERIC:
            raise TypeErr(f"类型错误: impl 的目标类型 {target} 未定义", it.line)
        self_t = self.conv(it.target_t, gm)
        gm["Self"] = self_t
        it._self_t, it._gm = self_t, gm
        tbl = self.methods.setdefault(target, {})
        methods = list(it.methods)
        given = dict(getattr(it, "assoc", []))
        if given and not it.trait:
            raise TypeErr(f"类型错误: 关联类型只能写在 impl Trait for 类型 里（{', '.join(given)}）", it.line)
        if it.trait:
            decl = self.trait_assoc.get(it.trait, {})
            for an in given:
                if an not in decl:
                    raise TypeErr(f"类型错误: trait {it.trait} 没有关联类型 {an}", it.line)
            for an, (bs, dflt) in decl.items():
                ta = given.get(an, dflt)
                if ta is None:
                    raise TypeErr(f"类型错误: impl {it.trait} for {target} 缺少关联类型 {an}（写 type {an} = …）", it.line)
                self.impl_assoc[(target, an)] = (it, ta)
                at = self.conv(ta, gm)
                for b in bs:
                    self.constraints.append((at, ("impl", b), it.line))
            self.impls.add((target, it.trait))
            if it.trait in BUILTIN_TRAITS and it.trait not in self.traits:
                pass
            elif it.trait not in self.traits:
                raise TypeErr(f"类型错误: 未知 trait {it.trait}", it.line)
            own = {m.name for m in it.methods}
            for m in self.traits.get(it.trait, []):
                if m.name not in own:
                    if m.body is None:
                        if getattr(m, "const_name", None):
                            raise TypeErr(f"类型错误: impl {it.trait} for {target} 缺少关联常量 {m.const_name}（写 const {m.const_name}: … = …）", it.line)
                        raise TypeErr(f"类型错误: impl {it.trait} for {target} 缺少方法 {m.name}", it.line)
                    methods.append(m)
        it._all_methods = methods
        tsigs = {}
        if it.trait in self.traits:
            for m in self.traits[it.trait]:
                tsigs[m.name] = m
        for m in methods:
            m_sig = self.fn_sig(m, gm, self_t)
            tm = tsigs.get(m.name)
            if tm is not None and tm is not m:
                self.guard(lambda m=m, tm=tm, m_sig=m_sig: self.check_impl_sig(it, m, m_sig, self.fn_sig(tm, dict(gm), self_t)))
            tbl[m.name] = (m_sig, m.params and m.params[0][0] == "self")
            if getattr(m, "is_unsafe", False): self.unsafe_methods.add((target, m.name))
            else: self.unsafe_methods.discard((target, m.name))
            if not hasattr(m, "_sigs"): m._sigs = {}
            m._sigs[target] = m_sig

    def check_impl_sig(self, it, m, isig, tsig):
        """impl 里的方法必须和 trait 声明的签名一致（泛型参数允许改名）。"""
        def rigid(t):
            return subst(t, {n: TRigid(n, g.bounds) for n, g in gen_objs(t).items()})
        is_self = lambda f: bool(f.params) and f.params[0][0] == "self"
        where = f"impl {it.trait} for {it.target} 的" + (f"关联常量 {m.const_name}" if getattr(m, "const_name", None) else f"方法 {m.name}")
        if is_self(m) != is_self(self.traits_m(it.trait, m.name)):
            raise TypeErr(f"类型错误: {where} 和 trait 声明不一致：self 参数{'多了' if is_self(m) else '少了'}", m.line)
        tu = bool(getattr(self.traits_m(it.trait, m.name), "is_unsafe", False))
        if bool(getattr(m, "is_unsafe", False)) != tu:
            raise TypeErr(f"类型错误: {where} 和 trait 声明不一致：{'trait 里是 unsafe fn' if tu else 'trait 里不是 unsafe fn'}", m.line)
        na, nb = len(isig.args) - 1, len(tsig.args) - 1
        if na != nb:
            raise TypeErr(f"类型错误: {where} 有 {na} 个参数，trait 声明的是 {nb} 个", m.line)
        try:
            self.unify(instantiate(tsig), rigid(isig), m.line)
            self.unify(rigid(tsig), instantiate(isig), m.line)
        except TypeErr:
            if getattr(m, "const_name", None):
                raise TypeErr(f"类型错误: {where} 的类型和 trait 声明不一致：期望 {show_t(tsig.args[-1])}，实际是 {show_t(isig.args[-1])}", m.line) from None
            raise TypeErr(f"类型错误: {where} 的签名和 trait 声明不一致：期望 {show_t(tsig)}，实际是 {show_t(isig)}", m.line) from None

    def traits_m(self, trait, name):
        return next(x for x in self.traits[trait] if x.name == name)

    def check_impl_bodies(self, it, scope):
        own = {id(x) for x in it.methods}
        for m in getattr(it, "_all_methods", []):
            if m.body is not None:
                self.guard(lambda m=m: self.check_fn_body(m, scope, sig=m._sigs[it.target]))
                if id(m) not in own:
                    # trait 默认方法的语法树由多个 impl 共用，_t 会被下一个 impl 覆盖：按目标类型留一份快照给 KIR
                    if not hasattr(m, "_tsnap"): m._tsnap = {}
                    m._tsnap[it.target] = snapshot_types(m.body)

    def check_fn_body(self, fn, scope, sig=None):
        if fn.body is None:
            return
        sig = sig or getattr(fn, "_sig", None)
        if sig is None:
            return  # 签名本身有错，已报告
        rigid = {n: TRigid(n, g.bounds) for n, g in gen_objs(sig).items()}
        ft = subst(sig, rigid)
        sc = Scope(scope)
        cga = self.sig_cgens.get(id(sig)) or []
        for n in cga:
            if n in self.tc_info:
                rigid.setdefault(n, TRigid(n)); sc.define(n, self.conv(self.tc_info[n][2], {}))
            else:
                rigid.setdefault(n, TRigid(n)); sc.define(n, INT)
        self.cg_ctx.append({n: rigid[n] for n in cga})
        self.rigid_stack = getattr(self, "rigid_stack", []) + [rigid]
        saved_pre, self._in_prelude = self._in_prelude, bool(getattr(fn, "prelude", False))
        for (name, _), pt in zip(fn.params, ft.args[:-1]):
            sc.define(name, pt); self.rec(name, pt, fn.line, "param")
        self.ret_stack.append(ft.args[-1])
        unsafe_fn = bool(getattr(fn, "is_unsafe", False))
        self.unsafe_depth += unsafe_fn          # unsafe fn 的函数体本身就是 unsafe 上下文
        try:
            if getattr(fn, "is_async", False) and fn.name != "main" and self.head(prune(ft.args[-1])) == "Future":
                # async fn：直接按内层类型 T 检查 Future { run: () => body } 里的闭包，报错更直观
                lam = fn.body.stmts[0].expr.fields[0][1]
                lt = self.expr(lam, sc, fn_t([], prune(ft.args[-1]).args[0]))
                self.unify(prune(ft.args[-1]).args[0], lt.args[-1], fn.line, f"async fn {fn.name} 的返回值")
                fn.body.stmts[0].expr._t = ft.args[-1]; fn.body._t = ft.args[-1]
                return
            bt = self.block(fn.body, sc, ft.args[-1])
            if not self.diverges(fn.body):
                self.unify(ft.args[-1], bt, self.last_line(fn.body), f"函数 {fn.name} 的返回值")
        finally:
            self.ret_stack.pop()
            self.cg_ctx.pop(); self.rigid_stack.pop()
            self.unsafe_depth -= unsafe_fn
            self._in_prelude = saved_pre

    def _body_gmap(self):
        """函数体里的类型标注（let x: List<T>、as T）可以用外层函数的泛型参数"""
        g = {}
        for r in getattr(self, "rigid_stack", []): g.update(r)
        return g

    def last_line(self, block):
        return block.stmts[-1].line if block.stmts else block.line

    def diverges(self, block):
        return bool(block.stmts) and block.stmts[-1].kind in ("Return", "Break", "Continue")

    # 语句
    def block(self, b, scope, expected=None):
        sc = Scope(scope)
        local = [s for s in b.stmts if s.kind in DECL_KINDS]
        if local:
            self.declare(local, sc)
            for s in local:
                if s.kind == "Fn":
                    self.check_fn_body(s, sc)
                elif s.kind == "Impl":
                    self.check_impl_bodies(s, sc)
        t = UNIT
        for i, s in enumerate(b.stmts):
            if s.kind in DECL_KINDS:
                t = UNIT; continue
            try:
                if expected is not None and i == len(b.stmts) - 1 and s.kind == "ExprStmt":
                    t = self.expr(s.expr, sc, expected)     # 期望类型传给尾表达式（dyn 隐式转换要用）
                else:
                    t = self.stmt(s, sc)
                    if s.kind == "ExprStmt" and i < len(b.stmts) - 1 and self.head(prune(t)) == "Future" and not self._in_prelude:
                        w = (s.line, "警告: Future 没有被 .await，不会执行")
                        if w not in self.warnings: self.warnings.append(w)
            except KiteError as err:
                # 错误恢复：记录后继续检查后续语句；let 失败时把变量绑成 Any 避免连锁报错
                self.errors.append(err)
                if s.kind == "Let":
                    for nm in self.pat_names(s.pat):
                        if sc.find(nm) is None or nm not in sc.vars:
                            sc.define(nm, ANY, s.mutable)
                t = ANY
        return t

    def pat_names(self, p):
        k = p[0]
        if k == "bind": return [p[1]]
        if k == "tuple": return [n for x in p[1] for n in self.pat_names(x)]
        if k == "list": return [n for x in p[1] for n in self.pat_names(x)] + ([p[2]] if p[2] and p[2] != "_" else []) + \
            [n for x in (p[3] if len(p) > 3 else []) for n in self.pat_names(x)]
        if k == "variant" and p[2]: return [n for x in p[2] for n in self.pat_names(x)]
        if k == "struct": return [n for _, x in p[2] for n in self.pat_names(x)]
        if k == "or": return self.pat_names(p[1][0])
        if k == "at": return self.pat_names(p[1]) + self.pat_names(p[2])
        return []

    def stmt(self, s, sc):
        k = s.kind
        if k == "ExprStmt":
            return self.expr(s.expr, sc)
        if k == "Let":
            ann = self.conv(s.ty, self._body_gmap()) if s.ty is not None else None
            vt = self.expr(s.value, sc, ann)
            if ann is not None:
                self.unify(ann, vt, s.line, "let 的类型标注")
                if isinstance(prune(ann), TDyn): vt = ann
            s.pat = self.pattern(s.pat, vt, sc, s.mutable, s.line)
            if getattr(s, "is_const", False):
                lv = const_literal(s.value)
                if lv is None: self.const_lits.pop(s.pat[1], None)
                else: self.const_lits[s.pat[1]] = lv
            return UNIT
        if k == "Assign":
            self.assign(s, sc); return UNIT
        if k == "Return":
            if not self.ret_stack:
                raise TypeErr("类型错误: return 只能出现在函数里", s.line)
            if self.ret_stack[-1] is SPAWN_RET:
                raise TypeErr("类型错误: spawn 任务里不能用 return（任务的值就是它的表达式）", s.line)
            vt = UNIT if s.value is None else self.expr(s.value, sc, self.ret_stack[-1])
            self.unify(self.ret_stack[-1], vt, s.line, "return 的值")
            return NEVER
        if k in ("Break", "Continue"):
            if not self.loop_depth:
                raise TypeErr(f"类型错误: {k.lower()} 只能出现在循环里", s.line)
            lab = getattr(s, "label", None)
            fr = next((f for f in reversed(self.labels) if lab is None or f[0] == lab), None)
            if fr is None:
                raise TypeErr(f"类型错误: 找不到循环标签 '{lab}", s.line)
            val = getattr(s, "value", None)
            if k == "Break":
                if fr[1] is None:
                    if val is not None:
                        raise TypeErr("类型错误: 只有 loop 的 break 能带值（while / for 的值总是 ()）", s.line)
                    fr[2] = True
                else:
                    vt = UNIT if val is None else self.expr(val, sc)
                    fr[2] = True
                    self.unify(fr[1], vt, val.line if val is not None else s.line, "loop 的 break 值")
            return NEVER
        if k == "While":
            if s.cond is not None:
                self.unify(BOOL, self.expr(s.cond, sc), s.line, "while 条件")
            fr = [getattr(s, "label", None), None, False]
            self.loop_depth += 1; self.labels.append(fr)
            try: self.block(s.body, sc)
            finally: self.loop_depth -= 1; self.labels.pop()
            # 语句位置的 loop（cond 为空）里没有 break 时永不结束，类型是 Never（可以作函数的尾语句）
            return NEVER if s.cond is None and not fr[2] else UNIT
        if k == "For":
            it = prune(self.expr(s.iter, sc))
            h = self.head(it)
            if h == "List": el = it.args[0]
            elif h == "Map": el = TCon("Tuple", [it.args[0], it.args[1]])
            elif h == "Set": el = it.args[0]
            elif h == "Str": el = STR
            elif h == "Chan": el = it.args[0]
            elif isinstance(it, TVar):
                el = TVar(); self.unify(list_t(el), it, s.line)
            else:
                raise TypeErr(f"类型错误: {show_t(it)} 不可迭代", s.line)
            inner = Scope(sc)
            s.pat = self.pattern(s.pat, el, inner, False, s.line)
            self.loop_depth += 1; self.labels.append([getattr(s, "label", None), None, False])
            try: self.block(s.body, inner)
            finally: self.loop_depth -= 1; self.labels.pop()
            return UNIT
        if k == "Defer":
            if s.body.kind == "Block": self.block(s.body, sc)
            else: self.expr(s.body, sc)
            return UNIT
        if k in DECL_KINDS:
            return UNIT
        raise TypeErr(f"类型错误: 未知语句 {k}", s.line)

    def assign(self, s, sc):
        t = s.target
        root = t
        while root.kind in ("Field", "Index"):
            root = root.obj
        if root.kind != "Name":
            raise TypeErr("类型错误: 赋值目标无效", s.line)
        ent = sc.find(root.name)
        if ent is None:
            raise TypeErr(f"类型错误: 未定义的变量 {root.name}", s.line)
        if not ent[1]:
            raise TypeErr(f"类型错误: 不能修改不可变绑定 `{root.name}`（改用 var 声明）", s.line)
        s_ = sc
        while s_ is not None and root.name not in s_.vars:
            if s_.task:
                raise TypeErr(f"类型错误: spawn 任务不能修改外层变量 `{root.name}`（数据竞争）", s.line)
            s_ = s_.parent
        tt = self.expr(t, sc)
        if s.op in ("|=", "&=", "^=", "<<=", ">>=") or (s.op == "-=" and self.head(tt) == "Set") or \
                s.op != "=" and s.op[0] in OP_METHODS and (self.user_type(tt) or self.op_bound(tt, s.op[0])):
            # a += b 在用户类型上改写为 a = a + b（目标表达式会再求值一次）
            s.value = Node("Bin", s.line, op=s.op[:-1], left=s.target, right=s.value)
            s.op = "="
        vt = self.expr(s.value, sc)
        self.unify(tt, vt, s.line, "赋值")
        if s.op != "=":
            self.constraints.append((tt, "add" if s.op == "+=" else "num", s.line))

    # 表达式
    def lookup(self, name, sc, line):
        ent = sc.find(name)
        if ent is None:
            if name in STD_SIGS:
                raise TypeErr(f"类型错误: 未定义的名字 {name}（需要 use std.{name}）", line)
            if name in self.structs or name in self.enums:
                raise TypeErr(f"类型错误: {name} 是类型，不是值", line)
            raise TypeErr(f"类型错误: 未定义的名字 {name}", line)
        t, _, scheme = ent
        if isinstance(t, tuple):
            return t
        if scheme and id(t) in self.va_sigs and not getattr(self, "_va_ok", False):
            raise TypeErr(f"类型错误: 可变参数的 extern fn {name} 只能直接调用，不能当作值使用", line)
        if scheme and id(t) in self.externs and not self.unsafe_depth:
            kind, nm = self.externs[id(t)]
            raise TypeErr(f"类型错误: 使用 {kind} {nm} 需要放在 unsafe {{ … }} 里", line)
        if scheme and id(t) in self.deprecated and not self._in_prelude:
            nm, msg = self.deprecated[id(t)]
            w = (line, f"警告: {nm} 已弃用" + (f"：{msg}" if msg else ""))
            if w not in self.warnings: self.warnings.append(w)
        return self.inst(t, line) if scheme else t

    def expr(self, e, sc, expected=None):
        t = self._expr(e, sc, expected)
        e._t = t          # 供 KIR 使用：每个表达式节点的类型
        return t

    def _expr(self, e, sc, expected=None):
        k = e.kind
        if k == "Lit":
            v = e.value
            if isinstance(v, bool): return BOOL
            if isinstance(v, int):
                if not -(1 << 63) <= v < (1 << 63):
                    raise TypeErr(f"类型错误: 整数常量 {v} 超出 64 位范围", e.line)
                return INT
            if isinstance(v, float): return FLOAT
            return STR
        if k == "Name":
            alts = self.variant_alts.get(e.name)
            if alts and len(alts) > 1 and not self._local(e.name, sc) and \
                    all(self.enums[a][1].get(e.name) is None for a in alts):
                v = TVar()   # 同名的无字段变体：类型留到约束求解时按用法决定，没有线索就取最后定义的
                self.constraints.append((v, ("variant", e.name, alts), e.line))
                return v
            self._pending_cg = None
            ent = sc.find(e.name)
            if ent is not None and ent[2] and not isinstance(ent[0], tuple) and self.externs.get(id(ent[0]), ("",))[0] == "unsafe 函数":
                # 取 unsafe 函数的值是安全的，得到 unsafe fn 类型；调用它时才需要 unsafe
                saved, self.unsafe_depth = self.unsafe_depth, self.unsafe_depth + 1
                try: t = self.lookup(e.name, sc, e.line)
                finally: self.unsafe_depth = saved
                return TCon("UnsafeFn", [t])
            t = self.lookup(e.name, sc, e.line)
            if self._pending_cg:
                self._pending_cg = None
                raise TypeErr(f"类型错误: {e.name} 带常量泛型参数，不能当值传递，请用 lambda 包一层", e.line)
            if isinstance(t, tuple):
                if t[1] == VARIADIC:
                    return fn_t([ANY], UNIT)
                raise TypeErr(f"类型错误: 内建函数 {e.name} 有多个重载，不能当值传递，请用 lambda 包一层", e.line)
            return t
        if k == "TypeApp":
            f = e.fn
            if f.kind != "Name":
                raise TypeErr("类型错误: 显式泛型实参 .<…> 只能跟在泛型函数名后面", e.line)
            ent = sc.find(f.name)
            if ent is None or isinstance(ent[0], tuple) or not ent[2] or id(ent[0]) not in self.fn_gens:
                if ent is None: self.lookup(f.name, sc, e.line)
                raise TypeErr(f"类型错误: {f.name} 不是泛型函数，不能写 .<…>", e.line)
            t, gens = ent[0], self.fn_gens[id(ent[0])][1]
            if len(gens) != len(e.targs):
                raise TypeErr(f"类型错误: {f.name} 有 {len(gens)} 个泛型参数（{', '.join(gens)}），给了 {len(e.targs)} 个", e.line)
            self.lookup(f.name, sc, e.line)      # 弃用警告等
            objs, m = gen_objs(t), {}
            cga = self.sig_cgens.get(id(t)) or []
            for g, ta in zip(gens, e.targs):
                rg = {k: v for k, v in (self.rigid_stack[-1] if getattr(self, "rigid_stack", None) else {}).items() if not k.startswith("__tc_")}
                m[g] = TVar() if ta is None else self.conv(ta, rg, const_ok=g in cga, want_const=g in cga)
            for name, g in objs.items():
                if name not in m: m[name] = TVar()
                for b in g.bounds:
                    self.constraints.append((m[name], ("impl", b), e.line))
            res = subst(t, m)
            f._t = res
            if cga:
                self._pending_cg = self.cg_vals(cga, m)
                self.take_cg(self._cg_node, f.name, e.line)
            e.__dict__.clear(); e.__dict__.update(f.__dict__)   # 运行时就是普通的函数名
            return res
        if k == "Interp":
            for p in e.parts:
                if not isinstance(p, str): self.expr(p, sc)
            return STR
        if k == "Bin":
            return self.binop(e, sc)
        if k == "Unary":
            o = e.operand
            if e.op == "-" and o.kind == "Lit" and type(o.value) is int and o.value == 1 << 63:
                e.__dict__.clear(); e.__dict__.update(Node("Lit", o.line, value=-(1 << 63)).__dict__)
                return INT   # -9223372036854775808 是合法常量
            t = self.expr(e.operand, sc)
            if e.op == "~" or e.op == "!" and self.head(t) == "Int":
                if self.head(t) != "Int":
                    if isinstance(prune(t), TVar): self.unify(INT, t, e.line, "~ 的操作数")
                    else: raise TypeErr(f"类型错误: ~ 是 Int 的按位取反，不能用于 {show_t(t)}（逻辑非请写 !）", e.line)
                if e.op == "!":
                    mig = getattr(self, "migrate_bnot", None)
                    if mig is None:
                        raise TypeErr("类型错误: ! 只用于 Bool；Int 的按位取反请写 ~x（kite fmt --migrate 可自动改写）", e.line)
                    if getattr(e, "pos", None) is not None: mig.append(e.pos)
                call = Node("Call", e.line, fn=Node("Name", e.line, name="__bnot"), args=[e.operand])
                e.__dict__.clear(); e.__dict__.update(call.__dict__)
                return self.expr(e, sc)
            if e.op == "!":
                self.unify(BOOL, t, e.line, "! 的操作数"); return BOOL
            if e.op == "-" and (self.user_type(t) or self.op_bound(t, "neg")):
                return self.overload(e, "neg", t, sc)
            self.constraints.append((t, "num", e.line)); return t
        if k == "Tuple":
            return TCon("Tuple", [self.expr(x, sc) for x in e.items]) if e.items else UNIT
        if k == "List":
            pe = prune(expected) if expected is not None else None
            if isinstance(pe, TCon) and pe.name == "Array" and len(pe.args) == 2 and not isinstance(pe, TRigid):
                el = TVar()
                pel = prune(pe.args[0])
                if isinstance(pel, TDyn): el = pel
                for x in e.items:
                    self.unify(el, self.expr(x, sc, el), x.line, "数组元素")
                return TCon("Array", [el, TCon(f"#{len(e.items)}")])
            dyn_el = pe.args[0] if isinstance(pe, TCon) and pe.name == "List" and pe.args and isinstance(prune(pe.args[0]), TDyn) else None
            el = prune(dyn_el) if dyn_el is not None else TVar()
            for x in e.items:
                self.unify(el, self.expr(x, sc, el), x.line, "列表元素")
            return list_t(el)
        if k == "Cast":
            # as：Int、Float、Bool 之间的数值转换，类型检查后改写成普通调用；同类型直接去掉
            src = prune(self.expr(e.value, sc)); dst = prune(self.conv(e.ty, self._body_gmap()))
            sn, dn = self.head(src), self.head(dst)
            ln = e.line
            if isinstance(src, TVar):
                raise TypeErr("类型错误: 无法推断 as 左边的类型，请加类型标注", ln)
            if sn == dn and sn in ("Int", "Float", "Bool"):
                rep = e.value
            elif (sn, dn) == ("Int", "Float"):
                rep = Node("Call", ln, fn=Node("Name", ln, name="float"), args=[e.value])
            elif (sn, dn) == ("Float", "Int"):
                rep = Node("Call", ln, fn=Node("Name", ln, name="int"), args=[e.value])
            elif sn == "Bool" and dn in ("Int", "Float"):
                one, zero = (1, 0) if dn == "Int" else (1.0, 0.0)
                blk = lambda v: Node("Block", ln, stmts=[Node("ExprStmt", ln, expr=Node("Lit", ln, value=v))])
                rep = Node("If", ln, cond=e.value, then=blk(one), els=blk(zero))
            else:
                raise TypeErr(f"类型错误: 不能把 {show_t(src)} 转换为 {show_t(dst)}（as 只支持 Int、Float、Bool 之间的数值转换）", ln)
            e.__dict__.clear(); e.__dict__.update(rep.__dict__)
            return self.expr(e, sc)
        if k == "Block":
            if getattr(e, "unsafe", False):
                self.unsafe_depth += 1
                try: return self.block(e, sc, expected)
                finally: self.unsafe_depth -= 1
            return self.block(e, sc, expected)
        if k == "Loop":
            fr = [e.label, TVar(), False]
            self.loop_depth += 1; self.labels.append(fr)
            try: self.block(e.body, sc)
            finally: self.loop_depth -= 1; self.labels.pop()
            return fr[1] if fr[2] else NEVER
        if k == "If":
            self.unify(BOOL, self.expr(e.cond, sc), e.line, "if 条件")
            dy = expected if expected is not None and isinstance(prune(expected), TDyn) else None
            tt = self.block(e.then, sc, expected)
            if e.els is None:
                return UNIT
            et = self.expr(e.els, sc, expected)
            if dy is not None:          # 两个分支分别转换成 dyn
                self.unify(dy, tt, e.line, "if 分支"); self.unify(dy, et, e.line, "else 分支")
                return dy
            return self.join(tt, et, e.line, "if 两个分支")
        if k == "Match":
            return self.match(e, sc, expected)
        if k == "Is":
            e.pat = self.pattern(e.pat, self.expr(e.value, sc), Scope(sc), False, e.line)
            return BOOL
        if k == "Lambda":
            return self.lam(e, sc, expected)
        if k == "Call":
            if e.fn.kind == "Name" and e.fn.name == "__await" and len(e.args) == 1:
                at = prune(self.expr(e.args[0], sc))
                if self.head(at) not in ("Future", None) and not isinstance(at, TVar):
                    raise TypeErr(f"类型错误: 只能 .await 一个 Future（async fn 的调用结果），这里是 {show_t(at)}", e.line)
                return self.call(e.fn, [("typed", at, e.args[0].line)], sc, e.line)
            if e.fn.kind == "Name" and e.fn.name == "__repeat" and len(e.args) == 2:
                cnt, L = e.args[1], None
                ctx = self.cg_ctx[-1] if self.cg_ctx else {}
                pe = prune(expected) if expected is not None else None
                if cnt.kind == "Name" and cnt.name in ctx: L = ctx[cnt.name]
                elif cnt.kind == "Lit" and isinstance(cnt.value, int) and not isinstance(cnt.value, bool) \
                        and isinstance(pe, TCon) and pe.name == "Array": L = TCon(f"#{cnt.value}")
                rt = self.call(e.fn, list(e.args), sc, e.line)
                return TCon("Array", [prune(rt).args[0], L]) if L is not None else rt
            if e.fn.kind == "Name":
                if hasattr(e.fn, "_va_base"): e.fn.name = e.fn._va_base
                ent = sc.find(e.fn.name)
                if ent is not None and not isinstance(ent[0], tuple) and id(ent[0]) in self.va_sigs:
                    return self.va_call(e, ent[0], sc)
            self._cg_node = e
            return self.call(e.fn, list(e.args), sc, e.line)
        if k == "Pipe":
            left = ("typed", self.expr(e.left, sc), e.left.line)
            r = e.right
            if r.kind == "Call":
                self._cg_node = r
                return self.call(r.fn, [left] + list(r.args), sc, e.line)
            return self.call(r, [left], sc, e.line)
        if k == "Field":
            if e.obj.kind == "Name" and self.cg_ctx and f"__tc_{e.obj.name}_{e.name}" in self.cg_ctx[-1] \
                    and sc.find(e.obj.name) is None:
                e.__dict__.update(kind="Name", name=f"__tc_{e.obj.name}_{e.name}"); del e.__dict__["obj"]
                return self.expr(e, sc, expected)
            if e.obj.kind == "Name" and getattr(self, "rigid_stack", None) and e.obj.name in self.rigid_stack[-1] \
                    and not e.obj.name.startswith("__tc_") and sc.find(e.obj.name) is None:
                raise TypeErr(f"类型错误: 类型参数 {e.obj.name} 没有关联常量 {e.name}（需要约束 {e.obj.name}: 某个声明了 const {e.name} 的 trait）", e.line)
            return self.field(e, sc)
        if k == "TupleIndex":
            t = prune(self.expr(e.obj, sc))
            if self.head(t) == "Tuple":
                if e.index >= len(t.args):
                    raise TypeErr(f"类型错误: 元组只有 {len(t.args)} 个元素", e.line)
                return t.args[e.index]
            raise TypeErr(f"类型错误: {show_t(t)} 不能用 .{e.index} 取元素（需要明确的元组类型）", e.line)
        if k == "Index":
            t = prune(self.expr(e.obj, sc)); it = self.expr(e.index, sc)
            h = self.head(t)
            if h == "List":
                self.unify(INT, it, e.line, "下标"); return t.args[0]
            if h == "Map":
                self.unify(t.args[0], it, e.line, "Map 的键"); return t.args[1]
            if h == "Str":
                self.unify(INT, it, e.line, "下标"); return STR
            if isinstance(t, TVar):
                el = TVar(); self.unify(list_t(el), t, e.line); self.unify(INT, it, e.line); return el
            raise TypeErr(f"类型错误: {show_t(t)} 不支持下标", e.line)
        if k == "Try":
            t = prune(self.expr(e.value, sc))
            if not self.ret_stack:
                raise TypeErr("类型错误: ? 只能用在函数里", e.line)
            if self.ret_stack[-1] is SPAWN_RET:
                raise TypeErr("类型错误: spawn 任务里不能用 ?（任务不能替外层函数返回）", e.line)
            ret = self.ret_stack[-1]
            h = self.head(t)
            if h == "Result":
                self.unify(ret, res_t(TVar(), t.args[1]), e.line, "? 要求函数返回同错误类型的 Result")
                return t.args[0]
            if h == "Option":
                self.unify(ret, opt_t(TVar()), e.line, "? 要求函数返回 Option")
                return t.args[0]
            raise TypeErr(f"类型错误: ? 只能用于 Result 或 Option，实际是 {show_t(t)}", e.line)
        if k == "StructLit":
            q = getattr(e.name, "qual", None)
            if q and q in self.enums and e.name not in (self.enums[q][1] or {}):
                raise TypeErr(f"类型错误: {q} 没有变体 {e.name}", e.line)
            if (q or e.name not in self.structs) and e.name in self.variant_owner:
                # 记录变体构造 Rect { h: 2.0, w: 1.0 } / Shape.Rect { … } → 按声明顺序改写成 Rect(1.0, 2.0)
                en = self.variant_owner[e.name]
                pt = prune(expected) if expected is not None else None
                if q and q in self.enums and e.name in (self.enums[q][1] or {}): en = q
                elif q: raise TypeErr(f"类型错误: {q} 没有变体 {e.name}", e.line)
                elif isinstance(pt, TCon) and pt.name in self.enums and e.name in (self.enums[pt.name][1] or {}): en = pt.name
                fs = self.enums[en][1][e.name] or []
                vn = f"{en}.{e.name}"
                if not fs or fs[0][0].startswith("_"):
                    raise TypeErr(f"类型错误: {vn} 不是记录变体，请写 {e.name}(…)", e.line)
                if getattr(e, "base", None) is not None:
                    raise TypeErr(f"类型错误: 记录变体 {vn} 不支持 ..base 更新", e.line)
                given = dict(e.fields)
                for f, _ in e.fields:
                    if f not in dict(fs): raise TypeErr(f"类型错误: {vn} 没有字段 {f}", e.line)
                missing = [f for f, _ in fs if f not in given]
                if missing:
                    raise TypeErr(f"类型错误: {vn} 缺少字段 {', '.join(missing)}", e.line)
                ln = e.line
                fn = Node("Field", ln, obj=Node("Name", ln, name=en), name=e.name) if q else Node("Name", ln, name=e.name)
                call = Node("Call", ln, fn=fn, args=[given[f] for f, _ in fs])
                e.__dict__.clear(); e.__dict__.update(call.__dict__)
                return self.expr(e, sc, expected)
            if e.name not in self.structs:
                raise TypeErr(f"类型错误: 未知结构体 {e.name}", e.line)
            gens, fields = self.structs[e.name]
            gm = {g: TVar() for g in gens}
            for g, bs in self.type_bounds.get(e.name, {}).items():
                for b in bs: self.constraints.append((gm[g], ("impl", b), e.line))
            decl = {f: self.conv(ft, gm) for f, ft in fields}
            given = {f for f, _ in e.fields}
            missing = [f for f in decl if f not in given]
            base = getattr(e, "base", None)
            if base is not None:
                # 结构体更新：先检查 base 的类型，再把缺的字段补成 base.f；base 不是变量名时先绑到临时变量
                st = TCon(e.name, [gm[g] for g in gens])
                self.unify(st, self.expr(base, sc, st), base.line, f"..base（应为 {e.name}）")
                for f, x in e.fields:
                    if f not in decl:
                        raise TypeErr(f"类型错误: {e.name} 没有字段 {f}", e.line)
                    self.unify(decl[f], self.expr(x, sc, decl[f]), x.line, f"字段 {f}")
                ln = e.line
                if base.kind == "Name":
                    src = base; pre = []
                else:
                    self._upd_n = getattr(self, "_upd_n", 0) + 1
                    tmp = f"__upd{self._upd_n}"
                    src = Node("Name", ln, name=tmp)
                    pre = [Node("Let", ln, pat=("bind", tmp), mutable=False, ty=None, value=base)]
                lit = Node("StructLit", ln, name=e.name, base=None,
                           fields=list(e.fields) + [(f, Node("Field", ln, obj=src, name=f)) for f in missing])
                if pre:
                    blk = Node("Block", ln, stmts=pre + [Node("ExprStmt", ln, expr=lit)])
                    e.__dict__.clear(); e.__dict__.update(blk.__dict__)
                    return self.expr(e, sc)
                e.fields, e.base = lit.fields, None
                for f in missing:
                    self.expr(dict(e.fields)[f], sc)
                return st
            if missing:
                raise TypeErr(f"类型错误: {e.name} 缺少字段 {', '.join(missing)}", e.line)
            for f, x in e.fields:
                if f not in decl:
                    raise TypeErr(f"类型错误: {e.name} 没有字段 {f}", e.line)
                self.unify(decl[f], self.expr(x, sc, decl[f]), x.line, f"字段 {f}")
            return TCon(e.name, [gm[g] for g in gens])
        if k == "Select":
            result = None
            for kind, a, b, pat, guard, body in e.arms:
                arm = Scope(sc)
                if guard is not None:
                    self.unify(BOOL, self.expr(guard, sc), e.line, "select 守卫")
                if kind in ("recv", "send"):
                    el = TVar()
                    self.unify(TCon("Chan", [el]), self.expr(a, sc), e.line, f"{kind} 的通道")
                    if kind == "send":
                        self.unify(el, self.expr(b, sc), e.line, "send 的值")
                    elif pat is not None:
                        if not self.irrefutable(pat):
                            raise TypeErr("类型错误: select 的 recv 绑定必须是变量名或 _（值是 Option，在分支里再 match）", e.line)
                        self.pattern(pat, TCon("Option", [el]), arm, False, e.line)
                elif kind == "after":
                    self.unify(INT, self.expr(a, sc), e.line, "after 的毫秒数")
                bt = self.expr(body, arm)
                result = bt if result is None else self.join(result, bt, body.line, "select 各分支")
            return result
        if k in ("Spawn", "Race"):
            ts = []
            for x in e.items:
                inner = Scope(sc); inner.task = True
                self.ret_stack.append(SPAWN_RET)
                saved_loop, self.loop_depth, self.labels = (self.loop_depth, self.labels), 0, []
                try:
                    ts.append(self.expr(x, inner))
                finally:
                    self.ret_stack.pop(); self.loop_depth, self.labels = saved_loop
            if k == "Race":
                r = ts[0]
                for x, t in zip(e.items[1:], ts[1:]):
                    r = self.join(r, t, x.line, "race 各项")
                return r
            return TCon("Tuple", ts) if ts else UNIT
        if k == "Await":       # spawn { … }.await / race { … }.await：是 Future 的项在各自的任务里 await
            g = e.value
            self.expr(g, sc)
            ts = []
            for i, x in enumerate(g.items):
                t = prune(x._t)
                if self.head(t) == "Future":
                    ft = self.lookup("__await", sc, x.line)
                    self.unify(ft.args[0], t, x.line)
                    nm = Node("Name", x.line, name="__await"); nm._t = ft
                    c = Node("Call", x.line, fn=nm, args=[x]); c._t = ft.args[-1]
                    g.items[i] = c; t = ft.args[-1]
                ts.append(t)
            if g.kind == "Race":
                r = ts[0]
                for x, t in zip(g.items[1:], ts[1:]):
                    r = self.join(r, t, x.line, "race 各项")
            else:
                r = TCon("Tuple", ts) if ts else UNIT
            g._t = r
            e.__dict__.clear(); e.__dict__.update(g.__dict__)
            return r
        raise TypeErr(f"类型错误: 未知表达式 {k}", e.line)

    def join(self, a, b, line, ctx):
        pa, pb = prune(a), prune(b)
        if isinstance(pa, TCon) and pa.name == "Never": return b
        if isinstance(pb, TCon) and pb.name == "Never": return a
        self.unify(a, b, line, ctx)
        return a

    def binop(self, e, sc):
        op = e.op
        lt = self.expr(e.left, sc); rt = self.expr(e.right, sc)
        if op in ("&&", "||"):
            self.unify(BOOL, lt, e.line, f"{op} 左边"); self.unify(BOOL, rt, e.line, f"{op} 右边")
            return BOOL
        if op in ("..", "..="):
            self.unify(INT, lt, e.line, "区间起点"); self.unify(INT, rt, e.line, "区间终点")
            return list_t(INT)
        if op in ("^", "<<", ">>") or (op in ("|", "&") and "Int" in (self.head(lt), self.head(rt))):
            # Int 位运算：同样改写为内建调用
            self.unify(INT, lt, e.line, f"运算 {op} 左边"); self.unify(INT, rt, e.line, f"运算 {op} 右边")
            fn = {"|": "__bor", "&": "__band", "^": "__bxor", "<<": "__shl", ">>": "__shr"}[op]
            call = Node("Call", e.line, fn=Node("Name", e.line, name=fn), args=[e.left, e.right])
            e.__dict__.clear(); e.__dict__.update(call.__dict__)
            return self.expr(e, sc)
        if op in ("|", "&") or (op == "-" and "Set" in (self.head(lt), self.head(rt))):
            # 集合运算：a | b、a & b、a - b 改写为内建调用（三个引擎都只看到函数调用）
            el = TVar()
            self.unify(TCon("Set", [el]), lt, e.line, f"运算 {op} 左边")
            self.unify(TCon("Set", [el]), rt, e.line, f"运算 {op} 右边")
            fn = {"|": "__set_union", "&": "__set_intersect", "-": "__set_difference"}[op]
            call = Node("Call", e.line, fn=Node("Name", e.line, name=fn), args=[e.left, e.right])
            e.__dict__.clear(); e.__dict__.update(call.__dict__)
            return self.expr(e, sc)
        if op in OP_METHODS and (self.user_type(lt) or self.op_bound(lt, op)):
            return self.overload(e, op, lt, sc)
        self.unify(lt, rt, e.line, f"运算 {op} 两边类型必须相同")
        if op in ("==", "!="):
            return BOOL
        if op in ("<", ">", "<=", ">="):
            self.constraints.append((lt, "ord", e.line)); return BOOL
        self.constraints.append((lt, "add" if op == "+" else "num", e.line))
        return lt

    def user_type(self, t):
        h = self.head(t)
        return h is not None and not isinstance(prune(t), TRigid) and h not in ("Option", "Result") and \
            (h in self.structs or h in self.enums)

    def op_bound(self, t, op):
        """泛型参数带运算符 trait 约束（T: Add 等）时，运算改写为方法调用；T: Num / Ord 仍走内建运算。"""
        t = prune(t)
        if not isinstance(t, TRigid): return False
        tr = OP_TRAITS.get(op)
        if tr is None or not self.has_bound(t, tr): return False
        builtin = {"+": "Num", "-": "Num", "*": "Num", "/": "Num", "%": "Num", "neg": "Num",
                   "<": "Ord", ">": "Ord", "<=": "Ord", ">=": "Ord"}[op]
        return not self.has_bound(t, builtin)

    def overload(self, e, op, lt, sc):
        """运算符重载：把 a + b 改写为 a.add(b)，a < b 改写为 a.cmp(b) < 0，-a 改写为 a.neg()。"""
        meth = "neg" if op == "neg" else OP_METHODS[op]
        h = self.head(lt)
        if not isinstance(prune(lt), TRigid) and meth not in self.methods.get(h, {}):
            sym = "-（取负）" if op == "neg" else op
            raise TypeErr(f"类型错误: {show_t(lt)} 没有方法 {meth}，不能用运算符 {sym}（在 impl {h} 里定义 fn {meth}）", e.line)
        if op == "neg":
            call = Node("Call", e.line, fn=Node("Field", e.line, obj=e.operand, name=meth), args=[])
        else:
            call = Node("Call", e.line, fn=Node("Field", e.line, obj=e.left, name=meth), args=[e.right])
        if op in ("<", ">", "<=", ">="):
            e.left, e.right = call, Node("Lit", e.line, value=0)
            ct = self.expr(call, sc)
            self.unify(INT, ct, e.line, f"{h}.cmp 的返回值")
            return BOOL
        e.__dict__.clear(); e.__dict__.update(call.__dict__)
        return self.expr(e, sc)

    def lam(self, e, sc, expected):
        exp = prune(expected) if expected is not None else None
        if isinstance(exp, TCon) and exp.name == "UnsafeFn" and not isinstance(exp, TRigid): exp = prune(exp.args[0])
        n = len(e.params)
        if isinstance(exp, TCon) and exp.name == "fn" and len(exp.args) - 1 == n:
            pts, ret = list(exp.args[:-1]), exp.args[-1]
        else:
            pts, ret = [TVar() for _ in range(n)], TVar()
        inner = Scope(sc)
        for (name, ta), pt in zip(e.params, pts):
            if ta is not None:
                self.unify(self.conv(ta, {}), pt, e.line, f"lambda 参数 {name}")
            inner.define(name, pt); self.rec(name, pt, e.line, "param")
        self.ret_stack.append(ret)
        saved_loop, self.loop_depth, self.labels = (self.loop_depth, self.labels), 0, []
        try:
            bt = self.block(e.body, inner, ret) if e.body.kind == "Block" and not getattr(e.body, "unsafe", False) \
                else self.expr(e.body, inner, ret)      # (x) => unsafe { … } 的块要按 unsafe 块检查
            if not (e.body.kind == "Block" and self.diverges(e.body)):
                self.unify(ret, bt, self.last_line(e.body) if e.body.kind == "Block" else e.line, getattr(e, "ctx", "lambda 返回值"))
        finally:
            self.ret_stack.pop(); self.loop_depth, self.labels = saved_loop
        return fn_t(pts, ret)

    def field(self, e, sc):
        # 枚举变体: Shape.Circle
        if e.obj.kind == "Name" and sc.find(e.obj.name) is None and e.obj.name in self.enums:
            en = e.obj.name
            if e.name not in self.enums[en][1]:
                raise TypeErr(f"类型错误: {en} 没有变体 {e.name}", e.line)
            return self.inst(self.variant_ctor_type(en, e.name), e.line)
        t = prune(self.expr(e.obj, sc))
        h = self.head(t)
        if h and h.startswith("Module:"):
            m = h[7:]
            if e.name not in STD_SIGS[m]:
                raise TypeErr(f"类型错误: std.{m} 没有 {e.name}", e.line)
            bt = self.builtin_type(STD_SIGS[m][e.name])
            if (m, e.name) in UNSAFE_STD and not self.unsafe_depth:
                raise TypeErr(f"类型错误: 使用 unsafe 函数 {m}.{e.name} 需要放在 unsafe {{ … }} 里", e.line)
            if isinstance(bt, tuple):
                return fn_t([ANY], UNIT)  # 可变参数函数作为值时视为单参数
            return self.inst(bt, e.line)
        if h in self.structs:
            gens, fields = self.structs[h]
            gm = dict(zip(gens, t.args))
            for f, ft in fields:
                if f == e.name:
                    return self.conv(ft, gm)
            raise TypeErr(f"类型错误: {h} 没有字段 {e.name}", e.line)
        if isinstance(t, TVar):
            raise TypeErr(f"类型错误: 无法推断 .{e.name} 的接收者类型，请加类型标注", e.line)
        raise TypeErr(f"类型错误: {show_t(t)} 没有字段 {e.name}", e.line)

    # 调用
    def arg_type(self, a, sc, expected=None):
        if isinstance(a, tuple):
            return a[1]
        return self.expr(a, sc, expected)

    def arg_line(self, a, line):
        return a[2] if isinstance(a, tuple) else a.line

    def call(self, fnode, args, sc, line):
        cg_node, self._cg_node = self._cg_node, None
        # 方法 / 模块函数 / UFCS
        if fnode.kind == "Field":
            obj = fnode.obj
            if obj.kind == "Name" and obj.name.split(".")[-1][:1].isupper() and (obj.name in self.structs or obj.name in self.methods) \
                    and not (obj.name in self.enums and sc.find(obj.name) is None):
                tbl = self.methods.get(obj.name, {})
                if fnode.name in tbl and tbl[fnode.name][1]:
                    raise TypeErr(f"类型错误: {obj.name}.{fnode.name} 是实例方法（带 self），请用 值.{fnode.name}(...) 调用", line)
                raise TypeErr(f"类型错误: 类型 {obj.name} 没有静态方法 {fnode.name}", line)
            if not (obj.kind == "Name" and sc.find(obj.name) is None and obj.name in self.enums):
                rt = prune(self.expr(obj, sc))
                h = self.head(rt)
                if h and h.startswith("Module:"):
                    return self.apply(self.field(fnode, sc) if STD_SIGS[h[7:]].get(fnode.name) != VARIADIC else ("builtin", VARIADIC),
                                      args, sc, line, fnode.name)
                if isinstance(rt, TRigid):
                    for b in self.closure(rt.bounds):
                        for m in self.traits.get(b, []):
                            if m.name == fnode.name:
                                if getattr(m, "is_unsafe", False) and not self.unsafe_depth:
                                    raise TypeErr(f"类型错误: 使用 unsafe 方法 {fnode.name} 需要放在 unsafe {{ … }} 里", line)
                                sig = self.fn_sig(m, {"Self": rt}, rt)
                                has_self = bool(m.params) and m.params[0][0] == "self"
                                recv = [("typed", rt, line)] if has_self else []
                                return self.apply(sig, recv + args, sc, line, fnode.name)
                    raise TypeErr(f"类型错误: {'' if isinstance(rt, TDyn) else '泛型 '}{rt.name} 没有方法 {fnode.name}（约束: {', '.join(rt.bounds) or '无'}）", line)
                tbl = self.methods.get(h, {})
                if fnode.name in tbl:
                    sig, has_self = tbl[fnode.name]
                    if (h, fnode.name) in self.unsafe_methods and not self.unsafe_depth:
                        raise TypeErr(f"类型错误: 使用 unsafe 方法 {h}.{fnode.name} 需要放在 unsafe {{ … }} 里", line)
                    recv = [("typed", rt, line)] if has_self else []
                    self._pending_cg = None
                    isig = self.inst(sig, line); self.take_cg(cg_node, fnode.name, line)
                    return self.apply(isig, recv + args, sc, line, fnode.name)
                if h in self.structs and any(f == fnode.name for f, _ in self.structs[h][1]):
                    return self.apply(self.field(fnode, sc), args, sc, line, fnode.name)
                ent = sc.find(fnode.name)
                if ent is not None:
                    if isinstance(rt, TVar):
                        raise TypeErr(f"类型错误: 无法推断 .{fnode.name}() 的接收者类型，请加类型标注", line)
                    return self.apply(self.lookup(fnode.name, sc, line), [("typed", rt, line)] + args, sc, line, fnode.name)
                if isinstance(rt, TVar):
                    raise TypeErr(f"类型错误: 无法推断 .{fnode.name}() 的接收者类型，请加类型标注", line)
                raise TypeErr(f"类型错误: {show_t(rt)} 没有方法 {fnode.name}", line)
        self._pending_cg = None
        if fnode.kind == "TypeApp": self._cg_node = cg_node
        ft = self.expr(fnode, sc) if fnode.kind != "Name" else self.lookup(fnode.name, sc, line)
        self._cg_node = None
        name = fnode.name if fnode.kind == "Name" else "函数"
        if fnode.kind == "Name": self.take_cg(cg_node, fnode.name, line)
        rt = self.apply(ft, args, sc, line, name)
        if fnode.kind == "Name" and fnode.name in ("sort", "min", "max") \
                and sc.find(fnode.name) is self._ord_builtin.get(fnode.name):
            el = rt
            if fnode.name == "sort":
                p = prune(rt); el = p.args[0] if self.head(p) == "List" else None
            if el is not None:
                self.ord_sites.append((fnode, el, len(args), line))
        return rt

    def solve_ord_sites(self):
        """sort / min / max：元素是实现了 Cmp 的用户类型（或只有 Cmp 约束的泛型）时，改写成预导入的 __*_cmp；
        元素是没实现 Cmp 的结构体 / 枚举时报错"""
        for fnode, el, n, line in self.ord_sites:
            t = prune(el); name = fnode.name
            if isinstance(t, TRigid):
                if self.has_bound(t, "Ord"): continue
                if self.has_bound(t, "Cmp"):
                    fnode.name = "__sort_cmp" if name == "sort" else f"__{name}{'2' if n == 2 else ''}_cmp"
                    continue
                self.errors.append(TypeErr(f"类型错误: {name} 需要可比较的元素，请给泛型加约束 {t.name}: Ord 或 {t.name}: Cmp", line))
                continue
            h = self.head(t)
            if h is None or h in ("Option", "Result") or not (h in self.structs or h in self.enums): continue
            if (h, "Cmp") in self.impls:
                fnode.name = "__sort_cmp" if name == "sort" else f"__{name}{'2' if n == 2 else ''}_cmp"
            else:
                self.errors.append(TypeErr(f"类型错误: {name} 需要可比较的元素：{show_t(t)} 没有实现 Cmp（impl Cmp for {h}）", line))
        self.ord_sites = []

    def apply(self, ft, args, sc, line, name):
        if isinstance(ft, tuple):  # 内建：可变参数或重载
            sig = ft[1]
            if sig == VARIADIC:
                for a in args: self.arg_type(a, sc)
                return UNIT
            key = f"#{len(args)}"
            if key in sig:
                chosen = sig[key]
            else:
                if not args:
                    raise TypeErr(f"类型错误: {name} 参数个数不对", line)
                first = prune(self.arg_type(args[0], sc))
                args = [("typed", first, self.arg_line(args[0], line))] + args[1:]
                h = self.head(first)
                if h not in sig:
                    if isinstance(first, TVar):
                        raise TypeErr(f"类型错误: 无法推断 {name} 第一个参数的类型，请加类型标注", line)
                    if name == "__slice":
                        raise TypeErr(f"类型错误: {show_t(first)} 不支持切片（只有 List 和 Str 可以切片）", line)
                    raise TypeErr(f"类型错误: {name} 不支持 {show_t(first)}，可用于 {', '.join(k for k in sig)}", line)
                chosen = sig[h]
            ft = self.inst(self.builtin_type(chosen), line)
            res = self.apply(ft, args, sc, line, name)
            return res
        ft = prune(ft)
        if isinstance(ft, TCon) and ft.name == "UnsafeFn" and not isinstance(ft, TRigid):
            if not self.unsafe_depth:
                raise TypeErr(f"类型错误: 调用 unsafe 函数值 {name}（{show_t(ft)}）需要放在 unsafe {{ … }} 里", line)
            ft = prune(ft.args[0])
        if isinstance(ft, TVar):
            new = fn_t([TVar() for _ in args], TVar())
            self.unify(ft, new, line); ft = new
        if not (isinstance(ft, TCon) and ft.name == "fn"):
            raise TypeErr(f"类型错误: {show_t(ft)} 不能被调用", line)
        ps, ret = ft.args[:-1], ft.args[-1]
        if len(ps) != len(args):
            raise TypeErr(f"类型错误: {name} 需要 {len(ps)} 个参数，给了 {len(args)} 个", line)
        # 先处理非 lambda 参数，让 lambda 能拿到推断好的参数类型
        order = sorted(range(len(args)), key=lambda i: 1 if (not isinstance(args[i], tuple) and args[i].kind == "Lambda") else 0)
        for i in order:
            a = args[i]
            at = self.arg_type(a, sc, ps[i])
            self.unify(ps[i], at, self.arg_line(a, line), "切片的下标" if name == "__slice" else f"{name} 的第 {i + 1} 个参数")
        if name in NUMERIC_BUILTINS:
            self.constraints.append((ret, "num", line))
        return ret

    # match 与模式
    def match(self, e, sc, expected=None):
        els = getattr(e, "let_else", None)
        if els is not None:
            last = els.stmts[-1] if els.stmts else None
            ok = self.diverges(els) or (last is not None and last.kind == "ExprStmt" and last.expr.kind == "Call"
                                        and last.expr.fn.kind == "Name" and last.expr.fn.name in ("panic", "repanic"))
            if not ok:
                raise TypeErr("类型错误: let ... else 的 else 块必须跳出（以 return / break / continue / panic 结尾）", els.line)
        st = self.expr(e.subject, sc)
        result = None
        for ai, (pat, guard, body) in enumerate(e.arms):
            arm = Scope(sc)
            pat = self.pattern(pat, st, arm, False, e.line)
            e.arms[ai] = (pat, guard, body)
            if guard is not None:
                self.unify(BOOL, self.expr(guard, arm), e.line, "match 守卫")
            bt = self.expr(body, arm, expected)
            if expected is not None and isinstance(prune(expected), TDyn):
                self.unify(expected, bt, body.line, "match 分支"); bt = expected
            result = bt if result is None else self.join(result, bt, body.line, "match 各分支")
        self.exhaustive(e, st)
        return result if result is not None else NEVER

    def irrefutable(self, p):
        t = p[0]
        if t in ("wild", "bind"): return True
        if t == "tuple": return all(self.irrefutable(x) for x in p[1])
        if t == "struct": return p[1] in self.structs and all(self.irrefutable(x) for _, x in p[2])
        if t == "or": return any(self.irrefutable(x) for x in p[1])
        if t == "at": return self.irrefutable(p[2])
        return False

    def expand_or(self, p):
        """把模式里的或模式展开成若干个不含或的模式（穷尽性检查用）"""
        k = p[0]
        if k == "or": return [q for a in p[1] for q in self.expand_or(a)]
        def prod(parts):
            out = [[]]
            for ps in parts: out = [o + [x] for o in out for x in ps]
            return out
        if k == "tuple": return [("tuple", c) for c in prod([self.expand_or(x) for x in p[1]])]
        if k == "at": return self.expand_or(p[2])
        if k == "list":
            post = list(p[3]) if len(p) > 3 else []
            n = len(p[1])
            return [("list", c[:n], p[2], c[n:]) for c in prod([self.expand_or(x) for x in list(p[1]) + post])]
        if k == "variant" and p[2]: return [("variant", p[1], c) for c in prod([self.expand_or(x) for x in p[2]])]
        if k == "struct":
            fs = [f for f, _ in p[2]]
            return [("struct", p[1], list(zip(fs, c))) for c in prod([self.expand_or(x) for _, x in p[2]])]
        return [p]

    def exhaustive(self, e, st):
        arms = [q for p, g, _ in e.arms if g is None for q in self.expand_or(p)]
        if any(self.irrefutable(p) for p in arms):
            return
        t = prune(st)
        h = self.head(t)
        w = self.missing_case(arms, t)
        if w is None:
            return
        if h in self.enums:
            need = set(self.enums[h][1])
            have = set()
            for p in arms:
                if p[0] == "variant" and (p[2] is None or all(self.irrefutable(x) for x in p[2])):
                    have.add(p[1])
                elif p[0] == "struct" and p[1] in need and all(self.irrefutable(x) for _, x in p[2]):
                    have.add(p[1])
            missing = [v for v in self.enums[h][1] if v not in have]
            if missing and all(m not in have and not any(p[0] in ("variant", "struct") and p[1] == m for p in arms) for m in missing):
                raise TypeErr(f"类型错误: match 没有覆盖 {h} 的 {', '.join(missing)}", e.line)
        if h == "Bool" and w in ("true", "false"):
            raise TypeErr("类型错误: match 没有覆盖 Bool 的所有值", e.line)
        if w == "_":
            raise TypeErr(f"类型错误: match 不穷尽，{show_t(t)} 需要一个兜底分支 _", e.line)
        raise TypeErr(f"类型错误: match 不穷尽，没有覆盖 {w}", e.line)

    # ── 穷尽性：Maranget 有用性算法，返回一个未覆盖的例子（None 表示已穷尽）──
    def _norm(self, p, t):
        k = p[0]
        if k in ("wild", "bind"): return None
        if k == "lit":
            return (("b", p[1]) if isinstance(p[1], bool) else ("lit", p[1]), [])
        if k == "range": return (("lit", p), [])
        if k == "tuple": return (("t", len(p[1])), list(p[1]))
        if k == "variant": return (("v", str(p[1])), list(p[2] or []))
        if k == "struct":
            name = str(p[1]); fs = dict((f, x) for f, x in p[2])
            owner = self.variant_owner.get(name)
            if name not in self.structs and owner:
                vf = self.enums[owner][1].get(name) or []
                return (("v", name), [fs.get(f, ("wild",)) for f, _ in vf])
            sf = self.structs.get(name, (None, []))[1]
            return (("s", name), [fs.get(f, ("wild",)) for f, _ in sf])
        if k == "list":
            post = list(p[3]) if len(p) > 3 else []
            n = len(p[1]) + len(post)
            return ((("len", n) if not p[2] else ("rest", n, len(post))), list(p[1]) + post)
        return (("lit", repr(p)), [])

    def _universe(self, t, heads):
        """列的全部构造器 [(key, 子类型列表)]；无穷（Int/Str 字面量）或未知时返回 None"""
        t = prune(t); h = self.head(t)
        if h is None or isinstance(t, TVar):
            vs = [k[1] for k in heads if k[0] == "v"]
            if vs and vs[0] in self.variant_owner:
                h = self.variant_owner[vs[0]]
                t = TCon(h, [TVar() for _ in self.enums[h][0]])
            elif any(k[0] == "b" for k in heads):
                h, t = "Bool", BOOL
            elif any(k[0] == "t" for k in heads):
                n = next(k[1] for k in heads if k[0] == "t"); h, t = "Tuple", TCon("Tuple", [TVar() for _ in range(n)])
            elif any(k[0] in ("len", "rest") for k in heads):
                h, t = "List", list_t(TVar())
            else:
                return None
        args = list(getattr(t, "args", []) or [])
        if h in self.enums:
            gens, vs = self.enums[h]
            gm = {g: (args[i] if i < len(args) else TVar()) for i, g in enumerate(gens)}
            out = []
            for v, fs in vs.items():
                try: sub = [self.conv(ft, gm) for _, ft in (fs or [])]
                except Exception: sub = [TVar() for _ in (fs or [])]
                out.append((("v", v), sub))
            return out
        if h == "Bool": return [(("b", True), []), (("b", False), [])]
        if h == "Tuple": return [(("t", len(args)), args)]
        if h in self.structs:
            gens, fs = self.structs[h]
            gm = {g: (args[i] if i < len(args) else TVar()) for i, g in enumerate(gens)}
            try: sub = [self.conv(ft, gm) for _, ft in fs]
            except Exception: sub = [TVar() for _ in fs]
            return [(("s", h), sub)]
        if h == "List":
            el = args[0] if args else TVar()
            L = max([k[1] for k in heads if k[0] in ("len", "rest")] or [0])
            return [(("len", n), [el] * n) for n in range(L + 2)]
        return None

    def _specialize(self, rows, key, arity):
        out = []
        for r in rows:
            hd, rest = r[0], r[1:]
            if hd is None:
                out.append([None] * arity + rest); continue
            k, subs = hd
            if k == key:
                out.append(subs + rest)
            elif k[0] == "rest" and key[0] == "len" and key[1] >= k[1]:
                cut = len(subs) - k[2]
                out.append(subs[:cut] + [None] * (key[1] - k[1]) + subs[cut:] + rest)
        return out

    def _show_case(self, key, subs, top_list_L=None):
        if key[0] == "b": return "true" if key[1] else "false"
        if key[0] == "t": return "(" + ", ".join(subs) + ")"
        if key[0] == "v": return key[1] + (f"({', '.join(subs)})" if subs else "")
        if key[0] == "s": return key[1] + " { .. }"
        if key[0] == "len":
            if top_list_L is not None and key[1] == top_list_L + 1:
                return "[" + ", ".join(subs) + ", ..]"
            return "[" + ", ".join(subs) + "]"
        return "_"

    def _missing(self, rows, types, depth=0):
        if not types:
            return None if rows else []
        if depth > 60: return None
        def nrm(r):
            p = r[0]
            return [p if (p is None or isinstance(p, tuple) and len(p) == 2 and isinstance(p[0], tuple)) else self._norm(p, types[0])] + r[1:]
        rows = [nrm(r) for r in rows]
        heads = [r[0][0] for r in rows if r[0] is not None]
        uni = self._universe(types[0], heads) if heads else None
        if uni is None:
            d = [r[1:] for r in rows if r[0] is None]
            w = self._missing(d, types[1:], depth + 1)
            return None if w is None else ["_"] + w
        L = max([k[1] for k in heads if k[0] in ("len", "rest")] or [0]) if uni and uni[0][0][0] == "len" else None
        for key, sub in uni:
            n = len(sub)
            sp = self._specialize(rows, key, n)
            w = self._missing([[x if x is None or (isinstance(x, tuple) and len(x) == 2 and isinstance(x[0], tuple)) else x for x in r] for r in sp],
                              sub + types[1:], depth + 1)
            if w is not None:
                return [self._show_case(key, w[:n], L)] + w[n:]
        return None

    def missing_case(self, arms, t):
        try:
            w = self._missing([[p] for p in arms], [t])
        except RecursionError:
            return None
        return None if w is None else w[0]

    def pattern(self, p, t, sc, mutable, line):
        """检查模式并返回改写后的模式（展开元组 / 变体里的 ..，常量名换成字面量）"""
        names = self.pat_names(p)
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise TypeErr(f"类型错误: 模式里重复绑定了 {'、'.join(dup)}", line)
        return self._pattern(p, t, sc, mutable, line)

    def _expand_dots(self, subs, n, what, line):
        if not any(x[0] == "dots" for x in subs):
            return subs
        i = next(j for j, x in enumerate(subs) if x[0] == "dots")
        fixed = len(subs) - 1
        if fixed > n:
            raise TypeErr(f"类型错误: {what}只有 {n} 项，模式里写了 {fixed} 项", line)
        return subs[:i] + [("wild",)] * (n - fixed) + subs[i + 1:]

    def _pattern(self, p, t, sc, mutable, line):
        k = p[0]
        if k == "wild": return p
        if k == "or":
            names = sorted(set(self.pat_names(p[1][0])))
            tys = {n: TVar() for n in names}
            alts = []; muts = set()
            for a in p[1]:
                got = sorted(set(self.pat_names(a)))
                if got != names:
                    def fmt(ns): return "、".join(ns) if ns else "（无）"
                    raise TypeErr(f"类型错误: 或模式的每个分支必须绑定相同的变量：{fmt(names)} 与 {fmt(got)}", line)
                tmp = Scope(sc)
                alts.append(self._pattern(a, t, tmp, mutable, line))
                for n in names:
                    self.unify(tys[n], tmp.vars[n][0], line, f"或模式里的 {n}")
                    if tmp.vars[n][1]: muts.add(n)
            for n in names: sc.define(n, tys[n], mutable or n in muts)
            return ("or", alts)
        if k == "bind":
            m = mutable or len(p) > 2
            sc.define(p[1], t, m); self.rec(p[1], t, line, "var" if m else "let"); return p
        if k == "at":
            sub = self._pattern(p[2], t, sc, mutable, line)
            self._pattern(p[1], t, sc, mutable, line)
            return ("at", p[1], sub)
        if k == "lit":
            v = p[1]
            lt = BOOL if isinstance(v, bool) else INT if isinstance(v, int) else FLOAT if isinstance(v, float) else STR
            self.unify(t, lt, line, "模式"); return p
        if k == "range":
            lo, hi = p[1], p[2]
            def lt(v): return STR if isinstance(v, str) else FLOAT if isinstance(v, float) else INT
            if lo is not None and hi is not None and lt(lo) is not lt(hi):
                raise TypeErr("类型错误: 区间模式两端的类型不一致", line)
            if lo is not None and hi is not None and (hi < lo or hi == lo and not p[3]):
                raise TypeErr(f"类型错误: 区间模式 {lo!r}..{'=' if p[3] else ''}{hi!r} 是空的", line)
            self.unify(t, lt(lo if lo is not None else hi), line, "区间模式"); return p
        if k == "dots":
            raise TypeErr("类型错误: .. 只能出现在元组、变体或列表模式里", line)
        if k == "tuple":
            subs = p[1]
            if any(x[0] == "dots" for x in subs):
                pt = prune(t)
                if not (isinstance(pt, TCon) and pt.name == "Tuple"):
                    raise TypeErr("类型错误: 带 .. 的元组模式需要已知长度的元组", line)
                subs = self._expand_dots(subs, len(pt.args), "元组", line)
            ts = [TVar() for _ in subs]
            self.unify(t, TCon("Tuple", ts), line, "元组模式")
            return ("tuple", [self._pattern(sp, st, sc, mutable, line) for sp, st in zip(subs, ts)])
        if k == "list":
            el = TVar(); self.unify(t, list_t(el), line, "列表模式")
            pre = [self._pattern(sp, el, sc, mutable, line) for sp in p[1]]
            post = [self._pattern(sp, el, sc, mutable, line) for sp in (p[3] if len(p) > 3 else [])]
            if p[2] and p[2] != "_":
                sc.define(p[2], list_t(el), mutable); self.rec(p[2], list_t(el), line, "var" if mutable else "let")
            return ("list", pre, p[2], post) if post else ("list", pre, p[2])
        if k == "variant":
            name, subs = p[1], p[2]
            if name in self.variant_owner:
                en = self.variant_owner[name]
                # 同名变体：先看限定名（A.Other），再看期望类型，最后才用最后定义的那个
                q = getattr(name, "qual", None)
                pt = prune(t)
                if q and q in self.enums and name in (self.enums[q][1] or {}): en = q
                elif isinstance(pt, TCon) and pt.name in self.enums and name in (self.enums[pt.name][1] or {}): en = pt.name
                gens, variants = self.enums[en]
                gm = {g: TVar() for g in gens}
                self.unify(t, TCon(en, [gm[g] for g in gens]), line, f"模式 {name}")
                fs = variants[name] or []
                if subs is None: return p
                subs = self._expand_dots(subs, len(fs), f"变体 {name} ", line)
                if len(subs) != len(fs):
                    raise TypeErr(f"类型错误: 模式 {name} 需要 {len(fs)} 个字段，给了 {len(subs)} 个", line)
                return ("variant", name, [self._pattern(sp, self.conv(ft, gm), sc, mutable, line) for sp, (_, ft) in zip(subs, fs)])
            if subs is None and name in self.structs:
                gens = self.structs[name][0]
                self.unify(t, TCon(name, [TVar() for _ in gens]), line); return p
            if subs is None and name in self.const_lits and sc.find(name):
                return self._pattern(("lit", self.const_lits[name]), t, sc, mutable, line)
            if subs is None and name[:1].isupper() and sc.find(name):
                raise TypeErr(f"类型错误: 常量 {name} 的值不是字面量，不能用作模式（请改用守卫 if x == {name}）", line)
            raise TypeErr(f"类型错误: 未知的变体 {name}", line)
        if k == "struct":
            name, fs = p[1], p[2]
            if name in self.structs:
                gens, fields = self.structs[name]
                gm = {g: TVar() for g in gens}
                self.unify(t, TCon(name, [gm[g] for g in gens]), line, f"模式 {name}")
                decl = {f: ft for f, ft in fields}
            elif name in self.variant_owner:
                en = self.variant_owner[name]
                gens, variants = self.enums[en]
                gm = {g: TVar() for g in gens}
                self.unify(t, TCon(en, [gm[g] for g in gens]), line, f"模式 {name}")
                decl = {f: ft for f, ft in (variants[name] or [])}
            else:
                raise TypeErr(f"类型错误: 未知类型 {name}", line)
            out = []
            for f, sp in fs:
                if f not in decl:
                    raise TypeErr(f"类型错误: {name} 没有字段 {f}", line)
                out.append((f, self._pattern(sp, self.conv(decl[f], gm), sc, mutable, line)))
            return ("struct", name, out)
        raise TypeErr(f"类型错误: 未知模式 {k}", line)
