# hub_api.py
# Backend of the hub window (overlay/hub). The hub sends
# {type: 'rpc', id, method, params} over the overlay socket; HubApi runs each
# call off the socket thread and answers {type: 'rpc', id, ok, result | error}.
# Only the methods in `_methods` can be called, and every settings write goes
# through ConfigManager so the running app and user_settings.yaml agree.

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from . import autostart, stats
from .app_setup import AppSetup
from .corrections import corrections_from_edit
from .dictionary import LEARN_HISTORY, list_hotwords, words_to_learn
from .styles import available_styles
from .transcript_log import (delete_transcript, load_transcripts, update_transcript,
                             find_transcript, queue_pending, record_transcript)
from .recording_archive import recording_path
from .utils import get_user_app_data_path, open_file

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

# Friendly names for the apps people dictate into most. Anything else shows
# as its exe name without ".exe".
APP_NAMES = {
    'code.exe': 'VS Code', 'cursor.exe': 'Cursor', 'windsurf.exe': 'Windsurf',
    'windowsterminal.exe': 'Terminal', 'wt.exe': 'Terminal', 'powershell.exe': 'PowerShell',
    'cmd.exe': 'Command Prompt', 'chrome.exe': 'Chrome', 'msedge.exe': 'Edge',
    'firefox.exe': 'Firefox', 'brave.exe': 'Brave', 'opera.exe': 'Opera',
    'claude.exe': 'Claude', 'chatgpt.exe': 'ChatGPT', 'discord.exe': 'Discord',
    'slack.exe': 'Slack', 'telegram.exe': 'Telegram', 'whatsapp.exe': 'WhatsApp',
    'winword.exe': 'Word', 'outlook.exe': 'Outlook', 'olk.exe': 'Outlook',
    'notion.exe': 'Notion', 'obsidian.exe': 'Obsidian', 'explorer.exe': 'Explorer',
    'notepad.exe': 'Notepad', 'spotify.exe': 'Spotify',
}

# The shipped app rules the Styles page shows, in the order people think of
# them. Password managers and games aren't styles: those rules turn typing off.
STYLE_RULES = (
    ('chat-apps', 'Chat'),
    ('email-clients', 'Email'),
    ('documents', 'Documents'),
    ('ai-prompts', 'AI assistants'),
    ('code-editors', 'Code editors'),
    ('terminals', 'Terminals'),
)

QUALITIES = ('fast', 'balanced', 'best')
MAX_ABOUT_ME = 1500
MAX_INSTRUCTIONS = 500
MAX_TERM = 80
MAX_RULE_APPS = 60
MAX_IGNORED_WORDS = 300   # removed words remembered so they are not learned again
LEARN_EVERY = 5           # dictations between looks at history for new words


def app_label(exe: str) -> str:
    exe = str(exe or '').strip()
    if not exe:
        return ''
    known = APP_NAMES.get(exe.lower())
    if known:
        return known
    name = exe[:-4] if exe.lower().endswith('.exe') else exe
    return name[:1].upper() + name[1:]


def _require_bool(value) -> bool:
    if not isinstance(value, bool):
        raise ValueError("expected true or false")
    return value


def _require_term(value) -> str:
    term = str(value or '').strip()
    if not term or len(term) > MAX_TERM or '\n' in term:
        raise ValueError(f"a word or phrase of up to {MAX_TERM} characters")
    return term


# ruamel's CommentedMap/Seq serialise fine, but plain types keep the reply
# predictable for the page.
def _plain_replacement(item) -> dict:
    return {key: item[key] for key in item}


