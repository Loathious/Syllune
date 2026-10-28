# Regression coverage for optional decoding and transactional quality changes.
# Models, recording devices and persisted settings are replaced at their boundaries.
import contextlib
import io
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from syllune.state_manager import StateManager
from syllune.whisper_engine import WhisperEngine
from syllune.whisper_engine_cpp import WhisperEngineCpp


class MemoryConfig:
    def __init__(self):
        self.values = {'quality': 'balanced', 'beam_size': 5}
        self.config = {}

    def get_whisper_config(self):
        return dict(self.values)

    def update_user_setting(self, section, key, value):
        self.values[key] = value

    def get_postprocess_config(self):
        return {}


class QualityTests(unittest.TestCase):
    def setUp(self):
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def engine(self, device='cpu'):
        with patch.object(WhisperEngine, '_load_model'), patch.object(WhisperEngine, '_load_language_models'):
            engine = WhisperEngine(model_key='large-v3-turbo', device=device)
        engine.logger = Mock()
        engine.model = Mock()
        engine.model.transcribe.return_value = (
            [SimpleNamespace(text=' Recovered words.')],
            SimpleNamespace(language='en', language_probability=1.0))
        return engine

    def state(self):
        state = object.__new__(StateManager)
        state._state_lock = threading.Lock()
        state.is_processing = state.is_model_loading = False
        state._pending_model_change = state._pending_device_change = None
        state.audio_recorder = Mock()
        state.audio_recorder.get_recording_status.return_value = False
        state.config_manager = MemoryConfig()
        state.logger = Mock()
        state.system_tray = Mock()
        state.level_overlay = Mock()
        state._update_ui_state = Mock()
        state.sync_ai_model = Mock()
        state.whisper_engine = SimpleNamespace(model_key='large-v3-turbo', model=object(), beam_size=5)
        self.requests = []

        def change(key, callback):
            self.requests.append((key, callback))
            return True

        state.whisper_engine.change_model = change
        return state

    def finish_switch(self, state, success):
        key, callback = self.requests[-1]
        if success:
            state.whisper_engine.model_key = key
        callback('Model ready!' if success else 'Failed to load model: unavailable')

    def test_optional_decoder_failure_uses_normal_decoder_with_same_audio(self):
        engine = self.engine()
        audio = np.ones(16000, dtype=np.float32)
        with patch.object(engine, '_transcribe_quick', side_effect=ValueError('unsupported generation')):
            self.assertEqual('Recovered words.', engine.transcribe_audio(audio, quick=True))
        np.testing.assert_array_equal(audio, engine.model.transcribe.call_args.args[0])
        self.assertIsNone(engine.last_error)

    def test_deferred_engine_loads_selected_default_on_request(self):
        with patch('syllune.whisper_engine.WhisperModel') as factory:
            engine = WhisperEngine(model_key='base', defer_load=True)
            factory.assert_not_called()
            self.assertIsNone(engine.model)
            engine.change_model('base')
            worker = engine._loading_thread
            if worker:
                worker.join(2)
        self.assertIsNotNone(engine.model)
        self.assertEqual('base', engine.model_key)

    def test_both_decoders_failing_preserves_actionable_error(self):
        engine = self.engine()
        engine.model.transcribe.side_effect = RuntimeError('normal decoder unavailable')
        with patch.object(engine, '_transcribe_quick', side_effect=RuntimeError('quick unavailable')):
            self.assertIsNone(engine.transcribe_audio(np.ones(16000), quick=True))
        self.assertEqual('normal decoder unavailable', engine.last_error)

    def test_quick_generation_reserves_total_sequence_length_for_long_prompt(self):
        engine = self.engine()
        engine.language = 'en'
        engine.model.feature_extractor.nb_max_frames = 3000
        engine.model.feature_extractor.return_value = np.zeros((128, 200), dtype=np.float32)
        engine.model.model.is_multilingual = True
        engine.model.max_length = 448
        engine.model.get_prompt.return_value = list(range(225))
        tokenizer = Mock(eot=50257)
        tokenizer.decode.return_value = 'Words.'

        def generate(_encoded, prompts, **kwargs):
            if kwargs['max_length'] <= len(prompts[0]):
                raise ValueError('max_length must include the prompt')
            return [SimpleNamespace(sequences_ids=[[10]])]

        engine.model.model.generate.side_effect = generate
        with patch('faster_whisper.tokenizer.Tokenizer', return_value=tokenizer), \
                patch('faster_whisper.transcribe.get_suppressed_tokens', return_value=()):
            self.assertEqual('Words.', engine._transcribe_quick(np.ones(16000))[0])

    def test_quality_change_is_instant_and_never_switches_model(self):
        state = self.state()
        state.apply_quality('best')
        self.assertEqual('best', state.current_quality())
        self.assertEqual([], self.requests)
        self.assertFalse(state.is_model_loading)
        self.assertEqual('large-v3-turbo', state.whisper_engine.model_key)
        state.apply_quality('fast')
        self.assertEqual(1, state.whisper_engine.beam_size)

    def test_quality_change_during_processing_leaves_inflight_decode_settings(self):
        state = self.state()
        state.is_processing = True
        state.apply_quality('fast')
        self.assertEqual('balanced', state.current_quality())
        self.assertEqual(5, state.whisper_engine.beam_size)

    def test_processing_completion_applies_queued_quality(self):
        state = self.state()
        state.is_processing = True
        state.apply_quality('fast')
        state._command_mode = state._note_mode = state._rephrase_mode = False
        state._rephrase_selection = ''
        state._streaming_delivery_active, state.streaming_delivery = False, None
        state.audio_feedback = Mock()
        state._transcription_pipeline(None)
        self.assertEqual('fast', state.current_quality())
        self.assertEqual(1, state.whisper_engine.beam_size)
        self.assertFalse(state.is_processing)

    def test_retry_completion_applies_queued_quality(self):
        state = self.state()
        state.is_processing = True
        state.apply_quality('fast')
        state.whisper_engine.transcribe_audio = lambda audio: None
        state.whisper_engine.last_error = 'decoder unavailable'
        state._retry_worker(np.ones(16000))
        self.assertEqual('fast', state.current_quality())
        self.assertFalse(state.is_processing)

    def test_quality_change_never_cancels_an_active_recording(self):
        state = self.state()
        state.audio_recorder.get_recording_status.return_value = True
        state.cancel_active_recording = Mock()
        state.apply_quality('best')
        state.cancel_active_recording.assert_not_called()
        self.assertEqual([], self.requests)
        self.assertEqual('balanced', state.current_quality())

    def test_cuda_switch_offloads_previous_model_and_restores_it_on_failure(self):
        engine = self.engine('cuda')
        old = engine.model
        notices = []

        def unavailable(*args, **kwargs):
            old.model.unload_model.assert_called_once_with(to_cpu=True)
            raise RuntimeError('requested model unavailable')

        with patch('syllune.whisper_engine.WhisperModel', side_effect=unavailable), \
                patch('threading.excepthook') as uncaught:
            engine.change_model('large', notices.append)
            worker = engine._loading_thread
            if worker:
                worker.join(2)
        self.assertIs(engine.model, old)
        self.assertEqual('large-v3-turbo', engine.model_key)
        old.model.load_model.assert_called_once()
        self.assertFalse(engine.is_loading())
        self.assertTrue(any('failed' in message.lower() for message in notices))
        uncaught.assert_not_called()

    def test_callback_exception_cannot_split_loaded_model_and_key(self):
        engine = self.engine()
        replacement = Mock()

        def observer(message):
            if 'ready' in message.lower():
                raise RuntimeError('UI no longer exists')

        with patch('syllune.whisper_engine.WhisperModel', return_value=replacement), \
                patch('threading.excepthook'):
            engine.change_model('large', observer)
            worker = engine._loading_thread
            if worker:
                worker.join(2)
        self.assertIs(engine.model, replacement)
        self.assertEqual('large', engine.model_key)
        self.assertFalse(engine.is_loading())

    def test_model_replacement_waits_until_lazy_decode_is_consumed(self):
        engine = self.engine()
        decoding, release, constructed, finished = (threading.Event() for _ in range(4))
        result = []

        def segments():
            decoding.set()
            if not release.wait(2):
                raise RuntimeError('test decoder was not released')
            yield SimpleNamespace(text=' Finished original decode.')

        engine.model.transcribe.return_value = (segments(), SimpleNamespace(language='en', language_probability=1.0))
        transcriber = threading.Thread(target=lambda: result.append(engine.transcribe_audio(np.ones(16000))))

        def replacement(*args, **kwargs):
            constructed.set()
            return Mock()

        def progress(message):
            if 'ready' in message.lower():
                finished.set()

        with patch('syllune.whisper_engine.WhisperModel', side_effect=replacement):
            transcriber.start()
            try:
                self.assertTrue(decoding.wait(2))
                engine.change_model('large', progress)
                self.assertFalse(constructed.wait(0.05))
            finally:
                release.set()
                transcriber.join(2)
            self.assertTrue(finished.wait(2))
        self.assertEqual(['Finished original decode.'], result)
        self.assertEqual('large', engine.model_key)

    def test_terminal_callback_can_start_next_model_without_losing_guard(self):
        engine = self.engine()
        finished = threading.Event()

        def second(message):
            if 'ready' in message.lower():
                finished.set()

        def first(message):
            if 'ready' in message.lower():
                engine.change_model('small', second)

        with patch('syllune.whisper_engine.WhisperModel', return_value=Mock()):
            engine.change_model('large', first)
            self.assertTrue(finished.wait(2))
        self.assertEqual('small', engine.model_key)
        self.assertFalse(engine.is_loading())

    def test_worker_start_failure_releases_model_load_guard_for_retry(self):
        engine = self.engine()
        with patch('threading.Thread.start', side_effect=RuntimeError('worker unavailable')):
            with self.assertRaises(RuntimeError):
                engine.change_model('large')
        finished = threading.Event()
        with patch('syllune.whisper_engine.WhisperModel', return_value=Mock()):
            self.assertTrue(engine.change_model('small', lambda message: finished.set() if 'ready' in message.lower() else None))
            self.assertTrue(finished.wait(2))
        self.assertEqual('small', engine.model_key)

    def test_optional_ai_exception_preserves_plain_transcript(self):
        state = self.state()
        state.smart_editor = Mock()
        state.smart_editor.edit.side_effect = RuntimeError('local AI unavailable')
        state._vocabulary = lambda cfg: []
        self.assertEqual('Original words.', state._smart_edit('Original words.', {}, {}, {}, {}))

    def test_optional_ai_exception_preserves_note_paragraphs(self):
        state = self.state()
        state.audio_feedback = Mock()
        state.smart_editor = Mock()
        state.smart_editor.edit.side_effect = RuntimeError('local AI unavailable')
        state._vocabulary = lambda cfg: []
        state.whisper_engine.transcribe_paragraphs = lambda audio: ['First words.', 'Second words.']
        with patch('syllune.state_manager.postprocess', side_effect=lambda text, cfg: text), \
                patch('syllune.notes.notes_folder', return_value='unused'), \
                patch('syllune.notes.save_note') as save:
            state._handle_note(np.ones(16000))
        self.assertEqual('First words.\n\nSecond words.', save.call_args.args[0])

    def test_cpp_failure_sets_error_and_same_model_finishes_callback(self):
        engine = object.__new__(WhisperEngineCpp)
        engine.logger = Mock()
        engine.vad_manager = None
        engine.language, engine.allowed_languages, engine.task = 'en', [], 'transcribe'
        engine.model_key = 'base.en'
        engine.model = Mock()
        engine.model.transcribe.side_effect = RuntimeError('decoder unavailable')
        self.assertIsNone(engine.transcribe_audio(np.ones(16000)))
        self.assertEqual('decoder unavailable', getattr(engine, 'last_error', None))
        notices = []
        engine.change_model('base.en', notices.append)
        self.assertTrue(any('loaded' in notice.lower() for notice in notices))


if __name__ == '__main__':
    unittest.main()
