# Canonical meeting turns become a chronological conversation and one file
# per stable speaker ID. Hashes protect handwritten edits; atomic byte writes
# keep exports valid across interruption and consistent across platforms.
import hashlib
import re
from pathlib import Path


def _clock(seconds):
    seconds = max(0, int(seconds or 0))
    hours, rest = divmod(seconds, 3600)
    return f'{hours}:{rest // 60:02d}:{rest % 60:02d}' if hours else f'{rest // 60:02d}:{rest % 60:02d}'


# Stable IDs, rather than display names, keep renamed speakers in their files.
def selected_transcript(meeting: dict, speaker: str = '') -> dict:
    speaker = str(speaker or '')
    speakers = meeting.get('speakers') or {}
    if speaker and (not re.fullmatch(r'you|s\d+', speaker) or speaker not in speakers):
        raise ValueError('Unknown meeting speaker')
    turns = sorted((turn for turn in meeting.get('turns') or []
                    if not speaker or turn['speaker'] == speaker), key=lambda turn: turn['start'])
    lines = []
    for turn in turns:
        name = speakers.get(turn['speaker']) or turn['speaker']
        lines.extend([f"[{_clock(turn['start'])}] {name}", turn['text'], ''])
    return {'text': '\n'.join(lines).strip(), 'turns': turns, 'speaker': speaker}


def _digest(data):
    return hashlib.sha256(data).hexdigest()


# An existing file with no known generated hash belongs to the user too.
def write_transcripts(meeting: dict, notes_folder: Path) -> bool:
    folder = Path(notes_folder) / 'Transcripts' / meeting['id']
    folder.mkdir(parents=True, exist_ok=True)
    old = meeting.get('transcript_exports') or {}
    exports, all_written = {}, True
    for key in ['conversation', *(meeting.get('speakers') or {})]:
        if key != 'conversation' and not re.fullmatch(r'you|s\d+', key):
            continue
        path = folder / ('conversation.md' if key == 'conversation' else f'speaker-{key}.md')
        previous = old.get(key) or {}
        preserve = False
        if path.exists():
            current = path.read_bytes()
            expected = previous.get('hash')
            preserve = not expected or (_digest(current) != expected and
                                        _digest(current.replace(b'\r\n', b'\n')) != expected)
        if preserve:
            exports[key] = {'path': str(path), 'hash': previous.get('hash', ''), 'preserved': True}
            all_written = False
            continue
        selection = selected_transcript(meeting, '' if key == 'conversation' else key)
        title = meeting.get('title') or 'Meeting'
        heading = 'Conversation' if key == 'conversation' else meeting['speakers'][key]
        data = (f'# {title} — {heading}\n\n' + selection['text'] + '\n').encode('utf-8')
        temporary = path.with_suffix('.md.tmp')
        temporary.write_bytes(data)
        temporary.replace(path)
        exports[key] = {'path': str(path), 'hash': _digest(data), 'preserved': False}
    meeting['transcript_exports'] = exports
    return all_written
