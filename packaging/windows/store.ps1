# Prepare a local, unsigned MSIX candidate from an existing Electron x64 payload.
# Staging is isolated from the NSIS build. This never installs, signs, submits,
# registers an account, or changes Windows trust/developer settings.
param(
    [string]$IdentityName,
    [string]$Publisher,
    [string]$PublisherDisplayName,
    [string]$PackageVersion,
    [switch]$ValidationOnly,
    [string]$InputDirectory,
    [string]$OutputDirectory,
    [string]$MakeAppxPath
)

# Store identity values must come from Partner Center; validation uses its own family.
function New-SylluneStoreManifest {
    param(
        [string]$IdentityName,
        [string]$Publisher,
        [string]$PublisherDisplayName,
        [string]$PackageVersion,
        [switch]$ValidationOnly
    )
    $displayName = 'Syllune'
    if ($ValidationOnly) {
        if ($IdentityName -or $Publisher -or $PublisherDisplayName -or $PackageVersion) {
            throw '-ValidationOnly cannot be combined with Store identity or version parameters.'
        }
        $IdentityName = 'Syllune.PackagingValidation'
        # Microsoft's unsigned marker keeps this test identity separate from Store apps.
        $Publisher = 'CN=Syllune Packaging Validation, OID.2.25.311729368913984317654407730594956997722=1'
        $PublisherDisplayName = 'Syllune Packaging Validation'
        $PackageVersion = '1.0.0.0'
        $displayName = 'Syllune Packaging Validation'
    } elseif (-not $IdentityName -or -not $Publisher -or -not $PublisherDisplayName -or -not $PackageVersion) {
        throw 'Store packaging requires IdentityName, Publisher, PublisherDisplayName from Partner Center and an explicit PackageVersion. Use -ValidationOnly for a local packaging smoke check.'
    }
    if ($IdentityName -notmatch '^[A-Za-z0-9.-]{3,50}$') { throw 'IdentityName must contain 3-50 letters, digits, periods, or hyphens.' }
    if (-not $ValidationOnly -and ($IdentityName -eq 'Syllune.PackagingValidation' -or $Publisher.Contains('OID.2.25.311729368913984317654407730594956997722'))) {
        throw 'The development validation identity cannot be used for a Store candidate.'
    }
    try { $null = [Security.Cryptography.X509Certificates.X500DistinguishedName]::new($Publisher) }
    catch { throw 'Publisher must be the exact X.500 distinguished name supplied by Partner Center.' }
    if ($PublisherDisplayName.Trim().Length -eq 0 -or $PublisherDisplayName.Length -gt 256) { throw 'PublisherDisplayName must contain 1-256 characters.' }
    $parsedVersion = $null
    if ($PackageVersion -notmatch '^\d+\.\d+\.\d+\.\d+$' -or -not [Version]::TryParse($PackageVersion, [ref]$parsedVersion) -or
        $parsedVersion.Major -lt 1 -or $parsedVersion.Major -gt 65535 -or $parsedVersion.Minor -gt 65535 -or
        $parsedVersion.Build -gt 65535 -or $parsedVersion.Revision -ne 0) {
        throw 'Store PackageVersion must have four numeric parts: major 1-65535, minor/build 0-65535, and final part 0.'
    }
    $escapedIdentity = [Security.SecurityElement]::Escape($IdentityName)
    $escapedPublisher = [Security.SecurityElement]::Escape($Publisher)
    $escapedPublisherDisplayName = [Security.SecurityElement]::Escape($PublisherDisplayName)
    return @"
<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
         IgnorableNamespaces="uap rescap">
  <Identity Name="$escapedIdentity" Publisher="$escapedPublisher" Version="$parsedVersion" ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>$displayName</DisplayName>
    <PublisherDisplayName>$escapedPublisherDisplayName</PublisherDisplayName>
    <Description>Local dictation and private meeting notes</Description>
    <Logo>Assets\StoreLogo.png</Logo>
  </Properties>
  <Resources><Resource Language="en-US" /><Resource Language="sv-SE" /></Resources>
  <Dependencies><TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" /></Dependencies>
  <Applications>
    <Application Id="Syllune" Executable="Application\Syllune.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="$displayName" Description="Local dictation and private meeting notes"
                          BackgroundColor="transparent" Square150x150Logo="Assets\Square150x150Logo.png"
                          Square44x44Logo="Assets\Square44x44Logo.png" />
    </Application>
  </Applications>
  <Capabilities>
    <Capability Name="internetClient" />
    <rescap:Capability Name="runFullTrust" />
    <DeviceCapability Name="microphone" />
  </Capabilities>
</Package>
"@
}

# Resize the existing product mark into the manifest's required image dimensions.
function New-SylluneStoreAssets {
    param([Parameter(Mandatory)][string]$SourceImage, [Parameter(Mandatory)][string]$Destination)
    Add-Type -AssemblyName System.Drawing
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $image = [Drawing.Image]::FromFile($SourceImage)
    try {
        foreach ($asset in @(@('StoreLogo', 50), @('Square44x44Logo', 44), @('Square150x150Logo', 150))) {
            $size = [int]$asset[1]
            $bitmap = [Drawing.Bitmap]::new($size, $size)
            $graphics = [Drawing.Graphics]::FromImage($bitmap)
            try {
                $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                $graphics.DrawImage($image, 0, 0, $size, $size)
                $bitmap.Save((Join-Path $Destination ($asset[0] + '.png')), [Drawing.Imaging.ImageFormat]::Png)
            } finally { $graphics.Dispose(); $bitmap.Dispose() }
        }
    } finally { $image.Dispose() }
}

