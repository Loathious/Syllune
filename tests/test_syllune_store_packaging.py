"""Exercise local Store manifest rules without accounts or package installation.

The packaging candidate remains separate from real Store identity and runtime
verification; these checks do not create signing keys or alter Windows trust.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image


@unittest.skipUnless(os.name == "nt", "Windows Store packaging helpers")
class StorePackagingTests(unittest.TestCase):
    def test_manifest_assets_have_required_dimensions(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="syllune-store-assets-") as temporary_directory:
            environment = os.environ.copy()
            environment["SYLLUNE_TEST_SOURCE_IMAGE"] = str(project_root / "overlay/assets/syllune.png")
            environment["SYLLUNE_TEST_ASSETS"] = temporary_directory
            result = subprocess.run(
                [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command",
                 "$ErrorActionPreference = 'Stop'; . .\\packaging\\windows\\store.ps1; "
                 "New-SylluneStoreAssets -SourceImage $env:SYLLUNE_TEST_SOURCE_IMAGE "
                 "-Destination $env:SYLLUNE_TEST_ASSETS"],
                cwd=project_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for filename, size in (("StoreLogo.png", 50), ("Square44x44Logo.png", 44), ("Square150x150Logo.png", 150)):
                with self.subTest(filename=filename), Image.open(Path(temporary_directory) / filename) as image:
                    self.assertEqual(image.size, (size, size))
                    self.assertEqual(image.format, "PNG")

    def test_manifest_identity_and_capabilities(self):
        project_root = Path(__file__).resolve().parents[1]
        checks = r'''
$ErrorActionPreference = 'Stop'
. .\packaging\windows\store.ps1
function Require($value, $message) { if (-not $value) { throw $message } }
function Reject([scriptblock]$action) {
    $caught = $false
    try { & $action | Out-Null } catch { $caught = $true }
    Require $caught 'Invalid Store identity/version was accepted.'
}
Reject { New-SylluneStoreManifest }
Reject { New-SylluneStoreManifest -ValidationOnly -IdentityName 'Real.Identity' }
$identity = @{ IdentityName = '12345.Publisher.Syllune'; Publisher = 'CN=00000000-0000-0000-0000-000000000001'; PublisherDisplayName = 'Example & Publisher'; PackageVersion = '1.24.1.0' }
[xml]$manifest = New-SylluneStoreManifest @identity
Require ($manifest.Package.Identity.Name -eq $identity.IdentityName) 'The exact Store name was lost.'
Require ($manifest.Package.Properties.PublisherDisplayName -eq 'Example & Publisher') 'XML escaping changed the publisher.'
Require ($manifest.Package.Identity.Version -eq '1.24.1.0') 'The explicit Store version was lost.'
Require ($manifest.Package.Applications.Application.Executable -eq 'Application\Syllune.exe') 'The desktop entry point must launch Electron.'
Require ($manifest.Package.Applications.Application.EntryPoint -eq 'Windows.FullTrustApplication') 'The desktop full-trust entry point is missing.'
Require (@($manifest.Package.Capabilities.DeviceCapability.Name) -contains 'microphone') 'Microphone capability is missing.'
Require (@($manifest.Package.Capabilities.Capability.Name) -contains 'runFullTrust') 'Full-trust capability is missing.'
foreach ($version in @('0.24.1.0', '1.24.1.1', '1.24.1', '65536.1.0.0', '1.65536.0.0', '1.0.65536.0')) {
    $identity.PackageVersion = $version
    Reject { New-SylluneStoreManifest @identity }
}
$identity.PackageVersion = '1.24.1.0'
$identity.IdentityName = 'invalid_identity'
Reject { New-SylluneStoreManifest @identity }
$identity.IdentityName = 'Syllune.PackagingValidation'
Reject { New-SylluneStoreManifest @identity }
[xml]$validation = New-SylluneStoreManifest -ValidationOnly
Require ($validation.Package.Identity.Name -eq 'Syllune.PackagingValidation') 'Validation must use a different package identity.'
Require ($validation.Package.Identity.Publisher -like '*OID.2.25.311729368913984317654407730594956997722=1') 'Validation identity must use the unsigned marker.'
Require ($validation.Package.Properties.DisplayName -like '*Validation*') 'Validation builds must be visibly labeled.'
'Store manifest checks passed.'
'''
        result = subprocess.run(
            [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", checks],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Store manifest checks passed", result.stdout)
