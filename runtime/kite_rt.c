/* Kite 原生运行时（M1 原型）
 *
 * 值表示：16 字节带标签值 V，Int/Float/Bool/Unit 不装箱，其余为堆对象指针。
 * 内存：非移动式标记-清除 GC（见“分配与垃圾回收”一节）。
 * 语义与 Python 参考实现（kite.py）保持一致，差分测试保证输出逐字节相同。
 * 区别：Int 为 64 位有符号整数，溢出时报错（参考实现是任意精度）。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdarg.h>
#include <stdbool.h>
#include <math.h>
#include <float.h>
#include <limits.h>
#include <pthread.h>
#include <setjmp.h>

enum { T_UNDEF, T_UNIT, T_BOOL, T_INT, T_FLOAT, T_STR, T_LIST, T_TUPLE, T_MAP, T_STRUCT,
       T_ENUM, T_FN, T_STYPE, T_ETYPE, T_MODULE, T_ITER, T_CHAN, T_SET };  /* T_SET 复用 KMap，值固定为 Unit */
/* 内建类型编号（与生成代码中的 TNAMES 前缀一致） */
enum { TID_UNIT, TID_BOOL, TID_INT, TID_FLOAT, TID_STR, TID_LIST, TID_TUPLE, TID_MAP, TID_FN,
       TID_RESULT, TID_OPTION, TID_CHAN, TID_SET, TID_USER };

typedef struct V { uint32_t t; union { int64_t i; double f; void *p; } u; } V;
/* Str：头部 {len, s} 指向一个字节区 KSBuf（cap、fill 在字节前面）。新建的字符串把字节区和头部放在同一个对象里；
   s + t 在 fill == len 且容量够时用 CAS 把 fill 推到 len + k，新头部共享同一个字节区（见 str_append） */
typedef struct { int64_t len; char *s; } KStr;
typedef struct { int64_t cap; int64_t fill; char b[]; } KSBuf;
#define SBUF(p) ((KSBuf *)((char *)(p) - 2 * sizeof(int64_t)))
#define SEALED ((int64_t)1 << 62)
typedef struct { int64_t len, cap; V *a; } KList;
typedef struct { int64_t n; V a[]; } KTuple;
typedef struct { int64_t len, seq; void *root; } KMap;  /* 持久化 HAMT，见 map_put */
typedef struct { int kind; uint64_t h; int64_t seq; V k, v; } HL;           /* kind 0：叶子 */
static HL *map_get(KMap *m, V k);
static HL **map_entries(KMap *m);
static KMap *map_put(KMap *m, V k, V v);
typedef struct { int id; const char *name; int n; const int *fsyms; } KSType;
typedef struct { KSType *ty; V f[]; } KStruct;
typedef struct { int id; const char *name; } KEType;
typedef struct { int tid; int var; int n; const int *names; V a[]; } KEnum;
typedef struct KChan KChan;
typedef struct { KList *l; int64_t i; KChan *ch; } KIter;
typedef struct KFn KFn;
typedef V (*KCode)(KFn *, int, V *);
typedef struct KTask { struct KTask *parent; int done; int cancelled, unwinding; } KTask;
typedef struct KUp { V *ref; V closed; struct KUp *next; KTask *owner; } KUp;
struct KFn { KCode code; const char *name; int tid; int var; int n; const int *names; int nup; KUp *ups[]; };

static const char *KITE_FILE = "?";
static const char **KITE_FILES; static int KITE_NFILES;   /* 多文件程序：行号 = 文件序号 * 1000000 + 行 */
static __thread int kline = 0;
static KTask MAIN_TASK;
static __thread KTask *cur_task = &MAIN_TASK;

/* ── 取消 ──
 * 取消是协作式的，但会自动展开：被取消的任务在下一个取消点（函数入口、循环回边、阻塞的通道操作/sleep/select）
 * 停下，依次执行各层未执行的 defer、关闭被闭包捕获的变量，然后 longjmp 回任务边界（run_one）。
 * 有效取消：从任务往上找，先遇到“正在展开”的任务则不算（清理代码不会再被取消），先遇到被取消的则算。
 * cancel_epoch 为 0（从未取消过）时取消点只是一次原子读。
 */
static int cancel_epoch;
static int task_cancelled(KTask *t) {
    for (; t; t = t->parent) {
        if (__atomic_load_n(&t->unwinding, __ATOMIC_ACQUIRE)) return 0;
        if (__atomic_load_n(&t->cancelled, __ATOMIC_ACQUIRE)) return 1;
    }
    return 0;
}
/* 需要展开的栈帧（有 defer 或有被闭包捕获的局部变量的函数才登记） */
typedef struct KFrame { struct KFrame *prev; struct KUp **open; struct V *L; struct V *defers; int *nd; } KFrame;
static __thread KFrame *cur_frame;
/* 边界：任务边界（run_one，接住取消和错误）或 recover 边界（只接错误，取消穿过它） */
typedef struct Boundary { struct Boundary *prev; KFrame *frame; int is_task; jmp_buf jb; } Boundary;
static __thread Boundary *cur_bound;
/* 运行时错误可恢复：die 时若有边界且没持有调度锁，就展开到最近的边界（longjmp 码 2）；
   否则（主任务最外层、持锁时、死锁、内存不足）打印并退出 */
static __thread int rt_locked;
static __thread char panic_full[1200], panic_msg[1024];
static void __attribute__((noreturn)) unwind_to(Boundary *b, int code);
extern const char *SYMS[];
extern const char *TNAMES[];
static int SYM_Ok, SYM_Err, SYM_Some, SYM_None;
static int KITE_SYM_REC = -1;   /* 记录变体的字段名数组末尾多放一个 __rec 作标记 */

static pthread_mutex_t die_mu = PTHREAD_MUTEX_INITIALIZER;
static void __attribute__((noreturn)) fatal_exit(const char *full) {
    pthread_mutex_lock(&die_mu);   /* 多个任务同时出错时只报第一个；不解锁，进程随即退出 */
    fflush(stdout);
    fprintf(stderr, "%s\n", full);
    exit(1);
}
/* 抛出（或继续传播）一个已格式化的错误 */
static void __attribute__((noreturn)) raise_panic(const char *full, const char *msg) {
    if (cur_bound && !rt_locked) {
        if (full != panic_full) snprintf(panic_full, sizeof panic_full, "%s", full);
        if (msg != panic_msg) snprintf(panic_msg, sizeof panic_msg, "%s", msg);
        unwind_to(cur_bound, 2);
    }
    fatal_exit(full);
}
static void __attribute__((noreturn)) die_v(int recoverable, const char *fmt, va_list ap) {
    char msg[1024];
    vsnprintf(msg, sizeof msg, fmt, ap);
    int fi = kline / 1000000, ln = kline % 1000000;
    snprintf(panic_full, sizeof panic_full, "%s:%d: %s", fi && fi < KITE_NFILES ? KITE_FILES[fi] : KITE_FILE, ln, msg);
    if (!recoverable) fatal_exit(panic_full);
    snprintf(panic_msg, sizeof panic_msg, "%s", msg);
    raise_panic(panic_full, panic_msg);
}
static void __attribute__((noreturn)) die(const char *fmt, ...) { va_list ap; va_start(ap, fmt); die_v(1, fmt, ap); }
static void __attribute__((noreturn)) fatal(const char *fmt, ...) { va_list ap; va_start(ap, fmt); die_v(0, fmt, ap); }

/* ── 分配与垃圾回收 ──
 * 非移动式标记-清除 GC，支持多线程（stop-the-world）
 * - 小对象（≤ 128 KB）按尺寸分级（25 级）放进 256 KB 对齐的区块，每块一个尺寸，带分配表与标记位图
 *   分配表每项：0 空闲，1 普通对象（逐字保守扫描），2 无指针对象（字符串、字符缓冲；标记但不扫描），
 *   3 值数组（List 的元素区：按 V 逐个看标签，只跟随指针类标签的负载，Int / Float / Bool 不当指针）
 * - 指针过滤：先和堆地址范围 [heap_lo, heap_hi) 比较，再查区块哈希表
 *   每个线程独占自己正在分配的区块（线程本地分配，快路径无锁）；取新区块时才加 heap_mu
 * - 大对象（> 128 KB）单独分配：起点按 256 KB 对齐、长度按 4 KB 取整，覆盖的每个区块都登记到同一个描述符；
 *   只扫描请求的字节数，无指针的大对象不扫描
 * - 根：全局变量表、字符串常量表、每个 Kite 线程的 C 栈与寄存器（setjmp 溢出）
 * - 栈和堆对象都做保守扫描：任何看起来指向堆内（含对象内部）的字都算引用
 * - 停顿协议：发起回收的线程置 gc_requested，其余线程在安全点（分配、循环回边、函数入口）停下，
 *   或已处于“阻塞”状态（等待子任务、读标准输入）；全部到齐后单线程标记清除，再统一放行
 * - 触发：自上次回收以来新分配量（按实际占用的槽位 / 页计）超过 max(8 MB, 上次存活量)；KITE_GC_STRESS=1 时每次分配都回收（测试用）
 * - KITE_GC_STATS=1 时退出前打印统计；KITE_GC_OFF=1 关闭回收（对比用）
 * - 压力模式下被回收的对象会被投毒（0xAB），任何悬空引用都会立刻出错
 */
#include <setjmp.h>
#include <errno.h>
#include <time.h>
#include <alloca.h>
#include <unistd.h>
#include <sys/wait.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <sys/un.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <dirent.h>
#include <dlfcn.h>
#include <signal.h>
#define ARENA ((uintptr_t)1 << 18)
#define NCLASS 25
static const uint32_t CLASS_SZ[NCLASS] = { 16, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024, 1536, 2048,
    3072, 4096, 6144, 8192, 12288, 16384, 24576, 32768, 49152, 87376, 131072 };
#define SMALL_MAX 131072
static uint8_t CLASS_OF[129];   /* (n + 15) / 16 → 尺寸级，n ≤ 2048 时查表 */
static inline int size_class(size_t n) {
    if (n <= 2048) return CLASS_OF[(n + 15) >> 4];
    int c = 14; while (CLASS_SZ[c] < n) c++;
    return c;
}

typedef struct Mutator Mutator;
typedef struct Block {
    char *base; uint32_t objsz, nobj; int large, noscan; size_t lsize, nbytes;
    uint8_t *alloc, *mark; uint32_t nfree, cursor;   /* 空闲槽位不串链表：分配时从 cursor 起在 alloc 表里找 0 */
    Mutator *owner;
    struct Block *next;
} Block;
struct Mutator {
    char *stack_base, *sp; jmp_buf regs;
    Block *avail[NCLASS]; size_t local_bytes;
    int parked; Mutator *next;
};
static Block *cls_blocks[NCLASS], *large_blocks;
static Block **btab; static size_t btab_cap, btab_n;
static size_t gc_threshold = (size_t)8 << 20, gc_live, gc_heap, gc_heap_peak, gc_count;
static size_t gc_bytes_since, gc_total;             /* 原子更新 */
static int gc_stress = -1, gc_stats, gc_off;
static int gc_requested;   /* 用 __atomic 读写：安全点无锁轮询 */
static pthread_mutex_t heap_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t world_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t world_cv = PTHREAD_COND_INITIALIZER;   /* 回收者等待其他线程到齐 */
static pthread_cond_t resume_cv = PTHREAD_COND_INITIALIZER;  /* 停下的线程等待放行 */
static Mutator *mutators; static int nmutators, nparked;
static __thread Mutator *me;
static V *gc_roots[4]; static size_t gc_root_n[4]; static int gc_nroots;
static void gc_add_root(V *p, size_t n) { gc_roots[gc_nroots] = p; gc_root_n[gc_nroots++] = n; }

static size_t bhash(uintptr_t k) { return (size_t)((k >> 18) * 11400714819323198485ull); }
static void btab_put(uintptr_t key, Block *b);
static void btab_grow(void) {
    Block **old = btab;
    btab_cap = btab_cap ? btab_cap * 2 : 1024; btab = calloc(btab_cap, sizeof(Block *)); btab_n = 0;
    for (int c = 0; c < NCLASS; c++) for (Block *b = cls_blocks[c]; b; b = b->next) btab_put((uintptr_t)b->base, b);
    for (Block *b = large_blocks; b; b = b->next)
        for (uintptr_t k = (uintptr_t)b->base; k < (uintptr_t)b->base + b->lsize; k += ARENA) btab_put(k, b);
    free(old);
}
static void btab_put(uintptr_t key, Block *b) {
    if ((btab_n + 1) * 2 > btab_cap) btab_grow();
    size_t i = bhash(key) & (btab_cap - 1);
    while (btab[i] && btab[i] != (Block *)1) i = (i + 1) & (btab_cap - 1);
    btab[i] = b; btab_n++;
}
static inline int bcovers(Block *b, uintptr_t key) {
    return (uintptr_t)b->base <= key && key < (uintptr_t)b->base + (b->large ? b->lsize : ARENA);
}
static void btab_del(uintptr_t key) {
    size_t i = bhash(key) & (btab_cap - 1);
    while (btab[i]) {
        if (btab[i] != (Block *)1 && bcovers(btab[i], key)) { btab[i] = (Block *)1; return; }
        i = (i + 1) & (btab_cap - 1);
    }
}
static uintptr_t heap_lo = UINTPTR_MAX, heap_hi;   /* 持有 heap_mu 时更新，回收期间只读 */
static void heap_range(char *base, size_t n) {
    if ((uintptr_t)base < heap_lo) heap_lo = (uintptr_t)base;
    if ((uintptr_t)base + n > heap_hi) heap_hi = (uintptr_t)base + n;
}
static Block *btab_get(uintptr_t p) {
    if (p < heap_lo || p >= heap_hi || !btab_cap) return NULL;
    uintptr_t key = p & ~(ARENA - 1);
    size_t i = bhash(key) & (btab_cap - 1);
    while (btab[i]) {
        Block *b = btab[i];
        if (b != (Block *)1 && bcovers(b, key)) return b;
        i = (i + 1) & (btab_cap - 1);
    }
    return NULL;
}

/* 标记 */
static void **mstack; static size_t msp, mcap;
static void mpush(void *p) {
    if (msp == mcap) { mcap = mcap ? mcap * 2 : 4096; mstack = realloc(mstack, mcap * sizeof(void *)); }
    mstack[msp++] = p;
}
static inline void gc_mark_ptr(uintptr_t p) {
    Block *b = btab_get(p);
    if (!b) return;
    if (b->large) {
        if (p >= (uintptr_t)b->base + b->lsize || b->mark[0]) return;
        b->mark[0] = 1; if (b->noscan != 2) mpush(b->base); return;
    }
    size_t idx = (p - (uintptr_t)b->base) / b->objsz;
    if (idx >= b->nobj || !b->alloc[idx] || b->mark[idx]) return;
    b->mark[idx] = 1;
    if (b->alloc[idx] != 2) mpush(b->base + idx * b->objsz);
}
static void gc_scan_range(char *lo, char *hi) {
    lo = (char *)(((uintptr_t)lo + 7) & ~(uintptr_t)7);
    for (char *q = lo; q + sizeof(void *) <= hi; q += sizeof(void *)) gc_mark_ptr(*(uintptr_t *)q);
}
static void gc_scan_vals(V *a, size_t n) {
    for (size_t i = 0; i < n; i++) if (a[i].t >= T_STR) gc_mark_ptr((uintptr_t)a[i].u.p);
}
static void gc_drain(void) {
    while (msp) {
        void *o = mstack[--msp];
        Block *b = btab_get((uintptr_t)o);
        int kind = b->large ? b->noscan : b->alloc[((char *)o - b->base) / b->objsz];
        if (kind == 4) gc_mark_ptr(((uintptr_t *)o)[1]);   /* Str 头部：只有 s 是指针 */
        else if (b->large) {
            if (kind == 3) gc_scan_vals((V *)b->base, b->nbytes / sizeof(V));
            else gc_scan_range(b->base, b->base + b->nbytes);
        } else if (kind == 3) gc_scan_vals((V *)o, b->objsz / sizeof(V));
        else gc_scan_range(o, (char *)o + b->objsz);
    }
}
/* 把当前线程的寄存器和栈顶记到 Mutator 里（停下或阻塞前调用） */
#define SAVE_CONTEXT(m) do { setjmp((m)->regs); volatile char here_ = 0; (m)->sp = (char *)&here_; } while (0)

/* 清除（世界已停止） */
/* 整块空闲的区块先留在备用池里（不在 btab 中），下次要新区块时直接复用，避免反复 munmap / mmap 和缺页；
   池子最多保留下一轮回收前要分配的量（max(8 MB, 存活量) / 256 KB 块），多出来的还给系统 */
static Block *spare_blocks; static size_t nspare;
static void gc_sweep(void) {
    gc_live = 0;
    size_t nused = 0;
    Block *empties = NULL;
    for (int c = 0; c < NCLASS; c++) {
        Block **pp = &cls_blocks[c];
        while (*pp) {
            Block *b = *pp; b->owner = NULL; b->cursor = 0;
            uint8_t *al = b->alloc, *mk = b->mark; uint32_t nlive = 0, n = b->nobj;
            if (gc_stress)
                for (uint32_t i = 0; i < n; i++)
                    if (al[i] && !mk[i]) memset(b->base + (size_t)i * b->objsz, 0xAB, b->objsz);
            for (uint32_t i = 0; i < n; i++) {     /* 只动两张字节表，不碰对象本身 */
                uint8_t a = mk[i] ? al[i] : 0; al[i] = a; nlive += a != 0;
            }
            b->nfree = n - nlive; gc_live += (size_t)nlive * b->objsz;
            memset(mk, 0, n);
            if (b->nfree == b->nobj && cls_blocks[c] != b) {   /* 整块空闲：先收集起来 */
                *pp = b->next; btab_del((uintptr_t)b->base);
                b->next = empties; empties = b;
                continue;
            }
            nused++;
            pp = &b->next;
        }
    }
    Block **pp = &large_blocks;
    while (*pp) {
        Block *b = *pp;
        if (b->mark[0]) { b->mark[0] = 0; gc_live += b->lsize; pp = &b->next; continue; }
        *pp = b->next;
        for (uintptr_t k = (uintptr_t)b->base; k < (uintptr_t)b->base + b->lsize; k += ARENA) btab_del(k);
        gc_heap -= b->lsize; free(b->base); free(b->mark); free(b);
    }
    (void)nused;
    size_t cap = (gc_live > ((size_t)8 << 20) ? gc_live : ((size_t)8 << 20)) / ARENA;   /* 下一轮反正会长到这么多 */
    while (empties) {
        Block *b = empties; empties = b->next;
        free(b->alloc); free(b->mark); b->alloc = b->mark = NULL;
        if (nspare < cap) { b->next = spare_blocks; spare_blocks = b; nspare++; continue; }
        gc_heap -= ARENA; free(b->base); free(b);
    }
    while (nspare > cap) {
        Block *b = spare_blocks; spare_blocks = b->next; nspare--;
        gc_heap -= ARENA; free(b->base); free(b);
    }
}
static double gc_pause_total, gc_pause_max, gc_sweep_total;   /* 毫秒 */
static double now_ms(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6; }
static void __attribute__((noinline)) gc_collect_stopped(void) {
    gc_count++;
    double t0 = gc_stats ? now_ms() : 0;
    SAVE_CONTEXT(me);
    for (int r = 0; r < gc_nroots; r++) gc_scan_range((char *)gc_roots[r], (char *)(gc_roots[r] + gc_root_n[r]));
    for (Mutator *m = mutators; m; m = m->next) {
        gc_scan_range((char *)&m->regs, (char *)(&m->regs + 1));
        if (m->sp && m->stack_base) gc_scan_range(m->sp, m->stack_base);
    }
    gc_drain();
    double t1 = gc_stats ? now_ms() : 0;
    gc_sweep();
    if (gc_stats) gc_sweep_total += now_ms() - t1;
    for (Mutator *m = mutators; m; m = m->next) memset(m->avail, 0, sizeof m->avail);
    __atomic_store_n(&gc_bytes_since, 0, __ATOMIC_RELAXED);
    gc_threshold = gc_live > ((size_t)8 << 20) ? gc_live : ((size_t)8 << 20);
    if (gc_stats) { double d = now_ms() - t0; gc_pause_total += d; if (d > gc_pause_max) gc_pause_max = d; }
}

/* 安全点：有回收请求时停下，等回收结束 */
static void __attribute__((noinline)) gc_park(void) {
    if (!me) return;
    SAVE_CONTEXT(me);
    pthread_mutex_lock(&world_mu);
    if (gc_requested) {
        me->parked = 1; nparked++;
        pthread_cond_signal(&world_cv);
        while (gc_requested) pthread_cond_wait(&resume_cv, &world_mu);
        me->parked = 0; nparked--;
    }
    pthread_mutex_unlock(&world_mu);
}
#define SAFEPOINT() do { if (__builtin_expect(__atomic_load_n(&gc_requested, __ATOMIC_RELAXED), 0)) gc_park(); } while (0)

static void __attribute__((noinline)) gc_collect(void) {
    pthread_mutex_lock(&world_mu);
    if (gc_requested) {               /* 别的线程正在回收：自己停下 */
        pthread_mutex_unlock(&world_mu); gc_park(); return;
    }
    __atomic_store_n(&gc_requested, 1, __ATOMIC_RELAXED);
    while (nparked < nmutators - 1) pthread_cond_wait(&world_cv, &world_mu);
    gc_collect_stopped();
    __atomic_store_n(&gc_requested, 0, __ATOMIC_RELAXED);
    pthread_cond_broadcast(&resume_cv);
    pthread_mutex_unlock(&world_mu);
}

/* 阻塞区：等待子任务或 I/O 时，本线程算作“已停下”，回收可以照常进行 */
static void __attribute__((noinline)) gc_block_enter(void) {
    if (!me) return;
    SAVE_CONTEXT(me);
    pthread_mutex_lock(&world_mu);
    me->parked = 1; nparked++;
    pthread_cond_signal(&world_cv);
    pthread_mutex_unlock(&world_mu);
}
static void gc_block_leave(void) {
    if (!me) return;
    pthread_mutex_lock(&world_mu);
    while (gc_requested) pthread_cond_wait(&resume_cv, &world_mu);
    me->parked = 0; nparked--;
    pthread_mutex_unlock(&world_mu);
}

/* 线程登记 */
static void gc_register(Mutator *m, char *stack_base) {
    memset(m, 0, sizeof *m); m->stack_base = stack_base;
    pthread_mutex_lock(&world_mu);
    while (gc_requested) pthread_cond_wait(&resume_cv, &world_mu);
    m->next = mutators; mutators = m; nmutators++;
    pthread_mutex_unlock(&world_mu);
    me = m;
}
static void gc_unregister(void) {
    Mutator *m = me;
    /* 不等待正在进行的回收：回收者在收集期间一直持有 world_mu，拿到锁就说明它在等人到齐或没在回收，
       此时直接退出登记并通知它（否则会互相等待而死锁） */
    pthread_mutex_lock(&world_mu);
    for (Mutator **pp = &mutators; *pp; pp = &(*pp)->next) if (*pp == m) { *pp = m->next; break; }
    nmutators--;
    pthread_mutex_lock(&heap_mu);
    for (int c = 0; c < NCLASS; c++) if (m->avail[c]) m->avail[c]->owner = NULL;
    pthread_mutex_unlock(&heap_mu);
    __atomic_fetch_add(&gc_bytes_since, m->local_bytes, __ATOMIC_RELAXED);
    pthread_cond_signal(&world_cv);
    pthread_mutex_unlock(&world_mu);
    me = NULL;
}

static Block *new_block(int c) {   /* 调用者持有 heap_mu */
    Block *b;
    if (spare_blocks) {
        b = spare_blocks; spare_blocks = b->next; nspare--;
        char *base = b->base; memset(b, 0, sizeof *b); b->base = base;
    } else {
        b = calloc(1, sizeof(Block));
        b->base = aligned_alloc(ARENA, ARENA);
        if (!b->base) fatal("内存不足");
        gc_heap += ARENA; if (gc_heap > gc_heap_peak) gc_heap_peak = gc_heap;
    }
    b->objsz = CLASS_SZ[c]; b->nobj = (uint32_t)(ARENA / b->objsz);
    b->alloc = calloc(b->nobj, 1); b->mark = calloc(b->nobj, 1);
    b->nfree = b->nobj; b->cursor = 0;
    b->next = cls_blocks[c]; cls_blocks[c] = b;
    btab_put((uintptr_t)b->base, b); heap_range(b->base, ARENA);
    return b;
}
static Block *acquire_block(int c) {
    pthread_mutex_lock(&heap_mu);
    Block *old = me->avail[c];
    if (old) old->owner = NULL;
    Block *b = NULL;
    for (Block *x = cls_blocks[c]; x; x = x->next) if (!x->owner && x->nfree) { b = x; break; }
    if (!b) b = new_block(c);
    b->owner = me; me->avail[c] = b;
    pthread_mutex_unlock(&heap_mu);
    return b;
}

