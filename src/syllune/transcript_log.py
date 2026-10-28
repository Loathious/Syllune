# Durable local dictation journal shared by History and the pending queue.
# New entries retain their original text and audio handle across edits; old
# entries remain readable. Only explicit text-age retention removes history.
import datetime
import json
import logging
import os
import tempfile
import threading
import uuid
from pathlib import Path

from .utils import get_user_app_data_path

logger = logging.getLogger(__name__)
_LOG_FILE = 'transcripts.jsonl'
_DELIVERY_STATES = frozenset(('pending', 'delivering', 'inserted', 'suppressed', 'failed'))
_write_lock = threading.Lock()


# Hand-edited settings must not disable the whole journal accidentally.
def _retention(value):
    if value is None or value == '':
        return None
    try:
        days = int(value)
    except (TypeError, ValueError):
        return None
    return days if days >= 0 else None


def _path() -> Path:
    return Path(get_user_app_data_path()) / _LOG_FILE


# Invalid JSON values are isolated to one line, including valid non-object JSON.
def _decode(line):
    try:
        entry = json.loads(line)
        return entry if isinstance(entry, dict) else None
    except (ValueError, TypeError):
        return None


# Rewrites use a sibling temp file so interruption cannot truncate saved history.
def _atomic_lines(path, lines):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='transcripts-', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            if lines:
                output.write('\n'.join(lines) + '\n')
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


# Save before delivery; callers receive a durable handle to update afterwards.
# None keeps all text, zero stores none, and positive days prune only old text.
def record_transcript(text: str, app: str = '', duration_s: float = 0.0, retention_days=None,
                      *, delivery='inserted', recording_id=None, original_text=None):
    if not text or not text.strip() or _retention(retention_days) == 0:
        return None
    if delivery not in _DELIVERY_STATES:
        return None
    if recording_id is not None:
        from .recording_archive import valid_recording_id
        if not valid_recording_id(recording_id):
            return None
    entry = {
        'id': uuid.uuid4().hex,
        'timestamp': datetime.datetime.now().isoformat(timespec='seconds'),
        'text': text,
        'original_text': text if original_text is None else original_text,
        'delivery': delivery,
        'app': app,
        'duration_s': round(duration_s, 2),
        'chars': len(text),
    }
    if recording_id is not None:
        entry['recording_id'] = recording_id
    try:
        with _write_lock:
            path = _path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a+b') as output:
                # A crash can leave an unfinished JSON line; start a new line
                # so recovery skips only that fragment, never the next entry.
                output.seek(0, os.SEEK_END)
                if output.tell():
                    output.seek(-1, os.SEEK_END)
                    if output.read(1) != b'\n':
                        output.write(b'\n')
                output.write((json.dumps(entry, ensure_ascii=False) + '\n').encode('utf-8'))
                output.flush()
                os.fsync(output.fileno())
            _maybe_rotate(path, _retention(retention_days))
        return entry
    except Exception as error:
        logger.debug('Transcript log write failed: %s', error)
        return None


# File order is authoritative even when several dictations share a timestamp.
def _load_oldest_first():
    try:
        with _write_lock:
            lines = _path().read_text(encoding='utf-8').splitlines()
        return [entry for line in lines if (entry := _decode(line)) and entry.get('text')]
    except FileNotFoundError:
        return []
    except OSError as error:
        logger.warning('Failed to load transcripts: %s', error)
        return []


def load_transcripts() -> list:
    return list(reversed(_load_oldest_first()))


def find_transcript(entry_id: str) -> dict | None:
    if not entry_id:
        return None
    return next((entry for entry in _load_oldest_first() if entry.get('id') == entry_id), None)


def pending_transcripts() -> list:
    return [entry for entry in _load_oldest_first() if entry.get('delivery') == 'pending']


# Apply one change to the newest matching entry without disturbing other lines.
def _rewrite_matching(matches, change):
    try:
        with _write_lock:
            path = _path()
            lines = path.read_text(encoding='utf-8').splitlines()
            for index in range(len(lines) - 1, -1, -1):
                entry = _decode(lines[index])
                if entry is None or not matches(entry):
                    continue
                changed = change(entry)
                if changed is None:
                    del lines[index]
                else:
                    lines[index] = json.dumps(changed, ensure_ascii=False)
                _atomic_lines(path, lines)
                return True, changed, entry
    except (OSError, ValueError, TypeError) as error:
        logger.debug('Transcript log update failed: %s', error)
    return False, None, None


def update_delivery(entry_id: str, status: str) -> bool:
    if status not in _DELIVERY_STATES or not entry_id:
        return False

    def change(entry):
        entry['delivery'] = status
        return entry
    return _rewrite_matching(lambda entry: entry.get('id') == entry_id, change)[0]


# Re-queueing a preview changes its insertable text while preserving the baseline.
def queue_pending(entry_id: str, text=None) -> dict | None:
    if not entry_id or (text is not None and (not isinstance(text, str) or not text.strip())):
        return None

    def change(entry):
        entry.setdefault('original_text', entry['text'])
        if text is not None:
            entry['text'] = text
            entry['chars'] = len(text)
        entry['delivery'] = 'pending'
        return entry
    changed, entry, _ = _rewrite_matching(lambda entry: entry.get('id') == entry_id, change)
    return entry if changed else None


# Timestamp/text matching preserves the existing hub API, including legacy lines.
def update_transcript(timestamp: str, old_text: str, new_text: str) -> bool:
    new_text = (new_text or '').strip()
    if not new_text:
        return False

    def change(entry):
        entry.setdefault('original_text', entry['text'])
        entry['text'] = new_text
        entry['chars'] = len(new_text)
        return entry
    return _rewrite_entry(timestamp, old_text, change)


# Audio is removed only after the durable text deletion succeeds.
def delete_transcript(timestamp: str, text: str) -> bool:
    changed, _, deleted = _rewrite_matching(
        lambda entry: entry.get('timestamp') == timestamp and entry.get('text') == text,
        lambda entry: None)
    if changed and deleted.get('recording_id'):
        from .recording_archive import delete_recording
        delete_recording(deleted['recording_id'], root=Path(get_user_app_data_path()) / 'dictation_audio')
    return changed


def _rewrite_entry(timestamp: str, text: str, change) -> bool:
    return _rewrite_matching(
        lambda entry: entry.get('timestamp') == timestamp and entry.get('text') == text, change)[0]


def _stamp(line: str) -> str:
    entry = _decode(line)
    return str(entry.get('timestamp') or '') if entry else ''


# Explicit age retention applies only to text; audio has no automatic expiry.
def _maybe_rotate(path: Path, retention_days=None):
    if not retention_days:
        return
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=retention_days)).isoformat(timespec='seconds')
        kept = [line for line in lines if _stamp(line) >= cutoff]
        if len(kept) != len(lines):
            _atomic_lines(path, kept)
    except OSError as error:
        logger.debug('Transcript retention failed: %s', error)
