# state_manager.py
# Central coordinator that owns the dictation pipeline state and routes events
# between components. Roughly:
#
#   HotkeyListener  ─►  StateManager  ─►  AudioRecorder.stop_recording()
#                            │                       │
#                            ▼                       ▼
#                     WhisperEngine.transcribe ──► ClipboardManager.deliver
#                                                     │
#                                                     ▼
#                                            stats + transcripts + audit
#
# Also owns the LevelOverlay + FallbackWindow lifecycle and the cross-cutting
# concerns: profile activation, app-rule application, AI rephrase, command mode,
# and the continuous-dictation restart loop.
#
# Thread safety: most state lives behind self._state_lock. The audio capture
# thread writes recording-status atomically; everything else mutates state
# only from the main thread or via the lock.

import collections
import logging
import os
import time
import threading
import platform
from contextlib import nullcontext
from pathlib import Path
from typing import Optional

import sounddevice as sd

from .audio_recorder import AudioRecorder
from .whisper_engine import WhisperEngine
from .clipboard_manager import ClipboardManager
from .system_tray import SystemTray
from .config_manager import ConfigManager
from .audio_feedback import AudioFeedback
from .utils import OptionalComponent
from .voice_activity_detection import VadEvent, VadManager
from .voice_commands import VoiceCommandManager
from .profiles import ProfileManager
from .app_rules import AppRules, formatting_overrides as app_rules_formatting_overrides, effective_postprocess_config
from .streaming_delivery import StreamingDelivery, decide_stream_delivery
from .transforms import TransformsManager
from .text_postprocess import postprocess, finish_style
from .smart_edit import SmartEditor
from .media_pause import MediaPause
from .game_mode import GameModeWatcher

# Beam width per quality. Fast also skips the AI edit.
QUALITY_BEAMS = {'fast': 1, 'balanced': 3, 'best': 5}
from .hub_api import HubApi
from .meetings import MeetingController, MeetingStore
from .stats import record_transcription
from .audit_log import record as audit_record
from .terminal_title import TerminalTitle
from .transcript_log import record_transcript, pending_transcripts, update_delivery
from .recording_archive import save_recording
from .pending_delivery import PendingDelivery
from .platform import foreground, audio_endpoints
from .level_overlay import LevelOverlay
from .fallback_window import FallbackWindow

# This app's own windows (pill, hub): the installed Syllune.exe, or in a
# development checkout the Electron binary under this project's overlay folder.
_OVERLAY_DIR = os.path.normcase(str(Path(__file__).resolve().parents[2] / 'overlay'))


def _is_own_window(target) -> bool:
    exe = str(target.get('exe', '')).lower()
    path = os.path.normcase(str(target.get('path', '')))
    return exe == 'syllune.exe' or ('electron' in exe and path.startswith(_OVERLAY_DIR))


