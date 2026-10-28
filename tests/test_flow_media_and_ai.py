# Media pause while recording, and the AI edit bringing Ollama back by itself.
import unittest
import urllib.error
from unittest.mock import Mock, patch

from whisper_key.media_pause import MediaPause
from whisper_key import smart_edit
from whisper_key.smart_edit import SmartEditor


class MediaPauseTests(unittest.TestCase):
    def _run(self, pause_result, enabled=True):
        media = Mock()
        media.pause_playing.return_value = pause_result
        media.resume.return_value = pause_result
        with patch('whisper_key.platform.media', media, create=True):
            controller = MediaPause(lambda: enabled)
            controller.pause()
            if controller._pause_thread:
                controller._pause_thread.join(2)
            controller.resume()
            for _ in range(50):
                if media.resume.called or not pause_result:
                    break
                import time
                time.sleep(0.02)
        return media

    def test_resumes_exactly_what_was_paused(self):
        media = self._run(['Spotify.exe'])
        media.resume.assert_called_once_with(['Spotify.exe'])

    def test_nothing_playing_means_nothing_resumed(self):
        media = self._run([])
        media.resume.assert_not_called()

    def test_disabled_never_touches_players(self):
        media = self._run(['Spotify.exe'], enabled=False)
        media.pause_playing.assert_not_called()
        media.resume.assert_not_called()


class OllamaRecoveryTests(unittest.TestCase):
    def _editor(self):
        return SmartEditor(lambda: {'endpoint': 'http://127.0.0.1:11434'})

    def test_refused_connection_starts_ollama_without_waiting(self):
        editor = self._editor()
        refused = urllib.error.URLError(ConnectionRefusedError(10061, 'refused'))
        with patch('urllib.request.urlopen', side_effect=refused), \
             patch.object(editor, '_start_server', return_value=True) as start:
            self.assertEqual('', editor.chat([], timeout=1))
        start.assert_called_once()

    def test_unrecoverable_server_is_skipped_for_a_while(self):
        editor = self._editor()
        refused = urllib.error.URLError(ConnectionRefusedError(10061, 'refused'))
        with patch('urllib.request.urlopen', side_effect=refused) as urlopen, \
             patch.object(editor, '_start_server', return_value=False):
            self.assertEqual('', editor.chat([], timeout=1))
            self.assertEqual('', editor.chat([], timeout=1))
        self.assertEqual(1, urlopen.call_count)

    def test_timeout_loads_the_model_in_the_background_once(self):
        editor = self._editor()
        timed_out = urllib.error.URLError(TimeoutError('timed out'))
        with patch('urllib.request.urlopen', side_effect=timed_out),              patch.object(editor, 'enabled', return_value=True),              patch.object(editor, 'preload') as preload:
            self.assertEqual('', editor.chat([], timeout=1))
            self.assertEqual('', editor.chat([], timeout=1))
        preload.assert_called_once()

    def test_remote_endpoint_is_never_started_locally(self):
        editor = SmartEditor(lambda: {'endpoint': 'http://10.0.0.5:11434'})
        with patch.object(smart_edit.subprocess, 'Popen') as popen:
            self.assertFalse(editor._start_server())
        popen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
