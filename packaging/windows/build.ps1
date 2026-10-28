# Build a complete, per-user Syllune installer using only local environments.
# This script does not publish, tag, install globally, or download speech models.
param([switch]$SkipEngine)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Create the project-local .venv first (see docs/syllune-install.md).' }
Push-Location $projectRoot
try {
    & $python tools\build_brand_assets.py
    if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed' }
    if (-not $SkipEngine) {
        & $python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\syllune-engine packaging\windows\SylluneEngine.spec
        if ($LASTEXITCODE -ne 0) { throw 'Engine build failed' }
    }
    if (-not (Test-Path -LiteralPath 'dist\SylluneEngine\SylluneEngine.exe')) { throw 'Engine artifact missing' }
    Push-Location overlay
    try {
        & npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Local UI dependencies failed' }
        $env:CSC_IDENTITY_AUTO_DISCOVERY = 'false'
        & npm.cmd run dist:win
        if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
    } finally { Pop-Location }
    Get-ChildItem -LiteralPath dist\syllune -Filter 'Syllune-Setup-*.exe' | ForEach-Object {
        $checksum = Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256
        "$($checksum.Hash.ToLower())  $($_.Name)" | Set-Content -LiteralPath ($_.FullName + '.sha256') -Encoding ascii
    }
} finally { Pop-Location }
