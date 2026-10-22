# 校验 NSIS 安装包内的应用资源；构建与发布工作流共用。
# 用法：powershell -File desktop/verify_windows_installer.ps1 -Installer <path-to-exe>
param(
  [Parameter(Mandatory = $true)][string]$Installer
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Installer)) { throw "installer missing: $Installer" }

$sevenZip = 'C:\Program Files\7-Zip\7z.exe'
if (-not (Test-Path $sevenZip)) { throw '7-Zip is required to inspect the installer' }

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("campusflow-installer-" + [guid]::NewGuid().ToString('N'))
$outer = Join-Path $work 'outer'
$app = Join-Path $work 'app'
New-Item -ItemType Directory -Force -Path $outer | Out-Null

try {
  & $sevenZip x $Installer "-o$outer" -y | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'failed to extract installer' }

  $appArchive = Get-ChildItem $outer -Recurse -Filter 'app-64.7z' | Select-Object -First 1
  if (-not $appArchive) { throw 'app-64.7z not found inside the installer' }
  & $sevenZip x $appArchive.FullName "-o$app" -y | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'failed to extract app-64.7z' }

  $expected = @(
    'resources\sidecar\campusflow-sidecar.exe',
    'resources\pgsql\bin\postgres.exe',
    'resources\pgsql\share\extension\vector.control',
    'resources\static\index.html',
    'resources\THIRD-PARTY-NOTICES.md'
  )
  foreach ($relative in $expected) {
    if (-not (Test-Path (Join-Path $app $relative))) {
      throw "missing from installer: $relative"
    }
  }
  Write-Host "installer contents verified: $($expected.Count) resources"
}
finally {
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
