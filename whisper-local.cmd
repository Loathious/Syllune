@echo off
REM Launches Whisper Local from the editable install.
REM Double-click this file, or pin its shortcut to taskbar/start menu.
cd /d "%~dp0"
REM Prefer this checkout's pinned environment when launched outside a terminal.
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0whisper-local.py"
) else (
    python "%~dp0whisper-local.py"
)
