# meeting_recorder.py
# Records a meeting as two time-aligned tracks: your microphone, and whatever
# this PC plays (the other side of a call, captured by WASAPI loopback). Both
# stream straight into 16 kHz mono WAV files, so an hour-long meeting never
# sits in RAM and a crash still leaves the audio on disk.

import ctypes
import logging
import queue
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
import soxr

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
LOOPBACK_RATE = 48000
# Blocking read duration: SoundCard drains all packets needed for this block.
LOOPBACK_POLL_S = 0.05
# How often to check whether Windows switched the default output (headphones
# plugged in, a USB headset connected), so the call keeps being captured.
OUTPUT_CHECK_S = 2.0
# A track that falls further behind the wall clock than this gets silence
# inserted. Loopback delivers nothing while nothing plays, and a mic can drop
# out for a moment; padding keeps both tracks on one timeline.
MAX_LAG_S = 0.25


# =============================================================================
# One track on disk
# =============================================================================

class _Track:
    # A WAV file whose length follows the wall clock from `started`, so every
    # sample's position in the file is the moment it was heard.
    def __init__(self, path: Path, started: float, input_rate: int = SAMPLE_RATE):
        self.path = path
        self._started = started
        self._wav = wave.open(str(path), 'wb')
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SAMPLE_RATE)
        self.frames = 0
        self._input_rate = None
        self._resampler = None
        self.set_input_rate(input_rate)

    # A device switch can change the rate mid-meeting (a 44.1 kHz headset after
    # a 48 kHz speaker); the resampler is rebuilt rather than mixing rates.
    def set_input_rate(self, rate: int):
        rate = int(rate)
        if rate == self._input_rate:
            return
        self._flush_resampler()
        self._input_rate = rate
        self._resampler = None if rate == SAMPLE_RATE else \
            soxr.ResampleStream(rate, SAMPLE_RATE, 1, dtype='float32', quality='HQ')

    def write(self, block: np.ndarray, arrived_at: float):
        if self._resampler is not None:
            block = self._resampler.resample_chunk(block)
        if not len(block):
            return
        # The block ends roughly when it arrived; anything missing before it
        # (a silent loopback, a mic hiccup) becomes silence.
        should_end_at = int((arrived_at - self._started) * SAMPLE_RATE)
        missing = should_end_at - len(block) - self.frames
        if missing > MAX_LAG_S * SAMPLE_RATE:
            self._write_silence(missing)
        self._write_samples(block)

    # Silence up to `seconds` of meeting time, so both tracks end together.
    def pad_to(self, seconds: float):
        self._flush_resampler()
        missing = int(seconds * SAMPLE_RATE) - self.frames
        if missing > 0:
            self._write_silence(missing)

    # Soxr buffers a short tail; finish it before padding or changing devices.
    def _flush_resampler(self):
        if self._resampler is not None:
            tail = self._resampler.resample_chunk(np.empty(0, dtype=np.float32), last=True)
            self._write_samples(tail)
            self._resampler = None

    def close(self):
        try:
            self._wav.close()
        except Exception as e:
            logger.warning(f"Closing {self.path.name} failed: {e}")

    def _write_silence(self, frames: int):
        chunk = np.zeros(min(frames, SAMPLE_RATE * 10), dtype=np.int16)
        while frames > 0:
            part = chunk[:min(frames, len(chunk))]
            self._wav.writeframes(part.tobytes())
            self.frames += len(part)
            frames -= len(part)

    def _write_samples(self, block: np.ndarray):
        pcm = (np.clip(block, -1.0, 1.0) * 32767.0).astype(np.int16)
        self._wav.writeframes(pcm.tobytes())
        self.frames += len(pcm)


# =============================================================================
# Recorder
# =============================================================================

