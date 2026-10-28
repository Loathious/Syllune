# electron_overlay.py
# Drop-in replacement for LevelOverlay that drives the animated Electron pill
# in /overlay. Electron runs as a child process and connects back to a
# localhost socket we open. JSON lines flow both ways: we stream state changes
# and ~60 Hz voice levels; the pill sends back clicks (dictate, stop, cancel)
# and the hub window sends its requests (rpc, answered by hub_api.py).
# If Electron is missing or never connects, start() raises and the state
# manager falls back to the Tk LevelOverlay.

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

from .utils import get_user_app_data_path

OVERLAY_DIR = Path(os.environ.get('SYLLUNE_OVERLAY_DIR') or Path(__file__).resolve().parents[2] / 'overlay')
ELECTRON_EXE = Path(os.environ['SYLLUNE_ELECTRON_EXE']) if os.environ.get('SYLLUNE_ELECTRON_EXE') else OVERLAY_DIR / 'node_modules' / 'electron' / 'dist' / 'electron.exe'


class ElectronOverlay:
    LEVEL_HZ = 60
    CONNECT_TIMEOUT_S = 15
    # The pill snaps to one of these; dragging it picks the nearest.
    POSITIONS = ('bottom-center', 'middle-left', 'middle-right')

    # `spectrum_provider()` returns per-band voice levels in dB (or None);
    # `on_action(message)` receives what the pill sends back, on a reader thread;
    # `on_rpc(message, reply)` receives the hub's requests, `reply` answers one.
    def __init__(self, level_provider: Callable[[], float],
                 locked_provider: Callable[[], bool] = lambda: False,
                 spectrum_provider: Callable[[], Optional[list]] = lambda: None,
                 on_action: Callable[[dict], None] = lambda message: None,
                 on_rpc: Callable[[dict, Callable[[dict], None]], None] = lambda message, reply: None,
                 position: str = 'bottom-center',
                 idle_pill: bool = True,
                 notes_dir: Optional[Path] = None):
        self.level_provider = level_provider
        self.locked_provider = locked_provider
        self.spectrum_provider = spectrum_provider
        self.on_action = on_action
        self.on_rpc = on_rpc
        self.position = position if position in self.POSITIONS else 'bottom-center'
        self.idle_pill = bool(idle_pill)
        self.notes_dir = notes_dir
        self._process: Optional[subprocess.Popen] = None
        self._connection: Optional[socket.socket] = None
        self._send_lock = threading.Lock()
        self._recording = threading.Event()
        self._last_locked = False
        self._stopping = False

    # --- Lifecycle -----------------------------------------------------------

    def start(self):
        # Installed apps already own a visible Electron shell. Connecting to
        # it avoids the old launcher->engine->second-shell race and makes a
        # second desktop click focus the same GUI instead of killing/restarting.
        shell_port = os.environ.get('SYLLUNE_UI_PORT')
        if shell_port:
            self._connection = socket.create_connection(('127.0.0.1', int(shell_port)), timeout=self.CONNECT_TIMEOUT_S)
            self._connection.settimeout(None)
            self._shell_pid = int(os.environ.get('SYLLUNE_UI_PID') or 0)
            self._send({'type': 'hello', 'token': os.environ.get('SYLLUNE_UI_TOKEN', '')})
            self._connected()
            return
        if not ELECTRON_EXE.exists():
            raise RuntimeError(f"Electron not installed ({ELECTRON_EXE}); run npm install in overlay/")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(self.CONNECT_TIMEOUT_S)
        port = listener.getsockname()[1]

        args = [str(ELECTRON_EXE)]
        # A packaged Electron executable already knows its app; passing the
        # source directory there makes Chromium interpret it as a user file.
        if not os.environ.get('SYLLUNE_ELECTRON_EXE'):
            args.append(str(OVERLAY_DIR))
        # The window keeps its browser storage in the cache folder, apart from
        # your data, and reads recordings from the data folder the engine uses.
        args += ['--port', str(port), '--data-dir', get_user_app_data_path()]
        try:
            from .platform import paths
            args += ['--cache-dir', str(paths.get_cache_path())]
        except ImportError:
            pass
        if self.notes_dir:
            args += ['--notes-dir', str(self.notes_dir)]
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        self._process = subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        try:
            self._connection, _ = listener.accept()
        except socket.timeout:
            self._process.kill()
            raise RuntimeError("Electron overlay never connected")
        finally:
            listener.close()
        self._connected()

    # Both development and installed transports share the same message pump.
    def _connected(self):
        self._connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._send({'type': 'config', 'position': self.position, 'idle_pill': self.idle_pill,
                    'notes_dir': str(self.notes_dir) if self.notes_dir else None})
        threading.Thread(target=self._level_pump, daemon=True, name='electron-overlay-levels').start()
        threading.Thread(target=self._read_actions, daemon=True, name='electron-overlay-actions').start()

    def shutdown(self):
        self._stopping = True
        self._send({'type': 'quit'})
        if self._connection:
            try:
                self._connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._connection.close()
        if self._process:
            try:
                self._process.wait(timeout=2)
            except Exception:
                self._process.kill()

    # Initialization can fail after IPC connects (hotkeys, audio, tray). Tell
    # the supervising shell before normal cleanup sends its final quit message.
    def startup_failed(self):
        self._send({'type': 'startup_error', 'message': 'Syllune could not finish starting. Open logs for details, then retry.'})

    # --- LevelOverlay API ----------------------------------------------------

    def show_recording(self):
        self._start_recording(note=False)

    def show_note_recording(self):
        self._start_recording(note=True)

    def show_processing(self):
        self._recording.clear()
        self._state('processing')

    def hide(self):
        self._recording.clear()
        self._state('hidden')

    def flash_success(self):
        self._recording.clear()
        self._state('success')

    def flash_failure(self, message: str = None):
        self._recording.clear()
        self._state('error', message=message or '')

    # Dictate-then-complete: the pill never shows live text.
    def set_streaming_text(self, text: str):
        pass

    def show_notice(self, message: str):
        self._state('notice', message=message)

    def set_position(self, name: str):
        if name in self.POSITIONS:
            self.position = name
            self._send({'type': 'config', 'position': name})

    # The small resting pill shown between dictations.
    def set_idle_pill(self, enabled: bool):
        self.idle_pill = bool(enabled)
        self._send({'type': 'config', 'idle_pill': self.idle_pill})

    def set_pending(self, count: int):
        self._send({'type': 'pending', 'count': int(count)})

    # While a game runs the pill window is fully hidden (not just transparent),
    # so it can't affect how the game is presented or how the mouse is read.
    def set_game_mode(self, active: bool):
        self._send({'type': 'game_mode', 'on': bool(active)})

    # --- Notes ---------------------------------------------------------------

    def note_saved(self, path):
        self._recording.clear()
        self._send({'type': 'note_saved', 'path': str(path)})

    # --- Meetings ------------------------------------------------------------

    # While a meeting records, the resting pill shows a red dot and the time
    # since `since_ms` (epoch ms); None ends that look.
    def set_meeting(self, since_ms: Optional[int]):
        self._send({'type': 'meeting', 'since': since_ms})

    # A meeting's notes were just written.
    def meeting_ready(self):
        self._state('meeting_ready')

    # --- Hub window ----------------------------------------------------------

    # Opens (or focuses) the hub on one of its pages: home, history, notes,
    # meetings, styles, dictionary, settings. Called from a tray click, which gives this
    # process the right to bring a window forward; Windows only lets Electron
    # take focus if we pass that right on, otherwise the hub just flashes in
    # the taskbar.
    def open_hub(self, page: str = 'home'):
        gui_pid = getattr(self, '_shell_pid', 0) or (self._process.pid if self._process else 0)
        if sys.platform == 'win32' and gui_pid:
            try:
                import ctypes
                ctypes.windll.user32.AllowSetForegroundWindow(gui_pid)
            except Exception:
                pass
        self._send({'type': 'open_hub', 'page': page})

    # Tells an open hub that something it shows changed (a new dictation),
    # so it can refresh without polling.
    def hub_event(self, name: str):
        self._send({'type': 'hub_event', 'name': name})

    # --- Internals -----------------------------------------------------------

    def _start_recording(self, note: bool):
        self._state('recording', note=note)
        self._last_locked = False   # the pill resets its hands-free controls on 'recording'
        self._recording.set()

    def _state(self, state: str, **extra):
        self._send({'type': 'state', 'state': state, **extra})

    def _send(self, message: dict):
        connection = self._connection
        if not connection:
            return
        with self._send_lock:
            try:
                connection.sendall((json.dumps(message) + '\n').encode('utf-8'))
            except OSError:
                logger.warning("Electron overlay connection closed")
                self._connection = None

    # Clicks on the pill and the hub's requests come back as JSON lines on
    # the same socket.
    def _read_actions(self):
        connection = self._connection
        if not connection:
            return
        try:
            for line in connection.makefile('r', encoding='utf-8'):
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                try:
                    if message.get('type') == 'rpc':
                        self.on_rpc(message, self._send)
                    else:
                        self.on_action(message)
                except Exception as e:
                    logger.warning(f"Overlay action failed: {e}")
        except OSError:
            pass

    # Streams the voice level and band levels while recording so the bars
    # follow your voice, and reports hands-free changes so the pill can show
    # its stop and cancel controls.
    def _level_pump(self):
        interval = 1.0 / self.LEVEL_HZ
        while not self._stopping:
            if not self._recording.wait(timeout=0.5):
                continue
            try:
                level = float(self.level_provider() or 0.0)
                bands = self.spectrum_provider()
            except Exception:
                level, bands = 0.0, None
            self._send({'type': 'level', 'v': round(level, 5), 'bands': bands})
            locked = bool(self.locked_provider())
            if locked != self._last_locked:
                self._state('locked', value=locked)
                self._last_locked = locked
            time.sleep(interval)
