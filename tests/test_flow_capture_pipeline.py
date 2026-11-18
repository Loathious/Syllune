# The real dictation pipeline must save before paste/fallback decisions.
# No model, microphone, clipboard or real app-data writes are used here.
import collections
import tempfile
import threading
import unittest
import contextlib
import io
from unittest.mock import Mock, patch
import numpy as np
from syllune.state_manager import StateManager
from syllune.transcript_log import load_transcripts


class CapturePipelineTests(unittest.TestCase):
    def setUp(self):
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__(); self.addCleanup(quiet.__exit__, None, None, None)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        context = patch('syllune.transcript_log.get_user_app_data_path', return_value=folder.name)
        context.start(); self.addCleanup(context.stop)
        for name in ('record_transcription', 'audit_record'):
            context = patch(f'syllune.state_manager.{name}')
            context.start(); self.addCleanup(context.stop)
        s = self.state = object.__new__(StateManager)
        s._state_lock = threading.Lock()
        s.is_model_loading = False
        s._command_mode = s._note_mode = s._rephrase_mode = False
        s._rephrase_selection = ''
        s._streaming_delivery_active = False
        s.streaming_delivery = None
        s._pending_model_change = s._pending_device_change = None
        s.logger = Mock()
        s.audio_feedback = s.audio_recorder = s.system_tray = s.level_overlay = Mock()
        s.audio_recorder.get_audio_duration.return_value = 1.2
        s.whisper_engine = Mock()
        s.whisper_engine.transcribe_audio.return_value = 'hello there'
        s.clipboard_manager = Mock()
        s.clipboard_manager.auto_paste = True
        s.config_manager = Mock()
        s.config_manager.config = {'history': {'retention_days': None, 'keep_audio': False}, 'audit': {}}
        s.config_manager.get_whisper_config.return_value = {}
        s.config_manager.get_postprocess_config.return_value = {}
        s.app_rules = Mock()
        s.app_rules.match_for_foreground.return_value = {}
        s.recent_transcriptions = collections.deque(maxlen=10)
        s._offer_outsider_language = s._update_ui_state = s._refresh_pending = s._maybe_restart_continuous = Mock()
        s._smart_edit = Mock()
        s._foreground_is_textable = lambda: False

    def test_missing_input_saves_pending_and_copies_without_typing(self):
        self.state.fallback_window = Mock()
        self.state._transcription_pipeline(np.zeros(19200, dtype=np.float32))
        entries = load_transcripts()
        self.assertEqual(1, len(entries))
        self.assertEqual('pending', entries[0]['delivery'])
        self.state.clipboard_manager.copy_text.assert_called_once_with('hello there')
        self.state.level_overlay.flash_copied.assert_called_once()
        self.state.clipboard_manager.deliver_transcription.assert_not_called()
        self.state.fallback_window.show.assert_not_called()
        self.state._smart_edit.assert_not_called()
        self.assertTrue(self.state.whisper_engine.transcribe_audio.call_args.kwargs['quick'])

    # "Type" delivery with clipboard copies switched off is a request to keep
    # dictation off the clipboard, and a missing text field does not override it.
    def test_clipboard_free_setups_are_not_copied_behind_their_back(self):
        self.state.clipboard_manager.silent_copy_allowed = False
        self.state._transcription_pipeline(np.zeros(19200, dtype=np.float32))
        self.state.clipboard_manager.copy_text.assert_not_called()
        self.assertEqual('pending', load_transcripts()[0]['delivery'])

    def test_failed_paste_leaves_saved_dictation_pending(self):
        self.state._foreground_is_textable = lambda: True
        self.state.clipboard_manager.deliver_transcription.return_value = False
        self.state._transcription_pipeline(np.zeros(19200, dtype=np.float32))
        entry = load_transcripts()[0]
        self.assertEqual('pending', entry['delivery'])
        self.assertEqual('hello there', entry['original_text'])

    def test_suppressed_target_still_saves_without_clipboard_side_effects(self):
        self.state.app_rules.match_for_foreground.return_value = {'suppress': True}
        self.state._transcription_pipeline(np.zeros(19200, dtype=np.float32))
        self.assertEqual('suppressed', load_transcripts()[0]['delivery'])
        self.state.clipboard_manager.copy_text.assert_not_called()


# Each quality must visibly change what happens: short messages stay instant
# in Fast and Balanced, longer ones get the AI edit in Balanced and Best, and
# Best decodes everything fully. The Short messages setting moves the line.
class QualityPolicyTests(CapturePipelineTests):
    def run_dictation(self, quality, seconds, short_message_seconds=5):
        self.state.config_manager.get_whisper_config.return_value = {
            'quality': quality, 'short_message_seconds': short_message_seconds}
        self.state.audio_recorder.get_audio_duration.return_value = seconds
        self.state._smart_edit.reset_mock()
        self.state._smart_edit.side_effect = lambda text, *args: text
        self.state._transcription_pipeline(np.zeros(16000, dtype=np.float32))
        quick = self.state.whisper_engine.transcribe_audio.call_args.kwargs['quick']
        return quick, self.state._smart_edit.called

    def test_each_quality_routes_short_and_long_dictations(self):
        expected = {
            ('fast', 2): (True, False), ('fast', 20): (False, False),
            ('balanced', 2): (True, False), ('balanced', 20): (False, True),
            ('best', 2): (False, True), ('best', 20): (False, True),
        }
        for (quality, seconds), outcome in expected.items():
            with self.subTest(quality=quality, seconds=seconds):
                self.assertEqual(outcome, self.run_dictation(quality, seconds))

    def test_short_messages_setting_moves_the_cutoff(self):
        self.assertEqual((True, False), self.run_dictation('balanced', 7, short_message_seconds=8))
        self.assertEqual((False, True), self.run_dictation('balanced', 2, short_message_seconds=0))
