# test_audio_com.py
# The microphone is opened on background threads, and on Windows that only
# works while COM is available to them. These tests pin the one parked thread
# that keeps it available, and the matching no-op on macOS.
import importlib.util
import sys
import threading
import unittest
from pathlib import Path

MACOS_MODULE = Path(__file__).parent.parent / 'src' / 'syllune' / 'platform' / 'macos' / 'audio_endpoints.py'


def _com_threads():
    return [thread for thread in threading.enumerate() if thread.name == 'audio-com']


class ComKeeperTests(unittest.TestCase):

    @unittest.skipUnless(sys.platform == 'win32', 'COM exists on Windows only')
    def test_one_parked_thread_however_often_it_is_asked_for(self):
        from syllune.platform.windows import audio_endpoints
        audio_endpoints.keep_com_available()
        audio_endpoints.keep_com_available()
        keepers = _com_threads()
        self.assertEqual(len(keepers), 1)
        # A non-daemon keeper would stop the app from ever exiting.
        self.assertTrue(keepers[0].daemon)
        self.assertTrue(keepers[0].is_alive())

    def test_macos_mirrors_the_call_and_starts_nothing(self):
        # Loaded from its file: the macOS package itself imports only on a Mac.
        spec = importlib.util.spec_from_file_location('macos_audio_endpoints', MACOS_MODULE)
        audio_endpoints = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(audio_endpoints)
        before = len(_com_threads())
        self.assertIsNone(audio_endpoints.keep_com_available())
        self.assertEqual(len(_com_threads()), before)


if __name__ == '__main__':
    unittest.main()
