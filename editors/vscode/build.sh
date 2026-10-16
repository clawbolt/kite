#!/bin/sh
# 把语言服务器打包进扩展并生成 .vsix
cd "$(dirname "$0")"
mkdir -p server && cp ../../kite.py ../../typecheck.py ../../vm.py ../../fmt.py ../../lsp.py ../../modules.py ../../pkg.py ../../prelude.kite server/ && rm -rf server/std && cp -r ../../std server/
npm install --silent --no-audit --no-fund
npx --yes @vscode/vsce package --allow-missing-repository
