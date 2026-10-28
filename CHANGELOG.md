# Changelog

All notable changes to Syllune. Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.24.2] - 2026-10-28

### Fixed
- Dictation runs on the GPU again in the installed app: the Windows build now includes the NVIDIA CUDA libraries the speech engine needs, instead of silently falling back to the much slower CPU.
- Punctuation: Whisper is primed with a punctuated sentence in the language you speak (English or Swedish), so short messages come back as full sentences instead of bare words.
- The AI edit no longer goes cold. Ollama keeps the model loaded (game mode still unloads it during games), and starting a recording warms it, so the first dictation after a break is not typed unpunctuated after an 8-second wait.

### Changed
- Quality now changes every dictation. Fast: quickest, never rewritten. Balanced: short messages typed at once, longer ones get the AI edit. Best: beam 5, full decoding and the AI edit on every dictation. Short messages also use the chosen beam width.
- New **Short messages** setting (Off, 3, 5 or 8 s) decides how long a recording may be and still take the instant path.

## [0.24.1] - 2026-10-28

### Fixed
- The Windows app keeps a visible startup window until the engine connects. Startup failures show Retry and Open logs instead of silently closing.
- Opening the desktop or Start menu shortcut again brings the existing app forward without starting another engine.
- Saved GPU settings fall back to CPU for the session if the optional CUDA runtime is missing. Windowless launches never wait for console answers.
- Speech models and optional streaming prepare after the GUI starts. Per-language speech models remain available, and an unavailable microphone no longer prevents setup from opening.
- Setup navigation survives slow initial requests, including an existing installation with missing model files.

### Packaging
- Version 0.24.1 in both the Windows app and speech engine.
- Added explicit trusted-certificate signing and verification for releases, plus local Microsoft Store packaging preparation. Public Windows trust still requires a verified publisher or certified Store distribution.

## [0.24.0] - 2026-10-28

The first Syllune release.

### Added
- **Dictation anywhere.** Hold Ctrl+Win and talk, or double-tap for hands-free. Text goes where your cursor is; if there's nowhere to type, it waits in History.
- **The pill.** A small floating pill that snaps to the bottom centre or either side of the screen. Hover it for hands-free dictation, a new meeting and the menu; drag it and it becomes a ball. The waveform stands upright on the sides, and a quiet "Not hearing anything" shows after five seconds of silence.
- **Quality.** One setting: Fast skips the AI edit, Balanced and Best tidy punctuation and phrasing with a local Ollama model.
- **History with replay.** Search, copy, fix or listen to past dictations, all kept on your PC.
- **Meetings and notes** with a transcript you can replay alongside the audio.
- **Writing styles**, globally or per app. AI chat apps get your words as a clean prompt.
- **Music pauses** while you talk and resumes after.
- **Windows installer** for your user only, with no Python, Node or Git needed.
- **Starts with Windows** by default (turn it off in Settings).
- **Knows the word "Syllune"** without adding it to your dictionary.

### Changed
- Settings are down to nine, in three groups: Dictation, General and Privacy. The tray menu keeps only everyday actions; support tools are under Help.
- Your data lives in `%APPDATA%\Syllune`.
- Changing quality is instant and never stops dictation.
- The app only asks Windows for the narrowest process rights, and its files carry full publisher and version details.
- Syllune is proprietary software, free to use for now. See LICENSE and THIRD_PARTY_NOTICES.md.
