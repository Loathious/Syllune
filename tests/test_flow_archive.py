# Exercise durable dictation history and retained WAVs with disposable data.
# No microphone, provider, clipboard, or user's history participates in tests.
import importlib
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from whisper_key import transcript_log


class DictationHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        context = patch.object(transcript_log, 'get_user_app_data_path', return_value=self.root)
        context.start()
        self.addCleanup(context.stop)

    def test_completed_text_has_stable_id_original_and_delivery(self):
        saved = transcript_log.record_transcript('Hello Åsa', app='editor', duration_s=1.234)
        self.assertIsInstance(saved, dict)
        self.assertRegex(saved['id'], r'^[0-9a-f]{32}$')
        self.assertEqual('inserted', saved['delivery'])
        self.assertEqual('Hello Åsa', saved['original_text'])
        self.assertEqual(saved, transcript_log.find_transcript(saved['id']))

    def test_pending_queue_survives_reload_in_file_order(self):
        first = transcript_log.record_transcript('first', delivery='pending')
        transcript_log.record_transcript('already sent')
        last = transcript_log.record_transcript('last', delivery='pending')
        importlib.reload(transcript_log)
        with patch.object(transcript_log, 'get_user_app_data_path', return_value=self.root):
            self.assertEqual([first['id'], last['id']],
                             [entry['id'] for entry in transcript_log.pending_transcripts()])
            self.assertTrue(transcript_log.update_delivery(first['id'], 'inserted'))
            self.assertEqual([last['id']], [entry['id'] for entry in transcript_log.pending_transcripts()])

    def test_edit_and_queue_variant_preserve_original(self):
        saved = transcript_log.record_transcript('Original words', original_text='Raw original words')
        self.assertTrue(transcript_log.update_transcript(saved['timestamp'], saved['text'], 'Edited words'))
        queued = transcript_log.queue_pending(saved['id'], text='Structured words')
        self.assertEqual('Raw original words', queued['original_text'])
        self.assertEqual('Structured words', queued['text'])
        self.assertEqual('pending', queued['delivery'])
        self.assertEqual(16, queued['chars'])

    def test_legacy_entry_edit_preserves_baseline_without_migration(self):
        path = self.root / 'transcripts.jsonl'
        path.write_text(json.dumps({'timestamp': '2020-01-01', 'text': 'legacy'}) + '\n', encoding='utf-8')
        self.assertTrue(transcript_log.update_transcript('2020-01-01', 'legacy', 'edited'))
        entry = transcript_log.load_transcripts()[0]
        self.assertEqual('legacy', entry['original_text'])
        self.assertNotIn('id', entry)

    def test_unlimited_history_does_not_silently_drop_entries(self):
        path = self.root / 'transcripts.jsonl'
        path.write_text('\n'.join(json.dumps({'timestamp': '2020-01-01', 'text': str(i)})
                                  for i in range(2500)) + '\n', encoding='utf-8')
        transcript_log.record_transcript('latest')
        self.assertEqual(2501, len(transcript_log.load_transcripts()))

    def test_invalid_delivery_cannot_poison_pending_queue(self):
        saved = transcript_log.record_transcript('keep', delivery='pending')
        self.assertFalse(transcript_log.update_delivery(saved['id'], 'unknown'))
        self.assertEqual('pending', transcript_log.find_transcript(saved['id'])['delivery'])

    def test_rewrite_failure_keeps_old_journal(self):
        saved = transcript_log.record_transcript('keep', delivery='pending')
        with patch('pathlib.Path.replace', side_effect=OSError('disk blocked')):
            self.assertFalse(transcript_log.update_delivery(saved['id'], 'inserted'))
        self.assertEqual('pending', transcript_log.find_transcript(saved['id'])['delivery'])

    def test_malformed_lines_and_non_objects_are_skipped(self):
        path = self.root / 'transcripts.jsonl'
        path.write_text('[]\nnull\ninvalid\n' + json.dumps({'text': 'valid'}) + '\n', encoding='utf-8')
        self.assertEqual([{'text': 'valid'}], transcript_log.load_transcripts())

    def test_empty_or_disabled_retention_returns_none(self):
        self.assertIsNone(transcript_log.record_transcript(''))
        self.assertIsNone(transcript_log.record_transcript('private', retention_days=0))
        self.assertEqual([], transcript_log.load_transcripts())

    def test_interrupted_last_line_cannot_swallow_next_completed_dictation(self):
        path = self.root / 'transcripts.jsonl'
        path.write_text('{"text":"interrupted', encoding='utf-8')
        saved = transcript_log.record_transcript('new completed dictation')
        self.assertEqual(saved, transcript_log.find_transcript(saved['id']))

    def test_missing_entry_id_cannot_resolve_legacy_history(self):
        path = self.root / 'transcripts.jsonl'
        path.write_text(json.dumps({'text': 'legacy'}) + '\n', encoding='utf-8')
        self.assertIsNone(transcript_log.find_transcript(None))

    def test_interrupted_delivery_requires_explicit_requeue(self):
        saved = transcript_log.record_transcript('possibly inserted', delivery='pending')
        self.assertTrue(transcript_log.update_delivery(saved['id'], 'delivering'))
        self.assertEqual([], transcript_log.pending_transcripts())
        self.assertEqual('delivering', transcript_log.find_transcript(saved['id'])['delivery'])
        self.assertEqual('pending', transcript_log.queue_pending(saved['id'])['delivery'])


