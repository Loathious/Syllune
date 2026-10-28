# Resumable first-run setup, with explicit local-model installation and a
# short, opt-in microphone meter. Settings use ConfigManager; model files use
# the recognizer's existing Hugging Face cache. No speech is retained here.
# An existing install keeps its working model: setup never swaps it unasked.
import shutil
import io
import threading
import time
from pathlib import Path

from . import transcript_log
from .dictation_languages import LANGUAGE_NAMES


# These sources match faster-whisper's shipped model map. Renderer input can
# select a key, never an arbitrary repository, URL, filename or destination.
MODEL_OPTIONS = {
    'base': {'label': 'Base', 'repo': 'Systran/faster-whisper-base', 'bytes': 150_000_000,
             'hint': 'Small download and a practical starting point on CPU.'},
    'small': {'label': 'Small', 'repo': 'Systran/faster-whisper-small', 'bytes': 500_000_000,
              'hint': 'More accurate; slower on CPU.'},
    'large-v3-turbo': {'label': 'Turbo', 'repo': 'mobiuslabsgmbh/faster-whisper-large-v3-turbo',
                       'bytes': 1_700_000_000, 'hint': 'Optional higher quality; a working GPU is recommended.'},
}
REQUIRED_FILES = ('model.bin', 'config.json', 'tokenizer.json')
DOWNLOAD_PATTERNS = ['config.json', 'preprocessor_config.json', 'model.bin', 'tokenizer.json', 'vocabulary.*']
LAST_STEP = 3                    # Welcome, Microphone, Speech model, Try it


def cache_root():
    # Honor HF_HUB_CACHE/HF_HOME exactly as the installed recognition library.
    from huggingface_hub.constants import HF_HUB_CACHE
    return Path(HF_HUB_CACHE)


def model_cached(key):
    option = MODEL_OPTIONS[key]
    snapshots = cache_root() / ('models--' + option['repo'].replace('/', '--')) / 'snapshots'
    if not snapshots.is_dir():
        return False
    for snapshot in snapshots.iterdir():
        try:
            files = [snapshot / name for name in REQUIRED_FILES]
            vocabulary = list(snapshot.glob('vocabulary.*'))
            if vocabulary and all(path.is_file() and path.stat().st_size > 0 for path in [*files, vocabulary[0]]):
                return True
        except OSError:
            continue
    return False


def free_cache_space():
    # Check the nearest existing parent without creating a cache on read-only
    # status calls. Downloads may need both partial files and final blobs.
    location = cache_root()
    while not location.exists() and location != location.parent:
        location = location.parent
    return shutil.disk_usage(location).free


def download_model(key, progress):
    from huggingface_hub import snapshot_download
    from tqdm.auto import tqdm

    class SetupProgress(tqdm):
        # Hugging Face reports actual transfer progress through this class;
        # older installed versions report completed files instead of bytes.
        def __init__(self, *args, **kwargs):
            # Fully initialize tqdm: the installed Hub adapter reads fields
            # that disabled tqdm omits. Render into memory, never the console.
            kwargs['disable'] = False
            kwargs['file'] = io.StringIO()
            super().__init__(*args, **kwargs)

        def update(self, amount=1):
            super().update(amount)
            progress(self.n, self.total or 0, self.unit or 'files')

    snapshot_download(MODEL_OPTIONS[key]['repo'], allow_patterns=DOWNLOAD_PATTERNS,
                      cache_dir=str(cache_root()), token=False, tqdm_class=SetupProgress)


