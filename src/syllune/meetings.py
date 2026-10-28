# meetings.py
# Meeting recordings, from the record button to a Markdown file. A meeting is
# recorded as two tracks (your mic, and what this PC plays), then processed:
# who spoke when, a transcript with speakers, an AI summary, and a Markdown
# file in Voice Notes\Meetings. Each meeting is a small JSON file in the app's
# data folder; audio stays until the user explicitly deletes the recording.

import bisect
import datetime
import hashlib
import json
import logging
import queue
import re
import shutil
import threading
import time
import wave
from contextlib import ExitStack
from pathlib import Path

import numpy as np
from .meeting_transcripts import selected_transcript, write_transcripts

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# Words closer than this belong to one utterance.
UTTERANCE_GAP_S = 1.0
# The same speaker continuing within this pause stays in one paragraph.
TURN_GAP_S = 2.0
# A mic word is an echo of the call when the call had the same word this
# close in time (the call leaking from speakers into the mic).
ECHO_WINDOW_S = 1.0
# Utterances that are mostly echo are dropped whole; inside a real utterance
# only runs of this many echoed words are removed.
ECHO_SHARE = 0.6
ECHO_RUN = 3

RECORDING, WAITING, PROCESSING, READY, FAILED = 'recording', 'waiting', 'processing', 'ready', 'failed'
YOU = 'you'


class _ProcessingCancelled(Exception):
    """A queued recording was deleted or the app is shutting down."""


# =============================================================================
# Store
# =============================================================================

class MeetingStore:
    # One folder per meeting under `root`: meeting.json, plus mic.wav and
    # system.wav and playback.wav retained until explicit user deletion.
    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.Lock()

    def folder(self, meeting_id: str) -> Path:
        if not re.fullmatch(r'\d{8}-\d{6}', str(meeting_id or '')):
            raise ValueError("unknown meeting")
        return self.root / meeting_id

    def create(self) -> dict:
        now = datetime.datetime.now().replace(microsecond=0)
        while (self.root / now.strftime('%Y%m%d-%H%M%S')).exists():
            now += datetime.timedelta(seconds=1)
        meeting = {
            'id': now.strftime('%Y%m%d-%H%M%S'),
            'started': now.isoformat(),
            'duration': 0,
            'status': RECORDING,
            'step': '',
            'progress': None,
            'error': '',
            'attempts': 0,
            'title': '',
            'language': '',
            'speakers': {},
            'turns': [],
            'summary': None,
            'markdown': '',
            'markdown_hash': '',
            'transcript_exports': {},
            'capture_warnings': [],
        }
        self.folder(meeting['id']).mkdir(parents=True, exist_ok=True)
        self.save(meeting)
        return meeting

    def load(self, meeting_id: str):
        path = self.folder(meeting_id) / 'meeting.json'
        with self._lock:
            try:
                return json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                return None

    # Written to a temporary file and swapped in, so a crash mid-write never
    # leaves a meeting unreadable. A meeting deleted from the hub while it
    # waited in the queue is not brought back.
    def save(self, meeting: dict):
        folder = self.folder(meeting['id'])
        path = folder / 'meeting.json'
        temporary = path.with_suffix('.tmp')
        with self._lock:
            if not folder.exists():
                return
            temporary.write_text(json.dumps(meeting, ensure_ascii=False, indent=1), encoding='utf-8')
            temporary.replace(path)

    def all(self) -> list:
        if not self.root.exists():
            return []
        meetings = []
        for folder in sorted(self.root.iterdir(), reverse=True):
            if folder.is_dir() and re.fullmatch(r'\d{8}-\d{6}', folder.name):
                meeting = self.load(folder.name)
                if meeting:
                    meetings.append(meeting)
        return meetings

    def has_audio(self, meeting_id: str) -> bool:
        folder = self.folder(meeting_id)
        return (folder / 'mic.wav').exists() or (folder / 'system.wav').exists()

    def delete_audio(self, meeting_id: str):
        for name in ('mic.wav', 'system.wav', 'playback.wav'):
            try:
                (self.folder(meeting_id) / name).unlink(missing_ok=True)
            except OSError as e:
                logger.warning(f"Could not delete meeting audio {name}: {e}")

    # The meeting's own folder only. Its Markdown file is yours and stays.
    def delete(self, meeting_id: str):
        with self._lock:
            shutil.rmtree(self.folder(meeting_id), ignore_errors=True)


# =============================================================================
# Audio
# =============================================================================

def read_track(path: Path):
    try:
        with wave.open(str(path), 'rb') as wav:
            pcm = wav.readframes(wav.getnframes())
    except (OSError, EOFError, wave.Error):
        return None
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