class StateManager:
    def __init__(self,
                 audio_recorder: AudioRecorder,
                 whisper_engine: WhisperEngine,
                 clipboard_manager: ClipboardManager,
                 config_manager: ConfigManager,
                 vad_manager: VadManager,
                 system_tray: Optional[SystemTray] = None,
                 audio_feedback: Optional[AudioFeedback] = None,
                 voice_command_manager: Optional[VoiceCommandManager] = None,
                 terminal_title: Optional[TerminalTitle] = None):

        self.audio_recorder = audio_recorder
        self.whisper_engine = whisper_engine
        self.clipboard_manager = clipboard_manager
        self.system_tray = OptionalComponent(system_tray)
        self.terminal_title = OptionalComponent(terminal_title)
        self.config_manager = config_manager
        self.audio_feedback = OptionalComponent(audio_feedback)
        self.vad_manager = vad_manager
        self.voice_command_manager = voice_command_manager

        self.is_processing = False
        self.is_model_loading = False
        self.is_paused = False
        self.last_transcription = None
        self.recent_transcriptions = collections.deque(maxlen=10)
        self._rephrase_mode = False
        self._rephrase_selection = ''
        self._rephrase_original_clipboard = ''
        self._continuous_aborted = False
        self._hotkey_listener_ref = None
        self._pending_model_change = None
        self._pending_quality = None
        self._quality_lock = threading.RLock()
        self._pending_device_change = None
        self._command_mode = False
        # Set by the note hotkey: this recording is saved as a note, not typed.
        self._note_mode = False
        self._state_lock = threading.Lock()
        # Serialises recorder start/stop against a recorder swap (device change,
        # the default-mic watcher). Without it a hotkey press could start a
        # recording on a recorder that is being shut down and replaced, and the
        # matching release would then stop the new one, which never recorded.
        # Re-entrant: a swap queued from the pipeline runs on the same thread.
        self._recorder_lock = threading.RLock()
        self._streaming_display_active = False
        # Commit-on-endpoint streaming delivery (opt-in). Active only for the
        # current recording when conditions are met; default off → zero impact.
        self._streaming_delivery_active = False
        self.streaming_delivery = None

        self.logger = logging.getLogger(__name__)
        self._current_audio_host = None
        self._initialize_audio_host()
        self.profile_manager = ProfileManager(config_manager)
        self.app_rules = AppRules()
        # Transforms run on the AI edit's model and endpoint, so they work with
        # whatever is installed in Ollama rather than the legacy default.
        self.transforms_manager = TransformsManager(
            ollama_config_provider=lambda: {
                **(config_manager.get_postprocess_config().get('ollama') or {}),
                'model': config_manager.get_ai_config().get('model') or 'gemma3:4b',
                'endpoint': config_manager.get_ai_config().get('endpoint') or 'http://127.0.0.1:11434',
                'timeout': 30,
            },
        )
        self.level_overlay = None
        self.fallback_window = FallbackWindow()
        # The local AI edit after Whisper (punctuation, tone, prompt layout).
        self.smart_editor = SmartEditor(config_manager.get_ai_config)
        # Pauses music/videos while the mic is open, resumes them after.
        self.media_pause = MediaPause(
            lambda: bool((self.config_manager.config.get('audio') or {}).get('pause_media_on_record', True)))
        self.game_mode = GameModeWatcher(self._on_game_mode, self._listed_games)
        # Answers the hub window's requests (stats, history, settings, ...).
        self.hub_api = HubApi(self, config_manager)
        # Meeting recordings: recorded on their own threads, then turned into
        # a transcript with speakers and a summary in the background.
        from .utils import get_user_app_data_path
        from pathlib import Path
        self.meetings = MeetingController(
            MeetingStore(Path(get_user_app_data_path()) / 'meetings'),
            # Late-bound: a model switch or device change replaces these.
            engine=lambda: self.whisper_engine,
            editor=self.smart_editor,
            game_active=lambda: bool(self.game_mode.active),
            mic_device=lambda: self.audio_recorder.get_device_id(),
            notes_folder=self.meeting_notes_folder,
            ai_enabled=lambda: bool(config_manager.get_ai_config().get('enabled', True)),
            sync_ai=self.sync_ai_model,
            on_change=self._on_meetings_changed,
            on_ready=self._on_meeting_ready,
        )
        # The meeting start time the pill and tray last showed, so progress
        # updates during processing don't redraw them twice a second.
        self._shown_meeting_start = None
        self._target_watcher = None
        self.pending_delivery = PendingDelivery(
            pending_transcripts, update_delivery,
            lambda text: self.clipboard_manager.deliver_transcription(text, False),
            self._pending_target_allowed, self._current_text_target, self._refresh_pending)

    def attach_components(self,
                          audio_recorder: AudioRecorder,
                          system_tray: Optional[SystemTray]):
        self.audio_recorder = audio_recorder
        self.system_tray = OptionalComponent(system_tray)
        self._ensure_audio_device_for_host(self._current_audio_host)
        self._start_default_input_watcher()

        try:
            self.transforms_manager.system_tray = system_tray
        except Exception:
            pass

        overlay_cfg = self.config_manager.config.get('overlay', {}) or {}
        if overlay_cfg.get('enabled', True):
            self.level_overlay = self._start_electron_overlay(overlay_cfg)
        if overlay_cfg.get('enabled', True) and not self.level_overlay:
            try:
                self.level_overlay = LevelOverlay(
                    # Late-bound: a device switch replaces self.audio_recorder.
                    level_provider=lambda: self.audio_recorder.get_current_level(),
                    click_through=overlay_cfg.get('click_through', True),
                    position=overlay_cfg.get('position', 'bottom-center'),
                )
                self.level_overlay.start()
            except Exception as e:
                self.logger.warning(f"Level overlay disabled: {e}")
                self.level_overlay = None

        self.game_mode.start()
        self.apply_quality()
        # After game mode, so a meeting left over from last run waits for a
        # game that is already running.
        self.meetings.start()
        self._refresh_pending()

    # =========================================================================
    # Quality, AI edit and game mode
    # =========================================================================

    def current_quality(self) -> str:
        quality = str(self.config_manager.get_whisper_config().get('quality') or 'balanced').lower()
        return quality if quality in ('fast', 'balanced', 'best') else 'balanced'

    # whisper.quality → beam size and the AI edit, nothing else. It used to
    # swap Best onto large-v3, a silent 3 GB download that refused every
    # dictation until it finished, so changing quality "broke" dictation.
    # Switching is now instant and never touches the speech model.
    def apply_quality(self, quality: str = None):
        quality = self.current_quality() if quality is None else str(quality).lower()
        if quality not in ('fast', 'balanced', 'best'):
            raise ValueError('Quality must be fast, balanced or best')
        with getattr(self, '_quality_lock', nullcontext()):
            if self.get_current_state() != 'idle':
                self._pending_quality = quality
                return
            self._commit_quality(quality)

    # Commit the settings as one completed change; optional AI is best effort.
    def _commit_quality(self, quality):
        self.config_manager.update_user_setting('whisper', 'quality', quality)
        self.whisper_engine.beam_size = QUALITY_BEAMS[quality]
        if getattr(self.whisper_engine, 'model', None) is not None:
            try:
                self.sync_ai_model()
            except Exception:
                self.logger.warning('Optional AI model preparation failed', exc_info=True)
        self._notify_hub('settings')
        self.logger.info(f"Quality: {quality} (beam {self.whisper_engine.beam_size})")

    # The newest request wins while an existing recording or model change runs.
    def _drain_pending_changes(self):
        with getattr(self, '_quality_lock', nullcontext()):
            if self.get_current_state() != 'idle':
                return
            quality = getattr(self, '_pending_quality', None)
            self._pending_quality = None
            if quality is not None:
                self.apply_quality(quality)
            if self.get_current_state() == 'idle' and self._pending_model_change:
                key, self._pending_model_change = self._pending_model_change, None
                self.request_model_change(key)

    # Puts the AI model where the settings want it: loaded for the next
    # dictation, or out of VRAM when AI is off, quality is Fast, or a game is
    # running. Also restores the dictation context after a meeting summary.
    def sync_ai_model(self):
        if self.current_quality() != 'fast' and self.smart_editor.enabled():
            self.smart_editor.preload()
        else:
            self.smart_editor.unload()

    def _listed_games(self):
        for rule in self.app_rules.rules or ():
            if rule.get('id') == 'games' and not rule.get('disabled'):
                match = rule.get('match') or ()
                return [match] if isinstance(match, str) else list(match)
        return ()

    # Game mode: the AI model leaves VRAM (if ai.unload_in_games) and the idle
    # pill hides, until the game exits.
    def _on_game_mode(self, active: bool):
        if self.config_manager.get_ai_config().get('unload_in_games', True):
            self.smart_editor.set_game_mode(active)
        set_game_mode = getattr(self.level_overlay, 'set_game_mode', None)
        if set_game_mode:
            set_game_mode(active)
        if not active:
            self.sync_ai_model()
            self.meetings.wake()
        if hasattr(self, 'pending_delivery'):
            self._refresh_pending()

    # Terms the AI edit must spell exactly: your hotwords and the canonical
    # side of your corrections and replacements.
    def _vocabulary(self, postprocess_cfg: dict) -> list:
        terms = list(self.config_manager.get_whisper_config().get('hotwords') or [])
        corrections = postprocess_cfg.get('corrections')
        if isinstance(corrections, dict):
            terms.extend(str(term) for term in corrections)
        for item in postprocess_cfg.get('replacements') or ():
            if isinstance(item, dict) and item.get('to'):
                terms.append(str(item['to']))
        seen, unique = set(), []
        for term in terms:
            if term.lower() not in seen:
                seen.add(term.lower())
                unique.append(term)
        return unique

    # The AI edit on top of the deterministic cleanup. Skipped in fast mode, for
    # a whole-snippet dictation, verbatim apps and rules with `ai: false`. The
    # style's own finishing touches run again afterwards, since the model
    # writes conventional prose.
    def _smart_edit(self, text: str, rule, raw_cfg: dict, cfg: dict, info: dict) -> str:
        rule = rule or {}
        if (self.current_quality() == 'fast' or info.get('whole_snippet') or rule.get('ai') is False
                or not text.strip()):
            return text
        style = rule.get('style') or raw_cfg.get('style') or ''
        try:
            edited = self.smart_editor.edit(
                text, style=style, instructions=str(rule.get('instructions') or ''),
                vocabulary=self._vocabulary(raw_cfg),
                language=getattr(self.whisper_engine, 'last_language', None) or '')
            if not edited:
                return text
            print(f"   ✓ AI edit ({self.smart_editor.last_ms} ms): '{edited}'")
            return finish_style(edited, cfg)
        except Exception:
            self.logger.warning('Optional AI edit failed; keeping original transcript', exc_info=True)
            return text

    # The animated Electron pill (overlay/). Returns None when Electron isn't
    # installed or fails to launch, so the Tk pill takes over.
    def _start_electron_overlay(self, overlay_cfg: dict):
        from .electron_overlay import ElectronOverlay
        from .notes import notes_folder
        notes_cfg = self.config_manager.config.get('notes', {}) or {}
        try:
            notes_dir = notes_folder(notes_cfg.get('folder'))
        except OSError as e:
            self.logger.warning(f"Notes folder unavailable: {e}")
            notes_dir = None
        try:
            overlay = ElectronOverlay(
                # Late-bound: a device switch replaces self.audio_recorder.
                level_provider=lambda: self.audio_recorder.get_current_level(),
                locked_provider=self._hands_free_locked,
                spectrum_provider=lambda: self.audio_recorder.get_spectrum_db(),
                on_action=self._on_overlay_action,
                on_rpc=self.hub_api.handle,
                position=overlay_cfg.get('position', 'bottom-center'),
                idle_pill=overlay_cfg.get('idle_pill', True),
                notes_dir=notes_dir,
            )
            overlay.start()
            return overlay
        except Exception as e:
            self.logger.warning(f"Electron overlay unavailable, using Tk pill: {e}")
            return None

    # True while either push-to-talk key is double-tap latched (hands-free).
    def _hands_free_locked(self) -> bool:
        listener = self._hotkey_listener_ref
        if not listener:
            return False
        latches = (getattr(listener, 'tap_latch', None), getattr(listener, 'note_latch', None))
        return any(latch and latch.state == latch.LATCHED for latch in latches)

    # Clicks on the pill. A click on the resting pill starts a hands-free
    # dictation (the next hotkey tap or the pill's stop button ends it); the
    # hands-free controls stop or cancel it. While a meeting records, the pill
    # opens the meeting or stops it instead.
    def _on_overlay_action(self, message: dict):
        action = message.get('action')
        if action in ('open_hub', 'pending_open'):
            self.open_hub('history' if action == 'pending_open' else 'home')
            return
        # The pill was dragged and snapped to one of the fixed positions.
        if action == 'pill_position':
            from .electron_overlay import ElectronOverlay
            if message.get('position') in ElectronOverlay.POSITIONS:
                self.set_overlay_position(message['position'])
            return
        if action == 'meeting_stop':
            self.stop_meeting()
            return
        if action == 'meeting_start':
            self.start_meeting()
            return
        if action == 'meeting_open':
            self.open_hub('meetings')
            return
        listener = self._hotkey_listener_ref
        latches = [getattr(listener, name, None) for name in ('tap_latch', 'note_latch')] if listener else []
        if action == 'dictate':
            if self.audio_recorder.get_recording_status():
                return
            self.start_recording()
            if self.audio_recorder.get_recording_status() and latches and latches[0]:
                latches[0].latch()
        elif action in ('stop', 'cancel'):
            for latch in latches:
                if latch:
                    latch.reset()
            if not self.audio_recorder.get_recording_status():
                return
            if action == 'stop':
                self.stop_recording()
            else:
                print("🎤 Recording cancelled!")
                self.cancel_active_recording()

    # The hub window (overlay/hub) on one of its pages. Returns False when the
    # Electron overlay isn't running, so the tray can fall back.
    def open_hub(self, page: str = 'home') -> bool:
        open_window = getattr(self.level_overlay, 'open_hub', None)
        if not open_window:
            return False
        open_window(page)
        return True

    # Tray "Notes" item: the hub's Notes page, or the notes folder itself
    # without Electron.
    def open_notes(self):
        if self.open_hub('notes'):
            return
        from .notes import notes_folder
        from .utils import open_file
        notes_cfg = self.config_manager.config.get('notes', {}) or {}
        open_file(str(notes_folder(notes_cfg.get('folder'))))

    # Lets an open hub refresh what it shows, e.g. after a new dictation.
    def _notify_hub(self, event: str):
        send = getattr(self.level_overlay, 'hub_event', None)
        if send:
            send(event)

    # =========================================================================
    # Meetings
    # =========================================================================

    # Editable targets are verified through accessibility, not executable names.
    def _text_target_module(self):
        import importlib
        from .platform import PLATFORM
        if PLATFORM not in ('windows', 'macos'):
            return None
        return importlib.import_module(f'{__package__}.platform.{PLATFORM}.text_target')

    def _current_text_target(self):
        try:
            module = self._text_target_module()
            return module.current_text_target() if module else None
        except Exception as e:
            self.logger.debug(f"Editable target unavailable: {e}")
            return None

    def _pending_target_allowed(self, target):
        if (self.is_paused or self.is_processing or self.is_model_loading or self.game_mode.active
                or self.audio_recorder.get_recording_status() or not self.clipboard_manager.auto_paste):
            return False
        if _is_own_window(target):
            return False
        rule = self.app_rules.match_for_foreground() or {}
        return not rule.get('suppress') and rule.get('auto_paste') is not False

    # Watch for clicks only while a queue exists and the user is not gaming.
    def _refresh_pending(self):
        entries = pending_transcripts()
        set_pending = getattr(self.level_overlay, 'set_pending', None)
        if set_pending:
            set_pending(len(entries))
        self._notify_hub('transcript')
        watch = bool(entries) and not self.game_mode.active
        if watch and self._target_watcher is None:
            try:
                module = self._text_target_module()
                if module:
                    self._target_watcher = module.TextTargetWatcher(self.pending_delivery.insert)
                    self._target_watcher.start()
            except Exception as e:
                self.logger.warning(f"Pending insertion watcher unavailable: {e}")
                self._target_watcher = None
        elif not watch and self._target_watcher is not None:
            self._target_watcher.stop()
            self._target_watcher = None

    # Save before delivery so a failed paste or a missing field cannot lose text.
    def _save_dictation(self, text, audio, duration, original):
        recording_id = None
        history = self.config_manager.config.get('history') or {}
        if history.get('keep_audio', True) and self._history_retention_days() != 0:
            try:
                recording_id = save_recording(audio)
            except Exception as e:
                self.logger.warning(f"Could not retain dictation audio: {e}")
        fg = foreground.get_foreground_app() or {}
        entry = record_transcript(text, app=fg.get('exe', ''), duration_s=duration,
            retention_days=self._history_retention_days(), delivery='pending',
            recording_id=recording_id, original_text=original)
        self.last_transcription = text
        self.recent_transcriptions.appendleft(text)
        self.system_tray.refresh_menu()
        if not entry and self._history_retention_days() != 0:
            self.system_tray.notify('Could not save history. Use Copy from Recent transcripts.')
        return entry

    # Starts recording a meeting (tray, hub). Returns False when the mic
    # couldn't be opened; the reason has been shown on the pill.
    def start_meeting(self) -> bool:
        try:
            self.meetings.start_meeting()
            return True
        except RuntimeError as e:
            self.logger.error(f"Meeting not started: {e}")
            flash_failure = getattr(self.level_overlay, 'flash_failure', None)
            if flash_failure:
                flash_failure(str(e))
            return False

    def stop_meeting(self):
        self.meetings.stop_meeting()

    def toggle_meeting(self):
        if self.meetings.active:
            self.stop_meeting()
        else:
            self.start_meeting()

    # Meeting notes go in their own folder inside your notes folder.
    def meeting_notes_folder(self):
        from .notes import notes_folder
        notes_cfg = self.config_manager.config.get('notes', {}) or {}
        return notes_folder(notes_cfg.get('folder')) / 'Meetings'

    # Called on every meeting change, including progress while one is being
    # processed: the hub refreshes each time, the pill and tray only when a
    # recording starts or stops.
    def _on_meetings_changed(self):
        self._notify_hub('meetings')
        started = self.meetings.started_ms()
        if started == self._shown_meeting_start:
            return
        self._shown_meeting_start = started
        set_meeting = getattr(self.level_overlay, 'set_meeting', None)
        if set_meeting:
            set_meeting(started)
        self.system_tray.refresh_menu()

    def _on_meeting_ready(self, meeting: dict):
        print(f"📝 Meeting notes ready: {meeting.get('markdown')}")
        meeting_ready = getattr(self.level_overlay, 'meeting_ready', None)
        if meeting_ready:
            meeting_ready()

    def handle_max_recording_duration_reached(self, audio_data):
        self.logger.info("Max recording duration reached - starting transcription")
        self.media_pause.resume()
        self._run_pipeline_async(audio_data, use_auto_enter=False)

    def handle_vad_event(self, event: VadEvent):
        if event == VadEvent.SILENCE_TIMEOUT:
            self.logger.info("VAD silence timeout detected - stopping recording")
            timeout_seconds = int(self.vad_manager.vad_silence_timeout_seconds)
            self._clear_streaming_display()
            print(f"⏰ Stopping recording after {timeout_seconds} seconds of silence...")
            with self._recorder_lock:
                audio_data = self.audio_recorder.stop_recording()
            self.media_pause.resume()
            self._run_pipeline_async(audio_data, use_auto_enter=False)

    # Called on the audio thread for each streaming result. Updates the overlay
    # preview always; when commit-on-endpoint delivery is active, hands FINALIZED
    # phrases to the delivery worker (never the revising partials).
    def handle_streaming_result(self, text: str, is_final: bool):
        if is_final:
            if self._streaming_display_active:
                print(f"\r   {text:<70}")
                self._streaming_display_active = False
            if self._streaming_delivery_active and self.streaming_delivery:
                self.streaming_delivery.submit_final(text)
        else:
            display_text = text if len(text) < 67 else "..." + text[-64:]
            print(f"\r   {display_text:<70}", end="", flush=True)
            self._streaming_display_active = True
        if self.level_overlay and text:
            self.level_overlay.set_streaming_text(text)

    def _clear_streaming_display(self):
        if self._streaming_display_active:
            print("\r" + " " * 75 + "\r", end="", flush=True)
            self._streaming_display_active = False
    
    # Returns as soon as the audio is captured; the transcription runs on its
    # own thread (see _run_pipeline_async).
    def stop_recording(self, use_auto_enter: bool = False) -> bool:
        with self._recorder_lock:
            if not self.audio_recorder.get_recording_status():
                return False
            self._clear_streaming_display()
            audio_data = self.audio_recorder.stop_recording()
        self.media_pause.resume()
        self._run_pipeline_async(audio_data, use_auto_enter)
        return True
    
    def cancel_active_recording(self):
        self._clear_streaming_display()
        # Tear down the streaming-delivery worker (already-typed text stays — you
        # can't un-type live output).
        if self.streaming_delivery:
            try:
                self.streaming_delivery.stop()
            except Exception:
                pass
            self.streaming_delivery = None
        self._streaming_delivery_active = False
        with self._state_lock:
            self._command_mode = False
            self._rephrase_mode = False
            self._rephrase_selection = ''
        self._continuous_aborted = True
        with self._recorder_lock:
            self.audio_recorder.cancel_recording()
        self.media_pause.resume()
        self.audio_feedback.play_cancel_sound()
        self._update_ui_state("idle")
        if self.level_overlay:
            self.level_overlay.hide()
        self._drain_pending_changes()
    
    def cancel_recording_hotkey_pressed(self) -> bool:
        current_state = self.get_current_state()
        
        if current_state == "recording":
            print("🎤 Recording cancelled!")            
            self.cancel_active_recording()
            return True
        else:
            return False
    
    def start_recording(self):
        if not self.can_start_recording():
            current_state = self.get_current_state()
            if self.is_processing:
                print("⏳ Still processing previous recording...")
            elif self.is_model_loading:
                print("⏳ Still loading model...")
            else:
                print(f"⏳ Cannot record while {current_state}...")
            return

        self._begin_recording()

    def set_paused(self, paused: bool):
        self.is_paused = paused
        if paused and self.audio_recorder.get_recording_status():
            self.cancel_active_recording()
        self.system_tray.notify("Hotkeys paused" if paused else "Hotkeys resumed")

    def start_rephrase_recording(self):
        if not self.can_start_recording():
            return
        import time
        import pyperclip
        from .platform import keyboard as kb

        try:
            self._rephrase_original_clipboard = pyperclip.paste()
        except Exception:
            self._rephrase_original_clipboard = ''

        kb.send_copy()
        time.sleep(0.12)
        try:
            selection = pyperclip.paste()
        except Exception:
            selection = ''
        try:
            pyperclip.copy(self._rephrase_original_clipboard)
        except Exception:
            pass

        if not selection or selection == self._rephrase_original_clipboard:
            print("\n   ✗ Nothing selected — rephrase needs selected text. Recording dictation instead.")
            self._rephrase_selection = ''
        else:
            self._rephrase_selection = selection
            print(f"\n🎙  Rephrase mode: selection captured ({len(selection)} chars)")
            print("   Speak instructions, then release to apply.")

        self._rephrase_mode = True
        self._streaming_delivery_active = False  # rephrase is never live-typed
        self.streaming_delivery = None
        self.media_pause.pause()
        with self._recorder_lock:
            started = self.audio_recorder.start_recording()
        if not started:
            self.media_pause.resume()
        if started:
            self.audio_feedback.play_start_sound()
            self._update_ui_state("recording")
            if self.level_overlay:
                self.level_overlay.show_recording()
            self._schedule_silent_mic_check()

    def start_note_recording(self):
        if not self.can_start_recording():
            return
        with self._state_lock:
            self._note_mode = True
        self._streaming_delivery_active = False  # notes are never live-typed
        self.streaming_delivery = None
        self.media_pause.pause()
        with self._recorder_lock:
            success = self.audio_recorder.start_recording()
        if not success:
            self.media_pause.resume()
        if success:
            print("\n📝 Note recording started! Speak now...")
            self.audio_feedback.play_start_sound()
            self._update_ui_state("recording")
            if self.level_overlay:
                show_note = getattr(self.level_overlay, 'show_note_recording', None)
                (show_note or self.level_overlay.show_recording)()
            self._schedule_silent_mic_check()
        else:
            with self._state_lock:
                self._note_mode = False

    # Transcribe a note into paragraphs, tidy each with the normal cleanup
    # pipeline in the formal style, and save it as a markdown file.
    def _handle_note(self, audio_data):
        from .notes import save_note, notes_folder
        paragraphs = self.whisper_engine.transcribe_paragraphs(audio_data)
        if not paragraphs:
            if self.level_overlay:
                self.level_overlay.flash_failure("No speech in note")
            return
        cfg = dict(self.config_manager.get_postprocess_config())
        vocabulary = self._vocabulary(cfg)
        cfg['style'] = 'formal'
        cleaned = [p for p in (postprocess(p, cfg).strip() for p in paragraphs) if p]
        # Each paragraph through the AI edit too, with room for long ones.
        if self.current_quality() != 'fast':
            language = getattr(self.whisper_engine, 'last_language', None) or ''
            for index, paragraph in enumerate(cleaned):
                try:
                    cleaned[index] = self.smart_editor.edit(
                        paragraph, style='note', vocabulary=vocabulary,
                        language=language, timeout=30) or paragraph
                except Exception:
                    self.logger.warning('Optional note AI edit failed; keeping paragraph', exc_info=True)
        if not cleaned:
            if self.level_overlay:
                self.level_overlay.flash_failure("Nothing to save")
            return
        notes_cfg = self.config_manager.config.get('notes', {}) or {}
        path = save_note("\n\n".join(cleaned), notes_folder(notes_cfg.get('folder')))
        print(f"   ✓ Note saved: {path}")
        self.audio_feedback.play_transcription_complete_sound()
        if self.level_overlay:
            note_saved = getattr(self.level_overlay, 'note_saved', None)
            if note_saved:
                note_saved(path)
            else:
                self.level_overlay.flash_success()

    def start_command_recording(self):
        if not self.can_start_recording():
            return

        with self._state_lock:
            self._command_mode = True
        self._streaming_delivery_active = False  # command mode is never live-typed
        self.streaming_delivery = None

        self.logger.info("Starting command mode recording")
        self.media_pause.pause()
        with self._recorder_lock:
            success = self.audio_recorder.start_recording()
        if not success:
            self.media_pause.resume()
        if success:
            print("\n🎤 Command mode activated! Speak a command...")
            self.config_manager.print_command_stop_instructions()
            self.audio_feedback.play_start_sound()
            self._update_ui_state("recording")
            if self.level_overlay:
                self.level_overlay.show_recording()
            self._schedule_silent_mic_check()

    def _begin_recording(self):
        self._apply_recording_context()
        self.media_pause.pause()
        with self._recorder_lock:
            success = self.audio_recorder.start_recording()
        if not success:
            self.media_pause.resume()

        if success:
            # Only spin up the streaming-delivery worker once recording is
            # confirmed — otherwise a failed start would leak the worker thread.
            self._setup_streaming_delivery()
            print("\n🎤 Recording started! Speak now...")
            self.config_manager.print_stop_instructions_based_on_config()
            self.audio_feedback.play_start_sound()
            self._update_ui_state("recording")
            if self.level_overlay:
                self.level_overlay.show_recording()
            self._schedule_silent_mic_check()

    # Decide whether this (plain dictation) recording should stream finalized
    # phrases to the cursor live, and if so spin up the delivery worker. Only the
    # plain-dictation path calls this; command/rephrase never stream-deliver.
    def _setup_streaming_delivery(self):
        self._streaming_delivery_active = False
        self.streaming_delivery = None
        streaming_available = bool(getattr(self.audio_recorder, 'continuous_streaming', None))
        active = decide_stream_delivery(
            self.config_manager.get_streaming_config(),
            streaming_available,
            bool(self.clipboard_manager.auto_paste),
            self._foreground_is_textable(),
            self.app_rules.match_for_foreground(),
        )
        if not active:
            return
        self.streaming_delivery = StreamingDelivery(
            deliver_fn=lambda seg: self.clipboard_manager.deliver_transcription(seg, use_auto_enter=False)
        )
        self.streaming_delivery.start()
        self._streaming_delivery_active = True
        self.logger.info("Streaming commit-on-endpoint delivery active for this recording")

    # Flush the trailing in-progress phrase, wait for the worker to finish typing
    # everything, then record + (optionally) press Enter. Returns the delivered
    # text, or '' if nothing was finalized (caller then falls back to Whisper).
    def _finalize_streaming_delivery(self, use_auto_enter: bool, duration: float) -> str:
        sd = self.streaming_delivery
        self._streaming_delivery_active = False
        self.streaming_delivery = None
        if sd is None:
            return ''

        # Deliver the un-endpointed remainder, if any.
        try:
            cont = getattr(self.audio_recorder, 'continuous_streaming', None)
            if cont:
                trailing = cont.finalize()
                if trailing:
                    sd.submit_final(trailing)
        except Exception as e:
            self.logger.debug(f"Streaming flush failed: {e}")

        text = sd.stop()  # drains + joins the worker: all text is on screen now
        if not text:
            return ''

        # If a segment failed to type, some words may be missing. We can't re-run
        # Whisper (it would duplicate what's already typed), so warn instead.
        if sd.had_failure:
            self.logger.warning("Streaming delivery had a failed segment; some words may be missing")
            self.system_tray.notify("Some dictated words may not have typed — please review.")

        if use_auto_enter:
            self.clipboard_manager.send_enter_key()

        self.last_transcription = text
        self.recent_transcriptions.appendleft(text)
        self.system_tray.refresh_menu()
        self.audio_feedback.play_transcription_complete_sound()
        if self.level_overlay:
            self.level_overlay.flash_success()

        fg = foreground.get_foreground_app() or {}
        record_transcription(char_count=len(text), duration_seconds=duration, app=fg.get('exe', ''))
        record_transcript(text, app=fg.get('exe', ''), duration_s=duration,
                          retention_days=self._history_retention_days())
        self._notify_hub('transcript')
        audit_enabled = (self.config_manager.config.get('audit') or {}).get('enabled', False)
        audit_record('delivered', text, fg.get('exe', ''), audit_enabled)

        self.logger.info(f"Streaming delivery complete: {len(text)} chars")
        self._maybe_restart_continuous()
        return text

    def _maybe_restart_continuous(self):
        audio_cfg = self.config_manager.config.get('audio', {})
        if not audio_cfg.get('continuous_mode', False) or self.is_paused:
            return
        self._continuous_aborted = False
        import threading
        def restart():
            import time
            time.sleep(0.6)
            if self.is_paused or self._continuous_aborted:
                self.logger.info("Continuous mode: restart aborted")
                return
            if not self.audio_recorder.get_recording_status():
                self.logger.info("Continuous mode: auto-restarting recording")
                self._begin_recording()
        threading.Thread(target=restart, daemon=True, name='continuous-restart').start()

    def _apply_recording_context(self):
        whisper_cfg = self.config_manager.get_whisper_config()
        prompt_parts = []
        base_prompt = whisper_cfg.get('initial_prompt') or ''
        if base_prompt:
            prompt_parts.append(base_prompt)
        language = whisper_cfg.get('language') or 'auto'
        task = whisper_cfg.get('task') or 'transcribe'

        try:
            rule = self.app_rules.match_for_foreground() or {}
        except Exception:
            rule = {}
        if rule.get('initial_prompt'):
            prompt_parts.append(str(rule['initial_prompt']))
            self.logger.debug(f"App rule {rule.get('match')} → initial_prompt applied")
        if rule.get('language'):
            language = rule['language']
        if rule.get('task'):
            task = rule['task']

        if whisper_cfg.get('prompt_from_selection', False):
            sel = self._grab_selection_text()
            if sel:
                prompt_parts.append(sel)
                self.logger.info(f"Selection seed applied ({len(sel)} chars)")

        combined_prompt = ' '.join(prompt_parts).strip()
        try:
            self.whisper_engine.initial_prompt = combined_prompt or None
            self.whisper_engine.language = None if language == 'auto' else language
            self.whisper_engine.task = task if task in ('transcribe', 'translate') else 'transcribe'
        except Exception as e:
            self.logger.debug(f"Engine context update failed: {e}")

    def _grab_selection_text(self) -> str:
        try:
            import time
            import pyperclip
            from .platform import keyboard as kb
            original = pyperclip.paste()
            kb.send_copy()
            time.sleep(0.08)
            selection = pyperclip.paste()
            try: pyperclip.copy(original)
            except Exception: pass
            if selection and selection != original:
                return selection[-200:].replace('\n', ' ')
        except Exception as e:
            self.logger.debug(f"Selection grab failed: {e}")
        return ''
    
    # The last recording was confidently in a language outside the user's
    # list, so it was transcribed as the closest listed one, likely badly.
    # Say so once per language per session, naming where to add it
    # in Settings.
    def _offer_outsider_language(self):
        code = getattr(self.whisper_engine, 'last_language_outsider', None)
        if not code:
            return
        offered = self.__dict__.setdefault('_outsider_languages_offered', set())
        if code in offered:
            return
        offered.add(code)
        from .dictation_languages import language_name
        name = language_name(code)
        self.logger.info(f"Speech sounded like {code}, which isn't in whisper.languages")
        self.system_tray.notify(
            f"Speaking {name}? It isn't one of your languages, so it was transcribed as "
            f"the closest one. Add \"{code}\" under Settings > General > Languages you speak.")

    # ── Silent microphone, caught while still recording ─────────────────────
    # A muted mic, a missing OS permission or a dead device delivers exact
    # zeros. Found only at the end, that costs the user everything they just
    # said, so check one second in and say so while they're still talking
    # A real mic, even a quiet one, has a noise
    # floor far above this threshold, so speech pauses never trip it.
    SILENT_MIC_CHECK_SECONDS = 1.0
    SILENT_MIC_PEAK = 1e-5

    def _silent_mic_message(self) -> str:
        where = ("Windows lets desktop apps use it (Settings > Privacy > Microphone)"
                 if platform.system() == 'Windows' else
                 "it is allowed under System Settings > Privacy & Security > Microphone")
        return f"No sound is reaching the microphone. Check it isn't muted, and that {where}."

    def _schedule_silent_mic_check(self):
        self._recording_session = getattr(self, '_recording_session', 0) + 1
        timer = threading.Timer(self.SILENT_MIC_CHECK_SECONDS, self._check_silent_mic,
                                args=(self._recording_session, self.audio_recorder))
        timer.daemon = True
        timer.start()

    # Runs on the timer thread. Only for the recording that scheduled it: a
    # recording that already ended, or a mic swapped meanwhile, says nothing.
    def _check_silent_mic(self, session, recorder):
        if session != getattr(self, '_recording_session', None) or recorder is not self.audio_recorder:
            return
        try:
            if not recorder.get_recording_status() or recorder.get_recording_peak() >= self.SILENT_MIC_PEAK:
                return
        except Exception:
            return
        self.logger.warning("Microphone delivered pure silence during the first second of recording")
        if self.level_overlay:
            self.level_overlay.show_notice("Mic is silent: check mute or permission")
        self.system_tray.notify(self._silent_mic_message())

    # Tell the user, once per app per session, that a per-app rule made this
    # delivery copy-only. Without it the app looks broken in exactly the place
    # the rule is most useful — a code editor, where the shipped default is
    # copy-only so dictation never types into source by surprise.
    def _announce_copy_only(self, rule):
        try:
            seen = self._copy_only_announced
        except AttributeError:
            seen = self._copy_only_announced = set()
        # Identify by the rule's own match pattern: it is always present,
        # unlike the foreground exe, which isn't looked up until later in
        # the pipeline.
        match = rule.get('match')
        key = str(match)
        if key in seen:
            return
        seen.add(key)
        where = ', '.join(match) if isinstance(match, (list, tuple)) else (match or 'this app')
        self.system_tray.notify(
            f"Copied, not pasted — your app rule for {where} is copy-only. "
            "Press Ctrl+V, or edit app_rules.yaml to change it.")
        self.logger.info(f"Copy-only delivery for {where} (app rule)")

    # Every transcription runs on a thread of its own. The callers are the hotkey
    # backend (on Windows, global-hotkeys calls back from its 20 ms polling
    # thread, so a transcription run inline there left every hotkey dead for
    # its whole duration), the VAD dispatcher and the capture loop, none of
    # which may block. `is_processing` is raised here, before the thread even
    # starts, so can_start_recording() is false from the moment the key is
    # released rather than from whenever the new thread gets scheduled.
    def _run_pipeline_async(self, audio_data, use_auto_enter: bool = False):
        with self._state_lock:
            self.is_processing = True
        threading.Thread(target=self._transcription_pipeline,
                         args=(audio_data, use_auto_enter),
                         daemon=True, name='transcription').start()

    # The heart of the app: everything between "user released the hotkey" and
    # "text is in their editor". Runs on a worker thread so the hotkey listener
    # never blocks. Order matters — transcribe, then branch by mode (command /
    # rephrase / dictation), then apply app rules + post-processing, then choose a
    # delivery route (suppressed, fallback window, or paste at cursor), and only
    # record stats/history once delivery actually succeeded. Every early return
    # is a legitimate "nothing to deliver" case and must still clear state via
    # the finally block.
    def _transcription_pipeline(self, audio_data, use_auto_enter: bool = False):
        try:
            with self._state_lock:
                self.is_processing = True
                command_mode = self._command_mode
                self._command_mode = False
                note_mode = self._note_mode
                self._note_mode = False
                rephrase_mode = self._rephrase_mode
                rephrase_selection = self._rephrase_selection
                self._rephrase_mode = False
                self._rephrase_selection = ''

            self.audio_feedback.play_stop_sound()

            if audio_data is None:
                self.system_tray.notify("No audio captured — was the mic muted?")
                # Flash the overlay too — tray balloons are often suppressed by Windows.
                if self.level_overlay:
                    self.level_overlay.flash_failure("No audio — mic muted?")
                return

            duration = self.audio_recorder.get_audio_duration(audio_data)
            # Kept for "Retry last dictation": in memory only, replaced by the
            # next recording, never written to disk.
            self._last_audio = audio_data

            if note_mode:
                print(f"   ✓ Recorded {duration:.1f} seconds, saving note...")
                self._update_ui_state("processing")
                if self.level_overlay:
                    self.level_overlay.show_processing()
                self._handle_note(audio_data)
                return

            # Commit-on-endpoint streaming: if finalized phrases were already typed
            # live during this recording, flush the trailing phrase, record it, and
            # skip the (now redundant) Whisper pass. Falls through to Whisper if
            # nothing was actually finalized (e.g. a very short utterance).
            if self._streaming_delivery_active and self.streaming_delivery:
                if self._finalize_streaming_delivery(use_auto_enter, duration):
                    return

            print(f"   ✓ Recorded {duration:.1f} seconds, transcribing...")

            self._update_ui_state("processing")
            if self.level_overlay:
                self.level_overlay.show_processing()

            whisper_cfg = self.config_manager.get_whisper_config()
            quick = (not command_mode and not rephrase_mode and whisper_cfg.get('quick_short_clips', True)
                     and duration <= float(whisper_cfg.get('quick_clip_seconds', 4.0)))
            transcribed_text = self.whisper_engine.transcribe_audio(audio_data, quick=quick)
            self._offer_outsider_language()

            if not transcribed_text:
                # Say which of the very different problems it was. An engine
                # error (a CUDA fault, a corrupt model) is not silence, and a
                # mic that delivered nothing at all is a mute switch or an OS
                # permission; "no speech" sends people looking elsewhere.
                failure = getattr(self.whisper_engine, 'last_error', None)
                if failure:
                    self.system_tray.notify(f"Transcription failed: {failure[:140]}. "
                                            "Your audio is kept: tray > Retry last dictation.")
                    if self.level_overlay:
                        self.level_overlay.flash_failure("Transcription failed")
                elif getattr(self.audio_recorder, 'last_recording_was_silent', False):
                    self.system_tray.notify(self._silent_mic_message())
                    if self.level_overlay:
                        self.level_overlay.flash_failure("Mic is silent")
                else:
                    self.system_tray.notify("Transcription was empty (silence or noise only).")
                    if self.level_overlay:
                        self.level_overlay.flash_failure("No speech detected")
                return

            if command_mode:
                matched = self._handle_command_transcription(transcribed_text, use_auto_enter)
                if self.level_overlay:
                    if matched:
                        self.level_overlay.flash_success()
                    else:
                        self.level_overlay.flash_failure()
                return

            if rephrase_mode and rephrase_selection:
                self._handle_rephrase(rephrase_selection, transcribed_text)
                return

            # Match the foreground app once, before post-processing, so a rule can
            # override formatting (e.g. code editors: verbatim, no auto-caps/periods)
            # in addition to the delivery behaviour handled further down.
            rule = self.app_rules.match_for_foreground()
            original_text = transcribed_text
            raw_postprocess_cfg = self.config_manager.get_postprocess_config()
            fmt_overrides = app_rules_formatting_overrides(rule, raw_postprocess_cfg)
            if fmt_overrides:
                self.logger.info(f"App rule {rule.get('match')} → formatting overrides {fmt_overrides}")
            postprocess_cfg = effective_postprocess_config(raw_postprocess_cfg, rule)
            postprocess_info = {}
            transcribed_text = postprocess(transcribed_text, postprocess_cfg, postprocess_info)
            if not quick:
                transcribed_text = self._smart_edit(transcribed_text, rule, raw_postprocess_cfg,
                                                    postprocess_cfg, postprocess_info)

            # Post-processing can legitimately empty the text — e.g. "scratch that"
            # erasing the whole utterance. Don't deliver an empty string (which
            # with auto-enter would just press Enter into the focused field).
            if not transcribed_text or not transcribed_text.strip():
                self.logger.info("Post-processing produced empty text; nothing to deliver")
                if self.level_overlay:
                    self.level_overlay.flash_failure("Nothing to type")
                return

            saved_entry = self._save_dictation(transcribed_text, audio_data, duration, original_text)
            if rule and rule.get('suppress'):
                self.logger.info(f"Delivery suppressed by app rule: {rule.get('match')}")
                # Clipboard-free setups get the recovery window instead of a
                # silent copy, so the transcript is still retrievable without
                # touching the clipboard behind the user's back (issue #12).
                if saved_entry:
                    update_delivery(saved_entry['id'], 'suppressed')
                self._refresh_pending()
                self.system_tray.notify('Saved in History. Insertion is disabled in this app.')
                if self.level_overlay:
                    self.level_overlay.flash_success()
                return

            if (not self._foreground_is_textable() or not self.clipboard_manager.auto_paste
                    or (rule and rule.get('auto_paste') is False)):
                self.logger.info('Dictation saved; waiting for a click into an editable field')
                self._refresh_pending()
                if self.level_overlay:
                    self.level_overlay.flash_success()
                return

            effective_auto_enter = use_auto_enter
            effective_auto_paste = None
            if rule:
                if rule.get('auto_send') is True:
                    effective_auto_enter = True
                elif rule.get('auto_send') is False:
                    effective_auto_enter = False
                if 'auto_paste' in rule:
                    effective_auto_paste = bool(rule['auto_paste'])

            previous_auto_paste = None
            if effective_auto_paste is not None:
                previous_auto_paste = self.clipboard_manager.auto_paste
                self.clipboard_manager.update_auto_paste(effective_auto_paste)
                # An app rule silently turning paste off reads as "the app is
                # broken" — it was reported as exactly that (issue #11). Say it
                # once per app per session: often enough to explain, rare enough
                # not to nag.
                if previous_auto_paste and not effective_auto_paste:
                    self._announce_copy_only(rule)

            try:
                if saved_entry and not update_delivery(saved_entry['id'], 'delivering'):
                    self._refresh_pending()
                    return
                success = self.clipboard_manager.deliver_transcription(
                    transcribed_text, effective_auto_enter
                )
            finally:
                if previous_auto_paste is not None:
                    self.clipboard_manager.update_auto_paste(previous_auto_paste)

            if saved_entry:
                update_delivery(saved_entry['id'], 'inserted' if success else 'pending')
            self._refresh_pending()

            if success:
                self.last_transcription = transcribed_text
                self.recent_transcriptions.appendleft(transcribed_text)
                self.system_tray.refresh_menu()
                self.audio_feedback.play_transcription_complete_sound()
                if self.level_overlay:
                    self.level_overlay.flash_success()
                fg = foreground.get_foreground_app() or {}
                record_transcription(
                    char_count=len(transcribed_text),
                    duration_seconds=duration,
                    app=fg.get('exe', ''),
                )
                self._notify_hub('transcript')
                audit_enabled = (self.config_manager.config.get('audit') or {}).get('enabled', False)
                audit_record('delivered', transcribed_text, fg.get('exe', ''), audit_enabled)
                self._maybe_restart_continuous()
            elif self.level_overlay:
                self.level_overlay.flash_failure()
            
        except Exception as e:
            # With the traceback: a one-line message here is what every bug
            # report quotes, and without the stack it says nothing about where.
            self.logger.error(f"Error in processing workflow: {e}", exc_info=True)
            print(f"❌ Error processing recording: {e}")
            if self.level_overlay:
                self.level_overlay.flash_failure()

        finally:
            # Safety net: if a streaming-delivery worker is still around (e.g. the
            # audio came back None and we returned before _finalize_streaming_delivery,
            # or an exception fired), stop+join it here so no path can leak the
            # worker thread or leave _streaming_delivery_active stuck True.
            if self.streaming_delivery is not None:
                try:
                    self.streaming_delivery.stop()
                except Exception:
                    pass
                self.streaming_delivery = None
            self._streaming_delivery_active = False

            with self._state_lock:
                self.is_processing = False
                pending_device = self._pending_device_change

            if pending_device:
                device_id, device_name = pending_device
                self.logger.info(f"Executing pending device change to: {device_name}")
                self._execute_audio_device_change(device_id, device_name)
                self._pending_device_change = None

            self._drain_pending_changes()
            if not (pending_device or self.is_model_loading):
                self._update_ui_state("idle")

    # history.retention_days: None keeps everything, 0 stores nothing, N keeps
    # N days. Read per delivery, so a change applies without a restart.
    def _history_retention_days(self):
        history = self.config_manager.config.get('history')
        return history.get('retention_days') if isinstance(history, dict) else None

    def _handle_command_transcription(self, text: str, use_auto_enter: bool = False) -> bool:
        log_config = self.config_manager.get_logging_config()
        if log_config.get('log_transcriptions', False):
            self.logger.info(f"Command mode transcription: '{text}'")
        else:
            self.logger.info("Command mode transcription received")

        if not self.voice_command_manager.enabled:
            self.logger.warning("Voice commands disabled")
            return False

        matched = self.voice_command_manager.match_command(text)
        if matched:
            self.voice_command_manager.execute_command(matched, use_auto_enter)
            return True
        print(f"   ✗ No matching command for: \"{text[:60]}\"")
        return False

    def get_application_state(self) -> dict:
        status = {
            "recording": self.audio_recorder.get_recording_status(),
            "processing": self.is_processing,
            "model_loading": self.is_model_loading,
        }
        
        return status
    
    def manual_transcribe_test(self, duration_seconds: int = 5):
        try:
            print(f"🎤 Recording for {duration_seconds} seconds...")
            print("Speak now!")
            
            self.audio_recorder.start_recording()
            
            time.sleep(duration_seconds)
            
            audio_data = self.audio_recorder.stop_recording()
            self._transcription_pipeline(audio_data)
            
        except Exception as e:
            self.logger.error(f"Manual test failed: {e}")
            print(f"❌ Test failed: {e}")

    # Single place that fans a state change out to every status surface, so
    # the tray icon and the terminal tab title can never drift apart. Both are
    # OptionalComponent-wrapped: a disabled tray or a non-TTY terminal is a
    # silent no-op rather than a branch at each call site.
    def _update_ui_state(self, state: str):
        self.system_tray.update_state(state)
        self.terminal_title.update_state(state)

    
    def shutdown(self):        
        if self._target_watcher is not None:
            self._target_watcher.stop()
            self._target_watcher = None
        self.game_mode.stop()
        self.terminal_title.stop()
        print("Syllune is shutting down... goodbye!")

        if self.audio_recorder.get_recording_status():
            self.audio_recorder.stop_recording()
            self.media_pause.resume()
        self.audio_recorder.shutdown()
        self.meetings.shutdown()

        if self.level_overlay:
            self.level_overlay.shutdown()

        self.system_tray.stop()
    
    def set_model_loading(self, loading: bool):
        with self._state_lock:
            old_state = self.is_model_loading
            self.is_model_loading = loading
            
            if old_state != loading:
                if loading:
                    self._update_ui_state("processing")
                else:
                    self._update_ui_state("idle")
    
    def is_transcription_recording(self) -> bool:
        return self.audio_recorder.get_recording_status() and not self._command_mode

    def can_start_recording(self) -> bool:
        with self._state_lock:
            return not (self.is_processing or self.is_model_loading or self.audio_recorder.get_recording_status())
    
    def get_current_state(self) -> str:
        with self._state_lock:
            if self.is_model_loading:
                return "model_loading"
            elif self.is_processing:
                return "processing"
            elif self.audio_recorder.get_recording_status():
                return "recording"
            else:
                return "idle"
    
    def request_model_change(self, new_model_key: str) -> bool:
        with getattr(self, '_quality_lock', nullcontext()):
            current_state = self.get_current_state()
            if current_state != 'idle':
                self._pending_model_change = new_model_key
                return True
            if (new_model_key == self.whisper_engine.model_key
                    and getattr(self.whisper_engine, 'model', None) is not None):
                return True
            self._execute_model_change(new_model_key)
            return True
    
    def update_transcription_mode(self, value):
        self.config_manager.update_user_setting('clipboard', 'auto_paste', value)
        self.clipboard_manager.update_auto_paste(value)

    def _execute_model_change(self, new_model_key: str):
        def progress_callback(message: str):
            lower = message.lower()
            if lower.startswith('failed') or lower.startswith('model change failed'):
                self._finish_model_change(new_model_key, False, message)
            elif 'ready' in lower or 'already loaded' in lower:
                self._finish_model_change(new_model_key, True, message)
            else:
                print(f"🔄 {message}")
        
        try:
            self.set_model_loading(True)
            print(f"🔄 Switching to [{new_model_key}] model...")
            
            accepted = self.whisper_engine.change_model(new_model_key, progress_callback)
            if accepted is False and self.is_model_loading:
                self._finish_model_change(new_model_key, False, 'Speech model change was not accepted. Retry in Settings.')
            
        except Exception as e:
            self.logger.error(f"Failed to initiate model change: {e}")
            print(f"❌ Failed to change model: {e}")
            self._finish_model_change(new_model_key, False, str(e))

    # Clear busy state even if a config write or a UI observer fails.
    def _finish_model_change(self, key, succeeded, message):
        with getattr(self, '_quality_lock', nullcontext()):
            try:
                if not succeeded:
                    self.logger.error(f'Model change failed: {message}')
                    self.system_tray.notify(f'Could not change speech model: {message[:160]}. Previous quality retained; retry in Settings.')
                    if self.level_overlay:
                        self.level_overlay.flash_failure('Model unavailable — retry in Settings')
            finally:
                self.set_model_loading(False)
            self._drain_pending_changes()

    def _handle_rephrase(self, selection: str, instruction: str):
        import time
        import pyperclip
        from .platform import keyboard as kb

        print(f"\n   🤖 Rephrasing ({len(selection)} chars) with instruction: '{instruction[:60]}'")

        # The same local model as the AI edit, so rephrase works out of the box.
        messages = [
            {'role': 'system', 'content': (
                "Rewrite the user's selected text as their spoken instruction asks. Keep its "
                "language unless the instruction asks for another. Output ONLY the rewritten "
                "text, with no preamble, quotes or commentary.")},
            {'role': 'user', 'content': f"Instruction: {instruction}\n\nText:\n{selection}"},
        ]
        polished = self.smart_editor.chat(messages, num_predict=len(selection) // 2 + 200,
                                          timeout=30)
        if not polished:
            print("   ✗ Rephrase failed — Ollama unreachable or returned nothing")
            self.system_tray.notify("Rephrase failed — is Ollama running?")
            if self.level_overlay:
                self.level_overlay.flash_failure()
            return

        try:
            pyperclip.copy(polished)
            time.sleep(0.05)
            kb.send_paste()
            time.sleep(0.2)
        finally:
            try:
                if self._rephrase_original_clipboard:
                    pyperclip.copy(self._rephrase_original_clipboard)
            except Exception:
                pass

        self.last_transcription = polished
        self.recent_transcriptions.appendleft(polished)
        self.system_tray.refresh_menu()
        self.system_tray.notify("Rephrased and pasted")
        if self.level_overlay:
            self.level_overlay.flash_success()
        self.logger.info(f"Rephrase complete: {len(selection)} -> {len(polished)} chars")
        print("   ✓ Rephrased and pasted")

    NON_TEXT_EXES = {'progman.exe', 'workerw.exe', 'dwm.exe', 'searchhost.exe',
                     'shellexperiencehost.exe', 'startmenuexperiencehost.exe',
                     'lockapp.exe', 'sihost.exe'}

    def _foreground_is_textable(self) -> bool:
        target = self._current_text_target()
        if not target:
            return False
        return not _is_own_window(target)


    def set_overlay_position(self, name: str) -> bool:
        if not self.level_overlay:
            return False
        self.level_overlay.set_position(name)
        self.config_manager.update_user_setting('overlay', 'position', name)
        self.logger.info(f"Overlay position set to {name}")
        return True

    def get_overlay_position(self) -> str:
        return self.config_manager.config.get('overlay', {}).get('position', 'bottom-center')

    def get_current_language(self) -> str:
        return self.config_manager.config.get('whisper', {}).get('language', 'auto')

    def set_language(self, code: str) -> bool:
        current = self.get_current_language()
        if current == code:
            return False
        self.config_manager.update_user_setting('whisper', 'language', code)
        try:
            self.whisper_engine.language = None if code == 'auto' else code
        except Exception:
            pass
        self.logger.info(f"Language switched: {current} -> {code}")
        print(f"   ✓ Language set to: {code}")
        self.system_tray.notify(f"Language: {code}")
        self.system_tray.refresh_menu()
        return True

    def apply_transform(self, name: str) -> bool:
        success = self.transforms_manager.apply(name)
        if self.level_overlay:
            if success:
                self.level_overlay.flash_success()
            else:
                self.level_overlay.flash_failure()
        return success

    def list_transforms(self) -> list:
        return self.transforms_manager.list_transforms()

    def reload_transforms(self) -> bool:
        old = [(t.get('name'), t.get('hotkey')) for t in self.transforms_manager.list_transforms()]
        self.transforms_manager.reload_if_changed()
        new = [(t.get('name'), t.get('hotkey')) for t in self.transforms_manager.list_transforms()]
        changed = old != new
        if changed:
            self.system_tray.notify(f"Transforms reloaded ({len(new)} active)")
            self.system_tray.refresh_menu()
            if self._hotkey_listener_ref:
                self._hotkey_listener_ref.refresh_transforms()
        return changed

    def set_hotkey_listener(self, listener):
        self._hotkey_listener_ref = listener

    def is_hotkeys_paused(self) -> bool:
        return bool(self.is_paused)

    # The tray's "Pause hotkeys" item. Routed through the listener so the tray
    # and the pause hotkey can never disagree about the state.
    def toggle_hotkeys_paused(self):
        if self._hotkey_listener_ref:
            self._hotkey_listener_ref.toggle_pause()

    def list_profiles(self) -> list:
        return self.profile_manager.list_profiles()

    def get_active_profile(self) -> Optional[str]:
        return self.profile_manager.get_active()

    def activate_profile(self, name: str) -> bool:
        old_whisper = dict(self.config_manager.get_whisper_config())

        if not self.profile_manager.apply(name):
            return False

        new_whisper = self.config_manager.get_whisper_config()

        if new_whisper.get('language') != old_whisper.get('language'):
            lang = new_whisper.get('language', 'auto')
            try:
                self.whisper_engine.language = None if lang == 'auto' else lang
            except Exception:
                pass

        if new_whisper.get('task') != old_whisper.get('task'):
            task = new_whisper.get('task') or 'transcribe'
            try:
                self.whisper_engine.task = task if task in ('transcribe', 'translate') else 'transcribe'
            except Exception:
                pass

        new_prompt = new_whisper.get('initial_prompt') or ''
        if new_prompt != (old_whisper.get('initial_prompt') or ''):
            try:
                self.whisper_engine.initial_prompt = new_prompt or None
            except Exception:
                pass

        if new_whisper.get('model') and new_whisper.get('model') != old_whisper.get('model'):
            try:
                self._execute_model_change(new_whisper['model'])
            except Exception as e:
                self.logger.error(f"Profile model change failed: {e}")

        self.logger.info(f"Activated profile: {name}")
        print(f"   ✓ Profile activated: {name}")
        self.system_tray.notify(f"Profile: {name}")
        self.system_tray.refresh_menu()
        return True

    def get_recent_transcriptions(self) -> list:
        return list(self.recent_transcriptions)

    def has_last_recording(self) -> bool:
        return getattr(self, '_last_audio', None) is not None

    # Transcribes the last recording again, with whatever model and settings
    # are current: after a failure, or when a dictation came out wrong and a
    # bigger model might do better. Started from the tray,
    # whose click has taken focus from the user's text field, so the result
    # opens in the dictation-captured window rather than being pasted blind.
    def retry_last_dictation(self) -> bool:
        audio = getattr(self, '_last_audio', None)
        if audio is None:
            self.system_tray.notify("Nothing to retry yet: dictate something first.")
            return False
        if self.get_current_state() != "idle":
            self.system_tray.notify("Busy: try again when the current dictation is done.")
            return False
        with self._state_lock:
            self.is_processing = True
        threading.Thread(target=self._retry_worker, args=(audio,),
                         daemon=True, name='retry-dictation').start()
        return True

    def _retry_worker(self, audio):
        try:
            self._update_ui_state("processing")
            if self.level_overlay:
                self.level_overlay.show_processing()
            text = self.whisper_engine.transcribe_audio(audio)
            if text:
                text = postprocess(text, effective_postprocess_config(
                    self.config_manager.get_postprocess_config(), None))
            if not text or not text.strip():
                failure = getattr(self.whisper_engine, 'last_error', None)
                self.system_tray.notify(f"Retry failed: {failure[:140]}" if failure
                                        else "Retry found no speech in the last recording.")
                if self.level_overlay:
                    self.level_overlay.flash_failure("Retry found nothing")
                return
            may_copy = self.clipboard_manager.silent_copy_allowed
            if may_copy:
                self.clipboard_manager.copy_text(text)
            self.fallback_window.show(
                text,
                reason=("Re-transcribed with the current model. Already on your clipboard."
                        if may_copy else
                        "Re-transcribed with the current model. Use Copy to take it."),
                allow_clipboard=may_copy,
            )
            self.last_transcription = text
            self.recent_transcriptions.appendleft(text)
            self.system_tray.refresh_menu()
            if self.level_overlay:
                self.level_overlay.flash_success()
        except Exception as e:
            self.logger.error(f"Retry failed: {e}", exc_info=True)
            self.system_tray.notify(f"Retry failed: {e}")
        finally:
            with self._state_lock:
                self.is_processing = False
            self._drain_pending_changes()
            if not self.is_model_loading:
                self._update_ui_state("idle")

    # Types the last dictation again at the cursor (paste-last hotkey), e.g.
    # after it went to the wrong window. Never auto-sends, and the foreground
    # app's rule still applies: nothing goes into a suppressed app (password
    # managers), and copy-only apps (terminals, where a pasted newline runs a
    # command) get the clipboard instead.
    def paste_last_transcription(self) -> bool:
        text = self.last_transcription
        if not text:
            self.system_tray.notify("Nothing to paste yet — dictate something first.")
            return False
        if self.get_current_state() != "idle":
            self.logger.info("Paste-last ignored: busy recording or transcribing")
            return False
        self._wait_for_modifiers_released()
        rule = None
        try:
            rule = self.app_rules.match_for_foreground()
        except Exception as e:
            self.logger.debug(f"Paste-last rule lookup failed: {e}")
        try:
            if rule and rule.get('suppress'):
                self.logger.info(f"Paste-last suppressed by app rule: {rule.get('match')}")
                self.system_tray.notify("Not pasted: this app is excluded in app_rules.yaml.")
                return False
            if rule and rule.get('auto_paste') is False:
                self.clipboard_manager.copy_text(text)
                self.system_tray.notify("Last dictation copied — this app is copy-only.")
                return True
            self.clipboard_manager.deliver_transcription(text, use_auto_enter=False)
        except Exception as e:
            self.logger.error(f"Paste-last failed: {e}")
            self.system_tray.notify("Couldn't paste the last dictation.")
            return False
        return True

    # A hotkey's release event can arrive while its other modifiers are still
    # down, and those would merge into the paste. Wait briefly for the user to
    # let go; after the timeout, deliver anyway rather than drop the request.
    def _wait_for_modifiers_released(self, timeout: float = 1.5):
        import time
        try:
            from .platform import keyboard as kb
            modifiers_held = kb.modifiers_held
        except (ImportError, AttributeError):
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if not modifiers_held():
                    return
            except Exception:
                return
            time.sleep(0.02)

    def recopy_recent_transcription(self, index: int):
        if 0 <= index < len(self.recent_transcriptions):
            text = self.recent_transcriptions[index]
            self.clipboard_manager.copy_text(text)
            print(f"   ✓ Re-copied: {text[:60]}{'...' if len(text) > 60 else ''}")

    def get_available_audio_devices(self, host_filter: Optional[str] = None):
        host_name = host_filter if host_filter is not None else self._current_audio_host
        return AudioRecorder.get_available_audio_devices(host_name)

    def get_current_audio_device_id(self):
        return self.audio_recorder.get_device_id()

    def get_available_audio_hosts(self):
        try:
            hostapis = sd.query_hostapis()
            devices = sd.query_devices()
        except Exception as e:
            self.logger.error(f"Failed to query audio hosts: {e}")
            return []

        hosts_with_input = {}
        for index, host in enumerate(hostapis):
            hosts_with_input[index] = {
                'name': host['name'],
                'index': index,
                'has_input': False
            }

        for device in devices:
            if device.get('max_input_channels', 0) > 0:
                host_index = device['hostapi']
                if host_index in hosts_with_input:
                    hosts_with_input[host_index]['has_input'] = True

        return [
            {'name': host['name'], 'index': host['index']}
            for host in hosts_with_input.values()
            if host['has_input']
        ]

    def get_current_audio_host(self) -> Optional[str]:
        return self._current_audio_host

    def set_audio_host(self, host_name: str) -> bool:
        if not host_name:
            return False

        available_hosts = self.get_available_audio_hosts()
        normalized_lookup = {host['name'].lower(): host for host in available_hosts}
        host_entry = normalized_lookup.get(host_name.lower())

        if not host_entry:
            self.logger.warning(f"Requested audio host '{host_name}' is not available")
            return False

        canonical_name = host_entry['name']
        if canonical_name == self._current_audio_host:
            return True

        self._current_audio_host = canonical_name
        self.config_manager.update_audio_host(canonical_name)
        self.logger.info(f"Audio host changed to {canonical_name}")

        self._ensure_audio_device_for_host(canonical_name)
        self.system_tray.refresh_menu()
        return True

    def request_audio_device_change(self, device_id: int, device_name: str):
        current_state = self.get_current_state()

        if device_id == self.audio_recorder.device:
            return True

        if current_state == "recording":
            print("🎤 Cancelling recording to switch audio device...")
            self.cancel_active_recording()
            self._execute_audio_device_change(device_id, device_name)
            return True

        if current_state == "processing":
            print("⏳ Queueing audio device change until transcription completes...")
            self._pending_device_change = (device_id, device_name)
            return True

        if current_state == "idle":
            self._execute_audio_device_change(device_id, device_name)
            return True

        self.logger.warning(f"Unexpected state for device change: {current_state}")
        return False

    def _execute_audio_device_change(self, device_id: int, device_name: str):
        try:
            print(f"🎤 Switching to: {device_name}")

            channels = self.audio_recorder.channels
            dtype = self.audio_recorder.dtype
            max_duration = self.audio_recorder.max_duration
            on_max_duration = self.audio_recorder.on_max_duration_reached
            vad_manager = self.audio_recorder.vad_manager
            streaming_manager = self.audio_recorder.streaming_manager
            on_streaming_result = self.audio_recorder.on_streaming_result

            audio_cfg = self.config_manager.config.get('audio') or {}
            noise_cfg = audio_cfg.get('noise_suppression') or {}
            new_recorder = AudioRecorder(
                on_vad_event=self.handle_vad_event,
                channels=channels,
                dtype=dtype,
                max_duration=max_duration,
                on_max_duration_reached=on_max_duration,
                vad_manager=vad_manager,
                streaming_manager=streaming_manager,
                on_streaming_result=on_streaming_result,
                device=device_id if device_id != -1 else None,
                noise_suppression_config=noise_cfg,
                whisper_mode_config=audio_cfg.get('whisper_mode') or {},
            )

            # Close the old stream, otherwise it keeps capturing in the background.
            with self._recorder_lock:
                old_recorder = self.audio_recorder
                self.audio_recorder = new_recorder
                old_recorder.shutdown()

            print(f"✅ Successfully switched audio device to: {device_name}")

        except Exception as e:
            self.logger.error(f"Failed to change audio device: {e}")
            print(f"❌ Failed to switch audio device: {e}")

    # ── Following the system default microphone ──────────────────────────────
    # With `input_device: default` the mic is resolved once at startup, and
    # PortAudio never refreshes its device list by itself. After a dock switch
    # or a wake-up at a desk with other hardware, recordings would keep using
    # the old (or a dead) mic. This thread polls the OS default input and
    # rebinds when it changes or when the capture stream has died.
    DEFAULT_INPUT_POLL_SECONDS = 2.0

    def _start_default_input_watcher(self):
        # Started whenever the platform can report the default mic, even with
        # none connected yet: a laptop opened undocked should still pick up the
        # dock's microphone when it arrives.
        if not audio_endpoints.is_supported():
            return
        threading.Thread(target=self._watch_default_input, daemon=True,
                         name="default-input-watcher").start()

    def _watch_default_input(self):
        known_input_id = audio_endpoints.get_default_input_id()
        while True:
            time.sleep(self.DEFAULT_INPUT_POLL_SECONDS)
            try:
                known_input_id = self._follow_default_input(known_input_id)
            except Exception as e:
                self.logger.error(f"Default input watcher failed: {e}")

    # One watcher tick: rebind if the default mic changed or capture died.
    # Returns the default input ID to compare against on the next tick.
    def _follow_default_input(self, known_input_id):
        # A mic picked explicitly in the tray stays put.
        if self.config_manager.get_setting('audio', 'input_device') != 'default':
            return known_input_id
        current_input_id = audio_endpoints.get_default_input_id()
        if current_input_id is None:
            # No microphone at all right now (unplugged, or none yet). There is
            # nothing to rebind to, so wait rather than log a failure every tick.
            return known_input_id
        default_changed = current_input_id != known_input_id
        if not default_changed and self.audio_recorder.is_capturing():
            return known_input_id
        # Never swap the mic mid-recording or mid-transcription; retry next tick.
        if self.get_current_state() != 'idle':
            return known_input_id
        self._rebind_to_default_input()
        return current_input_id or known_input_id

    def _rebind_to_default_input(self):
        # PortAudio enumerates devices only at init: close our stream, re-init
        # it, then pick the host's now-current default input. Under the recorder
        # lock, so no recording can start against a stream that is being torn
        # down, and no stream is open while PortAudio is re-initialised.
        with self._recorder_lock:
            self.audio_recorder.shutdown()
            sd._terminate()
            sd._initialize()

            device_id = self._get_default_device_for_host(self._current_audio_host)
            if device_id is None:
                self.logger.warning("Default input changed, but no input device is available")
                return

            device_name = self._get_device_name(device_id)
            self.logger.info(f"Default input changed, following it to: {device_name}")
            self._execute_audio_device_change(device_id, device_name)
        # Tray calls stay outside the lock: the menu rebuild reads state.
        self.system_tray.notify(f"Microphone: {device_name}")
        self.system_tray.refresh_menu()

    def _initialize_audio_host(self):
        try:
            configured_host = self.config_manager.get_setting('audio', 'host')
        except KeyError:
            configured_host = None

        available_hosts = self.get_available_audio_hosts()
        resolved_host = self._resolve_audio_host(configured_host, available_hosts)

        self._current_audio_host = resolved_host

        if resolved_host != configured_host:
            self.config_manager.update_audio_host(resolved_host)

    def _resolve_audio_host(self, configured_host: Optional[str], available_hosts):
        if not available_hosts:
            return None

        if configured_host:
            match = self._match_host(configured_host, available_hosts)
            if match:
                return match

        preferred_host = self._preferred_platform_host()
        if preferred_host:
            preferred_match = self._match_host(preferred_host, available_hosts)
            if preferred_match:
                return preferred_match

        return available_hosts[0]['name']

    def _match_host(self, requested: str, available_hosts) -> Optional[str]:
        requested_lower = requested.lower().strip()
        for host in available_hosts:
            if host['name'].lower() == requested_lower:
                return host['name']
        for host in available_hosts:
            if requested_lower in host['name'].lower():
                return host['name']
        return None

    def _preferred_platform_host(self) -> Optional[str]:
        system_name = platform.system().lower()
        if system_name == 'windows':
            return 'WASAPI'
        return None

    def _ensure_audio_device_for_host(self, host_name: Optional[str]):
        if not host_name or not self.audio_recorder:
            return

        try:
            current_device_id = self.audio_recorder.get_device_id()
        except Exception as e:
            self.logger.error(f"Unable to read current audio device: {e}")
            return

        if self._device_matches_host(current_device_id, host_name):
            return

        fallback_device_id = self._get_default_device_for_host(host_name)
        if fallback_device_id is None:
            self.logger.warning(f"No input devices available for host {host_name}")
            return

        device_name = self._get_device_name(fallback_device_id)
        success = self.request_audio_device_change(fallback_device_id, device_name)

        if not success:
            self.logger.warning(f"Failed to switch to fallback device {fallback_device_id} for host {host_name}")

    def _device_matches_host(self, device_id: int, host_name: str) -> bool:
        try:
            device_info = sd.query_devices(device_id)
            host_info = sd.query_hostapis(device_info['hostapi'])
            return host_info['name'].lower() == host_name.lower()
        except Exception:
            return False

    def _get_default_device_for_host(self, host_name: str) -> Optional[int]:
        try:
            target_index = None
            target_host = None
            hostapis = sd.query_hostapis()
            for idx, host in enumerate(hostapis):
                if host['name'].lower() == host_name.lower():
                    target_index = idx
                    target_host = host
                    break
            else:
                return None

            default_input = target_host.get('default_input_device', -1)
            if default_input is not None and default_input >= 0:
                device_info = sd.query_devices(default_input)
                if device_info.get('max_input_channels', 0) > 0:
                    return default_input

            all_devices = sd.query_devices()
            for idx, device in enumerate(all_devices):
                if device['hostapi'] == target_index and device.get('max_input_channels', 0) > 0:
                    return idx
        except Exception as e:
            self.logger.error(f"Failed to determine default device for host {host_name}: {e}")

        return None

    def _get_device_name(self, device_id: int) -> str:
        try:
            device_info = sd.query_devices(device_id)
            return device_info.get('name', f"Device {device_id}")
        except Exception:
            return f"Device {device_id}"
