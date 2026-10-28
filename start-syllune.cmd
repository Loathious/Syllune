@echo off
REM Launches Syllune from this source checkout.
REM Double-click this file, or pin its shortcut to taskbar/start menu.
cd /d "%~dp0"
REM Prefer this checkout's pinned environment when launched outside a terminal.
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0start-syllune.py"
) else (
    python "%~dp0start-syllune.py"
)
