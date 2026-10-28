# notes.py
# Voice notes: each note is one plain markdown file in a folder the user owns,
# so notes stay greppable and open in any editor (or Obsidian). The overlay's
# notes window reads the same folder directly; nothing else is stored.

import datetime
import os
import re
from pathlib import Path

TITLE_WORDS = 6


# The configured notes folder, or ~\Voice Notes by default. Not Documents:
# Defender's Controlled Folder Access blocks python.exe from writing there.
# Created on first use so the notes window never points at a missing folder.
def notes_folder(configured: str = None) -> Path:
    folder = Path(os.path.expandvars(os.path.expanduser(configured))) if configured \
        else Path.home() / 'Voice Notes'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# Files are named "<date time> <first words>.md" so they sort by time and are
# recognisable in Explorer. The title line is the first few words.
def save_note(text: str, folder: Path) -> Path:
    now = datetime.datetime.now()
    words = re.sub(r'[^\w\s-]', '', text.split('\n', 1)[0]).split()[:TITLE_WORDS]
    title = ' '.join(words) or 'Note'
    path = folder / f"{now:%Y-%m-%d %H%M%S} {title}.md"
    path.write_text(f"# {title}\n\n{text.strip()}\n", encoding='utf-8')
    return path
