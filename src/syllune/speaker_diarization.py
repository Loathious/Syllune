# speaker_diarization.py
# Who spoke when, for meeting transcripts: sherpa-onnx's offline diarization
# (pyannote segmentation, then 3D-Speaker voice embeddings clustered into
# speakers). It runs on the CPU so it never takes VRAM from Whisper or a game.
# The two models (~35 MB) download from Hugging Face on first use.

import logging
import os
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

SEGMENTATION_MODEL = ('csukuangfj/sherpa-onnx-pyannote-segmentation-3-0', 'model.onnx')
# TitaNet-small: in a comparison of six embedding models it was the one that
# kept two similar voices apart at every threshold, where CAM++ split one
# voice into three speakers. It is also the fastest of them.
EMBEDDING_MODEL = ('csukuangfj/speaker-embedding-models', 'nemo_en_titanet_small.onnx')

# How far apart two voices must be to count as two speakers. Higher merges
# more eagerly. Erring towards a split is the cheaper mistake: renaming two
# speakers to the same name joins them again.
CLUSTER_THRESHOLD = 0.6
# Speech shorter than this is not given a speaker of its own (a cough, "mm").
MIN_SPEECH_S = 0.3
# Pauses shorter than this inside one speaker's turn are bridged.
MIN_PAUSE_S = 0.5


def available() -> bool:
    import importlib.util
    return importlib.util.find_spec('sherpa_onnx') is not None


# Path to a model file, downloading it into the Hugging Face cache the first
# time (the same cache the Whisper models live in). Later runs never touch
# the network.
def _model_file(repo_and_name) -> str:
    os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING', '1')
    from huggingface_hub import hf_hub_download
    repo, filename = repo_and_name
    try:
        return hf_hub_download(repo_id=repo, filename=filename, local_files_only=True)
    except Exception:
        logger.info(f"Downloading speaker model {filename}")
        return hf_hub_download(repo_id=repo, filename=filename)


def _threads() -> int:
    # Half the cores: fast enough to diarize an hour in a minute or two
    # without making the rest of the PC stutter.
    return max(1, min(4, (os.cpu_count() or 4) // 2))


def _build(num_speakers: int):
    import sherpa_onnx
    threads = _threads()
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=_model_file(SEGMENTATION_MODEL)),
            num_threads=threads),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=_model_file(EMBEDDING_MODEL), num_threads=threads),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=num_speakers, threshold=CLUSTER_THRESHOLD),
        min_duration_on=MIN_SPEECH_S,
        min_duration_off=MIN_PAUSE_S,
    )
    if not config.validate():
        raise RuntimeError("Speaker detection models are incomplete")
    return sherpa_onnx.OfflineSpeakerDiarization(config)


# Mono float32 at 16 kHz in; [(start_s, end_s, speaker_index)] out, sorted by
# time, with speakers numbered in the order they first speak. `num_speakers`
# fixes the count when known (-1 = work it out). `on_progress(0..1)` is called
# as chunks finish.
def diarize(audio: np.ndarray, num_speakers: int = -1,
            on_progress: Optional[Callable[[float], None]] = None) -> list:
    if audio is None or len(audio) < 16000:
        return []
    diarizer = _build(num_speakers)

    def report(done, total):
        if on_progress and total:
            on_progress(done / total)
        return 0

    result = diarizer.process(np.ascontiguousarray(audio, dtype=np.float32),
                              callback=report).sort_by_start_time()
    order = {}
    segments = []
    for seg in result:
        index = order.setdefault(seg.speaker, len(order))
        segments.append((float(seg.start), float(seg.end), index))
    logger.info(f"Diarization: {len(order)} speaker(s) in {len(segments)} segments")
    return segments