# Seconds of the track louder than a quiet room. Loopback silence is digital
# zero and a mic's floor sits far below speech, so this is a cheap way to skip
# a track nobody spoke on before paying for a transcription.
def sound_seconds(audio, floor_db: float = -45.0) -> float:
    if audio is None or not len(audio):
        return 0.0
    frame = SAMPLE_RATE // 10
    usable = len(audio) // frame * frame
    if not usable:
        return 0.0
    rms = np.sqrt(np.mean(audio[:usable].reshape(-1, frame) ** 2, axis=1) + 1e-12)
    return float(np.sum(20 * np.log10(rms) > floor_db)) / 10


# Only exact digital silence is safe to skip; Whisper's VAD decides whether
# a nonzero track contains words, including a quiet or subsecond response.
def _has_signal(audio) -> bool:
    return audio is not None and bool(np.any(np.isfinite(audio) & (audio != 0)))


# Mix the retained PCM tracks in bounded chunks, avoiding a second hour-long
# float allocation. Raw tracks remain available if creating playback fails.
def write_playback(folder: Path):
    destination = folder / 'playback.wav'
    temporary = folder / 'playback.tmp'
    try:
        with ExitStack() as stack:
            tracks = [stack.enter_context(wave.open(str(folder / name), 'rb'))
                      for name in ('mic.wav', 'system.wav') if (folder / name).exists()]
            if not tracks:
                return
            if any(t.getnchannels() != 1 or t.getsampwidth() != 2 or t.getframerate() != SAMPLE_RATE for t in tracks):
                raise ValueError('Meeting playback requires 16 kHz mono PCM tracks')
            output = stack.enter_context(wave.open(str(temporary), 'wb'))
            output.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
            while True:
                blocks = [np.frombuffer(t.readframes(SAMPLE_RATE * 10), dtype=np.int16) for t in tracks]
                length = max(map(len, blocks))
                if not length:
                    break
                mixed = np.zeros(length, dtype=np.float32)
                for block in blocks:
                    mixed[:len(block)] += block
                mixed /= len(tracks)
                output.writeframes(np.clip(mixed, -32768, 32767).astype(np.int16).tobytes())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


# =============================================================================
# Transcript
# =============================================================================

def _speaker_key(index: int) -> str:
    return f's{index + 1}'


def default_speaker_name(key: str) -> str:
    return 'You' if key == YOU else f"Speaker {key[1:]}"


# Which diarized speaker said each word, by the word's midpoint. A word in a
# gap between segments goes to the nearest one within a second, else stays
# with whoever spoke last.
def assign_speakers(words: list, segments: list) -> list:
    if not segments:
        return [(start, end, text, _speaker_key(0)) for start, end, text in words]
    starts = [segment[0] for segment in segments]
    assigned, last = [], _speaker_key(segments[0][2])
    for start, end, text in words:
        middle = (start + end) / 2
        i = bisect.bisect_right(starts, middle) - 1
        speaker = None
        # Overlapping segments: look back a few for one that covers the word.
        for j in range(i, max(-1, i - 4), -1):
            if segments[j][0] <= middle <= segments[j][1]:
                speaker = segments[j][2]
                break
        if speaker is None:
            before = segments[i] if i >= 0 else None
            after = segments[i + 1] if i + 1 < len(segments) else None
            gaps = []
            if before is not None:
                gaps.append((middle - before[1], before[2]))
            if after is not None:
                gaps.append((after[0] - middle, after[2]))
            gap, nearest = min(gaps) if gaps else (None, None)
            if gap is not None and gap <= 1.0:
                speaker = nearest
        key = _speaker_key(speaker) if speaker is not None else last
        assigned.append((start, end, text, key))
        last = key
    return assigned


def _normalized(text: str) -> str:
    return re.sub(r'[^\w]', '', text.lower())


# Splits one speaker's words (time-ordered) into utterances at pauses.
def _utterances(words: list) -> list:
    groups, current = [], []
    for word in words:
        if current and word[0] - current[-1][1] >= UTTERANCE_GAP_S:
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    return groups


# Without headphones the mic hears the call too, and Whisper writes it down a
# second time as if you had said it. Words the call had at the same moment
# are echo: an utterance that is mostly echo goes, and inside a real one only
# runs of echoed words are cut, so a short "yes" that matches is never lost.
def remove_echo(mic_words: list, call_words: list) -> list:
    if not call_words or not mic_words:
        return mic_words
    call = sorted(((start + end) / 2, _normalized(text)) for start, end, text in call_words)
    middles = [item[0] for item in call]

    def is_echo(word):
        middle, text = (word[0] + word[1]) / 2, _normalized(word[2])
        if not text:
            return False
        lo = bisect.bisect_left(middles, middle - ECHO_WINDOW_S)
        hi = bisect.bisect_right(middles, middle + ECHO_WINDOW_S)
        return any(call[k][1] == text for k in range(lo, hi))

    kept = []
    for utterance in _utterances(mic_words):
        flags = [is_echo(word) for word in utterance]
        if len(utterance) >= ECHO_RUN and sum(flags) >= ECHO_SHARE * len(utterance):
            continue
        run = []
        for word, echoed in zip(utterance, flags):
            if echoed:
                run.append(word)
                continue
            if len(run) < ECHO_RUN:
                kept.extend(run)
            run = []
            kept.append(word)
        if len(run) < ECHO_RUN:
            kept.extend(run)
    removed = len(mic_words) - len(kept)
    if removed:
        logger.info(f"Removed {removed} echoed word(s) from the mic track")
    return kept


