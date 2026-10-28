# Tests the setup contract with disposable settings and synthetic model files.
# Network, model activation and microphone input stay injected: no model weights
# or personal recordings are used by this suite.
import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from whisper_key.app_setup import AppSetup, MODEL_OPTIONS, model_cached, download_model
from whisper_key.config_manager import ConfigManager
from whisper_key.hub_api import HubApi


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.path_patch = patch('whisper_key.config_manager.get_user_app_data_path', return_value=str(self.root))
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.config = ConfigManager(quiet=True)
        # Keep tests usable while the coordinating task adds the shipped schema.
        defaults = {'completed': False, 'step': 0, 'model_key': 'base'}
        self.config.config.setdefault('setup', copy.deepcopy(defaults))
        self.config._defaults_baseline.setdefault('setup', copy.deepcopy(defaults))
        self.state = SimpleNamespace(
            whisper_engine=SimpleNamespace(model_key='base', model=object(), device='cpu',
                                           allowed_languages=[], language=None,
                                           is_loading=lambda: False),
            get_current_state=lambda: 'idle',
            get_available_audio_devices=lambda: [{'id': 2, 'name': 'Test microphone'}],
            get_current_audio_device_id=lambda: 2,
            request_model_change=Mock(return_value=True),
        )
        self.cached = set()
        self.download = Mock(side_effect=lambda key, progress: self.cached.add(key))
        self.setup = AppSetup(self.config, self.state,
                              cache_check=lambda key: key in self.cached,
                              downloader=self.download, disk_free=lambda: 20_000_000_000)

    def wait(self):
        self.setup.wait_for_download(2)

    def test_status_and_progress_save_never_download_or_mark_complete(self):
        self.setup.status()
        self.setup.save({'step': 2, 'model_key': 'small'})
        self.download.assert_not_called()
        self.assertFalse(self.config.config['setup']['completed'])
        self.assertEqual(2, self.config.config['setup']['step'])
        self.assertIn('small', (self.root / 'user_settings.yaml').read_text())

    def test_selection_validation_happens_before_any_settings_write(self):
        for values in ({'step': -1}, {'step': True}, {'model_key': '../anything'},
                       {'completed': True}, {'languages': ['nonsense']},
                       {'languages': 'en'}, {'mic': 999}, {'mic': True}, {'mic': []}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.setup.save(values)
        self.assertEqual(0, self.config.config['setup']['step'])

    def test_languages_apply_to_live_recognizer_and_disk(self):
        self.setup.save({'languages': ['en', 'sv', 'en']})
        self.assertEqual(['en', 'sv'], self.state.whisper_engine.allowed_languages)
        self.assertEqual(['en', 'sv'], self.config.get_whisper_config()['languages'])
        self.assertIsNone(self.state.whisper_engine.language)

    def test_finish_is_explicit_and_requires_a_ready_selected_model(self):
        with self.assertRaises(ValueError):
            self.setup.complete()
        self.cached.add('base')
        self.setup.complete()
        self.assertTrue(self.config.config['setup']['completed'])
        self.assertTrue(ConfigManager(quiet=True).config['setup']['completed'])
        # Replay/back navigation preserves an already completed guide.
        self.setup.save({'step': 0})
        self.assertTrue(self.config.config['setup']['completed'])

    def test_download_allowlist_and_disk_guard(self):
        with self.assertRaises(ValueError):
            self.setup.start_download('https://example.org/model')
        self.setup.disk_free = lambda: 1
        with self.assertRaisesRegex(ValueError, 'space'):
            self.setup.start_download('base')
        self.download.assert_not_called()

    def test_download_failure_can_be_retried_and_is_not_ready(self):
        self.download.side_effect = OSError('connection interrupted')
        self.setup.start_download('base')
        self.wait()
        self.assertEqual('error', self.setup.status()['download']['state'])
        self.assertIn('Retry', self.setup.status()['download']['error'])
        self.assertFalse(self.setup.status()['ready'])
        self.download.side_effect = lambda key, progress: self.cached.add(key)
        self.setup.start_download('base')
        self.wait()
        self.assertEqual('ready', self.setup.status()['download']['state'])
        self.assertTrue(self.setup.status()['ready'])

    def test_background_download_reports_progress_and_blocks_second_download(self):
        entered, release = threading.Event(), threading.Event()
        def download(key, progress):
            progress(25, 100)
            entered.set()
            release.wait(2)
            self.cached.add(key)
        self.setup.downloader = download
        self.setup.start_download('base')
        self.assertTrue(entered.wait(1))
        progress = self.setup.status()['download']
        self.assertEqual(25, progress['completed'])
        self.assertEqual(100, progress['total'])
        with self.assertRaises(ValueError):
            self.setup.start_download('small')
        release.set()
        self.wait()

    def test_cached_model_reuses_cache_and_load_failure_keeps_completion_false(self):
        self.cached.add('small')
        self.setup.start_download('small')
        self.wait()
        self.download.assert_not_called()
        self.state.request_model_change.assert_called_once_with('small')
        self.assertEqual('error', self.setup.status()['download']['state'])
        self.assertFalse(self.config.config['setup']['completed'])
        self.assertNotEqual('small', self.config.get_whisper_config()['model'])

    def test_busy_engine_is_never_cancelled_by_setup(self):
        self.cached.add('small')
        self.state.get_current_state = lambda: 'recording'
        self.setup.start_download('small')
        self.wait()
        self.state.request_model_change.assert_not_called()
        self.assertEqual('error', self.setup.status()['download']['state'])

    def test_active_meeting_prevents_setup_model_activation_and_microphone_test(self):
        self.state.meetings = SimpleNamespace(started_ms=lambda: 123)
        self.cached.add('small')
        self.setup.start_download('small')
        self.wait()
        self.state.request_model_change.assert_not_called()
        with self.assertRaises(ValueError):
            self.setup.mic_test('start')

    def test_successful_activation_persists_selected_model_after_runtime_ready(self):
        self.cached.add('small')
        def activate(key):
            # At the activation boundary the previous config is still intact.
            self.assertNotEqual('small', self.config.get_whisper_config()['model'])
            self.state.whisper_engine.model_key = key
            return True
        self.state.request_model_change.side_effect = activate
        self.setup.start_download('small')
        self.wait()
        self.assertTrue(self.setup.status()['ready'])
        self.assertEqual('small', ConfigManager(quiet=True).get_whisper_config()['model'])

    def test_partial_cache_directory_is_not_usable(self):
        with patch('whisper_key.app_setup.cache_root', return_value=self.root):
            snapshot = self.root / 'models--Systran--faster-whisper-base' / 'snapshots' / 'revision'
            snapshot.mkdir(parents=True)
            (snapshot / 'model.bin.incomplete').write_bytes(b'partial')
            self.assertFalse(model_cached('base'))
            for filename in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt'):
                (snapshot / filename).write_bytes(b'fixture')
            self.assertTrue(model_cached('base'))

    def test_recommendation_stays_small_on_cpu(self):
        status = self.setup.status()
        self.assertEqual('base', status['recommended'])
        self.assertEqual(set(MODEL_OPTIONS), {item['key'] for item in status['models']})

    def test_guide_uses_actual_configured_hotkeys_and_recording_mode(self):
        self.config.config['hotkey'].update(recording_hotkey='alt+space', cancel_combination='f8',
                                            recording_mode='toggle', double_tap_to_lock=False)
        keys = self.setup.status()['hotkeys']
        self.assertEqual('alt+space', keys['record'])
        self.assertEqual('f8', keys['cancel'])
        self.assertEqual('toggle', keys['mode'])
        self.assertFalse(keys['double_tap'])

    def test_real_download_adapter_is_allowlisted_and_uses_existing_cache_without_auth(self):
        progress = Mock()
        with patch('huggingface_hub.snapshot_download') as fetch:
            download_model('base', progress)
        args, kwargs = fetch.call_args
        self.assertEqual(('Systran/faster-whisper-base',), args)
        self.assertIs(False, kwargs['token'])
        self.assertIn('model.bin', kwargs['allow_patterns'])
        self.assertNotIn('local_dir', kwargs)
        bar = kwargs['tqdm_class'](total=100, unit='B')
        bar.update(25)
        progress.assert_called_with(25, 100, 'B')
        bar.close()

    def test_restart_preserves_progress_without_claiming_download_in_progress(self):
        self.setup.save({'step': 2, 'model_key': 'small'})
        restarted = ConfigManager(quiet=True)
        service = AppSetup(restarted, self.state, cache_check=lambda key: False,
                           downloader=self.download, disk_free=lambda: 10000000000)
        status = service.status()
        self.assertEqual(2, status['step'])
        self.assertEqual('small', status['model_key'])
        self.assertEqual('idle', status['download']['state'])
        self.assertFalse(status['completed'])

    def test_mic_bad_request_does_not_partly_save_a_valid_step(self):
        api = HubApi(self.state, self.config)
        self.addCleanup(lambda: api._pool.shutdown(wait=True))
        with self.assertRaises(ValueError):
            api.setup_save({'step': 4, 'mic': 999})
        self.assertEqual(0, self.config.config['setup']['step'])

    def test_mic_test_only_opens_on_deliberate_start_and_closes(self):
        stream = Mock()
        with patch('sounddevice.InputStream', return_value=stream) as create:
            self.setup.status()
            create.assert_not_called()
            self.setup.mic_test('start')
            stream.start.assert_called_once()
            self.assertTrue(self.setup.status()['mic_test']['active'])
            self.setup.mic_test('stop')
            stream.close.assert_called_once()
            self.assertFalse(self.setup.status()['mic_test']['active'])
            self.assertTrue(self.setup.status()['mic_test']['tested'])

    def test_hub_dispatch_exposes_setup_and_uses_real_validation(self):
        api = HubApi(self.state, self.config)
        self.addCleanup(lambda: api._pool.shutdown(wait=True))
        replies = []
        api._run({'id': 1, 'method': 'setup_save', 'params': {'values': {'step': 4}}}, replies.append)
        self.assertTrue(replies[-1]['ok'], replies[-1])
        self.assertEqual(4, self.config.config['setup']['step'])
        api._run({'id': 2, 'method': 'setup_download', 'params': {'model_key': 'evil/repo'}}, replies.append)
        self.assertFalse(replies[-1]['ok'])


if __name__ == '__main__':
    unittest.main()
