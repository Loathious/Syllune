# Regression coverage for History's Structure preview style: it is offered list-
# shaped examples only, works on short dictations, and its lists read as one block.
# Text-only: no Ollama, microphone or model is needed.
import unittest

from syllune.smart_edit import (EXAMPLES, STRUCTURE_EXAMPLES, SmartEditor,
                                    _tighten_lists, is_faithful)


class StructureStyleTests(unittest.TestCase):
    def _messages(self, style):
        editor = SmartEditor(lambda: {})
        return editor._messages('send the draft then book the room', style, '', [], editor._cfg(), 'en')

    def test_structure_uses_only_list_examples(self):
        shown = [message['content'] for message in self._messages('structure')]
        self.assertIn(STRUCTURE_EXAMPLES[0][2], shown)
        self.assertNotIn(EXAMPLES[-1][2], shown)
        self.assertIn('structured prompt', shown[0])

    def test_automatic_prompt_style_keeps_plain_examples(self):
        shown = [message['content'] for message in self._messages('prompt')]
        self.assertIn(EXAMPLES[-1][2], shown)
        self.assertNotIn(STRUCTURE_EXAMPLES[0][2], shown)

    def test_short_dictation_still_reaches_the_model(self):
        replies = []
        editor = SmartEditor(lambda: {'min_words': 4})
        editor.chat = lambda messages, **options: replies.append(messages) or 'Book it.'
        self.assertEqual('Book it.', editor.edit('book it', style='structure'))
        self.assertEqual(1, len(replies))

    def test_blank_lines_between_items_are_removed_but_lead_gap_kept(self):
        self.assertEqual('Do this.\n\n- One.\n- Two.\n- Three.',
                         _tighten_lists('Do this.\n\n- One.\n\n- Two.\n \n- Three.'))

    def test_split_swedish_list_passes_the_guard(self):
        said = 'skriv ett mejl till Erik om att mötet flyttas och att han ska ta med budgeten'
        written = 'Skriv ett mejl till Erik om att mötet flyttas.\n\n- Han ska ta med budgeten.'
        self.assertTrue(is_faithful(said, written, 'structure'))


if __name__ == '__main__':
    unittest.main()