# Utterances from both tracks in time order, with one speaker's consecutive
# utterances joined into a paragraph while their pauses stay short.
def build_turns(words: list) -> list:
    by_speaker = {}
    for word in words:
        by_speaker.setdefault(word[3], []).append(word)
    utterances = []
    for speaker_words in by_speaker.values():
        utterances.extend(_utterances(sorted(speaker_words)))
    utterances.sort(key=lambda group: group[0][0])

    turns = []
    for group in utterances:
        text = re.sub(r'\s+', ' ', ''.join(word[2] for word in group)).strip()
        if not text:
            continue
        speaker, start, end = group[0][3], group[0][0], group[-1][1]
        last = turns[-1] if turns else None
        if last and last['speaker'] == speaker and start - last['end'] < TURN_GAP_S:
            last['text'] = f"{last['text']} {text}"
            last['end'] = round(max(last['end'], end), 2)
        else:
            turns.append({'speaker': speaker, 'start': round(start, 2), 'end': round(end, 2), 'text': text})
    return turns


# Both tracks to a transcript. With sound on the call track, the mic is you
# and the call is diarized into the other speakers. With a silent call track
# the meeting happened in the room, so the mic itself is diarized.
# `on_step(label, progress)` reports what is happening for the hub.
def build_transcript(mic, system, engine, on_step=lambda *_: None, before_stage=lambda: None) -> dict:
    from . import speaker_diarization

    def diarize(audio):
        before_stage()
        if not speaker_diarization.available():
            return []
        try:
            return speaker_diarization.diarize(
                audio, on_progress=lambda share: on_step('Finding who spoke', share))
        except Exception as e:
            logger.error(f"Speaker detection failed, using one speaker: {e}")
            return []

    languages = {}

    def transcribe(audio):
        before_stage()
        on_step('Transcribing', None)
        result = engine.transcribe_timed(audio, before_batch=before_stage)
        languages[result['language']] = languages.get(result['language'], 0) + len(result['words'])
        return result['words']

    has_call = _has_signal(system)
    has_mic = _has_signal(mic)
    if has_call:
        on_step('Finding who spoke', 0.0)
        segments = diarize(system)
        on_step('Transcribing', None)
        call_words = transcribe(system)
        mic_words = transcribe(mic) if has_mic else []
        words = [(s, e, t, YOU) for s, e, t in remove_echo(mic_words, call_words)]
        words += assign_speakers(call_words, segments)
    elif has_mic:
        on_step('Finding who spoke', 0.0)
        segments = diarize(mic)
        on_step('Transcribing', None)
        words = assign_speakers(transcribe(mic), segments)
    else:
        words = []

    turns = build_turns(words)
    speakers = {}
    if any(turn['speaker'] == YOU for turn in turns):
        speakers[YOU] = default_speaker_name(YOU)
    for turn in turns:
        key = turn['speaker']
        if key not in speakers:
            speakers[key] = None
    # Renumbered by who spoke first in the transcript, not by the diarizer's
    # internal order, so "Speaker 1" is the first voice you hear.
    renumber, number = {}, 0
    for key in speakers:
        if key != YOU:
            number += 1
            renumber[key] = f's{number}'
    for turn in turns:
        turn['speaker'] = renumber.get(turn['speaker'], turn['speaker'])
    speakers = {renumber.get(key, key): default_speaker_name(renumber.get(key, key)) for key in speakers}
    language = max(languages, key=languages.get) if languages else ''
    return {'turns': turns, 'speakers': speakers, 'language': language or '',
            'source': 'call' if has_call else 'room'}


# =============================================================================
# Summary
# =============================================================================

SUMMARY_RULES = """You write meeting notes from a transcript between <transcript> tags. Reply with JSON only, in exactly this shape:
{"title": "...", "summary": "...", "points": ["..."], "decisions": ["..."], "actions": ["..."]}

- Write every value in {language}, the language of the meeting.
- title: what the meeting was about, at most six words, no date.
- summary: two to four plain sentences.
- points: the main things discussed, one short line each.
- decisions: what was agreed. An empty list if nothing was decided.
- actions: who will do what, and by when if it was said, one line each, starting with the person's name as the transcript writes it. An empty list if there are none.
- Use only what is in the transcript. Never invent names, dates, numbers or decisions."""

