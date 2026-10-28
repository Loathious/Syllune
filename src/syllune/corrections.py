# corrections.py
# Persistence for post-transcription corrections (postprocess.replacements) —
# the backing store for the history window's one-click "Fix this everywhere...".
# Writes user_settings.yaml directly (same pattern as dictionary.py) so callers
# don't need a live ConfigManager; the running app hot-reloads the postprocess
# section on file change, so a correction takes effect on the next dictation.
# Also works out which corrections an edit in the hub's History page teaches.

import difflib
import logging
from pathlib import Path
from typing import List

from ruamel.yaml import YAML

from .utils import get_user_app_data_path

logger = logging.getLogger(__name__)
USER_SETTINGS = "user_settings.yaml"


def _settings_path() -> Path:
    return Path(get_user_app_data_path()) / USER_SETTINGS


# Read the current correction list from user_settings.yaml (empty if unset).
def list_replacements() -> List[dict]:
    path = _settings_path()
    if not path.exists():
        return []
    try:
        with open(path, encoding='utf-8') as f:
            data = YAML().load(f) or {}
        items = (data.get('postprocess') or {}).get('replacements') or []
        return [dict(i) for i in items if isinstance(i, dict)]
    except Exception as e:
        logger.warning(f"Could not read replacements: {e}")
        return []


# Append a correction (from_text → to_text). Returns False for empty input, a
# no-op fix (from == to), or an existing identical rule; True once persisted.
def add_replacement(from_text: str, to_text: str,
                    whole_word: bool = True, case_sensitive: bool = False) -> bool:
    from_text = (from_text or '').strip()
    to_text = (to_text or '')
    if not from_text or from_text == to_text:
        return False

    path = _settings_path()
    yaml = YAML()
    if path.exists():
        with open(path, encoding='utf-8') as f:
            data = yaml.load(f) or {}
    else:
        data = {}

    postprocess = data.setdefault('postprocess', {})
    current = list(postprocess.get('replacements') or [])

    # Skip an exact duplicate, but allow updating the target for an existing
    # "from" by replacing that entry rather than stacking a second. Match the
    # "from" case-INsensitively (matching is case-insensitive by default), so
    # adding "teh" when "Teh" already exists updates it instead of creating a
    # second rule that double-fires on the same text.
    for existing in current:
        if isinstance(existing, dict) and str(existing.get('from', '')).lower() == from_text.lower():
            if str(existing.get('to', '')) == to_text:
                return False
            existing['from'] = from_text
            existing['to'] = to_text
            _write(path, yaml, data)
            return True

    entry = {'from': from_text, 'to': to_text}
    if not whole_word:
        entry['whole_word'] = False
    if case_sensitive:
        entry['case_sensitive'] = True
    current.append(entry)
    postprocess['replacements'] = current
    _write(path, yaml, data)
    return True


# =============================================================================
# Learning from edits
# =============================================================================

# Function words in English and Swedish. A one-word correction of one of
# these ("the" → "a") is a rewording, and replacing it everywhere would wreck
# every later dictation.
_COMMON_WORDS = {
    'a', 'an', 'the', 'and', 'or', 'but', 'so', 'to', 'of', 'in', 'on', 'at',
    'for', 'is', 'it', 'i', 'you', 'we', 'he', 'she', 'they', 'this', 'that',
    'be', 'was', 'are', 'not', 'no', 'yes', 'with', 'as', 'by', 'my', 'me',
    'och', 'att', 'det', 'som', 'en', 'ett', 'är', 'på', 'för', 'med', 'jag',
    'du', 'vi', 'de', 'han', 'hon', 'inte', 'har', 'var', 'till', 'av', 'om',
    'den', 'så', 'men', 'eller', 'ja', 'nej', 'kan', 'ska', 'vill',
}

# Edge punctuation is ignored, so fixing a comma never reads as a word change.
_EDGE_PUNCTUATION = '.,!?;:"\'()[]{}«»“”‘’…-'

# How far an edit may go and still count as fixing a mishearing: short spans,
# a few of them, and each fix must look like what was heard (a rewording like
# "I think" → "we believe" doesn't).
MAX_SPAN_WORDS = 3
MAX_SPANS = 3
MIN_SIMILARITY = 0.45


def _core(token: str) -> str:
    return token.strip(_EDGE_PUNCTUATION)


# A capital letter added at the start of a word is sentence casing, not a
# fix to learn ("so" → "So"). Inner capitals and acronyms are ("github" →
# "GitHub", "ai" → "AI").
def _is_sentence_casing(heard: str, fixed: str) -> bool:
    return heard.lower() == fixed.lower() and fixed == heard[:1].upper() + heard[1:]


# The corrections an edit of a past dictation teaches, as (heard, fixed)
# pairs. Empty when the edit looks like a rewrite rather than a fix.
def corrections_from_edit(original: str, edited: str) -> List[tuple]:
    heard_words = [w for w in (_core(t) for t in (original or '').split()) if w]
    fixed_words = [w for w in (_core(t) for t in (edited or '').split()) if w]
    matcher = difflib.SequenceMatcher(a=heard_words, b=fixed_words, autojunk=False)
    changes = [op for op in matcher.get_opcodes() if op[0] != 'equal']
    if not changes or len(changes) > MAX_SPANS:
        return []

    learned = []
    for tag, i1, i2, j1, j2 in changes:
        # Inserted or deleted words are rewording; only swaps are mishearings.
        if tag != 'replace' or i2 - i1 > MAX_SPAN_WORDS or j2 - j1 > MAX_SPAN_WORDS:
            continue
        heard = ' '.join(heard_words[i1:i2])
        fixed = ' '.join(fixed_words[j1:j2])
        if heard == fixed or _is_sentence_casing(heard, fixed):
            continue
        if i2 - i1 == 1 and heard.lower() in _COMMON_WORDS:
            continue
        similarity = difflib.SequenceMatcher(a=heard.lower(), b=fixed.lower()).ratio()
        if heard.lower() != fixed.lower() and similarity < MIN_SIMILARITY:
            continue
        learned.append((heard, fixed))
    return learned


def _write(path: Path, yaml: YAML, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f)
    logger.info("Saved a post-transcription correction to user_settings.yaml")
