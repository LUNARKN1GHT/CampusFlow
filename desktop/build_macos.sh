#!/usr/bin/env bash
# P002a：构建 macOS sidecar（PyInstaller one-folder）。
#
# 用法：desktop/build_macos.sh
# 环境变量：
#   CAMPUSFLOW_BUILD_PYTHON     构建解释器（默认 3.12，必须 3.12）
#   CAMPUSFLOW_BUILD_VENV       独立构建虚拟环境目录（默认 desktop/.build-venv）
#   CAMPUSFLOW_PYINSTALLER_SPEC PyInstaller 约束（默认 pyinstaller>=6.11,<8）
#
# 构建 venv 与 backend 的 uv 环境分开，产物只依赖 desktop/dist 自身。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP="$ROOT/desktop"
VENV="${CAMPUSFLOW_BUILD_VENV:-$DESKTOP/.build-venv}"
PYTHON_VERSION="${CAMPUSFLOW_BUILD_PYTHON:-3.12}"
PYINSTALLER_SPEC="${CAMPUSFLOW_PYINSTALLER_SPEC:-pyinstaller>=6.11,<8}"

if ! command -v uv >/dev/null 2>&1; then
  echo "未找到 uv；请先安装 uv 再构建。" >&2
  exit 1
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "== 创建构建虚拟环境 =="
  uv venv --python "$PYTHON_VERSION" "$VENV"
fi

echo "== 安装构建依赖（可编辑安装 backend）=="
uv pip install --python "$VENV/bin/python" --quiet -e "$ROOT/backend" "$PYINSTALLER_SPEC"

echo "== 构建环境 =="
"$VENV/bin/python" -c 'import platform, sys; print(f"Python {sys.version.split()[0]} ({platform.machine()})")'
"$VENV/bin/pyinstaller" --version
uv pip freeze --python "$VENV/bin/python" | grep -Ei '^(pyinstaller|pyinstaller-hooks-contrib)==' || true

echo "== 清理旧产物 =="
rm -rf "$DESKTOP/build" "$DESKTOP/dist"

echo "== PyInstaller one-folder =="
cd "$ROOT"
start_ns=$(python3 -c 'import time; print(time.monotonic_ns())')
"$VENV/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --log-level INFO \
  --distpath "$DESKTOP/dist" \
  --workpath "$DESKTOP/build" \
  "$DESKTOP/campusflow-sidecar.spec"
end_ns=$(python3 -c 'import time; print(time.monotonic_ns())')

python3 - "$start_ns" "$end_ns" <<'PY'
import sys
elapsed = (int(sys.argv[2]) - int(sys.argv[1])) / 1e9
print(f"== 构建耗时：{elapsed:.1f} 秒 ==")
PY

du -sh "$DESKTOP/dist/campusflow-sidecar"
echo "产物：$DESKTOP/dist/campusflow-sidecar/campusflow-sidecar"
