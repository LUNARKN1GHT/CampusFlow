#Requires -Version 5.1
<#
P002b: build the Windows sidecar (PyInstaller one-folder).

Mirrors desktop/build_macos.sh. Output:
  desktop\dist\campusflow-sidecar\campusflow-sidecar.exe

Environment overrides:
  CAMPUSFLOW_BUILD_PYTHON      build interpreter (default 3.12)
  CAMPUSFLOW_BUILD_VENV        build-only virtualenv (default desktop\.build-venv)
  CAMPUSFLOW_PYINSTALLER_SPEC  PyInstaller constraint (default pyinstaller>=6.11,<8)
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$desktop = Join-Path $root 'desktop'
$venv = if ($env:CAMPUSFLOW_BUILD_VENV) { $env:CAMPUSFLOW_BUILD_VENV } else { Join-Path $desktop '.build-venv' }
$pythonVersion = if ($env:CAMPUSFLOW_BUILD_PYTHON) { $env:CAMPUSFLOW_BUILD_PYTHON } else { '3.12' }
$pyinstallerSpec = if ($env:CAMPUSFLOW_PYINSTALLER_SPEC) { $env:CAMPUSFLOW_PYINSTALLER_SPEC } else { 'pyinstaller>=6.11,<8' }
$python = Join-Path $venv 'Scripts\python.exe'
$pyinstaller = Join-Path $venv 'Scripts\pyinstaller.exe'

function Invoke-Checked {
    param([string]$FilePath, [string[]]$Arguments)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath exited with code $LASTEXITCODE"
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv was not found; install uv before building the sidecar.'
}

if (-not (Test-Path $python)) {
    Write-Host '== creating build virtualenv =='
    Invoke-Checked 'uv' @('venv', '--python', $pythonVersion, $venv)
}

Write-Host '== installing build dependencies (editable backend) =='
Invoke-Checked 'uv' @('pip', 'install', '--python', $python, '--quiet', '-e', (Join-Path $root 'backend'), $pyinstallerSpec)

Write-Host '== build environment =='
& $python -c "import platform, sys; print(f'Python {sys.version.split()[0]} ({platform.machine()})')"
if ($LASTEXITCODE -ne 0) { throw 'failed to report Python version' }
& $pyinstaller --version
if ($LASTEXITCODE -ne 0) { throw 'failed to report PyInstaller version' }

Write-Host '== cleaning previous output =='
foreach ($dir in @((Join-Path $desktop 'build'), (Join-Path $desktop 'dist'))) {
    if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
}

Write-Host '== PyInstaller one-folder =='
$started = Get-Date
Push-Location $root
try {
    Invoke-Checked $pyinstaller @(
        '--noconfirm',
        '--clean',
        '--log-level', 'INFO',
        '--distpath', (Join-Path $desktop 'dist'),
        '--workpath', (Join-Path $desktop 'build'),
        (Join-Path $desktop 'campusflow-sidecar.spec')
    )
}
finally {
    Pop-Location
}
$elapsed = (Get-Date) - $started

$binary = Join-Path $desktop 'dist\campusflow-sidecar\campusflow-sidecar.exe'
if (-not (Test-Path $binary)) {
    throw "sidecar binary not found: $binary"
}
$size = [math]::Round(((Get-ChildItem -Recurse -File (Split-Path $binary) | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
Write-Host ("== build finished in {0:N1} s, {1} MB ==" -f $elapsed.TotalSeconds, $size)
Write-Host "output: $binary"
