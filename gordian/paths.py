"""Where files live, and a save that will not replace a good file with a blank one.

The project folder holds config.json and the code. %APPDATA%\\GordianKnot holds
state.json, state.json.bak, history.jsonl, days\\, error.log, and the icon
used by the Start menu shortcut.

atomic_write checks the bytes after they are flushed, then replaces the real
file. It also copies those bytes to a .bak. A file of zeros is refused, which
is what used to wipe a day and leave the "could not be read" line.
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    root = os.environ.get("APPDATA")
    base = Path(root) if root else Path.home() / "AppData" / "Roaming"
    path = base / "GordianKnot"
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write(path: Path, text: str) -> None:
    data = text.encode("utf-8")
    if not data.strip(b"\x00 \t\r\n"):
        raise ValueError(f"refusing to replace {path.name} with an empty file")
    _replace_bytes(path, data, keep_backup=True)


def _replace_bytes(path: Path, data: bytes, keep_backup: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary.read_bytes() != data:
            raise OSError(f"{path.name} was not written completely")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    if not keep_backup:
        return
    try:
        _replace_bytes(path.with_name(path.name + ".bak"), data, keep_backup=False)
    except OSError:
        return


def log_exception(context: str, exc_info=None) -> None:
    info = exc_info if exc_info and exc_info[0] else sys.exc_info()
    try:
        path = data_dir() / "error.log"
        with path.open("a", encoding="utf-8") as handle:
            stamp = datetime.now().isoformat(timespec="seconds")
            handle.write(f"\n--- {stamp} {context} ---\n")
            if info[0] is not None:
                traceback.print_exception(*info, file=handle)
    except Exception:
        return
    if info[0] is not None:
        try:
            traceback.print_exception(*info)
        except Exception:
            return
