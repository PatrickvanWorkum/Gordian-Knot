"""Window titles, with the browser name taken off so a page can stand alone.

"YouTube — Mozilla Firefox" becomes YouTube. That page name is the activity
row, the Apps bar, and the visit key for a browser page that is not a known
reward site.
"""

from __future__ import annotations

import re


_BROWSERS = (
    "Mozilla Firefox",
    "Google Chrome",
    "Microsoft Edge",
    "Brave",
    "Opera",
    "Vivaldi",
    "Waterfox",
    "LibreWolf",
)

_SEPARATORS = (" \u2014 ", " \u2013 ", " - ")

_GENERIC = {name.casefold() for name in _BROWSERS}
_GENERIC.update({"firefox", "chrome", "edge", "brave", "opera"})


def page_label(title: str) -> str:
    text = re.sub(r"^\(\d+\)\s*", "", " ".join((title or "").split()))
    for suffix in _BROWSERS:
        for separator in _SEPARATORS:
            tail = separator + suffix
            if text.endswith(tail):
                return text[: -len(tail)].strip()
    return text


def activity_name(process: str, title: str) -> str:
    page = page_label(title)
    process_name = process or ""
    if page and page.casefold() not in _GENERIC and page.casefold() != process_name.casefold():
        return page
    return process_name or "Unknown window"
