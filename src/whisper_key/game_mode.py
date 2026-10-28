# game_mode.py
# Notices when you're gaming so the app can get out of the way: the AI model
# leaves VRAM and the idle pill hides. A game is either one the `games` app
# rule names, an Unreal/Steam/Epic/Riot game, or any other app that goes
# fullscreen (browsers and video players excepted).
#
# Known games keep game mode on until they exit, so alt-tabbing to Discord
# mid-match doesn't load 3 GB back onto the GPU. An unknown fullscreen app
# only counts while it is fullscreen in front.

import logging
import threading

from .platform import foreground

logger = logging.getLogger(__name__)

POLL_SECONDS = 2.0

# Fullscreen, but not games: videos, presentations, the desktop itself.
NOT_GAMES = {
    'chrome.exe', 'msedge.exe', 'firefox.exe', 'brave.exe', 'opera.exe', 'opera_gx.exe',
    'vivaldi.exe', 'arc.exe', 'zen.exe', 'vlc.exe', 'mpc-hc64.exe', 'mpv.exe',
    'potplayermini64.exe', 'wmplayer.exe', 'video.ui.exe', 'microsoft.media.player.exe',
    'powerpnt.exe', 'explorer.exe', 'applicationframehost.exe', 'electron.exe',
    'obs64.exe', 'spotify.exe', 'netflix.exe',
}

# Paths and names that mark an exe as a game even when no rule lists it.
GAME_HINTS = ('-win64-shipping.exe', '\\steamapps\\', '\\epic games\\', '\\riot games\\',
              '\\xboxgames\\', '\\ea games\\', '\\ubisoft game launcher\\games\\')


class GameModeWatcher:
    # `on_change(bool)` runs on the watcher thread whenever game mode flips.
    # `games_provider()` returns the exe names the games app rule lists.
    def __init__(self, on_change, games_provider=lambda: ()):
        self._on_change = on_change
        self._games_provider = games_provider
        self.active = False
        self._game_exe = None
        self._sticky = False
        self._stop = threading.Event()

    def start(self):
        # Establish GPU policy before preload or recovered meetings can run.
        try:
            self._tick()
        except Exception as e:
            logger.debug(f"Initial game mode check failed: {e}")
        threading.Thread(target=self._run, name='game-mode', daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(POLL_SECONDS):
            try:
                self._tick()
            except Exception as e:
                logger.debug(f"Game mode check failed: {e}")

    def _tick(self):
        if self.active:
            if self._still_playing():
                return
            self._set(False, None, False)
            return
        # Starting the app while alt-tabbed must still protect a running game.
        listed = {str(game).lower() for game in (self._games_provider() or ())}
        running = listed.intersection(foreground.running_exe_names()) if listed else set()
        if running:
            self._set(True, sorted(running)[0], True)
            return
        info = foreground.get_foreground_app() or {}
        exe = (info.get('exe') or '').lower()
        if not exe or exe in NOT_GAMES:
            return
        if self._is_known_game(exe, (info.get('path') or '').lower()):
            self._set(True, exe, True)
        elif foreground.is_fullscreen():
            self._set(True, exe, False)

    def _is_known_game(self, exe: str, path: str) -> bool:
        listed = {str(g).lower() for g in (self._games_provider() or ())}
        return exe in listed or any(hint in path or exe.endswith(hint) for hint in GAME_HINTS)

    # A known game counts until its process is gone; an unknown fullscreen app
    # only while it's still fullscreen in front.
    def _still_playing(self) -> bool:
        if self._sticky:
            return self._game_exe in foreground.running_exe_names()
        info = foreground.get_foreground_app() or {}
        return (info.get('exe') or '').lower() == self._game_exe and foreground.is_fullscreen()

    def _set(self, active: bool, exe, sticky: bool):
        self.active, self._game_exe, self._sticky = active, exe, sticky
        logger.info(f"Game mode {'on' if active else 'off'}" + (f" ({exe})" if exe else ""))
        try:
            self._on_change(active)
        except Exception as e:
            logger.warning(f"Game mode handler failed: {e}")