class MeetingRecorder:
    # `folder` receives mic.wav and system.wav. `mic_device` is a sounddevice
    # id (None = the Windows default input), normally the dictation mic.
    def __init__(self, folder: Path, mic_device=None):
        self.folder = Path(folder)
        self.mic_device = mic_device
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._threads = []
        self._mic_stream = None
        self._tracks = {}
        self._started = None
        self._stopped_at = None
        # Whether loopback ever delivered sound, so a meeting with nothing on
        # the speakers is known to be an in-room one.
        self.heard_system = False
        self._loopback_ready = threading.Event()
        self._loopback_error = None
        self._writer_error = None
        self._warnings = []
        self._warning_lock = threading.Lock()

    @property
    def warnings(self):
        with self._warning_lock:
            return list(self._warnings)

    # Deduplicate persistent device errors so a long outage stays readable.
    def _warn(self, message):
        message = str(message)
        with self._warning_lock:
            if message not in self._warnings:
                self._warnings.append(message)
        logger.warning(message)

    @property
    def elapsed(self) -> float:
        if self._started is None:
            return 0.0
        return (self._stopped_at or time.monotonic()) - self._started

    def start(self):
        # soundcard initializes COM on first import. Import on the caller
        # before initializing the capture thread; otherwise its S_FALSE check
        # rejects an already-initialized thread and silently loses call audio.
        import soundcard
        self._soundcard = soundcard
        self.folder.mkdir(parents=True, exist_ok=True)
        self._started = time.monotonic()
        mic_rate = self._open_mic()
        self._tracks = {
            'mic': _Track(self.folder / 'mic.wav', self._started, mic_rate),
            'system': _Track(self.folder / 'system.wav', self._started),
        }
        for name, target in (('meeting-writer', self._write_loop),
                             ('meeting-loopback', self._loopback_loop)):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        if not self._loopback_ready.wait(6):
            self.stop()
            raise RuntimeError("System audio capture didn't start")
        if self._loopback_error:
            self.stop()
            raise RuntimeError(f"System audio capture couldn't start: {self._loopback_error}")
        logger.info(f"Meeting recording started in {self.folder}")

    # Stops both captures, flushes what is queued and pads both tracks to the
    # meeting's length. Returns the duration in seconds.
    def stop(self) -> float:
        self._stopped_at = time.monotonic()
        self._stop.set()
        if self._mic_stream is not None:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except Exception as e:
                logger.debug(f"Closing the meeting mic stream: {e}")
            self._mic_stream = None
        # Producers must stop before the writer can declare its queue drained.
        for thread in self._threads:
            if thread.name != 'meeting-writer':
                thread.join(timeout=5)
        for thread in self._threads:
            if thread.name == 'meeting-writer':
                thread.join(timeout=15)
        if any(thread.is_alive() for thread in self._threads):
            raise RuntimeError("Audio is still being saved; the recording was kept for recovery")
        duration = self.elapsed
        if self._writer_error:
            raise RuntimeError(f"Audio couldn't be fully saved: {self._writer_error}")
        logger.info(f"Meeting recording stopped after {duration:.0f}s")
        return duration

    # ---- microphone ---------------------------------------------------------

    # Its own stream on the dictation mic, so dictating during a meeting (or
    # switching the dictation mic) never interrupts the meeting's audio.
    def _open_mic(self) -> int:
        device = self.mic_device
        try:
            info = sd.query_devices(device, 'input')
        except Exception:
            device, info = None, sd.query_devices(None, 'input')
        rate = int(info.get('default_samplerate') or 48000)

        def on_audio(indata, _frames, _time_info, status):
            if status:
                self._warn(f'Microphone capture interruption: {status}')
            self._queue.put(('mic', time.monotonic(), indata[:, 0].copy(), rate))

        self._mic_stream = sd.InputStream(device=device, samplerate=rate, channels=1,
                                          dtype='float32', callback=on_audio)
        self._mic_stream.start()
        return rate

    # ---- what the PC plays --------------------------------------------------

    # Drains the default output's loopback until stopped, reopening it when
    # the default output changes or the device goes away.
    def _loopback_loop(self):
        # soundcard talks to WASAPI through COM, which needs initialising on
        # every thread that uses it.
        ctypes.windll.ole32.CoInitializeEx(None, 0)
        soundcard = self._soundcard
        try:
            while not self._stop.is_set():
                try:
                    self._capture_default_output(soundcard)
                except Exception as e:
                    self._warn(f"System audio capture interrupted: {e}")
                    if not self._loopback_ready.is_set():
                        self._loopback_error = str(e)
                        self._loopback_ready.set()
                        return
                    self._stop.wait(1.0)
        finally:
            ctypes.windll.ole32.CoUninitialize()

    def _capture_default_output(self, soundcard):
        speaker = soundcard.default_speaker()
        source = soundcard.get_microphone(id=str(speaker.name), include_loopback=True)
        logger.info(f"Meeting: capturing what plays on {speaker.name}")
        # SoundCard's Windows backend corrupts single-channel capture. Request
        # the device's full channel layout, then downmix the returned frames.
        # https://github.com/bastibe/SoundCard#known-issues
        with source.recorder(samplerate=LOOPBACK_RATE) as recorder:
            self._loopback_ready.set()
            next_check = time.monotonic() + OUTPUT_CHECK_S
            while not self._stop.is_set():
                data = recorder.record(numframes=int(LOOPBACK_RATE * LOOPBACK_POLL_S))
                if len(data):
                    block = np.asarray(data, dtype=np.float32).reshape(len(data), -1).mean(axis=1)
                    if not self.heard_system and np.abs(block).max() > 0.003:
                        self.heard_system = True
                    self._queue.put(('system', time.monotonic(), block, LOOPBACK_RATE))
                if time.monotonic() >= next_check:
                    next_check = time.monotonic() + OUTPUT_CHECK_S
                    if soundcard.default_speaker().id != speaker.id:
                        self._warn('System output changed during recording; capture followed the new output.')
                        logger.info("Meeting: default output changed, following it")
                        return
                # record() already waits for audio. Sleeping again would drain
                # one WASAPI packet too slowly and chop speech into fragments.

    # ---- writer -------------------------------------------------------------

    # The only thread touching the WAV files, so the audio callbacks never wait
    # on the disk.
    def _write_loop(self):
        while True:
            try:
                name, arrived_at, block, rate = self._queue.get(timeout=0.2)
            except queue.Empty:
                if self._stop.is_set() and all(not t.is_alive() for t in self._threads
                                               if t is not threading.current_thread()):
                    # This is the only thread allowed to touch or close WAVs.
                    for track in self._tracks.values():
                        try:
                            track.pad_to(self.elapsed)
                        except Exception as e:
                            self._writer_error = str(e)
                            self._warn(f'Audio could not be fully saved: {e}')
                        finally:
                            track.close()
                    return
                continue
            track = self._tracks.get(name)
            if track is None:
                continue
            try:
                track.set_input_rate(rate)
                track.write(block, arrived_at)
            except Exception as e:
                self._writer_error = str(e)
                self._warn(f'Audio could not be fully saved: {e}')
                logger.error(f"Writing meeting audio failed: {e}")
