# Game-mode transitions must protect the GPU across quality changes.
# Foreground processes are simulated; these tests never launch a game.
import unittest
from unittest.mock import Mock, patch

from syllune.game_mode import GameModeWatcher
from syllune.state_manager import StateManager


class GameModeTests(unittest.TestCase):
    def test_running_listed_game_is_detected_when_another_app_has_focus(self):
        watcher = GameModeWatcher(Mock(), lambda: ['FortniteClient-Win64-Shipping.exe'])
        with patch('syllune.game_mode.foreground.get_foreground_app', return_value={'exe': 'discord.exe'}), \
             patch('syllune.game_mode.foreground.running_exe_names', return_value={'fortniteclient-win64-shipping.exe'}):
            watcher._tick()
            self.assertTrue(watcher.active)

    def test_initial_game_is_detected_before_start_returns(self):
        watcher = GameModeWatcher(Mock(), lambda: ['fortniteclient-win64-shipping.exe'])
        with patch('syllune.game_mode.foreground.get_foreground_app', return_value={
                'exe': 'fortniteclient-win64-shipping.exe', 'path': ''}):
            watcher.start()
            try:
                self.assertTrue(watcher.active)
            finally:
                watcher.stop()

    def test_fast_mode_still_tracks_game_start_and_end(self):
        state = Mock()
        state.current_quality.return_value = 'fast'
        state.config_manager.get_ai_config.return_value = {'unload_in_games': True}
        StateManager._on_game_mode(state, True)
        state.smart_editor.set_game_mode.assert_called_with(True)
        StateManager._on_game_mode(state, False)
        state.smart_editor.set_game_mode.assert_called_with(False)


if __name__ == '__main__':
    unittest.main()
