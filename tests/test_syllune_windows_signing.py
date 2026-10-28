"""Run Windows signing policy checks without certificates or security changes.

The PowerShell harness uses fake tool responses to cover fail-closed release
behavior; real trusted signing remains a separate release prerequisite.
"""

import os
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(os.name == "nt", "Windows Authenticode build helpers")
class WindowsSigningTests(unittest.TestCase):
    def test_signing_helpers_enforce_release_policy(self):
        script = Path(__file__).resolve().parents[1] / "packaging/windows/tests/signing.tests.ps1"
        result = subprocess.run(
            [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-File", str(script)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("signing checks passed", result.stdout)
