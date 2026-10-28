# Troubleshooting Syllune

## Nothing gets typed

- **Check the pill.** It should move while you hold Ctrl+Win and talk. If it stays still, pick your microphone in Settings → Microphone, and make sure Windows lets desktop apps use it: Settings → Privacy & security → Microphone → Let desktop apps access your microphone.
- **The app runs as administrator.** Windows doesn't let a normal program type into a window that runs as administrator (Task Manager, some installers and launchers). Your text waits in History; copy it from there.
- **No text field had focus.** The dictation waits in History. Click into a text field and it is typed there.

## The AI edit doesn't happen

Balanced and Best use a local [Ollama](https://ollama.com) model. If Ollama isn't running, Settings says so and Syllune types your words without the edit. Fast never uses it.

## Windows or your antivirus warns about the installer

Syllune isn't code-signed yet (certificates cost money), so SmartScreen shows "Windows protected your PC" the first time. Choose More info → Run anyway. Before that, you can check the download is intact: compare `Get-FileHash .\Syllune-Setup-<version>-x64.exe` with the `.sha256` file from the release.

An antivirus may also be wary of an unsigned app that listens for a shortcut and types text, because that's what keyloggers do too. Syllune only watches its own shortcut, and only types what you dictated. If your antivirus quarantines it, restore it and report it to the antivirus vendor as a false positive.

## The tray icon is missing

Windows hides new tray icons. Click the `^` next to the clock and drag the Syllune icon onto the taskbar.

## Sending a bug report

Right-click the tray icon → Help → Save logs for a bug report. It saves a zip with personal paths removed; look it over, then attach it to a [bug report](https://github.com/Loathious/Syllune/issues/new?template=bug_report.yml).

## Starting over

Exit Syllune and delete `%APPDATA%\Syllune\user_settings.yaml` to return every setting to its default. Delete the whole `%APPDATA%\Syllune` folder to also clear history, notes and statistics.
