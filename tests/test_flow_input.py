# Exercise release ordering and intentional editable-field delivery without
# injecting keys, touching the clipboard, or interacting with the desktop.
import importlib
import sys
import unittest
from unittest.mock import patch


def optional_module(name):
    try:
        return importlib.import_module('syllune.platform.windows.' + name)
    except ModuleNotFoundError:
        return None


@unittest.skipUnless(sys.platform == 'win32', 'Windows input backend')
class StartMenuGuardTests(unittest.TestCase):
    def setUp(self):
        self.module = optional_module('start_menu_guard')
        self.assertIsNotNone(self.module, 'Ctrl+Win needs a release-time guard')
        self.state = self.module.ReleaseState([{'ctrl', 'win'}])

    def press_chord(self):
        self.state.event(0xA2, True, now=1)
        self.state.event(0x5B, True, now=1.01)

    def test_win_first_release_is_masked(self):
        self.press_chord()
        self.assertTrue(self.state.event(0x5B, False, now=1.1))
        self.assertFalse(self.state.event(0xA2, False, now=1.11))

    def test_ctrl_first_release_gets_short_grace(self):
        self.press_chord()
        self.state.event(0xA2, False, now=1.1)
        self.assertTrue(self.state.event(0x5B, False, now=1.29))

    def test_grace_expires_without_eating_later_standalone_win(self):
        self.press_chord()
        self.state.event(0xA2, False, now=1.1)
        self.assertFalse(self.state.event(0x5B, False, now=1.31))
        self.state.event(0x5B, True, now=1.32)
        self.assertFalse(self.state.event(0x5B, False, now=1.4))

    def test_injected_keys_cannot_arm_or_consume_physical_release(self):
        self.state.event(0xA2, True, injected=True, now=1)
        self.state.event(0x5B, True, now=1.01)
        self.assertFalse(self.state.event(0x5B, False, now=1.1))
        self.press_chord()
        self.assertFalse(self.state.event(0x5B, False, injected=True, now=1.1))
        self.assertTrue(self.state.event(0x5B, False, now=1.11))

    def test_unregistered_ctrl_win_and_ordinary_win_pass_through(self):
        self.state = self.module.ReleaseState([])
        self.press_chord()
        self.assertFalse(self.state.event(0x5B, False, now=1.1))
        self.state = self.module.ReleaseState([{'ctrl', 'win'}])
        self.state.event(0x5C, True, now=2)
        self.assertFalse(self.state.event(0x5C, False, now=2.1))

    def test_failed_send_never_swallows_physical_win_release(self):
        self.press_chord()
        guard = self.module.StartMenuGuard()
        guard.state = self.state
        with patch.object(self.module.keyboard, 'mask_start_menu_and_release', return_value=False):
            self.assertFalse(guard.handle_event(0x5B, False, now=1.1))


@unittest.skipUnless(sys.platform == 'win32', 'Windows UI Automation backend')
class TextTargetTests(unittest.TestCase):
    def setUp(self):
        self.module = optional_module('text_target')
        self.assertIsNotNone(self.module, 'Pending delivery needs verified editable targets')

    def properties(self, **overrides):
        props = {'control_type': 50004, 'focused': True, 'focusable': True,
                 'enabled': True, 'password': False, 'offscreen': False,
                 'value_available': True, 'value_readonly': False,
                 'text_readonly': None}
        return props | overrides

    def test_native_and_chromium_editable_controls_are_accepted(self):
        self.assertTrue(self.module._editable(self.properties()))
        self.assertTrue(self.module._editable(self.properties(
            control_type=50030, value_available=False, text_readonly=False)))
        # Windows 11 Notepad's document reports no IsOffscreen value at all.
        self.assertTrue(self.module._editable(self.properties(control_type=50030, offscreen=None)))

    def test_readonly_password_disabled_and_unknown_fields_fail_closed(self):
        for overrides in ({'password': True}, {'password': None}, {'enabled': False},
                          {'focused': False}, {'value_readonly': True},
                          {'value_readonly': None}, {'control_type': 50000},
                          {'value_available': False, 'text_readonly': None},
                          {'offscreen': True}):
            with self.subTest(overrides=overrides):
                self.assertFalse(self.module._editable(self.properties(**overrides)))

    def test_hover_and_injected_mouse_events_cannot_deliver(self):
        delivered = []
        watcher = self.module.TextTargetWatcher(delivered.append)
        watcher._mouse_event(0x200, 0, (20, 20))
        watcher._mouse_event(0x201, 1, (20, 20))
        watcher._mouse_event(0x202, 1, (20, 20))
        self.assertTrue(watcher._clicks.empty())
        self.assertEqual([], delivered)

    def test_only_a_complete_physical_click_is_queued(self):
        watcher = self.module.TextTargetWatcher(lambda target: None)
        watcher._mouse_event(0x202, 0, (20, 20))
        self.assertTrue(watcher._clicks.empty())
        watcher._mouse_event(0x201, 0, (20, 20))
        watcher._mouse_event(0x202, 0, (20, 20))
        self.assertEqual((20, 20), watcher._clicks.get_nowait()[1])

    def test_dragging_is_not_a_deliberate_insertion_click(self):
        watcher = self.module.TextTargetWatcher(lambda target: None)
        watcher._mouse_event(0x201, 0, (20, 20))
        watcher._mouse_event(0x202, 0, (100, 20))
        self.assertTrue(watcher._clicks.empty())

    def test_late_probe_cannot_turn_an_old_click_into_delivery(self):
        delivered = []
        watcher = self.module.TextTargetWatcher(delivered.append)

        with patch.object(self.module, '_probe_text_target', return_value={'target_token': 'x'}), \
             patch.object(self.module.time, 'monotonic', return_value=3):
            self.assertFalse(watcher._deliver_click((20, 20), clicked_at=1))
        self.assertEqual([], delivered)


