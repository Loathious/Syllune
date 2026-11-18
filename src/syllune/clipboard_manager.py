# clipboard_manager.py
# Delivers transcribed text to the active app: either pastes (clipboard + Ctrl/Cmd+V,
# restoring the user's prior clipboard) or types it directly via SendInput, with an
# optional auto-Enter for chat apps. Cross-platform through the platform.keyboard layer.

import logging
import time
from typing import Optional

import pyperclip

from .platform import keyboard
from .utils import parse_hotkey

class ClipboardManager:
    def __init__(self, auto_paste, delivery_method, paste_hotkey,
                 paste_pre_paste_delay, paste_preserve_clipboard,
                 paste_clipboard_restore_delay,
                 type_also_copy_to_clipboard, type_auto_enter_delay,
                 type_auto_enter_delay_per_100_chars,
                 macos_key_simulation_delay):
        self.logger = logging.getLogger(__name__)
        self.auto_paste = auto_paste
        self.delivery_method = keyboard.validate_delivery_method(delivery_method)
        self.paste_hotkey = paste_hotkey
        self.paste_keys = parse_hotkey(paste_hotkey)
        self.paste_pre_paste_delay = paste_pre_paste_delay
        self.paste_preserve_clipboard = paste_preserve_clipboard
        self.paste_clipboard_restore_delay = paste_clipboard_restore_delay
        self.type_also_copy_to_clipboard = type_also_copy_to_clipboard
        self.type_auto_enter_delay = type_auto_enter_delay
        self.type_auto_enter_delay_per_100_chars = type_auto_enter_delay_per_100_chars
        keyboard.set_delay(macos_key_simulation_delay)
        if self.delivery_method == "paste":
            self._test_clipboard_access()
        self._print_status()

    # Only a log line. Windows lets one program hold the clipboard at a time, so
    # this read fails whenever another app happens to be using it; raising here
    # used to stop the whole engine from starting over a momentary clash.
    def _test_clipboard_access(self):
        try:
            pyperclip.paste()
            self.logger.info("Clipboard access test successful")
        except Exception as e:
            self.logger.warning(f"Clipboard is busy at startup, continuing: {e}")

    # Runs a clipboard call, trying again when another program holds the
    # clipboard for a moment (clipboard managers and remote-desktop tools do).
    def _with_clipboard_retry(self, call, attempts: int = 4):
        for attempt in range(attempts):
            try:
                return call()
            except Exception:
                if attempt == attempts - 1:
                    raise
                time.sleep(0.12)

    def _print_status(self):
        hotkey_display = self.paste_hotkey.upper()
        if self.auto_paste:
            if self.delivery_method == "type":
                print("   ✓ Auto-paste is ENABLED using direct text injection")
            else:
                print(f"   ✓ Auto-paste is ENABLED using clipboard paste ({hotkey_display})")
        else:
            print(f"   ✗ Auto-paste is DISABLED - paste manually with {hotkey_display}")

    # May the app put text on the clipboard WITHOUT the user asking?
    #
    # `type_also_copy_to_clipboard: false` with delivery_method "type" is an
    # explicit request for clipboard-free dictation, usually because a
    # clipboard manager is logging history or the machine is shared. The
    # delivery path already respected it; the recovery paths did not
    # (issue #12). An explicit Copy button is always still allowed - this
    # governs automatic copies only.
    @property
    def silent_copy_allowed(self) -> bool:
        if self.delivery_method == "type":
            return bool(self.type_also_copy_to_clipboard)
        return True

    def copy_text(self, text: str) -> bool:
        if not text:
            return False

        try:
            self.logger.info(f"Copying text to clipboard ({len(text)} chars)")
            self._with_clipboard_retry(lambda: pyperclip.copy(text))
            return True

        except Exception as e:
            self.logger.error(f"Failed to copy text to clipboard: {e}")
            return False

    def get_clipboard_content(self) -> Optional[str]:
        try:
            clipboard_content = pyperclip.paste()

            if clipboard_content:
                return clipboard_content
            else:
                return None

        except Exception as e:
            self.logger.error(f"Failed to paste text from clipboard: {e}")
            return None

    def copy_with_notification(self, text: str) -> bool:
        if not text:
            return False

        success = self.copy_text(text)

        if success:
            print("   ✓ Copied to clipboard")
            print(f"   ✓ You can now paste with {self.paste_hotkey.upper()} in any application!")

        return success

    def clear_clipboard(self) -> bool:
        try:
            pyperclip.copy("")
            return True

        except Exception as e:
            self.logger.error(f"Failed to clear clipboard: {e}")
            return False

    def _type_delivery(self, text: str) -> bool:
        inserted = False
        try:
            keyboard.type_text(text)
            inserted = True
            if self.type_also_copy_to_clipboard:
                pyperclip.copy(text)
            print("   ✓ Auto-pasted via text injection")
            return True
        except Exception as e:
            self.logger.error(f"Failed to inject text: {e}")
            return inserted

    def _clipboard_paste(self, text: str) -> bool:
        inserted = False
        try:
            original_content = None
            if self.paste_preserve_clipboard:
                original_content = pyperclip.paste()

            if not self.copy_text(text):
                return False

            time.sleep(self.paste_pre_paste_delay)
            keyboard.send_hotkey(*self.paste_keys)
            inserted = True

            print("   ✓ Auto-pasted via key simulation")

            # Only text can be read back through pyperclip: an image or a copied
            # file comes back as '', and "restoring" that would wipe it. Leaving
            # the transcript on the clipboard is the lesser harm in that case.
            if original_content:
                time.sleep(self.paste_clipboard_restore_delay)
                pyperclip.copy(original_content)

            return True

        except Exception as e:
            self.logger.error(f"Failed to simulate paste keypress: {e}")
            return inserted

    # Always clipboard + paste, whatever the delivery method. Text with line
    # breaks (a structured prompt) must not be typed key by key: in a chat box
    # every typed Enter would send the message.
    def paste_text(self, text: str) -> bool:
        return self._clipboard_paste(text)

    def execute_delivery(self, text: str) -> bool:
        if self.delivery_method == "type":
            return self._type_delivery(text)
        else:
            return self._clipboard_paste(text)

    def send_enter_key(self) -> bool:
        try:
            self.logger.info("Sending ENTER key to active application")
            keyboard.send_key('enter')
            print("   ✓ Text submitted with ENTER!")

            return True

        except Exception as e:
            self.logger.error(f"Failed to send ENTER key: {e}")
            return False

    def deliver_transcription(self,
                              transcribed_text: str,
                              use_auto_enter: bool = False) -> bool:

        try:
            if self.auto_paste:
                success = self.execute_delivery(transcribed_text)
                if success and use_auto_enter:
                    if self.delivery_method == "type":
                        delay = self.type_auto_enter_delay + len(transcribed_text) / 100 * self.type_auto_enter_delay_per_100_chars
                        if delay > 0:
                            time.sleep(delay)
                    if not self.send_enter_key():
                        self.logger.warning('Text was inserted, but optional Enter failed')
            else:
                print("📋 Copying to clipboard...")
                success = self.copy_with_notification(transcribed_text)

            return success

        except Exception as e:
            self.logger.error(f"Delivery workflow failed: {e}")
            return False

    def update_auto_paste(self, enabled: bool):
        self.auto_paste = enabled
        self._print_status()
