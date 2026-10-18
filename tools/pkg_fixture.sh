#!/bin/sh
# 搭一个有依赖的包示例（依赖需要仓库和 kite.lock）：
#   sh tools/pkg_fixture.sh DIR [KITEC]   → DIR/packages/{app,geom,mathx,util} + DIR/registry
# 之后：KITE_REGISTRY=DIR/registry KITEC check DIR/packages/app/src/main.kite
set -e
K=$(cd "$(dirname "$0")/.." && pwd)
D=$1
KC=${2:-$K/bin/kitec}
export KITE_HOME=${KITE_HOME:-$K}
rm -rf "$D" && mkdir -p "$D"
cp -r "$K/examples/packages" "$D/packages"
export KITE_REGISTRY="$D/registry"
(cd "$D/packages/mathx" && "$KC" publish >/dev/null)
(cd "$D/packages/geom" && "$KC" publish >/dev/null)
(cd "$D/packages/geom" && "$KC" get >/dev/null)
(cd "$D/packages/app" && "$KC" get >/dev/null)
echo "fixture: $D"