@unittest.skipUnless(sys.platform == 'win32', 'Windows SendInput backend')
class ReleaseBatchTests(unittest.TestCase):
    def test_mask_precedes_win_release_in_one_batch(self):
        from syllune.platform.windows import keyboard
        batches = []

        def send(inputs):
            batches.append([(item.ki.wVk, item.ki.dwFlags) for item in inputs])
            return len(inputs)

        with patch.object(keyboard, '_send', side_effect=send):
            self.assertTrue(keyboard.mask_start_menu_and_release(0x5C))
        self.assertEqual([[(0xE8, 0), (0xE8, 2), (0x5C, 3)]], batches)

    def test_incomplete_batch_and_non_win_key_are_not_claimed_as_released(self):
        from syllune.platform.windows import keyboard
        with patch.object(keyboard, '_send', return_value=2):
            self.assertFalse(keyboard.mask_start_menu_and_release(0x5B))
            self.assertFalse(keyboard.mask_start_menu_and_release(0xA2))


@unittest.skipUnless(sys.platform == 'win32', 'Windows UI Automation backend')
class TextTargetSafetyTests(unittest.TestCase):
    def setUp(self):
        self.module = optional_module('text_target')
        self.assertIsNotNone(self.module)

    def test_unknown_probe_and_callback_exceptions_do_not_kill_watcher(self):
        watcher = self.module.TextTargetWatcher(lambda target: (_ for _ in ()).throw(RuntimeError()))
        with patch.object(self.module, '_probe_text_target', return_value=None):
            self.assertFalse(watcher._deliver_click((20, 20)))
        with patch.object(self.module, '_probe_text_target', return_value={'target_token': 'x'}):
            self.assertFalse(watcher._deliver_click((20, 20)))

    def test_stop_is_safe_inside_callback_and_blocks_later_delivery(self):
        watcher = self.module.TextTargetWatcher(lambda target: watcher.stop())
        with patch.object(self.module, '_probe_text_target', return_value={'target_token': 'x'}):
            self.assertTrue(watcher._deliver_click((20, 20)))
            self.assertFalse(watcher._deliver_click((20, 20)))


@unittest.skipUnless(sys.platform == 'win32', 'Windows UI Automation backend')
class TargetProbeFlowTests(unittest.TestCase):
    def setUp(self):
        import ctypes
        from unittest.mock import Mock
        self.module = optional_module('text_target')
        self.metadata = {'exe': 'chrome.exe', 'path': 'C:/chrome.exe', 'title': 'Test'}
        self.properties = {30003: 50004, 30008: True, 30009: True,
                           30010: True, 30019: False, 30022: False,
                           30043: True, 30046: False}
        self.calls = []

        def create(*args):
            ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_void_p)).contents.value = 10

        def call(pointer, slot, *types):
            def invoke(*args):
                self.calls.append((slot, args))
                if slot == 8:
                    ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_void_p)).contents.value = 20
            return invoke

        ole = Mock()
        ole.CoCreateInstance.side_effect = create
        for name, replacement in (
                ('_ole32', ole), ('_call', call), ('_release', lambda pointer: None),
                ('_runtime_id', lambda element: (42, 9)),
                ('_property', lambda element, prop: self.properties.get(prop)),
                ('_user32', Mock(GetForegroundWindow=lambda: 100))):
            context = patch.object(self.module, name, replacement)
            context.start()
            self.addCleanup(context.stop)
        context = patch.object(self.module.foreground, 'get_foreground_app', return_value=self.metadata)
        self.foreground = context.start()
        self.addCleanup(context.stop)

    def test_repeat_focus_checks_have_same_token_and_foreground_metadata(self):
        first = self.module.current_text_target()
        second = self.module.current_text_target()
        self.assertEqual((100, (42, 9)), first['target_token'])
        self.assertEqual(first['target_token'], second['target_token'])
        self.assertEqual('chrome.exe', first['exe'])

    def test_foreground_change_during_probe_is_rejected(self):
        self.foreground.side_effect = [self.metadata, self.metadata | {'title': 'Other'}]
        self.assertIsNone(self.module.current_text_target())

    def test_click_outside_focused_editor_is_rejected(self):
        with patch.object(self.module, '_click_matches', return_value=False):
            self.assertIsNone(self.module._probe_text_target((20, 20)))

    def test_unknown_password_property_cannot_become_a_target(self):
        self.properties[30019] = None
        self.assertIsNone(self.module.current_text_target())

    def test_ui_automation_provider_uses_short_timeouts(self):
        self.assertIsNotNone(self.module.current_text_target())
        self.assertTrue(any(slot == 61 and args[-1] == 200 for slot, args in self.calls))
        self.assertTrue(any(slot == 63 and args[-1] == 200 for slot, args in self.calls))


if __name__ == '__main__':
    unittest.main()
