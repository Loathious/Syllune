# Meeting lifecycle tests use disposable stores and fake inference only.
# They protect user-edited notes and prevent cancelled jobs from coming back.
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np

from whisper_key.meetings import MeetingStore, MeetingController, WAITING, write_markdown, remove_echo, build_transcript


class MeetingLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MeetingStore(self.root / 'data')
        self.controller = MeetingController(self.store, engine=Mock(), editor=Mock(),
            game_active=lambda: True, mic_device=lambda: None, notes_folder=lambda: self.root / 'notes',
            ai_enabled=lambda: False, sync_ai=Mock(), on_change=Mock(), on_ready=Mock())

    def test_user_edited_markdown_is_preserved(self):
        m = self.store.create()
        m['turns'] = [{'speaker': 's1', 'start': 0, 'end': 2, 'text': 'Keep this transcript.'}]
        self.assertTrue(write_markdown(m, self.root / 'notes'))
        note = Path(m['markdown'])
        note.write_text('My own meeting notes\n', encoding='utf-8')
        m['title'] = 'A different title'
        self.assertFalse(write_markdown(m, self.root / 'notes'))
        self.assertEqual('My own meeting notes\n', note.read_text(encoding='utf-8'))

    def test_unedited_markdown_can_be_updated_on_windows(self):
        m = self.store.create()
        m['speakers'] = {'s1': 'Speaker 1'}
        m['turns'] = [{'speaker': 's1', 'start': 0, 'end': 2, 'text': 'Hello.'}]
        write_markdown(m, self.root / 'notes')
        m['speakers']['s1'] = 'Alex'
        self.assertTrue(write_markdown(m, self.root / 'notes'))
        self.assertIn('**Alex**', Path(m['markdown']).read_text(encoding='utf-8'))

    def test_store_rejects_parent_paths(self):
        with self.assertRaises(ValueError):
            self.store.folder('../outside')

    def test_short_real_acknowledgment_survives_overlapping_call_word(self):
        mic = [(1.1, 1.4, ' Yes')]
        self.assertEqual(mic, remove_echo(mic, [(1.0, 1.3, ' Yes')]))

    def test_long_echo_utterance_is_removed(self):
        words = [(0.0, 0.3, ' We'), (0.4, 0.6, ' agree'), (0.7, 0.9, ' today')]
        self.assertEqual([], remove_echo(words, words))

    def test_game_policy_is_checked_between_diarization_and_transcription(self):
        engine = Mock()
        events = []
        engine.transcribe_timed.side_effect = lambda *a, **kw: events.append('transcribe') or {
            'language': 'en', 'words': [(0, 1, ' Hello')]}
        with patch('whisper_key.speaker_diarization.available', return_value=False):
            build_transcript(np.zeros(32000, dtype=np.float32), np.ones(32000, dtype=np.float32) * .1,
                             engine, before_stage=lambda: events.append('policy'))
        self.assertEqual(['policy', 'policy', 'transcribe'], events)

    def test_delete_preserves_markdown(self):
        m = self.store.create()
        write_markdown(m, self.root / 'notes')
        self.store.delete(m['id'])
        self.assertTrue(Path(m['markdown']).exists())

    def test_shutdown_interrupts_game_wait_without_inference(self):
        m = self.store.create()
        m.update(status=WAITING, step='Queued')
        self.store.save(m)
        thread = threading.Thread(target=self.controller._wait_for_game, args=(m,), daemon=True)
        thread.start()
        time.sleep(0.05)
        self.controller.shutdown()
        thread.join(timeout=0.5)
        self.assertFalse(thread.is_alive(), 'shutdown must wake a job waiting for a game')

    def test_deleted_waiting_job_never_reaches_inference(self):
        m = self.store.create()
        m.update(status=WAITING, step='Queued')
        self.store.save(m)
        # Deleting while the worker waits must not resurrect a stale in-memory record.
        self.controller.delete(m['id'])
        self.controller._game_active = lambda: False
        self.controller._process(m)
        self.controller._engine.assert_not_called()
        self.assertIsNone(self.store.load(m['id']))


if __name__ == '__main__':
    unittest.main()
