# Verify focused text inputs through Windows UI Automation before offering a
# pending dictation. A physical mouse click is required; hover and focus changes
# alone cannot consume text. No text contents, clipboard, or keys are inspected.
import ctypes
from ctypes import wintypes as wt
import logging
import queue
import threading
import time
import uuid

from . import foreground
from .start_menu_guard import HookThread

logger = logging.getLogger(__name__)
_user32 = ctypes.WinDLL('user32', use_last_error=True)
_ole32 = ctypes.OleDLL('ole32')
_oleaut32 = ctypes.OleDLL('oleaut32')
_user32.GetForegroundWindow.restype = wt.HWND


class _GUID(ctypes.Structure):
    _fields_ = [('data1', wt.DWORD), ('data2', wt.WORD), ('data3', wt.WORD),
                ('data4', ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _VariantValue(ctypes.Union):
    # The record member makes VARIANT 24 bytes on x64, including its union.
    _fields_ = [('integer', ctypes.c_long), ('boolean', ctypes.c_short),
                ('pointer', ctypes.c_void_p), ('record', ctypes.c_void_p * 2)]


class _Variant(ctypes.Structure):
    _anonymous_ = ('value',)
    _fields_ = [('vt', wt.WORD), ('reserved', wt.WORD * 3), ('value', _VariantValue)]


_ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wt.DWORD]
_ole32.CoUninitialize.restype = None
_ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, wt.DWORD,
                                   ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
_oleaut32.VariantClear.argtypes = [ctypes.POINTER(_Variant)]
_oleaut32.SafeArrayGetDim.argtypes = [ctypes.c_void_p]
_oleaut32.SafeArrayGetLBound.argtypes = [ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_long)]
_oleaut32.SafeArrayGetUBound.argtypes = [ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_long)]
_oleaut32.SafeArrayGetElement.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long), ctypes.c_void_p]
_oleaut32.SafeArrayDestroy.argtypes = [ctypes.c_void_p]


# Use the native IUnknown vtable without generated files or another dependency.
# Slots follow the Windows UIAutomationClient type library (stable COM ABI).
def _call(pointer, slot, *argument_types):
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argument_types)(vtable[slot])


def _release(pointer):
    if pointer:
        _call(pointer, 2)(pointer)


def _scalar(value):
    if value.vt == 11:  # VT_BOOL; unsupported/mixed values must stay unknown.
        return bool(value.boolean)
    if value.vt in (3, 22):  # VT_I4 / VT_INT
        return int(value.integer)
    return None


def _property(element, property_id):
    value = _Variant()
    try:
        # ignoreDefaultValue prevents absent password/read-only properties from
        # being mistaken for an explicit false reported by the provider.
        _call(element, 11, ctypes.c_int, wt.BOOL, ctypes.POINTER(_Variant))(
            element, property_id, True, ctypes.byref(value))
        return _scalar(value)
    finally:
        _oleaut32.VariantClear(ctypes.byref(value))


def _runtime_id(element):
    array = ctypes.c_void_p()
    try:
        _call(element, 4, ctypes.POINTER(ctypes.c_void_p))(element, ctypes.byref(array))
        if not array or _oleaut32.SafeArrayGetDim(array) != 1:
            return None
        low, high = ctypes.c_long(), ctypes.c_long()
        _oleaut32.SafeArrayGetLBound(array, 1, ctypes.byref(low))
        _oleaut32.SafeArrayGetUBound(array, 1, ctypes.byref(high))
        if not 0 < high.value - low.value + 1 <= 64:
            return None
        values = []
        for index in range(low.value, high.value + 1):
            position, value = ctypes.c_long(index), ctypes.c_long()
            _oleaut32.SafeArrayGetElement(array, ctypes.byref(position), ctypes.byref(value))
            values.append(value.value)
        return tuple(values)
    finally:
        if array:
            _oleaut32.SafeArrayDestroy(array)


