# media_pause.py
# Pauses music and videos while you dictate and resumes them when you let go
# (audio.pause_media_on_record). Only players that were actually playing are
# paused, and only those are resumed. The OS calls run on a worker thread so
# starting a recording is never held up by a slow media app.

import logging
import threading

logger = logging.getLogger(__name__)


class MediaPause:
    # `enabled_getter` is read at each recording start, so the setting applies
    # to the next dictation without a restart.
    def __init__(self, enabled_getter):
        self._enabled = enabled_getter
        self._lock = threading.Lock()
        self._paused_ids = []
        self._pause_thread = None

    # Recording started: pause what is playing, in the background.
    def pause(self):
        try:
            if not self._enabled():
                return
        except Exception:
            return
        with self._lock:
            if self._pause_thread and self._pause_thread.is_alive():
                return
            self._pause_thread = threading.Thread(target=self._pause_worker,
                                                  name='media-pause', daemon=True)
            self._pause_thread.start()

    # Recording ended (released, stopped, cancelled or timed out): resume what
    # pause() paused. Safe to call when nothing was paused.
    def resume(self):
        with self._lock:
            pause_thread = self._pause_thread
        if pause_thread is None:
            return
        threading.Thread(target=self._resume_worker, args=(pause_thread,),
                         name='media-resume', daemon=True).start()

    # ---- workers ------------------------------------------------------------

    def _pause_worker(self):
        from .platform import media
        try:
            paused = media.pause_playing()
        except Exception as e:
            logger.debug(f"Media pause skipped: {e}")
            paused = []
        if paused:
            logger.info(f"Paused media while recording: {', '.join(paused)}")
        with self._lock:
            self._paused_ids.extend(paused)

    # Waits for a pause still in flight (a very quick tap), so a player paused
    # a moment after release is still resumed.
    def _resume_worker(self, pause_thread):
        from .platform import media
        pause_thread.join(timeout=3)
        with self._lock:
            if self._pause_thread is pause_thread:
                self._pause_thread = None
            paused, self._paused_ids = self._paused_ids, []
        if not paused:
            return
        try:
            resumed = media.resume(paused)
            if resumed:
                logger.info(f"Resumed media: {', '.join(resumed)}")
        except Exception as e:
            logger.debug(f"Media resume skipped: {e}")