PART_NOTE = "This is part {part} of {parts} of one long meeting; cover only this part."

COMBINE_RULES = """You combine partial notes of one long meeting into one set of notes. Each user message holds the notes of consecutive parts as JSON. Reply with JSON only, in exactly this shape:
{"title": "...", "summary": "...", "points": ["..."], "decisions": ["..."], "actions": ["..."]}

- Write every value in {language}.
- Merge repeated points, keep every decision and action item, and drop nothing that was agreed.
- Use only what is in the notes. Never invent anything."""

# Words per request. With the 8k context the AI edit already fits on the GPU,
# this leaves room for the rules and the reply even in Swedish (~1.6 tokens a
# word), so most meetings under half an hour need a single pass.
CHUNK_WORDS = 3500
SUMMARY_CONTEXT = 8192
SUMMARY_TOKENS = 900
SUMMARY_TIMEOUT_S = 240


def _language_name(code: str) -> str:
    from .smart_edit import LANGUAGE_NAMES
    return LANGUAGE_NAMES.get((code or '').lower(), 'the language of the transcript')


def _transcript_lines(meeting: dict) -> list:
    names = meeting.get('speakers') or {}
    return [f"{names.get(turn['speaker']) or default_speaker_name(turn['speaker'])}: {turn['text']}"
            for turn in meeting.get('turns') or ()]


def _chunks(lines: list) -> list:
    chunks, current, count = [], [], 0
    for line in lines:
        words = len(line.split())
        if current and count + words > CHUNK_WORDS:
            chunks.append(current)
            current, count = [], 0
        current.append(line)
        count += words
    if current:
        chunks.append(current)
    return chunks


# The model's JSON, reduced to the fields and types the notes use. Anything
# malformed is None, so a bad reply never lands in the notes.
def _clean_summary(reply: str):
    try:
        data = json.loads(reply)
    except (TypeError, ValueError):
        match = re.search(r'\{.*\}', reply or '', re.DOTALL)
        try:
            data = json.loads(match.group(0)) if match else None
        except ValueError:
            data = None
    if not isinstance(data, dict):
        return None

    def text(value, limit):
        return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]

    def items(value):
        if not isinstance(value, list):
            return []
        return [text(item, 300) for item in value if text(item, 300)][:15]

    summary = {
        'title': text(data.get('title'), 80).strip('"\'.'),
        'summary': text(data.get('summary'), 1500),
        'points': items(data.get('points')),
        'decisions': items(data.get('decisions')),
        'actions': items(data.get('actions')),
    }
    return summary if summary['summary'] or summary['points'] else None


def _ask(editor, rules: str, content: str):
    messages = [{'role': 'system', 'content': rules},
                {'role': 'user', 'content': content}]
    reply = editor.chat(messages, num_predict=SUMMARY_TOKENS, timeout=SUMMARY_TIMEOUT_S,
                        num_ctx=SUMMARY_CONTEXT, json_format=True)
    return _clean_summary(reply)


# Notes for the whole meeting. A long meeting is summarised part by part and
# the parts are then combined, so it never outgrows the model's context.
def summarize(meeting: dict, editor):
    lines = _transcript_lines(meeting)
    if not lines:
        return None
    language = _language_name(meeting.get('language'))
    rules = SUMMARY_RULES.replace('{language}', language)
    chunks = _chunks(lines)
    if len(chunks) == 1:
        return _ask(editor, rules, "<transcript>\n" + "\n".join(chunks[0]) + "\n</transcript>")
    parts = []
    for number, chunk in enumerate(chunks, 1):
        note = PART_NOTE.format(part=number, parts=len(chunks))
        part = _ask(editor, f"{rules}\n\n{note}", "<transcript>\n" + "\n".join(chunk) + "\n</transcript>")
        if part:
            parts.append(part)
    if not parts:
        return None
    combine = COMBINE_RULES.replace('{language}', language)
    return _ask(editor, combine, json.dumps(parts, ensure_ascii=False)) or parts[0]


# =============================================================================
# Markdown
# =============================================================================

def clock(seconds: float) -> str:
    seconds = int(seconds or 0)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60:02d}:{rest % 60:02d}"


