# Shared signing checks for the Windows installer build.
# Only an existing Windows-store identity is used; no certificate or trust settings
# are created here. Release verification requires the requested signer and timestamp.

# Reject unusable or self-issued identities before starting a costly release build.
function Assert-SylluneSigningCertificate {
    param([Parameter(Mandatory)]$Certificate)
    if (-not $Certificate.HasPrivateKey) { throw 'The signing certificate has no accessible private-key association.' }
    $now = Get-Date
    if ($Certificate.NotBefore -gt $now -or $Certificate.NotAfter -le $now) {
        throw 'The signing certificate is not currently valid.'
    }
    $usageExtensions = @($Certificate.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.37' })
    $usageIds = @($usageExtensions | ForEach-Object { $_.EnhancedKeyUsages } | ForEach-Object { $_.Value })
    if ($usageIds -notcontains '1.3.6.1.5.5.7.3.3') { throw 'The certificate does not permit code signing.' }
    if ($Certificate.Subject -eq $Certificate.Issuer) { throw 'Self-issued certificates are not supported for public releases.' }
    if (-not $Certificate.Verify()) { throw 'The signing certificate chain is not trusted or could not be verified on this computer.' }
}

# Select one explicit certificate instead of silently choosing another publisher.
function Get-SylluneSigningIdentity {
    param([Parameter(Mandatory)][string]$CertificateThumbprint)
    $thumbprint = ($CertificateThumbprint -replace '\s', '').ToUpperInvariant()
    if ($thumbprint -notmatch '^[0-9A-F]{40}$') { throw 'Provide the 40 hexadecimal characters of the signing certificate thumbprint.' }
    foreach ($store in @('CurrentUser', 'LocalMachine')) {
        $certificatePath = "Cert:\$store\My\$thumbprint"
        if (Test-Path -LiteralPath $certificatePath) {
            $certificate = Get-Item -LiteralPath $certificatePath
            Assert-SylluneSigningCertificate -Certificate $certificate
            return [pscustomobject]@{
                Certificate = $certificate
                Thumbprint = $thumbprint
                IsLocalMachineStore = ($store -eq 'LocalMachine')
            }
        }
    }
    throw 'No matching signing certificate exists in CurrentUser\My or LocalMachine\My. Provision a trusted code-signing identity separately; no unsigned fallback is allowed with -Signed.'
}

# Require an ordinary timestamp endpoint without putting credentials into build logs.
function Assert-SylluneTimestampServer {
    param([Parameter(Mandatory)][string]$TimestampServer)
    $timestampUri = $null
    if (-not [Uri]::TryCreate($TimestampServer, [UriKind]::Absolute, [ref]$timestampUri) -or
        $timestampUri.Scheme -notin @('http', 'https') -or $timestampUri.UserInfo -or
        $timestampUri.Query -or $timestampUri.Fragment) {
        throw 'Use an HTTP(S) RFC 3161 timestamp endpoint without credentials, query parameters, or fragments.'
    }
}

# Use an existing SDK tool, keeping tool installation outside the build's scope.
function Resolve-SylluneSignTool {
    param([string]$SignToolPath)
    if ($SignToolPath) {
        if (-not (Test-Path -LiteralPath $SignToolPath -PathType Leaf)) { throw 'The supplied SignTool path does not exist.' }
        return (Resolve-Path -LiteralPath $SignToolPath).ProviderPath
    }
    $command = Get-Command signtool.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
    $kitsBin = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    if (Test-Path -LiteralPath $kitsBin) {
        $tool = Get-ChildItem -LiteralPath $kitsBin -Directory | Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'x64\signtool.exe' } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
        if ($tool) { return $tool }
    }
    throw 'Signed builds require an already-installed Windows SDK SignTool. Supply its path with -SignToolPath.'
}

# Preserve the product build settings while making the selected signing mode explicit.
function New-SylluneBuildConfiguration {
    param(
        [Parameter(Mandatory)][string]$PackagePath,
        [switch]$Signed,
        [string]$CertificateThumbprint,
        [string]$TimestampServer
    )
    $configuration = (Get-Content -LiteralPath $PackagePath -Raw | ConvertFrom-Json).build
    $configuration | Add-Member -NotePropertyName forceCodeSigning -NotePropertyValue ([bool]$Signed) -Force
    $configuration.win | Add-Member -NotePropertyName signExecutable -NotePropertyValue ([bool]$Signed) -Force
    if ($Signed) {
        $CertificateThumbprint = ($CertificateThumbprint -replace '\s', '').ToUpperInvariant()
        if ($CertificateThumbprint -notmatch '^[0-9A-F]{40}$') { throw 'Signed configuration requires an explicit certificate thumbprint.' }
        Assert-SylluneTimestampServer -TimestampServer $TimestampServer
        $configuration.win | Add-Member -NotePropertyName signAndEditExecutable -NotePropertyValue $true -Force
        # A file/provider configured elsewhere must not override the explicit store identity.
        $configuration.win.PSObject.Properties.Remove('azureSignOptions')
        $signingOptions = [pscustomobject]@{
            certificateSha1 = $CertificateThumbprint
            signingHashAlgorithms = @('sha256')
            rfc3161TimeStampServer = $TimestampServer
        }
        $configuration.win | Add-Member -NotePropertyName signtoolOptions -NotePropertyValue $signingOptions -Force
    }
    return $configuration
}

# A success exit code alone is insufficient: enforce signer, trust, and timestamp.
function Assert-SylluneSignedFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$CertificateThumbprint,
        [Parameter(Mandatory)][string]$SignToolPath
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Signed artifact is missing: $Path" }
    & $SignToolPath verify /pa /all /tw $Path
    if ($LASTEXITCODE -ne 0) { throw "Signature verification failed or warned: $Path" }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or
        $signature.SignerCertificate.Thumbprint -ne $CertificateThumbprint -or
        -not $signature.TimeStamperCertificate) {
        throw "Artifact must have a valid timestamped signature from the requested certificate: $Path"
    }
}

# Sign the frozen Python executable before Electron copies it into the payload.
function Set-SylluneEngineSignature {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)]$Identity,
        [Parameter(Mandatory)][string]$SignToolPath,
        [Parameter(Mandatory)][string]$TimestampServer
    )
    Assert-SylluneTimestampServer -TimestampServer $TimestampServer
    $signArguments = @('sign', '/sha1', $Identity.Thumbprint, '/s', 'My', '/fd', 'SHA256', '/tr', $TimestampServer, '/td', 'SHA256')
    if ($Identity.IsLocalMachineStore) { $signArguments += '/sm' }
    & $SignToolPath @signArguments $Path
    if ($LASTEXITCODE -ne 0) { throw 'Engine signing failed or warned; installer build stopped.' }
    Assert-SylluneSignedFile -Path $Path -CertificateThumbprint $Identity.Thumbprint -SignToolPath $SignToolPath
}