# Find an existing SDK packager without installing or downloading tools.
function Resolve-SylluneMakeAppx {
    param([string]$MakeAppxPath)
    if ($MakeAppxPath) {
        if (-not (Test-Path -LiteralPath $MakeAppxPath -PathType Leaf)) { throw 'The supplied MakeAppxPath does not exist.' }
        return (Resolve-Path -LiteralPath $MakeAppxPath).ProviderPath
    }
    $command = Get-Command makeappx.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
    $sdkRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    if (Test-Path -LiteralPath $sdkRoot) {
        $tool = Get-ChildItem -LiteralPath $sdkRoot -Directory | Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'x64\makeappx.exe' } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
        if ($tool) { return $tool }
    }
    throw 'MSIX preparation requires an already-installed Windows SDK MakeAppx.exe.'
}

# Package only a completed payload into a new directory; leave source files intact.
function Invoke-SylluneStorePackage {
    param(
        [string]$IdentityName, [string]$Publisher, [string]$PublisherDisplayName, [string]$PackageVersion,
        [switch]$ValidationOnly, [string]$InputDirectory, [string]$OutputDirectory, [string]$MakeAppxPath
    )
    $ErrorActionPreference = 'Stop'
    $projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
    $manifest = New-SylluneStoreManifest -IdentityName $IdentityName -Publisher $Publisher -PublisherDisplayName $PublisherDisplayName -PackageVersion $PackageVersion -ValidationOnly:$ValidationOnly
    $MakeAppxPath = Resolve-SylluneMakeAppx -MakeAppxPath $MakeAppxPath
    if (-not $InputDirectory) { $InputDirectory = Join-Path $projectRoot 'dist\syllune\win-unpacked' }
    $InputDirectory = (Resolve-Path -LiteralPath $InputDirectory).ProviderPath
    foreach ($required in @('Syllune.exe', 'resources\app.asar', 'resources\engine\SylluneEngine.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $InputDirectory $required) -PathType Leaf)) { throw "A completed x64 desktop payload is required; missing $required" }
    }
    if (Get-ChildItem -LiteralPath $InputDirectory -Recurse -Force -Attributes ReparsePoint | Select-Object -First 1) { throw 'Refusing to package a payload containing filesystem links.' }
    if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot ('.temp\store-' + [Guid]::NewGuid().ToString('N')) }
    $OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
    $inputPrefix = $InputDirectory.TrimEnd('\') + '\'
    if ($OutputDirectory.StartsWith($inputPrefix, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $OutputDirectory)) {
        throw 'OutputDirectory must be a new directory outside the input payload.'
    }
    $stage = Join-Path $OutputDirectory 'staging'
    $applicationDirectory = Join-Path $stage 'Application'
    New-Item -ItemType Directory -Path $applicationDirectory -Force | Out-Null
    Get-ChildItem -LiteralPath $InputDirectory -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $applicationDirectory -Recurse
    }
    New-SylluneStoreAssets -SourceImage (Join-Path $projectRoot 'overlay\assets\syllune.png') -Destination (Join-Path $stage 'Assets')
    [IO.File]::WriteAllText((Join-Path $stage 'AppxManifest.xml'), $manifest, [Text.UTF8Encoding]::new($false))
    $fileName = if ($ValidationOnly) { 'Syllune-VALIDATION-ONLY-x64.msix' } else { "Syllune-StoreCandidate-$PackageVersion-x64.msix" }
    $packagePath = Join-Path $OutputDirectory $fileName
    # Keep MakeAppx schema/semantic validation enabled; never use /nv or /nfv.
    & $MakeAppxPath pack /d $stage /p $packagePath /h SHA256
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $packagePath -PathType Leaf)) { throw 'MakeAppx packaging failed; retained the isolated staging directory for diagnosis.' }
    $hash = Get-FileHash -LiteralPath $packagePath -Algorithm SHA256
    "$($hash.Hash.ToLowerInvariant())  $fileName" | Set-Content -LiteralPath ($packagePath + '.sha256') -Encoding ascii
    @"
LOCAL PACKAGING CANDIDATE ONLY
Package: $fileName
Validation identity: $([bool]$ValidationOnly)
The source NSIS/Electron payload was copied and not modified.
MakeAppx validation is not installation, runtime, certification, or Store approval.
Startup-task integration and packaged app-data behavior still require verification.
No certificate was created, no package installed, and nothing was uploaded.
"@ | Set-Content -LiteralPath (Join-Path $OutputDirectory 'STATUS.txt') -Encoding UTF8
    Write-Warning 'This unsigned MSIX is only a local packaging candidate. Store identity, runtime compatibility, and certification remain required.'
    Write-Output $packagePath
}

if ($MyInvocation.InvocationName -ne '.') { Invoke-SylluneStorePackage @PSBoundParameters }
