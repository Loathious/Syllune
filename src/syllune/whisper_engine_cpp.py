# whisper_engine_cpp.py
# Opt-in whisper.cpp backend (pip install 'syllune[whispercpp]'), selected
# via whisper.backend: whisper_cpp. Mirrors WhisperEngine's public API so the rest
# of the app is backend-agnostic. Better on Apple Silicon; uses GGUF models.

import logging
import os
import time
import threading
from contextlib import nullcontext
from typing import Callable, Optional

import numpy as np


class WhisperEngineCpp:
    def __init__(self,
                 model_key: str = "base.en",
                 device: str = "cpu",
                 compute_type: str = "int8",
                 language: Optional[str] = None,
                 beam_size: int = 5,
                 initial_prompt: str = "",
                 hotwords: Optional[list] = None,
                 task: str = "transcribe",
                 vad_manager=None,
                 model_registry=None,
                 log_transcriptions: bool = False,
                 languages: Optional[list] = None):

        try:
            from pywhispercpp.model import Model
        except ImportError as e:
            raise RuntimeError(
                "whisper.cpp backend requested but pywhispercpp is not installed. "
                "Install with: pip install 'syllune[whispercpp]' "
                "or pip install pywhispercpp"
            ) from e

        self.model_key = model_key
        self.device = device
        self.compute_type = compute_type
        self.language = None if language in (None, 'auto') else language
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt or None
        self.hotwords = hotwords or []
        self.task = task if task in ('transcribe', 'translate') else 'transcribe'
        self.log_transcriptions = log_transcriptions
        self.vad_manager = vad_manager
        self.registry = model_registry
        self.logger = logging.getLogger(__name__)
        self.model = None
        self._loading_thread = None
        self._progress_callback = None
        self._model_lock = threading.RLock()
        self.last_error = None
        self.last_language = None
        # Same contract as WhisperEngine: see dictation_languages.py.
        from .dictation_languages import normalize_languages
        self.allowed_languages = normalize_languages(languages)
        self.last_language_outsider = None

        self._Model = Model
        n_threads = max(1, (os.cpu_count() or 4) - 1)
        self._n_threads = n_threads

        self._load_model()

    def _load_model(self):
        print(f"🧠 Loading whisper.cpp model [{self.model_key}]...")
        print("   (Model files auto-download from ggerganov/whisper.cpp on first use)")
        try:
            self.model = self._Model(self.model_key, n_threads=self._n_threads)
            print(f"   ✓ whisper.cpp model [{self.model_key}] ready! (threads={self._n_threads})")
            self._warmup()
        except Exception as e:
            self.logger.error(f"Failed to load whisper.cpp model '{self.model_key}': {e}")
            raise

    # Mirror of WhisperEngine._language_from_allowed, using pywhispercpp's
    # auto_detect_language(), which returns probabilities for every language.
    def _language_from_allowed(self, audio_data):
        from .dictation_languages import choose_language
        if len(self.allowed_languages) == 1:
            return self.allowed_languages[0]
        try:
            _best, probabilities = self.model.auto_detect_language(audio_data)
        except Exception as e:
            self.logger.debug(f"Language detection failed, using auto: {e}")
            return None
        language, outsider = choose_language(dict(probabilities), self.allowed_languages)
        self.last_language_outsider = outsider
        return language

    def _warmup(self):
        try:
            silent = np.zeros(16000, dtype=np.float32)
            t0 = time.time()
            self.model.transcribe(silent)
            self.logger.info(f"whisper.cpp warmup in {time.time() - t0:.2f}s")
        except Exception as e:
            self.logger.debug(f"whisper.cpp warmup skipped: {e}")

    def transcribe_audio(self,
                         audio_data: Optional[np.ndarray], beam_size: int = None,
                         quick: bool = False) -> Optional[str]:
        with getattr(self, '_model_lock', nullcontext()):
            return self._transcribe_audio(audio_data, beam_size, quick)

    # Keep errors distinct from silence so the coordinator offers audio Retry.
    def _transcribe_audio(self, audio_data, beam_size=None, quick=False):
        self.last_error = None
        self.last_language = None
        # whisper.cpp has no separate language pass to fold in; quick = greedy.
        if quick and beam_size is None:
            beam_size = 1
        if audio_data is None or len(audio_data) == 0:
            self.logger.warning("No audio data to transcribe")
            return None

        try:
            if self.vad_manager and self.vad_manager.is_available():
                if not self.vad_manager.check_audio_for_speech(audio_data):
                    print("   ✗ No speech detected, skipping transcription")
                    return None

            if len(audio_data.shape) > 1:
                audio_data = audio_data.flatten()
            audio_data = audio_data.astype(np.float32)

            # Always pass both: pywhispercpp defaults language to 'en' (which
            # translates any other language), and it keeps params set by a
            # previous call, so a per-app translate would stick otherwise.
            self.last_language_outsider = None
            language = self.language
            if language is None and self.allowed_languages:
                language = self._language_from_allowed(audio_data)
            kwargs = {
                'language': language or 'auto',
                'translate': self.task == 'translate',
            }

            t0 = time.time()
            segments = self.model.transcribe(audio_data, **kwargs)
            self.last_language = language

            parts = []
            for s in segments:
                text_chunk = getattr(s, 'text', None) or (s if isinstance(s, str) else '')
                parts.append(text_chunk.strip())
            # whisper.cpp segments carry no leading space, unlike faster-whisper's
            transcribed_text = ' '.join(p for p in parts if p)

            elapsed = time.time() - t0
            print(f"   ✓ Transcription completed in {elapsed:.1f} seconds")

            if self.log_transcriptions:
                self.logger.info(f"Transcribed: '{transcribed_text}'")
            else:
                self.logger.info(f"Transcribed {len(transcribed_text)} chars")

            return transcribed_text or None

        except Exception as e:
            self.last_error = str(e) or e.__class__.__name__
            self.logger.error(f"Transcription failed: {e}", exc_info=True)
            print(f"❌ Transcription failed: {e}")
            return None

    def change_model(self,
                      new_model_key: str,
                      progress_callback: Optional[Callable[[str], None]] = None) -> bool:
        # Progress observers do not participate in the model transaction.
        def notify(message):
            if progress_callback:
                try:
                    progress_callback(message)
                except Exception:
                    self.logger.warning('Model progress observer failed', exc_info=True)

        with getattr(self, '_model_lock', nullcontext()):
            if new_model_key == self.model_key and self.model is not None:
                notify('Model already loaded')
                return True
            try:
                notify(f"Loading whisper.cpp model {new_model_key}...")
                replacement = self._Model(new_model_key, n_threads=self._n_threads)
                self.model, self.model_key = replacement, new_model_key
                self._warmup()
            except Exception as e:
                self.logger.error(f"Model change failed: {e}")
                notify(f"Model change failed: {e}")
                return False
        notify(f"Model {new_model_key} ready")
        return True

    def is_loading(self) -> bool:
        return False
