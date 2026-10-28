# platform/macos/paths.py
# Where Syllune keeps things on macOS: your data in Application Support, the
# window's browser cache in Caches, and the folder versions before the
# rename used. Windows mirror: platform/windows/paths.py.
import subprocess
from pathlib import Path

def get_app_data_path():
    return Path.home() / 'Library' / 'Application Support' / 'Syllune'

def get_cache_path():
    return Path.home() / 'Library' / 'Caches' / 'Syllune'

def open_file(path):
    subprocess.run(['open', str(path)])
