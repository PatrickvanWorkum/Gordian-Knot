"""Wall clock from the operating system (Settings → time & date)."""

from __future__ import annotations

from datetime import datetime


def local_now() -> datetime:
    """The PC's local date and time, including any manual clock or time-zone setting."""
    return datetime.now()