class AppSetup:
    def __init__(self, config, state, *, cache_check=None, downloader=None, disk_free=None, write_lock=None):
        self.config = config
        self.state = state
        self.cache_check = cache_check or model_cached
        self.downloader = downloader or download_model
        self.disk_free = disk_free or free_cache_space
        self.write_lock = write_lock or threading.RLock()
        self._lock = threading.RLock()
        self._thread = None
        self._download = {'state': 'idle', 'model_key': '', 'completed': 0,
                          'total': 0, 'unit': 'B', 'error': ''}
        self._mic_stream = None
        self._mic_timer = None
        self._mic = {'active': False, 'tested': False, 'level': 0, 'peak': 0, 'heard': False, 'error': ''}

    def _settings(self):
        return self.config.config.get('setup') or {}

    # The model the guide shows as chosen: an explicit setup choice, else the
    # model already in use (which may be one setup does not offer, such as a
    # Swedish KB-Whisper model chosen in Settings).
    def _selected(self):
        key = self._settings().get('model_key')
        if key in MODEL_OPTIONS:
            return key
        return str(self.config.get_whisper_config().get('model') or 'base')

    def _ready(self, key):
        engine = self.state.whisper_engine
        loaded = engine.model_key == key and engine.model is not None and not engine.is_loading()
        return loaded and (key not in MODEL_OPTIONS or self.cache_check(key))

    # Someone who has dictated before is updating, not installing: the guide
    # would only offer to change a setup that already works, so it is skipped.
    def _completed(self):
        setup = self._settings()
        if setup.get('completed') is True:
            return True
        try:
            used_before = transcript_log._path().stat().st_size > 0
        except OSError:
            used_before = False
        if setup.get('step') is not None or not used_before or not self._ready(self._selected()):
            return False
        with self.write_lock:
            self.config.update_user_setting('setup', 'completed', True)
        return True

    def _idle(self):
        meetings = getattr(self.state, 'meetings', None)
        return self.state.get_current_state() == 'idle' and not (meetings and meetings.started_ms())

    def status(self):
        selected = self._selected()
        engine = self.state.whisper_engine
        try:
            free_bytes = int(self.disk_free())
        except OSError:
            free_bytes = None
        # A usable, already-loaded GPU is stronger evidence than a CUDA setting
        # alone; never recommend the large download from configuration alone.
        gpu_ready = engine.device == 'cuda' and engine.model is not None
        with self._lock:
            transfer = dict(self._download)
            mic = dict(self._mic)
        hotkeys = self.config.get_hotkey_config()
        models = [{'key': key, 'label': item['label'], 'bytes': item['bytes'],
                   'required_bytes': item['bytes'] * 2 + 250_000_000,
                   'hint': item['hint'], 'cached': self.cache_check(key)}
                  for key, item in MODEL_OPTIONS.items()]
        if selected not in MODEL_OPTIONS:
            entry = (self.config.get_whisper_config().get('models') or {}).get(selected) or {}
            label = str(entry.get('label') or selected).split(' (')[0] if isinstance(entry, dict) else selected
            models.insert(0, {'key': selected, 'label': label, 'bytes': 0, 'required_bytes': 0,
                              'hint': 'The model you use now.', 'cached': True, 'current': True})
        return {
            'completed': self._completed(),
            'step': max(0, min(LAST_STEP, int(self._settings().get('step') or 0))),
            'model_key': selected, 'recommended': 'large-v3-turbo' if gpu_ready else 'base',
            'models': models,
            'free_bytes': free_bytes, 'ready': bool(self._ready(selected)),
            'download': transfer, 'mic_test': mic,
            'mics': self.state.get_available_audio_devices() or [],
            'mic': self.state.get_current_audio_device_id(),
            'languages': list(self.config.get_whisper_config().get('languages') or []),
            'language_options': [{'value': code, 'label': name} for code, name in LANGUAGE_NAMES.items()],
            'hotkeys': {'record': str(hotkeys.get('recording_hotkey') or 'Ctrl+Win'),
                        'stop': str(hotkeys.get('stop_key') or 'Ctrl'),
                        'cancel': str(hotkeys.get('cancel_combination') or 'Esc'),
                        'mode': str(hotkeys.get('recording_mode') or 'push_to_talk'),
                        'double_tap': bool(hotkeys.get('double_tap_to_lock'))},
        }

    def validate(self, values):
        if not isinstance(values, dict) or set(values) - {'step', 'model_key', 'languages', 'mic'}:
            raise ValueError('Unknown setup option')
        if 'step' in values and (type(values['step']) is not int or not 0 <= values['step'] <= LAST_STEP):
            raise ValueError('Choose a valid setup step')
        if 'model_key' in values:
            self._validate_model(values['model_key'])
        if 'languages' in values:
            languages = values['languages']
            if (not isinstance(languages, list) or len(languages) > 100
                    or any(not isinstance(code, str) or code not in LANGUAGE_NAMES for code in languages)):
                raise ValueError('Choose supported spoken languages')
        if 'mic' in values:
            devices = {device['id'] for device in self.state.get_available_audio_devices() or []}
            if (isinstance(values['mic'], bool) or not isinstance(values['mic'], (str, int))
                    or values['mic'] not in devices):
                raise ValueError("That microphone isn't connected")
            if not self._idle():
                raise ValueError('Finish the current recording, meeting or dictation before changing microphone')

    def save(self, values):
        self.validate(values)
        # Validate the full request before applying any part of it. Microphone
        # changes use HubApi's existing setter before reaching this method.
        for key in ('step', 'model_key'):
            if key in values:
                self.config.update_user_setting('setup', key, values[key])
        if 'languages' in values:
            languages = list(dict.fromkeys(values['languages']))
            self.config.update_user_setting('whisper', 'languages', languages)
            self.config.update_user_setting('whisper', 'language', 'auto')
            self.state.whisper_engine.allowed_languages = languages
            self.state.whisper_engine.language = None
        return self.status()

    def _validate_model(self, key):
        # Keeping the model already in use is always allowed.
        if isinstance(key, str) and key not in MODEL_OPTIONS and key == self.config.get_whisper_config().get('model'):
            return
        if not isinstance(key, str) or key not in MODEL_OPTIONS:
            raise ValueError('Choose Base, Small or Turbo')
        # A user-customized registry may remap a familiar key to another repo.
        # Do not quietly download one source and then load another.
        item = (self.config.get_whisper_config().get('models') or {}).get(key) or {}
        if not isinstance(item, dict) or item.get('source', key) not in (key, MODEL_OPTIONS[key]['repo']):
            raise ValueError('This model has a custom source. Use Advanced settings instead.')

    def start_download(self, key):
        self._validate_model(key)
        if key not in MODEL_OPTIONS:
            raise ValueError('This model is already installed')
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise ValueError('A model is already being prepared. Wait for it to finish.')
            required = MODEL_OPTIONS[key]['bytes'] * 2 + 250_000_000
            if not self.cache_check(key) and self.disk_free() < required:
                raise ValueError('Not enough free disk space. Free some space, then Retry.')
            self.config.update_user_setting('setup', 'model_key', key)
            self._download = {'state': 'downloading', 'model_key': key, 'completed': 0,
                              'total': 0, 'unit': 'B', 'error': ''}
            self._thread = threading.Thread(target=self._prepare, args=(key,),
                                            name='setup-model', daemon=True)
            self._thread.start()
        return self.status()

    def _progress(self, completed, total, unit='B'):
        with self._lock:
            self._download.update(completed=max(0, completed), total=max(0, total), unit=unit)

    def _prepare(self, key):
        try:
            if not self.cache_check(key):
                self.downloader(key, self._progress)
            if not self.cache_check(key):
                raise ValueError('The model download is incomplete')
            with self._lock:
                self._download['state'] = 'loading'
            if not self._ready(key):
                if not self._idle():
                    raise ValueError('Finish the current recording or dictation first')
                if not self.state.request_model_change(key):
                    raise ValueError('The model could not be activated')
                deadline = time.monotonic() + 180
                while (self.state.whisper_engine.is_loading()
                       or self.state.get_current_state() in ('recording', 'processing', 'model_loading')):
                    if time.monotonic() > deadline:
                        raise ValueError('Model loading took too long')
                    time.sleep(0.05)
            if not self._ready(key):
                raise ValueError('The downloaded model could not load; your previous model is unchanged')
            # Persist only after actual runtime success. Routing remains a
            # separate advanced choice; setup never installs additional models.
            with self.write_lock:
                self.config.update_user_setting('whisper', 'model', key)
            with self._lock:
                self._download['state'] = 'ready'
        except Exception:
            # Network exceptions can contain URLs, credentials or private cache
            # paths. Return a bounded, useful error instead of that raw detail.
            with self._lock:
                self._download.update(state='error', error=(
                    'Could not prepare the model. Check your connection, free disk space and that dictation is idle, '
                    'then Retry. Downloaded parts will be reused; your previous model is kept.'))

    def wait_for_download(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    def complete(self):
        if not self._ready(self._selected()):
            raise ValueError('Prepare a speech model before finishing. You can close setup and return later.')
        self.mic_test('stop')
        self.config.update_user_setting('setup', 'step', LAST_STEP)
        self.config.update_user_setting('setup', 'completed', True)
        return self.status()

    def mic_test(self, action):
        if action not in ('start', 'stop'):
            raise ValueError('Microphone test action is start or stop')
        with self._lock:
            if action == 'stop':
                if self._mic_timer:
                    self._mic_timer.cancel()
                    self._mic_timer = None
                stream, self._mic_stream = self._mic_stream, None
                self._mic['active'] = False
                if stream:
                    self._mic['tested'] = True
                    try:
                        stream.stop()
                    finally:
                        stream.close()
                return dict(self._mic)
            if self._mic_stream:
                return dict(self._mic)
            if not self._idle():
                raise ValueError('Finish the current recording or dictation before testing the microphone')
            import sounddevice as sd
            import numpy as np

            def level(data, frames, timing, status):
                peak = float(np.max(np.abs(data))) if data.size else 0
                self._mic.update(level=min(1, peak), peak=max(self._mic['peak'], peak),
                                 heard=self._mic['heard'] or peak > 0.01)

            self._mic = {'active': False, 'tested': False, 'level': 0, 'peak': 0, 'heard': False, 'error': ''}
            stream = None
            try:
                device = self.state.get_current_audio_device_id()
                stream = sd.InputStream(device=None if device == 'default' else device,
                                        channels=1, dtype='float32', callback=level)
                stream.start()
                self._mic_stream = stream
                self._mic['active'] = True
                self._mic_timer = threading.Timer(15, lambda: self.mic_test('stop'))
                self._mic_timer.daemon = True
                self._mic_timer.start()
            except Exception:
                if stream:
                    stream.close()
                self._mic['error'] = 'Microphone could not open. Check Windows microphone permission and select another input, then Retry.'
            return dict(self._mic)
