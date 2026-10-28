<p align="center"><img src="docs/assets/banner.png" alt="Syllune" width="100%"></p>

<p align="center">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%2010%2B-243B5A">
  <img alt="Runs locally" src="https://img.shields.io/badge/runs-100%25%20local-243B5A">
</p>

Hold a shortcut, speak, release. Syllune types what you said wherever your cursor is. Speech recognition runs on your own computer with [faster-whisper](https://github.com/SYSTRAN/faster-whisper), so nothing is uploaded and you don't need an account.

## Features

- **Dictation anywhere.** Hold Ctrl+Win, or double-tap it to dictate hands-free.
- **Fast on short messages, careful on long ones.** Uses your NVIDIA GPU when it has one, and you can pick the speed/quality balance in Settings.
- **Optional local AI cleanup.** If [Ollama](https://ollama.com) is installed, Syllune fixes punctuation and phrasing on your own machine.
- **Meeting notes and history.** Saves transcripts you can search, edit and replay.
- **English and Swedish.**

## Install

Download the latest `Syllune-Setup-<version>-x64.exe` from [Releases](https://github.com/Loathious/Syllune/releases/latest) and run it. No admin rights or developer tools are needed. The installer isn't code-signed yet, so Windows SmartScreen will show a warning first.

[Install and update](docs/syllune-install.md) · [Troubleshooting](docs/syllune-troubleshooting.md) · [Privacy](docs/syllune-privacy.md)

## Development

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
cd overlay; npm ci; cd ..
.venv\Scripts\python.exe start-syllune.py
```

## License

Proprietary, by Loathious. The source is public so you can see what it does, but it may not be copied or redistributed. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
