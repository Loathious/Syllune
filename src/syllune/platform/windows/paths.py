# platform/windows/paths.py
# Where Syllune keeps things on Windows: your data in %APPDATA%\Syllune, the
# window's browser cache in %LOCALAPPDATA%\Syllune.
# macOS mirror: platform/macos/paths.py.
import os
from pathlib import Path

def get_app_data_path():
    return Path(os.getenv('APPDATA')) / 'Syllune'

def get_cache_path():
    return Path(os.getenv('LOCALAPPDATA') or os.getenv('APPDATA')) / 'Syllune'

def open_file(path):
    os.startfile(path)
