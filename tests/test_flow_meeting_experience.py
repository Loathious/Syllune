# Meeting experience tests use generated audio and disposable folders only.
# They verify complete durable transcripts without opening devices or models.
import tempfile
import unittest
import wave
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from syllune.meetings import MeetingController, MeetingStore, READY, build_transcript
from syllune.meeting_recorder import MeetingRecorder


class MeetingExperienceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MeetingStore(self.root / 'data')
        self.controller = MeetingController(self.store, engine=Mock(), editor=Mock(),
            game_active=lambda: False, mic_device=lambda: None, notes_folder=lambda: self.root / 'notes',
            ai_enabled=lambda: True, sync_ai=Mock(), on_change=Mock(), on_ready=Mock())
        self.meeting = self.store.create()
        self.turns = [{'speaker': 'you', 'start': 0, 'end': .4, 'text': 'Yes.'},
                      {'speaker': 's1', 'start': 65, 'end': 66, 'text': 'The final answer.'}]
        self.result = {'turns': self.turns, 'speakers': {'you': 'You', 's1': 'Speaker 1'}, 'language': 'en', 'source': 'call'}

    def audio(self, name, seconds=1, amplitude=3000):
        path = self.store.folder(self.meeting['id']) / name
        with wave.open(str(path), 'wb') as output:
            output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            output.writeframes(np.full(int(seconds * 16000), amplitude, dtype=np.int16).tobytes())
        return path

    def process(self):
        with patch('syllune.meetings.build_transcript', return_value=self.result), \
                patch('syllune.meetings.summarize', return_value=None):
            self.controller._process(self.meeting)

    def test_retains_both_original_tracks_and_chunked_playback(self):
        mic, system = self.audio('mic.wav'), self.audio('system.wav', seconds=1.5)
        self.process()
        self.assertTrue(mic.exists())
        self.assertTrue(system.exists())
        tracks = self.controller.audio_paths(self.meeting['id'])
        self.assertEqual({'mic', 'system', 'playback'}, set(tracks))
        with wave.open(tracks['playback']['path']) as mixed:
            self.assertEqual(24000, mixed.getnframes())
        self.assertTrue(self.controller.get(self.meeting['id'])['audio_retained'])
        self.store.delete_audio(self.meeting['id'])
        self.assertFalse(any(p.exists() for p in [mic, system, Path(tracks['playback']['path'])]))

    def test_summary_exception_cannot_lose_canonical_or_exported_transcript(self):
        self.audio('mic.wav')
        def fail_summary(*_):
            saved = self.store.load(self.meeting['id'])
            self.assertEqual(self.turns, saved['turns'])
            self.assertTrue(Path(saved['transcript_exports']['conversation']['path']).exists())
            raise RuntimeError('Model unavailable')
        with patch('syllune.meetings.build_transcript', return_value=self.result), \
                patch('syllune.meetings.summarize', side_effect=fail_summary):
            self.controller._process(self.meeting)
        saved = self.store.load(self.meeting['id'])
        self.assertEqual(READY, saved['status'])
        self.assertEqual(self.turns, saved['turns'])
        self.assertIn('Model unavailable', saved['error'])

    def test_full_long_quiet_tracks_are_sent_to_timed_engine(self):
        engine = Mock()
        engine.transcribe_timed.side_effect = lambda audio, **_: {'language': 'en', 'words': [(len(audio) / 16000 - .3, len(audio) / 16000, ' final')]}
        mic = np.full(16000 * 66, .0001, dtype=np.float32)
        system = np.full(16000 * 70, .0002, dtype=np.float32)
        with patch('syllune.speaker_diarization.available', return_value=False):
            result = build_transcript(mic, system, engine)
        self.assertEqual([len(system), len(mic)], [len(call.args[0]) for call in engine.transcribe_timed.call_args_list])
        self.assertEqual(70, max(turn['end'] for turn in result['turns']))

    def test_subsecond_quiet_voiced_track_is_not_dropped(self):
        engine = Mock()
        engine.transcribe_timed.return_value = {'language': 'en', 'words': [(0, .2, ' yes')]}
        with patch('syllune.speaker_diarization.available', return_value=False):
            result = build_transcript(np.full(3200, .0001, dtype=np.float32), None, engine)
        self.assertEqual('yes', result['turns'][0]['text'])

    def test_conversation_and_individual_speaker_exports_and_filter(self):
        self.audio('mic.wav')
        self.process()
        exports = self.controller.get(self.meeting['id'])['transcript_exports']
        self.assertEqual({'conversation', 'you', 's1'}, set(exports))
        for key, name in [('conversation', 'conversation.md'), ('you', 'speaker-you.md'), ('s1', 'speaker-s1.md')]:
            path = Path(exports[key]['path'])
            self.assertEqual(self.root / 'notes' / 'Transcripts' / self.meeting['id'] / name, path)
            self.assertNotIn(b'\r\n', path.read_bytes())
        transcript = self.controller.transcript(self.meeting['id'], 's1')
        self.assertEqual([self.turns[1]], transcript['turns'])
        self.assertIn('The final answer.', transcript['text'])
        self.assertNotIn('Yes.', transcript['text'])
        with self.assertRaises(ValueError):
            self.controller.transcript(self.meeting['id'], '../escape')

    def test_rename_preserves_edited_transcripts_and_refreshes_unedited_files(self):
        self.audio('mic.wav')
        self.process()
        exports = self.controller.get(self.meeting['id'])['transcript_exports']
        edited = Path(exports['conversation']['path'])
        edited.write_bytes(b'My own transcript\n')
        result = self.controller.rename_speaker(self.meeting['id'], 's1', 'Alex')
        self.assertEqual(b'My own transcript\n', edited.read_bytes())
        self.assertTrue(result['transcript_kept'])
        self.assertIn('Alex', Path(exports['s1']['path']).read_text(encoding='utf-8'))
        self.assertIn('Alex', self.controller.transcript(self.meeting['id'], 's1')['text'])
        self.controller.delete(self.meeting['id'])
        self.assertTrue(edited.exists())

    def test_short_meeting_audio_is_kept_until_manual_deletion(self):
        path = self.audio('mic.wav', seconds=.25)
        self.controller._active = self.meeting
        self.controller._recorder = Mock(stop=Mock(return_value=.25), warnings=[])
        stopped = self.controller.stop_meeting()
        self.assertIsNotNone(stopped)
        self.assertTrue(path.exists())

    def test_capture_warnings_are_retained_in_meeting(self):
        self.audio('mic.wav')
        self.controller._active = self.meeting
        self.controller._recorder = Mock(stop=Mock(return_value=5), warnings=['Microphone input overflow'])
        stopped = self.controller.stop_meeting()
        self.assertEqual(['Microphone input overflow'], stopped['capture_warnings'])

    def test_writer_closes_track_even_when_final_padding_fails(self):
        recorder = MeetingRecorder(self.root)
        recorder._started = time.monotonic()
        track = Mock()
        track.pad_to.side_effect = OSError('Disk full')
        recorder._tracks = {'mic': track}
        recorder._stop.set()
        recorder._threads = [threading.current_thread()]
        recorder._write_loop()
        track.close.assert_called_once()

    def test_recovery_marks_interruption_and_uses_longer_system_track(self):
        self.audio('mic.wav', seconds=1)
        self.audio('system.wav', seconds=2)
        self.controller._recover()
        saved = self.store.load(self.meeting['id'])
        self.assertEqual(2, saved['duration'])
        self.assertTrue(saved['capture_warnings'])

    def test_failed_capture_start_preserves_partially_recorded_audio(self):
        recorder = Mock(warnings=['System audio unavailable'])
        def fail_start():
            folder = recorder.folder
            with wave.open(str(folder / 'mic.wav'), 'wb') as output:
                output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                output.writeframes(b'\x01\x00' * 8000)
            raise RuntimeError('System audio unavailable')
        def create_recorder(folder, _device):
            recorder.folder = folder
            recorder.start.side_effect = fail_start
            return recorder
        with patch('syllune.meeting_recorder.MeetingRecorder', side_effect=create_recorder):
            with self.assertRaises(RuntimeError):
                self.controller.start_meeting()
        failed = [m for m in self.store.all() if m['id'] != self.meeting['id']]
        self.assertEqual(1, len(failed))
        self.assertEqual('failed', failed[0]['status'])
        self.assertTrue(self.store.has_audio(failed[0]['id']))

    def test_unrecognized_existing_transcript_file_is_preserved(self):
        from syllune.meeting_transcripts import write_transcripts
        self.meeting.update(self.result)
        folder = self.root / 'notes' / 'Transcripts' / self.meeting['id']
        folder.mkdir(parents=True)
        path = folder / 'conversation.md'
        path.write_bytes(b'Existing handwritten text\n')
        self.assertFalse(write_transcripts(self.meeting, self.root / 'notes'))
        self.assertEqual(b'Existing handwritten text\n', path.read_bytes())


if __name__ == '__main__':
    unittest.main()