# Document controls include passive browser pages. Require an explicit writable
# attribute; merely exposing TextPattern does not prove the text can be edited.
def _text_readonly(element):
    pattern, text_range = ctypes.c_void_p(), ctypes.c_void_p()
    value = _Variant()
    try:
        _call(element, 16, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
            element, 10014, ctypes.byref(pattern))  # UIA_TextPatternId
        if not pattern:
            return None
        _call(pattern, 7, ctypes.POINTER(ctypes.c_void_p))(pattern, ctypes.byref(text_range))
        if not text_range:
            return None
        _call(text_range, 9, ctypes.c_int, ctypes.POINTER(_Variant))(
            text_range, 40015, ctypes.byref(value))  # UIA_IsReadOnlyAttributeId
        return _scalar(value)
    except (OSError, ValueError):
        return None
    finally:
        _oleaut32.VariantClear(ctypes.byref(value))
        _release(text_range)
        _release(pattern)


def _editable(properties):
    if properties.get('control_type') not in (50004, 50030):  # Edit / Document
        return False
    if any(properties.get(key) is not True for key in ('focused', 'focusable', 'enabled')):
        return False
    if properties.get('password') is not False:
        return False
    # Many providers (Windows 11 Notepad's document, for one) leave IsOffscreen
    # unset. The element was just focused by a click on it, so only an explicit
    # offscreen report rules it out; an unknown password state still does.
    if properties.get('offscreen') is True:
        return False
    if properties.get('value_available') is True:
        return properties.get('value_readonly') is False
    return properties.get('text_readonly') is False


# A click on a text child of a contenteditable counts; a click on unrelated
# browser chrome or blank page does not, even if the old editor retains focus.
def _click_matches(automation, focused, point):
    clicked, walker = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        _call(automation, 7, wt.POINT, ctypes.POINTER(ctypes.c_void_p))(
            automation, wt.POINT(*point), ctypes.byref(clicked))
        _call(automation, 16, ctypes.POINTER(ctypes.c_void_p))(
            automation, ctypes.byref(walker))  # RawViewWalker
        for _ in range(12):
            if not clicked:
                return False
            same = wt.BOOL()
            _call(automation, 3, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wt.BOOL))(
                automation, focused, clicked, ctypes.byref(same))
            if same.value:
                return True
            if not walker:
                return False
            parent = ctypes.c_void_p()
            _call(walker, 3, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                walker, clicked, ctypes.byref(parent))
            _release(clicked)
            clicked = parent
        return False
    finally:
        _release(clicked)
        _release(walker)


def _probe_text_target(click_point=None):
    automation, element = ctypes.c_void_p(), ctypes.c_void_p()
    initialized = False
    try:
        try:
            _ole32.CoInitializeEx(None, 0)  # MTA; UIA stays off the hook thread.
            initialized = True
        except OSError as error:
            if getattr(error, 'winerror', None) != -2147417850:  # existing STA
                return None
        hwnd = _user32.GetForegroundWindow()
        metadata = foreground.get_foreground_app()
        if not hwnd or not metadata.get('exe') or not metadata.get('path'):
            return None
        clsid = _GUID.parse('E22AD333-B25F-460C-83D0-0581107395C9')  # CUIAutomation8
        iid = _GUID.parse('34723AFF-0C9D-49D0-9896-7AB52DF8CD8A')  # IUIAutomation2
        _ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(automation))
        # UIA defaults to a 20-second transaction timeout. Fail closed promptly
        # for unresponsive providers instead of holding pending delivery open.
        _call(automation, 61, wt.DWORD)(automation, 200)  # ConnectionTimeout
        _call(automation, 63, wt.DWORD)(automation, 200)  # TransactionTimeout
        _call(automation, 8, ctypes.POINTER(ctypes.c_void_p))(automation, ctypes.byref(element))
        if not element:
            return None
        properties = {name: _property(element, property_id) for name, property_id in (
            ('control_type', 30003), ('focused', 30008), ('focusable', 30009),
            ('enabled', 30010), ('password', 30019), ('offscreen', 30022),
            ('value_available', 30043), ('value_readonly', 30046))}
        if properties['value_available'] is not True:
            properties['text_readonly'] = _text_readonly(element)
        if not _editable(properties):
            return None
        runtime = _runtime_id(element)
        if not runtime or (click_point is not None and not _click_matches(automation, element, click_point)):
            return None
        # Foreground/title can change during cross-process provider calls.
        if hwnd != _user32.GetForegroundWindow() or metadata != foreground.get_foreground_app():
            return None
        return metadata | {'hwnd': int(hwnd), 'target_token': (int(hwnd), runtime),
                           'editable': True, 'password': False}
    except (OSError, ValueError, TypeError):
        return None
    finally:
        _release(element)
        _release(automation)
        if initialized:
            _ole32.CoUninitialize()


