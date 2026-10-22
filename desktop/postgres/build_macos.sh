#!/usr/bin/env bash
# Build a self-contained, relocatable PostgreSQL 17 + pgvector runtime for the
# macOS desktop app.
#
# The build does not use Homebrew or a system PostgreSQL installation. It
# downloads the official PostgreSQL source tarball and the pgvector release
# tarball, verifies their SHA-256, compiles both against a build-only prefix,
# stages the result under desktop/vendor/pgsql, prunes developer files, and
# rewrites every bundled Mach-O install name to @loader_path so the staged
# directory can be copied anywhere before packaging.
#
# Usage:
#   desktop/postgres/build_macos.sh
#   CAMPUSFLOW_DESKTOP_VENDOR=/tmp/vendor desktop/postgres/build_macos.sh
#
# Environment overrides:
#   CAMPUSFLOW_DESKTOP_VENDOR  vendor root (default: <repo>/desktop/vendor)
#   CAMPUSFLOW_PG_URL          PostgreSQL source URL (default: official ftp)
#   CAMPUSFLOW_VECTOR_URL      pgvector source URL (default: GitHub release)
#   CAMPUSFLOW_BUILD_JOBS      parallel make jobs (default: hw.ncpu)
#
# The script is destructive only inside the vendor directory: it deletes and
# recreates <vendor>/pgsql and <vendor>/build.
set -euo pipefail
umask 022

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENDOR_DIR="${CAMPUSFLOW_DESKTOP_VENDOR:-$REPO_ROOT/desktop/vendor}"
STAGE_DIR="$VENDOR_DIR/pgsql"
BUILD_DIR="$VENDOR_DIR/build"
DOWNLOAD_DIR="$BUILD_DIR/downloads"
SOURCE_DIR="$BUILD_DIR/src"
DESTDIR_ROOT="$BUILD_DIR/root"
LOG_DIR="$BUILD_DIR/logs"

# Build-only prefix. It intentionally differs from the final staging path so the
# relocation step cannot accidentally rely on the directory it was built in.
BUILD_PREFIX="/opt/campusflow/pgsql"

PG_VERSION="17.11"
PG_SHA256="dd27f2b3c59e73ed14aa3324901242bf69a032a6347805f274e6260322d42979"
PG_URL="${CAMPUSFLOW_PG_URL:-https://ftp.postgresql.org/pub/source/v${PG_VERSION}/postgresql-${PG_VERSION}.tar.bz2}"

VECTOR_VERSION="0.8.7"
VECTOR_SHA256="cac0b10c360f05b2d521200105ba3697e773d4cd3731f5a915a7e37ebe0bea85"
VECTOR_URL="${CAMPUSFLOW_VECTOR_URL:-https://github.com/pgvector/pgvector/archive/refs/tags/v${VECTOR_VERSION}.tar.gz}"

JOBS="${CAMPUSFLOW_BUILD_JOBS:-$(sysctl -n hw.ncpu)}"
ARCH="$(uname -m)"
MACOS_VERSION="$(sw_vers -productVersion)"
CLANG_VERSION="$(clang --version | head -n 1)"

# Runtime binaries kept in the staged tree. Everything else in bin/ is removed.
KEPT_BINARIES="
clusterdb
createdb
dropdb
initdb
pg_basebackup
pg_checksums
pg_ctl
pg_dump
pg_dumpall
pg_isready
pg_resetwal
pg_restore
pg_rewind
pg_upgrade
pg_verifybackup
pg_waldump
postgres
psql
reindexdb
vacuumdb
"

log() {
  printf '[build] %s\n' "$*"
}

fail() {
  printf '[build] ERROR: %s\n' "$*" >&2
  exit 1
}

run_logged() {
  local name="$1"
  shift
  local log_file="$LOG_DIR/$name.log"
  if ! "$@" >"$log_file" 2>&1; then
    printf '[build] command failed (%s), last 60 log lines:\n' "$name" >&2
    tail -n 60 "$log_file" >&2 || true
    exit 1
  fi
}

