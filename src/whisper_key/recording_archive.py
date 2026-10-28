# Retain local dictation audio independently of text-history age settings.
# Archive handles are UUIDs, never caller-supplied paths; WAVs become visible
# only after a complete atomic write. Audio expires only by explicit deletion.
import os
import re
import tempfile
import uuid
import wave
from pathlib import Path

import numpy as np

from .utils import get_user_app_data_path

_RECORDING_ID = re.compile(r'[0-9a-f]{32}\Z')
_SAMPLE_RATE = 16000


# Explicit roots refer to the archive itself, allowing isolated tests/exports.
def _archive_root(root=None) -> Path:
    return Path(root) if root is not None else Path(get_user_app_data_path()) / 'dictation_audio'


def valid_recording_id(recording_id) -> bool:
    return isinstance(recording_id, str) and _RECORDING_ID.fullmatch(recording_id) is not None


# Preserve captured mono samples in a widely playable format without a codec dependency.
def save_recording(audio: np.ndarray, root: Path | None = None) -> str:
    samples = np.asarray(audio, dtype=np.float32)
    if samples.ndim == 2 and samples.shape[1] == 1:
        samples = samples[:, 0]
    if samples.ndim != 1 or not samples.size:
        raise ValueError('Dictation audio must contain mono samples')
    samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
    pcm = np.clip(np.clip(samples, -1.0, 1.0) * 32768.0, -32768, 32767).astype('<i2')
    folder = _archive_root(root)
    folder.mkdir(parents=True, exist_ok=True)
    recording_id = uuid.uuid4().hex
    destination = folder / f'{recording_id}.wav'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix='dictation-', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            with wave.open(output, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(_SAMPLE_RATE)
                wav.writeframes(pcm.tobytes())
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(destination)
        return recording_id
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


# Reject link escapes as well as path traversal before exposing audio to IPC.
def recording_path(recording_id, root: Path | None = None) -> Path | None:
    if not valid_recording_id(recording_id):
        return None
    folder = _archive_root(root)
    candidate = folder / f'{recording_id}.wav'
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        if candidate.resolve().parent != folder.resolve():
            return None
        return candidate
    except OSError:
        return None


def delete_recording(recording_id, root: Path | None = None) -> bool:
    path = recording_path(recording_id, root=root)
    if path is None:
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False
