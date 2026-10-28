# The public export leaves out private files, and its audit flags this
# machine's identity without the tool itself naming anyone.
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from syllune.state_manager import _OVERLAY_DIR, _is_own_window

import importlib.util
_spec = importlib.util.spec_from_file_location(
    'export_public', Path(__file__).resolve().parents[1] / 'tools' / 'export_public.py')
export_public = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_public)


class OwnWindowTests(unittest.TestCase):
    def test_installed_app_and_dev_overlay_are_own_windows(self):
        self.assertTrue(_is_own_window({'exe': 'Syllune.exe', 'path': r'C:\Apps\Syllune\Syllune.exe'}))
        dev = str(Path(_OVERLAY_DIR) / 'node_modules' / 'electron' / 'dist' / 'electron.exe')
        self.assertTrue(_is_own_window({'exe': 'electron.exe', 'path': dev}))

    def test_other_electron_apps_are_not(self):
        self.assertFalse(_is_own_window({'exe': 'electron.exe', 'path': r'C:\Other\electron.exe'}))
        self.assertFalse(_is_own_window({'exe': 'Code.exe', 'path': r'C:\VS Code\Code.exe'}))


class ExportAuditTests(unittest.TestCase):
    def test_audit_flags_local_identity_and_private_files(self):
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.dict('os.environ', {'USERNAME': 'testperson'}), \
             mock.patch.object(export_public, '_git_config', return_value=''):
            root = Path(folder)
            (root / 'notes.md').write_text('saved under C:/Users/testperson/x\n', encoding='utf-8')
            (root / 'clean.md').write_text('nothing personal here\n', encoding='utf-8')
            (root / 'transcripts.jsonl').write_text('{}\n', encoding='utf-8')
            found = {(path, label) for path, _, label in export_public.audit(root)}
        self.assertIn(('transcripts.jsonl', 'personal data file'), found)
        self.assertTrue(any(path == 'notes.md' for path, _ in found))
        self.assertFalse(any(path == 'clean.md' for path, _ in found))

    def test_private_files_are_never_exported(self):
        exported = list(export_public.outgoing_files())
        self.assertFalse([f for f in exported if f.startswith(('.temp/', '.claude/', 'docs/superpowers/'))])
        self.assertNotIn('CLAUDE.md', exported)


if __name__ == '__main__':
    unittest.main()
