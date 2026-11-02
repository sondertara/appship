#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

find src tests -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf dist
uv build
python3 -m zipapp src -p "/usr/bin/env python3" -o dist/appman.pyz
chmod +x dist/appman.pyz
cp scripts/install.sh dist/install.sh

(
    cd dist
    sha256sum appman.pyz ./*.whl ./*.tar.gz install.sh > SHA256SUMS
)

echo "Built:"
ls -lh dist
