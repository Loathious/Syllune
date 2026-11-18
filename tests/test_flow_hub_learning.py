# Exercise learning through the real hub API and disposable on-disk settings.
# The running app's history, dictionary and About-you text remain untouched.
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from syllune.config_manager import ConfigManager
from syllune.hub_api import HubApi
from syllune import transcript_log
from syllune.text_postprocess import postprocess


class HubLearningTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for module in ('config_manager', 'dictionary', 'transcript_log'):
            context = patch(f'syllune.{module}.get_user_app_data_path', return_value=folder.name)
            context.start()
            self.addCleanup(context.stop)
        self.config = ConfigManager(quiet=True)
        self.state = Mock()
        self.api = HubApi(self.state, self.config)
        self.addCleanup(lambda: self.api._pool.shutdown(wait=True))

    def test_history_edit_updates_future_dictation_and_persists(self):
        original = 'Please open cloud code today.'
        transcript_log.record_transcript(original)
        entry = transcript_log.load_transcripts()[0]
        result = self.api.history_update(entry['timestamp'], original, 'Please open Claude Code today.')
        self.assertEqual([{'from': 'cloud code', 'to': 'Claude Code'}], result['learned'])
        self.assertIn('Claude Code', postprocess('Please open cloud code today.', self.config.get_postprocess_config()))
        self.assertEqual('Please open Claude Code today.', transcript_log.load_transcripts()[0]['text'])
        self.assertIn('Claude Code', ConfigManager(quiet=True).get_postprocess_config()['replacements'][0]['to'])

    def test_words_said_often_are_learned_into_the_running_recognizer(self):
        for number in range(3):
            transcript_log.record_transcript(f'Vi möter Åsa i Örnsköldsvik nummer {number}.')
        self.assertEqual(['Åsa', 'Örnsköldsvik'], sorted(self.api._learn()))
        self.assertIn('Örnsköldsvik', self.state.whisper_engine.hotwords)
        self.assertIn('Örnsköldsvik', ConfigManager(quiet=True).get_whisper_config()['hotwords'])
        self.assertIn({'word': 'Örnsköldsvik', 'learned': True}, self.api.dictionary()['words'])
        self.assertEqual([], self.api._learn())

    def test_a_removed_word_is_not_learned_again(self):
        for number in range(3):
            transcript_log.record_transcript(f'Vi möter Åsa i Örnsköldsvik nummer {number}.')
        self.api._learn()
        words = [item['word'] for item in self.api.dictionary_remove('Åsa')['words']]
        self.assertEqual(['Örnsköldsvik'], words)
        self.assertEqual([], self.api._learn())
        # Typing it yourself brings it back, as your own word.
        self.assertIn({'word': 'Åsa', 'learned': False}, self.api.dictionary_add('Åsa')['words'])

    def test_learning_can_be_switched_off(self):
        for number in range(3):
            transcript_log.record_transcript(f'Vi möter Åsa i Örnsköldsvik nummer {number}.')
        self.assertEqual([], self.api.dictionary_auto(False)['words'])
        self.assertEqual([], self.api._learn())
        self.assertEqual(2, len(self.api.dictionary_auto(True)['words']))

    def test_a_word_added_with_its_misspelling_is_listened_for_and_corrected(self):
        result = self.api.dictionary_add('Syllune', heard='silune')
        self.assertIn({'word': 'Syllune', 'learned': False}, result['words'])
        self.assertEqual([{'from': 'silune', 'to': 'Syllune'}], result['replacements'])
        self.assertIn('Syllune', postprocess('Open silune now.', self.config.get_postprocess_config()))


if __name__ == '__main__':
    unittest.main()
