"""A Start menu shortcut named Gordian Knot, so Windows search can open it.

The shortcut runs GordianKnot.exe (or pythonw) with -m gordian and the project
folder as the working directory. It is refreshed in the background after the
window is up, so a slow shortcut write never freezes startup.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from gordian.app_icon import ensure_application_icon
from gordian.paths import log_exception, project_root


def install_start_menu() -> None:
    if os.name != "nt":
        return
    try:
        _install()
    except Exception:
        log_exception("start menu")


def install_start_menu_soon() -> None:
    """Refresh the shortcut after the window is showing."""
    if os.name != "nt":
        return
    threading.Thread(target=install_start_menu, name="gordian-startmenu", daemon=True).start()


def _install() -> None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return
    folder = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    folder.mkdir(parents=True, exist_ok=True)
    link = folder / "Gordian Knot.lnk"
    # Point at GordianKnot.bat (falls back to run.bat) so PYTHONPATH is set
    # and a single branded process starts.
    root = project_root()
    preferred = root / "GordianKnot.bat"
    target = preferred if preferred.exists() else root / "run.bat"
    icon = _icon_file()
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        "$shortcut = $shell.CreateShortcut($env:GORDIAN_LINK); "
        "$shortcut.TargetPath = $env:GORDIAN_TARGET; "
        "$shortcut.Arguments = ''; "
        "$shortcut.WorkingDirectory = $env:GORDIAN_WORKDIR; "
        "$shortcut.Description = 'Gordian Knot'; "
        "$shortcut.IconLocation = $env:GORDIAN_ICON; "
        "$shortcut.Save()"
    )
    env = os.environ.copy()
    env["GORDIAN_LINK"] = str(link)
    env["GORDIAN_TARGET"] = str(target)
    env["GORDIAN_WORKDIR"] = str(project_root())
    env["GORDIAN_ICON"] = f"{icon},0" if icon else str(target)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env,
            check=False,
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        return
    _notify(link)


def _icon_file() -> Path | None:
    return ensure_application_icon()


def _notify(link: Path) -> None:
    import ctypes

    ctypes.windll.shell32.SHChangeNotify(0x00000002, 0x0005, ctypes.c_wchar_p(str(link)), None)
