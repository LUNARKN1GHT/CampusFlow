#Requires -Version 5.1
# Windows runtime smoke (initdb + pgvector + sidecar) runs in desktop-windows-build.yml.
<#
P003b: stage a self-contained PostgreSQL 17 + pgvector runtime for the Windows app.

The script downloads the official EnterpriseDB Windows binaries (they ship headers,
import libraries and the MSVC runtime), stages them under desktop\vendor\pgsql,
compiles pgvector 0.8.7 against the staged headers with nmake, and prunes
development files. Nothing is installed system-wide.

Environment overrides:
  CAMPUSFLOW_DESKTOP_VENDOR  vendor root (default <repo>\desktop\vendor)
  CAMPUSFLOW_PG_URL          PostgreSQL binaries zip URL
  CAMPUSFLOW_PG_SHA256       expected SHA-256 of the zip
  CAMPUSFLOW_VECTOR_URL      pgvector source tarball URL
  CAMPUSFLOW_VECTOR_SHA256   expected SHA-256 of the tarball
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$scriptDir = $PSScriptRoot
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
$vendorDir = if ($env:CAMPUSFLOW_DESKTOP_VENDOR) { $env:CAMPUSFLOW_DESKTOP_VENDOR } else { Join-Path $repoRoot 'desktop\vendor' }
$stageDir = Join-Path $vendorDir 'pgsql'
$buildDir = Join-Path $vendorDir 'build'
$downloadDir = Join-Path $buildDir 'downloads'
$sourceDir = Join-Path $buildDir 'src'
$logDir = Join-Path $buildDir 'logs'

$pgVersion = '17.11'
$pgUrl = if ($env:CAMPUSFLOW_PG_URL) { $env:CAMPUSFLOW_PG_URL } else { 'https://get.enterprisedb.com/postgresql/postgresql-17.11-1-windows-x64-binaries.zip' }
$pgSha256 = if ($env:CAMPUSFLOW_PG_SHA256) { $env:CAMPUSFLOW_PG_SHA256 } else { '6eabdf00d2893713b75db4336a23c3fdf505f056e217ec6e2e95d901750cfea3' }

$vectorVersion = '0.8.7'
$vectorUrl = if ($env:CAMPUSFLOW_VECTOR_URL) { $env:CAMPUSFLOW_VECTOR_URL } else { "https://github.com/pgvector/pgvector/archive/refs/tags/v$vectorVersion.tar.gz" }
$vectorSha256 = if ($env:CAMPUSFLOW_VECTOR_SHA256) { $env:CAMPUSFLOW_VECTOR_SHA256 } else { 'cac0b10c360f05b2d521200105ba3697e773d4cd3731f5a915a7e37ebe0bea85' }

$keptBinaries = @(
    'clusterdb', 'createdb', 'dropdb', 'initdb', 'pg_basebackup', 'pg_checksums',
    'pg_ctl', 'pg_dump', 'pg_dumpall', 'pg_isready', 'pg_resetwal', 'pg_restore',
    'pg_rewind', 'pg_upgrade', 'pg_verifybackup', 'pg_waldump', 'postgres', 'psql',
    'reindexdb', 'vacuumdb'
)

function Invoke-Checked {
    param([string]$FilePath, [string[]]$Arguments)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath exited with code $LASTEXITCODE"
    }
}

