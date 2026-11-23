"""Turn the foreground window into earn, spend, idle, paused, or neutral.

Order matters. A productive process wins first, so Cursor stays work even when
a filename contains "youtube". A productive title keyword is next, so a browser
page you marked as work counts as work. Then reward processes, reward title
keywords, and the game check. Anything left is neutral and does not move the
balance.

Spend ignores idle time. Earn stops when the keyboard and mouse have been
still for idle_seconds. The lock screen is not itself a program you chose: it
keeps the previous spend going, and turns work into idle so a locked laptop
does not earn.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from gordian.address import site_match, website_name
from gordian.settings import Settings
from gordian.titles import page_label
from gordian.words import _letters, listed_in


LOCK_PROCESSES = frozenset({"lockapp.exe", "logonui.exe"})


@dataclass(frozen=True)
class Decision:
    kind: str
    process: str
    title: str
    locked: bool = False


@dataclass
class Memory:
    kind: str = "neutral"
    process: str = ""
    title: str = ""

    def remember(self, decision: Decision) -> None:
        self.kind = decision.kind
        self.process = decision.process
        self.title = decision.title


def classify(
    process: str,
    title: str,
    settings: Settings,
    path: str = "",
    fullscreen: bool = False,
    url: str = "",
) -> str:
    # Productive process, then productive page, then reward process, reward page, then game.
    if _matches(process, settings.productive_processes):
        return "productive"
    folded = f"{title} {site_match(url)}"
    letters = _letters(folded) if settings.productive_title_keywords or settings.reward_title_keywords else ""
    if any(listed_in(folded, keyword, letters) for keyword in settings.productive_title_keywords):
        return "productive"
    if _matches(process, settings.reward_processes):
        return "reward"
    if any(listed_in(folded, keyword, letters) for keyword in settings.reward_title_keywords):
        return "reward"
    if _is_game(process, path, fullscreen, settings):
        return "reward"
    return "neutral"


def _shown_title(process: str, title: str, url: str, settings: Settings) -> str:
    if not _matches(process, settings.browser_processes):
        return title
    page = page_label(title)
    if "youtube" in page.casefold():
        return title
    return website_name(url) or title


def segment_identity(process: str, title: str, settings: Settings) -> str:
    if not _matches(process, settings.browser_processes):
        return ""
    return page_label(title).casefold()


def _is_game(process: str, path: str, fullscreen: bool, settings: Settings) -> bool:
    if not process:
        return False
    if _matches(process, settings.browser_processes) or _matches(process, settings.ignored_processes):
        return False
    folded = path.replace("/", "\\").casefold()
    for marker in settings.game_path_markers:
        text = marker.replace("/", "\\").casefold()
        if text and text in folded:
            return True
    return fullscreen


def focus_of(category: str, idle_seconds: float, idle_limit: float) -> str:
    """Pause helper: productive / idle / unproductive / other from one classify."""
    if category == "reward":
        return "unproductive"
    if category == "productive" and idle_seconds < idle_limit:
        return "productive"
    if category == "productive":
        return "idle"
    return "other"


def resolve(sample, settings: Settings, memory: Memory, paused: bool, category: str | None = None) -> Decision:
    decision = _decision(sample, settings, memory, category)
    if paused:
        return Decision("paused", decision.process, decision.title, decision.locked)
    return decision


def _decision(sample, settings: Settings, memory: Memory, category: str | None = None) -> Decision:
    process = sample.process or ""
    title = sample.title or ""
    if process.casefold() in LOCK_PROCESSES:
        if memory.kind == "spend" and memory.process:
            return Decision("spend", memory.process, memory.title, True)
        return Decision("idle", process or "LockApp.exe", "Windows lock screen", True)

    path = getattr(sample, "path", "") or ""
    fullscreen = bool(getattr(sample, "fullscreen", False))
    url = getattr(sample, "url", "") or ""
    if category is None:
        category = classify(process, title, settings, path=path, fullscreen=fullscreen, url=url)
    if category == "reward":
        kind = "spend"
    elif sample.idle_seconds >= settings.idle_seconds:
        kind = "idle"
    elif category == "productive":
        kind = "earn"
    else:
        kind = "neutral"
    decision = Decision(kind, process, _shown_title(process, title, url, settings), False)
    memory.remember(decision)
    return decision


_match_cache: dict[tuple[str, tuple[str, ...]], bool] = {}


def _matches(process: str, patterns: list[str]) -> bool:
    name = process.casefold()
    key = (name, tuple(patterns))
    cached = _match_cache.get(key)
    if cached is not None:
        return cached
    found = False
    for pattern in patterns:
        text = pattern.strip().casefold()
        if not text:
            continue
        if "*" in text or "?" in text or "[" in text:
            if fnmatch.fnmatch(name, text):
                found = True
                break
        elif name == text:
            found = True
            break
    if len(_match_cache) > 512:
        _match_cache.clear()
    _match_cache[key] = found
    return found
