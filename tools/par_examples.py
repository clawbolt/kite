#!/usr/bin/env python3
"""并行跑 run_tests.sh 前两轮（VM 与树解释器一致、原生与 VM 一致 + GC 压力），用法：python3 tools/par_examples.py [并发数] [第几份/共几份，如 0/2]"""
import glob, os, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
def run(cmd, env=None):
    r = subprocess.run(cmd, capture_output=True, env=env)
    return r.returncode, r.stdout + r.stderr
def one(f):
    out = []
    if not any(k in f for k in ('bench', 'gc_churn')):
        a, va = run(['python3', 'kite.py', f]); b, tr = run(['python3', 'kite.py', '--tree', f])
        out.append(('PASS %s (VM 与解释器输出一致)' if a == 0 and b == 0 and va == tr else 'FAIL %s') % f)
    if not any(k in f for k in ('bench_native', 'bench_parallel', 'gc_churn')):
        d = tempfile.mkdtemp(); exe = os.path.join(d, 'bin')
        ok = False
        c, err = run(['python3', 'kite.py', 'build', f, '-o', exe])
        if c == 0:
            n, nat = run([exe]); v, vm = run(['python3', 'kite.py', f])
            if n == 0 and v == 0 and nat == vm:
                s, st = run([exe], dict(os.environ, KITE_GC_STRESS='1'))
                ok = s == 0 and st == vm
        out.append(('PASS %s (原生可执行文件与虚拟机输出一致，GC 压力模式一致)' if ok else 'FAIL %s (原生)') % f)
    return out
files = sorted(glob.glob('examples/*.kite'))
if len(sys.argv) > 2:
    i, k = map(int, sys.argv[2].split('/')); files = files[i::k]
with ThreadPoolExecutor(int(sys.argv[1]) if len(sys.argv) > 1 else 4) as ex:
    res = list(ex.map(one, files))
lines = [l for r in res for l in r]
print('\n'.join(lines))
sys.exit(1 if any(l.startswith('FAIL') for l in lines) else 0)
