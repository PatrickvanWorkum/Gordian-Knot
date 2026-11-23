"""A GordianKnot.exe launcher so Task Manager and the taskbar say Gordian Knot.

Windows labels a process from the exe FileDescription. Running as pythonw.exe
always shows as Python. This copies the venv pythonw into GordianKnot.exe and
rewrites FileDescription and ProductName with UpdateResource.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import struct
import sys
from ctypes import wintypes
from pathlib import Path

from gordian.app_icon import embed_icon_in_exe, ensure_application_icon
from gordian.paths import data_dir, log_exception, project_root

_ICON_STAMP_VERSION = "1"

APP_NAME = "Gordian Knot"
EXE_NAME = "GordianKnot.exe"


def ensure_branded_launcher() -> Path | None:
    """Return GordianKnot.exe, creating or refreshing it when needed."""
    if os.name != "nt":
        return None
    try:
        return _ensure()
    except Exception:
        log_exception("branding")
        return None


def pythonpath_for_branded() -> str:
    """Project folder + venv site-packages, so the base pythonw can import gordian."""
    root = project_root()
    site = root / ".venv" / "Lib" / "site-packages"
    return os.pathsep.join((str(root), str(site)))


def relaunch_as_branded() -> None:
    """Replace a venv pythonw with GordianKnot.exe so Task Manager shows the app name.

    GordianKnot.exe is a stamped copy of the *base* pythonw. The venv launcher
    stub must not be copied: that stub always spawns a child pythonw, which is
    what showed up as Python and left a second process hanging.

    No-op when already running as GordianKnot.exe. Uses GORDIAN_BRANDED so a
    failed re-exec cannot loop.
    """
    if os.name != "nt":
        return
    if os.environ.get("GORDIAN_BRANDED") == "1":
        return
    try:
        branded = ensure_branded_launcher()
    except Exception:
        log_exception("branding")
        return
    if branded is None or not branded.exists():
        return
    current = Path(sys.executable).resolve()
    if current.name.casefold() == EXE_NAME.casefold():
        return
    if current == branded.resolve():
        return
    env = os.environ.copy()
    env["GORDIAN_BRANDED"] = "1"
    env["PYTHONPATH"] = pythonpath_for_branded()
    args = [str(branded), "-m", "gordian", *sys.argv[1:]]
    try:
        os.chdir(project_root())
        os.execve(str(branded), args, env)
    except OSError:
        log_exception("branded relaunch")


def _ensure() -> Path | None:
    source = _base_pythonw()
    if source is None:
        return None
    # Keep the branded exe next to the venv scripts so run.bat can find it.
    target = project_root() / ".venv" / "Scripts" / EXE_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    need_copy = not target.exists() or target.stat().st_mtime < source.stat().st_mtime
    ico = ensure_application_icon()
    if need_copy:
        if target.exists():
            try:
                target.unlink()
            except OSError:
                return target if _description_is_ours(target) else None
        shutil.copy2(source, target)
        try:
            _stamp_version(target, source)
        except Exception:
            log_exception("branding")
        _stamp_icon(target, ico)
    elif not _description_is_ours(target):
        try:
            _stamp_version(target, source)
        except OSError:
            return target
        _stamp_icon(target, ico)
    elif ico is not None and ico.exists() and ico.stat().st_mtime > target.stat().st_mtime:
        _stamp_icon(target, ico)
    elif _description_is_ours(target):
        _maybe_stamp_icon(target, ico)
    return target


def _maybe_stamp_icon(target: Path, ico: Path | None) -> None:
    marker = data_dir() / "icon_stamp.version"
    try:
        stamped = marker.read_text(encoding="utf-8").strip()
    except OSError:
        stamped = ""
    if stamped == _ICON_STAMP_VERSION:
        return
    if _stamp_icon(target, ico):
        try:
            marker.write_text(_ICON_STAMP_VERSION, encoding="utf-8")
        except OSError:
            return


def _stamp_icon(target: Path, ico: Path | None) -> bool:
    if ico is None or not ico.is_file():
        return False
    try:
        embed_icon_in_exe(target, ico)
    except Exception:
        log_exception("branding icon")
        return False
    return True


def _base_pythonw() -> Path | None:
    """The real pythonw.exe, not the venv redirector stub."""
    base = getattr(sys, "_base_executable", None) or ""
    if base:
        path = Path(base)
        if path.name.casefold() == "python.exe":
            sibling = path.with_name("pythonw.exe")
            if sibling.exists():
                return sibling
        if path.name.casefold() == "pythonw.exe" and path.exists():
            return path
    cfg = project_root() / ".venv" / "pyvenv.cfg"
    if cfg.exists():
        try:
            for line in cfg.read_text(encoding="utf-8").splitlines():
                if line.lower().startswith("home"):
                    home = Path(line.split("=", 1)[1].strip())
                    candidate = home / "pythonw.exe"
                    if candidate.exists():
                        return candidate
        except OSError:
            pass
    current = Path(sys.executable)
    if current.name.casefold() == "python.exe":
        sibling = current.with_name("pythonw.exe")
        if sibling.exists():
            return sibling
    if current.name.casefold() == "pythonw.exe":
        return current
    return None


def _description_is_ours(path: Path) -> bool:
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
    except Exception:
        return False
    if not size:
        return False
    buffer = ctypes.create_string_buffer(size)
    if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buffer):
        return False
    value = ctypes.c_void_p()
    length = wintypes.UINT()
    translation = ctypes.c_void_p()
    translation_length = wintypes.UINT()
    if not ctypes.windll.version.VerQueryValueW(
        buffer, r"\VarFileInfo\Translation", ctypes.byref(translation), ctypes.byref(translation_length)
    ):
        return False
    if not translation.value or translation_length.value < 4:
        return False
    lang = ctypes.cast(translation.value, ctypes.POINTER(wintypes.WORD))[0]
    code = ctypes.cast(translation.value, ctypes.POINTER(wintypes.WORD))[1]
    path = f"\\StringFileInfo\\{lang:04X}{code:04X}\\FileDescription"
    if not ctypes.windll.version.VerQueryValueW(buffer, path, ctypes.byref(value), ctypes.byref(length)):
        return False
    text = ctypes.wstring_at(value.value) if value.value else ""
    return text == APP_NAME


def _stamp_version(path: Path, source: Path) -> None:
    original = _read_version_resource(source)
    if not original:
        raise OSError("could not read version resource")
    patched = _patch_version_strings(
        original,
        {
            "FileDescription": APP_NAME,
            "ProductName": APP_NAME,
            "InternalName": "GordianKnot",
            "OriginalFilename": EXE_NAME,
        },
    )
    _write_version_resource(path, patched)


def _read_version_resource(path: Path) -> bytes:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    kernel32.LoadLibraryExW.restype = wintypes.HMODULE
    kernel32.FindResourceW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p]
    kernel32.FindResourceW.restype = wintypes.HRSRC
    kernel32.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HRSRC]
    kernel32.SizeofResource.restype = wintypes.DWORD
    kernel32.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HRSRC]
    kernel32.LoadResource.restype = wintypes.HGLOBAL
    kernel32.LockResource.argtypes = [wintypes.HGLOBAL]
    kernel32.LockResource.restype = ctypes.c_void_p
    kernel32.FreeLibrary.argtypes = [wintypes.HMODULE]
    kernel32.FreeLibrary.restype = wintypes.BOOL

    module = kernel32.LoadLibraryExW(str(path), None, 0x00000002)
    if not module:
        raise OSError("LoadLibraryEx failed")
    try:
        resource = kernel32.FindResourceW(module, ctypes.c_void_p(1), ctypes.c_void_p(16))
        if not resource:
            raise OSError("FindResource failed")
        size = kernel32.SizeofResource(module, resource)
        handle = kernel32.LoadResource(module, resource)
        pointer = kernel32.LockResource(handle)
        if not size or not pointer:
            raise OSError("version resource empty")
        return ctypes.string_at(pointer, size)
    finally:
        kernel32.FreeLibrary(module)


def _write_version_resource(path: Path, data: bytes) -> None:
    begin = ctypes.windll.kernel32.BeginUpdateResourceW
    begin.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    begin.restype = wintypes.HANDLE
    update = ctypes.windll.kernel32.UpdateResourceW
    update.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.WORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    update.restype = wintypes.BOOL
    end = ctypes.windll.kernel32.EndUpdateResourceW
    end.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    end.restype = wintypes.BOOL

    handle = begin(str(path), False)
    if not handle:
        raise OSError("BeginUpdateResource failed")
    try:
        blob = ctypes.create_string_buffer(data, len(data))
        if not update(handle, ctypes.c_void_p(16), ctypes.c_void_p(1), 0x0409, blob, len(data)):
            raise OSError(f"UpdateResource failed ({ctypes.get_last_error()})")
        if not end(handle, False):
            raise OSError(f"EndUpdateResource failed ({ctypes.get_last_error()})")
        handle = None
    finally:
        if handle:
            end(handle, True)


def _patch_version_strings(raw: bytes, values: dict[str, str]) -> bytes:
    """Replace string values and rebuild lengths so Windows accepts the resource."""
    found = _string_table(raw)
    if not found:
        return raw
    header, table_key, pairs = found
    updated = [(key, values.get(key, value)) for key, value in pairs]
    table = _build_string_table(table_key, updated)
    return _splice_string_table(raw, header, table)


def _string_table(raw: bytes) -> tuple[int, str, list[tuple[str, str]]] | None:
    """Return (header_offset, table_key, pairs) for the first string table."""
    info_at = raw.find("StringFileInfo\0".encode("utf-16le"))
    if info_at < 0:
        return None
    cursor = info_at + len("StringFileInfo\0".encode("utf-16le"))
    cursor += (4 - (cursor % 4)) % 4
    if cursor + 6 > len(raw):
        return None
    length, _vlen, kind = struct.unpack_from("<HHH", raw, cursor)
    if kind != 1 or length < 6 or cursor + length > len(raw):
        return None
    header = cursor
    end = header + length
    key_at = header + 6
    key_chars: list[str] = []
    while key_at + 2 <= end:
        unit = struct.unpack_from("<H", raw, key_at)[0]
        key_at += 2
        if unit == 0:
            break
        key_chars.append(chr(unit))
    table_key = "".join(key_chars)
    cursor = key_at
    cursor += (4 - (cursor % 4)) % 4
    pairs: list[tuple[str, str]] = []
    while cursor + 6 <= end:
        entry_length, _value_words, entry_kind = struct.unpack_from("<HHH", raw, cursor)
        if entry_length < 6 or cursor + entry_length > end or entry_kind != 1:
            break
        key, value = _read_string_entry(raw, cursor)
        if key:
            pairs.append((key, value))
        cursor += entry_length
        cursor += (4 - (cursor % 4)) % 4
    return header, table_key, pairs


def _string_pairs(raw: bytes) -> list[tuple[str, str]]:
    found = _string_table(raw)
    return found[2] if found else []


def _read_string_entry(raw: bytes, header: int) -> tuple[str, str]:
    length, value_words, _kind = struct.unpack_from("<HHH", raw, header)
    end = header + length
    cursor = header + 6
    key_chars: list[str] = []
    while cursor + 2 <= end:
        unit = struct.unpack_from("<H", raw, cursor)[0]
        cursor += 2
        if unit == 0:
            break
        key_chars.append(chr(unit))
    cursor += (4 - (cursor % 4)) % 4
    value = raw[cursor : cursor + value_words * 2].decode("utf-16le").rstrip("\0")
    return "".join(key_chars), value


def _build_string_table(table_key: str, pairs: list[tuple[str, str]]) -> bytes:
    children = b""
    for key, value in pairs:
        # Each string entry must start on a 4-byte boundary within the table.
        if len(children) % 4:
            children += b"\0" * (4 - (len(children) % 4))
        children += _build_string_entry(key, value)
    return _build_block(table_key, b"", children, text=True)


def _build_string_entry(key: str, value: str) -> bytes:
    value_bytes = (value + "\0").encode("utf-16le")
    return _build_block(key, value_bytes, b"", text=True, value_words=True)


def _build_block(key: str, value: bytes, children: bytes, *, text: bool, value_words: bool = False) -> bytes:
    key_bytes = (key + "\0").encode("utf-16le")
    header = 6
    # Padding is measured from the start of this block, including the 6-byte header.
    pad1 = b"\0" * ((4 - ((header + len(key_bytes)) % 4)) % 4)
    after_value = header + len(key_bytes) + len(pad1) + len(value)
    pad2 = b"\0" * ((4 - (after_value % 4)) % 4) if children else b""
    payload = key_bytes + pad1 + value + pad2 + children
    value_length = len(value) // 2 if value_words else len(value)
    return struct.pack("<HHH", header + len(payload), value_length, 1 if text else 0) + payload


def _splice_string_table(raw: bytes, old_header: int, table: bytes) -> bytes:
    var_key = raw.find("VarFileInfo\0".encode("utf-16le"))
    if var_key < 6:
        return raw
    var_header = var_key - 6
    # Children must sit on a 4-byte boundary. Keep VarFileInfo intact and only
    # replace the string table, with fresh padding between them.
    pad = (4 - ((old_header + len(table)) % 4)) % 4
    data = raw[:old_header] + table + (b"\0" * pad) + raw[var_header:]

    info_key = data.find("StringFileInfo\0".encode("utf-16le"))
    if info_key >= 6:
        info_header = info_key - 6
        # StringFileInfo covers the table and the padding before VarFileInfo.
        info_length = (old_header + len(table) + pad) - info_header
        root = bytearray(data)
        struct.pack_into("<H", root, info_header, info_length)
        data = bytes(root)

    root = bytearray(data)
    struct.pack_into("<H", root, 0, len(root))
    return bytes(root)
