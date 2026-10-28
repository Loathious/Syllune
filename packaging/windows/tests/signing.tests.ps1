# Behavioral checks for release signing without creating certificates or trust roots.
# Fake SignTool and signature responses exercise failures without accessing private keys.
param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\signing.ps1')
$script:checks = 0

function Assert-True {
    param($Value, [string]$Message)
    if (-not $Value) { throw $Message }
    $script:checks++
}

function Assert-Throws {
    param([scriptblock]$Action, [string]$ExpectedMessage)
    $caught = $null
    try { & $Action } catch { $caught = $_ }
    Assert-True ($caught -and $caught.Exception.Message.Contains($ExpectedMessage)) "Expected failure containing '$ExpectedMessage'; received '$caught'."
}

function New-TestCertificate {
    $certificate = [pscustomobject]@{
        HasPrivateKey = $true
        NotBefore = (Get-Date).AddDays(-1)
        NotAfter = (Get-Date).AddDays(30)
        Subject = 'CN=Publisher'
        Issuer = 'CN=Trusted CA'
        Extensions = @([pscustomobject]@{
            Oid = [pscustomobject]@{ Value = '2.5.29.37' }
            EnhancedKeyUsages = @([pscustomobject]@{ Value = '1.3.6.1.5.5.7.3.3' })
        })
        Trusted = $true
    }
    $certificate | Add-Member -MemberType ScriptMethod -Name Verify -Value { return $this.Trusted }
    return $certificate
}

function Get-AuthenticodeSignature {
    param([string]$LiteralPath)
    return $script:signature
}

