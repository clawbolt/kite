"""自举第 2 步对拍：kitec check 与 tools/lower_dump.py 的输出逐字节比较

    python3 tools/kitec_tc_check.py KITEC_BIN [文件…]     （默认 examples/、tests/、std/、示例包、kitec/src/ 下全部 .kite）

Python 版输出 SKIP 的文件（有语法错误）不计；kitec 输出 UNSUP 的文件计为“未支持”。
"""
import glob, os, subprocess, sys
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
kc = sys.argv[1]
files = sys.argv[2:] or sorted(glob.glob(os.path.join(root, "examples/*.kite")) + glob.glob(os.path.join(root, "tests/*.kite"))
                              + glob.glob(os.path.join(root, "std/**/*.kite"), recursive=True) + glob.glob(os.path.join(root, "examples/packages/*/src/*.kite"))
                              + glob.glob(os.path.join(root, "examples/packages/*/tests/*.kite")) + glob.glob(os.path.join(root, "kitec/src/*.kite")))
same = diff = skip = unsup = 0
for f in files:
    py = subprocess.run([sys.executable, os.path.join(root, "tools/lower_dump.py"), f], capture_output=True, text=True).stdout
    if py.startswith("SKIP"): skip += 1; continue
    kt = subprocess.run([kc, "lower", f, os.path.join(root, "prelude.kite")], capture_output=True, text=True, timeout=120).stdout
    if kt.startswith("UNSUP"):
        unsup += 1
        if "-v" in os.environ.get("TCV", ""): print(f"未支持 {os.path.relpath(f, root)}: {kt.strip()}")
        continue
    if kt == py: same += 1
    else:
        diff += 1
        print(f"不一致 {os.path.relpath(f, root)}")
        if os.environ.get("TCV"):
            pl, kl = py.splitlines(), kt.splitlines()
            shown = 0
            for i in range(max(len(pl), len(kl))):
                a = pl[i] if i < len(pl) else "<无>"; b = kl[i] if i < len(kl) else "<无>"
                if a == b: continue
                j = next((j for j in range(min(len(a), len(b))) if a[j] != b[j]), min(len(a), len(b)))
                print(f"    第 {i + 1} 行 @{j}\n      py: …{a[max(0, j - 120):j + 160]}\n      kc: …{b[max(0, j - 120):j + 160]}")
                shown += 1
                if shown >= int(os.environ.get("TCN", "2")): break
print(f"kitec lower 对拍：{same} 个一致，{diff} 个不一致，{unsup} 个用到未移植特性，{skip} 个有语法错误跳过")
sys.exit(1 if diff else 0)
