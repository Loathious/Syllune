# Keep registered Ctrl+Win gestures from opening Start when either modifier is
# released first. The hook does bounded key bookkeeping and one SendInput batch;
# no application callbacks or UI Automation run on its Windows message thread.
import ctypes
from ctypes import wintypes as wt
import logging
import threading
import time

from . import keyboard

logger = logging.getLogger(__name__)
user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HANDLE
user32.CallNextHookEx.argtypes = [wt.HANDLE, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wt.HANDLE]
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE


# Canonical modifier names let left/right releases share the same bounded state.
def _key_name(vk):
    if vk in (0x11, 0xA2, 0xA3):
        return 'ctrl'
    if vk in (0x5B, 0x5C):
        return 'win'
    if vk in (0x10, 0xA0, 0xA1):
        return 'shift'
    if vk in (0x12, 0xA4, 0xA5):
        return 'alt'
    return str(vk)


class ReleaseState:
    def __init__(self, chords, grace_seconds=0.2):
        self.chords = [set(chord) for chord in chords if {'ctrl', 'win'} <= set(chord)]
        self.grace_seconds = grace_seconds
        self.down = set()
        self.armed = {}

    # A new standalone Win press never inherits a previous gesture's grace.
    def event(self, vk, is_down, *, injected=False, now=None):
        if injected:
            return False
        now = time.monotonic() if now is None else now
        if is_down:
            if vk not in self.down and vk in (0x5B, 0x5C):
                self.armed.pop(vk, None)
            self.down.add(vk)
            names = {_key_name(key) for key in self.down}
            if any(chord <= names for chord in self.chords):
                for win in self.down & {0x5B, 0x5C}:
                    self.armed[win] = float('inf')
            return False
        self.down.discard(vk)
        if vk in (0x5B, 0x5C):
            return now <= self.armed.pop(vk, -1)
        if _key_name(vk) == 'ctrl' and not any(_key_name(key) == 'ctrl' for key in self.down):
            for win in self.armed:
                self.armed[win] = now + self.grace_seconds
        return False


class HookThread:
    """Own one low-level hook and its message loop; stop is idempotent."""
    def __init__(self, hook_id, callback, name):
        self.hook_id, self.callback, self.name = hook_id, callback, name
        self._thread = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._hook = None
        self._proc = None

    # Startup waits only for hook installation, never for input or a provider.
    def start(self):
        if self._thread and self._thread.is_alive():
            return bool(self._hook)
        self._ready.clear()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        if not self._ready.wait(1):
            self.stop()
            return False
        return bool(self._hook)

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        message = wt.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)

        def dispatch(code, message_id, event_ptr):
            if code >= 0 and not self._stop.is_set():
                try:
                    if self.callback(message_id, event_ptr):
                        return 1
                except Exception:
                    # Always forward physical releases if anything goes wrong.
                    pass
            return user32.CallNextHookEx(self._hook, code, message_id, event_ptr)

        self._proc = HOOKPROC(dispatch)
        try:
            self._hook = user32.SetWindowsHookExW(
                self.hook_id, self._proc, kernel32.GetModuleHandleW(None), 0)
            self._ready.set()
            if not self._hook:
                logger.warning('%s could not install input hook', self.name)
                return
            while not self._stop.is_set() and user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            self._thread_id = 0
            self._ready.set()

    def stop(self):
        self._stop.set()
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)


class _KeyboardEvent(ctypes.Structure):
    _fields_ = [('vkCode', wt.DWORD), ('scanCode', wt.DWORD),
                ('flags', wt.DWORD), ('time', wt.DWORD), ('extra', ctypes.c_size_t)]


class StartMenuGuard:
    def __init__(self):
        self.state = ReleaseState([])
        self._hook = HookThread(13, self._on_key, 'dictation-win-release')

    def configure(self, combos):
        aliases = {'control': 'ctrl', 'window': 'win', 'windows': 'win',
                   'super': 'win', 'cmd': 'win'}
        chords = []
        for combo in combos:
            names = {aliases.get(key.strip(), key.strip()) for key in combo.split('+')}
            chords.append({str(keyboard.VK_MAP[name]) if name in keyboard.VK_MAP
                           and name not in ('ctrl', 'win', 'alt', 'shift') else name
                           for name in names})
        self.state = ReleaseState(chords)

    def handle_event(self, vk, is_down, *, injected=False, now=None):
        if self.state.event(vk, is_down, injected=injected, now=now):
            # Block the physical Win-up only after the complete ordered batch
            # was accepted. A failed SendInput must never leave Win held.
            return keyboard.mask_start_menu_and_release(vk)
        return False

    def _on_key(self, message, event_ptr):
        if message not in (0x100, 0x101, 0x104, 0x105):
            return False
        event = ctypes.cast(event_ptr, ctypes.POINTER(_KeyboardEvent)).contents
        return self.handle_event(event.vkCode, message in (0x100, 0x104),
                                 injected=bool(event.flags & 0x10))

    def start(self):
        self.state = ReleaseState(self.state.chords)
        return self._hook.start()

    def stop(self):
        self._hook.stop()
        self.state = ReleaseState(self.state.chords)
