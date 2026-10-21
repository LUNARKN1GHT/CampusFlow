#!/usr/bin/env bash
# End-to-end smoke test for the staged macOS PostgreSQL 17 + pgvector runtime.
#
# The script always starts from a clean scratch directory:
#   - initdb with locale=C, UTF8 and SCRAM-SHA-256, using a random password that
#     is only written to a mode-0600 pwfile and never to logs/stdout;
#   - start on a random free port bound to 127.0.0.1 only;
#   - create a database, CREATE EXTENSION vector, build an HNSW index, run a
#     nearest-neighbour query and insert a persistence marker;
#   - stop, assert no postgres process for this data directory remains, start
#     again, verify the marker and the vector query, stop again and re-check.
#
# Usage:
#   desktop/postgres/smoke_macos.sh
#   CAMPUSFLOW_PG_HOME=/tmp/relocated/pgsql desktop/postgres/smoke_macos.sh
#
# Environment:
#   CAMPUSFLOW_PG_HOME  staged PostgreSQL root (default <repo>/desktop/vendor/pgsql)
#   CAMPUSFLOW_PG_SCRATCH  scratch root (default /tmp/campusflow-pg-smoke)
#
# The scratch directory is deleted at the start of every run. Logs are written
# under <scratch>/logs; the random database password is not.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PG_HOME="${CAMPUSFLOW_PG_HOME:-$REPO_ROOT/desktop/vendor/pgsql}"
SCRATCH="${CAMPUSFLOW_PG_SCRATCH:-/tmp/campusflow-pg-smoke}"
DATA_DIR="$SCRATCH/data"
LOG_DIR="$SCRATCH/logs"
SOCKET_DIR="$SCRATCH/socket"
PW_FILE="$SCRATCH/.initdb-pwfile"
DB_NAME="campusflow_smoke"
DB_USER="campusflow"
INDEX_NAME="smoke_items_embedding_hnsw"

PORT=""
DB_PASSWORD=""
SERVER_STARTED=0

log() {
  printf '[smoke] %s\n' "$*"
}

fail() {
  printf '[smoke] FAIL: %s\n' "$*" >&2
  if [[ -f "$LOG_DIR/postgres.log" ]]; then
    printf '[smoke] last 40 lines of %s:\n' "$LOG_DIR/postgres.log" >&2
    tail -n 40 "$LOG_DIR/postgres.log" >&2 || true
  fi
  exit 1
}

cleanup() {
  local rc=$?
  if [[ -f "$DATA_DIR/postmaster.pid" ]]; then
    "$PG_HOME/bin/pg_ctl" -D "$DATA_DIR" -m immediate -w -t 15 stop \
      >>"$LOG_DIR/cleanup.log" 2>&1 || true
  fi
  rm -f "$PW_FILE"
  if [[ "$rc" -ne 0 ]]; then
    printf '[smoke] scratch directory kept for inspection: %s\n' "$SCRATCH" >&2
  fi
  exit "$rc"
}
trap cleanup EXIT

psql_db() {
  local database="$1"
  shift
  "$PG_HOME/bin/psql" -X -q -v ON_ERROR_STOP=1 \
    -h 127.0.0.1 -p "$PORT" -U "$DB_USER" -d "$database" "$@"
}

query_value() {
  local database="$1"
  local sql="$2"
  psql_db "$database" -At -c "$sql"
}

pick_free_port() {
  python3 - <<'PY'
import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY
}

start_server() {
  local attempt
  for attempt in 1 2 3; do
    if "$PG_HOME/bin/pg_ctl" -D "$DATA_DIR" -l "$LOG_DIR/postgres.log" \
         -w -t 30 start >>"$LOG_DIR/pg_ctl.log" 2>&1; then
      SERVER_STARTED=1
      return 0
    fi
    log "start attempt $attempt failed (port $PORT), retrying with a new port"
    PORT="$(pick_free_port)"
    printf '\nport = %s\n' "$PORT" >>"$DATA_DIR/postgresql.conf"
  done
  return 1
}

stop_server() {
  "$PG_HOME/bin/pg_ctl" -D "$DATA_DIR" -m fast -w -t 30 stop \
    >>"$LOG_DIR/pg_ctl.log" 2>&1
  SERVER_STARTED=0
}

assert_loopback_only() {
  local listeners bad
  listeners="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true)"
  [[ -n "$listeners" ]] || fail "nothing is listening on port $PORT"
  printf '%s\n' "$listeners"
  printf '%s\n' "$listeners" | grep -q "127.0.0.1:$PORT" \
    || fail "postgres is not listening on 127.0.0.1:$PORT"
  bad="$(printf '%s\n' "$listeners" | grep -E "(\*|0\.0\.0\.0|\[::\]|::):$PORT" || true)"
  [[ -z "$bad" ]] || fail "port $PORT is bound beyond loopback: $bad"
}

