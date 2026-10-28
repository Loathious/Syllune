# Syllune

Local dictation and private meeting notes for Windows.

Hold a shortcut, speak, let go: your words are typed where the cursor is. Speech recognition runs on your own computer with [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Nothing is uploaded and no account is needed.

## What it does

- **Dictation anywhere.** Hold Ctrl+Win to talk, or double-tap it for hands-free. Short clips take a faster path, so adding a few words feels instant.
- **Writing styles.** Formal, casual or verbatim, chosen globally or per app. In AI chat apps your dictation is formatted as a clean prompt.
- **Optional local AI editing.** If [Ollama](https://ollama.com) is installed, Syllune tidies punctuation and phrasing with a local model. If it isn't, plain dictation works the same.
- **Meetings and notes.** Record a meeting or a voice note and get a transcript you can replay alongside the audio.
- **History with replay.** Every dictation is kept on your device. Search it, copy it, fix it, or listen to the recording again.
- **Stays out of the way.** A small pill snaps to the bottom center or either side of the screen. Music pauses while you talk and resumes after. Game mode hides everything while you play.
- **Swedish and English.** Pick the languages you speak; Swedish can use the KB-Whisper models.

## Install

Download `Syllune-Setup-<version>-x64.exe` from [Releases](https://github.com/Loathious/Syllune/releases) and run it. It installs for your user only and needs no Python, Node or Git. The first-run guide helps you pick a microphone, languages and a speech model.

The installer is not code-signed yet, so Windows SmartScreen warns first. Compare the download with its `.sha256` file to check it arrived intact.

More: [install and update](docs/syllune-install.md) · [troubleshooting](docs/syllune-troubleshooting.md) · [privacy](docs/syllune-privacy.md) · [building a release](docs/syllune-release.md)

## Run from source

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
cd overlay; npm ci; cd ..
.venv\Scripts\python.exe start-syllune.py
```

Tests: `python -m unittest discover -s tests -t .` (with `PYTHONPATH=src`) and `npm test` in `overlay/`.

## License

Syllune is proprietary software by Loathious, free to use for now. The source is public so you can see what it does, but it may not be copied, changed or redistributed; see [LICENSE](LICENSE). Open-source code and components Syllune uses are listed with their notices in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
