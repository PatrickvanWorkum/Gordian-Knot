"""The sound played when a warning window appears.

An empty path is the Windows warning beep. A .wav path is played instead.
A missing file falls back to the beep.
"""

from __future__ import annotations

import os
import winsound


def sound_label(path: str) -> str:
    chosen = (path or "").strip()
    if chosen and os.path.isfile(chosen):
        return os.path.basename(chosen)
    return "Windows beep"


def play_warning(path: str) -> None:
    chosen = (path or "").strip()
    if chosen and os.path.isfile(chosen):
        try:
            winsound.PlaySound(
                chosen,
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
            return
        except RuntimeError:
            pass
    try:
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except RuntimeError:
        return