assert_no_leftover_process() {
  local leftovers
  leftovers="$(ps -axo pid=,command= | grep -F -- "$DATA_DIR" | grep -v grep || true)"
  [[ -z "$leftovers" ]] || fail "leftover process for $DATA_DIR:\n$leftovers"
  if "$PG_HOME/bin/pg_ctl" -D "$DATA_DIR" status >/dev/null 2>&1; then
    fail "pg_ctl still reports a running server for $DATA_DIR"
  fi
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "port $PORT is still listening after stop"
  fi
}

main() {
  log "PostgreSQL root: $PG_HOME"
  [[ -x "$PG_HOME/bin/postgres" ]] || fail "postgres not found under $PG_HOME"
  [[ -x "$PG_HOME/bin/initdb" ]] || fail "initdb not found under $PG_HOME"
  [[ -x "$PG_HOME/bin/pg_ctl" ]] || fail "pg_ctl not found under $PG_HOME"
  [[ -x "$PG_HOME/bin/pg_isready" ]] || fail "pg_isready not found under $PG_HOME"
  [[ -x "$PG_HOME/bin/psql" ]] || fail "psql not found under $PG_HOME"
  command -v python3 >/dev/null || fail "python3 is required to pick a free port"
  command -v lsof >/dev/null || fail "lsof is required to inspect listeners"

  local server_version
  server_version="$("$PG_HOME/bin/postgres" --version)"
  log "$server_version"
  [[ "$server_version" == *" 17."* ]] || fail "expected PostgreSQL 17, got: $server_version"

  log "resetting scratch directory $SCRATCH"
  rm -rf "$SCRATCH"
  mkdir -p "$DATA_DIR" "$LOG_DIR" "$SOCKET_DIR"
  chmod 700 "$SCRATCH" "$DATA_DIR"

  DB_PASSWORD="$(openssl rand -base64 32 | tr -d '\n')"
  [[ -n "$DB_PASSWORD" ]] || fail "failed to generate database password"
  printf '%s\n' "$DB_PASSWORD" >"$PW_FILE"
  chmod 600 "$PW_FILE"
  export PGPASSWORD="$DB_PASSWORD"

  PORT="$(pick_free_port)"
  log "random loopback port: $PORT (password: redacted, stored only in a mode-600 pwfile)"

  log "initdb (locale=C, UTF8, SCRAM-SHA-256)"
  "$PG_HOME/bin/initdb" -D "$DATA_DIR" \
    --locale=C \
    --encoding=UTF8 \
    --auth=scram-sha-256 \
    --username="$DB_USER" \
    --pwfile="$PW_FILE" >>"$LOG_DIR/initdb.log" 2>&1 \
    || fail "initdb failed, see $LOG_DIR/initdb.log"
  rm -f "$PW_FILE"
  [[ "$(cat "$DATA_DIR/PG_VERSION")" == "17" ]] || fail "unexpected PG_VERSION"
  log "cluster initialized: $(cat "$DATA_DIR/PG_VERSION")"

  {
    printf '\n# smoke test settings\n'
    printf "port = %s\n" "$PORT"
    printf "listen_addresses = '127.0.0.1'\n"
    printf "unix_socket_directories = '%s'\n" "$SOCKET_DIR"
    printf 'log_min_messages = warning\n'
  } >>"$DATA_DIR/postgresql.conf"

  log "starting postgres"
  start_server || fail "pg_ctl start failed"
  [[ -f "$DATA_DIR/postmaster.pid" ]] || fail "postmaster.pid missing"
  local pid
  pid="$(head -n 1 "$DATA_DIR/postmaster.pid")"
  kill -0 "$pid" 2>/dev/null || fail "PID $pid from postmaster.pid is not alive"
  "$PG_HOME/bin/pg_isready" -h 127.0.0.1 -p "$PORT" -t 15 -q \
    || fail "pg_isready failed"
  log "pg_isready OK (pid $pid in postmaster.pid)"
  assert_loopback_only

  local server_encoding lc_collate listen_setting effective_port
  server_encoding="$(query_value postgres 'SHOW server_encoding')"
  lc_collate="$(query_value postgres "SELECT datcollate FROM pg_database WHERE datname = 'postgres'")"
  listen_setting="$(query_value postgres 'SHOW listen_addresses')"
  effective_port="$(query_value postgres 'SHOW port')"
  [[ "$server_encoding" == "UTF8" ]] || fail "expected UTF8, got $server_encoding"
  [[ "$lc_collate" == "C" ]] || fail "expected lc_collate=C, got $lc_collate"
  [[ "$listen_setting" == "127.0.0.1" ]] || fail "listen_addresses is '$listen_setting'"
  [[ "$effective_port" == "$PORT" ]] || fail "server port is $effective_port, expected $PORT"
  grep -q 'scram-sha-256' "$DATA_DIR/pg_hba.conf" || fail "pg_hba.conf has no SCRAM rule"
  log "settings OK (server_encoding=$server_encoding, lc_collate=$lc_collate, listen_addresses=$listen_setting, port=$effective_port, hba=scram-sha-256)"

  log "creating database $DB_NAME and vector extension"
  "$PG_HOME/bin/createdb" -h 127.0.0.1 -p "$PORT" -U "$DB_USER" "$DB_NAME"
  psql_db "$DB_NAME" -c "CREATE EXTENSION vector"
  local ext_version
  ext_version="$(query_value "$DB_NAME" "SELECT extversion FROM pg_extension WHERE extname = 'vector'")"
  [[ -n "$ext_version" ]] || fail "vector extension is not installed"
  log "CREATE EXTENSION vector OK (extversion $ext_version)"

  log "creating vector(3) table and HNSW index"
  psql_db "$DB_NAME" <<'SQL'
CREATE TABLE smoke_items (
    id bigserial PRIMARY KEY,
    label text NOT NULL,
    embedding vector(3) NOT NULL
);
INSERT INTO smoke_items (label, embedding) VALUES
    ('near', '[1,2,3]'),
    ('far', '[10,20,30]'),
    ('persisted-after-restart', '[9,9,9]');
CREATE INDEX smoke_items_embedding_hnsw
    ON smoke_items USING hnsw (embedding vector_l2_ops);
SQL
  local index_def
  index_def="$(query_value "$DB_NAME" "SELECT indexdef FROM pg_indexes WHERE indexname = '$INDEX_NAME'")"
  [[ "$index_def" == *"USING hnsw"* ]] || fail "HNSW index not found: $index_def"
  log "HNSW index present: $index_def"

  local nearest plan
  nearest="$(query_value "$DB_NAME" "SELECT label FROM smoke_items ORDER BY embedding <-> '[1,2,4]' LIMIT 1")"
  [[ "$nearest" == "near" ]] || fail "nearest-neighbour query returned '$nearest'"
  log "nearest-neighbour query OK (nearest label: $nearest)"
  plan="$(query_value "$DB_NAME" "SET enable_seqscan = off; EXPLAIN (COSTS OFF) SELECT label FROM smoke_items ORDER BY embedding <-> '[1,2,4]' LIMIT 1")"
  printf '%s\n' "$plan" | grep -q "$INDEX_NAME" \
    || fail "planner did not use the HNSW index:\n$plan"
  log "EXPLAIN uses $INDEX_NAME"

  log "stopping postgres and checking for leftover processes"
  stop_server
  assert_no_leftover_process
  log "no postgres process remains for $DATA_DIR"

  log "restarting on the same port"
  start_server || fail "restart failed"
  "$PG_HOME/bin/pg_isready" -h 127.0.0.1 -p "$PORT" -t 15 -q \
    || fail "pg_isready failed after restart"
  assert_loopback_only

  local count marker ext_after
  count="$(query_value "$DB_NAME" "SELECT count(*) FROM smoke_items")"
  marker="$(query_value "$DB_NAME" "SELECT label FROM smoke_items WHERE id = 3")"
  ext_after="$(query_value "$DB_NAME" "SELECT extversion FROM pg_extension WHERE extname = 'vector'")"
  [[ "$count" == "3" ]] || fail "expected 3 rows after restart, got $count"
  [[ "$marker" == "persisted-after-restart" ]] || fail "marker row missing after restart"
  [[ "$ext_after" == "$ext_version" ]] || fail "vector extension version changed after restart"
  nearest="$(query_value "$DB_NAME" "SELECT label FROM smoke_items ORDER BY embedding <-> '[1,2,4]' LIMIT 1")"
  [[ "$nearest" == "near" ]] || fail "nearest-neighbour query failed after restart"
  log "restart persistence OK (rows=$count, marker=$marker, extversion=$ext_after, nearest=$nearest)"

  log "stopping postgres (final)"
  stop_server
  assert_no_leftover_process
  if grep -rF -- "$DB_PASSWORD" "$LOG_DIR" >/dev/null 2>&1; then
    fail "database password leaked into $LOG_DIR"
  fi
  unset PGPASSWORD
  log "password audit OK: random password does not appear in logs"

  log "PASS: initdb, loopback-only start, vector + HNSW, restart persistence, clean stop"
  log "logs: $LOG_DIR"
  log "data: $DATA_DIR"
}

main "$@"