$thumbprint = 'A' * 40
$temporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) ('syllune-signing-tests-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
try {
    $certificate = New-TestCertificate
    Assert-SylluneSigningCertificate -Certificate $certificate
    $certificate.HasPrivateKey = $false
    Assert-Throws { Assert-SylluneSigningCertificate $certificate } 'private-key'
    $certificate = New-TestCertificate
    $certificate.NotAfter = (Get-Date).AddDays(-1)
    Assert-Throws { Assert-SylluneSigningCertificate $certificate } 'not currently valid'
    $certificate = New-TestCertificate
    $certificate.NotBefore = (Get-Date).AddDays(1)
    Assert-Throws { Assert-SylluneSigningCertificate $certificate } 'not currently valid'
    $certificate = New-TestCertificate
    $certificate.Extensions = @()
    Assert-Throws { Assert-SylluneSigningCertificate $certificate } 'does not permit code signing'
    $certificate = New-TestCertificate
    $certificate.Issuer = $certificate.Subject
    Assert-Throws { Assert-SylluneSigningCertificate $certificate } 'Self-issued'
    $certificate = New-TestCertificate
    $certificate.Trusted = $false
    Assert-Throws { Assert-SylluneSigningCertificate $certificate } 'not trusted'
    Assert-Throws { Get-SylluneSigningIdentity 'not-a-thumbprint' } '40 hexadecimal'

    Assert-SylluneTimestampServer 'https://timestamp.example.test'
    foreach ($invalid in @('file:///timestamp', 'https://user:secret@timestamp.example.test', 'https://timestamp.example.test/?secret=value', 'invalid')) {
        Assert-Throws { Assert-SylluneTimestampServer $invalid } 'HTTP(S) RFC 3161'
    }
    Assert-Throws { Resolve-SylluneSignTool (Join-Path $temporaryDirectory 'absent.exe') } 'does not exist'

    $packagePath = Join-Path $PSScriptRoot '..\..\..\overlay\package.json'
    $unsigned = New-SylluneBuildConfiguration -PackagePath $packagePath
    Assert-True (-not $unsigned.forceCodeSigning -and -not $unsigned.win.signExecutable) 'Debug mode must disable signing even if a caller has an unrelated certificate configured.'
    $signed = New-SylluneBuildConfiguration -PackagePath $packagePath -Signed -CertificateThumbprint (' ' + $thumbprint.ToLower() + ' ') -TimestampServer 'https://timestamp.example.test'
    Assert-True ($signed.forceCodeSigning -and $signed.win.signExecutable -and $signed.win.signAndEditExecutable) 'Signed releases must require executable signing.'
    Assert-True ($signed.win.signtoolOptions.certificateSha1 -ceq $thumbprint) 'Signed configuration must normalize and preserve the explicit identity.'
    Assert-True ($signed.win.signtoolOptions.signingHashAlgorithms.Count -eq 1 -and $signed.win.signtoolOptions.signingHashAlgorithms[0] -eq 'sha256') 'Signed releases must use SHA-256.'
    Assert-True ($signed.win.signtoolOptions.rfc3161TimeStampServer -eq 'https://timestamp.example.test') 'The same timestamp service must reach Electron.'
    Assert-True ($signed.extraResources.Count -eq $unsigned.extraResources.Count -and $signed.nsis.perMachine -eq $unsigned.nsis.perMachine) 'Signing must preserve payload and installation settings.'
    Assert-Throws { New-SylluneBuildConfiguration -PackagePath $packagePath -Signed -TimestampServer 'https://timestamp.example.test' } 'explicit certificate thumbprint'

    $artifact = Join-Path $temporaryDirectory 'engine.exe'
    Set-Content -LiteralPath $artifact -Value 'test-only placeholder'
    $fakeSignTool = Join-Path $temporaryDirectory 'fake-signtool.ps1'
    @'
$global:SylluneSignToolCalls += ,@($args)
$global:LASTEXITCODE = $global:SylluneSignToolExitCode
'@ | Set-Content -LiteralPath $fakeSignTool
    $global:SylluneSignToolCalls = @()
    $global:SylluneSignToolExitCode = 0
    $script:signature = [pscustomobject]@{
        Status = 'Valid'
        SignerCertificate = [pscustomobject]@{ Thumbprint = $thumbprint }
        TimeStamperCertificate = [pscustomobject]@{ Subject = 'CN=Timestamp' }
    }
    $identity = [pscustomobject]@{ Thumbprint = $thumbprint; IsLocalMachineStore = $false }
    Set-SylluneEngineSignature -Path $artifact -Identity $identity -SignToolPath $fakeSignTool -TimestampServer 'https://timestamp.example.test'
    Assert-True ($global:SylluneSignToolCalls.Count -eq 2 -and $global:SylluneSignToolCalls[0][0] -eq 'sign' -and $global:SylluneSignToolCalls[1][0] -eq 'verify') 'Engine signing must immediately verify the result.'
    $signCall = $global:SylluneSignToolCalls[0]
    Assert-True ($signCall -contains '/sha1' -and $signCall -contains $thumbprint -and $signCall -contains '/fd' -and $signCall -contains '/tr' -and $signCall -contains '/td' -and $signCall -notcontains '/sm') 'Engine signing must select the requested user identity and timestamp.'
    $verifyCall = $global:SylluneSignToolCalls[1]
    Assert-True ($verifyCall -contains '/pa' -and $verifyCall -contains '/all' -and $verifyCall -contains '/tw') 'Verification must enforce Authenticode policy and timestamps.'
    $identity.IsLocalMachineStore = $true
    Set-SylluneEngineSignature -Path $artifact -Identity $identity -SignToolPath $fakeSignTool -TimestampServer 'https://timestamp.example.test'
    Assert-True ($global:SylluneSignToolCalls[2] -contains '/sm') 'Machine identities must select the machine store.'

    $global:SylluneSignToolExitCode = 2
    Assert-Throws { Assert-SylluneSignedFile -Path $artifact -CertificateThumbprint $thumbprint -SignToolPath $fakeSignTool } 'failed or warned'
    $callCount = $global:SylluneSignToolCalls.Count
    Assert-Throws { Set-SylluneEngineSignature -Path $artifact -Identity $identity -SignToolPath $fakeSignTool -TimestampServer 'https://timestamp.example.test' } 'signing failed or warned'
    Assert-True ($global:SylluneSignToolCalls.Count -eq $callCount + 1) 'A signing warning must stop before verification or packing.'
    $global:SylluneSignToolExitCode = 0
    $script:signature.TimeStamperCertificate = $null
    Assert-Throws { Assert-SylluneSignedFile -Path $artifact -CertificateThumbprint $thumbprint -SignToolPath $fakeSignTool } 'valid timestamped signature'
    $script:signature.TimeStamperCertificate = [pscustomobject]@{ Subject = 'CN=Timestamp' }
    $script:signature.SignerCertificate.Thumbprint = 'B' * 40
    Assert-Throws { Assert-SylluneSignedFile -Path $artifact -CertificateThumbprint $thumbprint -SignToolPath $fakeSignTool } 'requested certificate'
    $script:signature.SignerCertificate.Thumbprint = $thumbprint
    $script:signature.Status = 'HashMismatch'
    Assert-Throws { Assert-SylluneSignedFile -Path $artifact -CertificateThumbprint $thumbprint -SignToolPath $fakeSignTool } 'valid timestamped signature'
    Assert-Throws { Assert-SylluneSignedFile -Path (Join-Path $temporaryDirectory 'missing.exe') -CertificateThumbprint $thumbprint -SignToolPath $fakeSignTool } 'artifact is missing'
    Write-Output "$script:checks signing checks passed."
} finally {
    # This path is created above under the OS temp directory, never a caller-supplied target.
    $resolvedTemporaryDirectory = [IO.Path]::GetFullPath($temporaryDirectory)
    $resolvedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolvedTemporaryDirectory.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing to remove a test directory outside the OS temp folder.' }
    Remove-Item -LiteralPath $resolvedTemporaryDirectory -Recurse -Force
    Remove-Variable SylluneSignToolCalls, SylluneSignToolExitCode -Scope Global -ErrorAction SilentlyContinue
}
