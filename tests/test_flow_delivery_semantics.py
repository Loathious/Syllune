# A failed optional Enter or clipboard restore must not queue text twice.
import unittest
import contextlib
import io
from unittest.mock import Mock, patch
from syllune.clipboard_manager import ClipboardManager


class DeliverySemanticsTests(unittest.TestCase):
    def test_failed_enter_keeps_successful_insertion_successful(self):
        manager = object.__new__(ClipboardManager)
        manager.auto_paste, manager.delivery_method = True, 'paste'
        manager.logger = Mock()
        manager.execute_delivery = Mock(return_value=True)
        manager.send_enter_key = Mock(return_value=False)
        self.assertTrue(manager.deliver_transcription('Hello.', use_auto_enter=True))

    def test_restore_failure_does_not_repeat_a_completed_paste(self):
        manager = object.__new__(ClipboardManager)
        manager.logger = Mock()
        manager.paste_preserve_clipboard = True
        manager.paste_pre_paste_delay = manager.paste_clipboard_restore_delay = 0
        manager.paste_keys = ['ctrl', 'v']
        manager.copy_text = Mock(return_value=True)
        with patch('syllune.clipboard_manager.pyperclip.paste', return_value='original'), \
             patch('syllune.clipboard_manager.pyperclip.copy', side_effect=OSError('restore failed')), \
             patch('syllune.clipboard_manager.keyboard.send_hotkey') as paste, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(manager._clipboard_paste('Hello.'))
            paste.assert_called_once()