def duration_label(seconds: float) -> str:
    minutes = max(1, round((seconds or 0) / 60))
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min" if minutes % 60 else f"{minutes // 60} h"


def speaker_names(meeting: dict) -> list:
    names = []
    for name in (meeting.get('speakers') or {}).values():
        if name and name not in names:
            names.append(name)
    return names


def markdown_text(meeting: dict) -> str:
    started = datetime.datetime.fromisoformat(meeting['started'])
    title = meeting.get('title') or 'Meeting'
    meta = [f"{started:%Y-%m-%d %H:%M}", duration_label(meeting.get('duration'))]
    names = speaker_names(meeting)
    if names:
        meta.append(', '.join(names))
    lines = [f"# {title}", '', ' · '.join(meta), '']

    summary = meeting.get('summary') or {}
    if summary.get('summary'):
        lines += ['## Summary', '', summary['summary'], '']
    for heading, key, marker in (('Key points', 'points', '- '), ('Decisions', 'decisions', '- '),
                                 ('Action items', 'actions', '- [ ] ')):
        if summary.get(key):
            lines += [f"## {heading}", ''] + [marker + item for item in summary[key]] + ['']

    speakers = meeting.get('speakers') or {}
    lines += ['## Transcript', '']
    for turn in meeting.get('turns') or ():
        name = speakers.get(turn['speaker']) or default_speaker_name(turn['speaker'])
        lines += [f"**{name}** · {clock(turn['start'])}", turn['text'], '']
    return '\n'.join(lines).rstrip() + '\n'


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Writes (or rewrites) the meeting's Markdown file. A file you have edited
# since it was written is left alone; returns False then.
def write_markdown(meeting: dict, folder: Path) -> bool:
    path = Path(meeting['markdown']) if meeting.get('markdown') else None
    if path and path.exists() and meeting.get('markdown_hash'):
        try:
            current = path.read_bytes()
            # Earlier Windows writes translated LF to CRLF after hashing.
            # Accept that exact generated text, while preserving actual edits.
            if (_digest(current) != meeting['markdown_hash'] and
                    _digest(current.replace(b'\r\n', b'\n')) != meeting['markdown_hash']):
                return False
        except OSError:
            return False
    if path is None:
        started = datetime.datetime.fromisoformat(meeting['started'])
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', meeting.get('title') or 'Meeting').strip(' .') or 'Meeting'
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"{started:%Y-%m-%d %H%M} {title}"
        path, copy = folder / f"{stem}.md", 2
        while path.exists():
            path, copy = folder / f"{stem} ({copy}).md", copy + 1
    text = markdown_text(meeting)
    data = text.encode('utf-8')
    temporary = path.with_suffix('.md.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)
    meeting['markdown'] = str(path)
    meeting['markdown_hash'] = _digest(data)
    return True


# =============================================================================
# Controller
# =============================================================================

class MeetingController:
    # What the app, the tray and the hub drive. Recording runs on its own
    # threads; processing runs one meeting at a time on a worker thread, and
    # waits while a game has the GPU.
    #   engine():        the WhisperEngine (late-bound: a model switch replaces it)
    #   editor:          the SmartEditor, for summaries
    #   game_active():   whether a game has focus
    #   mic_device():    the dictation mic's sounddevice id
    #   notes_folder():  where Markdown files go
    #   ai_enabled():    whether AI summaries are on
    #   sync_ai():       puts the AI model back the way dictation wants it
    #   on_change():     a meeting started, stopped or moved a step
    #   on_ready(m):     a meeting's notes were just written
    def __init__(self, store: MeetingStore, *, engine, editor, game_active, mic_device,
                 notes_folder, ai_enabled, sync_ai, on_change, on_ready):
        self.store = store
        self._engine = engine
        self._editor = editor
        self._game_active = game_active
        self._mic_device = mic_device
        self._notes_folder = notes_folder
        self._ai_enabled = ai_enabled
        self._sync_ai = sync_ai
        self._on_change = on_change
        self._on_ready = on_ready
        self._lock = threading.Lock()
        self._active = None
        self._recorder = None
        self._started_epoch = None
        self._jobs = queue.Queue()
        self._process_now = set()
        self._wake = threading.Event()
        self._last_notice = 0.0
        self._worker = None
        self._closing = False

    # Begins processing, including meetings an earlier run left unfinished.
    def start(self):
        if self._worker:
            return
        self._recover()
        self._worker = threading.Thread(target=self._work, name='meetings', daemon=True)
        self._worker.start()

    # App exit: a recording is closed cleanly and left queued, and nothing new
    # starts processing; the next start picks it up.
    def shutdown(self):
        self._closing = True
        self.stop_meeting()
        self._wake.set()
        self._jobs.put((None, None))

    # ---- recording ----------------------------------------------------------

    @property
    def active(self):
        return self._active

    # When the current meeting started, in epoch milliseconds (None when no
    # meeting is recording), so the pill can show a real elapsed time.
    def started_ms(self):
        return int(self._started_epoch * 1000) if self._active and self._started_epoch else None

    def start_meeting(self) -> dict:
        from .meeting_recorder import MeetingRecorder
        with self._lock:
            if self._active:
                return self._active
            meeting = self.store.create()
            recorder = MeetingRecorder(self.store.folder(meeting['id']), self._mic_device())
            try:
                recorder.start()
            except Exception as e:
                logger.error(f"Meeting recording failed to start: {e}")
                if self.store.has_audio(meeting['id']):
                    # A loopback startup failure can follow several seconds
                    # of successful mic capture. That audio belongs to the user.
                    meeting.update(status=FAILED, error=str(e), step='',
                                   capture_warnings=list(recorder.warnings))
                    self.store.save(meeting)
                    self._on_change()
                else:
                    self.store.delete(meeting['id'])
                raise RuntimeError(f"Meeting recording couldn't start: {e}") from e
            self._active, self._recorder, self._started_epoch = meeting, recorder, time.time()
        logger.info("Meeting recording started")
        self._on_change()
        return meeting

    def stop_meeting(self):
        with self._lock:
            meeting, recorder = self._active, self._recorder
            if not meeting:
                return None
            self._active = self._recorder = self._started_epoch = None
        try:
            duration = recorder.stop()
        except Exception as e:
            # Keep incomplete audio for recovery; never transcribe/delete a
            # recording whose writer hasn't confirmed its final flush.
            meeting.update(duration=round(recorder.elapsed, 1), status=FAILED,
                           step='', error=str(e), progress=None)
            meeting['capture_warnings'] = list(recorder.warnings)
            self.store.save(meeting)
            self._on_change()
            return meeting
        logger.info(f"Meeting recording stopped ({clock(duration)})")
        meeting['capture_warnings'] = list(recorder.warnings)
        meeting.update(duration=round(duration, 1), status=WAITING, step='Queued')
        self.store.save(meeting)
        self._jobs.put(('process', meeting['id']))
        self._on_change()
        return meeting

    # ---- hub actions --------------------------------------------------------

    def list(self) -> list:
        return [self._summary_row(meeting) for meeting in self.store.all()]

    def get(self, meeting_id: str) -> dict:
        meeting = self._require(meeting_id)
        if self._active and self._active['id'] == meeting_id and self._recorder:
            meeting['capture_warnings'] = self._recorder.warnings
        meeting['can_retry'] = meeting['status'] == FAILED and self.store.has_audio(meeting_id)
        meeting['markdown_exists'] = bool(meeting.get('markdown')) and Path(meeting['markdown']).exists()
        meeting['audio_tracks'] = self.audio_paths(meeting_id)
        meeting['audio_retained'] = bool(meeting['audio_tracks'])
        meeting.setdefault('source', 'unknown')
        for export in (meeting.get('transcript_exports') or {}).values():
            export['exists'] = Path(export['path']).is_file()
        return meeting

    # The renderer receives only named retained tracks within this meeting.
    def audio_paths(self, meeting_id: str) -> dict:
        self._require(meeting_id)
        folder = self.store.folder(meeting_id).resolve()
        tracks = {}
        for key, label in [('playback', 'Conversation'), ('mic', 'Microphone'), ('system', 'System audio')]:
            path = folder / f'{key}.wav'
            if not path.is_file() or path.resolve().parent != folder:
                continue
            tracks[key] = {'label': label, 'path': str(path)}
        return tracks

    def transcript(self, meeting_id: str, speaker: str = '') -> dict:
        return selected_transcript(self._require(meeting_id), speaker)

    # Always open a derived file in the configured transcript folder rather
    # than trusting a persisted arbitrary path supplied by a renderer.
    def open_transcript(self, meeting_id: str, speaker: str = '') -> dict:
        from .utils import open_file
        meeting = self._require(meeting_id)
        selected_transcript(meeting, speaker)
        if not meeting.get('turns'):
            raise ValueError('This meeting has no transcript')
        write_transcripts(meeting, self._notes_folder())
        self.store.save(meeting)
        key = speaker or 'conversation'
        path = meeting['transcript_exports'][key]['path']
        open_file(path)
        return {'path': path, 'preserved': meeting['transcript_exports'][key]['preserved']}

    # Renames a speaker everywhere in the meeting. Giving two speakers the
    # same name merges them in the notes. '' restores the default name.
    def rename_speaker(self, meeting_id: str, key: str, name: str) -> dict:
        meeting = self._require(meeting_id)
        if meeting['status'] != READY or key not in (meeting.get('speakers') or {}):
            raise ValueError("That speaker can't be renamed right now")
        name = re.sub(r'\s+', ' ', str(name or '')).strip()[:40] or default_speaker_name(key)
        meeting['speakers'][key] = name
        written = True
        if meeting.get('markdown'):
            written = write_markdown(meeting, self._notes_folder())
        transcripts_written = write_transcripts(meeting, self._notes_folder())
        self.store.save(meeting)
        self._on_change()
        return {'meeting': self.get(meeting_id), 'markdown_kept': not written,
                'transcript_kept': not transcripts_written}

    def summarize_again(self, meeting_id: str) -> dict:
        meeting = self._require(meeting_id)
        if meeting['status'] != READY or not meeting.get('turns'):
            raise ValueError("This meeting has no transcript to summarise")
        if not self._ai_enabled():
            raise ValueError("Turn on AI edits in Settings to summarise meetings")
        meeting.update(status=WAITING, step='Queued', progress=None)
        self.store.save(meeting)
        self._jobs.put(('summarize', meeting_id))
        self._on_change()
        return self.get(meeting_id)

    def retry(self, meeting_id: str) -> dict:
        meeting = self._require(meeting_id)
        if meeting['status'] != FAILED or not self.store.has_audio(meeting_id):
            raise ValueError("This meeting can't be processed again")
        meeting.update(status=WAITING, step='Queued', error='', attempts=0)
        self.store.save(meeting)
        self._jobs.put(('process', meeting_id))
        self._on_change()
        return self.get(meeting_id)

    # Skips the wait for a game to close.
    def process_now(self, meeting_id: str) -> dict:
        self._require(meeting_id)
        self._process_now.add(meeting_id)
        self._wake.set()
        return self.get(meeting_id)

    def delete(self, meeting_id: str) -> dict:
        meeting = self._require(meeting_id)
        if meeting['status'] in (RECORDING, PROCESSING) or (self._active and self._active['id'] == meeting_id):
            raise ValueError("A meeting can't be deleted while it is recording or being processed")
        self.store.delete(meeting_id)
        self._on_change()
        return {}

    def open_markdown(self, meeting_id: str) -> dict:
        from .utils import open_file
        meeting = self._require(meeting_id)
        path = Path(meeting.get('markdown') or '')
        if not meeting.get('markdown') or not path.exists():
            raise ValueError("The Markdown file is gone (moved or deleted)")
        open_file(str(path))
        return {}

    def _require(self, meeting_id: str) -> dict:
        meeting = self.store.load(meeting_id)
        if not meeting:
            raise ValueError("That meeting no longer exists")
        return meeting

    @staticmethod
    def _summary_row(meeting: dict) -> dict:
        return {key: meeting.get(key) for key in
                ('id', 'started', 'duration', 'status', 'step', 'progress', 'error', 'title')} | {
            'speakers': speaker_names(meeting),
            'preview': (meeting.get('summary') or {}).get('summary')
                       or next((turn['text'] for turn in meeting.get('turns') or ()), ''),
        }

    # ---- processing ---------------------------------------------------------

    # Meetings left recording or processing by a crash are queued again. One
    # that already failed mid-processing twice is marked failed instead, so a
    # meeting that crashes the app can't do it on every start.
    def _recover(self):
        for meeting in self.store.all():
            if meeting['status'] not in (RECORDING, WAITING, PROCESSING):
                continue
            if not self.store.has_audio(meeting['id']):
                if meeting.get('turns'):
                    meeting.update(status=READY, step='', progress=None)
                else:
                    meeting.update(status=FAILED, error="The recording was interrupted", step='')
            elif meeting.get('attempts', 0) >= 2:
                meeting.update(status=FAILED, error="Processing stopped unexpectedly", step='')
            else:
                if meeting['status'] == RECORDING:
                    durations = []
                    for name in ('mic.wav', 'system.wav'):
                        try:
                            with wave.open(str(self.store.folder(meeting['id']) / name), 'rb') as track:
                                durations.append(track.getnframes() / track.getframerate())
                        except (OSError, EOFError, wave.Error):
                            pass
                    meeting['duration'] = round(max(durations, default=0), 1)
                    meeting.setdefault('capture_warnings', []).append(
                        'Recording was interrupted; the available audio was recovered and may have gaps.')
                meeting.update(status=WAITING, step='Queued')
                self._jobs.put(('process', meeting['id']))
            self.store.save(meeting)

    def _work(self):
        while True:
            kind, meeting_id = self._jobs.get()
            if self._closing:
                return
            meeting = self.store.load(meeting_id)
            if not meeting:
                continue
            try:
                if kind == 'process':
                    self._process(meeting)
                else:
                    self._summarize(meeting)
            except _ProcessingCancelled:
                continue
            except Exception as e:
                logger.error(f"Meeting {meeting_id} failed: {e}", exc_info=True)
                meeting.update(status=FAILED, step='', progress=None,
                               error=str(e) or e.__class__.__name__)
                try:
                    self.store.save(meeting)
                except OSError as save_error:
                    logger.error(f"Meeting {meeting_id} could not be saved: {save_error}")
            self._on_change()

    def _process(self, meeting: dict):
        meeting_id = meeting['id']
        forced = self._wait_for_game(meeting)
        if forced is None:
            return
        meeting['attempts'] = meeting.get('attempts', 0) + 1
        self._step(meeting, 'Finding who spoke', 0.0)

        folder = self.store.folder(meeting_id)
        mic, system = read_track(folder / 'mic.wav'), read_track(folder / 'system.wav')
        if mic is None and system is None:
            raise RuntimeError("The meeting's audio is missing")
        if mic is None:
            mic = np.zeros_like(system)
        if system is None:
            system = np.zeros_like(mic)

        started = time.time()
        # A game may start after this job began. Recheck between CPU/GPU
        # stages and transcription batches; Process now applies to this job.
        def before_stage():
            nonlocal forced
            if self._closing or self.store.load(meeting_id) is None:
                raise _ProcessingCancelled()
            if not forced:
                permission = self._wait_for_game(meeting)
                if permission is None:
                    raise _ProcessingCancelled()
                forced = permission

        result = build_transcript(mic, system, self._engine(),
                                  on_step=lambda label, share: self._step(meeting, label, share),
                                  before_stage=before_stage)
        meeting.update(result)
        # Save canonical words before any export, playback or optional model
        # work, so interruption can never discard completed transcription.
        self.store.save(meeting)
        write_transcripts(meeting, self._notes_folder())
        self.store.save(meeting)
        del mic, system
        try:
            write_playback(folder)
        except Exception as e:
            logger.warning(f'Meeting playback mix failed; original tracks retained: {e}')
            meeting.setdefault('capture_warnings', []).append(f'Combined playback unavailable: {e}')
        logger.info(f"Meeting {meeting_id}: {len(result['turns'])} turns, "
                    f"{len(result['speakers'])} speaker(s) in {time.time() - started:.0f}s")

        meeting['error'] = ''
        if meeting['turns']:
            before_stage()
            self._write_summary(meeting, forced)
            write_markdown(meeting, self._notes_folder())
        meeting.update(status=READY, step='', progress=None)
        self.store.save(meeting)
        if meeting['turns']:
            self._on_ready(meeting)

    def _summarize(self, meeting: dict):
        forced = self._wait_for_game(meeting)
        if forced is None:
            return
        meeting['error'] = ''
        self._write_summary(meeting, forced)
        write_transcripts(meeting, self._notes_folder())
        if meeting.get('markdown'):
            write_markdown(meeting, self._notes_folder())
        meeting.update(status=READY, step='', progress=None)
        self.store.save(meeting)

    # Adds the AI summary and title. When it can't, the meeting still
    # finishes, with a note in `error` the hub shows next to "Summarise".
    def _write_summary(self, meeting: dict, forced: bool = False):
        if not self._ai_enabled():
            meeting['error'] = "No summary: AI edits are off in Settings"
            return
        if self._game_active() and not forced:
            meeting['error'] = "No summary: a game was running"
            return
        self._step(meeting, 'Writing the summary', None)
        try:
            try:
                summary = summarize(meeting, self._editor)
            finally:
                # Restore dictation's model setup even when summary fails.
                self._sync_ai()
        except Exception as e:
            logger.warning(f'Meeting summary unavailable: {e}')
            meeting['error'] = f'No summary: {e}'
            return
        if not summary:
            meeting['error'] = "No summary: the AI model didn't answer (is Ollama running?)"
            return
        meeting['summary'] = summary
        if summary.get('title'):
            meeting['title'] = summary['title']

    # A game has the GPU and most of the CPU; transcribing an hour of audio
    # now would cost it frames. Waits until it closes, unless "Process now"
    # was pressed; returns True then, so the caller knows it was asked to run
    # during a game.
    def _wait_for_game(self, meeting: dict) -> bool | None:
        # None cancels a stale job; False is an ordinary non-forced run.
        if self._closing or self.store.load(meeting['id']) is None:
            return None
        while self._game_active() and meeting['id'] not in self._process_now:
            if self._closing or self.store.load(meeting['id']) is None:
                return None
            if meeting['status'] != WAITING or meeting['step'] != 'Waiting for your game to close':
                meeting.update(status=WAITING, step='Waiting for your game to close', progress=None)
                self.store.save(meeting)
                self._on_change()
            self._wake.wait(5)
            self._wake.clear()
        if self._closing or self.store.load(meeting['id']) is None:
            return None
        forced = meeting['id'] in self._process_now
        self._process_now.discard(meeting['id'])
        return forced

    # Game mode ended: a waiting meeting starts now rather than at its next check.
    def wake(self):
        self._wake.set()

    # Progress for the hub, saved and announced at most a few times a second.
    def _step(self, meeting: dict, label: str, share):
        changed = meeting.get('step') != label or meeting.get('status') != PROCESSING
        meeting.update(status=PROCESSING, step=label,
                       progress=None if share is None else round(float(share), 2))
        now = time.monotonic()
        if changed or now - self._last_notice > 0.5:
            self._last_notice = now
            self.store.save(meeting)
            self._on_change()