class HubApi:
    def __init__(self, state_manager, config_manager):
        self.state = state_manager
        self.config = config_manager
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='hub-rpc')
        # Writes are serialised: two quick toggles must not interleave their
        # read-modify-write of user_settings.yaml.
        self._write_lock = threading.Lock()
        self._setup = AppSetup(config_manager, state_manager, write_lock=self._write_lock)
        # Starts one short of the interval, so the first dictation of a session
        # already catches up on everything said before it.
        self._dictations_since_learning = LEARN_EVERY - 1
        self._methods = {
            # name: (function, writes)
            'overview': (self.overview, False),
            'history': (self.history, False),
            'history_update': (self.history_update, True),
            'history_delete': (self.history_delete, True),
            'history_structure': (self.history_structure, False),
            'history_queue': (self.history_queue, True),
            'audio_source': (self.audio_source, False),
            'dictionary': (self.dictionary, False),
            'dictionary_add': (self.dictionary_add, True),
            'dictionary_remove': (self.dictionary_remove, True),
            'dictionary_auto': (self.dictionary_auto, True),
            'replacement_remove': (self.replacement_remove, True),
            'styles': (self.styles, False),
            'style_set': (self.style_set, True),
            'rule_apps_set': (self.rule_apps_set, True),
            'settings': (self.settings, False),
            'setting_set': (self.setting_set, True),
            'setup_status': (self.setup_status, False),
            'setup_save': (self.setup_save, True),
            'setup_download': (self.setup_download, True),
            'setup_complete': (self.setup_complete, True),
            'setup_mic_test': (self.setup_mic_test, True),
            'open_folder': (self.open_folder, False),
            # Meetings keep their own files and locks, apart from settings.
            'meetings': (self.meetings, False),
            'meeting': (self.meeting, False),
            'meeting_start': (self.meeting_start, True),
            'meeting_stop': (self.meeting_stop, True),
            'meeting_rename_speaker': (self.meeting_rename_speaker, True),
            'meeting_summarize': (self.meeting_summarize, True),
            'meeting_retry': (self.meeting_retry, True),
            'meeting_process_now': (self.meeting_process_now, True),
            'meeting_delete': (self.meeting_delete, True),
            'meeting_open': (self.meeting_open, False),
            'meeting_transcript': (self.meeting_transcript, False),
            'meeting_open_transcript': (self.meeting_open_transcript, False),
            'meeting_audio': (self.meeting_audio, False),
        }
        self._setters = {
            'quality': self._set_quality,
            'short_messages': self._set_short_messages,
            'ai_model': self._set_ai_model,
            'about_me': self._set_about_me,
            'mic': self._set_mic,
            'sounds': self._set_sounds,
            'idle_pill': self._set_idle_pill,
            'autostart': self._set_autostart,
            'retention_days': self._set_retention,
            'keep_audio': lambda value: self.config.update_user_setting('history', 'keep_audio', _require_bool(value)),
            'update_checks': lambda value: self.config.update_user_setting('update_check', 'enabled', _require_bool(value)),
        }

    # --- Dispatch ------------------------------------------------------------

    # Called on the overlay's socket reader thread; `reply` sends a message back.
    def handle(self, message: dict, reply):
        self._pool.submit(self._run, message, reply)

    def _run(self, message: dict, reply):
        call_id = message.get('id')
        name = str(message.get('method') or '')
        params = message.get('params') or {}
        try:
            entry = self._methods.get(name)
            if entry is None or not isinstance(params, dict):
                raise ValueError(f"unknown method {name!r}")
            function, writes = entry
            if writes:
                with self._write_lock:
                    result = function(**params)
            else:
                result = function(**params)
            reply({'type': 'rpc', 'id': call_id, 'ok': True, 'result': result})
        except Exception as e:
            logger.warning(f"Hub call {name} failed: {e}")
            reply({'type': 'rpc', 'id': call_id, 'ok': False, 'error': str(e)})

    # --- Home ----------------------------------------------------------------

    def overview(self) -> dict:
        typing_wpm = self._typing_wpm()
        summary = stats.summary(typing_wpm)
        for app in summary['top_apps']:
            app['label'] = app_label(app['app']) or 'Other'
        recent = [self._transcript(entry) for entry in load_transcripts()[:5]]
        return {**summary, 'typing_wpm': typing_wpm, 'recent': recent, 'status': self._status()}

    def _status(self) -> dict:
        ai = self.config.get_ai_config()
        ai_enabled = bool(ai.get('enabled', True))
        return {
            'model': str(getattr(self.state.whisper_engine, 'model_key', '') or ''),
            'quality': self.state.current_quality(),
            'ai_enabled': ai_enabled,
            'ai_model': str(ai.get('model') or 'gemma3:4b'),
            'ai_reachable': ai_enabled and self.state.smart_editor.reachable(),
            'game_mode': bool(self.state.game_mode.active),
        }

    # --- History -------------------------------------------------------------

    def history(self, query: str = '', limit: int = 100, offset: int = 0) -> dict:
        entries = load_transcripts()
        query = str(query or '').strip().lower()
        if query:
            entries = [entry for entry in entries
                       if query in str(entry.get('text', '')).lower()
                       or query in app_label(entry.get('app')).lower()]
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        return {
            'items': [self._transcript(entry) for entry in entries[offset:offset + limit]],
            'total': len(entries),
            'saving': self._retention() != 0,
        }

    # Saves an edit of a past dictation and learns the mishearings it fixed,
    # so the same slip is corrected on every later dictation.
    def history_update(self, timestamp: str, original: str, edited: str) -> dict:
        if not update_transcript(str(timestamp), str(original), str(edited)):
            raise ValueError("That dictation is no longer in your history")
        learned = []
        for heard, fixed in corrections_from_edit(str(original), str(edited)):
            if self._add_replacement(heard, fixed):
                learned.append({'from': heard, 'to': fixed})
        if learned:
            logger.info(f"Learned {len(learned)} correction(s) from a history edit")
        return {'learned': learned}

    def history_delete(self, timestamp: str, text: str) -> dict:
        if not delete_transcript(str(timestamp), str(text)):
            raise ValueError("That dictation is no longer in your history")
        refresh = getattr(self.state, '_refresh_pending', None)
        if refresh:
            refresh()
        return {}

    # Structure is a preview, never an edit of the original journal entry.
    def history_structure(self, entry_id: str) -> dict:
        entry = find_transcript(str(entry_id))
        if not entry:
            raise ValueError('That dictation no longer exists')
        if len(entry['text']) > 12000:
            raise ValueError('This dictation is too long for one Structure preview')
        text = self.state.smart_editor.edit(entry['text'], style='structure', timeout=15)
        if not text:
            raise ValueError('The local model could not structure this faithfully. Your original is unchanged.')
        return {'text': text, 'original': entry['text']}

    def history_queue(self, entry_id: str, text=None) -> dict:
        original = find_transcript(str(entry_id))
        if not original:
            raise ValueError('That dictation no longer exists')
        if text is None:
            entry = queue_pending(original['id'])
        else:
            text = str(text).strip()
            if not text or len(text) > 50000:
                raise ValueError('Choose a nonempty preview under 50,000 characters')
            # Keep the original row and recording intact. The preview becomes
            # its own queued dictation, with the source words still attached.
            entry = record_transcript(text, app=original.get('app', ''), delivery='pending',
                                      original_text=original.get('original_text') or original['text'])
        if not entry:
            raise ValueError('Could not save the insertion queue')
        self.state._refresh_pending()
        return {'entry': self._transcript(entry)}

    # Only opaque archive/meeting IDs can select playable audio, never a path.
    def audio_source(self, kind: str, entry_id: str, track: str = 'playback') -> dict:
        if kind == 'dictation':
            entry = find_transcript(str(entry_id))
            path = recording_path(entry.get('recording_id')) if entry else None
        elif kind == 'meeting':
            info = self.state.meetings.audio_paths(str(entry_id)).get(str(track))
            path = info.get('path') if info else None
        else:
            raise ValueError('Unknown recording type')
        if not path:
            raise ValueError('This recording is no longer available')
        return {'path': str(path)}

    @staticmethod
    def _transcript(entry: dict) -> dict:
        return {
            'id': str(entry.get('id', '')),
            'delivery': str(entry.get('delivery', 'inserted')),
            'original_text': str(entry.get('original_text') or entry.get('text', '')),
            'has_audio': bool(recording_path(entry.get('recording_id'))) if entry.get('recording_id') else False,
            'timestamp': str(entry.get('timestamp', '')),
            'text': str(entry.get('text', '')),
            'app': app_label(entry.get('app')),
            'duration_s': entry.get('duration_s') or 0,
        }

    # --- Dictionary ----------------------------------------------------------

    # One list for the page: the words Syllune listens for (each marked if it
    # was learned rather than typed) and the spellings it always corrects.
    def dictionary(self) -> dict:
        settings = self._dictionary_settings()
        learned = {word.lower() for word in settings['learned']}
        words = [{'word': word, 'learned': word.lower() in learned} for word in self._hotwords()]
        replacements = [_plain_replacement(item) for item in self._replacements()]
        return {'words': words, 'replacements': replacements, 'auto_learn': settings['auto_learn']}

    # `heard` is optional: what Syllune keeps writing instead of the word. With
    # it the word is listened for and that spelling is corrected as well.
    def dictionary_add(self, word: str, heard: str = '') -> dict:
        word = _require_term(word)
        words = self._hotwords()
        if word.lower() not in {w.lower() for w in words}:
            self._set_hotwords(words + [word])
        settings = self._dictionary_settings()
        ignored = [w for w in settings['ignored'] if w.lower() != word.lower()]
        if ignored != settings['ignored']:
            self.config.update_user_setting('dictionary', 'ignored', ignored)
        if str(heard or '').strip():
            self._add_replacement(_require_term(heard), word)
        return self.dictionary()

    # A removed word is remembered, so learning never brings it back.
    def dictionary_remove(self, word: str) -> dict:
        word = str(word or '')
        words = self._hotwords()
        self._set_hotwords([w for w in words if w != word])
        settings = self._dictionary_settings()
        self.config.update_user_setting('dictionary', 'learned', [w for w in settings['learned'] if w != word])
        if word.strip() and word.lower() not in {w.lower() for w in settings['ignored']}:
            self.config.update_user_setting('dictionary', 'ignored', (settings['ignored'] + [word])[-MAX_IGNORED_WORDS:])
        return self.dictionary()

    def dictionary_auto(self, enabled: bool) -> dict:
        self.config.update_user_setting('dictionary', 'auto_learn', _require_bool(enabled))
        if enabled:
            self._learn()
        return self.dictionary()

    # Called by the engine each time a dictation is saved. History is only
    # read every few dictations, off the dictation's own thread.
    def learn_words(self):
        self._dictations_since_learning += 1
        if self._dictations_since_learning >= LEARN_EVERY:
            self._dictations_since_learning = 0
            self._pool.submit(self._learn_in_background)

    def _learn_in_background(self):
        try:
            with self._write_lock:
                added = self._learn()
            if added:
                logger.info(f"Dictionary learned {len(added)} word(s)")
                self.state._notify_hub('dictionary')
        except Exception as e:
            logger.warning(f"Could not learn words from history: {e}")

    # Adds the names and terms that keep coming up in recent dictations to the
    # running recogniser and to user_settings.yaml. Returns what was added.
    def _learn(self) -> list:
        settings = self._dictionary_settings()
        if not settings['auto_learn']:
            return []
        words = self._hotwords()
        # A spelling that is already corrected needs no help from the recogniser.
        ignored = list(settings['ignored'])
        for item in self._replacements():
            ignored.extend(str(item.get('to', '')).split())
        texts = [entry.get('text', '') for entry in load_transcripts()[:LEARN_HISTORY]]
        added = words_to_learn(texts, words, settings['learned'], ignored)
        if added:
            self._set_hotwords(words + added)
            kept = [w for w in settings['learned'] if w in words]
            self.config.update_user_setting('dictionary', 'learned', kept + added)
        return added

    def _dictionary_settings(self) -> dict:
        section = self.config.config.get('dictionary') or {}
        return {
            'auto_learn': bool(section.get('auto_learn', True)),
            'learned': [str(word) for word in section.get('learned') or []],
            'ignored': [str(word) for word in section.get('ignored') or []],
        }

    def replacement_remove(self, heard: str) -> dict:
        heard = str(heard or '').strip().lower()
        items = [_plain_replacement(item) for item in self._replacements()]
        kept = [item for item in items if str(item.get('from', '')).lower() != heard]
        if len(kept) != len(items):
            self.config.update_user_setting('postprocess', 'replacements', kept)
        return self.dictionary()

    # whisper.hotwords as the file has it now. `--add-word` writes the file
    # directly and that section isn't hot-reloaded, so the hub re-reads it
    # rather than saving a stale in-memory list over it.
    def _hotwords(self) -> list:
        words = [str(word) for word in list_hotwords()]
        self.config.config['whisper']['hotwords'] = list(words)
        return words

    def _set_hotwords(self, words: list):
        self.config.update_user_setting('whisper', 'hotwords', list(words))
        from .whisper_engine import join_hotwords
        # While the model is still loading there is no recogniser to tell yet;
        # it reads the saved list when it starts.
        if self.state.whisper_engine is not None:
            self.state.whisper_engine.hotwords = join_hotwords(words)

    # postprocess.replacements, refreshed from disk by the postprocess
    # hot-reload, so an edit made elsewhere is never overwritten.
    def _replacements(self) -> list:
        items = self.config.get_postprocess_config().get('replacements') or []
        return [item for item in items if hasattr(item, 'get')]

    # Same rule as corrections.add_replacement: a "heard" already listed
    # (ignoring case) gets its fix updated rather than a second entry.
    def _add_replacement(self, heard: str, fixed: str) -> bool:
        heard, fixed = heard.strip(), fixed.strip()
        if not heard or not fixed or heard == fixed:
            return False
        items = [_plain_replacement(item) for item in self._replacements()]
        for item in items:
            if str(item.get('from', '')).lower() == heard.lower():
                if str(item.get('to', '')) == fixed:
                    return False
                item['from'], item['to'] = heard, fixed
                break
        else:
            items.append({'from': heard, 'to': fixed})
        self.config.update_user_setting('postprocess', 'replacements', items)
        return True

    # --- Styles --------------------------------------------------------------

    def styles(self) -> dict:
        app_rules = self.state.app_rules
        app_rules._reload_if_changed()
        rules = {rule.get('id'): rule for rule in app_rules.rules or () if rule.get('id')}
        postprocess = self.config.get_postprocess_config()
        rows = []
        for rule_id, label in STYLE_RULES:
            rule = rules.get(rule_id)
            if not rule:
                continue
            apps = rule.get('match') or []
            rows.append({
                'id': rule_id,
                'label': label,
                'style': str(rule.get('style') or ''),
                'instructions': str(rule.get('instructions') or ''),
                'apps': [str(app) for app in ([apps] if isinstance(apps, str) else apps)],
            })
        return {
            'styles': list(available_styles(postprocess)),
            'default_style': str(postprocess.get('style') or ''),
            'rules': rows,
        }

    # `rule_id` 'default' is the global style, for apps no rule matches.
    def style_set(self, rule_id: str, style: str = None, instructions: str = None) -> dict:
        names = available_styles(self.config.get_postprocess_config())
        if style is not None and style != '' and style not in names:
            raise ValueError(f"unknown style {style!r}")
        if rule_id == 'default':
            self.config.update_user_setting('postprocess', 'style', style or '')
            return self.styles()
        self._require_style_rule(rule_id)
        changes = {}
        if style is not None:
            changes['style'] = style or None
        if instructions is not None:
            changes['instructions'] = str(instructions).strip()[:MAX_INSTRUCTIONS] or None
        self.state.app_rules.set_override(rule_id, changes)
        return self.styles()

    def rule_apps_set(self, rule_id: str, apps: list) -> dict:
        self._require_style_rule(rule_id)
        if not isinstance(apps, list):
            raise ValueError("expected a list of apps")
        cleaned, seen = [], set()
        for app in apps:
            app = _require_term(app)
            if app.lower() not in seen:
                seen.add(app.lower())
                cleaned.append(app)
        if not cleaned:
            raise ValueError("A style needs at least one app")
        self.state.app_rules.set_override(rule_id, {'match': cleaned[:MAX_RULE_APPS]})
        return self.styles()

    @staticmethod
    def _require_style_rule(rule_id: str):
        if rule_id not in {rule for rule, _ in STYLE_RULES}:
            raise ValueError(f"unknown rule {rule_id!r}")

    # --- Settings ------------------------------------------------------------

    def setup_status(self) -> dict:
        return self._setup.status()

    # Reuse the existing microphone setter so a saved choice also reaches the
    # running recorder. Validate the entire request before a device change.
    def setup_save(self, values: dict) -> dict:
        self._setup.validate(values)
        if 'mic' in values:
            self._set_mic(values['mic'])
        return self._setup.save(values)

    def setup_download(self, model_key: str) -> dict:
        return self._setup.start_download(model_key)

    def setup_complete(self) -> dict:
        return self._setup.complete()

    def setup_mic_test(self, action: str) -> dict:
        return self._setup.mic_test(action)

    def settings(self) -> dict:
        ai = self.config.get_ai_config()
        overlay = self.config.config.get('overlay') or {}
        editor = self.state.smart_editor
        models = editor.available_models()
        autostart_supported = autostart.is_supported()
        return {
            'quality': self.state.current_quality(),
            'short_messages': self.state.short_message_seconds(),
            'keep_audio': bool((self.config.config.get('history') or {}).get('keep_audio', True)),
            'mics': self.state.microphone_choices(),
            'mic': self.state.configured_microphone(),
            'ai_model': str(ai.get('model') or 'gemma3:4b'),
            'ai_models': models,
            'ai_reachable': bool(models) or editor.reachable(),
            'about_me': str(ai.get('about_me') or ''),
            'idle_pill': bool(overlay.get('idle_pill', True)),
            'sounds': bool(self.config.get_audio_feedback_config().get('enabled', True)),
            'retention_days': self._retention(),
            'autostart_supported': autostart_supported,
            'autostart': autostart_supported and autostart.is_enabled(),
            'update_checks': bool((self.config.config.get('update_check') or {}).get('enabled', False)),
        }

    def setting_set(self, key: str, value=None) -> dict:
        setter = self._setters.get(key)
        if setter is None:
            raise ValueError(f"unknown setting {key!r}")
        setter(value)
        return {}

    # Quality is the one switch for the AI edit: Fast skips it, Balanced and
    # Best use it, so choosing either also turns the edit back on.
    def _set_quality(self, value):
        if value not in QUALITIES:
            raise ValueError(f"quality is one of {', '.join(QUALITIES)}")
        if value != 'fast' and not self.config.get_ai_config().get('enabled', True):
            self.config.update_user_setting('ai', 'enabled', True)
        self.state.apply_quality(value)

    # The old model leaves VRAM before the new one loads, so switching never
    # holds both on the GPU.
    def _set_ai_model(self, value):
        model = _require_term(value)
        installed = self.state.smart_editor.available_models()
        if installed and model not in installed:
            raise ValueError(f"{model} isn't installed in Ollama")
        self.state.smart_editor.unload()
        self.config.update_user_setting('ai', 'model', model)
        self.state.sync_ai_model()

    def _set_about_me(self, value):
        self.config.update_user_setting('ai', 'about_me', str(value or '').strip()[:MAX_ABOUT_ME])

    # `value` is "default" or a microphone name from settings()['mics'].
    def _set_mic(self, value):
        if not isinstance(value, str) or not self.state.select_microphone(value):
            raise ValueError("That microphone isn't connected")

    # Seconds of recording that still count as a short message (0 = off).
    # Read per recording, so it applies from the next dictation.
    def _set_short_messages(self, value):
        from .state_manager import SHORT_MESSAGE_SECONDS
        if isinstance(value, bool) or value not in SHORT_MESSAGE_SECONDS:
            raise ValueError(f"short_messages is one of {', '.join(map(str, SHORT_MESSAGE_SECONDS))}")
        self.config.update_user_setting('whisper', 'short_message_seconds', int(value))

    def _set_sounds(self, value):
        self.config.update_user_setting('audio_feedback', 'enabled', _require_bool(value))
        feedback = getattr(self.state.audio_feedback, '_component', None)
        if feedback is not None:
            feedback.enabled = value

    def _set_idle_pill(self, value):
        self.config.update_user_setting('overlay', 'idle_pill', _require_bool(value))
        set_idle_pill = getattr(self.state.level_overlay, 'set_idle_pill', None)
        if set_idle_pill:
            set_idle_pill(value)

    def _set_autostart(self, value):
        if not autostart.is_supported():
            raise ValueError("Start on login isn't available in this install")
        autostart.enable() if _require_bool(value) else autostart.disable()
        self.state.system_tray.refresh_menu()

    # None keeps history, 0 stops saving new dictations, N keeps N days.
    def _set_retention(self, value):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                  or not 0 <= value <= 3650):
            raise ValueError("retention is a number of days, or null to keep everything")
        self.config.update_user_setting('history', 'retention_days', value)

    def _retention(self):
        self.config.get_postprocess_config()          # refreshes `history` from disk
        value = (self.config.config.get('history') or {}).get('retention_days')
        try:
            return None if value is None or value == '' else max(0, int(value))
        except (TypeError, ValueError):
            return None

    def _typing_wpm(self) -> int:
        try:
            value = int((self.config.config.get('stats') or {}).get('typing_wpm') or 0)
        except (TypeError, ValueError):
            value = 0
        return value if value > 0 else stats.DEFAULT_TYPING_WPM

    # --- Meetings ------------------------------------------------------------

    # The list, plus when the current recording started (epoch ms) so the
    # page can count up without polling.
    def meetings(self) -> dict:
        controller = self.state.meetings
        return {
            'meetings': controller.list(),
            'recording_since': controller.started_ms(),
            'ai_enabled': bool(self.config.get_ai_config().get('enabled', True)),
        }

    def meeting(self, meeting_id: str) -> dict:
        return self.state.meetings.get(meeting_id)

    def meeting_start(self) -> dict:
        self.state.meetings.start_meeting()
        return self.meetings()

    def meeting_stop(self) -> dict:
        self.state.stop_meeting()
        return self.meetings()

    def meeting_rename_speaker(self, meeting_id: str, key: str, name: str = '') -> dict:
        return self.state.meetings.rename_speaker(meeting_id, key, name)

    def meeting_summarize(self, meeting_id: str) -> dict:
        return self.state.meetings.summarize_again(meeting_id)

    def meeting_retry(self, meeting_id: str) -> dict:
        return self.state.meetings.retry(meeting_id)

    def meeting_process_now(self, meeting_id: str) -> dict:
        return self.state.meetings.process_now(meeting_id)

    def meeting_delete(self, meeting_id: str) -> dict:
        return self.state.meetings.delete(meeting_id)

    def meeting_open(self, meeting_id: str) -> dict:
        return self.state.meetings.open_markdown(meeting_id)

    def meeting_transcript(self, meeting_id: str, speaker: str = '') -> dict:
        return self.state.meetings.transcript(meeting_id, speaker)

    def meeting_open_transcript(self, meeting_id: str, speaker: str = '') -> dict:
        return self.state.meetings.open_transcript(meeting_id, speaker)

    def meeting_audio(self, meeting_id: str) -> dict:
        return self.state.meetings.audio_paths(meeting_id)

    # --- Folders -------------------------------------------------------------

    def open_folder(self, which: str) -> dict:
        if which == 'data':
            path = get_user_app_data_path()
        elif which == 'notes':
            from .notes import notes_folder
            path = notes_folder((self.config.config.get('notes') or {}).get('folder'))
        else:
            raise ValueError(f"unknown folder {which!r}")
        os.makedirs(path, exist_ok=True)
        open_file(str(path))
        return {}
