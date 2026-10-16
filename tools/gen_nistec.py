# 生成 std/crypto/internal/nistec.kite 里的 Montgomery 乘法（26 位分量，直线代码）
curves = {
    'p256': (2**256 - 2**224 + 2**192 + 2**96 - 1, 11),
    'p384': (2**384 - 2**128 - 2**96 + 2**32 - 1, 16),
    'p521': (2**521 - 1, 22),
    'p224': (2**224 - 2**96 + 1, 10),
}
M = 26
MASK = (1 << M) - 1

def limbs(x, n):
    return [(x >> (M * i)) & MASK for i in range(n)]

out = []
for name, (p, n) in curves.items():
    pl = limbs(p, n)
    pinv = (-pow(p, -1, 1 << M)) % (1 << M)
    L = [f'// {name}：{n} 个 26 位分量，R = 2^{M * n}', f'fn {name}_mul(a: List<Int>, b: List<Int>) -> List<Int> {{']
    for i in range(n):
        L.append(f'  let a{i} = a[{i}]')
    for i in range(n):
        L.append(f'  let b{i} = b[{i}]')
    for k in range(2 * n):
        terms = [f'a{i} * b{k - i}' for i in range(n) if 0 <= k - i < n]
        L.append(f'  var t{k} = ' + (' + '.join(terms) if terms else '0'))
    L.append('  var m = 0')
    for i in range(n):
        L.append(f'  m = ((t{i} & {MASK}) * {pinv}) & {MASK}')
        for j in range(n):
            if pl[j] == 0:
                continue
            if pl[j] == 1:
                L.append(f'  t{i + j} += m')
            else:
                L.append(f'  t{i + j} += m * {pl[j]}')
        L.append(f'  t{i + 1} += t{i} >> {M}')
    # 规范化高半部分
    for j in range(n, 2 * n - 1):
        L.append(f'  t{j + 1} += t{j} >> {M}')
        L.append(f'  t{j} = t{j} & {MASK}')
    L.append('  [' + ', '.join(f't{j}' for j in range(n, 2 * n)) + ']')
    L.append('}')
    out.append('\n'.join(L))
open('/tmp/a/mont.kite', 'w').write('\n\n'.join(out) + '\n')
