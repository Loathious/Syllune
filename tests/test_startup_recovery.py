# Startup recovery must work without a terminal in the installed application.
# These tests exercise prompt decisions and the real engine construction path;
# model loading is replaced only at its expensive external boundary.
import contextlib
import io
import os
import json
import socket
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class StartupRecoveryTests(unittest.TestCase):
    def test_streaming_warmup_failure_does_not_advertise_a_working_recognizer(self):
        from syllune.streaming_manager import StreamingManager
        def fail_warmup():
            raise RuntimeError('streaming runtime unavailable')
        recognizer = SimpleNamespace(load_model=lambda: True, warmup=fail_warmup)
        manager = StreamingManager(streaming_enabled=True)
        with patch('syllune.streaming_manager.StreamingRecognizer', return_value=recognizer):
            manager.initialize()
        self.assertFalse(manager.is_available())

    def test_background_streaming_is_unavailable_until_warmup_finishes(self):
        from syllune.streaming_manager import StreamingManager
        warming, release = threading.Event(), threading.Event()
        recognizer = SimpleNamespace(load_model=lambda: True,
                                     warmup=lambda: (warming.set(), release.wait(3)))
        manager = StreamingManager(streaming_enabled=True)
        with patch('syllune.streaming_manager.StreamingRecognizer', return_value=recognizer):
            worker = threading.Thread(target=manager.initialize)
            worker.start()
            try:
                self.assertTrue(warming.wait(2))
                self.assertFalse(manager.is_available())
            finally:
                release.set()
                worker.join(3)
        self.assertTrue(manager.is_available())

    def test_streaming_prepared_after_gui_start_is_attached_on_next_recording(self):
        from syllune.audio_recorder import AudioRecorder
        available = False
        rates = []
        recognizer = SimpleNamespace(set_recording_rate=rates.append, reset=lambda: None)
        streaming = SimpleNamespace(is_available=lambda: available,
                                    create_continuous_recognizer=lambda **kwargs: recognizer)
        with patch('syllune.audio_recorder.sd.query_devices', side_effect=RuntimeError('No default input device')), \
                patch.object(AudioRecorder, '_test_audio_source'), patch.object(AudioRecorder, '_start_capture'):
            recorder = AudioRecorder(on_vad_event=lambda event: None,
                                     vad_manager=SimpleNamespace(is_available=lambda: False),
                                     streaming_manager=streaming)
        available = True
        recorder.start_recording()
        self.assertIs(recorder.continuous_streaming, recognizer)
        self.assertEqual(rates, [16000])

    def test_missing_microphone_does_not_prevent_recorder_creation_for_setup(self):
        from syllune.audio_recorder import AudioRecorder
        with patch('syllune.audio_recorder.sd.query_devices', side_effect=RuntimeError('No default input device')), \
                patch.object(AudioRecorder, '_start_capture'):
            recorder = AudioRecorder(on_vad_event=lambda event: None,
                                     vad_manager=SimpleNamespace(is_available=lambda: False))
        self.assertFalse(recorder.get_recording_status())
        self.assertIsNone(recorder.device)

    def test_deferred_model_load_preserves_configured_language_routes(self):
        from syllune.whisper_engine import WhisperEngine
        primary, routed = object(), object()
        registry = SimpleNamespace(get_source=lambda key: key, is_model_cached=lambda key: True)
        engine = WhisperEngine(model_key='base', device='cpu', model_registry=registry,
                               language_models={'sv': 'swedish'}, defer_load=True)
        finished = threading.Event()
        with patch('syllune.whisper_engine.WhisperModel', side_effect=[primary, routed]), \
                patch.object(engine, '_warmup'):
            engine.change_model('base', lambda message: finished.set() if 'ready!' in message else None)
            self.assertTrue(finished.wait(3))
        self.assertIs(engine.model, primary)
        self.assertIs(engine.language_model_instances.get('sv'), routed)

    def test_installed_engine_connects_to_the_existing_gui_without_spawning_it_again(self):
        from syllune.electron_overlay import ElectronOverlay
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(2)
        messages = []
        errors = []

        def receive():
            try:
                connection, _ = listener.accept()
                with connection, connection.makefile('r', encoding='utf-8') as stream:
                    messages.extend(json.loads(stream.readline()) for _ in range(2))
                    stream.readline()  # shutdown sends quit before closing
            except Exception as error:
                errors.append(error)

        reader = threading.Thread(target=receive, daemon=True)
        reader.start()
        overlay = ElectronOverlay(lambda: 0)
        try:
            with patch.dict(os.environ, {'SYLLUNE_UI_PORT': str(listener.getsockname()[1]),
                                         'SYLLUNE_UI_TOKEN': 'test-startup-token', 'SYLLUNE_UI_PID': '123'}), \
                    patch('syllune.electron_overlay.subprocess.Popen', side_effect=AssertionError('duplicate GUI')):
                overlay.start()
                overlay.shutdown()
            reader.join(3)
            self.assertFalse(reader.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(messages[0], {'type': 'hello', 'token': 'test-startup-token'})
            self.assertEqual(messages[1]['type'], 'config')
        finally:
            listener.close()

    def test_redirected_input_never_opens_gpu_recovery_prompt(self):
        from syllune import onboarding
        with patch.object(onboarding.sys, 'stdin', io.StringIO()), \
                patch.object(onboarding, 'prompt_choice', side_effect=AssertionError('invisible prompt')), \
                contextlib.redirect_stdout(io.StringIO()):
            onboarding.handle_gpu_failure(RuntimeError('CUDA DLLs missing'), object())

    def test_redirected_input_never_runs_interactive_gpu_onboarding(self):
        from syllune import main
        config = SimpleNamespace(config={'onboarding': {'gpu': 'pending'}})
        original = {'device': 'cuda'}
        with patch.object(main.sys, 'stdin', io.StringIO()), \
                patch('syllune.hardware_detection.detect_and_print', side_effect=AssertionError('console probe')):
            self.assertIs(main.run_gpu_onboarding(config, original), original)

    def test_packaged_startup_defers_model_and_recovers_missing_gpu_libraries(self):
        from syllune import main
        from syllune.whisper_engine import WhisperEngine
        settings = {'model': 'base', 'device': 'cuda', 'compute_type': 'float16',
                    'language': 'auto', 'beam_size': 3}
        config = SimpleNamespace(config={'setup': {'completed': True}})
        registry = SimpleNamespace(is_model_cached=lambda key: True)
        with patch.dict(os.environ, {'SYLLUNE_ELECTRON_EXE': 'Syllune.exe'}), \
                patch('syllune.hardware_detection.missing_gpu_libraries', return_value=['cublas64_12.dll']), \
                patch.object(WhisperEngine, '_load_model', side_effect=AssertionError('blocks GUI')):
            engine = main.setup_whisper_engine(settings, None, registry, config_manager=config)
        self.assertIsNone(engine.model)
        self.assertEqual((engine.device, engine.compute_type), ('cpu', 'int8'))
        self.assertEqual((settings['device'], settings['compute_type']), ('cuda', 'float16'))


if __name__ == '__main__':
    unittest.main()
