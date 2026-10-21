# End-to-end smoke test for the staged Windows PostgreSQL 17 + pgvector runtime.
#
# The script always starts from a clean scratch directory under %TEMP%:
#   - initdb with locale=C, UTF8 and SCRAM-SHA-256, using a random password that
#     is only written to a password file and never logged;
#   - start on a loopback-only port;
#   - create a database, CREATE EXTENSION vector, build an HNSW index, run a
#     nearest-neighbour query and insert rows;
#   - stop, assert no server remains for this data directory, start again and
#     verify the rows and the vector query survive the restart;
#   - run the PyInstaller sidecar with --version when it has been built.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File desktop/postgres/smoke_windows.ps1
#   powershell -ExecutionPolicy Bypass -File desktop/postgres/smoke_windows.ps1 `
#     -PgHome D:\relocated\pgsql -Port 55441
#
# Parameters:
#   -PgHome   staged PostgreSQL root (default <repo>\desktop\vendor\pgsql)
#   -Sidecar  sidecar executable (default <repo>\desktop\dist\campusflow-sidecar\campusflow-sidecar.exe)
#   -Port     loopback port (default 55440)
#   -KeepScratch  keep the scratch directory for inspection instead of deleting it
#
# The scratch directory is deleted at the start of every run. The random
# database password is not written to stdout or to the PostgreSQL log.
[CmdletBinding()]
param(
    [string]$PgHome,
    [string]$Sidecar,
    [int]$Port = 55440,
    [switch]$KeepScratch
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $PgHome) { $PgHome = Join-Path $repoRoot 'desktop\vendor\pgsql' }
if (-not $Sidecar) { $Sidecar = Join-Path $repoRoot 'desktop\dist\campusflow-sidecar\campusflow-sidecar.exe' }

$scratch = Join-Path $env:TEMP 'campusflow-pg-smoke'
$dataDir = Join-Path $scratch 'data'
$logFile = Join-Path $scratch 'postgres.log'
$pwFile = Join-Path $scratch 'initdb-pwfile'
$dbName = 'campusflow_smoke'
$dbUser = 'campusflow'
$serverStarted = $false

function Write-Log([string]$Message) {
    Write-Host "[smoke] $Message"
}

function Fail([string]$Message) {
    [Console]::Error.WriteLine("[smoke] FAIL: $Message")
    if (Test-Path $logFile) {
        [Console]::Error.WriteLine("[smoke] last 40 lines of $logFile")
        Get-Content $logFile -Tail 40 | ForEach-Object { [Console]::Error.WriteLine($_) }
    }
    exit 1
}

function Get-Scalar([string]$Sql) {
    $value = & "$PgHome\bin\psql.exe" -h 127.0.0.1 -p $Port -U $dbUser -d $dbName `
        -t -A -v ON_ERROR_STOP=1 -c $Sql
    if ($LASTEXITCODE -ne 0) { Fail "query failed: $Sql" }
    return ($value -join "`n").Trim()
}

$initdb = Join-Path $PgHome 'bin\initdb.exe'
$pgCtl = Join-Path $PgHome 'bin\pg_ctl.exe'
$psql = Join-Path $PgHome 'bin\psql.exe'
foreach ($binary in @($initdb, $pgCtl, $psql)) {
    if (-not (Test-Path $binary)) { Fail "missing PostgreSQL binary: $binary" }
}

try {
    if (Test-Path $scratch) { Remove-Item -Recurse -Force $scratch }
    New-Item -ItemType Directory -Force -Path $scratch | Out-Null

    $alphabet = [char[]]((48..57) + (65..90) + (97..122))
    $password = -join (1..24 | ForEach-Object { Get-Random -InputObject $alphabet })
    Set-Content -Path $pwFile -Value $password -NoNewline -Encoding ASCII

    Write-Log "initdb (SCRAM-SHA-256, UTF8, locale=C)"
    & $initdb -D $dataDir -U $dbUser "--pwfile=$pwFile" -A scram-sha-256 -E UTF8 --locale=C
    if ($LASTEXITCODE -ne 0) { Fail "initdb failed with exit code $LASTEXITCODE" }

    Write-Log "starting PostgreSQL on 127.0.0.1:$Port"
    & $pgCtl -D $dataDir -l $logFile -o "-p $Port -h 127.0.0.1" -w -t 60 start
    if ($LASTEXITCODE -ne 0) { Fail "pg_ctl start failed with exit code $LASTEXITCODE" }
    $serverStarted = $true

    $env:PGPASSWORD = $password
    & $psql -h 127.0.0.1 -p $Port -U $dbUser -d postgres -v ON_ERROR_STOP=1 `
        -c "CREATE DATABASE $dbName"
    if ($LASTEXITCODE -ne 0) { Fail 'CREATE DATABASE failed' }

    Write-Log 'creating vector extension, HNSW index and sample rows'
    & $psql -h 127.0.0.1 -p $Port -U $dbUser -d $dbName -v ON_ERROR_STOP=1 `
        -c 'CREATE EXTENSION vector' `
        -c 'CREATE TABLE smoke_items (id bigserial primary key, label text, embedding vector(3))' `
        -c 'CREATE INDEX smoke_items_embedding_hnsw ON smoke_items USING hnsw (embedding vector_l2_ops)' `
        -c "INSERT INTO smoke_items (label, embedding) VALUES ('one', '[1,2,3]'), ('two', '[4,5,6]')"
    if ($LASTEXITCODE -ne 0) { Fail 'pgvector setup failed' }

    $nearest = Get-Scalar "SELECT label FROM smoke_items ORDER BY embedding <-> '[3,1,2]' LIMIT 1"
    if ($nearest -ne 'one') { Fail "unexpected nearest neighbour: '$nearest'" }
    Write-Log "nearest neighbour query: $nearest"

    Write-Log 'stopping PostgreSQL'
    & $pgCtl -D $dataDir -m fast -w -t 30 stop
    if ($LASTEXITCODE -ne 0) { Fail 'pg_ctl stop failed' }
    $serverStarted = $false

    & $pgCtl -D $dataDir status | Out-Null
    $status = $LASTEXITCODE
    if ($status -ne 3) { Fail "server still running or status unavailable (exit code $status)" }
    Write-Log 'no server process remains for the data directory'

    Write-Log 'restarting to verify persistence'
    & $pgCtl -D $dataDir -l $logFile -o "-p $Port -h 127.0.0.1" -w -t 60 start
    if ($LASTEXITCODE -ne 0) { Fail "pg_ctl restart failed with exit code $LASTEXITCODE" }
    $serverStarted = $true

    $count = Get-Scalar 'SELECT count(*) FROM smoke_items'
    if ($count -ne '2') { Fail "unexpected row count after restart: '$count'" }
    $nearest = Get-Scalar "SELECT label FROM smoke_items ORDER BY embedding <-> '[3,1,2]' LIMIT 1"
    if ($nearest -ne 'one') { Fail "unexpected nearest neighbour after restart: '$nearest'" }
    Write-Log "persistence verified after restart: $count rows, nearest '$nearest'"
}
finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    if ($serverStarted) {
        & $pgCtl -D $dataDir -m immediate -w -t 30 stop 2>&1 | Out-Null
    }
    if (-not $KeepScratch) {
        Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue
    }
}

if (Test-Path $Sidecar) {
    $version = & $Sidecar --version
    if ($LASTEXITCODE -ne 0) { Fail "sidecar --version failed with exit code $LASTEXITCODE" }
    Write-Log "sidecar: $version"
}
else {
    Write-Log "sidecar not built, skipping: $Sidecar"
}

Write-Log 'all checks passed'
