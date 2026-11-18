# system_tray.py
# The always-visible control surface: tray icon, balloon notifications, and a
# short right-click menu (open, settings, meeting, pause, microphone, recent,
# help). Everything else lives in the hub. The icon doubles as a state indicator
# for idle/recording/processing. pystray/Pillow are imported defensively so a
# machine without a usable tray still runs headless instead of crashing.

import logging
import os
import signal
from typing import Optional, TYPE_CHECKING
from pathlib import Path

from .utils import open_file
from .platform import app as platform_app, icons, console

try:
    import pystray
    from PIL import Image
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False
    pystray = None
    Image = None

if TYPE_CHECKING:
    from .state_manager import StateManager
    from .config_manager import ConfigManager

class SystemTray:
    def __init__(self,
                 state_manager: 'StateManager',
                 tray_config: dict = None,
                 config_manager: Optional['ConfigManager'] = None,
                 model_registry = None,
                 console_config: dict = None):

        self.state_manager = state_manager
        self.tray_config = tray_config or {}
        self.config_manager = config_manager
        self.model_registry = model_registry
        self.console_config = console_config or {}
        self.logger = logging.getLogger(__name__)
               
        self.icon = None  # pystray object, holds menu, state, etc.
        self.is_running = False
        self.current_state = "idle"
        self.available = True
        
        if self._check_tray_availability():
            self._load_icons_to_cache()
    
    def _check_tray_availability(self) -> bool:
        if not self.tray_config['enabled']:
            self.logger.warning("   ✗ System tray disabled in configuration")
            self.available = False

        elif not TRAY_AVAILABLE:
            self.logger.warning("   ✗ System tray not available - pystray or Pillow not installed")
            self.available = False

        return self.available

    def _load_icons_to_cache(self):
        try:
            self.icons = icons.get_tray_icons()
        except Exception as e:
            self.logger.error(f"Failed to load tray icons: {e}")
            self.icons = {
                "idle": self._create_fallback_icon("idle"),
                "recording": self._create_fallback_icon("recording"),
                "processing": self._create_fallback_icon("processing"),
            }
        
    def _create_fallback_icon(self, state: str) -> Image.Image:
        colors = {
            'idle': (128, 128, 128),      # Gray
            'recording': (34, 139, 34),   # Green  
            'processing': (255, 165, 0)   # Orange
        }
        
        color = colors.get(state, (128, 128, 128))  # Default to gray
        icon = Image.new('RGBA', (16, 16), color + (255,))

        return icon
    
    def _build_recent_transcriptions_menu(self):
        try:
            recent = self.state_manager.get_recent_transcriptions()
        except Exception:
            return []
        if not recent:
            return []

        def make_recopy(idx):
            return lambda icon, item: self.state_manager.recopy_recent_transcription(idx)

        items = []
        for i, text in enumerate(recent):
            label = text if len(text) <= 50 else text[:47] + "..."
            items.append(pystray.MenuItem(label, make_recopy(i)))
        return items

    # Rebuilt from scratch on every refresh rather than mutated in place, because
    # pystray menus are immutable once shown; that is how checkmarks and recent
    # transcripts stay current. Only what people reach for from the taskbar is
    # here; settings live in the hub and support tools sit under Help.
    def _create_menu(self):
        chosen_microphone = self.state_manager.configured_microphone()

        def switch_microphone(choice):
            return lambda icon, item: self._select_microphone(choice)

        def is_chosen_microphone(choice):
            return lambda item: chosen_microphone == choice

        microphones = [
            pystray.MenuItem(choice['name'], switch_microphone(choice['id']),
                             radio=True, checked=is_chosen_microphone(choice['id']))
            for choice in self.state_manager.microphone_choices() or []
        ]
        recent = self._build_recent_transcriptions_menu()

        help_items = [
            pystray.MenuItem("Run diagnostics", self._run_doctor_in_window),
            pystray.MenuItem("Save logs for a bug report", self._bundle_logs_action),
            pystray.MenuItem("Open log file", self._open_log_file),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Edit settings file", self._open_config_file),
            pystray.MenuItem("Open settings folder", self._open_config_folder),
            pystray.MenuItem("Show console", self._show_console) if console.owns_console() else None,
        ]

        # The hub is the app's window: a double-click on the tray icon opens it.
        menu_items = [
            pystray.MenuItem("Open Syllune", self._hub_page_opener('home'), default=True),
            pystray.MenuItem("Settings", self._hub_page_opener('settings')),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: "Stop meeting recording" if self.state_manager.meetings.active
                else "Record a meeting",
                self._toggle_meeting),
            pystray.MenuItem("Retry last dictation", self._retry_last_dictation,
                             enabled=lambda item: self.state_manager.has_last_recording()),
            # The pause hotkey's mouse equivalent, for a call or a game where
            # the chord is needed by something else.
            pystray.MenuItem("Pause hotkeys", self._toggle_pause,
                             checked=lambda item: self.state_manager.is_hotkeys_paused()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Microphone", pystray.Menu(*microphones)) if microphones else None,
            pystray.MenuItem("Recent", pystray.Menu(*recent)) if recent else None,
            pystray.MenuItem("Help", pystray.Menu(*[item for item in help_items if item is not None])),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restart Syllune", self._restart_application_from_tray),
            pystray.MenuItem("Exit", self._quit_application_from_tray),
        ]
        return pystray.Menu(*[item for item in menu_items if item is not None])

    def _open_config_folder(self, icon=None, item=None):
        try:
            config_dir = os.path.dirname(self.config_manager.user_settings_path)
            open_file(config_dir)
        except Exception as e:
            self.logger.error(f"Failed to open config folder: {e}")

    def _open_config_file(self, icon=None, item=None):
        try:
            open_file(self.config_manager.user_settings_path)
        except Exception as e:
            self.logger.error(f"Failed to open config file: {e}")

    def _open_log_file(self, icon=None, item=None):
        try:
            log_path = self.config_manager.get_log_file_path()
            open_file(log_path)
        except Exception as e:
            self.logger.error(f"Failed to open log file: {e}")

    # `choice` is "default" or a microphone name; the state manager saves it.
    def _select_microphone(self, choice: str):
        if self.state_manager.select_microphone(choice):
            self._set_menu()
        else:
            self.logger.warning(f"Microphone '{choice}' is not available")

    def _show_console(self, icon=None, item=None):
        console.show()

    def apply_console_settings(self):
        if not console.owns_console() or not self.available:
            return
        if self.console_config.get('start_hidden', False):
            console.hide()
        console.start_minimize_monitor(console.hide)

    def _quit_application_from_tray(self, icon=None, item=None):
        os.kill(os.getpid(), signal.SIGINT)

    # Relaunch, then quit this instance — the new one's single-instance guard
    # takes over the lock. The command comes from utils.build_relaunch_command,
    # the same builder autostart uses, so the two can't disagree.
    #
    # This used to be [sys.executable] + sys.argv, which broke every pip install
    # on Windows (issue #3): argv[0] is the console-script path, and pip ships
    # only a compiled syllune.exe stub there — no plain script for
    # python.exe to open. Extra CLI args are carried over so a restart keeps
    # flags like --test.
    def _restart_application_from_tray(self, icon=None, item=None):
        import subprocess
        import sys
        from .utils import build_relaunch_command
        try:
            command = build_relaunch_command() + sys.argv[1:]
            self.logger.info(f"Restarting via: {command}")
            subprocess.Popen(command)
        except Exception as e:
            # Only quit if the relaunch actually spawned — a failed restart
            # must not leave the user with no app at all.
            self.logger.error(f"Restart failed to relaunch: {e}")
            self.notify("Restart failed — please relaunch manually.")
            return
        os.kill(os.getpid(), signal.SIGINT)

    def _run_doctor_in_window(self, icon=None, item=None):
        self._run_module_in_window('--doctor')

    def _open_settings_window(self, icon=None, item=None):
        self._run_module_in_window('--settings', windowless=True)

    # A tray callback that opens the hub on `page`. Without the Electron
    # overlay the Tk settings window stands in.
    def _hub_page_opener(self, page: str):
        fallbacks = {'settings': self._open_settings_window,
                     'home': self._open_settings_window}

        def open_page(icon=None, item=None):
            try:
                if not self.state_manager.open_hub(page):
                    fallbacks[page]()
            except Exception as e:
                self.logger.error(f"Opening the hub failed: {e}")
        return open_page

    def _toggle_meeting(self, icon=None, item=None):
        try:
            self.state_manager.toggle_meeting()
        except Exception as e:
            self.logger.error(f"Meeting recording failed: {e}")

    def _retry_last_dictation(self, icon=None, item=None):
        try:
            self.state_manager.retry_last_dictation()
        except Exception as e:
            self.logger.error(f"Retry failed: {e}")

    def _toggle_pause(self, icon=None, item=None):
        try:
            self.state_manager.toggle_hotkeys_paused()
            self._set_menu()
        except Exception as e:
            self.logger.error(f"Failed to toggle pause: {e}")

    def _bundle_logs_action(self, icon=None, item=None):
        import threading

        def work():
            try:
                from .bundle_logs import bundle_logs
                import datetime
                stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
                output = str(Path.home() / 'Desktop' / f'syllune-logs-{stamp}.zip')
                bundle_logs(output)
                self.notify(f"Logs saved to your Desktop: syllune-logs-{stamp}.zip")
            except Exception as e:
                self.logger.error(f"Bundle logs failed: {e}")
                self.notify(f"Bundle failed: {e}")
        threading.Thread(target=work, daemon=True, name='bundle-logs').start()

    # Console tools (--doctor, --stats) get a console of their own to print in.
    # Tk windows (--settings, --history) get none: a black console popping up
    # behind the Settings window is the single most-asked "is this broken?"
    # about the tray. The command comes from build_relaunch_command, so a pip
    # install, a venv and the standalone .exe all relaunch the right thing.
    def _run_module_in_window(self, flag: str, windowless: bool = False):
        import subprocess
        from .utils import build_relaunch_command
        try:
            if windowless:
                command = build_relaunch_command(windowless=True) + [flag]
                creation_flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            else:
                command = build_relaunch_command() + [flag]
                creation_flags = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0)
            subprocess.Popen(command, creationflags=creation_flags)
        except Exception as e:
            self.logger.error(f"Failed to launch {flag}: {e}")
    
    # Every mutation of the tray object goes through here.
    #
    # On macOS these reach AppKit, which must only be touched from the main
    # thread. Through macOS 26 doing otherwise merely logged a warning; macOS 27
    # made it a hard SIGTRAP that kills the process outright (issue #13). The
    # callers here are worker threads — the recording thread drives update_state()
    # and the level monitor rewrites the title every 150 ms — so without this the
    # app dies on the first hotkey press. SIGTRAP is not a Python exception, so
    # the try/except below is for ordinary failures only; it could never have
    # caught the crash.
    #
    # On Windows run_on_ui_thread() calls straight through, so this costs nothing.
    def _on_ui_thread(self, mutate, description: str):
        def guarded():
            try:
                mutate()
            except Exception as e:
                self.logger.debug(f"Tray {description} failed: {e}")
        try:
            platform_app.run_on_ui_thread(guarded)
        except Exception as e:
            self.logger.debug(f"Could not dispatch tray {description}: {e}")

    # Menus are BUILT off-thread (pure Python) and only ASSIGNED on the UI
    # thread, which is the part that reaches NSStatusItem.
    def _set_menu(self):
        if not self.icon:
            return
        menu = self._create_menu()
        self._on_ui_thread(lambda: setattr(self.icon, 'menu', menu), 'menu update')

    def notify(self, message: str, title: str = "Syllune"):
        if not TRAY_AVAILABLE or not self.is_running or not self.icon:
            return
        self._on_ui_thread(lambda: self.icon.notify(message, title), 'notify')

    def update_state(self, new_state: str):
        if not TRAY_AVAILABLE or not self.is_running:
            return

        self.current_state = new_state

        art = self.icons.get(new_state)
        if art is not None:
            self._on_ui_thread(lambda: setattr(self.icon, 'icon', art), 'icon art')
        self._set_menu()

        if new_state == "recording":
            self._start_level_monitor()
        else:
            self._stop_level_monitor()
            tooltip = self.tray_config.get('tooltip', 'Syllune')
            self._on_ui_thread(lambda: setattr(self.icon, 'title', tooltip), 'tooltip')

    def _start_level_monitor(self):
        import threading
        prev_thread = getattr(self, '_level_thread', None)
        if prev_thread and prev_thread.is_alive():
            prev_stop = getattr(self, '_level_stop', None)
            if prev_stop:
                prev_stop.set()
            prev_thread.join(timeout=0.3)
        self._level_stop = threading.Event()

        def loop():
            while not self._level_stop.is_set():
                try:
                    level = self.state_manager.audio_recorder.get_current_level()
                except Exception:
                    level = 0.0
                bars = self._level_bars(level)
                if self.icon:
                    title = f"Syllune · 🎤 {bars}"
                    self._on_ui_thread(
                        lambda t=title: setattr(self.icon, 'title', t), 'level title')
                self._level_stop.wait(0.15)

        self._level_thread = threading.Thread(target=loop, daemon=True, name="tray-level")
        self._level_thread.start()

    def _stop_level_monitor(self):
        stop = getattr(self, '_level_stop', None)
        if stop:
            stop.set()

    def _level_bars(self, level: float) -> str:
        bins = ['░░░░░░', '█░░░░░', '██░░░░', '███░░░', '████░░', '█████░', '██████']
        scaled = min(int(level * 60), len(bins) - 1)
        return bins[scaled]

    def refresh_menu(self):
        self._set_menu()
    
    def start(self):
        if not self.available:
            return False

        if self.is_running:
            self.logger.warning("System tray is already running")
            return True

        try:
            idle_icon = self.icons.get("idle")
            menu = self._create_menu()

            self.icon = pystray.Icon(
                name="syllune",
                icon=idle_icon,
                title="Syllune",
                menu=menu
            )

            self.icon.run_detached()

            self.is_running = True
            print("   ✓ System tray icon is running...")

            return True

        except Exception as e:
            self.logger.error(f"Failed to start system tray: {e}")
            return False
    
    def stop(self):
        if not self.is_running:
            return

        try:
            self.icon.stop()
            self.is_running = False

        except Exception as e:
            self.logger.error(f"Error stopping system tray: {e}")
