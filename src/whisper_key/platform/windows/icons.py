# platform/windows/icons.py
# Loads the tray icon images for each app state (idle/recording/processing).
# macOS mirror: platform/macos/icons.py.
from pathlib import Path
from PIL import Image

from ...utils import resolve_asset_path

ASSETS_DIR = Path(resolve_asset_path("assets/brand"))

def get_tray_icons() -> dict:
    return {
        "idle": Image.open(ASSETS_DIR / "idle.png"),
        "recording": Image.open(ASSETS_DIR / "recording.png"),
        "processing": Image.open(ASSETS_DIR / "processing.png"),
    }
