# The fast path changes one decode, not the shared model or other workers.
import unittest
import contextlib
import io
import unittest.mock
from unittest.mock import Mock
from types import SimpleNamespace
import numpy as np
from syllune.whisper_engine import WhisperEngine


class QuickDecodeTests(unittest.TestCase):
    def test_beam_override_applies_only_to_this_recording(self):
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        engine = object.__new__(WhisperEngine)
        engine.model = Mock()
        engine.logger = Mock()
        engine.vad_manager = None
        engine.beam_size, engine.task = 5, 'transcribe'
        engine.initial_prompt = engine.hotwords = None
        engine.log_transcriptions = False
        engine._detect_language = lambda audio: 'en'
        engine._model_for = lambda language: engine.model
        engine.model.transcribe.side_effect = lambda *a, **kw: (
            [SimpleNamespace(text='Hello.')], SimpleNamespace(language='en', language_probability=1.0))
        self.assertEqual('Hello.', engine.transcribe_audio(np.ones(16000, dtype=np.float32), beam_size=1))
        self.assertEqual(1, engine.model.transcribe.call_args.kwargs['beam_size'])
        self.assertEqual(5, engine.beam_size)
        engine.transcribe_audio(np.ones(64000, dtype=np.float32))
        self.assertEqual(5, engine.model.transcribe.call_args.kwargs['beam_size'])

    # Short clips: one encoder pass feeds both language detection and decoding,
    # and the language is still chosen from the user's languages.
    def test_quick_path_encodes_once_and_picks_allowed_language(self):
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        engine = object.__new__(WhisperEngine)
        engine.logger = Mock()
        engine.vad_manager = None
        engine.beam_size, engine.task, engine.language = 5, 'transcribe', None
        engine.initial_prompt = engine.hotwords = None
        engine.log_transcriptions = False
        engine.allowed_languages = ['sv', 'en']
        engine.model = Mock()
        engine.model.feature_extractor.nb_max_frames = 3000
        engine.model.feature_extractor.return_value = np.zeros((128, 200), dtype=np.float32)
        engine.model.model.is_multilingual = True
        engine.model.model.detect_language.return_value = [[('<|de|>', 0.5), ('<|sv|>', 0.3), ('<|en|>', 0.2)]]
        engine.model.max_length = 448
        engine.model.get_prompt.return_value = [1, 2, 3]
        engine.model.model.generate.return_value = [SimpleNamespace(sequences_ids=[[10, 11]])]
        fake_tokenizer = Mock(eot=50257)
        fake_tokenizer.decode.return_value = ' Hej då.'
        with unittest.mock.patch('faster_whisper.tokenizer.Tokenizer', return_value=fake_tokenizer), \
             unittest.mock.patch('faster_whisper.transcribe.get_suppressed_tokens', return_value=(-1,)):
            text = engine.transcribe_audio(np.ones(16000, dtype=np.float32), quick=True)
        self.assertEqual('Hej då.', text)
        self.assertEqual(1, engine.model.encode.call_count)
        self.assertEqual('sv', engine.last_language)
        engine.model.transcribe.assert_not_called()


# Whisper punctuates only when its prompt shows punctuated text, so every
# decode is primed with a sentence in the clip's language.
class PunctuationPrimerTests(unittest.TestCase):
    def test_prompt_puts_primer_before_user_prompt(self):
        from syllune.whisper_engine import build_prompt, PUNCTUATION_PRIMERS
        self.assertEqual(PUNCTUATION_PRIMERS['en'] + ' Project Atlas.', build_prompt('en', 'Project Atlas.'))
        self.assertEqual(PUNCTUATION_PRIMERS['sv'], build_prompt('sv', ''))
        self.assertEqual('Notes.', build_prompt('de', 'Notes.'))
        self.assertIsNone(build_prompt(None, None))

    def test_normal_path_detects_language_to_prime_the_prompt(self):
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        from syllune.whisper_engine import PUNCTUATION_PRIMERS
        engine = object.__new__(WhisperEngine)
        engine.model = Mock()
        engine.model.model.is_multilingual = True
        engine.model.detect_language.return_value = ('sv', 0.9, [])
        engine.logger = Mock()
        engine.vad_manager = None
        engine.beam_size, engine.task, engine.language = 3, 'transcribe', None
        engine.allowed_languages, engine.language_model_instances = [], {}
        engine.initial_prompt = engine.hotwords = None
        engine.log_transcriptions = False
        engine.model.transcribe.return_value = (
            [SimpleNamespace(text=' Hej.')], SimpleNamespace(language='sv', language_probability=1.0))
        engine.transcribe_audio(np.ones(64000, dtype=np.float32))
        kwargs = engine.model.transcribe.call_args.kwargs
        self.assertEqual('sv', kwargs['language'])
        self.assertEqual(PUNCTUATION_PRIMERS['sv'], kwargs['initial_prompt'])

    def test_quick_path_uses_quality_beam_and_primer(self):
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        from syllune.whisper_engine import PUNCTUATION_PRIMERS
        engine = object.__new__(WhisperEngine)
        engine.logger = Mock()
        engine.beam_size, engine.task, engine.language = 5, 'transcribe', 'en'
        engine.initial_prompt = engine.hotwords = None
        engine.model = Mock()
        engine.model.feature_extractor.nb_max_frames = 3000
        engine.model.feature_extractor.return_value = np.zeros((128, 200), dtype=np.float32)
        engine.model.model.is_multilingual = True
        engine.model.max_length = 448
        engine.model.get_prompt.return_value = [1]
        engine.model.model.generate.return_value = [SimpleNamespace(sequences_ids=[[10]])]
        fake_tokenizer = Mock(eot=50257)
        fake_tokenizer.decode.return_value = 'Okay.'
        with unittest.mock.patch('faster_whisper.tokenizer.Tokenizer', return_value=fake_tokenizer), \
             unittest.mock.patch('faster_whisper.transcribe.get_suppressed_tokens', return_value=(-1,)):
            engine._transcribe_quick(np.ones(16000, dtype=np.float32))
        self.assertEqual(5, engine.model.model.generate.call_args.kwargs['beam_size'])
        fake_tokenizer.encode.assert_called_once_with(' ' + PUNCTUATION_PRIMERS['en'])
