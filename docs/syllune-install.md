# Install and update Syllune

Syllune's Windows installer contains the desktop interface, a private Python runtime, and the dictation engine. You do not need to install Python, Node, Git, or an AI account to use it. The installer is per-user and lets you choose its location and shortcuts.

## First launch

Open Syllune from Start. The guide helps you choose a microphone, languages, a local speech model, and a writing style. It also explains hold-to-talk, hands-free recording, snap docks, and your history settings.

Base is the smallest recommended model, about 150 MB. Small is about 500 MB. Turbo is an optional larger download, about 1.7 GB, and benefits from a working GPU. Model downloads need temporary disk space as well as space for the finished files. Existing model caches are reused. Basic dictation does not require Ollama; local AI editing is optional.

Choose Download in the guide to install the selected model. If the connection fails, retry from the same step. A model must be ready before finishing setup. A microphone test is opt-in and shows a level meter without saving the test audio.

## Use it

Hold the recording shortcut shown in the guide, speak, then release. The default shortcut is Ctrl+Win on Windows. Double-tap to use hands-free recording; stop or cancel from the pill. The guide displays the actual configured shortcut if it has been changed.

The pill rests as a thin line at the edge of the screen and opens when the cursor comes near. Drag it from anywhere to reveal three docks; the highlighted preview shows where it will land on release. Hover an icon to read its action. Click the hub icon to open history, notes, meetings, styles, and settings.

Every dictation is also copied to the clipboard. If there is no writable text field, it waits in History and the pill asks "Paste dictation?" the next time you click a writable field; nothing is inserted unless you accept. Password fields are excluded. Paste with Ctrl+V if an application's accessibility support prevents automatic insertion.

Choose a writing style during setup or in Styles. Per-app rules can override the default. Formal uses full sentences; Casual is lighter; Verbatim avoids style cleanup. In AI chat apps the pill offers a structured-prompt version after you dictate; it uses the optional local AI editor. The app never asks that editor to answer your dictation.

## Updates

Syllune follows this repository's Releases. Settings has Check now and View releases. Daily checks are optional. Download the matching Syllune installer and run it when no dictation or meeting is active. It replaces application files while retaining your existing data. This version does not silently install updates in the background.

Only install releases from the Syllune repository you obtained the app from. A development build without a configured repository says so instead of directing you to a different product. Unsigned builds may show a Windows publisher warning; the project does not claim signing until a release is actually signed.

## Privacy and troubleshooting

Audio, transcripts, settings, and statistics stay on the device. Model downloads and optional release checks require internet access. See [privacy](syllune-privacy.md) for storage and retention details.

If dictation fails, check the microphone meter, selected input, model status, and Windows microphone permissions. If AI editing is unavailable, plain dictation still works. A failed quality/model switch keeps the previous usable setting. Retry last dictation is available from the tray.

## Build from source (Windows)

Use Python 3.12 x64 and Node 22 or later. Build dependencies stay in this checkout:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r packaging\windows\requirements-runtime.txt -r packaging\windows\requirements-build.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
cd overlay
npm ci
cd ..
.\.venv\Scripts\python.exe -m syllune.main
```

Build the complete installer:

```powershell
powershell -File packaging\windows\build.ps1
```

The script creates `dist/syllune/Syllune-Setup-<version>-x64.exe` and its SHA-256 checksum. It never publishes automatically. GPU libraries are optional and are not part of the minimal CPU installer. Source users who want GPU speed can install the CUDA 12 libraries into this checkout's `.venv`.
