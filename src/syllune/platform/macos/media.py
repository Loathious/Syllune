# platform/macos/media.py
# No-op stub of the Windows media session control: macOS has no public API
# for pausing other apps' playback, so music keeps playing while recording.


def pause_playing() -> list:
    return []


def resume(app_ids: list) -> list:
    return []
