# Kite 1.00

- `grammar.ebnf`   语言文法
- `kitec/`         自举编译器（Kite 写成）：词法 → 语法 → 类型检查（类型化 IR）→ 字节码 / KIR → C
- `bootstrap/kitec.c` kitec 编译自己产出的 C 种子，`sh bootstrap.sh` 用它构建 bin/kitec
- `runtime/kite_rt.c` 原生运行时：值、字符串、集合、内建函数、标准库
- `std/`           标准库（Kite 写成）
- 格式化器、测试运行器、包管理、语言服务器都在 kitec 里：`kitec fmt` / `kitec test` / `kitec new|add|get|…` / `kitec lsp`
- `editors/vscode` VS Code 扩展（0.6 新增）
- `examples/`      fizzbuzz / shapes / results / generics / traits / closures / bench_fib
- `tests/`         必须被类型检查拒绝的反例
- `run_tests.sh`   一键回归测试
- `kite-spec.md`   生产级设计规格

## 构建与运行（不需要 Python）

编译器 kitec 用 Kite 写成并且自举；仓库里带一份它编译自己产出的 C（`bootstrap/kitec.c`，种子），只要有 C 编译器（cc / clang / gcc / zig 任一，或设置 `CC`）就能构建：

    sh bootstrap.sh                        # 种子 → bin/kitec → 用它从源码重编一次
    bin/kitec run examples/generics.kite   # 编译成临时可执行文件并运行（也可写 bin/kitec examples/generics.kite）
    bin/kitec build app.kite -o app        # 生成可执行文件（--emit-c app.c 保留 C 代码，--no-kir 关闭 KIR）
    bin/kitec check tests/bad.kite         # 只做类型检查
    bin/kitec dis examples/bench_fib.kite  # 查看字节码
    bin/kitec kir examples/kir.kite        # 查看 KIR
    sh run_tests.sh                        # 回归测试（不需要 Python）

- kitec 在 `$KITE_HOME`、可执行文件所在目录往上、当前目录往上依次寻找 `prelude.kite` 和 `runtime/`
- 改了 `kitec/src` 之后运行 `sh tools/kitec_bootstrap.sh --update-seed` 刷新种子；`run_tests.sh` 会检查种子 = stage1 = stage2
- `tests/expected/`：每个示例的标准输出；`tests/gold/*.md5`：kitec 各层输出（AST、检查结果、字节码、KIR、C）的基准，`sh tools/kitec_gold.sh --update` 重新生成
- 整个工具链不需要 Python：Python 参考实现（kite.py / typecheck.py / vm.py / lsp.py 等）已删除，最后一版在 git 标签 `python-ref`；REPL、树遍历解释器（`--tree`）和 `fmt --migrate` 随之废弃
- 下文各版本说明里的 `python3 kite.py …` 命令是历史记录，现在对应 `bin/kitec …`

## 原生编译（0.7）

    bin/kitec build app.kite             # 生成可执行文件 ./app
    bin/kitec build app.kite -o out/app  # 指定输出
    bin/kitec build app.kite --emit-c app.c  # 同时保留生成的 C 代码

- 流程：类型检查 → 字节码 → 每个函数生成一个 C 函数 → `cc -O2` → 独立可执行文件（约 80 KB，只依赖 libc）
- 栈深度在编译期已知，操作数栈变成固定下标的局部数组，跳转变成 goto，交给 C 编译器做寄存器分配
- Int / Float / Bool 不装箱；整数加减乘带溢出检查（溢出报错退出，不会悄悄回绕）
- 调用从未被重新赋值的顶层函数时生成直接 C 调用；方法分派生成 switch 表；每个模式生成专用匹配函数
- 主程序跑在 1 GB 栈的线程里，深递归不容易爆栈
- C 编译器自动探测：`$CC` → cc → clang → gcc → zig cc
- 差分测试：每个示例编译成可执行文件，输出必须与虚拟机逐字节一致；运行时错误的行号和信息也一致
- 基准 bench_fib：虚拟机 0.80 秒 → 原生 0.004 秒；bench_native（fib(32) + 2000 万次循环 + 百万元素 map/filter）原生 0.29 秒

已知限制：
- Int 是 64 位（参考解释器是任意精度）；upper/lower 只转换 ASCII
- Map 用有序线性表实现，大 Map 查找是 O(n)

## 通道（0.12）

```kite
let ch: Chan<Int> = chan(8)          // chan() 无缓冲，chan(n) 有 n 个缓冲位
let (_, total) = spawn {
  { for i in 1..=1000 { send(ch, i) }; close(ch) },
  {
    var s = 0
    for x in ch { s += x }           // 读到通道关闭且取空为止
    s
  },
}
```

- `send(ch, v)`：无缓冲时等到有人接收，有缓冲时满了才等；向已关闭的通道发送是运行时错误
- `recv(ch) -> Option<a>`：没有值时等待；关闭且取空后返回 None；`try_recv` 不等待
- `close(ch)`：唤醒所有等待者；重复关闭是运行时错误
- `for x in ch`：类型检查器知道元素类型是 `a`
- 死锁检测：所有任务都在等待（通道或子任务）且没有可启动的任务时，立刻报“死锁”而不是卡住
- 和 spawn 的数据竞争规则一起用：任务之间只能通过返回值和通道交换数据

调度：
- 原生后端：spawn 的各项由父线程和辅助线程动态认领，同时运行的线程不超过 CPU 核数；
  某个任务在通道上阻塞时让出名额，并立即为未开始的项启动线程，所以内联执行的项阻塞也不会卡住同组其他项。
  调度和通道共用一把锁，持锁时不分配内存；获取锁时轮询 GC 安全点，等待时进入 GC 阻塞区，GC 不会被卡住。
  ThreadSanitizer 无警告；并行基准性能不变（2 核 0.27s，单线程 0.47s）
- 解释器（树/虚拟机）：确定性的协作式调度。任何时刻只有一个任务在跑，按顺序执行各项，
  只在通道阻塞时切换，所以不用通道的程序完全不创建线程，用通道的程序每次输出都一样
- 示例 examples/channels.kite：生产者-消费者、4 工人的工作池、四级流水线、50 个任务同时阻塞；
  原生在 1–6 个线程下各跑 200 次、GC 压力模式下输出都与虚拟机一致

## select（0.13）

```kite
select {
  recv(jobs) as v if open => match v {   // v: Option<T>，关闭时为 None；if 守卫为假则本轮不参与
    Some(j) => handle(j),
    None    => { open = false },
  },
  send(out, x) => {},                      // 能发出去时执行
  after(500) => print("超时"),             // 超时（毫秒）
  // 或 _ => ...                           // 都没就绪时立刻执行（非阻塞）；after 与 _ 最多一个
}
```

- select 是表达式，各分支类型要一致；按书写顺序取第一个就绪的分支（确定性，不像 Go 那样随机）
- recv 的绑定只能是变量名或 `_`（值是 Option，在分支里再 match）
- 守卫让一个分支本轮“消失”，相当于 Go 里把通道设为 nil；汇合多个来源时用它屏蔽已关闭的通道
- 新内置：`sleep(ms)`、`now_ms()`（单调时钟）
- 死锁检测考虑超时：带 after 或在 sleep 中的任务迟早会醒，不算死锁；没有超时的 select 全部卡住时报死锁
- 解释器在 select、落空的 try_recv 处让出接力棒，用 `_` 分支忙等的循环不会饿死其他任务；
  原生在轮询时会为尚未开始的任务启动线程（KITE_THREADS=1 也不会卡住）
- 实现：一个等待者在每个通道的等待队列里各挂一项（在它自己的栈上），被任一分支唤醒后摘掉其余各项；
  超时用 pthread_cond_timedwait。示例 examples/select.kite：两路汇合、非阻塞发送、超时、取消、带超时的请求-应答、轮询；
  原生 1–6 线程各跑 150 次、GC 压力、ThreadSanitizer 均通过
- 顺带修复：match/select 守卫 `if flag => ...` 曾被误解析成 lambda `flag => ...`

还没有：select 的随机公平性选项。

## 结构化取消（0.14）

```kite
let fastest = race {                      // 第一个完成的胜出，其余被取消；全部停下后才返回
  fetch("副本A", 300),
  fetch("副本B", 5),
}
let r = timeout(20, () => spin())         // Option：按时完成 Some(结果)，否则取消并返回 None
```

- 取消点：函数入口、循环回边、通道收发、select、sleep。CPU 死循环和阻塞在通道上的任务都能被取消
- 展开：被取消的任务沿调用栈执行每一层还没执行的 defer，再在任务边界结束；被闭包捕获的局部变量会先关闭（搬到堆上），
  所以从被取消任务里传出去的闭包依然安全
- 传递：取消对任务的所有后代生效；父任务等所有子任务停下后自己再展开（结构化：不会留下孤儿任务）
- 清理不被打断：正在展开的任务里，defer 中的阻塞操作和 spawn 不会再被取消
- 开始前就被取消的项直接跳过，它的 defer 不会执行（因为还没登记）
- race 各项类型必须一致；timeout 的类型是 `fn(Int, fn() -> a) -> Option<a>`
- 原生实现：只给有 defer 或会创建闭包的函数登记栈帧，取消时 longjmp 回任务边界；从未发生取消时取消点只是一次原子读，
  基准测试无可测开销（0.29s 对 0.29s）。阻塞的等待者登记在表里，取消时被立即唤醒
- 解释器：取消以异常展开（defer 已在 finally 中执行）；有定时器时循环回边会定期检查到期并让出，所以 timeout 能打断死循环
- 示例 examples/cancel.kite：对冲请求、超时打断死循环、取消传给后代、取消阻塞中的接收、清理中做阻塞操作；
  原生 1–6 线程 100 次、GC 压力、ThreadSanitizer 均通过

还没有：手动取消句柄（cancel token）、取消原因/错误传播（任务出错目前直接结束程序）。

## Actor（0.15）

```kite
enum Account { Deposit(Int), Balance(Chan<Int>) }

let (seen, final) = actor(0, (bal: Int, m: Account) => match m {
  Deposit(n) => bal + n,
  Balance(r) => { send(r, bal); bal },
}, acct => {
  let _ = par_map(0..100, i => tell(acct, Deposit(10)))
  ask(acct, r => Balance(r))
})
// seen = 1000, final = 1000
```

- `actor(初始状态, 处理函数, body) -> (body 结果, 最终状态)`：处理函数 `fn(S, M) -> S` 一次只处理一条消息，状态无需加锁
- `tell(a, m)` 发消息；邮箱容量 64，满了发送方等待（背压）
- `ask(a, r => 消息(r))` 把一个回复通道塞进消息，等回复；消息通常用带 `Chan<T>` 字段的枚举
- 结构化生命周期：body 结束后邮箱关闭，actor 把剩余消息处理完才结束，然后 `actor(...)` 返回；不会留下孤儿 actor。
  句柄逃出 body 后再 tell/ask 报错“actor 已经停止，不能再发消息”
- 类型 `Actor<M>` 与 `Chan<M>` 区分：拿到 actor 句柄只能 tell/ask，不能直接 recv
- 和取消配合：`timeout(10, () => ask(a, ...))` 超时只取消等待方，已经发出的消息仍会被处理
- 处理函数在 actor 自己的任务里运行，同样受跨任务写变量检查
- 示例 examples/actors.kite：100 个并发客户存款、余额不足拒绝取款、两个 actor 协作记日志、ask 超时；
  原生 1–6 线程 120 次、GC 压力、ThreadSanitizer 均通过

还没有：actor 自己给自己发消息（可以把句柄放进消息里传进去）、选择性接收。

## 错误恢复与监督（0.16）

```kite
print(recover(() => 10 / zero))                 // Err("除以零")

let res = supervise(5, () => connect())          // 出错就重来，最多重启 5 次，超过则继续往上抛

let (_, total) = actor_supervised(0, (sum: Int, s: Str) => sum + unwrap(parse_int(s)), 2, acc => {
  for s in ["10", "20", "坏数据", "5"] { tell(acc, s) }
})                                               // 坏消息被丢弃，状态重置，actor 继续工作
```

- 运行时错误（panic、除以零、unwrap 失败、越界……）现在可恢复：`recover(f) -> Result<T, Str>`
- 结构化错误传播：spawn / par_map / race / actor 里一个子任务出错，兄弟任务被取消（执行各自的 defer），
  父任务等它们全部停下后重新抛出这个错误，位置保留在出错处。没人接住就像以前一样打印并退出
- `supervise(n, f)`：f 出错就重新运行，最多 n 次。监督策略靠组合得到：
  每个子任务各自 `supervise` 即 one_for_one；把整个 `spawn` 放进 `supervise` 即 one_for_all
- `actor_supervised(初始状态, 处理函数, n, body)`：处理函数出错时丢弃那条消息、状态重置为初始值；超过 n 次则升级给父任务
- 每次重启在 stderr 打印 `supervise：第 k 次重启（最多 n 次），原因：…`（先刷新 stdout，所以输出顺序在各引擎一致）
- 不可恢复：死锁、内存不足、无法创建线程（直接退出）
- 取消不会被 recover 接住（穿过 recover 边界到任务边界）
- 原生实现：错误沿登记的栈帧展开（执行 defer、关闭捕获变量）后 longjmp 到最近的边界；持有调度锁时出错仍直接退出
- 示例 examples/supervision.kite；tests/task_panic.kite（三个引擎一致报错、兄弟 defer 已执行）；
  原生 1–6 线程 120 次、GC 压力、ThreadSanitizer 均通过


## 带类型的错误值（0.17）

`recover` 现在返回 `Result<T, Panic>`，`Panic` 和 `PanicKind` 来自预导入（`prelude.kite`，每个程序自动带上）：

```kite
type Panic = { kind: PanicKind, message: Str, location: Str }
enum PanicKind { PanicCalled  DivByZero  IntOverflow  IndexOutOfRange  UnwrapFailed  AssertFailed  ChannelClosed  RuntimeFault }

match recover(() => xs[7]) {
  Ok(v) => print(v)
  Err(p) => match p.kind {
    IndexOutOfRange => print("越界:", p.message)
    _               => repanic(p)          // 原样重新抛出，位置仍是原出错处
  }
}

// 只重启暂时性故障，程序错误立即上抛
let v = supervise_if(5, p => p.kind == PanicCalled, () => fetch())
```

- `panic("x")` 的 message 是 `"x"`（不再带 `panic: ` 前缀）；location 是 `文件名:行`，三个引擎一致
- 分类按错误消息前缀，Python（`PANIC_PREFIXES`）与 C 运行时用同一张表
- 重启日志也显示去掉前缀的消息
- 设计取舍：可预期的失败仍然用 `Result<T, E>`（用户自定义 E）；Panic 只描述程序缺陷和运行时故障，种类固定
- 测试：tests/repanic.kite（三引擎报原位置）、tests/bad_panic.kite；run_tests.sh 88 项

## 测试（0.11）

```
kite test                    # 在包里：运行 src/ 和 tests/ 下所有测试
kite test dist               # 只运行名字含 dist 的测试（也可写 文件::名字）
kite test a.kite tests/      # 指定文件或目录
kite test --native           # 编译成原生程序跑（也支持 --tree、--no-check、-v）
```

- 测试是顶层、无参数、名字以 `test_` 开头的函数，不需要新语法
- 断言：`assert(c[, 消息])`、`assert_eq(左, 右[, 消息])`、`assert_ne(左, 右[, 消息])`、`panic(消息)`；
  `assert_eq` 两边类型必须相同（类型检查器保证），失败时显示两边的值
- src/ 里的测试和代码同模块，可以测私有函数；tests/ 里用 `use 包名` 引用本包，只能访问 pub 项
- 每个测试在全新的运行时里执行，`main` 不会运行
- 测试的 print 输出被捕获，只在失败时显示；失败信息定位到文件和行；有失败时退出码为 1
- 原生模式每个文件编译一次，每个测试单独一个进程，一个测试崩溃不影响其他测试

示例：examples/packages/geom（src/lib.kite 里 2 个单元测试，tests/geom_test.kite 里 3 个集成测试）。

## 模块与包管理（0.10）

```
kite new app                 # 创建程序包：kite.toml + src/main.kite
kite new geom --lib          # 创建库包：src/lib.kite
kite add geom                # 从仓库添加依赖（默认约束 ^最新正式版），写 kite.toml 和 kite.lock
kite add util --path ../util # 本地路径依赖
kite get / kite update       # 按锁文件安装 / 重新解析到最新兼容版本
kite tree                    # 依赖树
kite publish                 # 发布库包到仓库
kite run / check / build     # 在包目录里不写文件名时使用 src/main.kite；缺的依赖自动安装
```

模块：
- 一个文件一个模块，`use geom` 后用 `geom.area(s)`、`geom.Point`；`use geom.{Point, dist}` 直接引入；
  `use geom.shapes` 引入子模块（geom/src/shapes.kite）
- 查找顺序：当前包 src/ 下的 a.kite 或 a/mod.kite → 依赖包；循环引用报错并给出完整链条
- 只有 `pub` 项能被其他模块访问；impl 里的方法总是公开
- 模块的顶层名字在内部改名为“模块.名字”，不同模块里的同名函数和类型互不冲突
- 报错定位到具体文件和行（三个引擎和原生程序都一样），LSP 会连同依赖一起检查

包管理：
- kite.toml 声明依赖，约束语法与 Cargo 一致：`"1.2"`/`"^1.2"`、`"~1.2.3"`、`"=1.2.3"`、`">=1.0, <1.5"`、`"*"`；
  预发布版本只有约束里明确写了才会被选中
- 每个包只选一个版本，取满足所有约束的最高版本；冲突时列出每条约束来自谁
- kite.lock 记录精确版本、来源和 sha256；有锁文件时 `kite get` 不会升级，`kite update` 才升级
- 仓库目前是目录形式（`KITE_REGISTRY`，默认 ~/.kite/registry）：打包是确定性的，版本发布后不可覆盖，
  安装时校验 sha256，被篡改的包拒绝使用；发布前会对库做完整类型检查
