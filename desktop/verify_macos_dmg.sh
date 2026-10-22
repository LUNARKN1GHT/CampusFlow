#!/usr/bin/env bash
# 校验 macOS DMG 的资源完整性与架构；构建与发布工作流共用。
# 用法：verify_macos_dmg.sh <dmg> <arm64|x64>
set -euo pipefail

dmg=${1:?usage: verify_macos_dmg.sh <dmg> <arm64|x64>}
arch=${2:?usage: verify_macos_dmg.sh <dmg> <arm64|x64>}

case "$arch" in
  arm64) expected_macho='Mach-O 64-bit executable arm64' ;;
  x64) expected_macho='Mach-O 64-bit executable x86_64' ;;
  *) echo "unknown arch: $arch" >&2; exit 2 ;;
esac

case "$dmg" in
  *"mac-$arch.dmg") ;;
  *) echo "unexpected DMG for arch $arch: $dmg" >&2; exit 1 ;;
esac

mount=$(mktemp -d)
cleanup() {
  hdiutil detach "$mount" >/dev/null 2>&1 || true
  rmdir "$mount" 2>/dev/null || true
}
trap cleanup EXIT

hdiutil attach -readonly -nobrowse -mountpoint "$mount" "$dmg" >/dev/null

app="$mount/CampusFlow.app"
for relative in \
  Contents/Resources/sidecar/campusflow-sidecar \
  Contents/Resources/pgsql/bin/postgres \
  Contents/Resources/pgsql/share/extension/vector.control \
  Contents/Resources/pgsql/lib/vector.dylib \
  Contents/Resources/static/index.html \
  Contents/Resources/THIRD-PARTY-NOTICES.md; do
  if [ ! -e "$app/$relative" ]; then
    echo "missing from DMG: $relative" >&2
    exit 1
  fi
done

"$app/Contents/Resources/sidecar/campusflow-sidecar" --version
file "$app/Contents/MacOS/CampusFlow" | grep -q "$expected_macho"
file "$app/Contents/Resources/pgsql/bin/postgres" | grep -q "$expected_macho"
echo "DMG contents verified for $expected_macho"
