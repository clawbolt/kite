"""PMap（Python 引擎的持久化 Map）随机对拍：任意旧版本在任意时刻读取都必须与当时的快照一致。"""
import os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kite import PMap

def main():
    rng = random.Random(7)
    for trial in range(30):
        versions = [(PMap(), {})]
        for step in range(400):
            m, ref = versions[rng.randrange(len(versions))]   # 从任意旧版本派生（分叉）
            k = rng.randrange(40)
            if rng.random() < 0.15:
                m2, r2 = m.remove(k), dict(ref); r2.pop(k, None)
            else:
                v = rng.randrange(1000); m2 = m.set(k, v); r2 = dict(ref); r2[k] = v
            versions.append((m2, r2))
            if step % 7 == 0:
                for m, ref in rng.sample(versions, min(5, len(versions))):
                    assert m.items() == list(ref.items()), (trial, step)
        for m, ref in versions:
            assert m.items() == list(ref.items()) and len(m) == len(ref)
    print("PASS test_pmap")

main()