static void *kalloc_kind(size_t n, int noscan) {
    if (__builtin_expect(gc_stress < 0, 0)) {
        const char *s = getenv("KITE_GC_STRESS"); gc_stress = s && *s == '1';
        const char *t = getenv("KITE_GC_STATS"); gc_stats = t && *t == '1';
        const char *o = getenv("KITE_GC_OFF"); gc_off = o && *o == '1';
        for (int i = 0, c = 0; i <= 128; i++) { while (CLASS_SZ[c] < (uint32_t)i * 16) c++; CLASS_OF[i] = (uint8_t)c; }
        CLASS_OF[0] = 0;
    }
    if (n == 0) n = 1;
    SAFEPOINT();
    int c = n > SMALL_MAX ? -1 : size_class(n);
    size_t lsize = (n + 4095) & ~(size_t)4095;
    me->local_bytes += c < 0 ? lsize : CLASS_SZ[c];
    if (me->local_bytes >= ((size_t)64 << 10) || gc_stress) {
        size_t tot = __atomic_add_fetch(&gc_bytes_since, me->local_bytes, __ATOMIC_RELAXED);
        __atomic_fetch_add(&gc_total, me->local_bytes, __ATOMIC_RELAXED);
        me->local_bytes = 0;
        if (!gc_off && (gc_stress || tot >= gc_threshold)) gc_collect();
    }
    if (c < 0) {
        Block *b = calloc(1, sizeof(Block));
        b->large = 1; b->lsize = lsize; b->nbytes = n; b->noscan = noscan;
        if (posix_memalign((void **)&b->base, ARENA, b->lsize)) fatal("内存不足");
        b->mark = calloc(1, 1);
        memset(b->base, 0, n);
        pthread_mutex_lock(&heap_mu);
        b->next = large_blocks; large_blocks = b;
        for (uintptr_t k = (uintptr_t)b->base; k < (uintptr_t)b->base + b->lsize; k += ARENA) btab_put(k, b);
        heap_range(b->base, b->lsize);
        gc_heap += b->lsize; if (gc_heap > gc_heap_peak) gc_heap_peak = gc_heap;
        pthread_mutex_unlock(&heap_mu);
        return b->base;
    }
    Block *b = me->avail[c];
    if (!b || !b->nfree) b = acquire_block(c);
    uint8_t *q = memchr(b->alloc + b->cursor, 0, b->nobj - b->cursor);
    uint32_t idx = (uint32_t)(q - b->alloc);
    b->cursor = idx + 1; b->nfree--;
    void *p = b->base + (size_t)idx * b->objsz;
    *q = noscan ? noscan : 1;
    memset(p, 0, noscan == 2 || noscan == 4 ? n : b->objsz);   /* 无指针对象不扫描，尾部不必清零 */
    return p;
}
static inline void *kalloc(size_t n) { return kalloc_kind(n, 0); }
/* 不含任何 GC 指针的对象（字符串内容、字符缓冲）：标记时不扫描，避免把文本误认作指针 */
static inline void *kalloc_atomic(size_t n) { return kalloc_kind(n, 2); }
/* V 数组：扫描时只看指针类标签 */
static inline V *kalloc_vals(size_t n) { return kalloc_kind(sizeof(V) * n, 3); }
static void gc_report(void) {
    if (gc_stats)
        fprintf(stderr, "[gc] 回收 %zu 次，峰值堆 %.1f MB，累计分配 %.1f MB，结束时存活 %.1f MB，停顿合计 %.1f ms，最长 %.2f ms（清除 %.1f ms）\n",
                gc_count, gc_heap_peak / 1048576.0, (gc_total + (me ? me->local_bytes : 0)) / 1048576.0, gc_live / 1048576.0,
                gc_pause_total, gc_pause_max, gc_sweep_total);
}

/* ── 构造 ── */
static const V UNIT = { T_UNIT, { 0 } };
static inline V mkint(int64_t i) { V v; v.t = T_INT; v.u.i = i; return v; }
static inline V mkflt(double f) { V v; v.t = T_FLOAT; v.u.f = f; return v; }
static inline V mkbool(bool b) { V v; v.t = T_BOOL; v.u.i = b; return v; }
static inline V mkp(uint32_t t, void *p) { V v; v.t = t; v.u.p = p; return v; }
#define STR(v) ((KStr *)(v).u.p)
#define LIST(v) ((KList *)(v).u.p)
#define TUP(v) ((KTuple *)(v).u.p)
#define MAP(v) ((KMap *)(v).u.p)
#define STRUCTV(v) ((KStruct *)(v).u.p)
#define ENUMV(v) ((KEnum *)(v).u.p)
#define FN(v) ((KFn *)(v).u.p)

/* 新字符串：头部 + 字节区放在一个对象里（GC 种类 4，只扫描 s），字节区容量 cap，fill = len，s[len] = 0 */
static KStr *newstr(int64_t n, int64_t cap) {
    KStr *k = kalloc_kind(sizeof(KStr) + sizeof(KSBuf) + cap, 4);
    KSBuf *sb = (KSBuf *)(k + 1); sb->cap = cap; sb->fill = n;
    k->len = n; k->s = sb->b; k->s[n] = 0;
    return k;
}
static V mkstr(const char *s, int64_t n) {
    KStr *k = newstr(n, n + 1); memcpy(k->s, s, n);
    return mkp(T_STR, k);
}
/* s + t：字符串不可变，[0, fill) 的字节写过就不再改。a 的 fill 正好等于 a->len（尾部没人占）且容量够时，
   CAS fill: len → len + k 抢下尾部，写入 t 和新的结尾 0，新头部共享字节区；否则复制并按 1.5 倍留出余量 */
static KStr *str_append(KStr *a, const char *t, int64_t k) {
    int64_t n = a->len; KSBuf *sb = SBUF(a->s);
    if (n + k + 1 <= sb->cap) {
        int64_t want = n;
        if (__atomic_compare_exchange_n(&sb->fill, &want, n + k, 0, __ATOMIC_ACQ_REL, __ATOMIC_RELAXED)) {
            memcpy(a->s + n, t, k); a->s[n + k] = 0;
            KStr *r = kalloc_kind(sizeof(KStr), 4); r->len = n + k; r->s = a->s;
            return r;
        }
    }
    int64_t m = n + k, cap = m + m / 2 + 1; if (cap < 16) cap = 16;
    KStr *r = newstr(m, cap); memcpy(r->s, a->s, n); memcpy(r->s + n, t, k); r->s[m] = 0;
    return r;
}
/* 交给 C 的以 0 结尾的字符串：s[len] 在创建时写成 0，但之后可能被别人的 str_append 占用。
   fill == len 时用 CAS 封住（加 SEALED 位，之后的追加都复制），封住的直接用；已被占用的复制一份 */
