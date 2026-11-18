# Regression coverage for vocabulary learned from English and Swedish speech.
# These tests exercise text-only behavior, with no microphone or model needed.
import unittest

from syllune.dictionary import suggest_hotwords, words_to_learn
from syllune.corrections import corrections_from_edit
from syllune.smart_edit import SmartEditor


class FlowLearningTests(unittest.TestCase):
    def test_swedish_names_stay_whole(self):
        self.assertIn(('Örnsköldsvik', 3), suggest_hotwords(
            ['Vi möts i Örnsköldsvik med Åsa.'] * 3))

    def test_case_variants_combine_without_losing_canonical_spelling(self):
        suggestions = suggest_hotwords(['We use GitHub.', 'We use Github.', 'Open GitHub.'])
        self.assertIn(('GitHub', 3), suggestions)

    def test_contractions_and_weekdays_are_not_names(self):
        self.assertEqual([], suggest_hotwords(["And I'm sure I'll see you on Monday."] * 4))

    def test_learning_respects_removed_words_and_its_limit(self):
        texts = ['We use Redis and Kafka.', 'Then Redis and Kafka.', 'Also Redis and Kafka.']
        self.assertEqual(['Kafka', 'Redis'], sorted(words_to_learn(texts, [], [], [])))
        self.assertEqual(['Kafka'], words_to_learn(texts, [], [], ['Redis']))
        self.assertEqual([], words_to_learn(texts, ['Go'], ['Go'], [], limit=1))
        # Words you typed yourself never use up the room for learned ones.
        self.assertEqual(1, len(words_to_learn(texts, ['Go'], [], [], limit=1)))

    def test_ordinary_swedish_words_are_not_suggestions(self):
        self.assertEqual([], suggest_hotwords(['Ja, Det kan vi göra.'] * 4))

    def test_known_words_excluded_without_case_sensitivity(self):
        self.assertEqual([], suggest_hotwords(['Vi möter Åsa.'] * 3, known={'åsa'}))

    def test_edit_teaches_mishearing_not_rewording(self):
        self.assertEqual([('cloud code', 'Claude Code')], corrections_from_edit(
            'Please open cloud code today.', 'Please open Claude Code today.'))
        self.assertEqual([], corrections_from_edit('The work is done.', 'A task is ready.'))

    def test_about_you_reaches_local_editor(self):
        editor = SmartEditor(lambda: {'about_me': 'I work with Åsa on TitaNet.'})
        messages = editor._messages('send it to asa', '', '', ['Åsa'], editor._cfg(), 'sv')
        self.assertIn('I work with Åsa on TitaNet.', messages[0]['content'])
        self.assertIn('Åsa', messages[0]['content'])


if __name__ == '__main__':
    unittest.main()