download_verified() {
  local url="$1"
  local target="$2"
  local expected="$3"
  mkdir -p "$DOWNLOAD_DIR"
  if [[ ! -f "$target" ]]; then
    log "downloading $(basename "$target")"
    curl -fL --retry 3 --connect-timeout 20 -o "$target.part" "$url"
    mv "$target.part" "$target"
  else
    log "using cached $(basename "$target")"
  fi
  printf '%s  %s\n' "$expected" "$target" | shasum -a 256 -c - >/dev/null \
    || fail "SHA-256 mismatch for $target (delete the cached file to retry)"
}

# Rewrite install names of every bundled Mach-O so no reference points into the
# build prefix. References become @loader_path-relative; dylib IDs become
# @rpath-relative. Modified files are re-signed ad hoc because arm64 macOS
# refuses to execute binaries whose code signature no longer matches.
relocate_machos() {
  local rewritten=0
  local file dep base new_ref
  local -a machos

  machos=()
  while IFS= read -r file; do
    if file "$file" | grep -q 'Mach-O'; then
      machos+=("$file")
    fi
  done < <(find "$STAGE_DIR/bin" "$STAGE_DIR/lib" -type f)

  if [[ ${#machos[@]} -eq 0 ]]; then
    fail "no Mach-O files found under $STAGE_DIR"
  fi

  for file in "${machos[@]}"; do
    local changed=0
    local file_dir
    file_dir="$(dirname "$file")"
    while IFS= read -r dep; do
      [[ -z "$dep" ]] && continue
      case "$dep" in
        "$BUILD_PREFIX"/*)
          base="$(basename "$dep")"
          if [[ "$file_dir" == "$STAGE_DIR/bin" ]]; then
            new_ref="@loader_path/../lib/$base"
          elif [[ "$file_dir" == "$STAGE_DIR/lib/postgresql" ]]; then
            new_ref="@loader_path/../$base"
          elif [[ "$file_dir" == "$STAGE_DIR/lib" ]]; then
            new_ref="@loader_path/$base"
          else
            fail "unexpected Mach-O location: $file"
          fi
          install_name_tool -change "$dep" "$new_ref" "$file" >&2
          changed=1
          ;;
      esac
    done < <(otool -L "$file" | tail -n +2 | awk '{print $1}')

    if [[ "$file_dir" == "$STAGE_DIR/lib" && "$file" == *.dylib ]]; then
      install_name_tool -id "@rpath/$(basename "$file")" "$file" >&2
      changed=1
    fi

    if [[ "$changed" -eq 1 ]]; then
      codesign --force --sign - "$file" >/dev/null 2>&1 \
        || fail "ad-hoc codesign failed for $file"
      rewritten=$((rewritten + 1))
    fi
  done

  # No remaining Mach-O may reference the build prefix or the staging path.
  for file in "${machos[@]}"; do
    if otool -L "$file" | tail -n +2 | awk '{print $1}' \
      | grep -E "^($BUILD_PREFIX|$STAGE_DIR)/" >/dev/null; then
      fail "unrewritten install name in $file"
    fi
  done
  printf '%s' "$rewritten"
}

prune_stage() {
  log "pruning development files"
  rm -rf "$STAGE_DIR/include" \
         "$STAGE_DIR/lib/pkgconfig" \
         "$STAGE_DIR/lib/pgxs" \
         "$STAGE_DIR/share/man" \
         "$STAGE_DIR/share/doc" \
         "$STAGE_DIR/share/info"
  find "$STAGE_DIR" -name '*.a' -type f -delete
  rm -f "$STAGE_DIR"/lib/libecpg* \
        "$STAGE_DIR"/lib/libpgtypes* \
        "$STAGE_DIR"/lib/libpq.a \
        "$STAGE_DIR"/bin/ecpg \
        "$STAGE_DIR"/bin/pg_config

  local path name keep
  for path in "$STAGE_DIR"/bin/*; do
    [[ -e "$path" ]] || continue
    name="$(basename "$path")"
    keep=0
    for kept in $KEPT_BINARIES; do
      if [[ "$name" == "$kept" ]]; then
        keep=1
        break
      fi
    done
    if [[ "$keep" -eq 0 ]]; then
      rm -f "$path"
    fi
  done
}

write_versions() {
  local size rewritten tmp
  size="$(du -sh "$STAGE_DIR" | awk '{print $1}')"
  rewritten="$1"
  tmp="$SCRIPT_DIR/VERSIONS.md"
  {
    cat <<EOF
# Bundled PostgreSQL + pgvector (macOS)

This file is generated by \`desktop/postgres/build_macos.sh\`; re-run the script
instead of editing numbers by hand. The runtime is staged in
\`desktop/vendor/pgsql\` (gitignored) and is not committed.

## Versions and sources

| Component | Version | Source | SHA-256 |
| --- | --- | --- | --- |
| PostgreSQL | ${PG_VERSION} | ${PG_URL} | \`${PG_SHA256}\` |
| pgvector | ${VECTOR_VERSION} | ${VECTOR_URL} | \`${VECTOR_SHA256}\` |

Build host: macOS ${MACOS_VERSION}, ${ARCH}, ${CLANG_VERSION}.
Build-only prefix: \`${BUILD_PREFIX}\` (not present at runtime).
Staged tree size: ${size}. Mach-O files rewritten for relocation: ${rewritten}.

Bundled integrity hashes (SHA-256):

\`\`\`
EOF
    (cd "$STAGE_DIR" && shasum -a 256 bin/postgres bin/initdb bin/pg_ctl bin/pg_isready \
      bin/psql bin/pg_dump bin/pg_restore lib/vector.dylib)
    cat <<EOF
\`\`\`

Required executables kept: postgres, initdb, pg_ctl, pg_isready, psql,
pg_dump, pg_restore, createdb/dropdb, pg_dumpall, pg_basebackup, pg_upgrade,
pg_checksums, pg_resetwal, pg_rewind, pg_verifybackup, pg_waldump, reindexdb,
vacuumdb, clusterdb. Development files (headers, static libraries, pg_config,
ecpg, man pages, pkg-config files) are removed.

## Licenses

- PostgreSQL: PostgreSQL License. Full text: \`share/licenses/postgresql-COPYRIGHT\`.
- pgvector: PostgreSQL License. Full text: \`share/licenses/pgvector-LICENSE\`.

Both licenses permit redistribution of source and binaries with the license
text; the staged tree keeps the license files next to the binaries.

## Relocation

The tree is relocatable on macOS: every bundled dylib dependency is rewritten
to \`@loader_path\` and dylib IDs to \`@rpath\`, then binaries are re-signed ad
hoc. PostgreSQL resolves \`share/\` relative to the executable path, so copying
\`desktop/vendor/pgsql\` into an app bundle keeps working without Homebrew.
Signing, notarization and the installer layout are handled by P009.
EOF
  } >"$tmp"
}

main() {
  log "repository: $REPO_ROOT"
  log "vendor: $VENDOR_DIR"
  [[ "$ARCH" == "arm64" || "$ARCH" == "x86_64" ]] || fail "unsupported architecture: $ARCH"
  command -v curl >/dev/null || fail "curl is required"
  command -v clang >/dev/null || fail "Xcode command line tools (clang) are required"
  command -v codesign >/dev/null || fail "codesign is required"
  command -v install_name_tool >/dev/null || fail "install_name_tool is required"

  mkdir -p "$BUILD_DIR" "$LOG_DIR" "$DOWNLOAD_DIR"
  local pg_archive="$DOWNLOAD_DIR/postgresql-${PG_VERSION}.tar.bz2"
  local vector_archive="$DOWNLOAD_DIR/pgvector-${VECTOR_VERSION}.tar.gz"
  download_verified "$PG_URL" "$pg_archive" "$PG_SHA256"
  download_verified "$VECTOR_URL" "$vector_archive" "$VECTOR_SHA256"

  log "cleaning previous build and staging output"
  rm -rf "$SOURCE_DIR" "$DESTDIR_ROOT" "$STAGE_DIR"
  mkdir -p "$SOURCE_DIR"
  tar -xjf "$pg_archive" -C "$SOURCE_DIR"
  tar -xzf "$vector_archive" -C "$SOURCE_DIR"
  local pg_src="$SOURCE_DIR/postgresql-${PG_VERSION}"
  local vector_src="$SOURCE_DIR/pgvector-${VECTOR_VERSION}"
  [[ -d "$pg_src" ]] || fail "unexpected PostgreSQL archive layout"
  [[ -d "$vector_src" ]] || fail "unexpected pgvector archive layout"

  log "configuring PostgreSQL ${PG_VERSION} (prefix ${BUILD_PREFIX})"
  pushd "$pg_src" >/dev/null
  # OpenSSL, zlib, ICU and readline are optional for a loopback-only embedded
  # server; disabling them keeps the runtime free of Homebrew dylibs. SCRAM
  # authentication is implemented inside PostgreSQL and does not need OpenSSL.
  run_logged postgres-configure ./configure \
    --prefix="$BUILD_PREFIX" \
    --without-readline \
    --without-zlib \
    --without-openssl \
    --without-icu
  log "building PostgreSQL with ${JOBS} jobs"
  run_logged postgres-make make -j"$JOBS"
  run_logged postgres-install make install DESTDIR="$DESTDIR_ROOT"
  popd >/dev/null

  local staged_pg="$DESTDIR_ROOT$BUILD_PREFIX"
  [[ -x "$staged_pg/bin/pg_config" ]] || fail "pg_config missing from staged PostgreSQL"

  log "building pgvector ${VECTOR_VERSION} against staged PostgreSQL headers"
  pushd "$vector_src" >/dev/null
  run_logged pgvector-make make PG_CONFIG="$staged_pg/bin/pg_config" -j"$JOBS"
  # pg_config relocates its reported prefix to its own location, which is
  # already inside $DESTDIR_ROOT, so pgvector must not receive DESTDIR again.
  run_logged pgvector-install make install PG_CONFIG="$staged_pg/bin/pg_config"
  popd >/dev/null

  log "moving staged tree to $STAGE_DIR"
  mkdir -p "$VENDOR_DIR"
  mv "$staged_pg" "$STAGE_DIR"
  rm -rf "$DESTDIR_ROOT"

  prune_stage

  log "copying license files"
  mkdir -p "$STAGE_DIR/share/licenses"
  cp "$pg_src/COPYRIGHT" "$STAGE_DIR/share/licenses/postgresql-COPYRIGHT"
  cp "$vector_src/LICENSE" "$STAGE_DIR/share/licenses/pgvector-LICENSE"

  log "rewriting Mach-O install names for relocation"
  local rewritten
  rewritten="$(relocate_machos)"

  log "verifying staged binaries"
  local version_output
  version_output="$("$STAGE_DIR/bin/postgres" --version)"
  [[ "$version_output" == *"$PG_VERSION"* ]] || fail "unexpected postgres version: $version_output"
  "$STAGE_DIR/bin/pg_isready" --version >/dev/null
  [[ -f "$STAGE_DIR/share/extension/vector.control" ]] || fail "vector.control missing"
  [[ -f "$STAGE_DIR/lib/vector.dylib" ]] || fail "lib/vector.dylib missing"

  write_versions "$rewritten"

  log "done: $version_output"
  log "pgvector extension files: share/extension/vector* and lib/vector.dylib"
  log "next: desktop/postgres/smoke_macos.sh"
}

main "$@"