def current_text_target():
    """Return verified foreground metadata and a stable token, else None."""
    return _probe_text_target()


class _MouseEvent(ctypes.Structure):
    _fields_ = [('point', wt.POINT), ('mouseData', wt.DWORD), ('flags', wt.DWORD),
                ('time', wt.DWORD), ('extra', ctypes.c_size_t)]


class TextTargetWatcher:
    def __init__(self, on_target):
        self.on_target = on_target
        self._clicks = queue.Queue(maxsize=8)
        self._stopped = threading.Event()
        self._pressed = False
        self._press_point = (0, 0)
        self._press_at = 0
        self._worker = None
        self._hook = HookThread(14, self._on_mouse, 'dictation-click-hook')

    # The low-level callback queues coordinates only; COM may block in another
    # process and must never delay Windows input dispatch.
    def _mouse_event(self, message, flags, point):
        if flags & 1:
            return
        if message == 0x201:
            self._pressed = True
            self._press_point = point
            self._press_at = time.monotonic()
        elif message == 0x202:
            distance = sum((a - b) ** 2 for a, b in zip(point, self._press_point))
            if self._pressed and distance <= 64 and time.monotonic() - self._press_at <= 1:
                try:
                    self._clicks.put_nowait((time.monotonic(), point))
                except queue.Full:
                    pass
            self._pressed = False

    def _on_mouse(self, message, event_ptr):
        if message in (0x201, 0x202):
            event = ctypes.cast(event_ptr, ctypes.POINTER(_MouseEvent)).contents
            self._mouse_event(message, event.flags, (event.point.x, event.point.y))
        return False

    def _deliver_click(self, point, clicked_at=None):
        if self._stopped.is_set():
            return False
        clicked_at = time.monotonic() if clicked_at is None else clicked_at
        try:
            target = _probe_text_target(point)
            if (target and not self._stopped.is_set()
                    and time.monotonic() - clicked_at <= 0.75):
                self.on_target(target)
                return True
        except Exception:
            logger.debug('Editable-click callback failed', exc_info=True)
        return False

    def _run(self):
        while not self._stopped.is_set():
            try:
                clicked_at, point = self._clicks.get(timeout=0.1)
            except queue.Empty:
                continue
            # Focus is applied after mouse dispatch. Reject stale clicks rather
            # than delivering to whatever happens to become active much later.
            if self._stopped.wait(0.04):
                return
            if time.monotonic() - clicked_at <= 0.75:
                self._deliver_click(point, clicked_at)

    def start(self):
        if self._worker and self._worker.is_alive():
            return
        self._stopped.clear()
        self._pressed = False
        while not self._clicks.empty():
            self._clicks.get_nowait()
        if not self._hook.start():
            self._stopped.set()
            return
        self._worker = threading.Thread(target=self._run, name='dictation-editable-click', daemon=True)
        self._worker.start()

    def stop(self):
        self._stopped.set()
        self._hook.stop()
        if self._worker and self._worker is not threading.current_thread():
            self._worker.join(timeout=0.5)
