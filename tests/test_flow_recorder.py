# The WAV writer owns file lifetime, including the final flush and close.
# This simulates capture without opening a microphone or playing sound.
import threading
import tempfile
import wave
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock

import numpy as np
from whisper_key.meeting_recorder import MeetingRecorder, _Track


class RecorderFlushTests(unittest.TestCase):
    def test_resampler_tail_is_written_before_padding(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'mic.wav'
            track = _Track(path, 0, input_rate=48000)
            track.write(np.full(4800, .25, dtype=np.float32), .1)
            track.pad_to(.1)
            track.close()
            with wave.open(str(path)) as wav:
                audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
            self.assertGreater(np.count_nonzero(np.abs(audio) > 3000), 1500)

    def test_loopback_downmixes_all_channels(self):
        recorder = MeetingRecorder(Path('unused'))
        soundcard = MagicMock()
        source = soundcard.get_microphone.return_value
        stream = source.recorder.return_value.__enter__.return_value
        def capture(**kwargs):
            recorder._stop.set()
            return np.array([[0.2, 0.6], [0.4, 0.8]], dtype=np.float32)
        stream.record.side_effect = capture
        recorder._capture_default_output(soundcard)
        block = recorder._queue.get_nowait()[2]
        np.testing.assert_allclose(block, [0.4, 0.6])

    def test_blocking_loopback_read_is_not_throttled_again(self):
        recorder = MeetingRecorder(Path('unused'))
        soundcard = MagicMock()
        stream = soundcard.get_microphone.return_value.recorder.return_value.__enter__.return_value
        def capture(**kwargs):
            recorder._stop.set()
            return np.ones((2400, 2), dtype=np.float32) * .1
        stream.record.side_effect = capture
        recorder._stop.wait = Mock()
        recorder._capture_default_output(soundcard)
        recorder._stop.wait.assert_not_called()

    def test_writer_closes_its_own_tracks_after_queue_drains(self):
        recorder = MeetingRecorder(Path('unused'))
        recorder._started = time.monotonic()
        ownership = []
        track = Mock()
        track.close.side_effect = lambda: ownership.append(threading.current_thread().name)
        recorder._tracks = {'mic': track}
        recorder._queue.put(('mic', time.monotonic(), np.zeros(1600, dtype=np.float32), 16000))
        writer = threading.Thread(target=recorder._write_loop, name='meeting-writer')
        recorder._threads = [writer]
        writer.start()
        recorder.stop()
        self.assertFalse(writer.is_alive())
        track.write.assert_called_once()
        self.assertEqual(['meeting-writer'], ownership)


if __name__ == '__main__':
    unittest.main()
