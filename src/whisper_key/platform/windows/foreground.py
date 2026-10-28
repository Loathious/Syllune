# platform/windows/foreground.py
# Identifies the focused window's executable and title, which per-app rules use
# to decide delivery and formatting behaviour.
# macOS mirror: platform/macos/foreground.py.
import ctypes
import ctypes.wintypes as wt
import logging
import os

logger = logging.getLogger(__name__)

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_psapi = ctypes.windll.psapi

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('rcMonitor', wt.RECT),
                ('rcWork', wt.RECT), ('dwFlags', wt.DWORD)]


# Anti-cheat protected games (Fortnite/EAC, VALORANT/Vanguard) refuse the
# VM_READ access GetModuleFileNameExW needs; the limited query right still
# returns the image path, so games are recognised by exe name too.
def _exe_path_for_pid(pid: int) -> str:
    handle = _kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if handle:
        try:
            buf = ctypes.create_unicode_buffer(520)
            if _psapi.GetModuleFileNameExW(handle, None, buf, 520):
                return buf.value
        finally:
            _kernel32.CloseHandle(handle)
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ''
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = wt.DWORD(520)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        _kernel32.CloseHandle(handle)
    return ''


# True when the focused window covers its whole monitor (fullscreen games,
# videos). The desktop and shell windows also span the screen, so they're
# excluded by class name.
def is_fullscreen() -> bool:
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return False
        cls = ctypes.create_unicode_buffer(64)
        _user32.GetClassNameW(hwnd, cls, 64)
        if cls.value in ('Progman', 'WorkerW', 'Shell_TrayWnd'):
            return False
        rect = wt.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        monitor = _user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        m = info.rcMonitor
        return (rect.left <= m.left and rect.top <= m.top
                and rect.right >= m.right and rect.bottom >= m.bottom)
    except Exception as e:
        logger.debug(f"Fullscreen probe failed: {e}")
        return False


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ProcessID', wt.DWORD),
                ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wt.DWORD),
                ('cntThreads', wt.DWORD), ('th32ParentProcessID', wt.DWORD),
                ('pcPriClassBase', ctypes.c_long), ('dwFlags', wt.DWORD),
                ('szExeFile', ctypes.c_wchar * 260)]


# Lower-case exe names of every running process. Game mode uses it to stay on
# while a game runs in the background (alt-tabbed to Discord), which a
# foreground check alone would miss. The snapshot needs no process rights, so
# anti-cheat protected games are listed too.
_snapshot = ctypes.WinDLL('kernel32', use_last_error=True)
_snapshot.CreateToolhelp32Snapshot.restype = wt.HANDLE
_snapshot.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
_snapshot.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_snapshot.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_snapshot.CloseHandle.argtypes = [wt.HANDLE]
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def running_exe_names() -> set:
    names = set()
    snapshot = _snapshot.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == _INVALID_HANDLE:
        return names
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = _snapshot.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            names.add(entry.szExeFile.lower())
            ok = _snapshot.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _snapshot.CloseHandle(snapshot)
    return names


def get_foreground_app() -> dict:
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return {}

        pid = wt.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == 0:
            return {}

        exe_path = _exe_path_for_pid(pid.value)
        if not exe_path:
            return {}

        title_len = _user32.GetWindowTextLengthW(hwnd)
        title_buf = ctypes.create_unicode_buffer(title_len + 1)
        _user32.GetWindowTextW(hwnd, title_buf, title_len + 1)

        return {
            'exe': os.path.basename(exe_path).lower() if exe_path else '',
            'path': exe_path,
            'title': title_buf.value,
        }
    except Exception as e:
        logger.debug(f"Foreground app probe failed: {e}")
        return {}