function Remove-Tree {
    param([string]$Path)
    if (Test-Path $Path) {
        & cmd /c "rmdir /s /q `"$Path`""
        if ($LASTEXITCODE -ne 0) { throw "failed to remove $Path" }
    }
}

function Get-VerifiedFile {
    param([string]$Url, [string]$Target, [string]$ExpectedSha256)
    if (-not (Test-Path $Target)) {
        Write-Host "downloading $(Split-Path -Leaf $Target)"
        & curl.exe -fsSL --retry 3 --connect-timeout 20 -o "$Target.part" $Url
        if ($LASTEXITCODE -ne 0) { throw "download failed: $Url" }
        Move-Item -Force "$Target.part" $Target
    }
    else {
        Write-Host "using cached $(Split-Path -Leaf $Target)"
    }
    $actual = (Get-FileHash -Algorithm SHA256 $Target).Hash.ToLowerInvariant()
    if ($actual -ne $ExpectedSha256.ToLowerInvariant()) {
        Remove-Item -Force $Target
        throw "SHA-256 mismatch for $Target (cached file removed). Expected $ExpectedSha256, got $actual"
    }
    Write-Host "verified $(Split-Path -Leaf $Target): $actual"
}

function Find-VcVars {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path $vswhere)) {
        throw 'Visual Studio Installer (vswhere.exe) was not found; install the MSVC build tools.'
    }
    $vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $vsPath) {
        throw 'Visual Studio with the MSVC x64 toolchain was not found.'
    }
    $vcvars = Join-Path $vsPath 'VC\Auxiliary\Build\vcvars64.bat'
    if (-not (Test-Path $vcvars)) {
        throw "vcvars64.bat was not found under $vsPath"
    }
    return @{ VcVars = $vcvars; VsPath = $vsPath }
}

function Ensure-VcRuntime {
    param([string]$BinDir, [string]$VsPath)
    $needed = @('vcruntime140.dll', 'msvcp140.dll')
    $missing = $needed | Where-Object { -not (Test-Path (Join-Path $BinDir $_)) }
    if (-not $missing) { return }
    Write-Host 'copying MSVC runtime DLLs into the staged bin directory'
    $crtRoot = Join-Path $VsPath 'VC\Redist\MSVC'
    $crtDir = Get-ChildItem -Path $crtRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName 'x64\Microsoft.VC143.CRT' } |
        Where-Object { Test-Path $_ } |
        Select-Object -First 1
    if (-not $crtDir) {
        throw 'MSVC runtime DLLs are missing from the staged tree and the redist directory was not found.'
    }
    Copy-Item (Join-Path $crtDir '*.dll') $BinDir -Force
}

function Prune-Stage {
    Write-Host 'pruning development files'
    foreach ($path in @('include', 'lib\pgxs', 'share\doc', 'share\man', 'share\info', 'symbols', 'pgAdmin 4', 'StackBuilder')) {
        Remove-Tree (Join-Path $stageDir $path)
    }
    Get-ChildItem -Path $stageDir -Recurse -File |
        Where-Object { @('.lib', '.exp', '.pdb', '.a') -contains $_.Extension } |
        Remove-Item -Force
    foreach ($license in @('pgAdmin_license.txt', 'pgAdmin_3rd_party_licenses.txt', 'StackBuilder_3rd_party_licenses.txt')) {
        $target = Join-Path $stageDir $license
        if (Test-Path $target) { Remove-Item -Force $target }
    }
    # 只裁剪可执行文件；bin 下的 DLL 是运行时依赖，必须保留。
    foreach ($exe in Get-ChildItem -Path (Join-Path $stageDir 'bin') -File -Filter *.exe) {
        $name = [System.IO.Path]::GetFileNameWithoutExtension($exe.Name)
        if ($keptBinaries -notcontains $name) { Remove-Item -Force $exe.FullName }
    }
}

Write-Host "repository: $repoRoot"
Write-Host "vendor: $vendorDir"

if (-not (Get-Command 'tar' -ErrorAction SilentlyContinue)) {
    throw 'tar.exe is required (included with Windows 10 and later).'
}
$vc = Find-VcVars

New-Item -ItemType Directory -Force -Path $downloadDir, $logDir | Out-Null
$pgArchive = Join-Path $downloadDir "postgresql-$pgVersion-windows-x64-binaries.zip"
$vectorArchive = Join-Path $downloadDir "pgvector-$vectorVersion.tar.gz"
Get-VerifiedFile $pgUrl $pgArchive $pgSha256
Get-VerifiedFile $vectorUrl $vectorArchive $vectorSha256

Write-Host 'cleaning previous build and staging output'
Remove-Tree $sourceDir
Remove-Tree $stageDir
New-Item -ItemType Directory -Force -Path $sourceDir | Out-Null

Write-Host "extracting PostgreSQL $pgVersion"
& tar -xf $pgArchive -C $sourceDir --exclude "pgsql/pgAdmin 4/*" --exclude "pgsql/StackBuilder/*"
if ($LASTEXITCODE -ne 0) { throw 'failed to extract the PostgreSQL archive' }
$extractedPg = Join-Path $sourceDir 'pgsql'
if (-not (Test-Path (Join-Path $extractedPg 'bin\postgres.exe'))) {
    throw "unexpected PostgreSQL archive layout: $extractedPg"
}

Write-Host "extracting pgvector $vectorVersion"
& tar -xzf $vectorArchive -C $sourceDir
if ($LASTEXITCODE -ne 0) { throw 'failed to extract the pgvector archive' }
$vectorSrc = Join-Path $sourceDir "pgvector-$vectorVersion"
if (-not (Test-Path (Join-Path $vectorSrc 'Makefile.win'))) {
    throw "unexpected pgvector archive layout: $vectorSrc"
}

New-Item -ItemType Directory -Force -Path $vendorDir | Out-Null
Move-Item -Path $extractedPg -Destination $stageDir

Write-Host "building pgvector against $stageDir"
$env:PGROOT = $stageDir
$previousLocation = Get-Location
Set-Location $vectorSrc
try {
    $vcvars = $vc.VcVars
    $command = "call `"$vcvars`" >nul && nmake /F Makefile.win && nmake /F Makefile.win install"
    & cmd /c $command
    if ($LASTEXITCODE -ne 0) {
        throw "pgvector build failed with code $LASTEXITCODE"
    }
}
finally {
    Set-Location $previousLocation
    Remove-Item Env:\PGROOT -ErrorAction SilentlyContinue
}

Prune-Stage
Ensure-VcRuntime (Join-Path $stageDir 'bin') $vc.VsPath

Write-Host 'verifying staged binaries'
$versionOutput = & (Join-Path $stageDir 'bin\postgres.exe') --version 2>&1
$versionExit = $LASTEXITCODE
if ($versionExit -ne 0 -or "$versionOutput" -notlike "*$pgVersion*") {
    throw "unexpected postgres version (exit $versionExit): $versionOutput"
}
& (Join-Path $stageDir 'bin\pg_isready.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'pg_isready is not runnable' }
foreach ($file in @('lib\vector.dll', 'share\extension\vector.control', 'share\extension\vector--0.8.7.sql')) {
    if (-not (Test-Path (Join-Path $stageDir $file))) {
        throw "missing staged file: $file"
    }
}

$size = [math]::Round(((Get-ChildItem -Recurse -File $stageDir | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
Write-Host "done: $versionOutput ($size MB)"
Write-Host "pgvector: $stageDir\lib\vector.dll and share\extension\vector*"
Write-Host "next: package with desktop/electron electron-builder --win"
