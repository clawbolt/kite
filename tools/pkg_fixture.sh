#!/bin/sh
# Build the package fixture for the kitec check (deps need a registry and kite.lock):
#   sh tools/pkg_fixture.sh DIR   → DIR/packages/{app,geom,mathx,util} + DIR/registry
# Afterwards: KITE_REGISTRY=DIR/registry python3 tools/kitec_tc_check.py KITEC DIR/packages/*/src/*.kite DIR/packages/*/tests/*.kite
set -e
K=$(cd "$(dirname "$0")/.." && pwd)
D=$1
rm -rf "$D" && mkdir -p "$D"
cp -r "$K/examples/packages" "$D/packages"
export KITE_REGISTRY="$D/registry"
(cd "$D/packages/mathx" && python3 "$K/kite.py" publish >/dev/null)
(cd "$D/packages/geom" && python3 "$K/kite.py" publish >/dev/null)
(cd "$D/packages/geom" && python3 "$K/kite.py" get >/dev/null)
(cd "$D/packages/app" && python3 "$K/kite.py" get >/dev/null)
echo "fixture: $D"