static const char *cstrk(KStr *k) {
    KSBuf *sb = SBUF(k->s);
    int64_t f = __atomic_load_n(&sb->fill, __ATOMIC_ACQUIRE);
    if (f == (k->len | SEALED)) return k->s;
    if (f == k->len && __atomic_compare_exchange_n(&sb->fill, &f, k->len | SEALED, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return k->s;
    if (f == (k->len | SEALED)) return k->s;
    KStr *c = newstr(k->len, k->len + 1); memcpy(c->s, k->s, k->len);
    return c->s;
}
#define CSTR(v) cstrk(STR(v))
static V cstr(const char *s) { return mkstr(s, strlen(s)); }
static KList *newlist(int64_t cap) {
    KList *l = kalloc(sizeof(KList));
    l->len = 0; l->cap = cap < 4 ? 4 : cap; l->a = kalloc_vals(l->cap);
    return l;
}
static void lpush(KList *l, V x) {
    if (l->len == l->cap) {
        V *na = kalloc_vals(l->cap * 2);
        memcpy(na, l->a, sizeof(V) * l->len); l->a = na; l->cap *= 2;
    }
    l->a[l->len++] = x;
}
/* 均摊 O(1) 的 push / 列表拼接：List 是不可变值，元素区里 len 之后的槽位从没写过（标签是 T_UNDEF）。
   s 的下一个槽位还空着、容量也够时，新列表直接共享 s 的元素区，把新元素写进尾部；
   槽位 s->len 用 CAS 抢占（T_UNDEF → T_CLAIM），抢到的人独占整个尾部，其他共享者（包括别的线程）看到它非空就复制。
   复制时容量按 1.5 倍增长，之后在新列表上连续 push 就不再复制 */
#define T_CLAIM 0xFFFFFFFFu
static KList *list_append(KList *s, const V *xs, int64_t k) {
    int64_t n = s->len;
    if (k > 0 && n + k <= s->cap) {
        uint32_t want = T_UNDEF;
        if (__atomic_compare_exchange_n(&s->a[n].t, &want, T_CLAIM, 0, __ATOMIC_ACQ_REL, __ATOMIC_RELAXED)) {
            if (k > 1) memcpy(s->a + n + 1, xs + 1, sizeof(V) * (k - 1));
            s->a[n].u = xs[0].u;
            __atomic_store_n(&s->a[n].t, xs[0].t, __ATOMIC_RELEASE);
            KList *l = kalloc(sizeof(KList)); l->len = n + k; l->cap = s->cap; l->a = s->a;
            return l;
        }
    }
    int64_t want = n + k, cap = want + want / 2;
    KList *l = newlist(cap < 4 ? 4 : cap);
    memcpy(l->a, s->a, sizeof(V) * n); memcpy(l->a + n, xs, sizeof(V) * k); l->len = want;
    return l;
}
static V mklist(int n, V *xs) {
    KList *l = newlist(n);
    memcpy(l->a, xs, sizeof(V) * n); l->len = n;
    return mkp(T_LIST, l);
}
static V mktuple(int n, V *xs) {
    KTuple *t = kalloc(sizeof(KTuple) + sizeof(V) * n);
    t->n = n; memcpy(t->a, xs, sizeof(V) * n);
    return mkp(T_TUPLE, t);
}
static V mkenum(int tid, int var, int n, const int *names, V *xs) {
    KEnum *e = kalloc(sizeof(KEnum) + sizeof(V) * n);
    e->tid = tid; e->var = var; e->n = n; e->names = names;
    if (n) memcpy(e->a, xs, sizeof(V) * n);
    return mkp(T_ENUM, e);
}
static V kOk(V x) { return mkenum(TID_RESULT, SYM_Ok, 1, NULL, &x); }
static V kErr(V x) { return mkenum(TID_RESULT, SYM_Err, 1, NULL, &x); }
static V kSome(V x) { return mkenum(TID_OPTION, SYM_Some, 1, NULL, &x); }
static V kNone(void) { return mkenum(TID_OPTION, SYM_None, 0, NULL, NULL); }

static int tid_of(V v) {
    switch (v.t) {
    case T_BOOL: return TID_BOOL; case T_INT: return TID_INT; case T_FLOAT: return TID_FLOAT;
    case T_STR: return TID_STR; case T_LIST: return TID_LIST; case T_TUPLE: return TID_TUPLE;
    case T_MAP: return TID_MAP; case T_STRUCT: return STRUCTV(v)->ty->id;
    case T_ENUM: return ENUMV(v)->tid; case T_FN: return TID_FN; case T_CHAN: return TID_CHAN; case T_SET: return TID_SET;
    default: return TID_UNIT;
    }
}
static const char *tname(V v) { return TNAMES[tid_of(v)]; }

/* ── 字符串缓冲 & show ── */
typedef struct { char *s; int64_t len, cap; } Buf;
static void bput(Buf *b, const char *s, int64_t n) {
    if (b->len + n + 1 > b->cap) {
        int64_t nc = (b->cap ? b->cap * 2 : 64);
        while (nc < b->len + n + 1) nc *= 2;
        char *ns = kalloc_atomic(nc); if (b->len) memcpy(ns, b->s, b->len);
        b->s = ns; b->cap = nc;
    }
    memcpy(b->s + b->len, s, n); b->len += n; b->s[b->len] = 0;
}
static void bputs(Buf *b, const char *s) { bput(b, s, strlen(s)); }

static void fmt_float(double f, char *out) {
    if (isfinite(f) && floor(f) == f && fabs(f) < 1e16) { snprintf(out, 40, "%.1f", f); return; }
    if (isnan(f)) { strcpy(out, "nan"); return; }
    if (isinf(f)) { strcpy(out, f > 0 ? "inf" : "-inf"); return; }
    int p = 1;
    for (; p <= 17; p++) {
        snprintf(out, 40, "%.*g", p, f);
        if (strtod(out, NULL) == f) break;
    }
    /* 和 Python 的 repr 一致：十进制指数 >= 16 时用科学计数法（%g 在 17 位有效数字、指数 16 时会给出整数形式） */
    if (p > 17) p = 17;
    if (!strchr(out, 'e') && fabs(f) >= 1e16) snprintf(out, 40, "%.*e", p - 1, f);
}

static void show_to(Buf *b, V v, bool q);
static void show_seq(Buf *b, V *a, int64_t n) {
    for (int64_t i = 0; i < n; i++) { if (i) bputs(b, ", "); show_to(b, a[i], true); }
}
static void show_to(Buf *b, V v, bool q) {
    char tmp[64];
    switch (v.t) {
    case T_UNIT: bputs(b, "()"); break;
    case T_BOOL: bputs(b, v.u.i ? "true" : "false"); break;
    case T_INT: snprintf(tmp, sizeof tmp, "%lld", (long long)v.u.i); bputs(b, tmp); break;
    case T_FLOAT: fmt_float(v.u.f, tmp); bputs(b, tmp); break;
    case T_STR:
        if (!q) { bput(b, STR(v)->s, STR(v)->len); break; }
        bputs(b, "\"");
        for (int64_t i = 0; i < STR(v)->len; i++) {
            char c = STR(v)->s[i];
            if (c == '"') bputs(b, "\\\""); else bput(b, &c, 1);
        }
        bputs(b, "\""); break;
    case T_LIST: bputs(b, "["); show_seq(b, LIST(v)->a, LIST(v)->len); bputs(b, "]"); break;
    case T_TUPLE: bputs(b, "("); show_seq(b, TUP(v)->a, TUP(v)->n); bputs(b, ")"); break;
    case T_MAP:
        bputs(b, "{");
        HL **es = map_entries(MAP(v));
        for (int64_t i = 0; i < MAP(v)->len; i++) {
            if (i) bputs(b, ", ");
            show_to(b, es[i]->k, true); bputs(b, ": "); show_to(b, es[i]->v, true);
        }
        bputs(b, "}"); break;
    case T_SET: {
        bputs(b, "Set{");
        HL **es = map_entries(MAP(v));
        for (int64_t i = 0; i < MAP(v)->len; i++) { if (i) bputs(b, ", "); show_to(b, es[i]->k, true); }
        bputs(b, "}"); break;
    }
    case T_STRUCT: {
        KStruct *s = STRUCTV(v);
        bputs(b, s->ty->name); bputs(b, " { ");
        for (int i = 0; i < s->ty->n; i++) {
            if (i) bputs(b, ", ");
            bputs(b, SYMS[s->ty->fsyms[i]]); bputs(b, ": "); show_to(b, s->f[i], true);
        }
        bputs(b, " }"); break;
    }
    case T_ENUM: {
        KEnum *e = ENUMV(v);
        bputs(b, SYMS[e->var]);
        if (e->n && e->names && e->names[e->n] == KITE_SYM_REC) {
            bputs(b, " { ");
            for (int i = 0; i < e->n; i++) {
                if (i) bputs(b, ", ");
                bputs(b, SYMS[e->names[i]]); bputs(b, ": "); show_to(b, e->a[i], true);
            }
            bputs(b, " }");
        } else if (e->n) { bputs(b, "("); show_seq(b, e->a, e->n); bputs(b, ")"); }
        break;
    }
    case T_FN: bputs(b, "<fn "); bputs(b, FN(v)->name); bputs(b, ">"); break;
    case T_STYPE: bputs(b, "<type "); bputs(b, ((KSType *)v.u.p)->name); bputs(b, ">"); break;
    case T_ETYPE: bputs(b, "<type "); bputs(b, ((KEType *)v.u.p)->name); bputs(b, ">"); break;
    case T_MODULE: bputs(b, "<module "); bputs(b, (const char *)v.u.p); bputs(b, ">"); break;
    case T_CHAN: bputs(b, "<chan>"); break;
    default: bputs(b, "<?>");
    }
}
static V kshow(V v, bool q) {
    if (v.t == T_STR && !q) return v;
    Buf b = { 0 }; show_to(&b, v, q);
    return mkstr(b.s ? b.s : "", b.len);
}
static const char *cshow(V v) { V s = kshow(v, true); return CSTR(s); }

/* Python repr 风格（parse_int 错误信息用） */
static V krepr(V s) {
    KStr *k = STR(s);
    bool has_sq = memchr(k->s, '\'', k->len) != NULL, has_dq = memchr(k->s, '"', k->len) != NULL;
    char qc = (has_sq && !has_dq) ? '"' : '\'';
    Buf b = { 0 }; bput(&b, &qc, 1);
    for (int64_t i = 0; i < k->len; i++) {
        char c = k->s[i];
        if (c == '\\') bputs(&b, "\\\\");
        else if (c == '\n') bputs(&b, "\\n");
        else if (c == '\t') bputs(&b, "\\t");
        else if (c == '\r') bputs(&b, "\\r");
        else if (c == qc) { bputs(&b, "\\"); bput(&b, &c, 1); }
        else bput(&b, &c, 1);
    }
    bput(&b, &qc, 1);
    return mkstr(b.s, b.len);
}

/* ── UTF-8 ── */
static int u8len(unsigned char c) { return c < 0x80 ? 1 : c < 0xE0 ? 2 : c < 0xF0 ? 3 : 4; }
static int64_t ucount(KStr *s) {
    int64_t n = 0;
    for (int64_t i = 0; i < s->len; i += u8len((unsigned char)s->s[i])) n++;
    return n;
}
static V kchars(V s) {
    KList *l = newlist(STR(s)->len);
    for (int64_t i = 0; i < STR(s)->len;) {
        int k = u8len((unsigned char)STR(s)->s[i]);
        lpush(l, mkstr(STR(s)->s + i, k)); i += k;
    }
    return mkp(T_LIST, l);
}

/* ── 相等与比较 ── */
static bool k_eq(V a, V b);
static bool seq_eq(V *a, V *b, int64_t n) {
    for (int64_t i = 0; i < n; i++) if (!k_eq(a[i], b[i])) return false;
    return true;
}
static bool k_eq(V a, V b) {
    if (a.t != b.t) {
        if (a.t == T_INT && b.t == T_FLOAT) return (double)a.u.i == b.u.f;
        if (a.t == T_FLOAT && b.t == T_INT) return a.u.f == (double)b.u.i;
        return false;
    }
    switch (a.t) {
    case T_UNIT: return true;
    case T_BOOL: case T_INT: return a.u.i == b.u.i;
    case T_FLOAT: return a.u.f == b.u.f;
    case T_STR: return STR(a)->len == STR(b)->len && memcmp(STR(a)->s, STR(b)->s, STR(a)->len) == 0;
    case T_LIST: return LIST(a)->len == LIST(b)->len && seq_eq(LIST(a)->a, LIST(b)->a, LIST(a)->len);
    case T_TUPLE: return TUP(a)->n == TUP(b)->n && seq_eq(TUP(a)->a, TUP(b)->a, TUP(a)->n);
    case T_STRUCT: return STRUCTV(a)->ty == STRUCTV(b)->ty && seq_eq(STRUCTV(a)->f, STRUCTV(b)->f, STRUCTV(a)->ty->n);
    case T_ENUM: return ENUMV(a)->var == ENUMV(b)->var && ENUMV(a)->n == ENUMV(b)->n && seq_eq(ENUMV(a)->a, ENUMV(b)->a, ENUMV(a)->n);
    case T_SET:
        if (MAP(a)->len != MAP(b)->len) return false;
        if (MAP(a)->root == MAP(b)->root) return true;
        { HL **es = map_entries(MAP(a));
          for (int64_t i = 0; i < MAP(a)->len; i++) if (!map_get(MAP(b), es[i]->k)) return false; }
        return true;
    case T_MAP:
        if (MAP(a)->len != MAP(b)->len) return false;
        if (MAP(a)->root == MAP(b)->root) return true;
        HL **es = map_entries(MAP(a));
        for (int64_t i = 0; i < MAP(a)->len; i++) {
            HL *o = map_get(MAP(b), es[i]->k);
            if (!o || !k_eq(es[i]->v, o->v)) return false;
        }
        return true;
    default: return a.u.p == b.u.p;
    }
}
static bool isnum(V v) { return v.t == T_INT || v.t == T_FLOAT; }
static double asf(V v) { return v.t == T_INT ? (double)v.u.i : v.u.f; }
static int k_cmp(V a, V b);
static int seq_cmp(V *a, int64_t na, V *b, int64_t nb) {
    for (int64_t i = 0; i < na && i < nb; i++) { int c = k_cmp(a[i], b[i]); if (c) return c; }
    return na < nb ? -1 : na > nb;
}
static int k_cmp(V a, V b) {
    if (a.t == T_INT && b.t == T_INT) return a.u.i < b.u.i ? -1 : a.u.i > b.u.i;
    if (isnum(a) && isnum(b)) { double x = asf(a), y = asf(b); return x < y ? -1 : x > y; }
    if (a.t == T_STR && b.t == T_STR) {
        int64_t n = STR(a)->len < STR(b)->len ? STR(a)->len : STR(b)->len;
        int c = memcmp(STR(a)->s, STR(b)->s, n);
        if (c) return c < 0 ? -1 : 1;
        return STR(a)->len < STR(b)->len ? -1 : STR(a)->len > STR(b)->len;
    }
    if (a.t == T_BOOL && b.t == T_BOOL) return (int)(a.u.i - b.u.i);
    if (a.t == T_LIST && b.t == T_LIST) return seq_cmp(LIST(a)->a, LIST(a)->len, LIST(b)->a, LIST(b)->len);
    if (a.t == T_TUPLE && b.t == T_TUPLE) return seq_cmp(TUP(a)->a, TUP(a)->n, TUP(b)->a, TUP(b)->n);
    die("不能比较 %s 和 %s", tname(a), tname(b));
    return 0;
}

/* ── 运算 ── */
static V k_add(V a, V b) {
    if (a.t == T_INT && b.t == T_INT) {
        int64_t r; if (__builtin_add_overflow(a.u.i, b.u.i, &r)) die("整数溢出"); return mkint(r);
    }
    if (isnum(a) && isnum(b)) return mkflt(asf(a) + asf(b));
    if (a.t == T_STR && b.t == T_STR) {
        if (STR(b)->len == 0) return a;
        if (STR(a)->len == 0) return b;
        return mkp(T_STR, str_append(STR(a), STR(b)->s, STR(b)->len));
    }
    if (a.t == T_LIST && b.t == T_LIST) {
        if (LIST(b)->len == 0) return a;
        return mkp(T_LIST, list_append(LIST(a), LIST(b)->a, LIST(b)->len));
    }
    die("不能把 %s 和 %s 相加", tname(a), tname(b)); return UNIT;
}
static void numchk(const char *op, V a, V b) {
    if (!isnum(a) || !isnum(b)) die("运算 %s 不支持 %s 和 %s", op, tname(a), tname(b));
}
static V k_sub(V a, V b) {
    if (a.t == T_INT && b.t == T_INT) {
        int64_t r; if (__builtin_sub_overflow(a.u.i, b.u.i, &r)) die("整数溢出"); return mkint(r);
    }
    numchk("-", a, b); return mkflt(asf(a) - asf(b));
}
static V k_mul(V a, V b) {
    if (a.t == T_INT && b.t == T_INT) {
        int64_t r; if (__builtin_mul_overflow(a.u.i, b.u.i, &r)) die("整数溢出"); return mkint(r);
    }
    numchk("*", a, b); return mkflt(asf(a) * asf(b));
}
static V k_div(V a, V b) {
    numchk("/", a, b);
    if (a.t == T_INT && b.t == T_INT) {
        if (b.u.i == 0) die("除以零");
        return mkint(a.u.i / b.u.i);   /* 向零截断，与参考实现一致 */
    }
    if (asf(b) == 0.0) die("除以零");
    return mkflt(asf(a) / asf(b));
}
static V k_mod(V a, V b) {
    numchk("%", a, b);
    if (a.t == T_INT && b.t == T_INT) { if (b.u.i == 0) die("除以零"); return mkint(a.u.i % b.u.i); }
    if (asf(b) == 0.0) die("除以零");
    return mkflt(fmod(asf(a), asf(b)));
}
static bool k_ordchk(const char *op, V a, V b) {
    if (!(isnum(a) && isnum(b)) && !(a.t == T_STR && b.t == T_STR))
        die("运算 %s 不支持 %s 和 %s", op, tname(a), tname(b));
    return true;
}
static V k_range(V a, V b, bool inc) {
    if (a.t != T_INT || b.t != T_INT) die("区间两端必须是 Int");
    int64_t hi = b.u.i + (inc ? 1 : 0);
    KList *l = newlist(hi > a.u.i ? hi - a.u.i : 0);
    for (int64_t i = a.u.i; i < hi; i++) l->a[l->len++] = mkint(i);
    return mkp(T_LIST, l);
}
static bool truthy(V v) {
    if (v.t != T_BOOL) die("条件必须是 Bool，实际是 %s", tname(v));
    return v.u.i != 0;
}

/* ── Map ── */
static uint64_t hmix(uint64_t h) { h ^= h >> 33; h *= 0xff51afd7ed558ccdULL; h ^= h >> 33; h *= 0xc4ceb9fe1a85ec53ULL; return h ^ (h >> 33); }
static uint64_t k_hash(V v);
static uint64_t seq_hash(V *a, int64_t n, uint64_t h) { for (int64_t i = 0; i < n; i++) h = hmix(h * 31 + k_hash(a[i])); return h; }
/* 与 k_eq 一致：整数值的 Float 和 Int 哈希相同 */
static uint64_t k_hash(V v) {
    switch (v.t) {
    case T_UNIT: return 1;
    case T_BOOL: case T_INT: return hmix((uint64_t)v.u.i);
    case T_FLOAT: {
        double f = v.u.f;
        if (f == f && f >= -9.2e18 && f <= 9.2e18 && (double)(int64_t)f == f) return hmix((uint64_t)(int64_t)f);
        if (f == 0) return hmix(0);
        uint64_t b; memcpy(&b, &f, 8); return hmix(b);
    }
    case T_STR: { uint64_t h = 1469598103934665603ULL; KStr *s = STR(v); for (int64_t i = 0; i < s->len; i++) h = (h ^ (uint8_t)s->s[i]) * 1099511628211ULL; return hmix(h); }
    case T_LIST: return seq_hash(LIST(v)->a, LIST(v)->len, 7);
    case T_TUPLE: return seq_hash(TUP(v)->a, TUP(v)->n, 11);
    case T_STRUCT: return seq_hash(STRUCTV(v)->f, STRUCTV(v)->ty->n, (uint64_t)(uintptr_t)STRUCTV(v)->ty);
    case T_ENUM: return seq_hash(ENUMV(v)->a, ENUMV(v)->n, 13 + (uint64_t)ENUMV(v)->var);
    case T_SET: { uint64_t h = 23; HL **es = map_entries(MAP(v)); for (int64_t i = 0; i < MAP(v)->len; i++) h += hmix(es[i]->h); return h; }
    case T_MAP: { uint64_t h = 17; HL **es = map_entries(MAP(v)); for (int64_t i = 0; i < MAP(v)->len; i++) h += hmix(es[i]->h * 31 + k_hash(es[i]->v)); return h; }
    default: return hmix((uint64_t)(uintptr_t)v.u.p);
    }
}

/* 持久化 Map：HAMT（哈希数组映射字典树），写入只复制根到叶的路径，O(log32 n)。
   每个叶子记录首次插入的序号 seq，遍历时按 seq 排序，保持与 Python dict 相同的插入顺序。 */
typedef struct { int kind; uint32_t bm; int n; void *s[]; } HN;             /* kind 1：分支，kind 2：哈希全同的冲突桶 */
#define HKIND(p) (*(int *)(p))
static HN *hn_new(int kind, uint32_t bm, int n) {
    HN *x = kalloc(sizeof(HN) + sizeof(void *) * (n ? n : 1)); x->kind = kind; x->bm = bm; x->n = n; return x;
}
static HL *hl_new(uint64_t h, int64_t seq, V k, V v) {
    HL *l = kalloc(sizeof(HL)); l->kind = 0; l->h = h; l->seq = seq; l->k = k; l->v = v; return l;
}
static HN *hn_copy(HN *a, int extra) {
    HN *x = hn_new(a->kind, a->bm, a->n + extra);
    memcpy(x->s, a->s, sizeof(void *) * a->n); return x;
}
static void *h_merge(HL *a, HL *b, int shift) {
    if (a->h == b->h || shift >= 64) { HN *c = hn_new(2, 0, 2); c->s[0] = a; c->s[1] = b; return c; }
    uint32_t ia = (a->h >> shift) & 31, ib = (b->h >> shift) & 31;
    if (ia == ib) { HN *x = hn_new(1, 1u << ia, 1); x->s[0] = h_merge(a, b, shift + 5); return x; }
    HN *x = hn_new(1, (1u << ia) | (1u << ib), 2);
    if (ia < ib) { x->s[0] = a; x->s[1] = b; } else { x->s[0] = b; x->s[1] = a; }
    return x;
}
static uint64_t h_first_hash(void *p) { while (HKIND(p) != 0) p = ((HN *)p)->s[0]; return ((HL *)p)->h; }
/* 返回新节点；*added 表示新增了键（否则是更新，保留原 seq） */
static void *h_put(void *p, int shift, HL *l, bool *added) {
    if (!p) { HN *x = hn_new(1, 1u << ((l->h >> shift) & 31), 1); x->s[0] = l; *added = true; return x; }
    if (HKIND(p) == 0) {
        HL *o = p;
        if (o->h == l->h && k_eq(o->k, l->k)) { l->seq = o->seq; *added = false; return l; }
        *added = true; return h_merge(o, l, shift);
    }
    HN *n = p;
    if (n->kind == 2) {
        uint64_t ch = ((HL *)n->s[0])->h;
        if (ch != l->h) {   /* 冲突桶下沉为分支 */
            HN *w = hn_new(1, 1u << ((ch >> shift) & 31), 1); w->s[0] = n;
            return h_put(w, shift, l, added);
        }
        for (int i = 0; i < n->n; i++)
            if (k_eq(((HL *)n->s[i])->k, l->k)) {
                HN *x = hn_copy(n, 0); l->seq = ((HL *)n->s[i])->seq; x->s[i] = l; *added = false; return x;
            }
        HN *x = hn_copy(n, 1); x->s[n->n] = l; *added = true; return x;
    }
    uint32_t bit = 1u << ((l->h >> shift) & 31); int pos = __builtin_popcount(n->bm & (bit - 1));
    if (!(n->bm & bit)) {
        HN *x = hn_new(1, n->bm | bit, n->n + 1);
        memcpy(x->s, n->s, sizeof(void *) * pos); x->s[pos] = l;
        memcpy(x->s + pos + 1, n->s + pos, sizeof(void *) * (n->n - pos));
        *added = true; return x;
    }
    HN *x = hn_copy(n, 0); x->s[pos] = h_put(n->s[pos], shift + 5, l, added); return x;
}
static HL *h_get(void *p, uint64_t h, V k) {
    for (int shift = 0; p; shift += 5) {
        if (HKIND(p) == 0) { HL *l = p; return l->h == h && k_eq(l->k, k) ? l : NULL; }
        HN *n = p;
        if (n->kind == 2) {
            for (int i = 0; i < n->n; i++) { HL *l = n->s[i]; if (l->h == h && k_eq(l->k, k)) return l; }
            return NULL;
        }
        uint32_t bit = 1u << ((h >> shift) & 31);
        if (!(n->bm & bit)) return NULL;
        p = n->s[__builtin_popcount(n->bm & (bit - 1))];
    }
    return NULL;
}
/* 删除：返回新节点（可能为 NULL 或单个叶子）；*removed 表示确实删掉了 */
static void *h_del(void *p, int shift, uint64_t h, V k, bool *removed) {
    if (HKIND(p) == 0) { HL *l = p; if (l->h == h && k_eq(l->k, k)) { *removed = true; return NULL; } return p; }
    HN *n = p;
    if (n->kind == 2) {
        for (int i = 0; i < n->n; i++) {
            HL *l = n->s[i];
            if (l->h == h && k_eq(l->k, k)) {
                *removed = true;
                if (n->n == 2) return n->s[1 - i];
                HN *x = hn_new(2, 0, n->n - 1);
                memcpy(x->s, n->s, sizeof(void *) * i); memcpy(x->s + i, n->s + i + 1, sizeof(void *) * (n->n - i - 1));
                return x;
            }
        }
        return p;
    }
    uint32_t bit = 1u << ((h >> shift) & 31);
    if (!(n->bm & bit)) return p;
    int pos = __builtin_popcount(n->bm & (bit - 1));
    void *c = h_del(n->s[pos], shift + 5, h, k, removed);
    if (!*removed) return p;
    if (c) { HN *x = hn_copy(n, 0); x->s[pos] = c; return x; }
    if (n->n == 1) return NULL;
    HN *x = hn_new(1, n->bm & ~bit, n->n - 1);
    memcpy(x->s, n->s, sizeof(void *) * pos); memcpy(x->s + pos, n->s + pos + 1, sizeof(void *) * (n->n - pos - 1));
    return x;
}
static void h_collect(void *p, HL **out, int64_t *n) {
    if (!p) return;
    if (HKIND(p) == 0) { out[(*n)++] = p; return; }
    HN *x = p; for (int i = 0; i < x->n; i++) h_collect(x->s[i], out, n);
}
static int hl_seq_cmp(const void *a, const void *b) {
    int64_t x = (*(HL **)a)->seq, y = (*(HL **)b)->seq; return x < y ? -1 : x > y;
}
#ifndef KITE_HMASK
#define KITE_HMASK (~0ULL)   /* 测试用：-DKITE_HMASK=7 人为制造大量哈希冲突 */
#endif
#define MHASH(k) (k_hash(k) & (uint64_t)(KITE_HMASK))
static KMap *newmap(int64_t cap) { (void)cap; KMap *m = kalloc(sizeof(KMap)); m->len = 0; m->seq = 0; m->root = NULL; return m; }
static KMap *map_put(KMap *m, V k, V v) {
    bool added = false; HL *l = hl_new(MHASH(k), m->seq, k, v);
    void *root = h_put(m->root, 0, l, &added);
    if (HKIND(root) == 0) { HN *w = hn_new(1, 1u << (l->h & 31), 1); w->s[0] = root; root = w; }
    KMap *r = kalloc(sizeof(KMap)); r->root = root; r->len = m->len + added; r->seq = m->seq + added; return r;
}
static KMap *map_del(KMap *m, V k) {
    if (!m->root) return m;
    bool removed = false; uint64_t h = MHASH(k);
    void *root = h_del(m->root, 0, h, k, &removed);
    if (!removed) return m;
    if (root && HKIND(root) != 1) { HN *w = hn_new(1, 1u << (h_first_hash(root) & 31), 1); w->s[0] = root; root = w; }
    KMap *r = kalloc(sizeof(KMap)); r->root = root; r->len = m->len - 1; r->seq = m->seq; return r;
}
static HL *map_get(KMap *m, V k) { return m->root ? h_get(m->root, MHASH(k), k) : NULL; }
/* 按插入顺序取出全部叶子 */
static HL **map_entries(KMap *m) {
    HL **a = kalloc(sizeof(HL *) * (m->len ? m->len : 1)); int64_t n = 0;
    h_collect(m->root, a, &n);
    qsort(a, n, sizeof(HL *), hl_seq_cmp);
    return a;
}

/* ── 调用 ── */
static V call_v(V f, int argc, V *argv) {
    if (f.t == T_FN) return FN(f)->code(FN(f), argc, argv);
    if (f.t == T_STYPE) {
        KSType *st = f.u.p;
        if (argc != st->n) die("%s 需要 %d 个字段", st->name, st->n);
        KStruct *s = kalloc(sizeof(KStruct) + sizeof(V) * st->n);
        s->ty = st; memcpy(s->f, argv, sizeof(V) * argc);
        return mkp(T_STRUCT, s);
    }
    die("%s 不能被调用", tname(f)); return UNIT;
}
static V call1(V f, V a) { return call_v(f, 1, &a); }
static V call2(V f, V a, V b) { V x[2] = { a, b }; return call_v(f, 2, x); }

static V ctor_code(KFn *self, int argc, V *argv) {
    if (argc != self->n) die("%s 需要 %d 个参数，给了 %d 个", SYMS[self->var], self->n, argc);
    return mkenum(self->tid, self->var, argc, self->names, argv);
}
static void arity(KFn *self, int want, int got) {
    if (want != got) die("%s 需要 %d 个参数，给了 %d 个", self->name, want, got);
}

/* ── upvalue ── */
static KUp *find_up(KUp **open, V *slot) {
    for (KUp *u = *open; u; u = u->next) if (u->ref == slot) return u;
    KUp *u = kalloc(sizeof(KUp)); u->ref = slot; u->next = *open; u->owner = cur_task; *open = u; return u;
}
#define TASK_WRITE_ERR "spawn 任务不能修改外层变量（该变量属于其他任务）"
static inline void k_store_up(KUp *u, V v) {
    KTask *o = u->owner;
    while (o->done) o = o->parent;          /* 已结束的任务把变量交还给父任务 */
    if (o != cur_task) die(TASK_WRITE_ERR);
    *u->ref = v;
}
static inline void k_store_g(V *g, V v) {
    if (cur_task != &MAIN_TASK) die(TASK_WRITE_ERR);
    *g = v;
}
static void close_ups(KUp **open, V *from);
static V call_v(V f, int argc, V *argv);
/* 展开到边界 b：依次执行各层未执行的 defer、关闭被捕获的变量，然后 longjmp */
static void __attribute__((noreturn)) unwind_to(Boundary *b, int code) {
    while (cur_frame != b->frame) {
        KFrame *f = cur_frame;
        while (*f->nd > 0) { (*f->nd)--; call_v(f->defers[*f->nd], 0, NULL); }
        if (*f->open) close_ups(f->open, f->L);
        cur_frame = f->prev;
    }
    cur_bound = b;
    longjmp(b->jb, code);
}
static void __attribute__((noreturn)) cancel_unwind(void) {
    __atomic_store_n(&cur_task->unwinding, 1, __ATOMIC_RELEASE);
    Boundary *b = cur_bound;
    while (b && !b->is_task) b = b->prev;     /* 取消穿过 recover 边界 */
    if (!b) fatal("内部错误：没有任务边界的任务被取消");
    unwind_to(b, 1);
}
static void __attribute__((noinline)) cancel_check(void) { if (task_cancelled(cur_task)) cancel_unwind(); }
#define CANCEL_POINT() do { if (__builtin_expect(__atomic_load_n(&cancel_epoch, __ATOMIC_RELAXED), 0)) cancel_check(); } while (0)
static void close_ups(KUp **open, V *from) {
    KUp **pp = open;
    while (*pp) {
        KUp *u = *pp;
        if (u->ref >= from) { u->closed = *u->ref; u->ref = &u->closed; *pp = u->next; }
        else pp = &u->next;
    }
}
static V mkclosure(KCode code, const char *name, int nup) {
    KFn *f = kalloc(sizeof(KFn) + sizeof(KUp *) * nup);
    memset(f, 0, sizeof(KFn)); f->code = code; f->name = name; f->nup = nup;
    return mkp(T_FN, f);
}

/* ── 下标 / 字段 ── */
static V k_index(V o, V i) {
    if (o.t == T_LIST || o.t == T_TUPLE) {
        int64_t n = o.t == T_LIST ? LIST(o)->len : TUP(o)->n;
        V *a = o.t == T_LIST ? LIST(o)->a : TUP(o)->a;
        if (i.t != T_INT) die("下标必须是 Int");
        int64_t k = i.u.i < 0 ? i.u.i + n : i.u.i;
        if (k < 0 || k >= n) die("下标越界: %s", cshow(i));
        return a[k];
    }
    if (o.t == T_STR) {
        if (i.t != T_INT) die("下标必须是 Int");
        V cs = kchars(o); return k_index(cs, i);
    }
    if (o.t == T_MAP) {
        HL *l = map_get(MAP(o), i);
        if (!l) die("下标越界: %s", cshow(i));
        return l->v;
    }
    die("%s 不支持下标", tname(o)); return UNIT;
}
static V k_windex(V o, V i, V v) {
    if (o.t == T_LIST) {
        KList *l = newlist(LIST(o)->len); memcpy(l->a, LIST(o)->a, sizeof(V) * LIST(o)->len); l->len = LIST(o)->len;
        if (i.t != T_INT) die("下标必须是 Int");
        int64_t k = i.u.i < 0 ? i.u.i + l->len : i.u.i;
        if (k < 0 || k >= l->len) die("下标越界: %s", cshow(i));
        l->a[k] = v; return mkp(T_LIST, l);
    }
    if (o.t == T_MAP) return mkp(T_MAP, map_put(MAP(o), i, v));
    die("%s 不支持下标赋值", tname(o)); return UNIT;
}
/* xs[i] = v，xs 是编译期确认没有别名的局部变量：第一次写复制，之后原地写 */
static V k_windex_l(int *own, V o, V i, V v) {
    if (*own && o.t == T_LIST) {
        KList *l = LIST(o);
        if (i.t != T_INT) die("下标必须是 Int");
        int64_t k = i.u.i < 0 ? i.u.i + l->len : i.u.i;
        if (k < 0 || k >= l->len) die("下标越界: %s", cshow(i));
        l->a[k] = v; return o;
    }
    V r = k_windex(o, i, v); *own = r.t == T_LIST; return r;
}
static int sfield(KStruct *s, int sym) {
    for (int i = 0; i < s->ty->n; i++) if (s->ty->fsyms[i] == sym) return i;
    return -1;
}
static V module_get(const char *mod, const char *name);
static V etype_member(KEType *et, int sym);
static V k_field(V o, int sym) {
    if (o.t == T_STRUCT) { int i = sfield(STRUCTV(o), sym); if (i >= 0) return STRUCTV(o)->f[i]; }
    if (o.t == T_MODULE) return module_get((const char *)o.u.p, SYMS[sym]);
    if (o.t == T_ETYPE) return etype_member(o.u.p, sym);
    die("%s 没有字段 %s", tname(o), SYMS[sym]); return UNIT;
}
static V k_wfield(V o, int sym, V v) {
    if (o.t != T_STRUCT) die("只能给结构体字段赋值");
    KStruct *s = STRUCTV(o);
    int i = sfield(s, sym);
    if (i < 0) die("%s 没有字段 %s", s->ty->name, SYMS[sym]);
    KStruct *n = kalloc(sizeof(KStruct) + sizeof(V) * s->ty->n);
    n->ty = s->ty; memcpy(n->f, s->f, sizeof(V) * s->ty->n); n->f[i] = v;
    return mkp(T_STRUCT, n);
}
static V k_tidx(V o, int i) {
    if (o.t == T_TUPLE && i < TUP(o)->n) return TUP(o)->a[i];
    if (o.t == T_ENUM && i < ENUMV(o)->n) return ENUMV(o)->a[i];
    die("%s 不能用 .%d 取元素", tname(o), i); return UNIT;
}
static V k_interp(int n, V *parts) {
    Buf b = { 0 };
    for (int i = 0; i < n; i++) show_to(&b, parts[i], false);
    return mkstr(b.s ? b.s : "", b.len);
}

/* ── 迭代 ── */
static V to_list(V x) {
    if (x.t == T_LIST) return x;
    if (x.t == T_TUPLE) return mklist(TUP(x)->n, TUP(x)->a);
    if (x.t == T_STR) return kchars(x);
    if (x.t == T_MAP) {
        KList *l = newlist(MAP(x)->len);
        HL **es = map_entries(MAP(x));
        for (int64_t i = 0; i < MAP(x)->len; i++) { V kv[2] = { es[i]->k, es[i]->v }; lpush(l, mktuple(2, kv)); }
        return mkp(T_LIST, l);
    }
    if (x.t == T_SET) {
        KList *l = newlist(MAP(x)->len);
        HL **es = map_entries(MAP(x));
        for (int64_t i = 0; i < MAP(x)->len; i++) lpush(l, es[i]->k);
        return mkp(T_LIST, l);
    }
    die("%s 不可迭代", tname(x)); return UNIT;
}
static bool chan_recv(KChan *c, V *out);
static V k_iter(V x) {
    KIter *it = kalloc(sizeof(KIter));
    if (x.t == T_CHAN) { it->ch = x.u.p; it->l = NULL; }
    else { it->l = LIST(to_list(x)); it->ch = NULL; }
    it->i = 0;
    return mkp(T_ITER, it);
}
static inline bool k_next(V it, V *out) {
    KIter *k = it.u.p;
    if (__builtin_expect(k->ch != NULL, 0)) return chan_recv(k->ch, out);
    if (k->i >= k->l->len) return false;
    *out = k->l->a[k->i++]; return true;
}

/* ── 结构体字面量 ── */
static V k_struct(KSType *st, int n, const int *syms, V *vals) {
    KStruct *s = kalloc(sizeof(KStruct) + sizeof(V) * st->n);
    s->ty = st;
    for (int i = 0; i < st->n; i++) {
        int j;
        for (j = 0; j < n; j++) if (syms[j] == st->fsyms[i]) break;
        if (j == n) die("%s 缺少字段: %s", st->name, SYMS[st->fsyms[i]]);
        s->f[i] = vals[j];
    }
    if (n != st->n) die("%s 字段数量不对", st->name);
    return mkp(T_STRUCT, s);
}

/* ── 内建函数 ── */
#define B(name) static V name(KFn *self, int argc, V *argv)
#define NARGS(lo, hi) if (argc < (lo) || argc > (hi)) die("调用 %s 参数个数错误", self->name)
static V isvar(V v, int sym) { return mkbool(v.t == T_ENUM && ENUMV(v)->var == sym); }

B(b_print) {
    (void)self;
    Buf b = { 0 };
    for (int i = 0; i < argc; i++) { if (i) bputs(&b, " "); show_to(&b, argv[i], false); }
    bputs(&b, "\n"); fwrite(b.s, 1, b.len, stdout); return UNIT;
}
B(b_len) {
    NARGS(1, 1); V x = argv[0];
    switch (x.t) {
    case T_STR: return mkint(ucount(STR(x)));
    case T_LIST: return mkint(LIST(x)->len);
    case T_TUPLE: return mkint(TUP(x)->n);
    case T_MAP: case T_SET: return mkint(MAP(x)->len);
    }
    die("%s 没有长度", tname(x)); return UNIT;
}
B(b_str) { NARGS(1, 1); return kshow(argv[0], false); }
B(b_repeat) {
    NARGS(2, 2); if (argv[1].t != T_INT) die("[x; n] 的次数必须是 Int");
    int64_t n = argv[1].u.i; if (n < 0) die("[x; n] 的次数不能为负: %lld", (long long)n);
    KList *l = newlist(n); for (int64_t i = 0; i < n; i++) l->a[l->len++] = argv[0];
    return mkp(T_LIST, l);
}
/* 字符串插值的格式说明 {x:spec}：[[填充]对齐][0][宽度][.精度][x|X|o|b|e|?]，与 kite.py 的 fmt_value 一致 */
static int64_t u8count(const char *s, int64_t len) {
    int64_t n = 0; for (int64_t i = 0; i < len; i += u8len((unsigned char)s[i])) n++; return n;
}
B(b_fmt) {
    NARGS(2, 2); V v = argv[0]; KStr *sp = STR(argv[1]); const char *p = cstrk(sp), *end = p + sp->len;
    char fill[8] = " "; int fl = 1; char align = 0;
    if (p < end) {   /* 填充字符（可能是多字节）后跟对齐符，或只有对齐符 */
        int k = u8len((unsigned char)*p);
        if (p + k < end && strchr("<>^", p[k]) && p[k]) { memcpy(fill, p, k); fl = k; align = p[k]; p += k + 1; }
        else if (strchr("<>^", *p) && *p) { align = *p; p++; }
    }
    int zero = 0; if (p < end && *p == '0') { zero = 1; p++; }
    int64_t width = 0; while (p < end && *p >= '0' && *p <= '9') width = width * 10 + (*p++ - '0');
    int64_t prec = -1;
    if (p < end && *p == '.') { p++; prec = 0; while (p < end && *p >= '0' && *p <= '9') prec = prec * 10 + (*p++ - '0'); }
    char ty = p < end ? *p : 0;
    if ((ty && !strchr("xXobe?", ty)) || p + (ty ? 1 : 0) != end) die("无效的格式说明 {:%s}", cstrk(sp));
    bool num = v.t == T_INT || v.t == T_FLOAT;
    Buf b = { 0 };
    if (ty == '?') { show_to(&b, v, true); }
    else if (ty == 'x' || ty == 'X' || ty == 'o' || ty == 'b') {
        if (v.t != T_INT) die("格式 {:%s} 需要 Int，实际是 %s", cstrk(sp), tname(v));
        uint64_t u = v.u.i < 0 ? (uint64_t)0 - (uint64_t)v.u.i : (uint64_t)v.u.i;
        char tmp[80]; int n = 0; int base = ty == 'o' ? 8 : ty == 'b' ? 2 : 16;
        const char *dg = ty == 'X' ? "0123456789ABCDEF" : "0123456789abcdef";
        do { tmp[n++] = dg[u % base]; u /= base; } while (u);
        if (v.u.i < 0) bputs(&b, "-");
        while (n) { char c = tmp[--n]; bput(&b, &c, 1); }
    } else if (ty == 'e') {
        if (!num) die("格式 {:%s} 需要数值，实际是 %s", cstrk(sp), tname(v));
        double d = v.t == T_INT ? (double)v.u.i : v.u.f; char tmp[512];
        snprintf(tmp, sizeof tmp, "%.*e", (int)(prec < 0 ? 6 : prec), d);
        if (isinf(d) || isnan(d)) snprintf(tmp, sizeof tmp, "%s", isnan(d) ? "nan" : d > 0 ? "inf" : "-inf");
        bputs(&b, tmp);
    } else if (prec >= 0) {
        if (num) {
            double d = v.t == T_INT ? (double)v.u.i : v.u.f; int need = snprintf(NULL, 0, "%.*f", (int)prec, d);
            char *tmp = kalloc_atomic(need + 1); snprintf(tmp, need + 1, "%.*f", (int)prec, d);
            if (isinf(d) || isnan(d)) bputs(&b, isnan(d) ? "nan" : d > 0 ? "inf" : "-inf"); else bput(&b, tmp, need);
        } else if (v.t == T_STR) {
            KStr *s = STR(v); int64_t i = 0, c = 0;
            while (i < s->len && c < prec) { i += u8len((unsigned char)s->s[i]); c++; }
            bput(&b, s->s, i);
        } else die("格式 {:%s} 的精度只能用于数值或 Str，实际是 %s", cstrk(sp), tname(v));
    } else { V s = kshow(v, false); bput(&b, CSTR(s), STR(s)->len); }
    int64_t pad = width - u8count(b.s ? b.s : "", b.len);
    if (pad <= 0) return mkstr(b.s ? b.s : "", b.len);
    Buf o = { 0 };
    if (zero && num && !align) {
        int sg = b.len && (b.s[0] == '-' || b.s[0] == '+');
        bput(&o, b.s, sg); for (int64_t i = 0; i < pad; i++) bputs(&o, "0"); bput(&o, b.s + sg, b.len - sg);
        return mkstr(o.s, o.len);
    }
    if (!align) align = num ? '>' : '<';
    int64_t l = align == '<' ? 0 : align == '>' ? pad : pad / 2, r = pad - l;
    for (int64_t i = 0; i < l; i++) bput(&o, fill, fl);
    bput(&o, b.s ? b.s : "", b.len);
    for (int64_t i = 0; i < r; i++) bput(&o, fill, fl);
    return mkstr(o.s, o.len);
}
static int64_t f2i_ck(double f) {
    if (!(f >= -9223372036854775808.0 && f < 9223372036854775808.0)) die("整数溢出");
    return (int64_t)f;
}
B(b_int) {
    NARGS(1, 1); V x = argv[0];
    if (x.t == T_INT) return x;
    if (x.t == T_FLOAT) return mkint(f2i_ck(x.u.f));
    if (x.t == T_BOOL) return mkint(x.u.i);
    if (x.t == T_STR) {
        char *e; errno = 0; long long r = strtoll(CSTR(x), &e, 10);
        if (*e) die("int: 无法转换 %s", cshow(x));
        if (errno == ERANGE) die("整数溢出");
        return mkint(r);
    }
    die("int: 无法转换 %s", tname(x)); return UNIT;
}
B(b_float) {
    NARGS(1, 1); V x = argv[0];
    if (x.t == T_FLOAT) return x;
    if (x.t == T_INT) return mkflt((double)x.u.i);
    if (x.t == T_STR) { char *e; double r = strtod(CSTR(x), &e); if (*e) die("float: 无法转换 %s", cshow(x)); return mkflt(r); }
    die("float: 无法转换 %s", tname(x)); return UNIT;
}
B(b_type_of) { NARGS(1, 1); return cstr(tname(argv[0])); }
B(b_map) {
    NARGS(2, 2); KList *s = LIST(to_list(argv[0])); KList *l = newlist(s->len);
    for (int64_t i = 0; i < s->len; i++) l->a[l->len++] = call1(argv[1], s->a[i]);
    return mkp(T_LIST, l);
}
B(b_filter) {
    NARGS(2, 2); KList *s = LIST(to_list(argv[0])); KList *l = newlist(s->len);
    for (int64_t i = 0; i < s->len; i++) { V r = call1(argv[1], s->a[i]); if (r.t == T_BOOL && r.u.i) lpush(l, s->a[i]); }
    return mkp(T_LIST, l);
}
B(b_each) {
    NARGS(2, 2); KList *s = LIST(to_list(argv[0]));
    for (int64_t i = 0; i < s->len; i++) call1(argv[1], s->a[i]);
    return UNIT;
}
B(b_reduce) {
    NARGS(3, 3); KList *s = LIST(to_list(argv[0])); V acc = argv[1];
    for (int64_t i = 0; i < s->len; i++) acc = call2(argv[2], acc, s->a[i]);
    return acc;
}
B(b_sum) {
    NARGS(1, 1); KList *s = LIST(to_list(argv[0])); V acc = mkint(0);
    for (int64_t i = 0; i < s->len; i++) acc = k_add(acc, s->a[i]);
    return acc;
}
B(b_count) {
    NARGS(1, 2); KList *s = LIST(to_list(argv[0]));
    if (argc == 1) return mkint(s->len);
    int64_t n = 0;
    for (int64_t i = 0; i < s->len; i++) { V r = call1(argv[1], s->a[i]); if (r.t == T_BOOL && r.u.i) n++; }
    return mkint(n);
}
B(b_any) {
    NARGS(2, 2); KList *s = LIST(to_list(argv[0]));
    for (int64_t i = 0; i < s->len; i++) { V r = call1(argv[1], s->a[i]); if (r.t == T_BOOL && r.u.i) return mkbool(true); }
    return mkbool(false);
}
B(b_all) {
    NARGS(2, 2); KList *s = LIST(to_list(argv[0]));
    for (int64_t i = 0; i < s->len; i++) { V r = call1(argv[1], s->a[i]); if (!(r.t == T_BOOL && r.u.i)) return mkbool(false); }
    return mkbool(true);
}
B(b_find) {
    NARGS(2, 2); KList *s = LIST(to_list(argv[0]));
    for (int64_t i = 0; i < s->len; i++) { V r = call1(argv[1], s->a[i]); if (r.t == T_BOOL && r.u.i) return kSome(s->a[i]); }
    return kNone();
}
B(b_first) { NARGS(1, 1); KList *s = LIST(to_list(argv[0])); return s->len ? kSome(s->a[0]) : kNone(); }
B(b_last) { NARGS(1, 1); KList *s = LIST(to_list(argv[0])); return s->len ? kSome(s->a[s->len - 1]) : kNone(); }
B(b_get) {
    NARGS(2, 2); V x = argv[0], i = argv[1];
    if (x.t == T_MAP) { HL *l = map_get(MAP(x), i); return l ? kSome(l->v) : kNone(); }
    if (x.t == T_LIST && i.t == T_INT) {
        int64_t n = LIST(x)->len, k = i.u.i < 0 ? i.u.i + n : i.u.i;
        return (i.u.i >= -n && i.u.i < n) ? kSome(LIST(x)->a[k]) : kNone();
    }
    die("get 不支持 %s", tname(x)); return UNIT;
}
static V slice(KList *s, int64_t a, int64_t b) {
    if (a < 0) a = 0; if (b > s->len) b = s->len; if (b < a) b = a;
    return mklist((int)(b - a), s->a + a);
}
B(b_take) { NARGS(2, 2); KList *s = LIST(to_list(argv[0])); int64_t n = argv[1].u.i; return slice(s, 0, n < 0 ? s->len + n : n); }
B(b_skip) { NARGS(2, 2); KList *s = LIST(to_list(argv[0])); int64_t n = argv[1].u.i; return slice(s, n < 0 ? s->len + n : n, s->len); }
B(b_reverse) {
    NARGS(1, 1); V x = argv[0];
    KList *s = LIST(to_list(x)); KList *l = newlist(s->len);
    for (int64_t i = s->len - 1; i >= 0; i--) l->a[l->len++] = s->a[i];
    if (x.t == T_STR) { Buf b = { 0 }; for (int64_t i = 0; i < l->len; i++) bput(&b, STR(l->a[i])->s, STR(l->a[i])->len); return mkstr(b.s ? b.s : "", b.len); }
    return mkp(T_LIST, l);
}
static __thread V sort_key_fn;
static void msort(V *a, V *k, V *ta, V *tk, int64_t n) {
    if (n < 2) return;
    int64_t m = n / 2;
    msort(a, k, ta, tk, m); msort(a + m, k + m, ta, tk, n - m);
    int64_t i = 0, j = m, o = 0;
    while (i < m && j < n) {
        if (k_cmp(k[j], k[i]) < 0) { ta[o] = a[j]; tk[o++] = k[j++]; }
        else { ta[o] = a[i]; tk[o++] = k[i++]; }
    }
    while (i < m) { ta[o] = a[i]; tk[o++] = k[i++]; }
    while (j < n) { ta[o] = a[j]; tk[o++] = k[j++]; }
    memcpy(a, ta, sizeof(V) * n); memcpy(k, tk, sizeof(V) * n);
}
static void msort_with(V f, V *a, V *ta, int64_t n) {
    if (n < 2) return;
    int64_t m = n / 2;
    msort_with(f, a, ta, m); msort_with(f, a + m, ta, n - m);
    int64_t i = 0, j = m, o = 0;
    while (i < m && j < n) {
        V r = call2(f, a[j], a[i]);
        if (r.t != T_INT) die("sort_with 的比较函数必须返回 Int");
        if (r.u.i < 0) ta[o++] = a[j++]; else ta[o++] = a[i++];
    }
    while (i < m) ta[o++] = a[i++];
    while (j < n) ta[o++] = a[j++];
    memcpy(a, ta, sizeof(V) * n);
}
B(b_sort_with) {
    NARGS(2, 2); KList *s = LIST(to_list(argv[0])); int64_t n = s->len;
    V *a = kalloc(sizeof(V) * (n + 1)), *ta = kalloc(sizeof(V) * (n + 1));
    for (int64_t i = 0; i < n; i++) a[i] = s->a[i];
    msort_with(argv[1], a, ta, n);
    KList *l = newlist(n); memcpy(l->a, a, sizeof(V) * n); l->len = n; return mkp(T_LIST, l);
}
B(b_sort) {
    NARGS(1, 2); KList *s = LIST(to_list(argv[0])); int64_t n = s->len;
    V *a = kalloc(sizeof(V) * (n + 1)), *k = kalloc(sizeof(V) * (n + 1));
    V *ta = kalloc(sizeof(V) * (n + 1)), *tk = kalloc(sizeof(V) * (n + 1));
    for (int64_t i = 0; i < n; i++) { a[i] = s->a[i]; k[i] = argc == 2 ? call1(argv[1], s->a[i]) : s->a[i]; }
    msort(a, k, ta, tk, n);
    KList *l = newlist(n); memcpy(l->a, a, sizeof(V) * n); l->len = n; return mkp(T_LIST, l);
}
B(b_zip) {
    NARGS(2, 2); KList *a = LIST(to_list(argv[0])), *b = LIST(to_list(argv[1]));
    int64_t n = a->len < b->len ? a->len : b->len; KList *l = newlist(n);
    for (int64_t i = 0; i < n; i++) { V p[2] = { a->a[i], b->a[i] }; l->a[l->len++] = mktuple(2, p); }
    return mkp(T_LIST, l);
}
B(b_enumerate) {
    NARGS(1, 1); KList *s = LIST(to_list(argv[0])); KList *l = newlist(s->len);
    for (int64_t i = 0; i < s->len; i++) { V p[2] = { mkint(i), s->a[i] }; l->a[l->len++] = mktuple(2, p); }
    return mkp(T_LIST, l);
}
B(b_push) {
    NARGS(2, 2); KList *s = LIST(to_list(argv[0]));
    return mkp(T_LIST, list_append(s, &argv[1], 1));
}
B(b_concat) { NARGS(2, 2); return k_add(to_list(argv[0]), to_list(argv[1])); }
B(b_contains) {
    NARGS(2, 2); V x = argv[0], y = argv[1];
    if (x.t == T_STR && y.t == T_STR) {
        if (STR(y)->len == 0) return mkbool(true);
        for (int64_t i = 0; i + STR(y)->len <= STR(x)->len; i++)
            if (memcmp(STR(x)->s + i, STR(y)->s, STR(y)->len) == 0) return mkbool(true);
        return mkbool(false);
    }
    if (x.t == T_MAP || x.t == T_SET) return mkbool(map_get(MAP(x), y) != NULL);
    KList *s = LIST(to_list(x));
    for (int64_t i = 0; i < s->len; i++) if (k_eq(s->a[i], y)) return mkbool(true);
    return mkbool(false);
}
B(b_join) {
    NARGS(1, 2); KList *s = LIST(to_list(argv[0])); Buf b = { 0 };
    for (int64_t i = 0; i < s->len; i++) {
        if (i && argc == 2) bput(&b, STR(argv[1])->s, STR(argv[1])->len);
        show_to(&b, s->a[i], false);
    }
    return mkstr(b.s ? b.s : "", b.len);
}
static bool isws(char c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f'; }
B(b_split) {
    NARGS(1, 2); KStr *s = STR(argv[0]); KList *l = newlist(4);
    if (argc == 1 || STR(argv[1])->len == 0) {
        int64_t i = 0;
        while (i < s->len) {
            while (i < s->len && isws(s->s[i])) i++;
            int64_t j = i; while (j < s->len && !isws(s->s[j])) j++;
            if (j > i) lpush(l, mkstr(s->s + i, j - i));
            i = j;
        }
    } else {
        KStr *sep = STR(argv[1]); int64_t st = 0;
        for (int64_t i = 0; i + sep->len <= s->len;) {
            if (memcmp(s->s + i, sep->s, sep->len) == 0) { lpush(l, mkstr(s->s + st, i - st)); i += sep->len; st = i; }
            else i++;
        }
        lpush(l, mkstr(s->s + st, s->len - st));
    }
    return mkp(T_LIST, l);
}
B(b_trim) {
    NARGS(1, 1); KStr *s = STR(argv[0]); int64_t a = 0, b = s->len;
    while (a < b && isws(s->s[a])) a++;
    while (b > a && isws(s->s[b - 1])) b--;
    return mkstr(s->s + a, b - a);
}
#include "kite_case.h"
static const KCase *case_find(uint32_t cp, bool up) {
    const KCase *t = up ? KCASE_UP : KCASE_LO; int64_t lo = 0, hi = (up ? KCASE_UP_N : KCASE_LO_N) - 1;
    while (lo <= hi) { int64_t mid = (lo + hi) / 2; if (t[mid].cp == cp) return &t[mid]; if (t[mid].cp < cp) lo = mid + 1; else hi = mid - 1; }
    return NULL;
}
static uint32_t utf8_at(const char *s, int64_t len, int64_t i, int *n) {
    uint8_t c = (uint8_t)s[i];
    int k = c < 0x80 ? 1 : c >= 0xF0 ? 4 : c >= 0xE0 ? 3 : c >= 0xC0 ? 2 : 1;
    if (i + k > len) k = 1;
    uint32_t cp = k == 1 ? c : k == 2 ? c & 0x1F : k == 3 ? c & 0x0F : c & 0x07;
    for (int j = 1; j < k; j++) cp = (cp << 6) | ((uint8_t)s[i + j] & 0x3F);
    *n = k; return cp;
}
static bool is_cased(uint32_t cp) {
    if (cp < 0x80) return (cp | 32) >= 'a' && (cp | 32) <= 'z';
    return case_find(cp, true) || case_find(cp, false);
}
/* 与 Python str.upper/lower 一致（含 ß→SS 等多字符映射与希腊语词尾 σ→ς） */
static V casemap(V x, bool up) {
    KStr *s = STR(x); Buf b = { 0 }; bool prev_cased = false;
    for (int64_t i = 0; i < s->len;) {
        int n; uint32_t cp = utf8_at(s->s, s->len, i, &n);
        if (cp < 0x80) {
            char c = (char)cp;
            if (up && c >= 'a' && c <= 'z') c -= 32;
            if (!up && c >= 'A' && c <= 'Z') c += 32;
            bput(&b, &c, 1);
        } else if (!up && cp == 0x3A3) {
            bool next_cased = false;
            int64_t j = i + n;
            if (j < s->len) { int m; next_cased = is_cased(utf8_at(s->s, s->len, j, &m)); }
            bput(&b, prev_cased && !next_cased ? "\xcf\x82" : "\xcf\x83", 2);
        } else {
            const KCase *c = case_find(cp, up);
            if (c) bput(&b, c->s, c->n); else bput(&b, s->s + i, n);
        }
        prev_cased = is_cased(cp);
        i += n;
    }
    return mkstr(b.s ? b.s : "", b.len);
}
B(b_upper) { NARGS(1, 1); return casemap(argv[0], true); }
B(b_lower) { NARGS(1, 1); return casemap(argv[0], false); }
B(b_starts_with) {
    NARGS(2, 2); KStr *s = STR(argv[0]), *p = STR(argv[1]);
    return mkbool(p->len <= s->len && memcmp(s->s, p->s, p->len) == 0);
}
B(b_ends_with) {
    NARGS(2, 2); KStr *s = STR(argv[0]), *p = STR(argv[1]);
    return mkbool(p->len <= s->len && memcmp(s->s + s->len - p->len, p->s, p->len) == 0);
}
B(b_replace) {
    NARGS(3, 3); KStr *s = STR(argv[0]), *a = STR(argv[1]), *r = STR(argv[2]); Buf b = { 0 };
    if (a->len == 0) {          /* 空模式：每个字符前和末尾各插一次（和 VM / Python / Go 相同） */
        for (int64_t i = 0; i < s->len; i++) {
            if (((unsigned char)s->s[i] & 0xC0) != 0x80) bput(&b, r->s, r->len);
            bput(&b, s->s + i, 1);
        }
        bput(&b, r->s, r->len);
        return mkstr(b.s ? b.s : "", b.len);
    }
    for (int64_t i = 0; i < s->len;) {
        if (i + a->len <= s->len && memcmp(s->s + i, a->s, a->len) == 0) { bput(&b, r->s, r->len); i += a->len; }
        else { bput(&b, s->s + i, 1); i++; }
    }
    return mkstr(b.s ? b.s : "", b.len);
}
B(b_chars) { NARGS(1, 1); return kchars(argv[0]); }
static bool parse_int_str(KStr *s, int64_t *out) {
    int64_t a = 0, b = s->len;
    while (a < b && isws(s->s[a])) a++;
    while (b > a && isws(s->s[b - 1])) b--;
    if (a == b) return false;
    bool neg = false;
    if (s->s[a] == '+' || s->s[a] == '-') { neg = s->s[a] == '-'; a++; }
    if (a == b) return false;
    int64_t r = 0; char prev = 0;
    for (int64_t i = a; i < b; i++) {
        char c = s->s[i];
        if (c == '_' && prev && prev != '_' && i + 1 < b) { prev = c; continue; }
        if (c < '0' || c > '9') return false;
        if (__builtin_mul_overflow(r, 10, &r) || __builtin_add_overflow(r, c - '0', &r)) return false;
        prev = c;
    }
    *out = neg ? -r : r; return true;
}
B(b_parse_int) {
    NARGS(1, 1); int64_t r;
    if (parse_int_str(STR(argv[0]), &r)) return kOk(mkint(r));
    V m = k_add(cstr("无法解析为整数: "), krepr(argv[0])); return kErr(m);
}
B(b_parse_float) {
    NARGS(1, 1); char *e; const char *s = CSTR(argv[0]);
    while (isws(*s)) s++;
    double r = strtod(s, &e);
    while (*e && isws(*e)) e++;
    if (e == s || *e) { V m = k_add(cstr("无法解析为浮点数: "), krepr(argv[0])); return kErr(m); }
    return kOk(mkflt(r));
}
static V map_column(KMap *m, bool keys) {
    HL **es = map_entries(m); KList *l = newlist(m->len);
    for (int64_t i = 0; i < m->len; i++) lpush(l, keys ? es[i]->k : es[i]->v);
    return mkp(T_LIST, l);
}
B(b_keys) { NARGS(1, 1); if (argv[0].t != T_MAP) die("keys 需要 Map"); return map_column(MAP(argv[0]), true); }
B(b_values) { NARGS(1, 1); if (argv[0].t != T_MAP) die("values 需要 Map"); return map_column(MAP(argv[0]), false); }
B(b_remove) {
    NARGS(2, 2); if (argv[0].t != T_MAP && argv[0].t != T_SET) die("remove 只能用于 Map 或 Set");
    return mkp(argv[0].t, map_del(MAP(argv[0]), argv[1]));
}
/* ── Set：KMap 的键，值固定为 Unit ── */
static KMap *set_add(KMap *m, V x) { return map_get(m, x) ? m : map_put(m, x, UNIT); }
static KMap *need_set(V x, const char *f) { if (x.t != T_SET) die("%s 需要 Set", f); return MAP(x); }
B(b_Set) {
    NARGS(0, 1); KMap *m = newmap(4);
    if (argc == 1) { KList *s = LIST(to_list(argv[0])); for (int64_t i = 0; i < s->len; i++) m = set_add(m, s->a[i]); }
    return mkp(T_SET, m);
}
B(b_insert) { NARGS(2, 2); return mkp(T_SET, set_add(need_set(argv[0], "insert"), argv[1])); }
B(b_union) {
    NARGS(2, 2); KMap *a = need_set(argv[0], "union"), *b = need_set(argv[1], "union");
    HL **es = map_entries(b);
    for (int64_t i = 0; i < b->len; i++) a = set_add(a, es[i]->k);
    return mkp(T_SET, a);
}
static V set_filter(V av, V bv, const char *f, bool keep_in) {
    KMap *a = need_set(av, f), *b = need_set(bv, f), *r = newmap(4);
    HL **es = map_entries(a);
    for (int64_t i = 0; i < a->len; i++) if ((map_get(b, es[i]->k) != NULL) == keep_in) r = map_put(r, es[i]->k, UNIT);
    return mkp(T_SET, r);
}
B(b_intersect) { NARGS(2, 2); return set_filter(argv[0], argv[1], "intersect", true); }
B(b_difference) { NARGS(2, 2); return set_filter(argv[0], argv[1], "difference", false); }
B(b_is_subset) {
    NARGS(2, 2); KMap *a = need_set(argv[0], "is_subset"), *b = need_set(argv[1], "is_subset");
    if (a->len > b->len) return mkbool(false);
    HL **es = map_entries(a);
    for (int64_t i = 0; i < a->len; i++) if (!map_get(b, es[i]->k)) return mkbool(false);
    return mkbool(true);
}
B(b_to_list) { NARGS(1, 1); return to_list(argv[0]); }
B(b_set) { NARGS(3, 3); return k_windex(argv[0], argv[1], argv[2]); }
B(b_Map) {
    NARGS(0, 1); KMap *m = newmap(4);
    if (argc == 1) {
        KList *s = LIST(to_list(argv[0]));
        for (int64_t i = 0; i < s->len; i++) {
            V p = s->a[i]; if (p.t != T_TUPLE || TUP(p)->n != 2) die("Map 需要 (键, 值) 列表");
            m = map_put(m, TUP(p)->a[0], TUP(p)->a[1]);
        }
    }
    return mkp(T_MAP, m);
}
static V minmax(KFn *self, int argc, V *argv, int sign) {
    V *a = argv; int64_t n = argc;
    if (argc == 1) { KList *s = LIST(to_list(argv[0])); a = s->a; n = s->len; }
    if (n == 0) die("%s: 空序列", self->name);
    V best = a[0];
    for (int64_t i = 1; i < n; i++) if (k_cmp(a[i], best) * sign < 0) best = a[i];
    return best;
}
B(b_min) { return minmax(self, argc, argv, 1); }
B(b_max) { return minmax(self, argc, argv, -1); }
B(b_abs) {
    NARGS(1, 1); V x = argv[0];
    if (x.t == T_INT) { if (x.u.i == INT64_MIN) die("整数溢出"); return mkint(x.u.i < 0 ? -x.u.i : x.u.i); }
    if (x.t == T_FLOAT) return mkflt(fabs(x.u.f));
    die("abs 不支持 %s", tname(x)); return UNIT;
}
B(b_Ok) { NARGS(0, 1); return kOk(argc ? argv[0] : UNIT); }
B(b_Err) { NARGS(1, 1); return kErr(argv[0]); }
B(b_Some) { NARGS(1, 1); return kSome(argv[0]); }
B(b_unwrap) {
    NARGS(1, 1); V v = argv[0];
    if (v.t == T_ENUM && (ENUMV(v)->var == SYM_Ok || ENUMV(v)->var == SYM_Some)) return ENUMV(v)->a[0];
    die("unwrap 失败: %s", CSTR(kshow(v, false))); return UNIT;
}
B(b_unwrap_or) {
    NARGS(2, 2); V v = argv[0];
    if (v.t == T_ENUM && (ENUMV(v)->var == SYM_Ok || ENUMV(v)->var == SYM_Some)) return ENUMV(v)->a[0];
    return argv[1];
}
B(b_is_ok) { NARGS(1, 1); return isvar(argv[0], SYM_Ok); }
B(b_is_err) { NARGS(1, 1); return isvar(argv[0], SYM_Err); }
B(b_is_some) { NARGS(1, 1); return isvar(argv[0], SYM_Some); }
B(b_is_none) { NARGS(1, 1); return isvar(argv[0], SYM_None); }
B(b_panic) { NARGS(0, 1); die("panic: %s", argc ? CSTR(kshow(argv[0], false)) : ""); return UNIT; }
B(b_assert) {
    NARGS(1, 2);
    if (!(argv[0].t == T_BOOL && argv[0].u.i)) die("assert: %s", argc == 2 ? CSTR(kshow(argv[1], false)) : "断言失败");
    return UNIT;
}
B(b_assert_eq) {
    NARGS(2, 3);
    if (!k_eq(argv[0], argv[1])) {
        const char *l = cshow(argv[0]), *r = cshow(argv[1]);
        if (argc == 3) die("assert_eq 失败（%s）: 左边 %s，右边 %s", CSTR(kshow(argv[2], false)), l, r);
        die("assert_eq 失败: 左边 %s，右边 %s", l, r);
    }
    return UNIT;
}
B(b_assert_ne) {
    NARGS(2, 3);
    if (k_eq(argv[0], argv[1])) {
        const char *l = cshow(argv[0]);
        if (argc == 3) die("assert_ne 失败（%s）: 两边都是 %s", CSTR(kshow(argv[2], false)), l);
        die("assert_ne 失败: 两边都是 %s", l);
    }
    return UNIT;
}
B(b_kite_test) { NARGS(0, 0); const char *t = getenv("KITE_TEST"); return mkstr(t ? t : "", t ? strlen(t) : 0); }
B(b_read_line) {
    (void)self; (void)argc; (void)argv;
    Buf b = { 0 }; int c; bool any = false;
    for (;;) {
        gc_block_enter(); c = getchar(); gc_block_leave();   /* 读输入可能长时间阻塞：不能挡住 GC */
        if (c == EOF) break;
        any = true; if (c == '\n') break; char ch = (char)c; bput(&b, &ch, 1);
    }
    if (!any) return kNone();
    return kSome(mkstr(b.s ? b.s : "", b.len));
}
/* fs：错误信息统一为 "路径: strerror"，与 Python 引擎的 OSError.strerror 一致 */
static V fs_err(V path) { int er = errno; V m = k_add(path, cstr(": ")); return kErr(k_add(m, cstr(strerror(er)))); }
static int str_cmp(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }
B(b_fs_read) {
    NARGS(1, 1); FILE *f = fopen(CSTR(argv[0]), "rb");
    if (!f) return fs_err(argv[0]);
    Buf b = { 0 }; char tmp[4096]; size_t n;
    while ((n = fread(tmp, 1, sizeof tmp, f)) > 0) bput(&b, tmp, n);
    fclose(f); return kOk(mkstr(b.s ? b.s : "", b.len));
}
B(b_fs_write) {
    NARGS(2, 2); FILE *f = fopen(CSTR(argv[0]), "wb");
    if (!f) return fs_err(argv[0]);
    fwrite(STR(argv[1])->s, 1, STR(argv[1])->len, f); fclose(f); return kOk(UNIT);
}
B(b_fs_append) {
    NARGS(2, 2); FILE *f = fopen(CSTR(argv[0]), "ab");
    if (!f) return fs_err(argv[0]);
    fwrite(STR(argv[1])->s, 1, STR(argv[1])->len, f); fclose(f); return kOk(UNIT);
}
B(b_fs_exists) { NARGS(1, 1); struct stat st; return mkbool(stat(CSTR(argv[0]), &st) == 0); }
B(b_fs_is_file) { NARGS(1, 1); struct stat st; return mkbool(stat(CSTR(argv[0]), &st) == 0 && S_ISREG(st.st_mode)); }
B(b_fs_is_dir) { NARGS(1, 1); struct stat st; return mkbool(stat(CSTR(argv[0]), &st) == 0 && S_ISDIR(st.st_mode)); }
B(b_fs_size) {
    NARGS(1, 1); struct stat st;
    if (stat(CSTR(argv[0]), &st) != 0) return fs_err(argv[0]);
    return kOk(mkint((int64_t)st.st_size));
}
B(b_fs_list) {
    NARGS(1, 1); DIR *d = opendir(CSTR(argv[0]));
    if (!d) return fs_err(argv[0]);
    int n = 0, cap = 16; char **names = malloc(cap * sizeof(char *)); struct dirent *e;
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        if (n == cap) names = realloc(names, (cap *= 2) * sizeof(char *));
        names[n++] = strdup(e->d_name);
    }
    closedir(d); qsort(names, n, sizeof(char *), str_cmp);
    KList *l = newlist(n);
    for (int i = 0; i < n; i++) { l->a[l->len++] = cstr(names[i]); free(names[i]); }
    free(names); return kOk(mkp(T_LIST, l));
}
B(b_fs_mkdir) {   /* 同 mkdir -p：已存在的目录不算错 */
    NARGS(1, 1); KStr *p = STR(argv[0]); char *buf = strdup(cstrk(p)); struct stat st;
    for (char *c = buf + 1; ; c++) {
        if (*c == '/' || *c == 0) {
            char save = *c; *c = 0;
            if (mkdir(buf, 0777) != 0 && !(errno == EEXIST && stat(buf, &st) == 0 && S_ISDIR(st.st_mode))) {
                if (errno != EEXIST) { int er = errno; free(buf); errno = er; return fs_err(argv[0]); }
                free(buf); errno = EEXIST; return fs_err(argv[0]);
            }
            *c = save; if (!save) break;
        }
    }
    free(buf); return kOk(UNIT);
}
B(b_fs_remove) { NARGS(1, 1); if (remove(CSTR(argv[0])) != 0) return fs_err(argv[0]); return kOk(UNIT); }
B(b_fs_rename) { NARGS(2, 2); if (rename(CSTR(argv[0]), CSTR(argv[1])) != 0) return fs_err(argv[0]); return kOk(UNIT); }
static int64_t intarg(KFn *self, V v) { if (v.t != T_INT) die("%s 需要整数", self->name); return v.u.i; }
static double numarg(KFn *self, V v) { if (!isnum(v)) die("%s 需要数字", self->name); return asf(v); }
B(b_sqrt) { NARGS(1, 1); return mkflt(sqrt(numarg(self, argv[0]))); }
/* std.math：和 Go 一样按 IEEE 754，定义域外得到 nan、溢出得到 ±inf */
#define MF1(f) B(b_m_##f) { NARGS(1, 1); return mkflt(f(numarg(self, argv[0]))); }
MF1(tan) MF1(asin) MF1(acos) MF1(atan) MF1(sinh) MF1(cosh) MF1(tanh) MF1(asinh) MF1(acosh) MF1(atanh)
MF1(exp) MF1(exp2) MF1(expm1) MF1(log2) MF1(log10) MF1(log1p) MF1(cbrt)
#define MF2(n, f) B(b_m_##n) { NARGS(2, 2); return mkflt(f(numarg(self, argv[0]), numarg(self, argv[1]))); }
MF2(atan2, atan2) MF2(hypot, hypot) MF2(fmod, fmod) MF2(copysign, copysign) MF2(remainder, remainder)
B(b_m_round) { NARGS(1, 1); return argv[0].t == T_INT ? argv[0] : mkint(f2i_ck(round(numarg(self, argv[0])))); }
B(b_m_trunc) { NARGS(1, 1); return argv[0].t == T_INT ? argv[0] : mkint(f2i_ck(trunc(numarg(self, argv[0])))); }
B(b_m_is_nan) { NARGS(1, 1); return mkbool(isnan(numarg(self, argv[0]))); }
B(b_m_is_inf) { NARGS(1, 1); return mkbool(isinf(numarg(self, argv[0]))); }
B(b_m_signbit) { NARGS(1, 1); return mkbool(signbit(numarg(self, argv[0])) != 0); }
static int64_t gcd64(int64_t a, int64_t b) {
    uint64_t x = a < 0 ? -(uint64_t)a : (uint64_t)a, y = b < 0 ? -(uint64_t)b : (uint64_t)b;
    while (y) { uint64_t t = x % y; x = y; y = t; }
    if (x > (uint64_t)INT64_MAX) die("整数溢出");
    return (int64_t)x;
}
B(b_m_gcd) { NARGS(2, 2); return mkint(gcd64(intarg(self, argv[0]), intarg(self, argv[1]))); }
B(b_m_lcm) {
    NARGS(2, 2); int64_t a = intarg(self, argv[0]), b = intarg(self, argv[1]);
    if (!a || !b) return mkint(0);
    int64_t g = gcd64(a, b), r;
    if (__builtin_mul_overflow(a / g, b, &r) || r == INT64_MIN) die("整数溢出");
    return mkint(r < 0 ? -r : r);
}
B(b_pow) { NARGS(2, 2); return mkflt(pow(numarg(self, argv[0]), numarg(self, argv[1]))); }
B(b_floor) { NARGS(1, 1); return argv[0].t == T_INT ? argv[0] : mkint(f2i_ck(floor(numarg(self, argv[0])))); }
B(b_ceil) { NARGS(1, 1); return argv[0].t == T_INT ? argv[0] : mkint(f2i_ck(ceil(numarg(self, argv[0])))); }
B(b_sin) { NARGS(1, 1); return mkflt(sin(numarg(self, argv[0]))); }
B(b_cos) { NARGS(1, 1); return mkflt(cos(numarg(self, argv[0]))); }
B(b_log) {
    NARGS(1, 2); double x = numarg(self, argv[0]);
    return mkflt(argc == 2 ? log(x) / log(numarg(self, argv[1])) : log(x));
}

/* ── 并发：spawn 与 par_map ──
 * 结构化并发：spawn { a, b, ... } 把每一项作为一个任务并行执行，全部完成后返回元组。
 * 调度：每个任务一个 OS 线程，同时运行的线程数不超过 CPU 核数（KITE_THREADS 可覆盖）；
 * 超出时在当前线程内联执行（嵌套 spawn 不会爆线程）。任务栈 256 MB（虚拟内存，按需提交）。
 * 数据竞争：值都是不可变的；可变的只有变量。每个被闭包捕获的变量记录“所属任务”，
 * 只有所属任务（或其已结束任务的祖先链上的有效所有者）可以写，其他任务写会立刻报错。
 * 全局变量属于主任务。这样任何两个并行任务都不可能写同一个变量。
 */
static int max_threads = 0;
static void init_threads(void) {
    const char *e = getenv("KITE_THREADS");
    long n = e ? atol(e) : sysconf(_SC_NPROCESSORS_ONLN);
    max_threads = n < 1 ? 1 : (int)n;
}

/* ── 调度器 ──
 * spawn/par_map 的各项组成一个 Group，父线程和辅助线程从中动态认领（不预先分段）。
 * 同时运行的线程数不超过 max_threads；但任务在通道上阻塞（或等待子任务）时让出名额，
 * 若还有未认领的项就立刻启动新线程去跑它——所以“内联执行”的项阻塞也不会卡住同组的其他项。
 * sched_mu 保护所有调度与通道状态；持锁期间不分配内存、不经过安全点。
 * 获取 sched_mu 用 trylock 自旋并轮询安全点，等待用 gc_block_enter/leave 包住，GC 不会被卡住。
 * 每个阻塞的线程有自己的 Waiter；唤醒方负责把它记回“运行中”，所以 sched_running 始终准确：
 * 当某线程要阻塞时，若 sched_running 变为 0 且没有未认领的项，就是死锁，直接报错。
 */
typedef struct Waiter {
    pthread_cond_t cv; int woken, status, waiting, fired, cancellable; V val;
    KTask *task; struct Waiter *bprev, *bnext;     /* 阻塞登记表（取消时据此唤醒） */
} Waiter;
/* 等待队列项（在等待者自己的栈上）：select 时一个等待者在多个通道上各挂一项 */
typedef struct QNode { Waiter *w; int idx; V val; struct QNode *next; } QNode;
typedef struct Group {
    struct TaskJob *jobs; int64_t n, next, remaining;
    Waiter *joiner; struct Group *pnext; int listed;
    int race; int64_t winner;
    char *err_full, *err_msg;      /* 第一个出错子任务的错误（其余子任务随之被取消） */
} Group;
typedef struct TaskJob { V fn; V arg; int has_arg; KTask *task; V *out; } TaskJob;
static pthread_mutex_t sched_mu = PTHREAD_MUTEX_INITIALIZER;
static int sched_running = 1;        /* 正在执行 Kite 代码（未阻塞）的线程数 */
static int sched_timed = 0;          /* 带超时等待的线程数（它们迟早会醒，所以不算死锁） */
static Group *pending_groups;        /* 还有未认领项的组 */
static const char *DEADLOCK_ERR = "死锁：所有任务都在等待（通道收发或等待子任务），没有任务能继续";

static void sched_lock(void) {
    while (pthread_mutex_trylock(&sched_mu) != 0) { SAFEPOINT(); sched_yield(); }
    rt_locked++;
}
static void sched_unlock(void) { rt_locked--; pthread_mutex_unlock(&sched_mu); }
static void unlist(Group *g) {
    if (!g->listed) return;
    for (Group **pp = &pending_groups; *pp; pp = &(*pp)->pnext) if (*pp == g) { *pp = g->pnext; break; }
    g->listed = 0;
}
/* 认领一项（持锁）：优先给定的组，否则任意待办组 */
static int claim(Group **gp, int64_t *ip) {
    Group *g = *gp;
    if (g && g->next < g->n) { *ip = g->next++; if (g->next == g->n) unlist(g); return 1; }
    while (pending_groups) {
        g = pending_groups;
        if (g->next < g->n) { *gp = g; *ip = g->next++; if (g->next == g->n) unlist(g); return 1; }
        unlist(g);
    }
    return 0;
}
static Waiter *blocked_head;   /* 可取消的阻塞等待者（持锁访问） */
static void wake(Waiter *w, int idx, int status);
/* 持锁：取消一组任务（及后代），唤醒其中正在可取消等待的 */
static void cancel_tasks_locked(Group *g, int64_t keep) {
    for (int64_t j = 0; j < g->n; j++)
        if (j != keep) __atomic_store_n(&g->jobs[j].task->cancelled, 1, __ATOMIC_RELEASE);
    __atomic_add_fetch(&cancel_epoch, 1, __ATOMIC_RELEASE);
    for (Waiter *w = blocked_head; w; w = w->bnext)
        if (w->waiting && !w->woken && task_cancelled(w->task)) wake(w, w->fired, 4);
}
/* 任务边界：被取消的任务展开到这里（结果记为 ()）。race 中第一个正常完成的项取消其余各项 */
static void run_one(Group *g, int64_t i) {
    TaskJob *j = &g->jobs[i];
    KTask *saved = cur_task; cur_task = j->task;
    Boundary b; b.prev = cur_bound; b.frame = cur_frame; b.is_task = 1; cur_bound = &b;
    volatile int ok = 0;
    V r = UNIT;
    int code = 0;
    if (!task_cancelled(j->task)) {
        if ((code = setjmp(b.jb)) == 0) {
            r = j->has_arg ? call_v(j->fn, 1, &j->arg) : call_v(j->fn, 0, NULL);
            ok = 1;
        } else {
            r = UNIT;
        }
    }
    cur_bound = b.prev; cur_frame = b.frame;
    __atomic_store_n(&j->task->done, 1, __ATOMIC_RELEASE);
    cur_task = saved;
    *j->out = r;
    if (code == 2) {   /* 子任务出错：记下第一个错误，取消兄弟任务；父任务等全部停下后重新抛出 */
        char *full = strdup(panic_full), *msg = strdup(panic_msg);
        sched_lock();
        if (!g->err_full) { g->err_full = full; g->err_msg = msg; full = msg = NULL; }
        cancel_tasks_locked(g, i);
        sched_unlock();
        free(full); free(msg);
    }
    if (ok && g->race) {
        sched_lock();
        if (g->winner < 0) { g->winner = i; cancel_tasks_locked(g, i); }
        sched_unlock();
    }
}
/* 唤醒（持锁）：status 1 成功，2 通道已关闭，3 超时 */
static void wake(Waiter *w, int idx, int status) {
    w->waiting = 0; w->fired = idx; w->status = status; w->woken = 1; sched_running++;
    pthread_cond_signal(&w->cv);
}
static void finish_one(Group *g) {           /* 持锁 */
    if (--g->remaining == 0 && g->joiner) { Waiter *w = g->joiner; g->joiner = NULL; wake(w, 0, 1); }
}
typedef struct { Group *g; int64_t i; } HelperArg;
static void *helper_main(void *arg) {
    volatile char base = 0; Mutator m;
    gc_register(&m, (char *)&base + 64);
    HelperArg a = *(HelperArg *)arg; free(arg);
    Group *g = a.g; int64_t i = a.i;
    for (;;) {
        run_one(g, i);
        sched_lock();
        finish_one(g);
        if (!claim(&g, &i)) break;
        sched_unlock();
    }
    sched_running--;
    if (sched_running == 0 && sched_timed == 0) fatal("%s", DEADLOCK_ERR);   /* 剩下的线程全在无限期等待，且没有待办项 */
    sched_unlock();
    gc_unregister();
    return NULL;
}
/* 持锁：为组 g 的第 i 项启动辅助线程 */
static int start_helper(Group *g, int64_t i) {
    HelperArg *a = malloc(sizeof(HelperArg)); a->g = g; a->i = i;
    pthread_attr_t at; pthread_attr_init(&at); pthread_attr_setstacksize(&at, (size_t)256 << 20);
    pthread_t th;
    int ok = pthread_create(&th, &at, helper_main, a) == 0;
    pthread_attr_destroy(&at);
    if (!ok) { free(a); return 0; }
    pthread_detach(th);
    sched_running++;
    return 1;
}
/* 持锁：当前线程阻塞，直到被 wake 或超时（timeout_ms < 0 表示不限时；超时则 status=3, fired=tidx）；
   返回时仍持锁且已重新计入运行中 */
static void block_on(Waiter *w, int64_t timeout_ms, int tidx) {
    w->task = cur_task;
    if (w->cancellable) {
        if (__atomic_load_n(&cancel_epoch, __ATOMIC_RELAXED) && task_cancelled(cur_task)) {
            w->waiting = 0; w->status = 4; w->woken = 1; return;   /* 已被取消：不等待 */
        }
        w->bprev = NULL; w->bnext = blocked_head;
        if (blocked_head) blocked_head->bprev = w;
        blocked_head = w;
    }
    sched_running--;
    if (timeout_ms >= 0) sched_timed++;
    if (sched_running == 0) {
        Group *g = NULL; int64_t i;
        if (claim(&g, &i)) { if (!start_helper(g, i)) fatal("无法创建线程"); }
        else if (sched_timed == 0) fatal("%s", DEADLOCK_ERR);
    }
    struct timespec dl;
    if (timeout_ms >= 0) {
        clock_gettime(CLOCK_REALTIME, &dl);
        dl.tv_sec += timeout_ms / 1000; dl.tv_nsec += (timeout_ms % 1000) * 1000000L;
        if (dl.tv_nsec >= 1000000000L) { dl.tv_sec++; dl.tv_nsec -= 1000000000L; }
    }
    gc_block_enter();
    while (!w->woken) {
        if (timeout_ms < 0) { pthread_cond_wait(&w->cv, &sched_mu); continue; }
        if (pthread_cond_timedwait(&w->cv, &sched_mu, &dl) == ETIMEDOUT && !w->woken) {
            w->waiting = 0; w->fired = tidx; w->status = 3; w->woken = 1; sched_running++;
        }
    }
    if (timeout_ms >= 0) sched_timed--;
    if (w->cancellable) {
        if (w->bprev) w->bprev->bnext = w->bnext; else blocked_head = w->bnext;
        if (w->bnext) w->bnext->bprev = w->bprev;
    }
    gc_block_leave();
}
static void waiter_init(Waiter *w) {
    pthread_cond_init(&w->cv, NULL); w->woken = 0; w->status = 0; w->waiting = 1; w->fired = 0; w->cancellable = 1;
}
/* 阻塞操作因取消返回后（已解锁）：展开当前任务 */
static void __attribute__((noreturn)) cancel_now(void) { cancel_unwind(); }

/* 结构化并发：n 个任务全部完成后返回 */
static int64_t run_jobs(TaskJob *jobs, int64_t n, int race) {
    if (n == 0) return -1;
    Group *g = kalloc(sizeof(Group));
    g->jobs = jobs; g->n = n; g->next = 0; g->remaining = n; g->joiner = NULL; g->listed = 0;
    g->race = race; g->winner = -1; g->err_full = g->err_msg = NULL;
    sched_lock();
    g->next = 1;
    if (n > 1) { g->pnext = pending_groups; pending_groups = g; g->listed = 1; }
    /* 有空闲名额就启动辅助线程 */
    while (g->next < g->n && sched_running < max_threads) {
        int64_t i = g->next++;
        if (g->next == g->n) unlist(g);
        if (!start_helper(g, i)) { g->next--; if (!g->listed && g->next < g->n) { g->pnext = pending_groups; pending_groups = g; g->listed = 1; } break; }
    }
    sched_unlock();
    int64_t i = 0;
    for (;;) {
        run_one(g, i);
        sched_lock();
        finish_one(g);
        if (g->next < g->n) { i = g->next++; if (g->next == g->n) unlist(g); sched_unlock(); continue; }
        break;
    }
    if (g->remaining > 0) {
        Waiter w; waiter_init(&w); w.cancellable = 0;   /* 结构化：总要等子任务结束 */
        g->joiner = &w;
        block_on(&w, -1, 0);
        pthread_cond_destroy(&w.cv);
    }
    sched_unlock();
    if (g->err_full) {   /* 结构化错误传播：子任务的错误在父任务里重新抛出（保留原位置） */
        snprintf(panic_full, sizeof panic_full, "%s", g->err_full);
        snprintf(panic_msg, sizeof panic_msg, "%s", g->err_msg);
        free(g->err_full); free(g->err_msg); g->err_full = g->err_msg = NULL;
        raise_panic(panic_full, panic_msg);
    }
    CANCEL_POINT();      /* 自己（或祖先）被取消了：子任务已全部停下，现在展开自己 */
    return g->winner;
}

/* ── 通道 ──
 * chan() 无缓冲：发送方一直等到接收方取走；chan(n) 有 n 个缓冲位，满了才等。
 * 等待中的发送方把值放在自己栈上的 Waiter 里（仍是 GC 根），接收方直接取走。
 * recv 在通道关闭且取空后返回 None；向已关闭通道发送、重复关闭都是运行时错误。
 */
/* 持锁：轮询（select 的 _ 分支、落空的 try_recv）时若有未开始的项就为它启动线程，避免轮询循环饿死它们 */
static void kick_pending(void) {
    Group *g = NULL; int64_t i;
    if (pending_groups && claim(&g, &i)) start_helper(g, i);
}
struct KChan { int64_t cap, len, head; V *buf; int closed; QNode *rq, *rq_tail, *sq, *sq_tail; };
static void q_push(QNode **h, QNode **t, QNode *n) { n->next = NULL; if (*t) (*t)->next = n; else *h = n; *t = n; }
/* 取出第一个仍在等待的项（select 已被别的分支唤醒的项直接丢弃） */
static QNode *q_pop(QNode **h, QNode **t) {
    while (*h) {
        QNode *n = *h; *h = n->next; if (!*h) *t = NULL;
        if (n->w->waiting) return n;
    }
    return NULL;
}
static int q_live(QNode **h, QNode **t) {
    while (*h && !(*h)->w->waiting) { *h = (*h)->next; if (!*h) *t = NULL; }
    return *h != NULL;
}
static void q_remove(QNode **h, QNode **t, QNode *n) {
    QNode *prev = NULL;
    for (QNode *x = *h; x; prev = x, x = x->next) if (x == n) {
        if (prev) prev->next = x->next; else *h = x->next;
        if (*t == x) *t = prev;
        return;
    }
}
static KChan *as_chan(V v, const char *fn) {
    if (v.t != T_CHAN) die("%s 需要通道，实际是 %s", fn, tname(v));
    return v.u.p;
}
/* 以下 *_now 都在持锁时调用，不阻塞 */
static int can_recv(KChan *c) { return c->len > 0 || q_live(&c->sq, &c->sq_tail) || c->closed; }
static int can_send(KChan *c) { return q_live(&c->rq, &c->rq_tail) || c->len < c->cap; }
static bool recv_now(KChan *c, V *out) {   /* 前提：can_recv */
    if (c->len > 0) {
        *out = c->buf[c->head]; c->head = (c->head + 1) % c->cap; c->len--;
        QNode *s = q_pop(&c->sq, &c->sq_tail);
        if (s) { c->buf[(c->head + c->len++) % c->cap] = s->val; wake(s->w, s->idx, 1); }
        return true;
    }
    QNode *s = q_pop(&c->sq, &c->sq_tail);
    if (s) { *out = s->val; wake(s->w, s->idx, 1); return true; }
    return false;   /* 已关闭 */
}
static void send_now(KChan *c, V v) {     /* 前提：can_send */
    QNode *r = q_pop(&c->rq, &c->rq_tail);
    if (r) { r->w->val = v; wake(r->w, r->idx, 1); return; }
    c->buf[(c->head + c->len++) % c->cap] = v;
}
static void chan_send(KChan *c, V v) {
    sched_lock();
    if (c->closed) { sched_unlock(); die("向已关闭的通道发送"); }
    if (can_send(c)) { send_now(c, v); sched_unlock(); return; }
    Waiter w; waiter_init(&w);
    QNode n = { &w, 0, v, NULL };
    q_push(&c->sq, &c->sq_tail, &n);
    block_on(&w, -1, 0);
    int st = w.status;
    if (st == 4) q_remove(&c->sq, &c->sq_tail, &n);
    sched_unlock();
    pthread_cond_destroy(&w.cv);
    if (st == 4) cancel_now();
    if (st == 2) die("向已关闭的通道发送");
}
static bool chan_recv(KChan *c, V *out) {
    sched_lock();
    if (can_recv(c)) { bool ok = recv_now(c, out); sched_unlock(); return ok; }
    Waiter w; waiter_init(&w);
    QNode n = { &w, 0, UNIT, NULL };
    q_push(&c->rq, &c->rq_tail, &n);
    block_on(&w, -1, 0);
    bool ok = w.status == 1;
    if (ok) *out = w.val;
    if (w.status == 4) q_remove(&c->rq, &c->rq_tail, &n);
    sched_unlock();
    pthread_cond_destroy(&w.cv);
    if (w.status == 4) cancel_now();
    return ok;
}
static KChan *new_chan(int64_t cap) {
    KChan *c = kalloc(sizeof(KChan));
    c->cap = cap; c->len = c->head = 0; c->closed = 0; c->rq = c->rq_tail = c->sq = c->sq_tail = NULL;
    c->buf = cap ? kalloc(sizeof(V) * cap) : NULL;
    return c;
}
B(b_chan) {
    NARGS(0, 1);
    int64_t cap = 0;
    if (argc) { if (argv[0].t != T_INT || argv[0].u.i < 0) die("chan 的容量必须是非负整数"); cap = argv[0].u.i; }
    KChan *c = kalloc(sizeof(KChan));
    c->cap = cap; c->len = c->head = 0; c->closed = 0; c->rq = c->rq_tail = c->sq = c->sq_tail = NULL;
    c->buf = cap ? kalloc(sizeof(V) * cap) : NULL;
    return mkp(T_CHAN, c);
}
B(b_send) { NARGS(2, 2); chan_send(as_chan(argv[0], "send"), argv[1]); return UNIT; }
B(b_recv) { NARGS(1, 1); V v; return chan_recv(as_chan(argv[0], "recv"), &v) ? kSome(v) : kNone(); }
B(b_try_recv) {
    NARGS(1, 1); KChan *c = as_chan(argv[0], "try_recv");
    sched_lock();
    V v; int got = 0;
    if (c->len > 0 || q_live(&c->sq, &c->sq_tail)) got = recv_now(c, &v);
    else kick_pending();
    sched_unlock();
    return got ? kSome(v) : kNone();
}
B(b_close) {
    NARGS(1, 1); KChan *c = as_chan(argv[0], "close");
    sched_lock();
    if (c->closed) { sched_unlock(); die("通道已经关闭"); }
    c->closed = 1;
    QNode *n;
    while ((n = q_pop(&c->rq, &c->rq_tail))) wake(n->w, n->idx, 2);
    while ((n = q_pop(&c->sq, &c->sq_tail))) wake(n->w, n->idx, 2);
    sched_unlock();
    return UNIT;
}
/* __select(ops, 超时毫秒, 有无默认分支)：ops 是 [(0 收 | 1 发, 通道, 值)]，返回 (分支号, 值)。
   按书写顺序取第一个就绪的分支；都没就绪时有默认分支就返回 (n, ())，否则在所有通道上同时等待 */
B(b_select) {
    NARGS(3, 3);
    KList *ops = LIST(argv[0]);
    int64_t n = ops->len, timeout = argv[1].u.i;
    int has_default = argv[2].u.i != 0;
    KChan **chs = kalloc(sizeof(KChan *) * (n ? n : 1));
    int *kind = kalloc(sizeof(int) * (n ? n : 1));
    V *vals = kalloc(sizeof(V) * (n ? n : 1));
    for (int64_t i = 0; i < n; i++) {
        KTuple *t = ops->a[i].u.p;
        kind[i] = (int)t->a[0].u.i; chs[i] = as_chan(t->a[1], "select"); vals[i] = t->a[2];
    }
    QNode *nodes = n <= 16 ? alloca(sizeof(QNode) * (n ? n : 1)) : malloc(sizeof(QNode) * n);
    V out[2]; int st = 1; int64_t fired = n;
    sched_lock();
    for (int64_t i = 0; i < n; i++) {
        if (kind[i] == 0) {
            if (can_recv(chs[i])) { V v; st = recv_now(chs[i], &v) ? 1 : 2; out[1] = v; fired = i; goto done; }
        } else if (kind[i] == 1) {
            if (chs[i]->closed) { sched_unlock(); die("向已关闭的通道发送"); }
            if (can_send(chs[i])) { send_now(chs[i], vals[i]); fired = i; goto done; }
        }
    }
    if (has_default || timeout == 0) { fired = n; st = 3; kick_pending(); goto done; }
    {
        Waiter w; waiter_init(&w);
        for (int64_t i = 0; i < n; i++) {
            nodes[i].w = &w; nodes[i].idx = (int)i; nodes[i].val = vals[i];
            if (kind[i] == 0) q_push(&chs[i]->rq, &chs[i]->rq_tail, &nodes[i]);
            else if (kind[i] == 1) q_push(&chs[i]->sq, &chs[i]->sq_tail, &nodes[i]);
        }
        block_on(&w, timeout, (int)n);
        for (int64_t i = 0; i < n; i++) {
            if (kind[i] == 0) q_remove(&chs[i]->rq, &chs[i]->rq_tail, &nodes[i]);
            else if (kind[i] == 1) q_remove(&chs[i]->sq, &chs[i]->sq_tail, &nodes[i]);
        }
        fired = w.fired; st = w.status; out[1] = w.val;
        pthread_cond_destroy(&w.cv);
    }
done:
    sched_unlock();
    if (n > 16) free(nodes);
    if (st == 4) cancel_now();
    out[0] = mkint(fired);
    if (st == 3 || fired == n) { out[1] = UNIT; return mktuple(2, out); }
    if (kind[fired] == 0) out[1] = st == 1 ? kSome(out[1]) : kNone();
    else { if (st == 2) die("向已关闭的通道发送"); out[1] = UNIT; }
    return mktuple(2, out);
}
B(b_sleep) {
    NARGS(1, 1);
    if (argv[0].t != T_INT) die("sleep 需要 Int（毫秒）");
    int64_t ms = argv[0].u.i < 0 ? 0 : argv[0].u.i;
    Waiter w; waiter_init(&w);
    sched_lock(); block_on(&w, ms, 0); sched_unlock();
    pthread_cond_destroy(&w.cv);
    if (w.status == 4) cancel_now();
    return UNIT;
}
B(b_now_ms) {
    NARGS(0, 0);
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return mkint((int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000);
}

static V k_spawn(int n, V *thunks) {
    TaskJob *jobs = kalloc(sizeof(TaskJob) * n);
    V *out = kalloc(sizeof(V) * n);
    for (int i = 0; i < n; i++) {
        KTask *t = kalloc(sizeof(KTask)); t->parent = cur_task; t->cancelled = t->unwinding = 0;
        jobs[i].fn = thunks[i]; jobs[i].task = t; jobs[i].out = &out[i]; jobs[i].has_arg = 0;
    }
    run_jobs(jobs, n, 0);
    return mktuple(n, out);
}
static KTask *new_task(void) {
    KTask *t = kalloc(sizeof(KTask)); t->parent = cur_task; t->cancelled = t->unwinding = 0; t->done = 0; t->cancelled = 0; t->unwinding = 0;
    return t;
}
/* race：第一个正常完成的项胜出，其余被取消；所有项都停下后才返回 */
B(b_race) {
    NARGS(1, 1); KList *fs = LIST(argv[0]);
    int64_t n = fs->len;
    TaskJob *jobs = kalloc(sizeof(TaskJob) * (n ? n : 1));
    V *out = kalloc(sizeof(V) * (n ? n : 1));
    for (int64_t i = 0; i < n; i++) {
        jobs[i].fn = fs->a[i]; jobs[i].has_arg = 0; jobs[i].task = new_task(); jobs[i].out = &out[i];
    }
    int64_t w = run_jobs(jobs, n, 1);
    return w >= 0 ? out[w] : UNIT;
}
B(b_sleep);
static KFn bf_b_sleep_;
/* timeout(ms, f)：f 先完成返回 Some(结果)，否则取消 f 并返回 None */
B(b_timeout) {
    NARGS(2, 2);
    if (argv[0].t != T_INT) die("timeout 需要 Int（毫秒）");
    TaskJob *jobs = kalloc(sizeof(TaskJob) * 2);
    V *out = kalloc(sizeof(V) * 2);
    jobs[0].fn = mkp(T_FN, &bf_b_sleep_); jobs[0].arg = argv[0]; jobs[0].has_arg = 1; jobs[0].task = new_task(); jobs[0].out = &out[0];
    jobs[1].fn = argv[1]; jobs[1].has_arg = 0; jobs[1].task = new_task(); jobs[1].out = &out[1];
    int64_t w = run_jobs(jobs, 2, 1);
    return w == 1 ? kSome(out[1]) : kNone();
}
B(b_par_map) {
    NARGS(2, 2); KList *s = LIST(to_list(argv[0]));
    int64_t n = s->len;
    TaskJob *jobs = kalloc(sizeof(TaskJob) * (n ? n : 1));
    KList *l = newlist(n);
    for (int64_t i = 0; i < n; i++) {
        KTask *t = kalloc(sizeof(KTask)); t->parent = cur_task; t->cancelled = t->unwinding = 0;
        jobs[i].fn = argv[1]; jobs[i].arg = s->a[i]; jobs[i].has_arg = 1; jobs[i].task = t; jobs[i].out = &l->a[i];
    }
    run_jobs(jobs, n, 0);
    l->len = n;
    return mkp(T_LIST, l);
}

#define BF(c, n) static KFn bf_##c = { c, n, 0, 0, 0, 0, 0 }
BF(b_print, "print"); BF(b_par_map, "par_map"); BF(b_len, "len"); BF(b_str, "str"); BF(b_int, "int"); BF(b_float, "float");
BF(b_type_of, "type_of"); BF(b_map, "map"); BF(b_filter, "filter"); BF(b_each, "each");
BF(b_reduce, "reduce"); BF(b_sum, "sum"); BF(b_count, "count"); BF(b_any, "any"); BF(b_all, "all");
BF(b_find, "find"); BF(b_first, "first"); BF(b_last, "last"); BF(b_get, "get"); BF(b_take, "take");
BF(b_skip, "skip"); BF(b_reverse, "reverse"); BF(b_sort, "sort"); BF(b_sort_with, "sort_with"); BF(b_fmt, "__fmt"); BF(b_repeat, "__repeat"); BF(b_zip, "zip");
BF(b_enumerate, "enumerate"); BF(b_push, "push"); BF(b_concat, "concat"); BF(b_contains, "contains");
BF(b_join, "join"); BF(b_split, "split"); BF(b_trim, "trim"); BF(b_upper, "upper"); BF(b_lower, "lower");
BF(b_starts_with, "starts_with"); BF(b_ends_with, "ends_with"); BF(b_replace, "replace");
BF(b_chars, "chars"); BF(b_parse_int, "parse_int"); BF(b_parse_float, "parse_float");
BF(b_keys, "keys"); BF(b_remove, "remove"); BF(b_values, "values"); BF(b_set, "set"); BF(b_Map, "Map"); BF(b_Set, "Set"); BF(b_insert, "insert"); BF(b_union, "union"); BF(b_intersect, "intersect"); BF(b_difference, "difference"); BF(b_is_subset, "is_subset"); BF(b_to_list, "to_list"); BF(b_min, "min");
BF(b_max, "max"); BF(b_abs, "abs"); BF(b_Ok, "Ok"); BF(b_Err, "Err"); BF(b_Some, "Some");
BF(b_unwrap, "unwrap"); BF(b_unwrap_or, "unwrap_or"); BF(b_is_ok, "is_ok"); BF(b_is_err, "is_err");
BF(b_is_some, "is_some"); BF(b_is_none, "is_none"); BF(b_panic, "panic"); BF(b_assert, "assert"); BF(b_assert_eq, "assert_eq"); BF(b_assert_ne, "assert_ne"); BF(b_kite_test, "__kite_test");
static KFn bf_b_sleep_ = { b_sleep, "sleep", 0, 0, 0, 0, 0 };
BF(b_race, "__race"); BF(b_timeout, "timeout");
/* ── Actor ──
 * actor(init, handler, body)：邮箱是容量 64 的通道；一个任务循环收消息并 state = handler(state, msg)，
 * 另一个任务执行 body(邮箱)，结束后关闭邮箱；处理任务把剩余消息处理完才结束。返回 (body 结果, 最终状态) */
#define ACTOR_MAILBOX 64
B(b_actor_loop) {   /* arg = (init, handler, mailbox) */
    KTuple *t = TUP(argv[0]);
    V st = t->a[0], h = t->a[1]; KChan *mb = t->a[2].u.p;
    V m;
    while (chan_recv(mb, &m)) { V args[2] = { st, m }; st = call_v(h, 2, args); }
    return st;
}
B(b_actor_body) {   /* arg = (body, mailbox) */
    KTuple *t = TUP(argv[0]);
    KChan *mb = t->a[1].u.p;
    V r = call_v(t->a[0], 1, &t->a[1]);
    sched_lock(); int closed = mb->closed; sched_unlock();
    if (!closed) { V a = t->a[1]; b_close(NULL, 1, &a); }
    return r;
}
BF(b_actor_loop, "<actor>"); BF(b_actor_body, "<actor body>");
B(b_actor) {
    NARGS(3, 3);
    KChan *mb = new_chan(ACTOR_MAILBOX);
    V mbv = mkp(T_CHAN, mb);
    V a0[3] = { argv[0], argv[1], mbv }, a1[2] = { argv[2], mbv };
    TaskJob *jobs = kalloc(sizeof(TaskJob) * 2);
    V *out = kalloc(sizeof(V) * 2);
    jobs[0].fn = mkp(T_FN, &bf_b_actor_loop); jobs[0].arg = mktuple(3, a0);
    jobs[1].fn = mkp(T_FN, &bf_b_actor_body); jobs[1].arg = mktuple(2, a1);
    for (int i = 0; i < 2; i++) { jobs[i].has_arg = 1; jobs[i].task = new_task(); jobs[i].out = &out[i]; }
    run_jobs(jobs, 2, 0);
    V res[2] = { out[1], out[0] };
    return mktuple(2, res);
}
static KChan *actor_mb(V a, const char *op) {
    KChan *c = as_chan(a, op);
    sched_lock(); int closed = c->closed; sched_unlock();
    if (closed) die("actor 已经停止，不能再发消息");
    return c;
}
B(b_tell) { NARGS(2, 2); chan_send(actor_mb(argv[0], "tell"), argv[1]); return UNIT; }
B(b_ask) {
    NARGS(2, 2);
    KChan *c = actor_mb(argv[0], "ask");
    V rv = mkp(T_CHAN, new_chan(1));
    chan_send(c, call_v(argv[1], 1, &rv));
    V v;
    if (!chan_recv(rv.u.p, &v)) die("ask：actor 关闭了回复通道而没有回复");
    return v;
}
BF(b_actor, "actor"); BF(b_tell, "tell"); BF(b_ask, "ask");

/* ── 错误恢复与监督 ── */
/* 调用 f；出错返回 0（错误在 panic_full / panic_msg） */
static int try_call(V f, int argc, V *argv, V *out) {
    Boundary b; b.prev = cur_bound; b.frame = cur_frame; b.is_task = 0; cur_bound = &b;
    KTask *task = cur_task;
    if (setjmp(b.jb) == 0) {
        V r = call_v(f, argc, argv);
        cur_bound = b.prev;
        *out = r; return 1;
    }
    cur_bound = b.prev; cur_frame = b.frame; cur_task = task;
    return 0;
}
static int classify_panic(const char *msg, const char **shown);
static void restart_log(const char *who, int64_t n, int64_t max, const char *msg) {
    classify_panic(msg, &msg);
    fflush(stdout);
    fprintf(stderr, "%s：第 %lld 次重启（最多 %lld 次），原因：%s\n", who, (long long)n, (long long)max, msg);
}
/* 运行时错误分类，与 kite.py 的 PANIC_PREFIXES 保持一致 */
static const struct { const char *p; int k; } PANIC_PREFIXES[] = {
    {"panic: ", 0}, {"除以零", 1}, {"整数溢出", 2}, {"下标越界", 3}, {"unwrap 失败", 4},
    {"assert", 5}, {"向已关闭的通道发送", 6}, {"actor 已经停止", 6}, {"通道已经关闭", 6},
};
static int classify_panic(const char *msg, const char **shown) {
    *shown = msg;
    for (size_t i = 0; i < sizeof PANIC_PREFIXES / sizeof PANIC_PREFIXES[0]; i++) {
        size_t n = strlen(PANIC_PREFIXES[i].p);
        if (!strncmp(msg, PANIC_PREFIXES[i].p, n)) {
            if (PANIC_PREFIXES[i].k == 0) *shown = msg + n;
            return PANIC_PREFIXES[i].k;
        }
    }
    return 7;
}
B(b_recover_raw) {
    NARGS(1, 1);
    V r;
    if (try_call(argv[0], 0, NULL, &r)) return kOk(r);
    const char *shown;
    int k = classify_panic(panic_msg, &shown);
    size_t fl = strlen(panic_full), ml = strlen(panic_msg);
    size_t ll = fl >= ml + 2 ? fl - ml - 2 : 0;           /* panic_full = "位置: 消息" */
    V t[3] = { mkint(k), mkstr(shown, strlen(shown)), mkstr(panic_full, ll) };
    return kErr(mktuple(3, t));
}
B(b_repanic) {
    NARGS(2, 2);
    char full[1200], msg[1024];
    snprintf(msg, sizeof msg, "%s", CSTR(argv[0]));
    snprintf(full, sizeof full, "%s: %s", CSTR(argv[1]), msg);
    raise_panic(full, msg);
}
B(b_restart_log_k) {
    NARGS(4, 4);
    fflush(stdout);
    fprintf(stderr, "%s：第 %lld 次重启（最多 %lld 次），原因：%s\n", CSTR(argv[0]),
            (long long)argv[1].u.i, (long long)argv[2].u.i, CSTR(argv[3]));
    return UNIT;
}
B(b_supervise) {
    NARGS(2, 2);
    if (argv[0].t != T_INT) die("supervise 需要 Int（最多重启次数）");
    int64_t max = argv[0].u.i;
    for (int64_t n = 1;; n++) {
        V r;
        if (try_call(argv[1], 0, NULL, &r)) return r;
        if (n > max) raise_panic(panic_full, panic_msg);
        restart_log("supervise", n, max, panic_msg);
    }
}
B(b_actor_sup_loop) {   /* arg = (init, handler, mailbox, max) */
    KTuple *t = TUP(argv[0]);
    V st = t->a[0], h = t->a[1]; KChan *mb = t->a[2].u.p; int64_t max = t->a[3].u.i, n = 0;
    V m;
    while (chan_recv(mb, &m)) {
        V args[2] = { st, m }, r;
        if (try_call(h, 2, args, &r)) { st = r; continue; }
        if (++n > max) raise_panic(panic_full, panic_msg);   /* 超过上限：升级给父任务 */
        restart_log("actor", n, max, panic_msg);
        st = t->a[0];
    }
    return st;
}
BF(b_actor_sup_loop, "<actor>");
B(b_actor_supervised) {
    NARGS(4, 4);
    if (argv[2].t != T_INT) die("actor_supervised 需要 Int（最多重启次数）");
    KChan *mb = new_chan(ACTOR_MAILBOX);
    V mbv = mkp(T_CHAN, mb);
    V a0[4] = { argv[0], argv[1], mbv, argv[2] }, a1[2] = { argv[3], mbv };
    TaskJob *jobs = kalloc(sizeof(TaskJob) * 2);
    V *out = kalloc(sizeof(V) * 2);
    jobs[0].fn = mkp(T_FN, &bf_b_actor_sup_loop); jobs[0].arg = mktuple(4, a0);
    jobs[1].fn = mkp(T_FN, &bf_b_actor_body); jobs[1].arg = mktuple(2, a1);
    for (int i = 0; i < 2; i++) { jobs[i].has_arg = 1; jobs[i].task = new_task(); jobs[i].out = &out[i]; }
    run_jobs(jobs, 2, 0);
    V res[2] = { out[1], out[0] };
    return mktuple(2, res);
}
B(b_restart_log_w) {
    NARGS(6, 6);
    fflush(stdout);
    fprintf(stderr, "%s：窗口内第 %lld 次重启（每 %lld 毫秒最多 %lld 次），等待 %lld 毫秒，原因：%s\n", CSTR(argv[0]),
            (long long)argv[1].u.i, (long long)argv[3].u.i, (long long)argv[2].u.i, (long long)argv[4].u.i, CSTR(argv[5]));
    return UNIT;
}
BF(b_restart_log_w, "__restart_log_w");
BF(b_recover_raw, "__recover_raw"); BF(b_repanic, "__repanic"); BF(b_restart_log_k, "__restart_log"); BF(b_supervise, "supervise"); BF(b_actor_supervised, "actor_supervised");
BF(b_select, "__select"); BF(b_sleep, "sleep"); BF(b_now_ms, "now_ms"); BF(b_chan, "chan"); BF(b_send, "send"); BF(b_recv, "recv"); BF(b_try_recv, "try_recv"); BF(b_close, "close");
BF(b_read_line, "read_line"); BF(b_fs_read, "read"); BF(b_fs_write, "write"); BF(b_fs_append, "append"); BF(b_fs_exists, "exists");
BF(b_fs_is_file, "is_file"); BF(b_fs_is_dir, "is_dir"); BF(b_fs_size, "size"); BF(b_fs_list, "list");
BF(b_fs_mkdir, "mkdir"); BF(b_fs_remove, "remove"); BF(b_fs_rename, "rename"); BF(b_sqrt, "sqrt");
BF(b_pow, "pow"); BF(b_floor, "floor"); BF(b_ceil, "ceil"); BF(b_sin, "sin"); BF(b_cos, "cos");
BF(b_m_tan, "tan"); BF(b_m_asin, "asin"); BF(b_m_acos, "acos"); BF(b_m_atan, "atan"); BF(b_m_sinh, "sinh"); BF(b_m_cosh, "cosh"); BF(b_m_tanh, "tanh"); BF(b_m_asinh, "asinh"); BF(b_m_acosh, "acosh"); BF(b_m_atanh, "atanh"); BF(b_m_exp, "exp"); BF(b_m_exp2, "exp2"); BF(b_m_expm1, "expm1"); BF(b_m_log2, "log2"); BF(b_m_log10, "log10"); BF(b_m_log1p, "log1p"); BF(b_m_cbrt, "cbrt"); BF(b_m_atan2, "atan2"); BF(b_m_hypot, "hypot"); BF(b_m_fmod, "fmod"); BF(b_m_copysign, "copysign"); BF(b_m_remainder, "remainder"); BF(b_m_round, "round"); BF(b_m_trunc, "trunc"); BF(b_m_is_nan, "is_nan"); BF(b_m_is_inf, "is_inf"); BF(b_m_signbit, "signbit"); BF(b_m_gcd, "gcd"); BF(b_m_lcm, "lcm");
BF(b_log, "log");

/* ── 标准库底层原语（std/*.kite 用它们实现 env、process、time、str） ── */
static int KARGC; static char **KARGV;
B(b_env_get) {
    NARGS(1, 1); const char *v = getenv(CSTR(argv[0]));
    return v ? kSome(cstr(v)) : kNone();
}
B(b_args) {
    NARGS(0, 0); KList *l = newlist(KARGC);
    for (int i = 1; i < KARGC; i++) lpush(l, cstr(KARGV[i]));
    return mkp(T_LIST, l);
}
B(b_exit) { NARGS(1, 1); fflush(stdout); fflush(stderr); _exit((int)argv[0].u.i); return UNIT; }
B(b_run) {
    NARGS(1, 1); fflush(stdout);
    FILE *f = popen(CSTR(argv[0]), "r");
    if (!f) { V t[2] = { mkint(127), cstr("") }; return mktuple(2, t); }
    Buf b = {0}; char tmp[4096]; size_t n;
    while ((n = fread(tmp, 1, sizeof tmp, f)) > 0) bput(&b, tmp, (int64_t)n);
    int st = pclose(f);
    int code = st == -1 ? 127 : WIFEXITED(st) ? WEXITSTATUS(st) : 128 + WTERMSIG(st);
    V t[2] = { mkint(code), mkstr(b.s ? b.s : "", b.len) }; return mktuple(2, t);
}
B(b_ord) {
    NARGS(1, 1); KStr *k = STR(argv[0]); int n;
    return mkint(k->len ? (int64_t)utf8_at(k->s, k->len, 0, &n) : -1);
}
static int u8enc(char *o, uint32_t cp) {
    if (cp < 0x80) { o[0] = (char)cp; return 1; }
    if (cp < 0x800) { o[0] = (char)(0xC0 | cp >> 6); o[1] = (char)(0x80 | (cp & 0x3F)); return 2; }
    if (cp < 0x10000) { o[0] = (char)(0xE0 | cp >> 12); o[1] = (char)(0x80 | ((cp >> 6) & 0x3F)); o[2] = (char)(0x80 | (cp & 0x3F)); return 3; }
    o[0] = (char)(0xF0 | cp >> 18); o[1] = (char)(0x80 | ((cp >> 12) & 0x3F)); o[2] = (char)(0x80 | ((cp >> 6) & 0x3F)); o[3] = (char)(0x80 | (cp & 0x3F)); return 4;
}
B(b_chr) {
    NARGS(1, 1); int64_t c = argv[0].u.i; char o[4];
    if (c < 0 || c > 0x10FFFF || (c >= 0xD800 && c <= 0xDFFF)) c = 0xFFFD;
    return mkstr(o, u8enc(o, (uint32_t)c));
}
B(b_unix_ms) {
    NARGS(0, 0); struct timespec ts; clock_gettime(CLOCK_REALTIME, &ts);
    return mkint((int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000);
}
/* 第 cp 个字符的字节偏移（超出时为字符串长度） */
static int64_t ubyte(KStr *s, int64_t cp) {
    int64_t i = 0;
    while (cp > 0 && i < s->len) { i += u8len((unsigned char)s->s[i]); cp--; }
    return i > s->len ? s->len : i;
}
B(b_str_slice) {
    NARGS(3, 3); KStr *s = STR(argv[0]);
    int64_t a = argv[1].u.i, b = argv[2].u.i; if (a < 0) a = 0; if (b < a) b = a;
    int64_t x = ubyte(s, a), y = x;
    for (int64_t k = b - a; k > 0 && y < s->len; k--) y += u8len((unsigned char)s->s[y]);
    if (y > s->len) y = s->len;
    return mkstr(s->s + x, y - x);
}
static int64_t iarg(KFn *self, V v) { if (v.t != T_INT) die("%s 需要 Int", self->name); return v.u.i; }
static int64_t shift_n(int64_t n) { if (n < 0 || n >= 64) die("移位位数超出范围: %lld", (long long)n); return n; }
B(b_band) { NARGS(2, 2); return mkint(iarg(self, argv[0]) & iarg(self, argv[1])); }
B(b_bor) { NARGS(2, 2); return mkint(iarg(self, argv[0]) | iarg(self, argv[1])); }
B(b_bxor) { NARGS(2, 2); return mkint(iarg(self, argv[0]) ^ iarg(self, argv[1])); }
B(b_bnot) { NARGS(1, 1); return mkint(~iarg(self, argv[0])); }
B(b_shl) { NARGS(2, 2); int64_t a = iarg(self, argv[0]), n = shift_n(iarg(self, argv[1])); return mkint((int64_t)((uint64_t)a << n)); }
B(b_shr) { NARGS(2, 2); int64_t a = iarg(self, argv[0]), n = shift_n(iarg(self, argv[1])); return mkint(a >> n); }
B(b_slice) {
    NARGS(4, 4); V x = argv[0];
    if (x.t != T_STR && x.t != T_LIST) die("%s 不支持切片", tname(x));
    int64_t a = argv[1].u.i, b = argv[2].u.i, mode = argv[3].u.i;
    int64_t n = x.t == T_STR ? ucount(STR(x)) : LIST(x)->len;
    int64_t i = a < 0 ? a + n : a;
    int64_t j = mode == 2 ? n : (b < 0 ? b + n : b) + (mode == 1);
    if (!(0 <= i && i <= j && j <= n)) {
        if (mode == 2) die("切片越界: %lld..，长度 %lld", (long long)a, (long long)n);
        die("切片越界: %lld%s%lld，长度 %lld", (long long)a, mode == 1 ? "..=" : "..", (long long)b, (long long)n);
    }
    if (x.t == T_LIST) return mklist(j - i, LIST(x)->a + i);
    KStr *s = STR(x); int64_t bx = ubyte(s, i), by = bx;
    for (int64_t k = j - i; k > 0; k--) by += u8len((unsigned char)s->s[by]);
    return mkstr(s->s + bx, by - bx);
}
B(b_str_find) {
    NARGS(3, 3); KStr *s = STR(argv[0]), *p = STR(argv[1]); int64_t st = argv[2].u.i;
    if (st < 0) st = 0;
    if (st > ucount(s)) return mkint(-1);
    int64_t x = ubyte(s, st);
    for (int64_t i = x; i + p->len <= s->len; i += u8len((unsigned char)s->s[i])) {
        if (memcmp(s->s + i, p->s, p->len) == 0) {
            int64_t c = st;
            for (int64_t j = x; j < i; j += u8len((unsigned char)s->s[j])) c++;
            return mkint(c);
        }
    }
    return mkint(-1);
}
BF(b_env_get, "__env_get"); BF(b_args, "__args"); BF(b_exit, "__exit"); BF(b_run, "__run");
BF(b_ord, "__ord"); BF(b_chr, "__chr"); BF(b_unix_ms, "__unix_ms");
BF(b_str_slice, "__str_slice"); BF(b_slice, "__slice");
BF(b_band, "__band"); BF(b_bor, "__bor"); BF(b_bxor, "__bxor"); BF(b_bnot, "__bnot"); BF(b_shl, "__shl"); BF(b_shr, "__shr"); BF(b_str_find, "__str_find");

/* ── TCP 原语（std/net.kite、std/http.kite 用）：套接字都是非阻塞的，"会阻塞" 时返回 None，
   由 Kite 代码 sleep(1) 后重试，这样等待 I/O 会让出调度、可被取消，GC 也不会被卡住 ── */
typedef struct { char *in; int64_t ilen, icap; char *out; int64_t olen, opos, ocap; int eof; void *ssl; int64_t wpend; } NetBuf;
#define NET_MAX 65536
static NetBuf *NETB[NET_MAX];
static pthread_mutex_t net_mu = PTHREAD_MUTEX_INITIALIZER;
static NetBuf *netb(int64_t fd) {
    if (fd < 0 || fd >= NET_MAX) return NULL;
    pthread_mutex_lock(&net_mu);
    if (!NETB[fd]) NETB[fd] = calloc(1, sizeof(NetBuf));
    NetBuf *b = NETB[fd]; pthread_mutex_unlock(&net_mu); return b;
}
static V net_err(const char *what) { char m[512]; snprintf(m, sizeof m, "%s: %s", what, strerror(errno)); return kErr(cstr(m)); }
static V net_closed(void) { return kErr(cstr("连接已关闭")); }
static void set_nb(int fd) { fcntl(fd, F_SETFL, fcntl(fd, F_GETFL, 0) | O_NONBLOCK); }
/* 解析主机（IPv4 优先，其次 IPv6；host 为 "" 时是 0.0.0.0），结果写进 sa，返回地址长度；失败返回 -1 */
static int net_addr_t(const char *host, int64_t port, struct sockaddr_storage *sa, int socktype) {
    struct addrinfo hints, *res, *r; memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_UNSPEC; hints.ai_socktype = socktype;
    if (!*host) host = "0.0.0.0";
    char h2[300]; size_t hl = strlen(host);
    if (hl >= 2 && host[0] == '[' && host[hl - 1] == ']' && hl < sizeof h2) { memcpy(h2, host + 1, hl - 2); h2[hl - 2] = 0; host = h2; }
    gc_block_enter(); int rc = getaddrinfo(host, NULL, &hints, &res); gc_block_leave();
    if (rc != 0 || !res) return -1;
    struct addrinfo *pick = NULL;
    for (r = res; r; r = r->ai_next) if (r->ai_family == AF_INET) { pick = r; break; }
    if (!pick) for (r = res; r; r = r->ai_next) if (r->ai_family == AF_INET6) { pick = r; break; }
    if (!pick) { freeaddrinfo(res); return -1; }
    int n = (int)pick->ai_addrlen; memset(sa, 0, sizeof *sa); memcpy(sa, pick->ai_addr, n);
    if (sa->ss_family == AF_INET) ((struct sockaddr_in *)sa)->sin_port = htons((uint16_t)port);
    else ((struct sockaddr_in6 *)sa)->sin6_port = htons((uint16_t)port);
    freeaddrinfo(res); return n;
}
static int net_addr(const char *host, int64_t port, struct sockaddr_storage *sa) { return net_addr_t(host, port, sa, SOCK_STREAM); }
static V no_host(const char *h) { char m[300]; snprintf(m, sizeof m, "无法解析主机 %s", h); return kErr(cstr(m)); }
B(b_tcp_listen) {
    NARGS(2, 2); const char *h = CSTR(argv[0]); int64_t port = argv[1].u.i; char what[300];
    snprintf(what, sizeof what, "无法监听 %s:%lld", h, (long long)port);
    struct sockaddr_storage sa; int sl = net_addr(h, port, &sa);
    if (sl < 0) return no_host(h);
    int fd = socket(sa.ss_family, SOCK_STREAM, 0); if (fd < 0) return net_err(what);
    int one = 1; setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    if (bind(fd, (struct sockaddr *)&sa, sl) < 0 || listen(fd, 128) < 0) { V e = net_err(what); close(fd); return e; }
    if (fd >= NET_MAX) { close(fd); return kErr(cstr("文件描述符太多")); }
    set_nb(fd); return kOk(mkint(fd));
}
B(b_tcp_port) {
    NARGS(1, 1); struct sockaddr_storage sa; socklen_t n = sizeof sa;
    if (getsockname((int)argv[0].u.i, (struct sockaddr *)&sa, &n) < 0) return mkint(-1);
    if (sa.ss_family == AF_INET) return mkint(ntohs(((struct sockaddr_in *)&sa)->sin_port));
    if (sa.ss_family == AF_INET6) return mkint(ntohs(((struct sockaddr_in6 *)&sa)->sin6_port));
    return mkint(0);
}
B(b_tcp_accept) {
    NARGS(1, 1); int c = accept((int)argv[0].u.i, NULL, NULL);
    if (c < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR || errno == ECONNABORTED) return kOk(kNone());
        return net_err("accept 失败");
    }
    if (c >= NET_MAX) { close(c); return kOk(kNone()); }
    set_nb(c); int one = 1; setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    return kOk(kSome(mkint(c)));
}
B(b_tcp_connect) {
    NARGS(2, 2); const char *h = CSTR(argv[0]); int64_t port = argv[1].u.i; char what[300];
    snprintf(what, sizeof what, "无法连接 %s:%lld", h, (long long)port);
    struct sockaddr_storage sa; int sl = net_addr(h, port, &sa);
    if (sl < 0) return no_host(h);
    int fd = socket(sa.ss_family, SOCK_STREAM, 0); if (fd < 0) return net_err(what);
    gc_block_enter(); int rc = connect(fd, (struct sockaddr *)&sa, sl); int en = errno; gc_block_leave();
    if (rc < 0) { errno = en; V e = net_err(what); close(fd); return e; }
    if (fd >= NET_MAX) { close(fd); return kErr(cstr("文件描述符太多")); }
    set_nb(fd); int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    return kOk(mkint(fd));
}
/* ── UDP / Unix 套接字 / DNS（std/net.kite 用）：数据报按字节列表收发；"会阻塞" 时返回 None ── */
static V sockaddr_val(struct sockaddr_storage *sa, socklen_t n) {
    char ip[INET6_ADDRSTRLEN + 1] = { 0 }; int port = 0;
    if (sa->ss_family == AF_INET) {
        inet_ntop(AF_INET, &((struct sockaddr_in *)sa)->sin_addr, ip, sizeof ip); port = ntohs(((struct sockaddr_in *)sa)->sin_port);
    } else if (sa->ss_family == AF_INET6) {
        struct sockaddr_in6 *s6 = (struct sockaddr_in6 *)sa;
        inet_ntop(AF_INET6, &s6->sin6_addr, ip, sizeof ip); port = ntohs(s6->sin6_port);
        if (s6->sin6_scope_id) { char z[24]; snprintf(z, sizeof z, "%%%u", (unsigned)s6->sin6_scope_id); strncat(ip, z, sizeof ip - strlen(ip) - 1); }
    } else if (sa->ss_family == AF_UNIX) {
        struct sockaddr_un *su = (struct sockaddr_un *)sa;
        size_t off = offsetof(struct sockaddr_un, sun_path);
        size_t l = n > off ? strnlen(su->sun_path, n - off) : 0;
        V t[2] = { mkstr(su->sun_path, l), mkint(-1) }; return mktuple(2, t);
    }
    V t[2] = { cstr(ip), mkint(port) }; return mktuple(2, t);
}
B(b_sock_name) {
    NARGS(2, 2); struct sockaddr_storage sa; socklen_t n = sizeof sa; memset(&sa, 0, sizeof sa);
    int r = (argv[1].t == T_BOOL && argv[1].u.i) ? getpeername((int)argv[0].u.i, (struct sockaddr *)&sa, &n)
                                                  : getsockname((int)argv[0].u.i, (struct sockaddr *)&sa, &n);
    if (r < 0) { V t[2] = { cstr(""), mkint(0) }; return mktuple(2, t); }
    return sockaddr_val(&sa, n);
}
/* __udp_open(host, port, connect)：connect 为 false 时绑定 host:port；为 true 时连接到 host:port */
B(b_udp_open) {
    NARGS(3, 3); const char *h = CSTR(argv[0]); int64_t port = argv[1].u.i; int conn = argv[2].t == T_BOOL && argv[2].u.i;
    char what[300]; snprintf(what, sizeof what, conn ? "无法连接 udp %s:%lld" : "无法监听 udp %s:%lld", h, (long long)port);
    struct sockaddr_storage sa; int sl = net_addr_t(h, port, &sa, SOCK_DGRAM);
    if (sl < 0) return no_host(h);
    int fd = socket(sa.ss_family, SOCK_DGRAM, 0); if (fd < 0) return net_err(what);
    int rc = conn ? connect(fd, (struct sockaddr *)&sa, sl) : bind(fd, (struct sockaddr *)&sa, sl);
    if (rc < 0) { V e = net_err(what); close(fd); return e; }
    if (fd >= NET_MAX) { close(fd); return kErr(cstr("文件描述符太多")); }
    set_nb(fd); return kOk(mkint(fd));
}
static int bytes_arg(V v, char **out, int64_t *n) {
    KList *l = LIST(v); char *b = malloc(l->len ? l->len : 1);
    for (int64_t i = 0; i < l->len; i++) b[i] = (char)(l->a[i].t == T_INT ? l->a[i].u.i : 0);
    *out = b; *n = l->len; return 0;
}
static V bytes_val(const char *b, int64_t n) {
    KList *l = newlist(n); for (int64_t i = 0; i < n; i++) l->a[i] = mkint((unsigned char)b[i]);
    l->len = n; return mkp(T_LIST, l);
}
static int unix_addr(const char *path, struct sockaddr_un *su) {
    memset(su, 0, sizeof *su); su->sun_family = AF_UNIX;
    size_t l = strlen(path); if (l >= sizeof su->sun_path) return -1;
    memcpy(su->sun_path, path, l);
    if (path[0] == '@') su->sun_path[0] = 0;   /* Linux 抽象命名空间 */
    return (int)(offsetof(struct sockaddr_un, sun_path) + l);
}
/* __sock_sendto(fd, kind, host, port, data)：kind 0 = 已连接（忽略地址），1 = IP 地址，2 = Unix 路径 */
B(b_sock_sendto) {
    NARGS(5, 5); int fd = (int)argv[0].u.i; int64_t kind = argv[1].u.i; const char *h = CSTR(argv[2]);
    char *buf; int64_t n; bytes_arg(argv[4], &buf, &n); ssize_t r;
    if (kind == 0) r = send(fd, buf, n, MSG_NOSIGNAL);
    else if (kind == 1) {
        struct sockaddr_storage sa; int sl = net_addr_t(h, argv[3].u.i, &sa, SOCK_DGRAM);
        if (sl < 0) { free(buf); return no_host(h); }
        r = sendto(fd, buf, n, MSG_NOSIGNAL, (struct sockaddr *)&sa, sl);
    } else {
        struct sockaddr_un su; int sl = unix_addr(h, &su);
        if (sl < 0) { free(buf); return kErr(cstr("Unix 套接字路径太长")); }
        r = sendto(fd, buf, n, MSG_NOSIGNAL, (struct sockaddr *)&su, sl);
    }
    free(buf);
    if (r < 0) { if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) return kOk(kNone()); return net_err("发送失败"); }
    return kOk(kSome(mkint(r)));
}
/* __sock_recvfrom(fd, max) → Some((字节, 地址, 端口)) */
B(b_sock_recvfrom) {
    NARGS(2, 2); int fd = (int)argv[0].u.i; int64_t mx = argv[1].u.i; if (mx <= 0) mx = 65536;
    char *buf = malloc(mx); struct sockaddr_storage sa; socklen_t sl = sizeof sa; memset(&sa, 0, sizeof sa);
    ssize_t r = recvfrom(fd, buf, mx, 0, (struct sockaddr *)&sa, &sl);
    if (r < 0) { free(buf); if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) return kOk(kNone()); return net_err("接收失败"); }
    V a = sockaddr_val(&sa, sl);
    if (sl == 0) { V t2[2] = { cstr(""), mkint(0) }; a = mktuple(2, t2); }
    V t[3] = { bytes_val(buf, r), TUP(a)->a[0], TUP(a)->a[1] }; free(buf);
    return kOk(kSome(mktuple(3, t)));
}
/* __unix_open(path, dgram, mode)：mode 0 = 监听/绑定，1 = 连接 */
B(b_unix_open) {
    NARGS(3, 3); const char *p = CSTR(argv[0]); int dg = argv[1].t == T_BOOL && argv[1].u.i; int64_t mode = argv[2].u.i;
    char what[400]; snprintf(what, sizeof what, mode ? "无法连接 unix %s" : "无法监听 unix %s", p);
    struct sockaddr_un su; int sl = unix_addr(p, &su);
    if (sl < 0) return kErr(cstr("Unix 套接字路径太长"));
    int fd = socket(AF_UNIX, dg ? SOCK_DGRAM : SOCK_STREAM, 0); if (fd < 0) return net_err(what);
    int rc;
    if (mode) { gc_block_enter(); rc = connect(fd, (struct sockaddr *)&su, sl); gc_block_leave(); }
    else { rc = bind(fd, (struct sockaddr *)&su, sl); if (rc == 0 && !dg) rc = listen(fd, 128); }
    if (rc < 0) { V e = net_err(what); close(fd); return e; }
    if (fd >= NET_MAX) { close(fd); return kErr(cstr("文件描述符太多")); }
    set_nb(fd); return kOk(mkint(fd));
}
/* __dns_lookup(host, canon)：canon 为 false 时返回全部地址（按 getaddrinfo 顺序去重）；为 true 时返回 [规范名] */
B(b_dns_lookup) {
    NARGS(2, 2); const char *h = CSTR(argv[0]); int canon = argv[1].t == T_BOOL && argv[1].u.i;
    struct addrinfo hints, *res, *r; memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_UNSPEC; hints.ai_socktype = SOCK_STREAM; if (canon) hints.ai_flags = AI_CANONNAME;
    gc_block_enter(); int rc = getaddrinfo(h, NULL, &hints, &res); gc_block_leave();
    if (rc != 0) { char m[400]; snprintf(m, sizeof m, "lookup %s: %s", h, rc == EAI_NONAME ? "no such host" : gai_strerror(rc)); return kErr(cstr(m)); }
    KList *l = newlist(8);
    if (canon) { lpush(l, cstr(res && res->ai_canonname ? res->ai_canonname : h)); freeaddrinfo(res); return kOk(mkp(T_LIST, l)); }
    for (r = res; r; r = r->ai_next) {
        char ip[INET6_ADDRSTRLEN] = { 0 };
        if (r->ai_family == AF_INET) inet_ntop(AF_INET, &((struct sockaddr_in *)r->ai_addr)->sin_addr, ip, sizeof ip);
        else if (r->ai_family == AF_INET6) inet_ntop(AF_INET6, &((struct sockaddr_in6 *)r->ai_addr)->sin6_addr, ip, sizeof ip);
        else continue;
        int dup = 0; for (int64_t i = 0; i < l->len; i++) if (strcmp(STR(l->a[i])->s, ip) == 0) dup = 1;
        if (!dup) lpush(l, cstr(ip));
    }
    freeaddrinfo(res); return kOk(mkp(T_LIST, l));
}
/* __dns_reverse(ip) → 主机名 */
B(b_dns_reverse) {
    NARGS(1, 1); const char *ip = CSTR(argv[0]); struct sockaddr_storage sa; memset(&sa, 0, sizeof sa); socklen_t sl;
    if (inet_pton(AF_INET, ip, &((struct sockaddr_in *)&sa)->sin_addr) == 1) { sa.ss_family = AF_INET; sl = sizeof(struct sockaddr_in); }
    else if (inet_pton(AF_INET6, ip, &((struct sockaddr_in6 *)&sa)->sin6_addr) == 1) { sa.ss_family = AF_INET6; sl = sizeof(struct sockaddr_in6); }
    else { char m[300]; snprintf(m, sizeof m, "unrecognized address: %s", ip); return kErr(cstr(m)); }
    char host[NI_MAXHOST];
    gc_block_enter(); int rc = getnameinfo((struct sockaddr *)&sa, sl, host, sizeof host, NULL, 0, NI_NAMEREQD); gc_block_leave();
    if (rc != 0) { char m[400]; snprintf(m, sizeof m, "lookup %s: %s", ip, rc == EAI_NONAME ? "no such host" : gai_strerror(rc)); return kErr(cstr(m)); }
    return kOk(cstr(host));
}
/* __dns_port(network, service) → 端口（/etc/services） */
B(b_dns_port) {
    NARGS(2, 2); const char *net = CSTR(argv[0]), *svc = CSTR(argv[1]);
    const char *proto = strncmp(net, "udp", 3) == 0 ? "udp" : "tcp";
    struct servent *se = getservbyname(svc, proto);
    if (!se) { char m[400]; snprintf(m, sizeof m, "lookup %s/%s: unknown port", net, svc); return kErr(cstr(m)); }
    return kOk(mkint(ntohs((uint16_t)se->s_port)));
}
BF(b_sock_name, "__sock_name"); BF(b_udp_open, "__udp_open"); BF(b_sock_sendto, "__sock_sendto");
BF(b_sock_recvfrom, "__sock_recvfrom"); BF(b_unix_open, "__unix_open"); BF(b_dns_lookup, "__dns_lookup");
BF(b_dns_reverse, "__dns_reverse"); BF(b_dns_port, "__dns_port");
/* ── TLS：运行时 dlopen libssl（不需要头文件，也不需要编译期链接）；SSL 对象挂在 NetBuf 上，
   读写缓冲层把 SSL_ERROR_WANT_READ/WRITE 当作"会阻塞" ── */
typedef struct {
    int ok;
    void *(*client_method)(void); void *(*server_method)(void);
    void *(*ctx_new)(const void *); void (*ctx_free)(void *);
    int (*ctx_default_paths)(void *); int (*ctx_load_verify)(void *, const char *, const char *);
    void (*ctx_set_verify)(void *, int, void *); uint64_t (*ctx_set_options)(void *, uint64_t);
    int (*ctx_use_chain)(void *, const char *); int (*ctx_use_key)(void *, const char *, int);
    int (*ctx_check_key)(const void *);
    void *(*ssl_new)(void *); void (*ssl_free)(void *); int (*set_fd)(void *, int);
    void (*set_connect)(void *); void (*set_accept)(void *);
    long (*ssl_ctrl)(void *, int, long, void *); int (*set1_host)(void *, const char *);
    int (*do_handshake)(void *); int (*ssl_read)(void *, void *, int); int (*ssl_write)(void *, const void *, int);
    int (*get_error)(const void *, int); int (*shutdown)(void *); long (*verify_result)(const void *);
    const char *(*verify_str)(long); unsigned long (*err_get)(void); const char *(*err_reason)(unsigned long);
    void (*err_clear)(void);
} TlsLib;
static TlsLib TLS;
static pthread_once_t tls_once = PTHREAD_ONCE_INIT;
static void tls_load(void) {
    const char *ssl_names[] = {"libssl.so.3", "libssl.so", "libssl.3.dylib", "/opt/homebrew/opt/openssl@3/lib/libssl.3.dylib",
                               "/usr/local/opt/openssl@3/lib/libssl.3.dylib", NULL};
    const char *cry_names[] = {"libcrypto.so.3", "libcrypto.so", "libcrypto.3.dylib", "/opt/homebrew/opt/openssl@3/lib/libcrypto.3.dylib",
                               "/usr/local/opt/openssl@3/lib/libcrypto.3.dylib", NULL};
    void *h = NULL, *c = NULL;
    for (int i = 0; !h && ssl_names[i]; i++) h = dlopen(ssl_names[i], RTLD_NOW | RTLD_GLOBAL);
    for (int i = 0; !c && cry_names[i]; i++) c = dlopen(cry_names[i], RTLD_NOW | RTLD_GLOBAL);
    if (!h || !c) return;
    signal(SIGPIPE, SIG_IGN);   /* SSL_write 用 write()，对方断开时不能让进程被 SIGPIPE 杀掉 */
#define TS(f, n) if (!(*(void **)&TLS.f = dlsym(h, n))) return;
#define TC(f, n) if (!(*(void **)&TLS.f = dlsym(c, n))) return;
    TS(client_method, "TLS_client_method") TS(server_method, "TLS_server_method") TS(ctx_new, "SSL_CTX_new")
    TS(ctx_free, "SSL_CTX_free") TS(ctx_default_paths, "SSL_CTX_set_default_verify_paths")
    TS(ctx_load_verify, "SSL_CTX_load_verify_locations") TS(ctx_set_verify, "SSL_CTX_set_verify")
    TS(ctx_set_options, "SSL_CTX_set_options") TS(ctx_use_chain, "SSL_CTX_use_certificate_chain_file")
    TS(ctx_use_key, "SSL_CTX_use_PrivateKey_file") TS(ctx_check_key, "SSL_CTX_check_private_key")
    TS(ssl_new, "SSL_new") TS(ssl_free, "SSL_free") TS(set_fd, "SSL_set_fd")
    TS(set_connect, "SSL_set_connect_state") TS(set_accept, "SSL_set_accept_state") TS(ssl_ctrl, "SSL_ctrl")
    TS(set1_host, "SSL_set1_host") TS(do_handshake, "SSL_do_handshake") TS(ssl_read, "SSL_read")
    TS(ssl_write, "SSL_write") TS(get_error, "SSL_get_error") TS(shutdown, "SSL_shutdown")
    TS(verify_result, "SSL_get_verify_result")
    TC(verify_str, "X509_verify_cert_error_string") TC(err_get, "ERR_get_error") TC(err_reason, "ERR_reason_error_string")
    TC(err_clear, "ERR_clear_error")
#undef TS
#undef TC
    TLS.ok = 1;
}
static int tls_ready(void) { pthread_once(&tls_once, tls_load); return TLS.ok; }
static V tls_unavailable(void) { return kErr(cstr("TLS 不可用：找不到 libssl / libcrypto（OpenSSL 3）")); }
#define K_SSL_ERROR_SSL 1
#define K_SSL_WANT_READ 2
#define K_SSL_WANT_WRITE 3
#define K_SSL_SYSCALL 5
#define K_SSL_ZERO_RETURN 6
static __thread char tls_msg[256];
/* 取出 OpenSSL 错误队列里最后一个原因（与 Python ssl 的 reason 小写化后一致） */
static const char *tls_reason(void) {
    unsigned long e, last = 0;
    while ((e = TLS.err_get()) != 0) last = e;
    const char *r = last ? TLS.err_reason(last) : NULL;
    snprintf(tls_msg, sizeof tls_msg, "%s", r ? r : (errno ? strerror(errno) : "unknown error"));
    return tls_msg;
}
/* 每个 TLS 服务端上下文一个编号（从 1 开始） */
#define TLS_CTX_MAX 256
static void *TLS_SCTX[TLS_CTX_MAX]; static int tls_nctx;
#define K_SSL_OP_IGNORE_UNEXPECTED_EOF (1ULL << 7)
B(b_tls_server_ctx) {
    NARGS(2, 2); const char *cert = CSTR(argv[0]), *key = CSTR(argv[1]); char m[600];
    if (!tls_ready()) return tls_unavailable();
    const char *bad = access(cert, R_OK) < 0 ? cert : access(key, R_OK) < 0 ? key : NULL;
    if (bad) { snprintf(m, sizeof m, "无法加载证书 %s: %s", cert, strerror(errno)); return kErr(cstr(m)); }
    void *ctx = TLS.ctx_new(TLS.server_method());
    TLS.ctx_set_options(ctx, K_SSL_OP_IGNORE_UNEXPECTED_EOF);
    if (TLS.ctx_use_chain(ctx, cert) != 1 || TLS.ctx_use_key(ctx, key, 1) != 1 || TLS.ctx_check_key(ctx) != 1) {
        snprintf(m, sizeof m, "无法加载证书 %s: %s", cert, tls_reason()); TLS.ctx_free(ctx); return kErr(cstr(m));
    }
    pthread_mutex_lock(&net_mu);
    if (tls_nctx >= TLS_CTX_MAX) { pthread_mutex_unlock(&net_mu); TLS.ctx_free(ctx); return kErr(cstr("TLS 上下文太多")); }
    TLS_SCTX[tls_nctx++] = ctx; int id = tls_nctx; pthread_mutex_unlock(&net_mu);
    return kOk(mkint(id));
}
static V tls_attach(int64_t fd, void *ctx, int server, const char *host) {
    NetBuf *b = netb(fd); if (!b) return net_closed();
    TLS.err_clear();
    void *ssl = TLS.ssl_new(ctx);
    if (!ssl) { char m[300]; snprintf(m, sizeof m, "TLS 握手失败: %s", tls_reason()); return kErr(cstr(m)); }
    TLS.set_fd(ssl, (int)fd);
    if (server) TLS.set_accept(ssl);
    else {
        TLS.ssl_ctrl(ssl, 55 /* SSL_CTRL_SET_TLSEXT_HOSTNAME */, 0, (void *)host);
        TLS.set1_host(ssl, host);
        TLS.set_connect(ssl);
    }
    b->ssl = ssl; b->wpend = 0;
    return kOk(UNIT);
}
B(b_tls_accept) {
    NARGS(2, 2); int64_t id = argv[1].u.i;
    if (!tls_ready()) return tls_unavailable();
    if (id < 1 || id > tls_nctx) return kErr(cstr("无效的 TLS 上下文"));
    return tls_attach(argv[0].u.i, TLS_SCTX[id - 1], 1, NULL);
}
B(b_tls_client) {
    NARGS(4, 4); const char *host = CSTR(argv[1]), *ca = CSTR(argv[2]); int verify = (int)argv[3].u.i; char m[600];
    if (!tls_ready()) return tls_unavailable();
    TLS.err_clear();
    void *ctx = TLS.ctx_new(TLS.client_method());
    TLS.ctx_set_options(ctx, K_SSL_OP_IGNORE_UNEXPECTED_EOF);
    if (*ca) {
        if (access(ca, R_OK) < 0) { snprintf(m, sizeof m, "无法加载 CA %s: %s", ca, strerror(errno)); TLS.ctx_free(ctx); return kErr(cstr(m)); }
        if (TLS.ctx_load_verify(ctx, ca, NULL) != 1) { snprintf(m, sizeof m, "无法加载 CA %s: %s", ca, tls_reason()); TLS.ctx_free(ctx); return kErr(cstr(m)); }
    } else TLS.ctx_default_paths(ctx);
    TLS.ctx_set_verify(ctx, verify ? 1 /* SSL_VERIFY_PEER */ : 0, NULL);
    V r = tls_attach(argv[0].u.i, ctx, 0, verify ? host : NULL);
    TLS.ctx_free(ctx);          /* SSL 对象持有上下文的引用 */
    return r;
}
B(b_tls_handshake) {
    NARGS(1, 1); NetBuf *b = netb(argv[0].u.i); if (!b || !b->ssl) return net_closed();
    TLS.err_clear(); errno = 0;
    int rc = TLS.do_handshake(b->ssl);
    if (rc == 1) return kOk(mkbool(true));
    int e = TLS.get_error(b->ssl, rc);
    if (e == K_SSL_WANT_READ || e == K_SSL_WANT_WRITE) return kOk(mkbool(false));
    char m[400]; long vr = TLS.verify_result(b->ssl);
    if (vr != 0) snprintf(m, sizeof m, "TLS 握手失败: %s", TLS.verify_str(vr));
    else snprintf(m, sizeof m, "TLS 握手失败: %s", tls_reason());
    return kErr(cstr(m));
}

/* 把内核里已有的数据读进缓冲区；出错返回 -1（TLS 错误返回 -2，原因在 tls_msg） */
static int net_fill(int fd, NetBuf *b) {
    while (!b->eof) {
        if (b->icap - b->ilen < 4096) { b->icap = b->icap * 2 + 8192; b->in = realloc(b->in, b->icap); }
        if (b->ssl) {
            TLS.err_clear(); errno = 0;
            int n = TLS.ssl_read(b->ssl, b->in + b->ilen, (int)(b->icap - b->ilen));
            if (n > 0) { b->ilen += n; continue; }
            int e = TLS.get_error(b->ssl, n);
            if (e == K_SSL_WANT_READ || e == K_SSL_WANT_WRITE) break;
            if (e == K_SSL_ZERO_RETURN || (e == K_SSL_SYSCALL && errno == 0)) { b->eof = 1; break; }
            if (e == K_SSL_SYSCALL) return -1;
            tls_reason(); return -2;
        }
        ssize_t n = recv(fd, b->in + b->ilen, b->icap - b->ilen, 0);
        if (n > 0) { b->ilen += n; continue; }
        if (n == 0) { b->eof = 1; break; }
        if (errno == EAGAIN || errno == EWOULDBLOCK) break;
        if (errno == EINTR) continue;
        return -1;
    }
    return 0;
}
static V net_ferr(int rc, const char *what) {
    if (rc == -2) { char m[400]; snprintf(m, sizeof m, "%s: %s", what, tls_msg); return kErr(cstr(m)); }
    return net_err(what);
}
static V net_take(NetBuf *b, int64_t n, int64_t skip) {
    V s = mkstr(b->in, n);
    memmove(b->in, b->in + n + skip, b->ilen - n - skip); b->ilen -= n + skip;
    return s;
}
B(b_tcp_read_line) {
    NARGS(1, 1); int fd = (int)argv[0].u.i; NetBuf *b = netb(fd); if (!b) return net_closed();
    for (int pass = 0; pass < 2; pass++) {
        char *nl = b->ilen ? memchr(b->in, '\n', b->ilen) : NULL;
        if (nl) {
            int64_t n = nl - b->in, k = n > 0 && b->in[n - 1] == '\r' ? n - 1 : n;
            V s = mkstr(b->in, k); net_take(b, n, 1); return kOk(kSome(s));
        }
        if (pass == 0) { int rc = net_fill(fd, b); if (rc < 0) return net_ferr(rc, "读取失败"); }
    }
    return b->eof ? net_closed() : kOk(kNone());
}
B(b_tcp_read_bytes) {
    NARGS(2, 2); int fd = (int)argv[0].u.i; int64_t want = argv[1].u.i < 0 ? 0 : argv[1].u.i;
    NetBuf *b = netb(fd); if (!b) return net_closed();
    if (b->ilen < want) { int rc = net_fill(fd, b); if (rc < 0) return net_ferr(rc, "读取失败"); }
    if (b->ilen >= want) return kOk(kSome(net_take(b, want, 0)));
    return b->eof ? net_closed() : kOk(kNone());
}
B(b_tcp_read_all) {
    NARGS(1, 1); int fd = (int)argv[0].u.i; NetBuf *b = netb(fd); if (!b) return net_closed();
    { int rc = net_fill(fd, b); if (rc < 0) return net_ferr(rc, "读取失败"); }
    return b->eof ? kOk(kSome(net_take(b, b->ilen, 0))) : kOk(kNone());
}
static V net_flush(int fd, NetBuf *b) {
    while (b->opos < b->olen) {
        if (b->ssl) {
            int len = (int)(b->wpend ? b->wpend : b->olen - b->opos);   /* WANT 之后必须用同样的参数重试 */
            TLS.err_clear(); errno = 0;
            int n = TLS.ssl_write(b->ssl, b->out + b->opos, len);
            if (n > 0) { b->opos += n; b->wpend = 0; continue; }
            int e = TLS.get_error(b->ssl, n);
            if (e == K_SSL_WANT_READ || e == K_SSL_WANT_WRITE) { b->wpend = len; return kOk(mkbool(false)); }
            if (e == K_SSL_SYSCALL && errno) return net_err("写入失败");
            tls_reason(); return net_ferr(-2, "写入失败");
        }
        ssize_t n = send(fd, b->out + b->opos, b->olen - b->opos, MSG_NOSIGNAL);
        if (n > 0) { b->opos += n; continue; }
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return kOk(mkbool(false));
        return net_err("写入失败");
    }
    b->olen = b->opos = 0; return kOk(mkbool(true));
}
B(b_tcp_write) {
    NARGS(2, 2); int fd = (int)argv[0].u.i; NetBuf *b = netb(fd); if (!b) return net_closed();
    KStr *d = STR(argv[1]);
    if (b->olen + d->len > b->ocap) { b->ocap = (b->olen + d->len) * 2 + 4096; b->out = realloc(b->out, b->ocap); }
    memcpy(b->out + b->olen, d->s, d->len); b->olen += d->len;
    return net_flush(fd, b);
}
B(b_tcp_flush) { NARGS(1, 1); int fd = (int)argv[0].u.i; NetBuf *b = netb(fd); if (!b) return net_closed(); return net_flush(fd, b); }
B(b_tcp_close) {
    NARGS(1, 1); int64_t fd = argv[0].u.i;
    if (fd >= 0 && fd < NET_MAX) {
        pthread_mutex_lock(&net_mu); NetBuf *b = NETB[fd]; NETB[fd] = NULL; pthread_mutex_unlock(&net_mu);
        if (b) { if (b->ssl) { TLS.shutdown(b->ssl); TLS.ssl_free(b->ssl); } free(b->in); free(b->out); free(b); }
        close((int)fd);
    }
    return UNIT;
}
B(b_byte_len) { NARGS(1, 1); return mkint(STR(argv[0])->len); }
BF(b_tcp_listen, "__tcp_listen"); BF(b_tcp_port, "__tcp_port"); BF(b_tcp_accept, "__tcp_accept");
BF(b_tcp_connect, "__tcp_connect"); BF(b_tcp_read_line, "__tcp_read_line"); BF(b_tcp_read_bytes, "__tcp_read_bytes");
BF(b_tcp_read_all, "__tcp_read_all"); BF(b_tcp_write, "__tcp_write"); BF(b_tcp_flush, "__tcp_flush");
/* UTF-8 字节：Str → List<Int>；List<Int> → Option<Str>（严格校验：过长编码、代理区、超出 U+10FFFF 都算非法） */
B(b_bytes) {
    NARGS(1, 1); KStr *s = STR(argv[0]); KList *l = newlist(s->len);
    for (int64_t i = 0; i < s->len; i++) l->a[i] = mkint((unsigned char)s->s[i]);
    l->len = s->len; return mkp(T_LIST, l);
}
static bool utf8_valid(const unsigned char *p, int64_t n) {
    int64_t i = 0;
    while (i < n) {
        unsigned c = p[i];
        if (c < 0x80) { i++; continue; }
        int k; unsigned cp, lo;
        if (c >= 0xC2 && c <= 0xDF) { k = 1; cp = c & 0x1F; lo = 0x80; }
        else if (c >= 0xE0 && c <= 0xEF) { k = 2; cp = c & 0x0F; lo = 0x800; }
        else if (c >= 0xF0 && c <= 0xF4) { k = 3; cp = c & 0x07; lo = 0x10000; }
        else return false;
        for (int j = 1; j <= k; j++) { if (i + j >= n || (p[i + j] & 0xC0) != 0x80) return false; cp = (cp << 6) | (p[i + j] & 0x3F); }
        if (cp < lo || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) return false;
        i += k + 1;
    }
    return true;
}
B(b_from_bytes) {
    NARGS(1, 1); KList *l = LIST(argv[0]);
    KStr *k = newstr(l->len, l->len + 1);
    for (int64_t i = 0; i < l->len; i++) {
        V b = l->a[i];
        if (b.t != T_INT || b.u.i < 0 || b.u.i > 255) return kNone();
        k->s[i] = (char)b.u.i;
    }
    k->s[l->len] = 0;
    if (!utf8_valid((unsigned char *)k->s, l->len)) return kNone();
    return kSome(mkp(T_STR, k));
}
/* std.os / std.time 用的系统调用 */
B(b_fs_stat) {
    NARGS(2, 2); struct stat st;
    int r = (argv[1].t == T_BOOL && argv[1].u.i) ? stat(CSTR(argv[0]), &st) : lstat(CSTR(argv[0]), &st);
    if (r != 0) return fs_err(argv[0]);
    int kind = S_ISREG(st.st_mode) ? 0 : S_ISDIR(st.st_mode) ? 1 : S_ISLNK(st.st_mode) ? 2 : 3;
    V t[4] = { mkint((int64_t)st.st_size), mkint((int64_t)st.st_mtim.tv_sec * 1000 + st.st_mtim.tv_nsec / 1000000),
               mkint(kind), mkint(st.st_mode & 07777) };
    return kOk(mktuple(4, t));
}
B(b_getcwd) { NARGS(0, 0); char buf[PATH_MAX]; if (!getcwd(buf, sizeof buf)) buf[0] = 0; return cstr(buf); }
B(b_chdir) { NARGS(1, 1); if (chdir(CSTR(argv[0])) != 0) return fs_err(argv[0]); return kOk(UNIT); }
B(b_chmod) { NARGS(2, 2); if (chmod(CSTR(argv[0]), (mode_t)argv[1].u.i) != 0) return fs_err(argv[0]); return kOk(UNIT); }
B(b_env_set) { NARGS(2, 2); setenv(CSTR(argv[0]), CSTR(argv[1]), 1); return UNIT; }
B(b_env_unset) { NARGS(1, 1); unsetenv(CSTR(argv[0])); return UNIT; }
extern char **environ;
B(b_environ) {
    NARGS(0, 0); int n = 0; while (environ[n]) n++;
    char **xs = malloc((n ? n : 1) * sizeof(char *)); for (int i = 0; i < n; i++) xs[i] = environ[i];
    qsort(xs, n, sizeof(char *), str_cmp);
    KList *l = newlist(n); for (int i = 0; i < n; i++) l->a[l->len++] = cstr(xs[i]);
    free(xs); return mkp(T_LIST, l);
}
B(b_hostname) { NARGS(0, 0); char buf[256] = { 0 }; gethostname(buf, sizeof buf - 1); return cstr(buf); }
B(b_getpid) { NARGS(0, 0); return mkint((int64_t)getpid()); }
B(b_read_bytes) {
    NARGS(1, 1); FILE *f = fopen(CSTR(argv[0]), "rb");
    if (!f) return fs_err(argv[0]);
    Buf b = { 0 }; char tmp[4096]; size_t n;
    while ((n = fread(tmp, 1, sizeof tmp, f)) > 0) bput(&b, tmp, n);
    fclose(f);
    KList *l = newlist(b.len);
    for (size_t i = 0; i < b.len; i++) l->a[i] = mkint((unsigned char)b.s[i]);
    l->len = b.len; return kOk(mkp(T_LIST, l));
}
B(b_write_bytes) {
    NARGS(3, 3); KList *l = LIST(argv[1]);
    char *buf = malloc(l->len ? l->len : 1);
    for (int64_t i = 0; i < l->len; i++) {
        V x = l->a[i];
        if (x.t != T_INT || x.u.i < 0 || x.u.i > 255) { free(buf); die("字节必须在 0 到 255 之间"); }
        buf[i] = (char)x.u.i;
    }
    FILE *f = fopen(CSTR(argv[0]), (argv[2].t == T_BOOL && argv[2].u.i) ? "ab" : "wb");
    if (!f) { free(buf); return fs_err(argv[0]); }
    fwrite(buf, 1, l->len, f); fclose(f); free(buf); return kOk(UNIT);
}
B(b_tz_offset) {
    NARGS(1, 1); time_t t = (time_t)(argv[0].u.i >= 0 ? argv[0].u.i / 1000 : -((-argv[0].u.i + 999) / 1000)); struct tm tm;
    localtime_r(&t, &tm); return mkint((int64_t)tm.tm_gmtoff);
}
/* 操作系统的密码学安全随机数（std.crand / std.uuid 用） */
B(b_random_bytes) {
    NARGS(1, 1); int64_t n = argv[0].u.i < 0 ? 0 : argv[0].u.i; KList *l = newlist(n);
    unsigned char *buf = malloc(n ? n : 1); int64_t got = 0;
    FILE *f = fopen("/dev/urandom", "rb");
    if (f) { got = (int64_t)fread(buf, 1, n, f); fclose(f); }
    if (got != n) die("__random_bytes: 读取 /dev/urandom 失败");
    for (int64_t i = 0; i < n; i++) l->a[i] = mkint(buf[i]);
    free(buf); l->len = n; return mkp(T_LIST, l);
}
B(b_unix_ns) {
    NARGS(0, 0); struct timespec ts; clock_gettime(CLOCK_REALTIME, &ts);
    return mkint((int64_t)ts.tv_sec * 1000000000 + ts.tv_nsec);
}
B(b_eprint) {
    NARGS(1, 1); fflush(stdout); KStr *s = STR(argv[0]);
    fwrite(s->s, 1, s->len, stderr); fputc('\n', stderr); fflush(stderr); return UNIT;
}
BF(b_tcp_close, "__tcp_close"); BF(b_byte_len, "__byte_len"); BF(b_bytes, "__bytes"); BF(b_from_bytes, "__from_bytes"); BF(b_eprint, "__eprint");
BF(b_fs_stat, "__fs_stat"); BF(b_getcwd, "__getcwd"); BF(b_chdir, "__chdir"); BF(b_chmod, "__chmod");
BF(b_env_set, "__env_set"); BF(b_env_unset, "__env_unset"); BF(b_environ, "__environ"); BF(b_hostname, "__hostname");
BF(b_getpid, "__getpid"); BF(b_read_bytes, "__read_bytes"); BF(b_write_bytes, "__write_bytes"); BF(b_tz_offset, "__tz_offset"); BF(b_random_bytes, "__random_bytes"); BF(b_unix_ns, "__unix_ns");
BF(b_tls_server_ctx, "__tls_server_ctx"); BF(b_tls_accept, "__tls_accept"); BF(b_tls_client, "__tls_client"); BF(b_tls_handshake, "__tls_handshake");

/* std.ptr：裸指针（运行时是地址整数），读写按字节偏移 */
static char *ptr_chk(V p, const char *what) { if (!p.u.i) die("ptr.%s: 空指针", what); return (char *)(intptr_t)p.u.i; }
B(b_ptr_null) { NARGS(0, 0); return mkint(0); }
B(b_ptr_is_null) { NARGS(1, 1); return mkbool(argv[0].u.i == 0); }
B(b_ptr_addr) { NARGS(1, 1); return mkint(argv[0].u.i); }
B(b_ptr_offset) { NARGS(2, 2); int64_t r; if (__builtin_add_overflow(argv[0].u.i, argv[1].u.i, &r)) die("整数溢出"); return mkint(r); }
B(b_ptr_from_addr) { NARGS(1, 1); return mkint(argv[0].u.i); }
B(b_ptr_alloc) {
    NARGS(1, 1); if (argv[0].u.i < 0) die("ptr.alloc: 大小不能是负数");
    size_t n = argv[0].u.i ? (size_t)argv[0].u.i : 1; void *p = calloc(1, n);
    if (!p) die("ptr.alloc: 内存不足"); return mkint((int64_t)(intptr_t)p);
}
B(b_ptr_free) { NARGS(1, 1); if (argv[0].u.i) free((void *)(intptr_t)argv[0].u.i); return UNIT; }
B(b_ptr_read_int) { NARGS(2, 2); int64_t v; memcpy(&v, ptr_chk(argv[0], "read_int") + argv[1].u.i, 8); return mkint(v); }
B(b_ptr_write_int) { NARGS(3, 3); int64_t v = argv[2].u.i; memcpy(ptr_chk(argv[0], "write_int") + argv[1].u.i, &v, 8); return UNIT; }
B(b_ptr_read_float) { NARGS(2, 2); double v; memcpy(&v, ptr_chk(argv[0], "read_float") + argv[1].u.i, 8); return mkflt(v); }
B(b_ptr_write_float) {
    NARGS(3, 3); double v = argv[2].t == T_INT ? (double)argv[2].u.i : argv[2].u.f;
    memcpy(ptr_chk(argv[0], "write_float") + argv[1].u.i, &v, 8); return UNIT;
}
B(b_ptr_read_byte) { NARGS(2, 2); return mkint((unsigned char)ptr_chk(argv[0], "read_byte")[argv[1].u.i]); }
B(b_ptr_write_byte) { NARGS(3, 3); ptr_chk(argv[0], "write_byte")[argv[1].u.i] = (char)(argv[2].u.i & 255); return UNIT; }
B(b_ptr_to_str) { NARGS(1, 1); return cstr(ptr_chk(argv[0], "to_str")); }
B(b_ptr_from_str) {
    NARGS(1, 1); KStr *k = STR(argv[0]); char *p = malloc(k->len + 1);
    if (!p) die("ptr.alloc: 内存不足"); memcpy(p, k->s, k->len); p[k->len] = 0; return mkint((int64_t)(intptr_t)p);
}
BF(b_ptr_null, "null"); BF(b_ptr_is_null, "is_null"); BF(b_ptr_addr, "addr"); BF(b_ptr_offset, "offset");
BF(b_ptr_from_addr, "from_addr"); BF(b_ptr_alloc, "alloc"); BF(b_ptr_free, "free"); BF(b_ptr_read_int, "read_int");
BF(b_ptr_write_int, "write_int"); BF(b_ptr_read_float, "read_float"); BF(b_ptr_write_float, "write_float");
BF(b_ptr_read_byte, "read_byte"); BF(b_ptr_write_byte, "write_byte"); BF(b_ptr_to_str, "to_str"); BF(b_ptr_from_str, "from_str");

static V module_get(const char *mod, const char *name) {
#define M(n, f) if (!strcmp(name, n)) return mkp(T_FN, &bf_##f)
    if (!strcmp(mod, "io")) { M("print", b_print); M("println", b_print); M("read_line", b_read_line); }
    else if (!strcmp(mod, "fs")) { M("read", b_fs_read); M("write", b_fs_write); M("append", b_fs_append); M("exists", b_fs_exists);
        M("is_file", b_fs_is_file); M("is_dir", b_fs_is_dir); M("size", b_fs_size); M("list", b_fs_list);
        M("mkdir", b_fs_mkdir); M("remove", b_fs_remove); M("rename", b_fs_rename); }
    else if (!strcmp(mod, "ptr")) { M("null", b_ptr_null); M("is_null", b_ptr_is_null); M("addr", b_ptr_addr);
        M("offset", b_ptr_offset); M("from_addr", b_ptr_from_addr); M("alloc", b_ptr_alloc); M("free", b_ptr_free);
        M("read_int", b_ptr_read_int); M("write_int", b_ptr_write_int); M("read_float", b_ptr_read_float);
        M("write_float", b_ptr_write_float); M("read_byte", b_ptr_read_byte); M("write_byte", b_ptr_write_byte);
        M("to_str", b_ptr_to_str); M("from_str", b_ptr_from_str); }
    else if (!strcmp(mod, "math")) {
        M("sqrt", b_sqrt); M("pow", b_pow); M("floor", b_floor); M("ceil", b_ceil);
        M("sin", b_sin); M("cos", b_cos); M("log", b_log);
        M("tan", b_m_tan); M("asin", b_m_asin); M("acos", b_m_acos); M("atan", b_m_atan); M("sinh", b_m_sinh);
        M("cosh", b_m_cosh); M("tanh", b_m_tanh); M("asinh", b_m_asinh); M("acosh", b_m_acosh); M("atanh", b_m_atanh);
        M("exp", b_m_exp); M("exp2", b_m_exp2); M("expm1", b_m_expm1); M("log2", b_m_log2); M("log10", b_m_log10);
        M("log1p", b_m_log1p); M("cbrt", b_m_cbrt); M("atan2", b_m_atan2); M("hypot", b_m_hypot); M("fmod", b_m_fmod);
        M("copysign", b_m_copysign); M("remainder", b_m_remainder); M("round", b_m_round); M("trunc", b_m_trunc);
        M("is_nan", b_m_is_nan); M("is_inf", b_m_is_inf); M("signbit", b_m_signbit); M("gcd", b_m_gcd); M("lcm", b_m_lcm);
        if (!strcmp(name, "pi")) return mkflt(M_PI);
        if (!strcmp(name, "e")) return mkflt(M_E);
        if (!strcmp(name, "tau")) return mkflt(2 * M_PI);
        if (!strcmp(name, "phi")) return mkflt((1 + sqrt(5.0)) / 2);
        if (!strcmp(name, "sqrt2")) return mkflt(sqrt(2.0));
        if (!strcmp(name, "ln2")) return mkflt(log(2.0));
        if (!strcmp(name, "ln10")) return mkflt(log(10.0));
        if (!strcmp(name, "inf")) return mkflt(INFINITY);
        if (!strcmp(name, "nan")) return mkflt(NAN);
        if (!strcmp(name, "max_float")) return mkflt(DBL_MAX);
        if (!strcmp(name, "smallest_float")) return mkflt(4.9406564584124654e-324);
        if (!strcmp(name, "max_int")) return mkint(INT64_MAX);
        if (!strcmp(name, "min_int")) return mkint(INT64_MIN);
    }
#undef M
    die("模块 %s 没有成员 %s", mod, name); return UNIT;
}

/* 主函数放到大栈线程里跑，深递归不会轻易爆栈 */
static void kite_main(void);
static void *kite_thread(void *arg) {
    (void)arg; volatile char base = 0; static Mutator m0;
    init_threads(); gc_register(&m0, (char *)&base + 64);
    kite_main(); gc_report(); return NULL;
}
int main(int argc, char **argv) {
    KARGC = argc; KARGV = argv;
    pthread_attr_t at; pthread_attr_init(&at);
    pthread_attr_setstacksize(&at, (size_t)1 << 30);
    pthread_t th;
    if (pthread_create(&th, &at, kite_thread, NULL) != 0) kite_thread(NULL);
    else pthread_join(th, NULL);
    fflush(stdout);
    return 0;
}
