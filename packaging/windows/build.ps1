# Build a complete, per-user Syllune installer using only local environments.
# Signed releases use an existing trusted Windows-store identity; local debug builds
# remain explicitly unsigned. This script does not publish or provision certificates.
param(
    [switch]$SkipEngine,
    [switch]$Signed,
    [string]$CertificateThumbprint,
    [string]$SignToolPath,
    [string]$TimestampServer = 'http://timestamp.digicert.com'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'signing.ps1')
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$packagePath = Join-Path $projectRoot 'overlay\package.json'
$identity = $null
# Fail before asset generation, dependency installation, or freezing the engine.
if ($Signed) {
    if (-not $CertificateThumbprint) { throw '-Signed requires -CertificateThumbprint for an already-provisioned trusted code-signing certificate.' }
    if ($env:ELECTRON_BUILDER_OFFLINE -eq 'true') { throw 'Signed releases require timestamping; unset ELECTRON_BUILDER_OFFLINE first.' }
    Assert-SylluneTimestampServer -TimestampServer $TimestampServer
    $identity = Get-SylluneSigningIdentity -CertificateThumbprint $CertificateThumbprint
    $SignToolPath = Resolve-SylluneSignTool -SignToolPath $SignToolPath
} elseif ($CertificateThumbprint -or $SignToolPath) {
    throw 'Use -Signed when supplying signing options; unsigned builds never select a signing identity.'
}
$configuration = New-SylluneBuildConfiguration -PackagePath $packagePath -Signed:$Signed -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer
$version = (Get-Content -LiteralPath $packagePath -Raw | ConvertFrom-Json).version
$installer = Join-Path $projectRoot "dist\syllune\Syllune-Setup-$version-x64.exe"
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Create the project-local .venv first (see docs/syllune-install.md).' }
$temporaryConfig = $null
$previousDiscovery = [Environment]::GetEnvironmentVariable('CSC_IDENTITY_AUTO_DISCOVERY', 'Process')
$previousSignTool = [Environment]::GetEnvironmentVariable('SIGNTOOL_PATH', 'Process')
Push-Location $projectRoot
try {
    # Refresh editable package metadata after a version bump; otherwise the
    # frozen app can report the previous version despite a new installer name.
    & $python -m pip install --no-deps --no-build-isolation -e .
    if ($LASTEXITCODE -ne 0) { throw 'Local package metadata refresh failed' }
    & $python tools\build_brand_assets.py
    if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed' }
    if (-not $SkipEngine) {
        & $python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\syllune-engine packaging\windows\SylluneEngine.spec
        if ($LASTEXITCODE -ne 0) { throw 'Engine build failed' }
    }
    if (-not (Test-Path -LiteralPath 'dist\SylluneEngine\SylluneEngine.exe')) { throw 'Engine artifact missing' }
    if ($Signed) {
        Set-SylluneEngineSignature -Path (Join-Path $projectRoot 'dist\SylluneEngine\SylluneEngine.exe') -Identity $identity -SignToolPath $SignToolPath -TimestampServer $TimestampServer
        $env:CSC_IDENTITY_AUTO_DISCOVERY = 'true'
        $env:SIGNTOOL_PATH = $SignToolPath
    } else {
        $env:CSC_IDENTITY_AUTO_DISCOVERY = 'false'
        Write-Warning 'This is an unsigned local/debug installer. Public downloads may show Windows SmartScreen warnings.'
    }
    # Use the current package build settings without rewriting tracked configuration.
    $temporaryDirectory = Join-Path $projectRoot '.temp'
    New-Item -ItemType Directory -Path $temporaryDirectory -Force | Out-Null
    $temporaryConfig = Join-Path $temporaryDirectory ("windows-build-{0}.json" -f [Guid]::NewGuid().ToString('N'))
    $configuration | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $temporaryConfig -Encoding UTF8
    Push-Location overlay
    try {
        & npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Local UI dependencies failed' }
        & npm.cmd run dist:win -- --config $temporaryConfig
        if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
    } finally { Pop-Location }
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw 'The current version installer is missing.' }
    if ($Signed) {
        foreach ($artifact in @(
            (Join-Path $projectRoot 'dist\syllune\win-unpacked\Syllune.exe'),
            (Join-Path $projectRoot 'dist\syllune\win-unpacked\resources\engine\SylluneEngine.exe'),
            $installer
        )) {
            Assert-SylluneSignedFile -Path $artifact -CertificateThumbprint $identity.Thumbprint -SignToolPath $SignToolPath
        }
        Write-Host 'Signed installer, desktop app, and packaged engine verified with the requested publisher and timestamps.'
    }
    $checksum = Get-FileHash -LiteralPath $installer -Algorithm SHA256
    "$($checksum.Hash.ToLower())  $([IO.Path]::GetFileName($installer))" | Set-Content -LiteralPath ($installer + '.sha256') -Encoding ascii
} finally {
    [Environment]::SetEnvironmentVariable('CSC_IDENTITY_AUTO_DISCOVERY', $previousDiscovery, 'Process')
    [Environment]::SetEnvironmentVariable('SIGNTOOL_PATH', $previousSignTool, 'Process')
    if ($temporaryConfig -and (Test-Path -LiteralPath $temporaryConfig)) { Remove-Item -LiteralPath $temporaryConfig }
    Pop-Location
}
