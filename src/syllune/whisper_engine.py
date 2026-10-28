# whisper_engine.py
# The default speech-recognition backend, wrapping faster-whisper (CTranslate2).
# Loads the model once at startup and warms it with a dummy transcription so the
# user's first real recording isn't slowed by lazy initialisation. Model switches
# happen on a background thread with progress callbacks so the UI stays live.
# `whisper_engine_cpp.py` mirrors this class's API for the opt-in whisper.cpp
# backend — keep the two signatures in sync.

import logging
import time
import threading
from contextlib import nullcontext
from functools import wraps
from typing import Optional, Callable

import numpy as np

from .utils import import_ctranslate2_without_optional_backends
# Before faster_whisper, which imports ctranslate2: see the helper for why.
import_ctranslate2_without_optional_backends()
from faster_whisper import WhisperModel  # noqa: E402

# Approximate on-disk download sizes for the standard Whisper models, shown in
# the first-run download message so the wait is expected, not mysterious. Keyed
# by the leading size token (handles ".en" and "large-v3" style keys). Unknown
# models just omit the size hint.
_MODEL_SIZE_HINTS = {
    'tiny': '~75 MB',
    'base': '~141 MB',
    'small': '~465 MB',
    'medium': '~1.5 GB',
    'large': '~3 GB',
    'distil': '~1.5 GB',
}


def _model_size_hint(model_key: str) -> str:
    key = (model_key or '').lower()
    for prefix, size in _MODEL_SIZE_HINTS.items():
        if key.startswith(prefix):
            return size
    return ''


# Keep lazy segment iteration inside the same lock as model replacement.
def _serialize_model_use(method):
    @wraps(method)
    def serialized(self, *args, **kwargs):
        with getattr(self, '_model_lock', nullcontext()):
            return method(self, *args, **kwargs)
    return serialized



# Words every dictation is biased toward, on top of your own dictionary. They
# never show up in the dictionary list; "Syllune" is here so the app's own
# name is never heard as "Sylvan" or "silly moon".
BUILT_IN_HOTWORDS = ('Syllune',)


def join_hotwords(words) -> str:
    merged = list(words or ())
    merged += [word for word in BUILT_IN_HOTWORDS if word.lower() not in {w.lower() for w in merged}]
    return ", ".join(merged)

