# Syllune privacy

Speech recognition runs on this device. Optional text editing uses a local Ollama server. Syllune has no built-in account, telemetry service, or automatic transcript upload.

## What is stored

To preserve existing installations, Windows data remains under `%APPDATA%\whisperkey`; the internal Python package also keeps its established name. The visible app is Syllune.

Settings, dictionary entries, corrections, local statistics, dictation history, and saved notes can contain personal information. If Save dictation audio is enabled, recordings are retained with history. Meeting recordings retain their audio until deleted. Review these choices in the setup guide and Settings. The microphone level test does not retain a recording.

Delete unwanted entries in the app. Back up useful notes before uninstalling. Uninstalling application files does not silently delete your recordings or settings.

## Network access

- Selected speech models are downloaded from their model repository, then used locally.
- Optional daily checks contact the configured Syllune GitHub repository for release metadata.
- Local AI editing contacts the configured local Ollama endpoint. Advanced source settings can change that endpoint; choose deliberately before sending text to another host.
- Commands you explicitly run can have their own effects and network access.

These requests can reveal ordinary connection metadata such as your IP address to the service. Dictation audio is not uploaded for recognition. Application logs may contain diagnostic information; inspect any support bundle before sharing it.

## Publishing source safely

The public repository excludes personal settings, recordings, histories, local assistant files, local build output, private verification screenshots, and development logs. New public history uses a public nickname/noreply identity and preserves the original MIT notice. Release preparation includes a source audit and a separate review of the outgoing file list. Automated scans help find mistakes but do not replace review.