class RecordingArchiveTests(unittest.TestCase):
    def setUp(self):
        # Import inside setup so the first failing history test shows the old contract.
        from whisper_key import recording_archive
        self.archive = recording_archive
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for module in (recording_archive, transcript_log):
            context = patch.object(module, 'get_user_app_data_path', return_value=self.root)
            context.start()
            self.addCleanup(context.stop)

    def test_audio_is_playable_mono_16k_pcm16_with_clipping(self):
        saved = self.archive.save_recording(np.array([-2., -1., 0., .5, 1., 2.], dtype=np.float32))
        self.assertRegex(saved, r'^[0-9a-f]{32}$')
        path = self.archive.recording_path(saved)
        self.assertEqual(self.root / 'dictation_audio' / f'{saved}.wav', path)
        with wave.open(str(path), 'rb') as audio:
            self.assertEqual((1, 2, 16000, 6),
                             (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()))
            self.assertEqual([-32768, -32768, 0, 16384, 32767, 32767],
                             np.frombuffer(audio.readframes(6), dtype='<i2').tolist())

    def test_lookup_and_delete_never_accept_paths_or_uppercase_ids(self):
        outside = self.root / 'keep.wav'
        outside.write_bytes(b'keep')
        for value in ('../keep', str(outside), 'A' * 32, '', None):
            self.assertIsNone(self.archive.recording_path(value))
            self.assertFalse(self.archive.delete_recording(value))
        self.assertEqual(b'keep', outside.read_bytes())

    def test_explicit_history_deletion_removes_linked_audio(self):
        saved = self.archive.save_recording(np.zeros(160, dtype=np.float32))
        entry = transcript_log.record_transcript('linked', recording_id=saved)
        self.assertTrue(transcript_log.delete_transcript(entry['timestamp'], entry['text']))
        self.assertIsNone(self.archive.recording_path(saved))

    def test_history_age_retention_does_not_expire_audio(self):
        saved = self.archive.save_recording(np.zeros(160, dtype=np.float32))
        path = self.root / 'transcripts.jsonl'
        path.write_text(json.dumps({'text': 'old', 'timestamp': '2000-01-01', 'recording_id': saved}) + '\n', encoding='utf-8')
        transcript_log.record_transcript('fresh', retention_days=1)
        self.assertEqual(['fresh'], [entry['text'] for entry in transcript_log.load_transcripts()])
        self.assertIsNotNone(self.archive.recording_path(saved))

    def test_explicit_root_roundtrip_and_deletion(self):
        folder = self.root / 'chosen-archive'
        saved = self.archive.save_recording(np.zeros((10, 1), dtype=np.float32), root=folder)
        self.assertEqual(folder / f'{saved}.wav', self.archive.recording_path(saved, root=folder))
        self.assertTrue(self.archive.delete_recording(saved, root=folder))
        self.assertFalse(self.archive.delete_recording(saved, root=folder))

    def test_failed_atomic_save_leaves_no_partial_recording(self):
        folder = self.root / 'chosen-archive'
        with patch('pathlib.Path.replace', side_effect=OSError('disk blocked')):
            with self.assertRaises(OSError):
                self.archive.save_recording(np.zeros(16, dtype=np.float32), root=folder)
        self.assertEqual([], list(folder.iterdir()))


if __name__ == '__main__':
    unittest.main()