class WhisperEngine:
    def __init__(self,
                 model_key: str = "tiny",
                 device: str = "cpu",
                 compute_type: str = "int8",
                 language: str = None,
                 beam_size: int = 5,
                 initial_prompt: str = "",
                 hotwords: list = None,
                 task: str = "transcribe",
                 vad_manager = None,
                 model_registry = None,
                 log_transcriptions: bool = False,
                 languages: list = None,
                 language_models: dict = None,
                 defer_load: bool = False):

        self.model_key = model_key
        self.device = device
        self.compute_type = compute_type
        self.language = None if language == 'auto' else language
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt or None
        self.hotwords = join_hotwords(hotwords)
        self.task = task if task in ('transcribe', 'translate') else 'transcribe'
        self.model = None
        self.logger = logging.getLogger(__name__)
        self.registry = model_registry
        self.log_transcriptions = log_transcriptions

        self._loading_thread = None
        self._progress_callback = None
        self._model_lock = threading.RLock()
        self._loading_lock = threading.RLock()
        self.last_model_error = None

        self.vad_manager = vad_manager
        # Why the last transcribe_audio() returned nothing, when it was an
        # error rather than silence. The pipeline reads it so a CUDA fault
        # mid-session is reported as a failure, not as "no speech detected".
        self.last_error = None
        # The user's dictation languages (see dictation_languages.py). Only
        # consulted when `language` is auto; an explicit language or an app
        # rule's language always wins.
        from .dictation_languages import normalize_languages
        self.allowed_languages = normalize_languages(languages)
        # Set when the last recording was confidently in a language outside
        # that list; the pipeline offers to add it.
        self.last_language_outsider = None
        # The language the last dictation was transcribed as; the AI edit is
        # told it so it never translates.
        self.last_language = None

        # Language router: {language code: model key}. After the main model
        # detects the language, a clip in a routed language is transcribed by
        # that model instead (e.g. sv -> KB-Whisper, a Swedish fine-tune that
        # makes about half the errors of stock Whisper on Swedish). Everything
        # else, including mixed sv/en clips detected as English, stays on the
        # main model.
        self.language_model_keys = dict(language_models or {})
        self.language_model_instances = {}

        if not defer_load:
            self._load_model()
            self._load_language_models()

    def _get_model_source(self, model_key: str) -> str:
        if self.registry:
            return self.registry.get_source(model_key)
        return model_key

    def _is_model_cached(self, model_key: str = None) -> bool:
        if model_key is None:
            model_key = self.model_key
        if self.registry:
            return self.registry.is_model_cached(model_key)
        return False

    def _load_model(self):
        try:
            print(f"🧠 Loading Whisper AI model [{self.model_key}]...")

            was_cached = self._is_model_cached()
            if not was_cached:
                size = _model_size_hint(self.model_key)
                size_note = f" ({size})" if size else ""
                print(f"⬇  Downloading the '{self.model_key}' model{size_note} — "
                      "first run only. This can take a few minutes; a progress bar "
                      "appears below.")

            if self.device == 'cuda':
                self._verify_gpu_runtime()

            model_source = self._get_model_source(self.model_key)
            self.model = WhisperModel(
                model_source,
                device=self.device,
                compute_type=self.compute_type
            )

            if not was_cached:
                print("\n")  # Workaround for download status bar misplacement

            print(f"   ✓ Whisper model [{self.model_key}] ready!")
            device_label = "GPU" if self.device == "cuda" else "CPU"
            print(f"   ✓ Running on {device_label} with {self.compute_type} precision")
            self._warmup()

        except Exception as e:
            self.logger.error(f"Failed to load Whisper model: {e}")
            raise

    # Fail fast when the CUDA runtime is incomplete. The model itself loads
    # without cuBLAS/cuDNN; their absence only shows on the first inference,
    # as a hang rather than an error (issue #15). Raising RuntimeError here
    # routes through the caller's GPU-failure path, which offers GPU setup or
    # CPU instead of freezing on the user's first dictation.
    def _verify_gpu_runtime(self):
        try:
            from .hardware_detection import missing_gpu_libraries
        except ImportError:
            return  # no platform backend (Linux CI): nothing to probe
        missing = missing_gpu_libraries()
        if missing:
            raise RuntimeError(
                f"CUDA libraries not found: {', '.join(missing)}. "
                "Re-run GPU setup to install them, or switch to CPU."
            )

    def _load_model_async(self,
                          new_model_key: str,
                          progress_callback: Optional[Callable[[str], None]] = None):
        # UI observers cannot turn a successful model load into a partial rollback.
        def notify(message):
            if progress_callback:
                try:
                    progress_callback(message)
                except Exception:
                    self.logger.warning('Model progress observer failed', exc_info=True)

        def _background_loader():
            # Dictation, notes and meeting batches must finish before offloading
            # the old model. Keep its CPU weights until a replacement succeeds.
            with self._model_lock:
                old_model = self.model
                offloaded = False
                self.last_model_error = None
                try:
                    notify("Checking model cache...")
                    was_cached = self._is_model_cached(new_model_key)
                    notify("Loading cached model..." if was_cached else "Downloading model...")
                    model_source = self._get_model_source(new_model_key)
                    if old_model is not None and self.device == 'cuda':
                        old_model.model.unload_model(to_cpu=True)
                        offloaded = True
                    replacement = WhisperModel(model_source, device=self.device,
                                               compute_type=self.compute_type)
                    self.model, self.model_key = replacement, new_model_key
                    terminal_message = "Model ready!"
                except Exception as error:
                    self.last_model_error = str(error) or error.__class__.__name__
                    if offloaded:
                        try:
                            old_model.model.load_model()
                        except Exception as rollback_error:
                            self.model = None
                            self.last_model_error += f"; previous model could not be restored: {rollback_error}"
                    terminal_message = f"Failed to load model: {self.last_model_error}"
                    self.logger.error(terminal_message, exc_info=True)
            # Clear the guard before notifying: the terminal callback can submit
            # a newer queued selection without it being silently rejected.
            with self._loading_lock:
                self._loading_thread = None
                self._progress_callback = None
            notify(terminal_message)

        with self._loading_lock:
            if self._loading_thread is not None:
                self.logger.warning("Model loading already in progress")
                return False
            self._progress_callback = progress_callback
            self._loading_thread = threading.Thread(target=_background_loader, daemon=True,
                                                    name='speech-model-load')
            try:
                self._loading_thread.start()
            except Exception:
                self._loading_thread = None
                self._progress_callback = None
                raise
        return True
    
    def is_loading(self) -> bool:
        thread = self._loading_thread
        return thread is not None and thread.is_alive()
    

    def _warmup(self, model=None):
        try:
            silent = np.zeros(16000, dtype=np.float32)
            t0 = time.time()
            segments, _ = (model or self.model).transcribe(
                silent, language=self.language, beam_size=1, vad_filter=False
            )
            for _ in segments:
                pass
            self.logger.info(f"Whisper warmup completed in {time.time() - t0:.2f}s")
        except Exception as e:
            self.logger.debug(f"Whisper warmup skipped: {e}")

    # Loads every routed model next to the main one. A routed model that fails
    # to load is skipped (its language falls back to the main model) rather
    # than taking the whole app down.
    def _load_language_models(self):
        for language, key in self.language_model_keys.items():
            if not key or key == self.model_key:
                continue
            try:
                print(f"🧠 Loading [{key}] for language '{language}'...")
                if not self._is_model_cached(key):
                    print(f"⬇  Downloading the '{key}' model — first run only.")
                model = WhisperModel(self._get_model_source(key),
                                     device=self.device, compute_type=self.compute_type)
                self._warmup(model)
                self.language_model_instances[language] = model
                print(f"   ✓ [{key}] ready for '{language}'")
            except Exception as e:
                self.logger.error(f"Could not load routed model {key} for {language}: {e}")
                print(f"   ✗ [{key}] failed to load; '{language}' uses [{self.model_key}]")

    def _model_for(self, language: Optional[str]):
        return self.language_model_instances.get(language) or self.model

    def _detect_language(self, audio_data: np.ndarray) -> Optional[str]:
        if self.language is not None:
            return self.language
        if self.allowed_languages:
            return self._language_from_allowed(audio_data)
        if self.language_model_instances:
            try:
                top, _prob, _all = self.model.detect_language(audio_data)
                return top
            except Exception as e:
                self.logger.debug(f"Language detection failed: {e}")
        return None

    # Mono float32 @ 16 kHz in, plain text out (None/'' when there's no speech).
    # An optional VAD pre-check short-circuits silent clips so we don't pay for a
    # full decode on an accidental hotkey press. Callers rely on this contract —
    # whisper_engine_cpp and system_audio both feed it the same array shape.
    # `quick` is the short-clip path: one encoder pass for both language and
    # text, greedy decoding, main model only (see _transcribe_quick).
    @_serialize_model_use
    def transcribe_audio(self,
                         audio_data: np.ndarray, beam_size: int = None,
                         quick: bool = False) -> Optional[str]:
        if self.model is None:
            self.last_error = getattr(self, 'last_model_error', None) or 'No speech model is loaded. Choose a model in Settings and retry.'
            self.logger.warning(f"Recording dropped: {self.last_error}")
            return None
        
        if audio_data is None or len(audio_data) == 0:
            self.logger.warning("No audio data to transcribe")
            return None

        self.last_error = None
        self.last_language_outsider = None
        self.last_language = None
        try:
            speech_detected = True
            if self.vad_manager and self.vad_manager.is_available():
                speech_detected = self.vad_manager.check_audio_for_speech(audio_data)
            
            if not speech_detected:
                print("   ✗ No speech detected, skipping transcription")
                return None
                       
            start_time = time.time() # Time transcription for user feedback
            
            # Prep audio for faster-whisper
            if len(audio_data.shape) > 1:
                audio_data = audio_data.flatten()
            
            audio_data = audio_data.astype(np.float32)

            quick_result = None
            if quick and self._can_transcribe_quick(audio_data):
                try:
                    quick_result = self._transcribe_quick(audio_data)
                except Exception:
                    self.logger.warning('Quick decode failed; retrying normal transcription', exc_info=True)
                    self.last_language_outsider = None
            if quick_result is not None:
                transcribed_text, language, confidence = quick_result
                detected_language = language
            else:
                language = self._detect_language(audio_data)
                model = self._model_for(language)

                transcribe_kwargs = dict(
                    beam_size=self.beam_size if beam_size is None else beam_size,
                    language=language,
                    task=self.task,
                    condition_on_previous_text=False,
                )
                if self.initial_prompt:
                    transcribe_kwargs["initial_prompt"] = self.initial_prompt
                if self.hotwords:
                    transcribe_kwargs["hotwords"] = self.hotwords

                segments, info = model.transcribe(audio_data, **transcribe_kwargs)
                transcribed_text = "".join(segment.text for segment in segments)
                detected_language = info.language
                confidence = info.language_probability

            transcribed_text = transcribed_text.strip()

            end_time = time.time()
            transcription_time = end_time - start_time
            print(f"   ✓ Transcription completed in {transcription_time:.1f} seconds")

            self.last_language = language or detected_language
            self.logger.info(f"Transcription complete. Language: {detected_language} (confidence: {confidence:.2f}) - Time: {transcription_time:.2f}s")
            if self.log_transcriptions:
                self.logger.info(f"Transcribed text: '{transcribed_text}'")
            else:
                self.logger.info(f"Transcribed {len(transcribed_text)} chars")
            
            if transcribed_text:
                print(f"   ✓ Transcribed: '{transcribed_text}'")
                return transcribed_text
            else:
                self.logger.info("Transcription was empty")
                return None
                
        except Exception as e:
            self.last_error = str(e) or e.__class__.__name__
            self.logger.error(f"Transcription failed: {e}", exc_info=True)
            return None


    # =========================================================================
    # Quick path for short clips
    # =========================================================================
    # The normal path encodes the clip twice (once to detect the language,
    # once inside transcribe()), and the encoder always runs on a padded 30 s
    # window, so for a two-second clip that encoder is most of the wait. Here
    # it runs once: language and text both come from the same encoder output.
    # Swedish isn't routed to KB-Whisper on this path (that would be a second
    # encode on a bigger decoder); a few quick words trade that accuracy for
    # roughly half the latency.

    # One 30 s window, translation off: anything else takes the normal path.
    def _can_transcribe_quick(self, audio_data: np.ndarray) -> bool:
        return (self.task == 'transcribe' and self.model is not None
                and len(audio_data) <= 30 * 16000)

    # Returns (text, language, language_probability).
    def _transcribe_quick(self, audio_data: np.ndarray):
        from faster_whisper.audio import pad_or_trim
        from faster_whisper.tokenizer import Tokenizer
        from faster_whisper.transcribe import get_suppressed_tokens
        from .dictation_languages import choose_language

        model = self.model
        features = model.feature_extractor(audio_data)
        encoder_output = model.encode(pad_or_trim(features[..., :model.feature_extractor.nb_max_frames]))

        multilingual = model.model.is_multilingual
        language, confidence = self.language, 1.0
        if multilingual and language is None:
            probabilities = {token[2:-2]: prob
                             for token, prob in model.model.detect_language(encoder_output)[0]}
            if self.allowed_languages:
                language, outsider = choose_language(probabilities, self.allowed_languages)
                self.last_language_outsider = outsider
            else:
                language = max(probabilities, key=probabilities.get)
            confidence = probabilities.get(language, 0.0)
            self.logger.info(f"Dictation language: {language} (quick path)")
        if not multilingual:
            language = 'en'

        tokenizer = Tokenizer(model.hf_tokenizer, multilingual, task='transcribe', language=language)
        previous = tokenizer.encode(" " + self.initial_prompt.strip()) if self.initial_prompt else []
        prompt = model.get_prompt(tokenizer, previous, without_timestamps=True, hotwords=self.hotwords)
        result = model.model.generate(
            encoder_output, [prompt], beam_size=1,
            max_length=model.max_length,
            suppress_blank=True,
            suppress_tokens=list(get_suppressed_tokens(tokenizer, [-1])),
        )[0]
        tokens = [t for t in result.sequences_ids[0] if t < tokenizer.eot]
        return tokenizer.decode(tokens), language, confidence

    # Long-form transcription for notes: returns a list of paragraphs, split
    # wherever the speaker paused for at least `paragraph_gap_s`. Clips past
    # 30 s go through faster-whisper's batched pipeline, which decodes the
    # VAD-cut chunks in parallel on the GPU, so a few minutes of speech come
    # back in seconds rather than tens of seconds.
    @_serialize_model_use
    def transcribe_paragraphs(self, audio_data: np.ndarray,
                              paragraph_gap_s: float = 1.5) -> list:
        if self.model is None or audio_data is None or len(audio_data) == 0:
            return []
        self.last_error = None
        try:
            audio_data = audio_data.flatten().astype(np.float32)
            start_time = time.time()
            language = self._detect_language(audio_data)
            self.last_language = language
            model = self._model_for(language)

            kwargs = dict(beam_size=self.beam_size, language=language, task=self.task)
            if self.initial_prompt:
                kwargs["initial_prompt"] = self.initial_prompt
            if self.hotwords:
                kwargs["hotwords"] = self.hotwords

            if len(audio_data) > 30 * 16000:
                pipeline = self._batched_pipeline(model)
                segments, _info = pipeline.transcribe(audio_data, batch_size=8, **kwargs)
            else:
                segments, _info = model.transcribe(audio_data, vad_filter=True,
                                                   condition_on_previous_text=False, **kwargs)

            paragraphs, current, last_end = [], [], None
            for seg in segments:
                text = seg.text.strip()
                if not text:
                    continue
                if last_end is not None and seg.start - last_end >= paragraph_gap_s and current:
                    paragraphs.append(" ".join(current))
                    current = []
                current.append(text)
                last_end = seg.end
            if current:
                paragraphs.append(" ".join(current))

            elapsed = time.time() - start_time
            print(f"   ✓ Note transcribed in {elapsed:.1f}s "
                  f"({len(audio_data) / 16000:.0f}s of audio, {len(paragraphs)} paragraphs)")
            self.logger.info(f"Note transcription: {elapsed:.2f}s, language={language}")
            return paragraphs
        except Exception as e:
            self.last_error = str(e) or e.__class__.__name__
            self.logger.error(f"Note transcription failed: {e}", exc_info=True)
            return []

    # A fresh pipeline per call: it is a thin wrapper, it keeps per-run state
    # (a note and a meeting can transcribe at once), and caching one would
    # keep a model alive in VRAM after a quality switch replaced it.
    def _batched_pipeline(self, model):
        from faster_whisper import BatchedInferencePipeline
        return BatchedInferencePipeline(model=model)

    # Meeting transcription: every word with its start and end in seconds, so
    # words can be matched to speakers. Kept apart from the dictation path: a
    # meeting processed in the background must not change last_language or
    # last_error, which the next dictation reports from. Returns
    # {'language': code, 'words': [(start, end, text)]}; text keeps Whisper's
    # leading space, so joining words with '' gives normal spacing.
    @_serialize_model_use
    def transcribe_timed(self, audio_data: np.ndarray, language: str = None, before_batch=None) -> dict:
        audio_data = audio_data.flatten().astype(np.float32)
        language = language or self._meeting_language(audio_data)
        model = self._model_for(language)
        kwargs = dict(beam_size=self.beam_size, language=language, task='transcribe',
                      word_timestamps=True)
        if self.hotwords:
            kwargs['hotwords'] = self.hotwords
        started = time.time()
        segments, info = self._batched_pipeline(model).transcribe(audio_data, batch_size=8, **kwargs)
        words = []
        iterator = iter(segments)
        while True:
            # Yield to game mode before asking the lazy pipeline for more GPU work.
            if before_batch:
                before_batch()
            try:
                segment = next(iterator)
            except StopIteration:
                break
            for word in segment.words or ():
                if word.word.strip():
                    words.append((float(word.start), float(word.end), word.word))
        self.logger.info(f"Meeting track: {len(audio_data) / 16000:.0f}s of audio, "
                         f"{len(words)} words in {time.time() - started:.1f}s, language={language}")
        return {'language': language or info.language, 'words': words}

    # The language of a meeting track, judged from speech only. Dictation's
    # check reads the first 30 s, which in a meeting is often silence or
    # someone else's small talk; here four VAD-cut windows vote.
    def _meeting_language(self, audio_data: np.ndarray) -> Optional[str]:
        if self.language is not None:
            return self.language
        if len(self.allowed_languages) == 1:
            return self.allowed_languages[0]
        if not getattr(getattr(self.model, 'model', None), 'is_multilingual', True):
            return None
        try:
            _top, _prob, all_probs = self.model.detect_language(
                audio_data, vad_filter=True, language_detection_segments=4)
        except Exception as e:
            self.logger.debug(f"Meeting language detection failed: {e}")
            return None
        if not self.allowed_languages:
            return _top
        from .dictation_languages import choose_language
        language, _outsider = choose_language(dict(all_probs), self.allowed_languages)
        return language

    # With `language: auto` and a list of the user's languages, detect the
    # language and keep the choice inside that list. One language is simply
    # used; several cost one extra detection pass over the first 30 s.
    # English-only models (*.en) have nothing to choose.
    def _language_from_allowed(self, audio_data: np.ndarray) -> Optional[str]:
        from .dictation_languages import choose_language
        if len(self.allowed_languages) == 1:
            return self.allowed_languages[0]
        if not getattr(getattr(self.model, 'model', None), 'is_multilingual', True):
            return None
        try:
            _top, _prob, all_probs = self.model.detect_language(audio_data)
        except Exception as e:
            self.logger.debug(f"Language detection failed, using auto: {e}")
            return None
        language, outsider = choose_language(dict(all_probs), self.allowed_languages)
        self.last_language_outsider = outsider
        self.logger.info(f"Dictation language: {language}"
                         + (f" (speech sounded like {outsider})" if outsider else ""))
        return language

    def change_model(self,
                     new_model_key: str,
                     progress_callback: Optional[Callable[[str], None]] = None):
        
        if new_model_key == self.model_key and self.model is not None and not self.is_loading():
            if progress_callback:
                progress_callback("Model already loaded")
            return True
        
        return self._load_model_async(new_model_key, progress_callback)

