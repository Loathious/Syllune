# Exercise learning through the real hub API and disposable on-disk settings.
# The running app's history, dictionary and About-you text remain untouched.
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from whisper_key.config_manager import ConfigManager
from whisper_key.hub_api import HubApi
from whisper_key import transcript_log
from whisper_key.text_postprocess import postprocess


class HubLearningTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for module in ('config_manager', 'dictionary', 'transcript_log'):
            context = patch(f'whisper_key.{module}.get_user_app_data_path', return_value=folder.name)
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

    def test_suggestion_can_be_added_to_running_recognizer(self):
        for number in range(3):
            transcript_log.record_transcript(f'Vi möter Åsa i Örnsköldsvik nummer {number}.')
        self.assertIn('Örnsköldsvik', [s['word'] for s in self.api.dictionary()['suggestions']])
        self.api.dictionary_add('Örnsköldsvik')
        self.assertIn('Örnsköldsvik', self.state.whisper_engine.hotwords)
        self.assertIn('Örnsköldsvik', ConfigManager(quiet=True).get_whisper_config()['hotwords'])
        self.assertNotIn('Örnsköldsvik', [s['word'] for s in self.api.dictionary()['suggestions']])


if __name__ == '__main__':
    unittest.main()