- 依赖安装在项目的 .kite/deps/ 下

示例：examples/packages/（app 依赖仓库里的 geom，geom 依赖 mathx，再加一个路径依赖 util）。
tests/test_packages.py 在临时目录里完整走一遍：发布、解析、锁定、升级、冲突、篡改、私有项、循环引用、三个引擎和原生编译。

已知限制：
- 枚举变体仍是全局名字，不同模块的变体不能重名（会给出明确报错）
- 结构体字面量需要先 `use geom.Point` 再写 `Point { ... }`，还不支持 `geom.Point { ... }`
- 还没有远程仓库服务、包签名、工作区（workspace）

## 并发（0.9）

结构化并发：任务的生命周期不会超出创建它的表达式。

```kite
let (a, b) = spawn { fib(32), count_primes(20000) }   // 并行执行，全部完成后返回元组
let squares = par_map(1..=1000, n => n * n)            // 数据并行，结果顺序与输入一致
```

- `spawn { e1, e2, ... }`：每一项是一个任务（可以是代码块），可嵌套
- `par_map(xs, f)`：每个元素一个任务，原生后端按 CPU 核数分段并行
- 原生后端：真正的多线程；同时运行的线程数 ≤ CPU 核数（`KITE_THREADS` 可覆盖），超出时内联执行，嵌套 spawn 不会爆线程
- Python 参考实现和虚拟机按顺序执行任务，语义相同，所以三个引擎输出一致

数据竞争在设计上不可能发生：
- 值全部不可变，可变的只有变量
- 编译期：任务里不能给外层变量赋值，不能用 `return` 或 `?` 跳出任务
- 运行期：每个被闭包捕获的变量记录所属任务，别的任务通过闭包写它会立即报错
  （`spawn 任务不能修改外层变量（该变量属于其他任务）`）；任务结束后变量交还给父任务；全局变量属于主任务
- 任务出错时整个程序以该错误退出（多个任务同时出错时报告最先到达的那个）

多线程 GC：线程本地分配（快路径无锁），回收时 stop-the-world，线程在分配、循环回边、函数入口这些安全点停下，
等待子任务或读标准输入的线程算作已停下。ThreadSanitizer 下无数据竞争报告。

基准（2 核机器，bench_parallel：两个 fib(32) + 4 个分配密集任务共 360 MB 分配）：
单线程 0.48 秒 → 双线程 0.28 秒，约 1.7 倍。

尚未实现：Chan / Actor、取消与超时、工作窃取调度器、任务栈按需增长（现在每个线程预留 256 MB 虚拟栈）。

## 垃圾回收（0.8）

原生运行时使用非移动式标记-清除 GC：
- 小对象（≤ 2 KB）按 14 个尺寸等级放进 256 KB 对齐的区块，带分配/标记位图和空闲链；大对象单独分配
- 根：全局变量、字符串常量、每个 Kite 线程的 C 栈和寄存器（setjmp 溢出）
- 保守扫描（与 Boehm GC 同类）：看起来指向堆内的字都算引用，支持内部指针；运行时 C 代码无需登记临时变量
- 触发条件：自上次回收后新分配超过 max(8 MB, 上次存活量)；整块空闲的区块归还给系统
- 环境变量：`KITE_GC_STATS=1` 打印统计，`KITE_GC_STRESS=1` 每次分配都回收并给死对象投毒（测试用），`KITE_GC_OFF=1` 关闭回收

测试：
- 每个示例都在压力模式下再跑一遍，输出必须与虚拟机一致（误回收会被投毒立刻暴露）
- gc_churn：产生约 470 MB 垃圾并保留一个 3 MB 大对象，峰值堆 17 MB（关闭 GC 时 520 MB，进程 RSS 11 MB 对 557 MB）

后续路线：精确栈图（由编译器生成）→ 分代（年轻代 bump 分配 + 复制）→ 并发标记，见规范 GC 一节。

