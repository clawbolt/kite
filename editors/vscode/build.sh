#!/bin/sh
# 把语言服务器打包进扩展并生成 .vsix
cd "$(dirname "$0")"
# 服务器：kitec（先在仓库根目录 sh bootstrap.sh 生成 bin/kitec）+ prelude.kite + runtime/ + std/（kitec 从自身位置往上找它们）
[ -x ../../bin/kitec ] || { echo "缺少 bin/kitec：先在仓库根目录运行 sh bootstrap.sh"; exit 1; }
rm -rf server && mkdir -p server/bin && cp ../../bin/kitec server/bin/ && cp ../../prelude.kite server/ && cp -r ../../runtime ../../std server/
npm install --silent --no-audit --no-fund
npx --yes @vscode/vsce package --allow-missing-repository
