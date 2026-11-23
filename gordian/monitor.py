"""Windows calls: who is in front, and how a reward window is closed.

get_sample reads the foreground process, its path, its title, idle time, and
whether the window covers the monitor (the game check).

close_program never targets Gordian, pythonw, Explorer, or a protected system
process. A browser gets Ctrl+W so one tab closes. Anything else gets Alt+F4
on the root window. If that process is still alive after a few seconds it is
terminated and the display mode is restored, which is what clears a game that
stayed on a black fullscreen.

The warning window is topmost and does not take focus, so it can sit over
another program the way Sticky Keys does. Showing it again uses a pin that
does not flash a second window into existence.
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from gordian.address import read_address


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

ERROR_ALREADY_EXISTS = 183
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_mutex_handle = None


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwnd", wintypes.HWND),
        ("dwFlags", wintypes.DWORD),
        ("uCount", wintypes.UINT),
        ("dwTimeout", wintypes.DWORD),
    ]


def _configure_win32() -> None:
    kernel32.GetTickCount.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    user32.GetLastInputInfo.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = ctypes.c_void_p
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.FlashWindowEx.argtypes = [ctypes.POINTER(FLASHWINFO)]
    user32.FlashWindowEx.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user32.MessageBoxW.restype = ctypes.c_int


_configure_win32()


@dataclass(frozen=True)
class Sample:
    process: str
    title: str
    idle_seconds: float
    pid: int = 0
    path: str = ""
    fullscreen: bool = False
    hwnd: int = 0
    url: str = ""


def prepare_windows() -> None:
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
        shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
        shell32.SetCurrentProcessExplicitAppUserModelID("GordianKnot.Application")
    except Exception:
        return


def acquire_single_instance() -> bool:
    global _mutex_handle
    handle = kernel32.CreateMutexW(None, False, "Local\\GordianKnotMonitor")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return False
    _mutex_handle = handle
    return True


def alert(text: str) -> None:
    user32.MessageBoxW(None, text, "Gordian Knot", 0x40)


def warn(text: str) -> None:
    MB_ICONWARNING = 0x00000030
    MB_SETFOREGROUND = 0x00010000
    MB_TOPMOST = 0x00040000
    user32.MessageBoxW(None, text, "Gordian Knot", MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST)


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if not hwnd:
        return None
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def _topmost():
    return wintypes.HWND(-1)


def stick_window(hwnd: int, x: int, y: int, width: int, height: int) -> None:
    if not hwnd:
        return
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    user32.SetWindowPos(hwnd, _topmost(), x, y, width, height, SWP_NOACTIVATE | SWP_SHOWWINDOW)


def _frame_hwnd(hwnd: int) -> int:
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    frame = user32.GetAncestor(hwnd, 2)
    return int(frame or hwnd)


def release_owner(hwnd: int) -> None:
    """Let a popup stay visible while the main window is hidden in the tray.

    winfo_id is the client area. The title bar belongs to the outer frame, and
    that is the window whose owner has to change. Clearing the owner on the
    client pulls the buttons out of the frame and leaves a blank window.
    """
    if not hwnd:
        return
    frame = _frame_hwnd(hwnd)
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SetWindowLongPtrW(frame, -8, 0)


def _colorref(color: str) -> int:
    text = (color or "").lstrip("#")
    if len(text) != 6:
        return 0
    red = int(text[0:2], 16)
    green = int(text[2:4], 16)
    blue = int(text[4:6], 16)
    return red | (green << 8) | (blue << 16)


def set_title_theme(hwnd: int, dark: bool, caption: str = "") -> None:
    """Match the Windows title bar to night mode. Attribute 20 is current; 19 is the older name.

    Attributes 35 and 36 paint the caption itself, so a popup does not keep the light band.
    """
    if not hwnd:
        return
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:
        return
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    targets: list[int] = []
    for item in (_frame_hwnd(hwnd), int(user32.GetParent(hwnd) or 0), int(hwnd)):
        if item and item not in targets:
            targets.append(item)
    dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
    dark_value = ctypes.c_int(1 if dark else 0)
    default = ctypes.c_int(0xFFFFFFFF)
    fill = ctypes.c_int(_colorref(caption or "#14161a"))
    ink = ctypes.c_int(_colorref("#e7ecf3"))
    user32.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    user32.RedrawWindow.restype = wintypes.BOOL
    for frame in targets:
        for attribute in (20, 19):
            dwmapi.DwmSetWindowAttribute(frame, attribute, ctypes.byref(dark_value), ctypes.sizeof(dark_value))
        if dark:
            dwmapi.DwmSetWindowAttribute(frame, 34, ctypes.byref(fill), ctypes.sizeof(fill))
            dwmapi.DwmSetWindowAttribute(frame, 35, ctypes.byref(fill), ctypes.sizeof(fill))
            dwmapi.DwmSetWindowAttribute(frame, 36, ctypes.byref(ink), ctypes.sizeof(ink))
        else:
            for attribute in (34, 35, 36):
                dwmapi.DwmSetWindowAttribute(frame, attribute, ctypes.byref(default), ctypes.sizeof(default))
        user32.SetWindowPos(frame, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
        user32.RedrawWindow(frame, None, None, 0x0001 | 0x0100 | 0x0400)


def hold_inactive(hwnd: int) -> None:
    if not hwnd:
        return
    GWL_EXSTYLE = -20
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOPMOST = 0x00000008
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    current = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) or 0)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, current | WS_EX_NOACTIVATE | WS_EX_TOPMOST)


def pin_window(hwnd: int) -> None:
    if not hwnd:
        return
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOACTIVATE = 0x0010
    user32.SetWindowPos(hwnd, _topmost(), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def raise_window(hwnd: int) -> None:
    if not hwnd:
        return
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    user32.SetWindowPos(hwnd, _topmost(), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)


def flash_window(hwnd: int) -> None:
    if not hwnd:
        return
    info = FLASHWINFO()
    info.cbSize = ctypes.sizeof(FLASHWINFO)
    info.hwnd = hwnd
    info.dwFlags = 3
    info.uCount = 6
    info.dwTimeout = 0
    user32.FlashWindowEx(ctypes.byref(info))


def minimize_window(hwnd: int) -> None:
    if hwnd:
        user32.ShowWindow(hwnd, 6)


PROTECTED_PROCESSES = frozenset(
    {
        "explorer.exe",
        "sihost.exe",
        "dwm.exe",
        "csrss.exe",
        "winlogon.exe",
        "services.exe",
        "lsass.exe",
        "svchost.exe",
        "taskmgr.exe",
        "searchhost.exe",
        "shellexperiencehost.exe",
        "startmenuexperiencehost.exe",
        "textinputhost.exe",
        "applicationframehost.exe",
        "systemsettings.exe",
        "lockapp.exe",
        "logonui.exe",
        "python.exe",
        "pythonw.exe",
        "widgetboard.exe",
        "gamebar.exe",
        "gamebarftserver.exe",
    }
)


def may_close(process: str) -> bool:
    name = (process or "").casefold()
    if not name or name in PROTECTED_PROCESSES or "gordian" in name:
        return False
    return True


def close_style(process: str, browsers: tuple[str, ...] | None = None) -> str:
    if not may_close(process):
        return ""
    if _is_browser(process, browsers):
        return "tab"
    return "window"


_pending_closes: set[int] = set()
_close_token = 0


def cancel_pending_closes() -> None:
    """Drop a close that has not finished. Used when free time is available again."""
    global _close_token
    _close_token += 1
    _pending_closes.clear()


def close_program(hwnd: int, pid: int, process: str, browsers: tuple[str, ...] | None = None) -> bool:
    if not may_close(process):
        return False
    if close_style(process, browsers) == "tab":
        _close_browser_tab(hwnd)
        return True
    root = _root_window(hwnd)
    if root and int(user32.GetForegroundWindow() or 0) != int(root):
        _focus_window(root)
    if root:
        _tap_chord(0x12, 0x73)
    if pid and pid not in _pending_closes:
        _pending_closes.add(pid)
        token = _close_token
        threading.Thread(
            target=_finish_close, args=(pid, root, token), name="gordian-close", daemon=True
        ).start()
    return True


def _is_browser(process: str, browsers: tuple[str, ...] | None) -> bool:
    name = (process or "").casefold()
    if browsers is None:
        from gordian.settings import BROWSERS

        browsers = BROWSERS
    return name in {item.casefold() for item in browsers}


def _root_window(hwnd: int) -> int:
    if not hwnd:
        return 0
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    root = user32.GetAncestor(hwnd, 2)
    return int(root or hwnd)


def _focus_window(hwnd: int) -> None:
    if not hwnd:
        return
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    foreground = user32.GetForegroundWindow()
    current = kernel32.GetCurrentThreadId()
    slot = wintypes.DWORD()
    fore_thread = user32.GetWindowThreadProcessId(foreground, ctypes.byref(slot)) if foreground else 0
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    attached = bool(fore_thread and fore_thread != current and user32.AttachThreadInput(current, fore_thread, True))
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(current, fore_thread, False)


def _tap_chord(modifier: int, key: int) -> None:
    user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_ulonglong]
    user32.keybd_event(modifier, 0, 0, 0)
    user32.keybd_event(key, 0, 0, 0)
    user32.keybd_event(key, 0, 0x0002, 0)
    user32.keybd_event(modifier, 0, 0x0002, 0)


def _close_browser_tab(hwnd: int) -> None:
    if not hwnd:
        return
    if int(user32.GetForegroundWindow() or 0) != int(hwnd):
        _focus_window(hwnd)
    _tap_chord(0x11, 0x57)


def _finish_close(pid: int, hwnd: int, token: int) -> None:
    try:
        time.sleep(3)
        if token != _close_token:
            return
        PROCESS_TERMINATE = 0x0001
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE, False, pid)
        if not handle:
            return
        try:
            code = wintypes.DWORD()
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
                return
            if hwnd:
                user32.ShowWindow(hwnd, 0)
            kernel32.TerminateProcess(handle, 1)
            user32.ChangeDisplaySettingsW.argtypes = [ctypes.c_void_p, wintypes.DWORD]
            user32.ChangeDisplaySettingsW.restype = ctypes.c_long
            user32.ChangeDisplaySettingsW(None, 0)
        finally:
            kernel32.CloseHandle(handle)
    finally:
        if token == _close_token:
            _pending_closes.discard(pid)


def _page_address(hwnd: int, process: str, title: str = "") -> str:
    return read_address(hwnd, process, title)


_path_cache_pid = 0
_path_cache_value = ""


def get_sample() -> Sample:
    idle = _idle_seconds()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return Sample("", "", idle, 0)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    path = _process_path_cached(int(pid.value))
    name = Path(path).name if path else ""
    title = _window_title(hwnd)
    # Browsers are never treated as fullscreen games; skip the monitor check.
    fullscreen = False if (name or "").casefold() in {
        "firefox.exe", "chrome.exe", "msedge.exe", "brave.exe",
        "opera.exe", "vivaldi.exe", "waterfox.exe", "librewolf.exe",
    } else _covers_screen(hwnd)
    return Sample(
        name,
        title,
        idle,
        int(pid.value),
        path,
        fullscreen,
        int(hwnd),
        _page_address(int(hwnd), name, title),
    )


def _process_path_cached(pid: int) -> str:
    global _path_cache_pid, _path_cache_value
    if pid and pid == _path_cache_pid:
        return _path_cache_value
    path = _process_path(pid)
    _path_cache_pid = pid
    _path_cache_value = path
    return path


def _idle_seconds() -> float:
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    elapsed = (kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
    return elapsed / 1000.0


_title_buffer = ctypes.create_unicode_buffer(512)
_path_buffer = ctypes.create_unicode_buffer(32768)


def _window_title(hwnd) -> str:
    _title_buffer.value = ""
    user32.GetWindowTextW(hwnd, _title_buffer, 512)
    return " ".join(_title_buffer.value.split())[:300]


def _process_path(pid: int) -> str:
    if not pid:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(len(_path_buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, _path_buffer, ctypes.byref(size)):
            return ""
        return _path_buffer.value[: size.value]
    finally:
        kernel32.CloseHandle(handle)


def _covers_screen(hwnd) -> bool:
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    monitor = user32.MonitorFromWindow(hwnd, 2)
    if not monitor:
        return False
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    screen = info.rcMonitor
    slack = 16
    return (
        abs(rect.left - screen.left) <= slack
        and abs(rect.top - screen.top) <= slack
        and abs(rect.right - screen.right) <= slack
        and abs(rect.bottom - screen.bottom) <= slack
    )


# Title-bar logo items. Windows uses the low 4 bits of a system command itself,
# so each id is a multiple of 16. Passing one of these to the default window
# procedure destroys the window, which is why a choice must be swallowed here.
LOGO_COMMANDS = frozenset({0x0100, 0x0110, 0x0120, 0x0130, 0x0140, 0x0150, 0x0160})


def logo_command(wparam: int) -> int:
    """The logo-menu id in a system command, or 0 when it is a normal command."""
    command = int(wparam) & 0xFFF0
    if command in LOGO_COMMANDS:
        return command
    return 0


def watch_shutdown(root, callback) -> None:
    """Save when Windows is shutting down or the user is logging off.

    The same window procedure accepts a left-click on the title-bar logo.
    It only stores the choice. The window toolkit must not be called from here.
    """
    session = ctypes.WinDLL("user32", use_last_error=True)
    query_end = 0x0011
    end_session = 0x0016
    sys_command = 0x0112
    window_proc_index = -4
    result_type = ctypes.c_ssize_t
    proc_type = ctypes.WINFUNCTYPE(result_type, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    session.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    session.GetWindowLongPtrW.restype = ctypes.c_void_p
    session.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    session.SetWindowLongPtrW.restype = ctypes.c_void_p
    session.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    session.CallWindowProcW.restype = result_type
    session.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    session.GetAncestor.restype = wintypes.HWND

    client = int(root.winfo_id() or 0)
    frame = int(session.GetAncestor(client, 2) or 0) if client else 0
    handles = []
    for hwnd in (frame, client):
        if hwnd and hwnd not in handles:
            handles.append(hwnd)

    def make_proc(previous):
        def proc(window, msg, wparam, lparam):
            if msg in (query_end, end_session):
                try:
                    callback()
                except Exception:
                    pass
            elif msg == sys_command:
                command = logo_command(int(wparam))
                if command:
                    # Remember the choice and stop. Calling into the window
                    # toolkit here, or letting the default handler see the
                    # command, closes the application.
                    root._pending_logo_command = command
                    return 0
            return session.CallWindowProcW(previous, window, msg, wparam, lparam)

        return proc

    root._shutdown_procs = []
    for hwnd in handles:
        previous = session.GetWindowLongPtrW(wintypes.HWND(hwnd), window_proc_index)
        if not previous:
            continue
        wrapped = proc_type(make_proc(previous))
        root._shutdown_procs.append(wrapped)
        session.SetWindowLongPtrW(
            wintypes.HWND(hwnd),
            window_proc_index,
            ctypes.cast(wrapped, ctypes.c_void_p).value,
        )


def install_logo_menu(root, items: list[tuple[int, str]]) -> None:
    """Put commands at the top of the menu opened by the title-bar logo."""
    session = ctypes.WinDLL("user32", use_last_error=True)
    session.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    session.GetAncestor.restype = wintypes.HWND
    session.GetSystemMenu.argtypes = [wintypes.HWND, wintypes.BOOL]
    session.GetSystemMenu.restype = wintypes.HMENU
    session.InsertMenuW.argtypes = [
        wintypes.HMENU,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_size_t,
        wintypes.LPCWSTR,
    ]
    session.InsertMenuW.restype = wintypes.BOOL
    client = int(root.winfo_id() or 0)
    frame = int(session.GetAncestor(client, 2) or 0) if client else 0
    handles = []
    for hwnd in (frame, client):
        if hwnd and hwnd not in handles:
            handles.append(hwnd)
    by_position = 0x0400
    separator = 0x0800
    for hwnd in handles:
        window = wintypes.HWND(hwnd)
        session.GetSystemMenu(window, True)
        menu = session.GetSystemMenu(window, False)
        if not menu:
            continue
        for index, (command, label) in enumerate(items):
            session.InsertMenuW(menu, index, by_position, command, label)
        session.InsertMenuW(menu, len(items), by_position | separator, 0, None)