## 格式化器（0.6）

    bin/kitec fmt file.kite          # 输出格式化结果
    bin/kitec fmt -w src/*.kite      # 写回文件
    bin/kitec fmt --check src/*.kite # CI 用：有未格式化文件就返回 1

- 完整规则见 [docs/fmt.md](docs/fmt.md)；仓库里所有 .kite 文件都按它格式化过（run_tests 会检查）
- 行宽 100（全角字符算 2 列），2 空格缩进；语句之间的换行有语义，原样保留
- 放不下时从外到内断开：赋值右边 → 最低优先级的 `||` `&&` `|>` → 方法链（≥2 次调用）→ 括号列表
- 括号列表：单行 → 拥抱最后一个闭包/块/字面量 → 每项一行加尾逗号（短 token 列表按行填满）；多行且带尾逗号的列表保持展开（魔法尾逗号）
- match 分支的 `=>` 不再对齐；运算符两侧空格；泛型 `List<T>` 与比较 `a < b` 自动区分；注释全部保留
- 安全网：输出和输入的 token 序列（含语句分隔，忽略尾逗号）必须一致且能解析，否则拒绝输出；保证幂等

## 编辑器支持（0.6）

语言服务器 `kitec lsp`（Kite 写的，kitec/src/lsp.kite，不需要 Python）走标准 LSP，任何支持 LSP 的编辑器都能用：
- 实时诊断：每次编辑重新做语法 + 类型检查（引用了其他模块时连同模块和依赖包），一次显示所有错误和警告
- 悬停：签名 + 声明上方的 `//` 注释作为文档；局部变量和参数的推断类型；内建函数、标准库模块
- 跳转到定义（跨文件：`m.f`、`use m.f` 导入的名字）、重命名、查找引用、文档内高亮（pub 名字连同包内 use 它的文件）
- 文档大纲、补全（`.` 后补全方法/字段/`std` 模块成员）、签名提示（正在输入时也能用）、格式化
- 回放测试：`sh tests/lsp.sh bin/kitec`

**VS Code**：安装 `kite-lang-0.6.0.vsix`（扩展 → ⋯ → 从 VSIX 安装）。build.sh 会把 bin/kitec 打包进扩展（也可在设置 kite.kitecPath 里指定）；
自带语法高亮（含字符串插值）、括号/注释配置、保存时自动格式化。重新打包：`editors/vscode/build.sh`。

**Neovim**（0.10+）：

    vim.filetype.add({ extension = { kite = "kite" } })
    vim.api.nvim_create_autocmd("FileType", { pattern = "kite", callback = function()
      vim.lsp.start({ name = "kite", cmd = { "/path/to/kite/bin/kitec", "lsp" } })
    end })

**Helix**（languages.toml）：

    [[language]]
    name = "kite"
    scope = "source.kite"
    file-types = ["kite"]
    language-servers = ["kite-lsp"]
    [language-server.kite-lsp]
    command = "/path/to/kite/bin/kitec"
    args = ["lsp"]

## 字节码虚拟机（0.5）
- 47 条栈式指令；局部变量编译期解析为槽位，不再逐层按名字查环境
- Lua 式 upvalue 闭包：循环里每次迭代的闭包各自捕获自己的变量
- 编译期跟踪栈深度，break/continue 可以从表达式中间安全跳出
- defer 在块结束、break/continue、return、? 提前返回和运行时错误时都会执行
- 即使 --no-check，给 let 赋值也会在编译期被拒绝
- 差分测试：每个示例都在两个引擎上运行，输出必须逐字节一致
- 基准（bench_fib：fib(24) + 20 万次循环）：解释器 2.2s → 虚拟机 0.81s，约 2.7 倍

## 类型检查器
- 基于合一的类型推断：局部变量、lambda、未标注的函数参数和返回值都能推断
- 泛型函数、泛型 struct、泛型 enum（如 Tree<T>），调用处自动实例化
- 双向检查：map(words, w => w.len()) 中 w 自动推断为 Str
- 严格数值：Int 和 Float 不能混算，无隐式转换；无法确定的数值默认为 Int
- match 穷尽性：漏掉枚举变体、Bool 分支或缺少兜底 _ 都会报错
- ? 运算符：检查函数返回类型与 Result 错误类型一致
- 不可变：给 let 绑定赋值是编译错误
- 错误恢复：一次报告多个错误（最多 20 条），附行号
- trait 约束（0.4）：fn largest<T: Ord>、<T: Describe + Show>；函数体内泛型只能用约束提供的方法和运算；
  调用处检查实参类型是否实现了 trait；impl 缺少 trait 方法会报错；trait 支持默认方法
- 内建 trait：Eq、Ord、Show、Num、Hash（Num ⇒ Ord ⇒ Eq）；Int/Float 实现 Num，Str 实现 Ord

## 已知限制（M2 计划）
- 用户类型实现 Ord/Num 后还不能用运算符（需要运算符重载）
- impl 的方法签名尚未与 trait 声明逐项比对
- Set 字面量未实现

## 重启策略：时间窗口与退避（0.18）

```kite
let p = with_backoff(restart_policy(5, 10000), 10, 1000)   // 10 秒内最多重启 5 次；等待 10、20、40……最多 1000 毫秒
let v = supervise_with(p, e => e.kind == PanicCalled, () => fetch())
let (_, st) = actor_supervised_with(0, handler, p, body)
```

- `RestartPolicy = { max_restarts, within_ms, backoff_ms, max_backoff_ms }`（预导入）；`restart_policy(n, ms)` 不带退避，`with_backoff(p, 起始, 上限)` 加指数退避
- 窗口语义同 Erlang 的 intensity/period：只统计最近 within_ms 毫秒内的重启，超过 max_restarts 就把错误原样升级
- 退避按连续失败次数计：起始 × 2^(k-1)，封顶；actor 处理成功一条消息后连续计数清零
- 退避期间的 sleep 是取消点：外层 timeout / race 能打断正在退避的监督者
- 日志：`supervise：窗口内第 k 次重启（每 W 毫秒最多 n 次），等待 B 毫秒，原因：…`
- 策略逻辑全部用 Kite 写在 prelude.kite（三个引擎共用一份）；旧的 supervise / actor_supervised（只计总次数）保留
- 测试：examples/supervision.kite 第 7–9 节（三引擎一致、原生 1–6 线程、GC 压力、TSan），tests/bad_policy.kite

## 同名变体（0.19）

两个枚举有同名变体时，模式里的 `A.Other` 现在按限定名检查；不写限定名的模式按被匹配值的类型选枚举
（所以用户自定义 `DivByZero` 不再影响 `match p.kind { DivByZero => ... }`）。表达式里的无字段同名变体（0.20 起）也按用法推断：`let a: A = Other`、`fa(Other)`、`[Other, X]` 都选 A；没有线索时取最后定义的枚举。带字段的同名构造器仍取最后定义的，需要时写 `A.Ctor(...)`。
示例 examples/variants.kite。


## 运算符重载、签名检查、Unicode 与 Map（0.21）

用户类型在 impl 里定义同名方法后即可使用运算符：

| 运算符 | 方法 |
|---|---|
| `+ - * / %` | `add` `sub` `mul` `div` `rem`（接收一个参数） |
| 取负 `-a` | `neg(self)` |
| `< > <= >=` | `cmp(self, o) -> Int`（负数、0、正数） |

```kite
impl V2 {
  fn add(self, o: V2) -> V2 { V2 { x: self.x + o.x, y: self.y + o.y } }
  fn cmp(self, o: V2) -> Int { self.x - o.x }
}
var c = a + b
c += a          // 改写为 c = c + a
print(a < b)
```

- 类型检查时把 `a + b` 改写成 `a.add(b)`，三个引擎行为一致；用到运算符时操作数类型必须已知。`--no-check` 下不生效。泛型见 0.24 的运算符 trait。
- impl 中的方法必须与 trait 声明的签名一致（参数个数、self、参数与返回类型；泛型参数可改名），否则报错。
- 原生后端 `upper`/`lower` 支持完整 Unicode（表由 Python 生成：`runtime/kite_case.h`），包括 `ß→SS` 和希腊语词尾 `ς`，与 Python 引擎一致。
- （0.21 的原生 Map 哈希索引已在 0.22 由持久化 HAMT 取代。）


## 持久化 Map（0.22）

Map 是不可变值：`set(m, k, v)`、`m[k] = v`、`remove(m, k)` 都返回新 Map，旧版本保持不变，而且写入不再整份复制。

```kite
let a = Map([("x", 1)])
let b = set(a, "y", 2)     // a 仍然只有 x
let c = remove(b, "x")     // 新增内建函数 remove
```

- 原生后端用 HAMT（32 路哈希字典树）实现，读、写、删都是 O(log32 n)，只复制根到叶的路径；哈希完全相同的键放进冲突桶。遍历、`keys`、`values` 和打印都按首次插入的顺序进行，与 Python 引擎一致。
- Python 引擎用 Baker 式重定根：最新版本直接持有 dict，旧版本记录差异，访问旧版本时自动回滚；线性使用（`m = set(m, k, v)`）每次写入 O(1)。`remove` 会完整复制一次。
- 原生 20 万次写入加 20 万次读取约 0.2 秒（原先是 O(n²)）。
- 修复：Python 引擎的整数 `%` 原先经过浮点运算，超过 2^53 时结果不准，现在与原生一致。

- 0.23：任何值都能当 Map 的键（List、Tuple、结构体、带数据的变体、Map 本身；函数和通道按身份比较），三个引擎行为一致。Python 引擎内部用 `map_key` 把键规范化为可哈希形式。更新已有键时，键也替换为新给的值（与原生一致）。

## 泛型运算符约束（0.24）

预导入里定义了运算符 trait：`Add` `Sub` `Mul` `Div` `Rem`（`fn op(self, o: Self) -> Self`）、`Neg`（`fn neg(self) -> Self`）、`Cmp`（`fn cmp(self, o: Self) -> Int`）。Int 和 Float 实现了全部，Str 实现了 `Add` 与 `Cmp`。

```kite
fn total<T: Add>(xs: List<T>, zero: T) -> T {
  var acc = zero
  for x in xs { acc += x }
  acc
}
impl Add for V2 { fn add(self, o: V2) -> V2 { ... } }
total([1, 2], 0); total(["a", "b"], ""); total([v1, v2], V2 { x: 0, y: 0 })
```

- 泛型参数带运算符 trait 约束时，`a + b` 改写为 `a.add(b)`，`a < b` 改写为 `a.cmp(b) < 0`，`-a` 改写为 `a.neg()`。带 `Num` 或 `Ord` 约束时仍走内建运算，速度不变。
- 用户类型要传进这类泛型函数，需要写 `impl Add for V2`；只在 `impl V2` 里定义 `add` 只能直接使用运算符。

## 静态方法（0.25）

`impl` 里不带 `self` 的方法是静态方法，用 `类型.方法(...)` 调用；在 impl 内部可以写 `Self.方法(...)`，签名里的 `Self` 指当前类型。

```kite
impl V2 {
  fn new(x: Int, y: Int) -> Self { V2 { x: x, y: y } }
  fn zero() -> V2 { Self.new(0, 0) }
}
impl<T> Stack<T> {
  fn empty() -> Stack<T> { Stack { items: [] } }
}
let v = V2.new(3, 4)
let s = Stack.empty().push(1)
let mk = V2.new          // 可以当函数值使用
```

- 静态方法会编译成顶层函数 `V2.new`，三个引擎行为一致，也能跨模块调用（`geom.Point.origin()`）。
- 不存在的静态方法报 `类型 V2 没有静态方法 x`；用类型名调用实例方法时，会提示改用 `值.方法(...)`。
- 已修正格式化器对 `impl<T>` 的处理。

## LSP 悬停显示局部类型（0.26）

把鼠标悬停在局部变量、模式绑定、函数参数或 lambda 参数上，会显示类型检查器推断出的类型，例如 `var acc: T`、`let ys: List<Int>`、`(参数) xs: List<T>`。会挑选同一个函数里、在光标之前最近的那次绑定；文件有类型错误时也能显示。

## 模式绑定语法与精确穷尽性（0.27）

```kite
if let Some(x) = o { print(x) } else { print("无") }      // 可以作为表达式，可以接 else if let
while let Some(v) = recv(ch) { total += v }                 // 支持 break / continue
let Some(x) = find(xs, n => n % 2 == 0) else { return "没有" }   // 绑定的变量在后面的代码里都能用
```

- `let ... else` 的 else 块必须跳出（以 return / break / continue / panic 结尾），否则在编译时报错。
- 三种写法都在解析时降级为 match，三个引擎行为一致。
- 穷尽性检查改用 Maranget 有用性算法，覆盖嵌套变体、元组、列表（`[]`、`[x]`、`[x, ..rest]`）、结构体、Bool，并给出一个漏掉的例子：`没有覆盖 (true, None)`、`没有覆盖 Some(None)`、`没有覆盖 [_]`。Int 和 Str 的字面量仍然需要 `_` 兜底。

## KIR 中间表示（0.28 第一阶段，0.29 第二阶段，0.30 第三阶段，0.31–0.32 补齐）

类型检查器会给每个表达式节点记下类型，`kir.py` 再把带类型的 AST 降级成 KIR：一种基于基本块的寄存器式 IR，每个寄存器的类型固定为 `i64`、`f64`、`b1`（不装箱的标量）或 `val`（装箱的任意值）。标量全程不装箱，只在和 `val` 交界处插入 `box` / `unbox`。M1 的代码生成后端定为 KIR → C（不引入 Cranelift / LLVM）。

```
$ kite kir examples/kir.kite
fn fib(v0: i64 /*n*/) -> i64 {
b0:
  v2 = lt v0, 2
  cbr v2, b1, b2
...
// 走字节码路径的函数：
//   main: 表达式 Select     （另一个程序；回退时会写明原因）
```

- 覆盖范围（第一阶段）：签名只含 Int / Float / Bool（返回值还可以是 Unit）的非泛型函数，函数体可以用标量运算、let / var、赋值、if / while / loop / break / continue / return、标量 match（字面量、区间、绑定、守卫）、相互递归，以及 abs / min / max / int / float。其他函数照常走字节码路径，两边通过装箱包装函数互相调用。
- 第二阶段（0.29）：泛型和非泛型顶层函数都能进 KIR，函数体还可以用字符串插值、列表 / 元组 / 结构体字面量、字段 / 下标 / 元组下标、解构 let、任意模式的 match 和 is、for（`for i in a..b` 编译成计数循环，不分配列表）、`?`、方法调用、管道，以及只捕获不可变变量的 lambda（捕获的值存进已关闭的 upvalue）。仍会回退的：spawn / race / select、defer、嵌套定义、给字段 / 下标 / 全局变量赋值、捕获可变变量的闭包。示例程序里绝大多数 main 现在都走 KIR，`tests/kir_boxed.kite` 覆盖这些构造。
- 第三阶段（0.30）：impl 里自己写的方法、defer（按块作用域执行，break / continue / return / 出错展开时都会执行，与字节码相同）、spawn / race、给字段和下标赋值（值语义，逐层生成新值写回）、捕获可变变量的闭包（这些变量放进 upvalue 单元，spawn 任务修改外层变量照样报错）都进了 KIR。
- 0.31：select 和函数里的局部 fn（可以递归、相互递归）也进了 KIR，示例程序全部走 KIR、没有回退；`kite test --native` 在做过类型检查时同样走 KIR。
- 0.32：trait 的默认方法也进了 KIR（类型检查器按每个 impl 留一份类型快照，每个实现类型各编译一份），函数里的局部 type / enum / trait / impl 不再导致回退。
- 优化：常量传播与折叠（溢出或除零的不折叠）、常量条件分支化简、空块穿透、不可达块删除、基本块合并、死代码删除。
- 原生后端：KIR 函数直接生成 C，用 `int64_t` 和 `double`，不装箱也不检查标签；溢出、除零、GC 安全点和取消点的语义保持不变。设置 `KITE_NO_KIR=1` 可以关闭 KIR，用来做对照。
- 基准（fib(35) + 1 亿次循环 + 600×400 Mandelbrot）：0.85 秒 → 0.18 秒，约 4.8 倍。`examples/bench_native.kite`（main 里有循环、管道和 lambda，0.28 时整体回退）：0.26 秒 → 0.057 秒，约 4.6 倍。

## 标准库分层（1.00）

`std/` 按 Go 的包路径组织成目录，`use` 路径与 Go 的 import 路径一一对应：

```kite
use std.go.ast                      // std/go/ast.kite，绑定名 ast
use std.go.{token, parser}          // 一次引入同一目录下的几个模块
use std.encoding.json.{Json, parse} // 先取最长的模块文件前缀，剩下的段是条目
use std.crypto.rand as crand
```

- 解析规则和用户包相同：`std/a/b.kite` 或 `std/a/b/mod.kite`，取最长匹配；`std/image.kite` 和 `std/image/png.kite` 可以并存。`io`、`fs`、`math`、`ptr` 仍是内建模块，`std/math/` 下的 `big`、`bits`、`cmplx`、`rand` 不影响 `use std.math`。
- 目录：`archive`、`compress`、`container`、`crypto`、`encoding`（含 `encoding/json/jsontext`、`encoding/json/v2`）、`go`（含 `go/doc/comment`、`go/build/constraint`、`go/version`）、`hash`、`image`、`index`、`log`、`math`（含 `math/big/float`、`math/big/rat`）、`mime`、`net`、`regexp`、`text`、`unicode`。`bufio`、`bytes`、`fmt`、`strings` 等 Go 里本来就在顶层的包留在顶层。
- 内部模块：路径里含 `internal` 的模块只能被它的父目录及其子模块使用，规则同 Go。`std.crypto.internal.nistec` 只供 `std.crypto.*`，`std.internal.unicode_tables`、`std.internal.html_entities` 只供 std 内部；外部引用报 `不能使用内部模块 …`。
- 兼容：旧的平铺名字（`std.go_ast`、`std.json`、`std.crand`、`std.bigfloat` …）在 Python 实现里保留一个版本，映射到新路径，并在 stderr 打印一次 `警告: std.X 已改名为 std.Y`；绑定名不变。内部模块不提供旧名字。kitec 只认新路径。

## 标准库模块（0.33）

用 Kite 自己写，放在 `kite/std/`，`use std.encoding.json` 时由模块系统加载。三种引擎因此行为完全一致，原生后端里也走 KIR。io / fs / math 仍是内建模块。

- `std.encoding.json`：`Json` 枚举（`JNull`、`JBool`、`JInt`、`JNum`、`JStr`、`JArr`、`JObj`），`parse(text) -> Result<Json, Str>`，`stringify`（紧凑，键按字典序）、`pretty`（两空格缩进），取值用 `field`、`at`、`as_str`、`as_int`、`as_float`、`as_bool`。支持 `\uXXXX` 和代理对，错误信息带字符位置。
- `std.strings`：`find`、`find_from`、`slice`、`lines`、`repeat`、`pad_left`、`pad_right`、`strip_prefix`、`strip_suffix`、`count`、`ord`、`chr`、`is_digit`、`is_alpha`、`is_space`。下标按字符计。
- `std.time`：`unix_ms`、`mono_ms`、`since_ms`、`sleep_ms`、`format_utc`（ISO 8601，UTC）。
- `std.env`：`get`、`get_or`、`args`（`kite run app.kite a b` 和 `./app a b` 都得到 `["a", "b"]`）。
- `std.process`：`exit`、`run(cmd) -> (退出码, 标准输出)`、`output(cmd) -> Result<Str, Str>`。

底层原语是带 `__` 前缀的内建函数（`__env_get`、`__args`、`__exit`、`__run`、`__ord`、`__chr`、`__unix_ms`、`__str_slice`、`__str_find`）。字符串字面量新增 `\r` 转义。示例：`examples/stdmods.kite`。

### 网络：std.net 与 std.net.http（0.34）

- `std.net`：TCP（IPv4）。`net.listen(host, port) -> Result<Listener, Str>`（port 为 0 时由系统分配端口，结果在 `.port`），`listener.accept()`，`net.connect(host, port)`，`conn.read_line()`、`read_bytes(n)`（按字节计）、`read_all()`、`write(s)`、`close()`。套接字都是非阻塞的：等待时 sleep 退避（1 到 16 毫秒），所以 I/O 会让出调度，能放进 `spawn` 或 `par_map`，也能被 `timeout` 取消。
- `std.net.http`：HTTP/1.1，只支持 `http://`。客户端用 `http.get(url)`、`http.post(url, body, content_type)`、`http.request(method, url, headers, body)`，返回 `Result<Response, Str>`，支持 Content-Length、chunked 和读到连接关闭。服务器先 `http.listen(port)`，再 `http.serve(l, handler)`（8 个任务并发 accept）、`serve_with(l, workers, handler)` 或 `serve_n(l, n, handler)`；handler 类型为 `fn(Request) -> Response`，panic 时回 500。辅助函数：`text`、`html`、`json`、`response`、`with_header`、`req.header(name)`、`parse_query`、`url_decode`。头部名统一转成小写。
- 底层原语：`__tcp_*`、`__byte_len`。示例：`examples/http.kite`（同一个程序里起服务器再请求它）。

### keep-alive（0.35）

- 服务器：HTTP/1.1 默认保持连接，HTTP/1.0 要客户端带 `Connection: keep-alive` 才保持。客户端发 `Connection: close`，或 handler 的响应头里带 `connection: close`，处理完就关闭连接。等第一个请求最多 10 秒，之后空闲 5 秒就关闭，一个连接最多处理 100 个请求。`serve` 默认 32 个任务，每个任务同一时间服务一个连接。`handle_one` 现在处理一个连接上的全部请求。
- 客户端：`let c = http.client("http://host:port")?`，然后 `c.get(path)`、`c.post(path, body, type)`、`c.request(method, path, headers, body)`，最后 `c.close()`。请求复用同一个连接；复用的连接被服务器关掉时，自动换新连接重试一次。连接放在容量为 1 的通道里，所以多个任务共用一个 Client 也安全（请求会排队）。`http.get` / `post` 仍然是一次性连接。
- `net`：新增 `conn.read_line_within(ms) -> Result<Option<Str>, Str>`，超时返回 None。

### Set（0.36）

持久化集合，和 Map 一样任何值都能当元素（结构体、列表、集合本身都可以），遍历按插入顺序，重复元素保留第一次出现的那个。

```kite
let a = Set([3, 1, 4, 1, 5])      // Set{3, 1, 4, 5}
let b = insert(a, 9)               // 返回新集合，a 不变
remove(b, 1); contains(b, 4); len(b)
union(a, b); intersect(a, b); difference(a, b); is_subset(a, b)
for x in a { ... }                 // to_list(a) 转成列表，再用 map / filter
Set([1, 2]) == Set([2, 1])         // true：相等不看顺序；集合也能当 Map 的键
var seen: Set<Str> = Set()
```

打印形如 `Set{1, 2}`，`type_of` 返回 `"Set"`。`to_list` 也能用于 List、Map（得到 (键, 值) 列表）和 Str。

### 集合字面量与运算符（0.37）

```kite
let a = Set{1, 2, 3}
let e: Set<Int> = Set{}
a | b      // 并集      a & b   // 交集      a - b   // 差集
s |= t; s &= t; s -= t
```

字面量和打印格式相同（`Set{…}`），`kite fmt` 会让 `{` 紧贴 `Set`，括号内侧不留空格。在 if、while、for、match 的条件里，`Set` 和 `{` 中间必须没有空格，否则 `{` 会被当成代码块。`|` 和 `&` 只能用于 Set，优先级同 Rust：`&` 高于 `|`，两者都高于比较、低于区间和算术，所以 `a | b == c` 是 `(a | b) == c`。

### 或模式（0.38）

```kite
match shape {
  Circle(_) | Dot        => "圆的"
  Square(x) | Circle(x)  => x          // 每个分支都绑定 x，且类型相同
}
match n { 0 | 1 => "小"; 10..20 | 100 => "特别"; _ => "其它" }
if let Some(1 | 2) = opt { … }
x is Some(1 | 3)
(Some(n), None) | (None, Some(n)) => …
```

或模式可以嵌套在任意子模式里。各分支绑定的变量集合必须相同，类型也必须一致，否则报类型错误。穷尽性检查会先把或模式展开，再交给原来的算法。在模式里，`|` 一定是“或”；在表达式里，`|` 是集合的并。

### 切片（0.39）

```kite
let s = "héllo, 世界!"
s[0..5]   s[7..]   s[..5]   s[..=1]   s[-3..]   s[..]     // Str 按字符（码点）计算
xs[1..3]  xs[2..]  xs[-2..]                               // List 一样
for c in s { … }    s[i]                                  // 逐字符遍历与取字符（原本就支持）
```

`xs[a..b]` 不包含 b，`xs[a..=b]` 包含 b，两端都可以省略，负数从末尾往回数。越界会报 `切片越界: 1..5，长度 2`（和下标越界一样）。如果需要自动截断，用 `strings.slice`。List 和 Str 之外的类型做切片是类型错误。

### 带标签的 break / continue（0.40）

```kite
'outer: for i in 0..len(xs) {
  for j in i + 1..len(xs) {
    if xs[i] + xs[j] == target { break 'outer }
  }
}
'rows: for row in grid { for x in row { if x < 0 { continue 'rows } } }
```

`while`、`for`、`loop`、`while let` 都可以加标签。不带标签的 break / continue 仍然作用于最内层循环。跳出多层循环时，途经的 `defer` 都会执行。标签不能跨越闭包或 spawn 的边界；写了不存在的标签会报类型错误：`找不到循环标签 'x`。

### loop 表达式与带值的 break（0.41）

```kite
let code = loop {
  tries += 1
  if tries == 3 { break 200 }
}
fn find(grid: List<List<Int>>, t: Int) -> Option<(Int, Int)> {
  var r = 0
  'search: loop {
    if r >= len(grid) { break None }
    for c in 0..len(grid[r]) { if grid[r][c] == t { break 'search Some((r, c)) } }
    r += 1
  }
}
```

`loop` 是表达式，它的值就是 `break v` 里的 v。各处 break 的值类型必须一致；不带值的 `break` 相当于 `break ()`；没有任何 break 的 loop 类型是 Never。`while` 和 `for` 的 break 不能带值，否则报类型错误。标签可以和带值 break 一起用（`break 'search v`），途经的 defer 照常执行。

### Int 位运算（0.42）

```kite
a & b   a | b   a ^ b   !a   a << 3   a >> 3      // >> 是算术右移
perm |= exec    perm &= !write    h ^= c    x <<= 2    x >>= 1
```

优先级和 Rust 一样：`* / %` > `+ -` > `<< >>` > 区间 > `&` > `^` > `|` > 比较。`!` 作用在 Int 上是按位取反，作用在 Bool 上是逻辑非。移位位数必须在 0 到 63 之间，否则报 `移位位数超出范围: 64`；左移时移出 64 位的部分直接丢弃。`|` 和 `&` 用在 Set 上仍然是并集和交集。泛型里的 `List<List<Int>>` 不受 `>>` 记号影响。

### 0.43：已知问题修复

- **Int 溢出在三个引擎中行为一致**：`+ - * /`、取负、`abs`、`sum`、`int()`、`math.floor/ceil` 结果超出 64 位时，树解释器和 VM 也会报 `整数溢出`，与原生后端相同。之前只有原生后端检查。`parse_int` 遇到超范围的数返回 Err；`int(Float)` 遇到 NaN、∞ 或超范围也报错。字面量超出 64 位直接报类型错误，`-9223372036854775808` 仍然合法。
- **局部 impl 方法和局部 `use std.X` 都走 KIR**，原生后端不再回退到字节码。函数体内的 `mod` 仍只支持写在顶层。0.44 起，函数体里也可以 `use std.strings` 等用 Kite 写的标准库模块，和内建模块一样会提升到全局。

### std.fs 扩展（0.45）

| 函数 | 类型 | 说明 |
|---|---|---|
| `fs.read(p)` / `fs.write(p, s)` | `Result<Str, Str>` / `Result<Unit, Str>` | 读取和覆盖写入（已有） |
| `fs.append(p, s)` | `Result<Unit, Str>` | 追加写入，文件不存在时会创建 |
| `fs.exists(p)` / `fs.is_file(p)` / `fs.is_dir(p)` | `Bool` | 判断路径是否存在及类型 |
| `fs.size(p)` | `Result<Int, Str>` | 文件字节数 |
| `fs.list(p)` | `Result<List<Str>, Str>` | 目录下的名字，按字典序排序，不含 `.` 和 `..` |
| `fs.mkdir(p)` | `Result<Unit, Str>` | 同 `mkdir -p`，目录已存在不算错 |
| `fs.remove(p)` | `Result<Unit, Str>` | 删除文件或空目录 |
| `fs.rename(a, b)` | `Result<Unit, Str>` | 重命名或移动 |

三个引擎的错误信息统一为 `路径: 原因`，例如 `/tmp/x: No such file or directory`。原来 `read` 和 `write` 的报错格式也一并改成了这种。

### LSP：跳转定义与重命名（0.46）

- **跳转定义**：除了顶层函数、类型、方法和字段，现在也支持局部变量和参数，会跳到光标前最近的那次绑定。
- **重命名（F2）**：只在当前文件内生效。
  - 局部变量：只改同一作用域里指向这个绑定的引用，被遮蔽的同名变量不受影响。
  - 顶层名字：改掉所有没有被局部变量遮蔽的引用。
  - 字段和方法：改 `.name`、定义处，以及结构体字面量里的 `name:`。
  - 字符串插值 `{…}` 里的名字也会一起改，注释和普通字符串内容不会动。
  - 新名字必须是合法标识符，不能是关键字。
- 已知限制：不跨文件；同一函数里块级作用域的遮蔽按“光标前最近的绑定”近似处理。

### 结构体更新语法（0.47）

```kite
let c = Cfg { host: "localhost", port: 80, debug: false }
let d = Cfg { port: 8080, ..c }        // 其余字段从 c 复制
let e = Cfg { debug: true, ..load() }  // base 可以是任意表达式，只求值一次
```

- `..base` 必须放在最后一项，否则会报语法错误；`base` 的类型必须和字面量的结构体类型相同。泛型结构体也可以用。
- 实现方式：类型检查器把缺少的字段补成 `base.f`。`base` 不是变量名时，会先绑定到一个临时变量，所以三个引擎不需要任何改动。示例见 `examples/struct_update.kite`。

### sort / min / max 支持 Cmp 类型，新增 sort_with（0.48）

```kite
impl Cmp for Version { fn cmp(self, o: Version) -> Int { ... } }
sort(vs)  min(vs)  max(a, b)          // 自动按 Version.cmp 比较
fn top<T: Cmp>(xs: List<T>, n: Int) -> List<T> { sort(xs).reverse().take(n) }
sort_with(words, (a, b) => len(a) - len(b))   // 自定义比较函数，稳定排序
```

- 元素是实现了 `Cmp` 的结构体或枚举，或者是只带 `Cmp` 约束的泛型时，类型检查器会把调用改写成预导入里的 `__sort_cmp`、`__min_cmp` 等函数。
- 对没有实现 `Cmp` 的结构体或枚举排序，或者对不带 `Ord`、`Cmp` 约束的泛型排序，现在会在编译期报类型错误。以前这种情况要到运行时才崩溃。
- `min` 和 `max` 遇到空列表时，三个引擎统一报 `min: 空序列`。以前 Python 引擎在这里会抛出内部异常。
- 示例见 `examples/cmp_sort.kite`。

### 文法 v0.5 迁移第一批：字面量、const、as、格式说明（0.49）

```kite
const MASK: Int = 0xff_ff            // const：名字大写，类型必写
0xff 0o17 0b1010 1_000_000 1e3 2.5E-3
'k'  '\n'  '\u{98ce}'  "\u{7b5d}"     // 字符字面量（Char 即单字符 Str）
r"C:\temp"  r#"say "hi""#           // 原始字符串：不转义、不插值
n as Float / 2.0   x as Int          // Int、Float、Bool 之间转换；Float→Int 截断，溢出时 panic
[0; 3]  [[0; 3]; 2]                  // 重复列表
"{n:>5} {n:05} {x:.2} {n:x} {s:?} {s:*^9} {v:.2e}"
```

- 格式说明的写法是 `[[填充]对齐][0][宽度][.精度][x|X|o|b|e|?]`。宽度按码点计算；数字默认右对齐，其他值默认左对齐；精度作用于 Str 时表示截断。
- 无效的格式说明在编译期报错。插值里表达式后面如果还有多余内容，也会报错。修复了一个旧 bug：以前 `{n:>5}` 会把 `:>5` 静默丢掉。
- `as` 的优先级高于 `* /`、低于一元运算。不支持的转换，比如 `Str as Int`，属于类型错误。
- 示例见 `examples/literals.kite`。

### 文法 v0.6 迁移第二批：优先级与 ~（0.50，破坏性）

优先级从低到高依次是：`||`，`&&`，`is`，`== !=`，`< > <= >=`，`|>`，`.. ..=`，`|`，`^`，`&`，`<< >>`，`+ -`，`* / %`，`as`，一元运算。

```kite
xs |> len == 4          // (xs |> len) == 4；以前是 xs |> (len == 4)
0..4 |> map(f)          // 区间比 |> 绑得紧
1 | 2..5                // (1 | 2)..5；区间比 | ^ & 绑得松
~mask  mask & ~0b10     // ~ 是 Int 的按位取反；! 只用于 Bool
o is Some(n) && n > 1   // n 在 && 右侧可见
if o is Some(n) && n % 2 == 0 { print(n) }   // 在 then 分支里也可见
```

- `|>` 的右侧只能是后缀表达式（函数名、调用、字段）。`xs |> len + 1` 会报错并提示改写成 `(xs |> len) + 1`。
- `==` 和比较运算不可结合，`1 < 2 < 3` 会报错并提示改写成 `a < b && b < c`。
- 迁移旧代码用 `kite fmt --migrate [-w] 文件…`：先按 0.48 的优先级解析，凡是在新优先级下含义会变的子表达式都加上括号；再按旧语义做类型检查，把作用在 Int 上的 `!` 改成 `~`。
- 示例见 `examples/precedence.kite`。

### 顶层只允许声明（0.51，破坏性）

- 文件顶层只能写 `fn`、`type`、`enum`、`trait`、`impl`、`use`、`mod` 和 `const`。
- 顶层的 `let`/`var` 会报错，提示改成 `const NAME: T = …`；可变状态要放进函数里。
- 其他顶层语句也会报错，提示把它们放进 `fn main`。
- REPL 不受这条限制。
- `kite fmt --migrate` 会逐行列出需要手工改写的顶层 `let`/`var`。不自动改写是因为 const 的名字要求大写，自动改名可能和局部变量冲突。

### 第三批（一）：记录变体与 where 子句（0.52）

```kite
enum Shape {
  Circle { r: Float }
  Rect { w: Float, h: Float }
  Dot
}
let s = Rect { h: 2.0, w }          // 字段顺序任意，支持简写；也可以写成 Shape.Rect { … }
match s { Rect { w, h: hh } => w * hh, Circle { r } => r, Dot => 0.0 }
if s is Rect { w, .. } && w > 1.0 { … }
print(s)                            // Rect { w: 3.0, h: 2.0 }

fn top<T>(xs: List<T>, n: Int) -> List<T>
  where T: Ord { … }
impl<T> Pair<T> where T: Ord { … }  // impl 上的约束（包括 impl<T: Ord>）现在会作用到方法体里
```

- 记录变体在内部就是带字段名的变体，构造时由类型检查器按声明顺序改写成位置参数，所以三个引擎的模式匹配和相等比较都和普通变体一样。只有显示时用 `{ … }` 形式。
- 记录变体不支持 `..base` 更新。
- `where` 目前只能约束泛型参数，可以写在 fn 和 impl 上；写在 type 和 enum 上会报错。修复了一个旧 bug：以前 impl 上的泛型约束不会作用到方法体里。
- 示例见 `examples/records.kite`。

### 第三批（二）：dyn / impl 类型（0.53）

```kite
fn total(xs: List<dyn Shape>) -> Float { xs |> map(s => s.area()) |> sum }
let xs: List<dyn Shape> = [Circle { r: 1.0 }, Sq { s: 2.0 }]   // 不同类型可以放进同一个列表
fn describe(s: impl Shape, tag: impl Show) -> Str { … }        // 参数位置：匿名泛型参数
fn make(big: Bool) -> impl Shape {                              // 返回位置：不透明类型，按 dyn 处理
  if big { Sq { s: 10.0 } } else { Circle { r: 1.0 } }
}
```

- 实现了 trait 的类型，在期望 `dyn Tr` 的地方会自动转换：包括 let 标注、参数、返回值、列表元素、if 和 match 的分支。`dyn` 值只能使用 trait 提供的方法，不能访问字段，也不能转回具体类型。可以同时要求多个 trait：`dyn A + B`。
- 参数里写 `impl Tr`，等于给函数加一个带约束的匿名泛型参数。返回类型里写 `impl Tr`，调用方看到的是 `dyn Tr`。其他位置写 `impl` 会报错。
- 运行时本来就按值动态分派，所以 dyn 只是类型检查层面的功能，三个引擎都没有改动。List 是值语义，所以 `List<Circle>` 可以安全地传给 `List<dyn Shape>`。
- 示例见 `examples/dyn.kite`。

### 第三批（三）：关联类型（0.54）

```kite
trait Source {
  type Item: Show                       // 可以带约束，也可以写默认值：type Item = Int
  fn next(self) -> Option<Self.Item>
}
impl Source for Counter {
  type Item = Int
  fn next(self) -> Option<Int> { … }    // Self.Item 和 Int 可以互换着写
}
fn first<S: Source>(s: S) -> S.Item { s.next().unwrap() }
let n: Int = first(Counter { n: 3 }) + 1
```

- `T.Item` 和 `Self.Item` 在泛型函数体内是不透明类型，只能用关联类型约束提供的能力。到了调用处，一旦 T 确定为具体类型，就会规约成该类型 impl 里写的类型。
- impl 必须给出 trait 里所有没有默认值的关联类型，多写会报错。关联类型的约束会检查（例如 `type Item: Ord` 不接受 Bool）。
- 和 dyn 一样，这只是类型检查层面的功能，引擎没有改动。示例见 `examples/assoc.kite`。
- 修复：fmt 现在能把 `Option<Self.Item>` 这类带点的类型识别成泛型。

### 第三批（四）：显式泛型实参与属性（0.55）

```kite
let n = id.<Int>(3)
let p = pair.<Int, _>(1, "b")        // _ 表示让编译器推断
let f = id.<Str>                     // 不调用也可以，得到具体类型的函数值

@deprecated("改用 parse_int")
fn old_parse(s: Str) -> Int { int(s) }   // 调用处给出警告（stderr / LSP 黄线），不影响编译

@test
fn checks_add() { assert(1 + 1 == 2) }    // kite test 会收集它，函数名不必以 test_ 开头
```

- `.<…>` 只能跟在泛型函数名（顶层或局部 fn）后面。类型实参按声明顺序对应，个数必须一致，约束照常检查。类型检查之后它就是普通的函数名，三个引擎都不受影响。
- 支持的属性有 `@test`、`@deprecated(说明)`、`@inline`、`@cold`、`@allow(…)`、`@doc(…)`。其中 inline、cold、allow、doc 目前只是被接受，不产生任何效果。写未知属性会报语法错误。
- 属性可以写在声明、trait/impl 成员、结构体字段和枚举变体前面；写在语句前面会报错。`@test` 只能用在没有参数的 fn 上。

### 第三批（五）：async fn 与 .await（0.56）

```kite
async fn fetch(n: Int) -> Int { slow_square(n) }     // 类型是 fn(Int) -> Future<Int>

async fn main() {
  let f = fetch(3)                                    // 只是打包，还没执行
  print(f.await)                                      // 9
  let (a, b, c) = spawn { fetch(4), fetch(5), slow_square(6) }.await   // 三项并行
}
```

- Future 是惰性的（和 Rust 一样）：调用 async fn 只是把函数体打包成 `Future<T>`，`.await` 时才在当前任务里执行。要并发，就把多个 Future 放进 `spawn { … }.await`（或 `race { … }.await`）：组里凡是 Future 的项，都会在各自的任务里被 await；不是 Future 的项照常求值。
- `.await` 只能写在 async fn、async 块或 async 闭包里（包括 `async fn main`），`spawn { … }.await` / `race { … }.await` 例外：组本身就是同步等待的，在哪里都能写；普通闭包里写 `.await` 会报语法错误。对不是 Future 的值 `.await` 是类型错误。
- 一个 Future 作为语句被丢弃、没有 `.await` 时，会给出警告"Future 没有被 .await，不会执行"。
- async 方法（impl 和 trait 里）同样可用。实现上 `async fn f() -> T { body }` 被改写成 `fn f() -> Future<T> { Future { run: () => body } }`，`x.await` 被改写成 prelude 里的 `__await(x)`；Future 是 prelude 定义的普通结构体，三个引擎都不用改。
- async 块和 async 闭包见下文（0.58）。

### 第三批（六）：extern "C" 与 unsafe（0.57）

```kite
extern "C" {
  fn strlen(s: Str) -> Int
  fn sqrt(x: Float) -> Float
}

@link("m")                  // 额外链接的库：原生后端加 -lm，解释器 / 字节码用 ctypes 加载 libm
extern {
  fn cbrt(x: Float) -> Float
}

fn c_len(s: Str) -> Int { unsafe { strlen(s) } }
```

- extern 块只能写在顶层，里面只能放 fn 签名（不能有函数体或泛型）。ABI 字符串可以省略，写的话只能是 "C"。
- 参数类型只支持 Int（int64_t）、Float（double）、Bool、Str（以 NUL 结尾的 UTF-8 `const char *`，只在调用期间有效）。返回值类型还可以是 Unit（void），省略不写就是 Unit。返回 Str 时会复制一份 C 字符串，NULL 返回成 ""。
- 使用 extern 函数（调用它或把它当值）必须放在 `unsafe { … }` 里，否则报类型错误。`unsafe { … }` 是表达式，值就是块的值。
- 三个引擎：解释器和字节码用 ctypes 在进程符号（libc、libm）和 `@link` 指定的库里查找函数，找不到就在加载时报错"extern: 找不到 C 函数 X"。原生后端生成直接调用 C 符号的包装（用汇编名绑定，不会和运行时头文件冲突），并把 `@link` 的库加到链接参数里。
- C 那边的 int 参数按 int64 传；在 x86-64 和 arm64 上这样做没问题，但严格来说应该声明成和 C 签名一致的宽度（以后会加 I32 之类的类型）。
- 还不支持：指针、结构体、回调、变参函数（printf）。

### async 块与 async 闭包（0.58）

```kite
let double = async (x: Int) => fetch(x).await * 2      // fn(Int) -> Future<Int>
let job = async {                                       // Future<Int>，此时还没执行
  fetch(1).await + fetch(2).await
}
let tag = async fn(s: Str) -> Str { if s == "" { return "空" }; "<{s}>" }
let (x, y) = spawn { double(5), async { fetch(7).await + 1 } }.await
```

- 写法有四种：`async { … }`、`async x => e`、`async (a, b) => e`、`async fn(a: T) -> R { … }`。体内可以 `.await`。结果和 async fn 一样是惰性的 Future，`.await` 时才执行。
- async 闭包体里的 `return` 只从这个闭包返回。async 块是循环标签的边界，不能 break 到外层循环。
- 普通闭包仍然不能 `.await`，哪怕它写在 async fn 里面。
- 语句开头的 `async fn 名字` 是声明，`async fn(…)` 和 `async {` 是表达式。

### unsafe fn（0.59）

```kite
// 调用者必须保证 n >= 0
unsafe fn fast_abs(n: Int) -> Int {
  labs(n)                      // 函数体本身就是 unsafe 上下文，可以直接调用 extern 函数
}

impl Buf {
  unsafe fn raw_len(self) -> Int { strlen(self.s) }
}

fn main() {
  unsafe { print(fast_abs(-5), b.raw_len()) }
}
```

- `unsafe fn` 可以写在顶层、函数体里、impl（实例方法和静态方法）、trait 和 extern 块里。前缀顺序是 `[pub] [async] [unsafe] fn`。
- 调用（或者取值）unsafe 函数或方法必须放在 `unsafe { … }` 里，否则报类型错误："使用 unsafe 函数 X 需要放在 unsafe { … } 里"。
- unsafe fn 的函数体是 unsafe 上下文。
- trait 里声明成 unsafe fn 的方法，impl 里也必须写 unsafe fn，反过来也一样。
- unsafe 只在编译期检查，不影响运行。目前的限制：把 unsafe 函数存进变量之后，通过变量调用就不再检查了（还没有 `unsafe fn(…)` 函数类型）。

### 模式补齐（0.60）

```kite
const MAX: Int = 9

match n {
  MAX => "max"                       // 常量模式：值为字面量的 const
  ..0 => "negative"                  // 开区间：..0  ..=0  100..
  small @ 1..=8 => "small {small}"   // @ 绑定：匹配子模式，同时绑定整个值
  _ => "other"
}
match c { 'a'..='z' => "lower", _ => "other" }      // 字符 / 字符串区间（按码点）
match xs { [first, ..mid, last] => …, [.., z] => …, _ => … }   // .. 可以出现在任意位置
match t { (a, .., d) => a + d }                     // 元组 / 元组变体里的 ..
match p { (var a, b) => { a += b } }                // var：可变绑定
impl Shape { fn kind(self) -> Str { match self { Self.Circle(_) => "c", Self.Rect(w, ..) => "r" } } }
```

- 0.59 及以前，`[.., last]` 和 `[a, ..mid, z]` 会被当成“剩余项在末尾”，结果是错的；0.60 修正了这个问题。
- 常量模式只接受初值是（可带负号的）字面量的 const；其他 const 请用守卫 `if x == K`。
- 模式里同一个名字绑定两次是类型错误；区间两端类型必须一致，空区间（`5..=1`）报错。
- 带 `..` 的元组模式需要知道元组长度；超出长度报错。

### use … as 与通配导入（0.60）

```kite
use geom as g                  // 模块改名：g.dist2(p)
use geom.Point as P            // 类型 / 函数改名
use geom.{origin as o, dist2}  // 分组里逐项改名
use geom.*                     // 导入模块的全部 pub 名字
use std.math as m              // 内建 std 模块同样支持
use std.encoding.json.*
```

- 分组和通配导入不能整体写 `as`。通配只导入 pub 名字。

### 超 trait（0.60）

```kite
trait Named { fn name(self) -> Str }
trait Greeter: Named {
  fn greet(self) -> Str { "hello, {self.name()}" }   // 默认方法可以调用超 trait 的方法
}
fn intro<T: Greeter>(x: T) -> Str { x.name() }      // T: Greeter 隐含 T: Named
let g: dyn Named = some_greeter                      // dyn Greeter 可以向上转换成 dyn Named
```

- `impl Greeter for X` 要求 X 也实现了 Named（以及更上层的超 trait），否则报错。
- 超 trait 不存在或构成循环都是类型错误。超 trait 可以是内建的运算符 trait：`trait Arith: Add + Mul {}`。

### 关联常量（0.61）

```kite
trait Shape {
  const SIDES: Int                 // 必须由 impl 提供
  const NAME: Str = "shape"        // 带默认值，impl 可以覆盖
  fn describe(self) -> Str { "{Self.NAME} with {Self.SIDES} sides" }
}
impl Shape for Sq { const SIDES: Int = 4 }
impl Circle { const PI: Float = 3.14159 }   // 固有 impl 也可以有常量

print(Sq.SIDES, Circle.PI)
```

- 通过 `类型.N` 访问；impl 里可以写 `Self.N`；trait 的默认方法里 `Self.N` 按值的实际类型取（需要有 self 参数）。
- impl 缺少没有默认值的常量、类型和 trait 声明不一致、trait 里没有这个常量，都是类型错误。
- 暂不支持：通过泛型参数访问（`T.N`），以及函数体内局部 impl 里的关联常量。

### type / enum 上的约束与 where（0.61）

```kite
type Sorted<T> where T: Ord = { items: List<T> }
enum Tree<T: Ord> { Leaf, Node(Tree<T>, T, Tree<T>) }
impl<T> Sorted<T> {
  fn top(self) -> T { max(self.items) }     // 隐含约束：这里的 T 自动带 Ord
}
```

- 构造时检查约束：`Sorted { items: [p] }` 里 p 的类型没有实现 Ord 就报错，枚举变体构造也一样。
- `impl<T> Sorted<T>` 自动继承类型上的约束，不必重写。类型别名（`type A<T: B> = …`）上的约束只解析不检查。

### 标准库第八批：encoding/json 对齐 Go 1.27（0.97）

Go 1.27 的 encoding/json（v1）改由 v2 实现，语法错误、Compact/Indent 的输出都来自 jsontext。这一批照着 jsontext 和 internal/jsonwire 移植语法层，再在上面实现 v1 的函数。对拍从 35 组增加到 36 组：`tests/go/json.go` 对 20 个基础文档做删除、替换、插入变异，共 3119 个输入，Unmarshal + Marshal、Valid、Compact、Indent 的结果、错误信息和 SyntaxError.Offset 全部逐字节一致，三引擎一致。

| 模块 | 对应 Go | 内容 |
|---|---|---|
| `std.encoding.json.jsontext` | encoding/json/jsontext、internal/jsonwire | `read_value` `check_eof` `append_format`（重排空白、缩进）、`consume_string` `consume_number`、`append_quote` `append_unquote` `append_float`、`v1_message`；输入是字节列表，可以含非法 UTF-8 |
| `std.encoding.json`（扩充） | encoding/json | `valid` `check_valid`（`SyntaxError { msg, offset }`）、`compact` `indent` `html_escape`、`unmarshal`（相当于解到 `any`：数字为 `JNum`，重复键后者生效，坏 UTF-8 和代理对换成 U+FFFD，数字溢出的错误带路径）、`marshal` `marshal_indent`（键排序，转义 `< > &` 和 U+2028/2029，Go 的浮点格式）。原有的 `parse` `stringify` `pretty` 不变 |

**0.98 补上 json/v2 和 jsontext**：移植了 jsontext 的状态机（栈、PeekKind / ReadToken / ReadValue、分隔符检查）和带 JSON Pointer 的错误。对拍增加到 37 组：`tests/go/jsonv2.go` 用 23 个基础文档、11 种变异，共 8800 多个输入，比较 `Value.IsValid`、`Compact`、`Indent`（默认和自定义缩进）、`Canonicalize`、v2 的 `Unmarshal` 和 `Marshal(Deterministic)`，44164 行全部一致。

| 模块 | 对应 Go | 内容 |
|---|---|---|
| `std.encoding.json.jsontext`（扩充） | jsontext.Value 等 | `is_valid` `compact` `indent` `indent_with` `canonicalize`（RFC 8785：数字规范化、字符串最小转义、成员按 UTF-16 排序）；`quote_go` `unquote_go`（AppendQuote / AppendUnquote）；`new_decoder` `peek_kind` `read_token` `read_value_at` `dec_check_eof` `stack_pointer`；`error_string` 生成 "jsontext: … within "/a/0" after offset N" |
| `std.encoding.json.v2` | encoding/json/v2 | `unmarshal`（解到 `Json`：严格 UTF-8，重复成员名报错，数字溢出报 `cannot unmarshal JSON number … into Go float64 within "/x": value out of range`）、`marshal`（键按字节序、不转义 HTML） |

**仍未做**：Kite 没有反射，所以 v2 的结构体标签、`omitzero`、自定义 Marshaler 这些无法照搬；流式 Decoder/Encoder（`io.Reader`）没有移植。

**0.99 补上 asn1、ed25519、ecdh、ecdsa、rsa**：对拍增加到 42 组，新增的 5 组都与 Go 1.27.1 逐字节一致（asn1 一组就有 131051 行输出）。

| 模块 | 对应 Go | 内容 |
|---|---|---|
| `std.encoding.asn1` | encoding/asn1 | 没有反射，用 `Ty` 描述目标类型（结构体字段带 `asn1:"..."` 参数）、`Val` 承载值；`unmarshal` / `marshal`（含 `_with_params`），覆盖 INTEGER / big.Int / BOOLEAN / ENUMERATED / Flag / BIT STRING / OID / 各种字符串 / OCTET STRING / UTCTime / GeneralizedTime / RawValue / RawContent / interface{} / SEQUENCE / SET OF，explicit / implicit / application / private / optional / default / omitempty；时间解析按 time.Parse 的规则和错误文字移植 |
| `std.crypto.ed25519` | crypto/ed25519 | `new_key_from_seed` `sign` `verify` `sign_with_options` `verify_with_options`（Ed25519、Ed25519ph、Ed25519ctx），非规范公钥 / S ≥ L 等边界与 Go 相同；域运算是 10 分量直线代码 |
| `std.crypto.internal.nistec` | internal/fips140/nistec | P-256 / P-384 / P-521：Montgomery 域乘法（`tools/gen_nistec.py` 生成直线代码）、Jacobian 点运算、4 位窗口标量乘法、编码与校验 |
| `std.crypto.ecdh` | crypto/ecdh | X25519、P-256、P-384、P-521 的 `new_private_key` `new_public_key` `ecdh`，错误文字与 Go 相同（含 arm64 上 P-256 的写法） |
| `std.crypto.ecdsa` | crypto/ecdsa | `parse_raw_private_key` `parse_uncompressed_public_key` `sign`（RFC 6979 确定性签名，HMAC_DRBG 照 Go 移植）`verify_asn1`（严格 DER） |
| `std.crypto.rsa` | crypto/rsa | 由分量构造并校验密钥；PKCS #1 v1.5 签名 / 验证 / 加密 / 解密，PSS，OAEP；随机字节由调用方给出 |

`std.math.big` 的模幂对奇数模数改用 Montgomery 乘法加 4 位窗口，RSA 私钥运算快了一倍多。

**仍未做**：需要真随机数的 GenerateKey、SignASN1、带随机盐的默认用法要调用方自己给随机字节；这些实现都不是常数时间的，只适合学习和测试，不要用于生产环境的密钥。

**1.00 第一批“剩余标准库”**：对拍增加到 52 组，新增 10 组全部与 Go 1.27.1 逐字节一致。

| 模块 | 对应 Go | 内容 |
|---|---|---|
| `std.crypto.des` / `std.crypto.rc4` | crypto/des、crypto/rc4 | DES、3DES（返回 cipher.Block，可接 CBC 等模式）、RC4 |
| `std.crypto.dsa` | crypto/dsa | generate_key、sign、verify（随机字节由调用方给出；不含 GenerateParameters） |
| `std.crypto.elliptic` | crypto/elliptic | P-224/256/384/521 与自定义曲线：add、double、scalar_mult、marshal(_compressed)、unmarshal(_compressed)、generate_key；std.crypto.internal.nistec 增加 P-224 和压缩点解码 |
| `std.crypto.mlkem` | crypto/mlkem | ML-KEM-768/1024：密钥、封装（含 mlkemtest 的确定性封装）、解封装、隐式拒绝 |
| `std.crypto.mldsa` | crypto/mldsa | ML-DSA-44/65/87：从种子生成密钥、编解码、确定性与 hedged 签名、context、外部 μ、验签；错误文字与 Go 相同 |
| `std.go.constant` | go/constant | 精确常量：Int（含大整数）、Float（分数 / 512 位浮点）、Complex；make_from_literal、binary_op、shift、compare、to_int/to_float、String/ExactString 与 Go 逐字节相同 |
| `std.net.http.cookiejar` | net/http/cookiejar | RFC 6265 Jar（可注入时间）、PublicSuffixList、Cookie 解析与 String、punycode/IDNA |
| `std.path.filepath` | path/filepath | Unix 语义的全部导出函数：clean join rel match glob eval_symlinks walk walk_dir is_local localize 等 |
| `std.image.color.palette` | image/color/palette | plan9（256 色）、web_safe（216 色） |
| `std.text.template.parse` | text/template/parse | 模板语法树：parse / tree_parse（PARSE_COMMENTS、SKIP_FUNC_CHECK、自定义定界符、define / block 树集）、全部节点（枚举 Node，变体名同 Go）、string、is_empty_tree、error_context；词法、数字常量（int/uint/float/complex）和全部错误信息与 Go 逐字节相同 |
| `std.crypto.cryptobyte` | golang.org/x/crypto/cryptobyte | DER 读取：read_asn1 / read_any_asn1(_element) / 可选元素 / 整数（int64、uint64、big、字节）/ OID / UTCTime / GeneralizedTime / BIT STRING / 布尔，规则与 Go 相同 |
| `std.crypto.x509.pkix` | crypto/x509/pkix | Name、RDNSequence、fill_from_rdn_sequence、to_rdn_sequence、name_string / rdn_string（转义和 #十六进制同 Go）、Extension、AlgorithmIdentifier |
| `std.crypto.x509` | crypto/x509（部分） | parse_certificate(s)、parse_revocation_list、parse_pkix_public_key（RSA / ECDSA P-224…P-521 / Ed25519 / X25519 / ML-DSA / DSA）、全部标准扩展、OID（parse_oid、oid_from_ints、oid_string …）；parse_certificate_request（CSR）；PKCS#1 / SEC1 / PKCS#8 私钥与 PKCS#1 公钥的解析和编码、marshal_pkix_public_key；check_signature、check_signature_from、CRL / CSR 签名校验；错误文字同 Go |
| `std.debug.elf` | debug/elf | new_file/open、头部/节/段、Section.data（含 zlib 压缩节）、symbols、dynamic_symbols、imported_symbols/libraries、dyn_string/dyn_value、版本表；全部常量及 String/GoString（不含 DWARF、重定位、zstd） |
| `std.index.suffixarray` | index/suffixarray | 后缀数组、lookup、与 Go 逐字节相同的 write/read 序列化（暂无 FindAllIndex） |
| `std.text.scanner` | text/scanner | 完整的 Go 记号扫描器：模式、空白集合、自定义标识符字符、错误与位置 |
| `std.go.version` | go/version | lang、compare、is_valid |
| `std.go.build.constraint` | go/build/constraint | //go:build 与 // +build 解析、eval、plus_build_lines、go_version |
| `std.net.textproto` | net/textproto | 读行、续行、MIME 头、状态码响应、点编码读写、规范化头名 |
| `std.mime.multipart` | mime/multipart | Reader（含 quoted-printable 部分、read_form）、Writer |

### 标准库第七批：跟进 Go 1.27（0.96）

对拍基准从 Go 1.23 换成 Go 1.27.1：全部 `.expected` 用 1.27 重新生成，再补上 1.24–1.27 新增的包。对拍从 31 组增加到 35 组，三引擎一致。

**1.24–1.27 的行为变化（已跟进）**

| 模块 | 变化 |
|---|---|
| `std.unicode` | 升到 Unicode 17.0.0；表格改由 `tools/gen_unicode.go` 直接从 Go 的 unicode 包生成，逐码点一致。文字从 11 种近似区块变成 Go 的全部 Scripts，并新增 `property_table`（Properties）、`script_names` `property_names`，`to_title` 改为查表 |
| `std.crypto.subtle` | `constant_time_less_or_eq` 不再要求 32 位范围内 |
| `std.net.mail` | 域字面量：`[IPv6:…]` 按 IPv6 解析，没有前缀的必须是 IPv4；头部错误信息里的行用 `%q` 引起来 |
| `std.mime` | `add_extension_type` 给 text/* 补 `charset=utf-8` 时保留类型（Go 1.23 的 bug 已修） |
| `std.mime.quotedprintable` | `=` 后的空白再接换行算软换行 |
| `std.crypto.cipher` | AES 的 CTR 走 crypto/aes 自己的实现，panic 信息不同；GCM 零长度 nonce 的错误信息变短 |
| `std.image.png` | 不压缩时 zlib 流用固定 Huffman 的空块（`03 00`）结束 |

**新增**

| 模块 | 对应 Go | 内容 |
|---|---|---|
| `std.uuid` | uuid（1.27） | `parse` `must_parse` `new` `new_v4` `new_v7` `nil_uuid` `max_uuid`，`string` `marshal_text` `append_text` `compare`；严格递增的 v7 用 `V7Gen` |
| `std.crypto.rand` | crypto/rand | `read(n)`（操作系统 CSPRNG）、`text()`、`int(max)` |
| `std.crypto.sha3` | crypto/sha3（1.24） | SHA3-224/256/384/512、SHAKE128/256、cSHAKE128/256，`write` / `read` / `sum_to` |
| `std.crypto.hkdf` `std.crypto.pbkdf2` | crypto/hkdf、crypto/pbkdf2（1.24） | `extract` `expand` `key`（`*_bytes` 版本的 info 可以是任意字节）；`pbkdf2.key`；错误信息和 Go 相同 |
| `std.strings` `std.bytes` | strings、bytes（1.27） | `cut_last` |
| `std.math.big` | math/big（1.27） | `BigInt.divide(y, mode)`，`TRUNC` `FLOOR` `ROUND` `CEIL` |

**新内建函数**：`__random_bytes(n)`（/dev/urandom）、`__unix_ns()`，三个引擎和 kitec 都已支持。

### 标准库第六批：对齐 Go（0.95）

做法和第五批相同：每个包都和真正的 Go 1.23 逐字节对拍，并且在树解释器、虚拟机、原生后端三个引擎上的输出一致。对拍从 14 组增加到 31 组。

| 模块 | 对应 Go | 内容 |
|---|---|---|
| `std.fmt` | fmt | `sprintf` `sprint` `sprintln` `errorf` `printf` `println_` `print_` `go_type`（%T）；参数用 `Arg` 枚举（`VInt` `VStr` `VFloat` `VList` `VMap` `VStruct` `VErr` `VNil` `VTyped` …）。支持所有动词、标志、宽度、精度、`*` 和 `%[n]`，`%!d(...)` 这类错误写法和 Go 相同；测试收录了 Go 的 fmt_test.go 中 659 行 fmtTests 和全部 reorderTests |
| `std.strconv`（扩充） | strconv | `format_float_go`（e E f g G b x X，prec -1 为最短表示，支持 32 位）、`parse_float_go` / `parse_float`（含十六进制浮点和 `_`）、`format_uint`、`quote_go` `quote_to_ascii` `quote_to_graphic` `quote_rune*` `unquote_go` `unquote_char` `quoted_prefix` `is_print` `is_graphic`、`float64_bits` 等 |
| `std.math.big.rat` | math/big.Rat | `Rat`：`parse`（分数、小数、指数、进制前缀）、四则运算、`cmp` `float_string` `rat_string` `float64`（带 exact 标志） |
| `std.math.big.float` | math/big.Float | `BigFloat`：精度和 6 种舍入模式、Accuracy、四则运算正确舍入、`sqrt`、`text`（e f g b p x，含最短表示）、`parse`、`float64` / `float32` / `int` / `rat`、`mant_exp` |
| `std.math.cmplx` | math/cmplx | `Complex` 及全部函数；除法照搬 runtime.complex128div。按 12 位有效数字对拍（Go 在 aarch64 上用 FMA，最后一两位可能不同） |
| `std.mime` `std.mime.quotedprintable` | mime、mime/quotedprintable | 内建类型表、`parse_media_type` / `format_media_type`（含 RFC 2231）、RFC 2047 的 Q/B 编码和解码；quoted-printable 的 Writer 和 Reader |
| `std.net.netip` | net/netip | `Addr` `AddrPort` `Prefix` 的解析、格式化、分类和比较，错误信息和 Go 相同 |
| `std.net.mail` | net/mail | 地址解析和 `Address.string()`、`read_message`、`parse_date`（24 种格式） |
| `std.compress.lzw` `std.compress.bzip2` | compress/lzw、compress/bzip2 | lzw 写出的字节和 Go 相同；bzip2 解压，连中途出错时返回的部分数据长度都和 Go 一致 |
| `std.archive.tar` `std.archive.zip` | archive/tar、archive/zip | tar 读写，写出的字节和 Go 相同（USTAR/PAX/GNU 自动选择、稀疏文件读取）；zip 读写，Store 方式的字节和 Go 相同 |
| `std.image.png` | image/png | 全部颜色类型、位深、Adam7 的解码；不压缩时编码结果和 Go 字节相同，其他压缩级别下滤波行数据相同 |
| `std.image.color` | image/color、image/color/palette | 全部颜色类型与模型转换、YCbCr/CMYK 互转、调色板（Plan9、WebSafe），与 Go 逐值相同 |
| `std.image` | image | Point / Rectangle 全部几何方法；RGBA、RGBA64、NRGBA、NRGBA64、Alpha、Alpha16、Gray、Gray16、CMYK、Paletted、YCbCr、NYCbCrA、Uniform；图像是值，sub_image 不共享像素 |
| `std.image.draw` | image/draw | draw / draw_mask（OVER、SRC）、Floyd–Steinberg；结果与 Go 的快速路径和通用路径逐字节相同 |
| `std.image.gif` | image/gif | 解码 / 全帧解码 / 配置、编码 / 动画编码（Plan9 + 抖动量化），字节与报错均与 Go 相同；不支持自定义 Quantizer / Drawer |
| `std.image.jpeg` | image/jpeg | 基线、渐进式、重启间隔、全部采样比（含 flex）、灰度 / YCbCr / RGB / CMYK / YCCK 解码，编码按质量 1..100，与 Go 1.27 新 DCT 逐字节相同 |
| `std.log.slog` | log/slog | Level 及解析、Value / Attr、TextHandler / JSONHandler（ReplaceAttr、with_attrs / with_group、空组规则）、Logger；没有反射，Any 值显式给出文本和 JSON 两种写法 |
| `std.regexp.syntax` | regexp/syntax | Parse（全部标志、错误码与错误片段）、Regexp.String / Simplify / Equal / MaxCap / CapNames、Compile 与 Prog.String / Prefix / StartCond、Inst.MatchRune 等；Go 测试里的 427 个模式逐字节一致 |
| `std.kite.token` | （对标 go/token） | Kite 记号种类 KINDS、keywords / is_keyword、operators / is_operator、is_identifier、position（字符下标 → 行列） |
| `std.kite.scanner` | （对标 go/scanner） | Kite 词法分析 lex → List<Tok>，就是 kitec 用的那一份 |
| `std.kite.ast` | （对标 go/ast） | Kite 强类型语法树 Node / K；kname、kids、with_kids、dump；类型检查后表达式节点带 Node.t |
| `std.kite.parser` | （对标 go/parser） | parse / parse_type，就是 kitec 的解析器，报错 `ERR 行:列 信息` |
| `std.kite.format` | （对标 go/format） | fmt（带安全网）/ format_source，就是 kitec fmt |
| `std.kite.types` | （对标 go/types） | check_file / check_source → Info（类型化 IR、顶层签名、局部变量类型、警告）或 Diag 列表；sig、locals_at、type_text |
| `std.kite.doc` | （对标 go/doc） | from_file / from_source → Module（模块文档 + pub 声明及其注释、impl 方法）；synopsis、text、markdown |
| `std.kite.version` | （对标 go/version） | 语义化版本 parse_version / vcmp / compare / is_valid，约束 parse_constraint / satisfies / sat_str（规则同 Cargo） |
| `std.go.token` | go/token | 记号常量与 String / Precedence / Lookup、IsIdentifier 等；Position、File（行表、行指令信息、SetLinesForContent、MergeLine）、FileSet；File / FileSet 是值，扫描后用 set_file 写回 |
| `std.go.scanner` | go/scanner | Scanner（分号自动插入、注释、行指令 //line 与 /*line*/、全部错误信息）、Error / ErrorList；错误收集在 s.errors，不用回调 |
| `std.go.ast` | go/ast | 节点是一个枚举 Node（变体名同 Go 类型名，nil 是 Nil）；pos / end、comment_text、children（Walk 顺序）、preorder、inspect（折叠）、fprint（ast.Fprint 格式，含 NotNilFilter）、Object / Scope；Ident.obj 是 File.objects 的编号。没有 CommentMap、Filter、MergePackageFiles |
| `std.go.parser` | go/parser | parse_file / parse_expr_from / parse_expr，全部模式常量；错误恢复、10 个错误放弃、//go:build 推出 GoVersion、标识符解析（resolver）都同 Go；不打印 TRACE，没有 ParseDir |
| `std.go.doc.comment` | go/doc/comment | parse（零值 Parser：标题、旧式标题、列表、代码块、链接定义、自动链接、[pkg.Name] 文档链接）与 comment（零值 Printer.Comment）；1158 个用例逐字节一致。没有 Text / Markdown / HTML，LookupPackage / LookupSym 不可配置 |
| `std.go.printer` | go/printer | fprint / fprint_bytes，Config 的 RawFormat、TabIndent、UseSpaces、SourcePos、Indent；注释穿插、文档注释重排、//go:build 与 // +build 合并、对齐（tabwriter）和 trimmer 都同 Go。没有 CommentedNode |
| `std.go.format` | go/format | source（整文件或声明 / 语句片段，等同 gofmt）、node；ast.SortImports 在 std.go.ast 里（sort_imports，返回新的 FileSet 和 File） |
| `std.crypto.aes` `std.crypto.cipher` | crypto/aes、crypto/cipher | AES-128/192/256；CBC、CTR、CFB、OFB、GCM（通过 NIST 测试向量和 Go 自带的 GCM 测试向量） |
| `std.encoding.xml` | encoding/xml | `Decoder`（`token` / `raw_token`，命名空间、Strict/AutoClose、实体、带行号的语法错误）、`Encoder`（缩进）、`escape_text`；用元素树 API（`parse` / `marshal`）代替依赖反射的 Marshal/Unmarshal |

**和 Go 不同的地方**：除了第五批列出的几点，还有：Kite 的字符串必须是合法 UTF-8，所以 Go 会保留原始非法字节的地方，Kite 会换成 U+FFFD；没有 `io.Reader` / `io.Writer`，输入一次性给全，输出整体返回；没有反射和接口值，`fmt` 用 `Arg` 枚举传参。每个模块开头的注释里写有详细差别。

**其他**：`std.hash.crc32` 的表改成常量，不再每次调用都重建；移植须知见 `docs/go-std-porting.md`。

### 标准库第五批：对齐 Go，与真正的 Go 逐字节对拍（0.94）

每个包都写一对测试程序：`tests/go/NAME.go` 用真正的 Go 1.23 运行，输出存进 `tests/go/NAME.expected`；
`examples/go_NAME.kite` 做同样的事。两边输出必须逐字节相同（`sh tools/go_check.sh`，已接入 `run_tests.sh`）。
另外，这些示例在树解释器、虚拟机、原生后端三个引擎上的输出也一致。目前共 14 组对拍。

| 模块 | 对应 Go | 内容 |
|---|---|---|
| `std.math.bits` | math/bits | 全部函数：`leading_zeros*` `trailing_zeros*` `ones_count*` `rotate_left*` `reverse*` `reverse_bytes*` `len*` `add*` `sub*` `mul*` `div*` `rem*`；另外提供无符号 64 位的辅助函数 `wadd` `wsub` `wmul` `shr` `ult` `udiv` `urem` `ustr` |
| `std.math.rand` | math/rand/v2 | `new_pcg` `new_chacha8`，种子相同时序列和 Go 完全一样；`int_n` `int64_n` `uint64_n` `int32_n` `uint32_n` `float64` `float32` `perm` `shuffle` `norm_float64` `exp_float64`（ziggurat 表和 Go 相同）`new_zipf` / `zipf_uint64`；`seeded()` 按时间取种子 |
| `std.crypto.md5` `std.crypto.sha1` `std.crypto.sha512` | crypto/md5、sha1、sha512 | `sum` / `digest`；sha512 包括 384、512/224、512/256；`std.crypto.sha256` 新增 `sum224` |
| `std.crypto.hmac` `std.crypto.subtle` | crypto/hmac、crypto/subtle | `hmac.sum(摘要函数, 块大小, key, msg)`、`hmac.equal`；`constant_time_*` 和 `xor_bytes` |
| `std.hash.crc64` `std.hash.adler32` | hash/crc64、hash/adler32 | ISO 和 ECMA 多项式、`make_table`、`update`；`std.hash.crc32` 新增 `make_table`，以及 Castagnoli 和 Koopman 多项式 |
| `std.encoding.base32` | encoding/base32 | `std_encoding` `hex_encoding` `new_encoding` `with_padding`，解码规则和报错位置都和 Go 相同 |
| `std.encoding.base64`（扩充） | encoding/base64 | `Encoding` 类型：`std_encoding` `url_encoding` `raw_std_encoding` `raw_url_encoding` `.strict()` `.with_padding()` `.encoded_len()` `.decoded_len()` |
| `std.encoding.ascii85` `std.encoding.pem` `std.unicode.utf16` | encoding/ascii85、encoding/pem、unicode/utf16 | 全部函数 |
| `std.html` | html | `escape_string` / `unescape_string`；带完整的 HTML5 实体表（2231 个，由 `tools/gen_html_entities.py` 生成） |
| `std.strings`（补全） | strings | `index` `last_index` `index_any` `index_func` `index_rune` `split`（Go 语义）`split_after(_n)` `fields_func` `cut_prefix` `cut_suffix` `trim` `trim_left` `trim_right` `trim_func` `trim_space` `replace` `replace_all` `to_upper` `to_lower` `to_title` `compare` `map_rune`，以及 `Builder` 和 `new_replacer`；`title` 和 `equal_fold` 改成 Go 的规则 |
| `std.bytes` `std.unicode.utf8`（补全） | bytes、unicode/utf8 | bytes 包几乎全部函数，加上 `Buffer`；无效 UTF-8 字节按 U+FFFD、宽度 1 处理，和 Go 相同。utf8 新增 `decode_rune` `decode_last_rune` `full_rune` `valid_rune` `rune_count_bytes` 等 |
| `std.sort` `std.cmp` `std.maps` | sort、cmp、maps | 见各模块开头的说明 |
| `std.errors` | errors、fmt.Errorf 的 %w | `new` `wrap` `errorf` `join` `unwrap` `has`（Go 的 Is）`find`（Go 的 As） |
| `std.container.list` `std.container.ring` | container/list、container/ring | 不可变版本：修改操作都返回新值。元素用编号引用 |
| `std.text.tabwriter` | text/tabwriter | 算法逐行移植，所有标志位都支持（HTML 过滤、转义、右对齐、丢弃空列、制表符缩进、调试竖线） |
| `std.math.big` | math/big.Int | `BigInt`：四则运算、`quo`/`rem` 和 `div`/`modulo`、`exp`（含模逆）、`gcd_ext`（系数和 Go 相同）、`mod_inverse`、`sqrt`、移位、补码位运算、`text`（2..62 进制）、`parse`（含前缀和 `_`）、`probably_prime`、`binomial` |

**和 Go 不同的地方**（Kite 语言本身决定的）
- 值不可变：有状态的对象（`Rand`、`Buffer`、`Builder`、`LinkedList`、`tabwriter.Writer`）每次操作都返回新值。
- 字符串按字符下标：`strings.index` 等返回的是字符位置。纯 ASCII 时和 Go 的字节位置相同。
- 有些名字和 Kite 的关键字或内建名字冲突，所以改了名：`errors.is` 叫 `has`，`big.Int` 叫 `BigInt`，`Mod` 叫 `modulo`，`Abs` 叫 `abs_value`，`list.List` 叫 `LinkedList`，`strings.Map` 叫 `map_rune`。
- `sort.Slice` 用的是稳定排序，相等元素的先后顺序可能和 Go 不同，其余结果相同。

**顺带修复的问题**
- 语句位置的 `loop` 里如果没有 `break`，类型现在是 Never，所以可以放在函数末尾（以前会报“返回值期望 Int，实际是 Unit”）。Python 版检查器和 kitec 同时修改。
- 原生后端的 `replace(s, "", x)` 以前直接返回原串，现在和虚拟机一样，在每个字符前和末尾都插入 x。
- `unicode` 的大小写映射补上了 İ 的小写、带下标 iota 的希腊字母，以及双字母合字和格鲁吉亚文的标题大写。现在 0 到 U+1FFFF 范围内和 Go 完全一致。

### 自举第 4 步：不动点（0.93）

- 新增 `kitec build FILE prelude.kite [-o 输出] [--emit-c C 文件] [--no-kir]`：kitec 不依赖 Python，直接把 Kite 程序编译成可执行文件，产出的 C 与 `kite build` 逐字节相同；extern 声明的库自动链接
- 新增 `tools/kitec_bootstrap.sh`，做三阶段自举：stage0（Python 版编出的 kitec）→ stage1（kitec 编译自己）→ stage2（stage1 编译自己）。三份 C 逐字节相同（190428 行），stage2 编出来的示例程序和虚拟机输出一致
- `run_tests.sh` 新加两项：第 3 步的五套对拍（lower / bc / c / kir / ck，外加 KIR fuzz）和三阶段自举

### 自举第 3d 步：KIR（0.92）

- 新增 `kitec/src/kir.kite`：kir.py 的移植。带类型的语法树降级成 KIR（寄存器式、基本块，标量 `i64` / `f64` / `b1` 不装箱），覆盖 Lower 的全部构造（模式、for 计数循环、闭包与 upvalue 单元、defer、spawn / race / select、局部 fn、trait 默认方法、`?`、字段 / 下标赋值），以及 collect 的不动点和三个优化（常量传播与折叠、CFG 化简、死代码删除）。折叠在 64 位 Int 上做溢出检查，结果和 Python 的任意精度版本一致
- 检查器把每个表达式节点的类型记进 `C.ts`（按 `_id`，Python 版的 `e._t`）；trait 默认方法按实现类型留快照 `C.tsnap`（`_tsnap`）。检查器合成又会被检查的节点（`spawn … .await` 的 `__await` 调用、常量泛型的隐藏实参、结构体更新补出的 `base.f` 和临时变量块）现在也带编号，类型能记下来。类型查询走 `check.prn`（prune 加关联类型投影规约，和 Python 版 prune 相同）
- `kitec/src/cgen.kite` 加上 KIR → C（cnames / c_sig / emit_c / emit_wrapper）：`cgen.generate_k` 先输出 KIR 函数，再输出字节码 Proto，已进 KIR 的函数换成装箱包装
- 新增 `kitec kir FILE prelude.kite`（`src/kirdump.kite`）与 `tools/kir_dump.py`、`tools/kitec_kir_check.py`（190 个一致，0 个不一致）、`tools/kitec_kir_fuzz.py`
- 新增 `kitec ck FILE prelude.kite`：走 KIR 的 C 源码，与 `tools/c_dump.py --kir`（和 `kite build` 默认相同）逐字节比较：`tools/kitec_ck_check.py`（190 个一致，0 个不一致）、`tools/kitec_ck_fuzz.py`
- 顺带验证了第 4 步的不动点：`kitec ck kitec/src/main.kite` 产出的 C 编译成 kitec2，kitec2 再编译自己，两轮 C 逐字节相同
- 在 aarch64 上用 `zig cc` 时，ziglang 会把 CPU 探测成带 SVE 等扩展的型号，个别虚拟化环境里运行会报 Illegal instruction；设 `CC="zig cc -mcpu=generic"` 即可

### 自举第 3c 步：字节码到 C（0.91）

- 新增 `kitec/src/cgen.kite`：native.py 的 Gen 移植（不走 KIR，相当于 `KITE_NO_KIR=1`）。栈深度分析、每个 Proto 一个 C 函数、已知顶层函数直接调用、模式匹配函数、方法表、枚举成员、extern 包装（C 结构体、回调跳板、可变参数变体），输出包含 runtime/kite_rt.c
- 字节码全局表细分值的种类（内建、变体构造器、单元变体、std 模块及成员、extern），对应 Python 版 globals 里的对象
- 检查器把可变参数 extern 的各个变体以 `_va` 字段挂在声明上（Python 版的 `_va_variants`）；模块加载器记录源文件表（`kite.SOURCES`）
- 新增 `kitec c FILE prelude.kite`、`tools/c_dump.py`、`tools/kitec_c_check.py`（188 个一致，0 个不一致）、`tools/kitec_c_fuzz.py`

### 自举第 3b 步：字节码编译器（0.90）

- 新增 `kitec/src/bc.kite`：vm.py 的 FnComp / Compiler 移植，把检查后的语法树编译成字节码（槽位分配、Lua 式 upvalue、编译期栈深度、break/continue 弹栈、defer、或模式共用槽位、select 改写）
- 全局表按 native.Gen 读取的顺序记录：启动时的内建名在前，重新定义的名字保持原位置；方法表、结构体字段、枚举变体一并输出
- 新增 `kitec bc FILE prelude.kite`、`tools/bc_dump.py`（按 Gen 的 Proto 顺序输出，APPEND_L 关闭，和原生后端一致）、`tools/kitec_bc_check.py`（187 个一致，0 个不一致）、`tools/kitec_bc_fuzz.py`
- 暂未支持：内建 std 模块的 `use std.x.*`（输出 UNSUP）

### 自举第 3a 步：检查后的语法树（0.89）

- 第 3 步（C 代码生成）的路线：先让 kitec 产出和 Python 版完全一样的"类型检查后语法树"（3a），再移植字节码编译器 vm.Compiler（3b）、字节码到 C 的 native.Gen（3c）和 KIR（3d），每一步都逐字节对拍
- Python 版检查器会就地改写语法树。kitec 的语法树是不可变值，所以给每个节点编号（`_id`），检查时把改写记在一张表里，检查完统一套用
- 已移植的改写：位运算和集合运算改成内建调用，复合赋值展开，运算符重载改成方法调用，`~` 改成 `__bnot`，`as` 转换，记录变体字面量改成构造调用，结构体更新展开（临时变量 `__updN`），`spawn/race … .await` 插入 `__await`，模式里的 `..` 展开，`sort/min/max` 改用 `__*_cmp`，常量泛型的隐藏实参和隐藏形参，关联常量 `T.N` 改成隐藏参数名，extern 的 `ext_tys` 描述和可变参数变体名，impl 继承类型的隐含约束，去掉 `.<T>`
- 顺带修正：kitec 的 prelude 项带 `prelude` 标记，lower_consts / lower_statics 生成的函数字段和 Python 版一致，`let … else` 的 else 块和第二个分支共用（模块解析后两处一致）
- 新增 `kitec lower FILE prelude.kite`、`tools/lower_dump.py`、`tools/kitec_lower_check.py`（186 个一致，0 个不一致）、`tools/kitec_lower_fuzz.py`（300 个变异体 0 个不一致）

### 自举第 2 步完成：有依赖的包（0.88）

- kitec 读取 `kite.lock`（由 `kite get` 生成），按 `path+` 和仓库来源找到依赖目录，所有传递依赖共用一张表，和 Python 版 `dependency_packages` 一样
- `use 依赖包.子模块` 和 `use 依赖包`（`src/lib.kite`）；依赖包缺 `lib.kite` 时报错和 Python 版相同
- 没有锁文件或依赖还没安装时输出 UNSUP，安装仍由 Python 包管理器负责
- 新增 `tools/pkg_fixture.sh`：搭一个临时仓库，发布 mathx 和 geom，再对 app 和 geom 运行 `kite get`。7 个包文件对拍全部一致
- 类型检查器移植完成：仓库内 186 个一致，0 个不一致。剩下 5 个 UNSUP 是没有锁文件的示例包，用上面的脚本搭好后对拍一致

### 自举第 2 步第四批：关联类型、关联常量、常量泛型（0.87）

- 关联类型：
  - 投影 `T.Item` 和 `Self.Item`：在 prune 里规约，泛型参数上规约为 `S.Item` 这样的刚性类型，具体类型按 impl 规约
  - 暂时没法规约的投影先推迟合一，最后报"无法确定 … 是否一致"
  - impl 里关联类型缺失、多写、写在非 trait impl 里，以及关联类型的约束
- 关联常量：
  - 移植了 `lower_consts`：impl 和 trait 里的 const 改写为 `类型.N()` 和 `__c_N` 方法
  - 泛型参数上的 `T.N` 作为隐藏参数传入
  - 报错："缺少关联常量"、"类型和 trait 声明不一致"、"无法确定 … 取不到它的关联常量"
- 常量泛型与定长数组：
  - `[T; N]`、`[x; N]`、数组字面量的长度
  - 数组可以当列表用，列表不能当数组用
  - `f.<2>()` 显式指定，`impl<const N: Int>`
  - 调用点的常量实参在类型求解后检查（推不出来、不是整数、当值传递时报错）
- `head` 把 `[T; N]` 当作 List：迭代、下标、内建函数重载都按 List 处理
- 对拍：186 个一致，0 个不一致。单文件程序已全部覆盖，只剩 5 个有依赖的包输出 UNSUP。400 个变异的模糊测试，0 处不一致。

### 自举第 2 步第三批：unsafe、extern、async fn、dyn（0.86）

- `kitec/src/check.kite` 新增以下检查，报错文字与 Python 版相同：
  - `unsafe fn`、`unsafe { … }` 块、`unsafe fn` 类型：普通函数可以当 unsafe fn 用，反过来不行
  - 取 unsafe 函数的值是安全的，调用这个值必须在 unsafe 里
  - unsafe 方法，以及它和 trait 声明是否一致
  - std.ptr 里的 unsafe 函数（直接 use 和 `ptr.x` 两种写法）
- `extern fn`：
  - 参数和返回值只能是 Int/Float/Bool/Str/Ptr、I32/U8/F32 等 C 宽度、字段全是这些类型的结构体，或 C 回调
  - 可变参数 extern 只能直接调用，多出来的参数按实际类型检查
- `async fn`：直接检查 `Future { run: () => body }` 里闭包的内层类型，和 Python 版一样
- `dyn Trait`：
  - 隐式转换为 dyn 时检查实际类型是否实现了全部 trait，dyn 也可以向上转换
  - let 标注、列表元素、if 和 match 分支都能把期望的 dyn 类型往下传
- 对拍：176 个一致，0 个不一致。还剩 15 个输出 UNSUP：7 个用关联类型或关联常量，3 个用常量泛型，5 个是有依赖的包。模糊测试在开发时跑了 300 个变异，0 处不一致。

### 自举第 2 步第二批：kitec 模块系统，kitec 能检查自己（0.85）

- 新文件 `kitec/src/modules.kite`（约 1300 行），逐条移植 modules.py 的 Loader 和 Resolver：
  - 多文件程序、包内模块（`a.kite` / `a/mod.kite`、`lib.kite`）、Kite 写的 std 模块
  - 内联 `mod`、函数体里的 `mod` / `use`、`super`
  - pub / pub(pkg) / pub(mod) 可见性、循环引用检测、变体重名检查、顶层 const 按依赖排序
  - 行号编码（文件序号 × 1000000 + 行）
  - 报错文字与 Python 版相同
- `kitec check` 改为经模块系统加载，和 `kite check` 一样。kitec 能检查自己的全部源码，native 约 0.8 秒（Python 版约 1.1 秒），结果逐字节一致。
- 对拍范围扩大到 examples、tests、std、示例包和 kitec/src：154 个一致，0 个不一致。模糊测试在开发时又跑了 600 个变异，没有一处不一致。
- 有依赖的包（kite.toml 里有 [dependencies]）仍由 Python 版的包管理器解析，kitec 输出 UNSUP。
- 修复（Python 版也改了）：模块里的结构体更新 `P { x: 1, ..base() }`，`..` 后面表达式里的名字之前没有改名，引用模块内的函数会报“未定义的名字”。`examples/inline_mod.kite` 加了这个用例。

### 自举第 2 步第一批：kitec 类型检查器核心（0.84）

- 新文件 `kitec/src/tys.kite`（类型表示）和 `kitec/src/check.kite`（约 3000 行，逐条移植 typecheck.py 的 Checker）：合一、泛型函数和类型、trait 约束与超 trait、impl 与方法、静态方法改写、运算符重载、类型别名约束、模式检查与穷尽性（Maranget 算法）、spawn / race / select / `.await`、`.<…>` 显式泛型实参、错误恢复。报错文字与 Python 版相同。
- 检查器状态 `C` 按值传递。类型变量的绑定存在持久化的替换表里，出错时 `F` 带着出错那一刻的状态，所以错误恢复后的推断结果也和 Python 版一样。
- 内建函数签名表 `kitec/src/builtins.kite` 由 `tools/gen_builtins.py` 从 typecheck.py 生成。
- 用法：`kitec check FILE prelude.kite`。输出每个错误一行（按行号排序），再输出每个顶层函数推断出的签名。
- 对拍：`tools/tc_dump.py` 输出 Python 版的结果。`tools/kitec_tc_check.py` 比较全部单文件程序：97 个一致，0 个不一致。`tools/kitec_tc_fuzz.py` 随机替换 token，比较报错路径：开发时跑了 1,500 个变异，没有一处不一致。
- 还没移植：关联类型和关联常量、常量泛型和定长数组、dyn、extern、unsafe、async fn、内联 mod，以及多文件程序（模块加载）。碰到这些时输出 UNSUP，共 35 个文件。
- 三个引擎都能跑 kitec check，检查 tests/bad.kite 的输出相同。

### 自举第 1 步：kitec 词法 + 语法分析（0.83）

- 新目录 `kitec/`（一个 Kite 包）：`src/lexer.kite`、`src/parser.kite` 逐条移植了 kite.py 的 `lex` 和 `Parser`，共约 2900 行 Kite。所有语法糖和报错文字都和 Python 版一样。解析器状态 `P` 按值传递，每个解析函数返回 `(新状态, 结果)`。
- 语法树 `ast.V` 和 Python 版一样是动态结构：节点、元组、记录字段表、限定名、大整数各有对应。
- 对拍：`tools/ast_dump.py` 输出规范文本，`tools/kitec_check.sh` 对全部 198 个源文件（含 kitec 自己）逐字节比较，`tools/kitec_fuzz.py` 随机删改 token 再比较输出（含报错）。开发时跑了 1,500 个变异，没有一处不一致。
- 三个引擎都能跑 kitec，解析 parser.kite 时输出一致：native 约 0.1 秒（Python 版 0.2 秒），VM 约 8 秒，tree 约 18 秒。
- 详见 `kitec/README.md`。下一步：移植类型检查器。

### tree / VM 的 push 均摊 O(1)，deflate 动态 Huffman（0.82）

- **原地追加**：`xs = push(xs, e)`、`xs += e`、`xs = xs + e`（`xs` 是 var 局部变量）在 tree 和 VM 引擎里改成原地追加，前提是运行时这个列表没有别的引用（用 `sys.getrefcount` 判断）。只要有别名（另一个变量、结构体字段、Map 里的值、之前保存的快照），就照旧复制，所以值语义不变。VM 新增指令 `APPEND_L`；`e` 里有赋值时不做优化。20 万次 push：19 秒 → 0.3 秒。
- **deflate**：先用 LZ77 生成记号（level 4 以上惰性匹配，哈希链长度 level*8，level 9 为 128），再分别算出固定 Huffman、动态 Huffman 和存储三种编码的位数，取最短的。Huffman 码长超过 15（码长码超过 7）时，把频率减半重算。1.1 MB 文本压缩后从 207 KB 降到 145 KB（zlib 默认 122 KB）。
- **inflate 提速**：一次读 3 个字节取位，再用 9 位快查表解码。VM 解压 100 KB：15 秒 → 1.5 秒。native 解压 1.1 MB 约 0.07 秒。
- 测试：`tests/push_alias.kite`，覆盖别名、快照、结构体字段、Map 里的值、闭包；tree 和 VM 都要在 20 秒内跑完。

### 标准库第四批：对齐 Go（0.81）

| 模块 | 对应 Go | 主要内容 |
|---|---|---|
| `std.compress.flate` | compress/flate | `compress(bs, level)`（0 只存储；1..9 用 LZ77 哈希链和固定 Huffman 编码）、`decompress`（存储块、固定 Huffman、动态 Huffman 都支持）、`inflate_at`、`compress_str` / `decompress_str` |
| `std.compress.gzip` | compress/gzip | `compress` / `compress_level` / `compress_str`、`decompress` / `decompress_str`（校验 CRC32 和长度，支持 FEXTRA/FNAME/FCOMMENT/FHCRC 和多个成员）、`header` |
| `std.compress.zlib` | compress/zlib、hash/adler32 | `compress` / `compress_level` / `compress_str`、`decompress` / `decompress_str`（校验头部和 Adler-32）、`adler32` |
| `std.text.template` | text/template | 取值、管道、`if/else if/else`、`range`（数组、对象、整数，`break`/`continue`）、`with/else with`、变量声明和赋值、`define/template/block`、注释、`{{-`/`-}}` 去空白；内建 `and or not len index slice eq ne lt le gt ge print printf println html urlquery js`；`add_func` 加自定义函数；`sprintf` 也可以单独用 |

- 和 Python 的 zlib/gzip 互相对拍：Python 压缩的（level 0/1/9、多个成员）Kite 都能解开，Kite 压缩的 Python 也都能解开。
- 压缩只用固定 Huffman，压缩率比 zlib 的默认设置低（1.1 MB 的文本：Kite 207 KB，zlib 122 KB）。native 引擎压缩 1.1 MB 约 3.3 秒，解压约 0.1 秒。tree 和 VM 引擎里 `push` 是 O(n)，只适合几 KB 的数据。
- 模板数据用 `json.Json` 表示。与 Go 的差别：`len` 按字符数算（Go 按字节），浮点数按 Kite 的格式输出。
- 顺带修复：`__fmt` 收到非法的格式说明时，VM 和 tree 引擎会抛 Python 异常、native 会静默忽略；现在三个引擎都报“无效的格式说明 {:…}”。
- 示例：`examples/gostd4.kite`。

### 标准库第三批：对齐 Go（0.80）

| 模块 | 对应 Go | 主要内容 |
|---|---|---|
| `std.unicode` | unicode | `is_letter` `is_upper` `is_lower` `is_title` `is_digit` `is_number` `is_mark` `is_punct` `is_symbol` `is_space` `is_control` `is_graphic` `is_print`、`in_class(名字, c)`、`table(名字)`、`in_table`、`to_upper` / `to_lower` / `to_title`、`upper` / `lower`（整个字符串）、`VERSION` |
| `std.internal.unicode_tables` | （数据） | 由 `tools/gen_unicode.py` 生成（Unicode 15.0）：34 个一般分类、11 种文字、简单大小写映射，用 base36 差分压缩，约 27 KB |
| `std.regexp`（扩充） | regexp | `\pL` `\p{Lu}` `\p{Han}` `\PL` `\p{^Greek}` `\p{Any}`，字符类里也能用；`(?i)` 对单个非 ASCII 字符按 Unicode 大小写匹配 |
| `std.net.url` | net/url | `parse` / `to_str`（`URL` 含 scheme、opaque、user、password、host、path、raw_query、fragment）、`hostname` `port` `is_abs` `query` `first`、`parse_query` / `encode_query`、`query_escape` / `query_unescape`、`path_escape` / `path_unescape`、`resolve` / `resolve_str`（RFC 3986 5.2，标准里的例子全部通过）、`join_path`、`remove_dot_segments` |
| `std.encoding.binary` | encoding/binary | `big_*` / `little_*` 的 `put_u16/u32/u64`、`get_u16/u32/u64`、`get_i16/i32`、`put_uvarint` / `put_varint`（zigzag）、`read_uvarint` / `read_varint`、`format_u64` |

- 文字（Han、Hiragana、Latin 等）是用 Unicode 区块和“已分配码点”取交集近似出来的，因为 Python 没有 Scripts.txt；一般分类是精确的。
- `is` 是关键字，所以按名字判断分类的函数叫 `in_class`。
- u64 用 Int 的 64 位补码表示，2^63 以上的值显示为负数，`format_u64` 输出它的无符号十进制。`<<` 按 64 位回绕，三个引擎一致。
- 示例：`examples/gostd3.kite`。

### 标准库第二批：对齐 Go（0.79）

| 模块 | 对应 Go | 主要内容 |
|---|---|---|
| `std.math`（扩充） | math | 新增 `tan` `asin` `acos` `atan` `atan2` `sinh` `cosh` `tanh` `asinh` `acosh` `atanh` `exp` `exp2` `expm1` `log2` `log10` `log1p` `cbrt` `hypot` `fmod` `copysign` `remainder` `round` `trunc` `is_nan` `is_inf` `signbit` `gcd` `lcm`，以及常量 `tau` `phi` `sqrt2` `ln2` `ln10` `inf` `nan` `max_float` `smallest_float` `max_int` `min_int` |
| `std.os` | os、path/filepath | `stat` / `lstat`（返回 `FileInfo`）、`read_dir`、`walk`、`walk_filter`、`glob`、`remove_all`、`mkdir_all`、`read_bytes` / `write_bytes` / `append_bytes`、`copy_file`、`chmod`、`getwd`、`chdir`、`hostname`、`getpid`、`getenv` / `setenv` / `unsetenv` / `environ`、`temp_dir`、`home_dir` |
| `std.time`（扩充） | time | `DateTime` 拆分（`utc` / `local` / `in_offset`）、`date` / `to_ms` 组装、`add_date`、`truncate`、Go 布局的 `format` / `format_local` / `parse`（常量 `RFC3339` `DATETIME` `DATE_ONLY` `TIME_ONLY` `RFC1123`）、`format_duration` / `parse_duration`，以及 `SECOND` `MINUTE` `HOUR` `DAY` |
| `std.bufio` | bufio | 不可变 `Reader`（`read_line` `read_until` `read_word` `read_char` `peek` `read_rest`）、`Writer`（`write` `write_line` `flush_to` `flush_append` `flush_stdout`）、`scan_lines` `scan_words` `stdin_lines` |
| `std.regexp` | regexp（RE2 语法） | `compile` / `must_compile`、`is_match` `matches` `find` `find_index` `find_all` `find_all_index` `captures` `captures_all` `named` `replace_all`（`$1` `${name}`）、`replace_all_literal` `replace_all_func` `split` `quote_meta` |

- **regexp** 用 Pike VM 实现，匹配时间和 输入长度 × 模式长度 成正比，`(a+)+$` 这类模式不会回溯爆炸。语义是最左优先，和 Go 相同。支持字符类、POSIX 类、`\d \w \s \b`、命名组，以及 `(?i)` `(?s)` `(?m)` 标志。`(?i)` 只对 ASCII 字母生效，暂不支持 `\p{…}` Unicode 类。
- **math 改为 IEEE 754 语义（行为变化）**：和 Go 一样，`sqrt(-1.0)` 得到 `nan`，`log(0.0)` 得到 `-inf`，溢出得到 `inf`，不再报 `math domain error`。树解释器和 VM 通过 ctypes 调用 C 的 libm，所以三个引擎的结果逐位相同。之前 VM 和树解释器遇到 `pow` 溢出会抛出 Python 异常，这次一并修正。`floor` / `ceil` / `round` / `trunc` 返回 Int，超出范围时报 `整数溢出`。
- 新增运行时原语：`__fs_stat` `__getcwd` `__chdir` `__chmod` `__env_set` `__env_unset` `__environ` `__hostname` `__getpid` `__read_bytes` `__write_bytes` `__tz_offset`。
- 时间支持 1970 年以前的日期；布局里没有时区时按 UTC 解析，`MST` 只认 `UTC` 和 `GMT`。
- 示例：`examples/gostd2.kite`（三个引擎加 GC 压力，输出一致）。

### 标准库第一批：对齐 Go（0.78）

新增 12 个模块，并扩充了 `std.strings`。全部用 Kite 编写，树解释器、虚拟机、原生后端输出一致（`examples/gostd.kite`）。

| 模块 | 对应 Go | 主要函数 |
|---|---|---|
| `std.strconv` | strconv | `itoa` `atoi` `parse_int_base` `format_int` `parse_bool` `format_bool` `format_float` `quote` `unquote` |
| `std.strings`（扩充） | strings | `find_last` `find_any` `contains_any` `fields` `split_n` `cut` `equal_fold` `title` `trim_chars` `trim_left_chars` `trim_right_chars` `trim_prefix` `trim_suffix` `replace_n` `map_chars` `find_func` `is_upper` `is_lower` `is_alnum` |
| `std.slices` | slices | `index` `index_func` `binary_search` `binary_search_func` `is_sorted` `insert` `delete` `replace` `compact` `unique` `equal` `chunk` `windows` `flatten` `repeat` `partition` `group_by` `max_by` `min_by` |
| `std.path` | path | `clean` `join` `base` `dir` `split` `ext` `stem` `is_abs` `match_glob` |
| `std.encoding.hex` | encoding/hex | `encode` `encode_bytes` `decode` `decode_str` `dump` |
| `std.encoding.base64` | encoding/base64 | `encode` `decode` `decode_str` `url_*` `raw_url_*` |
| `std.encoding.csv` | encoding/csv | `parse` `parse_with` `format` `format_with` `parse_records` |
| `std.crypto.sha256` | crypto/sha256、crypto/hmac | `sum` `sum_bytes` `digest` `hmac` `hmac_bytes` |
| `std.hash.crc32` | hash/crc32 | `checksum` `checksum_bytes` `update` |
| `std.hash.fnv` | hash/fnv | `hash32` `hash64` `hex64`（FNV-1a） |
| `std.flag` | flag | `str` `int` `bool` 声明，`parse` `get` `get_int` `get_bool` `is_set` `args` `usage` |
| `std.log` | log | `info` `warn` `error` `fatal`，`new` / `with_level` 加 `linfo` 等 |
| `std.container.heap` | container/heap | 不可变配对堆：`min_heap` `max_heap` `new` `insert` `peek` `pop` `size` `drain` `from_list` |
| `std.unicode.utf8` | unicode/utf8 | `bytes` `decode` `valid` `byte_len` `rune_count` `rune_len` `encode_rune` `runes` `from_runes` |

- 二进制数据用 `List<Int>`（0 到 255）表示；字符串按 UTF-8 字节处理。
- 新增三个运行时原语，三个引擎都实现了：`__bytes`（Str 转 UTF-8 字节）、`__from_bytes`（严格校验，不合法时为 None）、`__eprint`（写到标准错误）。
- 语言修正：泛型函数体里的类型标注可以用外层的类型参数（`var out: List<T> = []`）。
- 修正：被导入文件里的语法错误现在报那个文件自己的路径和行号，之前错报成主文件。

### LSP 签名提示（0.77）

- `textDocument/signatureHelp`：输入 `(` 或 `,` 时显示函数签名，并高亮当前参数。会跳过字符串和注释，嵌套调用取最里层；在列表、代码块或结构体字面量里不弹。
- 用户定义的函数和方法和跳转到定义用同一套解析（跨文件 `m.f(`、`use` 导入的名字都行），带上声明上方的文档注释；`x.m(…)` 不显示 `self`。
- 内建函数和 `std` 模块函数（`push(`、`fs.read(`）用类型检查器里的签名，有重载的（如 `get`）会列出全部。
- 正在输入时文件往往暂时解析不了：这时沿用上一次成功分析的符号，`use` 只解析 use 行，没成功解析过的文件按文本找 `fn name(` 声明。悬停和补全也因此在输入中途保持可用。

### LSP 查找所有引用（0.76）

- `textDocument/references`（VS Code 的 Shift+F12 / “查找所有引用”）：和重命名用同一套解析。局部变量只找同一作用域里解析到它的引用，被局部遮蔽的同名变量不算。pub 名字跨文件查找：`m.name`、`use` 语句里的名字，以及按名字导入后的引用。
- 比重命名多一点：`use geo.area as a` / `use geo.{area as a}` 时，别名本身和文件里对 `a` 的调用也算作 `area` 的引用。
- 支持 `includeDeclaration: false`，这时不列出定义处。
- `textDocument/documentHighlight`：光标停在名字上时，高亮本文件里的所有引用。

### 均摊 O(1) 的字符串拼接（0.75，原生后端）

Str 仍然是不可变值，`s = s + t` 在循环里不再是 O(n²)：

- Str 改成头部 `{len, s}` 加字节区。字节区前面记着容量 `cap` 和已占用长度 `fill`。新建的字符串把头部和字节区放在同一个对象里，GC 只扫描 `s` 这一个指针。
- `a + b` 时，如果 `a` 的尾部没人占（`fill == len`）且容量够，就用 CAS 抢下尾部、写入 `b`，新头部共享同一个字节区；否则复制一次并按 1.5 倍留余量。
- 交给 C 的字符串（文件路径、getenv、strtod、FFI 的 Str 参数等）通过 `CSTR`：尾部没人占时直接封住（之后对它的追加都会复制），被占了就复制一份带结尾 0 的副本。
- 树解释器和虚拟机不变。

| 测试程序 | 0.74 | 0.75 |
|---|---|---|
| 20 万次 `s + "ab"` + 5 万次 `t + "{i},"` | 5.1 s，累计分配 46.4 GB | 0.014 s，累计分配 12.2 MB |

### 均摊 O(1) 的 push 与拼接（0.74，原生后端）

List 仍然是不可变值，但 `push(xs, x)` 和 `xs + ys` 不再每次复制整个列表：

- 元素区里 `len` 之后的槽位从没写过。如果 `xs` 的下一个槽位还空着、容量也够，新列表就直接共享 `xs` 的元素区，只把新元素写进尾部。
- 同一个旧值再 push 一次时，它的尾部已经被占用，于是复制一份并按 1.5 倍扩容，之后在新列表上连续 push 仍然不复制。
- 尾部槽位用原子 CAS 抢占，所以多个线程在同一个基础列表上 push 也是安全的（`examples/list_share.kite` 在多线程 + GC 压力模式下测试）。
- 下标赋值仍然整份复制，因为它要改的是已经共享的元素。
- 树解释器和虚拟机的语义不变（仍然是 O(n) 复制）。

| 测试程序 | 0.73 | 0.74 |
|---|---|---|
| 10 万次 push + 5 万次 `ys + [i]` | 15.8 s，累计分配 95.9 GB | 0.013 s，累计分配 17.5 MB |
| 100 万次 push + 20 万次拼接（`tests/push_big.kite`） | 实际跑不完（逐次复制约需 8 TB） | 0.08 s，峰值堆 50.8 MB |

### GC 改进（0.73，原生后端）

- 尺寸分级从 14 级（≤ 2 KB）扩到 25 级（≤ 128 KB）。以前每个超过 2 KB 的对象（4 KB 的字符串、500 个元素的列表）都单独占 256 KB；大对象现在只按 4 KB 取整。
- 字符串和字符缓冲标为无指针对象：标记，但不扫描内容。List 的元素区按值扫描，只跟随指针类标签，Int / Float 不会被误当成指针。大对象只扫描实际请求的字节。
- 清除阶段只处理两张字节表（分配表、标记表），不再往每个空闲槽位里写链表指针；分配时用 memchr 找空位。
- 整块空闲的区块进入备用池（容量是下一轮回收前要分配的量），不再反复 munmap / mmap。
- 分配计数改按实际占用的槽位 / 页计算。先比较堆地址范围，再查区块哈希表。
- KITE_GC_STATS 额外打印停顿合计、最长停顿和清除耗时。

| 测试程序 | 0.72 | 0.73 |
| --- | --- | --- |
| 3000 个 4 KB 字符串轮换（分配 1.4 GB） | 1.31 s，峰值堆 8.3 GB | 0.13 s，峰值堆 30 MB |
| 2000 个 400 元素列表轮换（分配 30 GB） | 内存耗尽被杀 | 3.4 s，峰值堆 36 MB |
| bench_parallel | 219 ms | 159 ms |

### LSP 跨文件跳转与重命名（0.72）

- 跳转到定义：`geo.area(…)`、`use geo.area` 导入的 `area`、`use geo.{area as a}` 里的 `a`，还有 use 语句里的名字本身，都会跳到 geo.kite 里的定义。
- 重命名：pub 的顶层名字（fn、类型、枚举、trait、常量、extern fn）会连同包里所有 use 了这个模块的文件一起改，范围包括 `geo.area`、use 路径和分组里的 `area`、按名字导入或 `use geo.*` 之后没被局部变量遮蔽的引用。`use geo.area as a` 只改 use 里的 `area`，`a` 保持不动。导入文件自己也定义了同名顶层名字时，那个文件里的引用跳过不改。
- pub 类型的字段 / 方法：导入文件里的 `.name` 和结构体字面量里的 `name:` 一起改（按名字匹配，和单文件规则一样）。
- 不是 pub 的名字只改本文件。范围是包根（有 kite.toml 时）或文件所在目录；没打开的文件从磁盘读取。可以从定义处或任何一个引用处发起重命名。

### TLS / HTTPS（0.71）

```kite
let r = http.get("https://example.com/")                       // 系统根证书，校验证书链和主机名
let mine = net.TlsConfig { ca: "ca.pem", verify: true }        // 自定义 CA；verify: false 关闭校验（仅限测试）
http.request_with("GET", url, Map(), "", mine)
let c = http.client_with("https://api.local:8443", mine)       // keep-alive 同样可用
let l = http.listen_tls(8443, "cert.pem", "key.pem")?          // HTTPS 服务器，配合 serve / serve_with / serve_n
let conn = net.connect_tls("example.com", 443)?                // 底层：net.connect_tls / connect_tls_with / listen_tls
```

- 三套引擎都用系统的 OpenSSL 3：Python 引擎走 ssl 模块，原生程序在运行时 dlopen libssl/libcrypto，编译时不需要头文件，也不用链接。找不到时报"TLS 不可用"。
- 读写依旧是非阻塞的，等待时会让出调度。客户端握手在 connect_tls 里完成（会让出、可以被取消）；服务端握手推迟到连接第一次读写时进行，所以握手慢的客户端卡不住 accept 循环，握手失败只会断掉那一个连接。
- 错误文本在三套引擎里一致，例如"TLS 握手失败: self-signed certificate"、"无法加载证书 x.pem: No such file or directory"。
- `net.Listener` 新增了 `tls` 字段（0 表示普通 TCP）。

### extern 窄 C 宽度与可变参数（0.70）

```kite
type DivI = { quot: I32, rem: I32 }
extern "C" {
  fn abs(x: I32) -> I32
  fn div(a: I32, b: I32) -> DivI
  fn sqrtf(x: F32) -> F32
  fn snprintf(buf: Ptr, n: Int, fmt: Str, ...) -> I32
}
unsafe { snprintf(buf, 128, "%s 有 %.2f 米长", "风筝", 1.5) }
```

- extern 签名和 C 结构体字段可以写 I8/I16/I32/I64/U8/U16/U32/U64/F32/F64：在 Kite 里分别还是 Int/Float，跨边界时转换。参数超出范围会报"extern: v 超出 I32 的范围"；U64 按位传递，大于 I64 最大值的返回值会变成负数。
- `...` 只能写在 extern fn 的最后。多出来的参数可以是 Int（int64）、Float（double）、Bool（int）、Str、Ptr，按 C 的默认提升传递。每种实参类型组合在内部生成一个变体，所以可变参数的 extern 只能直接调用，不能当作值使用。
- 顺带修复：`(x) => unsafe { … }` 这样的 lambda 体之前没有按 unsafe 块检查。

### extern C 回调（0.69）

```kite
extern "C" {
  fn qsort(base: Ptr, n: Int, size: Int, cmp: fn(Ptr, Ptr) -> Int)
}
unsafe { qsort(buf, n, 8, (a: Ptr, b: Ptr) => by_value(b, a)) }   // 闭包也可以
```

- extern 参数可以是 `fn(…) -> R`，参数和返回值只能是 Int/Float/Bool/Ptr（返回值还可以是 Unit）。普通函数和闭包都能传。
- 回调只在这次 extern 调用期间有效（同步回调，如 qsort/bsearch）；C 端把函数指针存起来以后再调，属于未定义行为。
- 回调里 panic，会在 C 函数返回后按原样报错（三个引擎一致）。

### extern 指针与 C 结构体（0.68）

```kite
use std.ptr
type Cplx = { re: Float, im: Float }     // 字段都是 Int/Float/Bool/Ptr → 可以按值传给 C
extern "C" {
  fn cabs(z: Cplx) -> Float
  fn conj(z: Cplx) -> Cplx
  fn strdup(s: Str) -> Ptr
}
unsafe {
  let p = strdup("kite")
  print(ptr.to_str(p), cabs(Cplx { re: 3.0, im: 4.0 }))
  ptr.free(p)
}
```

- 新类型 `Ptr`：不透明的裸指针，和 Int 不能互相赋值，可以用作 extern 的参数和返回值。
- `std.ptr`：`null`、`is_null`、`addr`、`offset` 是安全的；`from_addr`、`alloc`（内存清零）、`free`、`read_int`/`write_int`、`read_float`/`write_float`、`read_byte`/`write_byte`（按字节偏移）、`to_str`（读 C 字符串）、`from_str`（malloc 一份副本）必须写在 `unsafe { }` 里。读写空指针会报"ptr.X: 空指针"。
- 结构体按值传递：非泛型、字段都是 Int(int64)/Float(double)/Bool/Ptr 或这样的嵌套结构体，C 端布局按字段的声明顺序。

### 函数体里的 mod（0.67）

```kite
fn report() -> Str {
  mod shapes {
    pub type Pt = { x: Int, y: Int }
    pub fn scaled(n: Int) -> Int { super.helper(n) }   // super 指向所在的模块
  }
  use shapes.Pt
  let p = Pt { x: 1, y: 2 }
  str(shapes.scaled(p.x))
}
```

- 函数体里的 mod 只在声明它的块里可见（方法、lambda、嵌套块里都可以写），不同函数里的同名 mod 互不冲突。
- 块里可以用 `use 局部模块.X`（支持分组和 as，不支持 `*`），效果覆盖整个块。
- 函数体里不能写文件模块 `mod m;`。

### 类型别名上的约束（0.66）

```kite
type Sorted<T: Ord> = List<T>
fn smallest<T: Ord>(xs: Sorted<T>) -> T { xs[0] }
let b: Sorted<Blob> = []        // 错误：Blob 没有实现 Ord
fn bad<T>(xs: Sorted<T>) -> Int // 错误：Sorted<T> 要求 T: Ord
let p: Pair<Int> = (1, 2)       // 错误：类型别名 Pair 需要 2 个类型参数，给了 1 个
```

- 顺带修复：`type L = List<Int>` 这种以 `>` 结尾的别名行，以前会被当成续行，导致下一行解析失败。

### 泛型参数上的关联常量 T.N（0.65）

```kite
trait Shape {
  const SIDES: Int
  const NAME: Str = "shape"
}
fn sides<T: Shape>() -> Int { T.SIDES }
fn describe<T: Shape>(x: T) -> Str { "{T.NAME} with {T.SIDES} sides" }
impl<T: Shape> Grid<T> { fn corners(self) -> Int { T.SIDES * self.cells.len() } }

sides.<Tri>()          // 3
describe(Sq { a: 2.0 }) // "shape with 4 sides"（用 trait 默认值）
```

- 对每个约束了带常量 trait 的类型参数 T，函数多一个隐藏参数，调用点按 T 的实际类型传入 `类型.N`；T 仍是调用者的类型参数时，转交调用者自己的隐藏参数。和常量泛型共用同一套机制，引擎不用改。
- T 只出现在约束里时（如 `sides`），调用要写 `sides.<Tri>()`，否则报"无法确定 sides 的类型参数"。
- 函数体里的 `f.<T>()` 现在可以用外层的类型参数 T。

### 内联模块 mod m { … }（0.63）

```kite
mod geom {
  pub type Point = { x: Float, y: Float }
  pub fn dist(a: Point, b: Point) -> Float { … }
  fn secret() -> Int { 42 }            // 私有：只能在 geom 里用
  pub mod units {                      // 可以嵌套；pub mod 才能在外面访问
    pub fn to_cm(inch: Float) -> Float { inch * 2.54 }
  }
}
use geom.units.to_cm

let p = geom.Point { x: 0.0, y: 0.0 }  // 模块限定的结构体字面量
geom.dist(p, p); geom.units.to_cm(1.0); to_cm(1.0)
```

- 内联模块和单独文件里的模块规则相同：外面只能用 `pub` 项，`use` 可以导入其成员；其他文件可以通过 `use util.text.shout` 访问 `util.kite` 里的 `pub mod text`。
- 只能写在顶层或另一个 mod 块里，不能写在函数体内。
- 子模块用 `super` 引用外层（0.64）：`super.helper(2)`、`use super.Config`、`super.super.SCALE`、`super.兄弟模块.f()`。和 Rust 一样，后代模块能看到祖先模块的私有项和私有子模块。`super` 只能在内联模块里用，也不能用作模块名。
- 顶层 const 按依赖顺序初始化，所以子模块的 `pub const K: Int = super.BASE * 2` 可以写在 BASE 前面。
- 顺带支持了 `模块.类型 { … }` 结构体字面量（文件模块也能用）。

### unsafe fn(…) 函数类型（0.63）

```kite
type Op = { name: Str, run: unsafe fn(Int) -> Int }
let f: unsafe fn(Int) -> Int = raw_double   // 取 unsafe 函数的值是安全的
let g: unsafe fn(Int) -> Int = safe_inc     // 普通函数可以当 unsafe fn 用
unsafe { f(5) }                              // 调用必须在 unsafe 里
```

- unsafe fn 的值不能当普通 `fn` 用（报错：unsafe fn 不能当普通函数用）。lambda 可以赋给 unsafe fn 类型。
- 从这一版开始，unsafe 函数的名字当值使用时得到 `unsafe fn` 类型，不再丢失 unsafe 性。

### 常量泛型与定长数组（0.62）

```kite
fn dot<const N: Int>(a: [Float; N], b: [Float; N]) -> Float {
  var s = 0.0
  for i in 0..N { s += a[i] * b[i] }   // N 在函数体里就是一个 Int
  s
}
fn zeros<const N: Int>() -> [Int; N] { [0; N] }

type Vec<const N: Int> = { data: [Float; N] }
impl<const N: Int> Vec<N> {
  fn dim(self) -> Int { N }
  fn zero() -> Vec<N> { Vec { data: [0.0; N] } }
}

dot([1.0, 2.0], [3.0, 4.0])          // N = 2，从实参推断
let z: [Int; 4] = zeros()            // N = 4，从期望类型推断
zeros.<2>()                          // 显式指定
let w: Vec<2> = Vec.zero()
```

- `[T; N]` 是定长数组类型（N 是整数或常量泛型参数），运行时就是列表；数组可以传给要 `List<T>` 的地方，反过来不行（列表长度编译期未知）。
- 数组字面量 `[a, b, c]` 和 `[x; 3]` 在期望数组类型时得到长度；长度不符是类型错误：`期望 [Int; 2]，实际是 [Int; 3]`。
- 常量参数只支持 `Int`，名字须大写；用作类型、把类型参数当长度、推断不出来，都会报错。
- 实现：常量参数在类型检查后变成函数的隐藏 Int 参数，调用处自动补上实参，所以三个引擎不需要改动。限制：带常量参数的函数不能直接当值传（用 lambda 包一层）；trait impl 的方法里不能把 impl 的常量参数当值用。

### 可见性 pub(pkg) / pub(mod)（0.61）

- `pub`：任何地方可见；`pub(pkg)`：只在本包内可见；`pub(mod)`：只对定义文件所在目录里的模块可见（定义在 `net/http.kite` 里，则 `net/` 下的模块可用；lib.kite 里的 pub(mod) 等于整个包）。
- 越界使用报错，例如 `net.http.helper 是 pub(mod)，只对所在目录的模块可见`；`use m.*` 只导入对当前模块可见的名字。

