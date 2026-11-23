"""The site name comes from the address, not the tab title.

A login page is often titled "Login". The host after https:// and a leading
www is the website, then split into words: https://www.interactivebrokers.co.uk
is Interactive Brokers.
The label before .co.uk, .com.au, and the other two-part endings is the name,
so a login.example.co.uk page is still Example.

The address is read from the browser's own address box (the toolbar control
named with "address"). The page underneath is not walked, and the full address
is not saved.
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes

_SUFFIXES = {
    "co.uk",
    "org.uk",
    "ac.uk",
    "gov.uk",
    "com.au",
    "net.au",
    "org.au",
    "co.nz",
    "com.br",
    "co.jp",
    "ne.jp",
    "or.jp",
    "co.za",
    "com.sg",
    "com.hk",
    "co.in",
    "com.mx",
    "com.tr",
    "co.kr",
    "com.tw",
    "com.ar",
}

_BROWSERS = {
    "firefox.exe",
    "chrome.exe",
    "msedge.exe",
    "brave.exe",
    "opera.exe",
    "vivaldi.exe",
    "waterfox.exe",
    "librewolf.exe",
}

_ADDRESS_WORDS = ("address", "location", "url")
_TOOLBAR = 50021
_COMBO = 50003
_EDIT = 50004
_DOCUMENT = 50030
_NAME = 30005
_CONTROL = 30003
_VALUE = 30045
_PASSWORD = 30019


def capitalise_name(text: str) -> str:
    from gordian.words import split_words

    cleaned = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text or "")
    cleaned = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", cleaned)
    cleaned = cleaned.replace("_", " ").replace("-", " ")
    words: list[str] = []
    for part in cleaned.split():
        if part.isdigit():
            words.append(part)
            continue
        for piece in split_words(part):
            words.append(piece[:1].upper() + piece[1:])
    return " ".join(words)


def site_token(url: str) -> str:
    host = _host(url)
    if host.startswith("www."):
        host = host[4:]
    labels = [part for part in host.split(".") if part]
    if not labels:
        return ""
    if len(labels) >= 3 and ".".join(labels[-2:]) in _SUFFIXES:
        return labels[-3]
    if len(labels) >= 2:
        return labels[-2]
    return labels[0]


def page_host(url: str) -> str:
    return _host(url)


def website_name(url: str) -> str:
    return capitalise_name(site_token(url))


def site_match(url: str) -> str:
    """Token plus host, so a saved domain and the short name both match."""
    host = _host(url)
    if host.startswith("www."):
        host = host[4:]
    token = site_token(url)
    if host and token and host != token:
        return f"{token} {host}"
    return token or host


# UI Automation can hang for minutes on some browser windows. The tick never
# waits on it. A single worker thread reads the bar, and anything slower than
# _FETCH_SECONDS is abandoned so a stuck call cannot pile up forever.
_REFRESH_SECONDS = 30.0
_FETCH_SECONDS = 1.5
_cache_lock = threading.Lock()
_cache: dict[int, tuple[str, str, float]] = {}
_pending: set[int] = set()
_wake = threading.Event()
_jobs: list[tuple[int, str, str]] = []
_worker_started = False


def read_address(hwnd: int, process: str = "", title: str = "") -> str:
    """Return the address-bar URL for a browser window without blocking the tick.

    The first call for a window may return "" while a worker reads the bar.
    Later ticks reuse the cached value until the title changes, or until a
    quiet refresh after _REFRESH_SECONDS.
    """
    if not hwnd or (process or "").casefold() not in _BROWSERS:
        return ""
    key = int(hwnd)
    shown = " ".join((title or "").split())
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            cached_title, url, stamped = cached
            if cached_title == shown:
                due = _REFRESH_SECONDS if url else 2.0
                if key not in _pending and now - stamped >= due:
                    _queue(key, process, shown)
                return url
            if key in _pending:
                return url
        elif key in _pending:
            return ""
        _queue(key, process, shown)
    return cached[1] if cached is not None and cached[0] == shown else ""


def _queue(hwnd: int, process: str, title: str) -> None:
    global _worker_started
    _pending.add(hwnd)
    _jobs.append((hwnd, process, title))
    if not _worker_started:
        _worker_started = True
        threading.Thread(target=_worker, name="gordian-address", daemon=True).start()
    _wake.set()


def _worker() -> None:
    while True:
        _wake.wait()
        _wake.clear()
        while True:
            with _cache_lock:
                if not _jobs:
                    break
                hwnd, process, title = _jobs.pop(0)
            url = _fetch_limited(hwnd)
            with _cache_lock:
                previous = _cache.get(hwnd)
                if previous is not None and previous[0] == title and not url:
                    url = previous[1]
                _cache[hwnd] = (title, url, time.monotonic())
                _pending.discard(hwnd)
                if len(_cache) > 32:
                    oldest = sorted(_cache.items(), key=lambda item: item[1][2])[:8]
                    for key, _value in oldest:
                        if key != hwnd:
                            _cache.pop(key, None)


def _fetch_limited(hwnd: int) -> str:
    # UI Automation inside this process can freeze the whole app. A short-lived
    # child process is killed if it does not finish in time.
    from gordian.paths import project_root

    env = os.environ.copy()
    root = str(project_root())
    site = os.path.join(root, ".venv", "Lib", "site-packages")
    env["PYTHONPATH"] = os.pathsep.join((root, site, env.get("PYTHONPATH", "")))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "gordian.address", str(int(hwnd))],
            capture_output=True,
            text=True,
            timeout=_FETCH_SECONDS,
            creationflags=flags,
            env=env,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    if completed.returncode != 0:
        return ""
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _host(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    lowered = text.casefold()
    if lowered.startswith(("about:", "chrome:", "edge:", "brave:", "opera:", "moz-extension:", "view-source:")):
        return ""
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/")[0].split("?")[0].split("#")[0]
    if "@" in text:
        text = text.rsplit("@", 1)[-1]
    return text.split(":")[0].strip().strip(".").casefold()


class _Union(ctypes.Union):
    _fields_ = [("lVal", ctypes.c_int), ("bstrVal", ctypes.c_void_p)]


class _Variant(ctypes.Structure):
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("r1", ctypes.c_ushort),
        ("r2", ctypes.c_ushort),
        ("r3", ctypes.c_ushort),
        ("u", _Union),
    ]


_thread_local = threading.local()


def _services():
    # UI Automation objects stay on the thread that created them.
    auto = getattr(_thread_local, "auto", None)
    condition = getattr(_thread_local, "condition", None)
    if auto is not None and auto.value and condition is not None and condition.value:
        return auto, condition
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    ole32.CoInitialize.argtypes = [ctypes.c_void_p]
    ole32.CoInitialize.restype = ctypes.HRESULT
    ole32.CoInitialize(None)
    ole32.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    ole32.CLSIDFromString.restype = ctypes.HRESULT
    ole32.CoCreateInstance.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.HRESULT

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    clsid = GUID()
    iid = GUID()
    if ole32.CLSIDFromString("{ff48dba4-60ef-4201-aa87-54103eef594e}", ctypes.byref(clsid)) < 0:
        return None
    if ole32.CLSIDFromString("{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}", ctypes.byref(iid)) < 0:
        return None
    created = ctypes.c_void_p()
    if ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(created)) < 0 or not created.value:
        return None
    condition = ctypes.c_void_p()
    if _invoke(created, 21, ctypes.POINTER(ctypes.c_void_p), ctypes.byref(condition)) < 0 or not condition.value:
        _release(created)
        return None
    auto = ctypes.c_void_p(created.value)
    _thread_local.auto = auto
    _thread_local.condition = condition
    return auto, condition


def _invoke(ptr, index: int, *argtypes_and_args):
    argtypes = argtypes_and_args[: len(argtypes_and_args) // 2]
    args = argtypes_and_args[len(argtypes_and_args) // 2 :]
    table = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    function = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(table[index])
    return function(ptr, *args)


def _release(ptr) -> None:
    if not ptr or not getattr(ptr, "value", None):
        return
    table = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])(ptr)
    ptr.value = 0


def _text(element) -> str:
    value = _property(element, _NAME)
    try:
        if value.vt == 8 and value.u.bstrVal:
            return ctypes.wstring_at(value.u.bstrVal)
        return ""
    finally:
        _clear(value)


def _number(element, prop: int) -> int:
    value = _property(element, prop)
    try:
        if value.vt in {3, 19, 22, 23}:
            return int(value.u.lVal)
        return -1
    finally:
        _clear(value)


def _property(element, prop: int) -> _Variant:
    value = _Variant()
    _invoke(element, 10, ctypes.c_int, ctypes.POINTER(_Variant), prop, ctypes.byref(value))
    return value


def _clear(value: _Variant) -> None:
    oleaut32 = ctypes.WinDLL("oleaut32")
    oleaut32.VariantClear.argtypes = [ctypes.POINTER(_Variant)]
    oleaut32.VariantClear.restype = ctypes.HRESULT
    oleaut32.VariantClear(ctypes.byref(value))


def _elements(parent, scope: int) -> list:
    services = _services()
    if services is None:
        return []
    _, condition = services
    found = ctypes.c_void_p()
    if _invoke(parent, 6, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), scope, condition, ctypes.byref(found)) < 0 or not found.value:
        return []
    length = ctypes.c_int()
    _invoke(found, 3, ctypes.POINTER(ctypes.c_int), ctypes.byref(length))
    items = []
    for index in range(min(int(length.value), 40)):
        item = ctypes.c_void_p()
        if _invoke(found, 4, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), index, ctypes.byref(item)) >= 0 and item.value:
            items.append(item)
    _release(found)
    return items


def _address_value(element) -> str:
    name = _text(element).casefold()
    if not any(word in name for word in _ADDRESS_WORDS):
        return ""
    if _number(element, _PASSWORD) == 1:
        return ""
    value = _property(element, _VALUE)
    try:
        if value.vt == 8 and value.u.bstrVal:
            text = " ".join(ctypes.wstring_at(value.u.bstrVal).split())
        else:
            text = ""
    finally:
        _clear(value)
    if not text or " " in text:
        return ""
    lowered = text.casefold()
    if lowered.startswith(("http://", "https://", "www.")) or "." in text:
        return text
    return ""


def _read_address(hwnd: int) -> str:
    services = _services()
    if services is None:
        return ""
    auto, _condition = services
    root = ctypes.c_void_p()
    if _invoke(auto, 6, wintypes.HWND, ctypes.POINTER(ctypes.c_void_p), hwnd, ctypes.byref(root)) < 0 or not root.value:
        return ""
    found = ""
    children = _elements(root, 2)
    try:
        for child in children:
            if found or _number(child, _CONTROL) != _TOOLBAR:
                continue
            items = _elements(child, 4)
            try:
                for item in items:
                    kind = _number(item, _CONTROL)
                    if kind == _DOCUMENT or kind not in {_COMBO, _EDIT}:
                        continue
                    found = _address_value(item)
                    if found:
                        break
            finally:
                for item in items:
                    _release(item)
    finally:
        for child in children:
            _release(child)
        _release(root)
    return found

def _cli_read() -> None:
    if len(sys.argv) < 2:
        return
    try:
        hwnd = int(sys.argv[1])
    except ValueError:
        return
    try:
        ole32 = ctypes.WinDLL('ole32', use_last_error=True)
        ole32.CoInitialize.argtypes = [ctypes.c_void_p]
        ole32.CoInitialize.restype = ctypes.HRESULT
        ole32.CoInitialize(None)
        print(_read_address(hwnd))
    except Exception:
        return


if __name__ == '__main__':
    _cli_read()
