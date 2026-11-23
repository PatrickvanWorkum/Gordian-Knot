"""What to say, and when, once free time is used up.

A visit is one reward thing in front of you. Browsers become site:<keyword>
when the title contains a reward keyword, so every YouTube tab is one visit.
Other browser pages are page:<title>. Other programs are app:<exe>.

advance_overtime walks the stages for that visit. Stage 1 is the first
warning, as soon as remaining is at or below zero. Stage 2 is the second
warning (SECOND_WARNING, one minute). Stage 3 closes the window (CLOSE_AFTER,
five minutes). Switching to a different
unproductive program flashes the window again and keeps the same clock.
Opening a visit that was already closed returns "blocked", then "again" so
the close is retried until that program is gone. "hold" keeps the stage alive
across a one-sample focus blip so the window is not destroyed and rebuilt.

The overlay text does not name the program. The schedule lock message does,
because that one is a separate minimize, not this timed close.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from gordian.formatting import format_duration


from gordian.titles import activity_name, page_label
from gordian.words import listed_in


STEAM_PROCESSES = frozenset({"steam.exe", "steamwebhelper.exe"})


@dataclass(frozen=True)
class WarningDecision:
    warned: bool
    show: bool


def reward_label(process: str, title: str) -> str:
    name = (process or "").casefold()
    if name in STEAM_PROCESSES or name.startswith("steam"):
        return "Steam"
    if "youtube" in (title or "").casefold():
        return "YouTube"
    page = page_label(title)
    if page and len(page) <= 48 and page.casefold() not in {"mozilla firefox", "google chrome"}:
        return page
    display = process or "This program"
    if display.casefold().endswith(".exe"):
        display = display[:-4]
    return display


def lockout_message(label: str) -> str:
    return f"{label} is locked during this part of the schedule."


def schedule_lockdown_message() -> str:
    return "This part of the schedule is in lockdown. Unproductive programs stay closed."


def warning_message(remaining: float) -> str:
    if remaining < 0:
        over = format_duration(abs(remaining))
        return (
            "Free time is used up, "
            f"and the balance is overspent by {over}. "
            "Earn more in a work program first."
        )
    return "Free time is used up. Earn more in a work program first."


def blocked_message(remaining: float) -> str:
    if remaining < 0:
        over = format_duration(abs(remaining))
        return (
            "Free time is used up, "
            f"and the balance is overspent by {over}. "
            "This program stays closed until that overspent time is earned back."
        )
    return "Free time is used up. This program stays closed until that overspent time is earned back."


def overlay_text(remaining: float, stage: int, seconds_left: float) -> str:
    if stage >= 4:
        return blocked_message(remaining)
    balance = warning_message(remaining)
    if stage >= 3:
        return f"{balance} The program in front is closing."
    if stage >= 2:
        left = format_duration(max(seconds_left, 0))
        return f"{balance} It will close in {left} if it stays in front."
    return balance


SECOND_WARNING = 60
CLOSE_AFTER = 300


@dataclass
class Overtime:
    key: str = ""
    started: float | None = None
    stage: int = 0


def second_warning_message(remaining: float, seconds_left: float = 240) -> str:
    return overlay_text(remaining, 2, seconds_left)


def closing_message(remaining: float = 0) -> str:
    return overlay_text(remaining, 3, 0)


def visit_key(process: str, title: str, browsers: list[str], keywords: list[str]) -> str:
    name = (process or "").casefold()
    if name and any(fnmatch.fnmatch(name, item.strip().casefold()) for item in browsers if item.strip()):
        for keyword in keywords:
            text = keyword.strip().casefold()
            if text and listed_in(title, text):
                return f"site:{text}"
        page = page_label(title).casefold()
        return f"page:{page or name}"
    return f"app:{name}"


def may_force_close(remaining: float, lockdown: bool) -> bool:
    """A reward window stays open while free time is left, unless a block is in lockdown."""
    return bool(lockdown) or remaining <= 0


def advance_overtime(
    kind: str,
    remaining: float,
    paused: bool,
    key: str,
    now: float,
    state: Overtime,
    dialog_open: bool,
    barred: set[str] | None = None,
    lockdown: bool = False,
    hold: bool = False,
) -> tuple[Overtime, str]:
    # Free time, or a pause, ends the timer. A warning window that has the focus
    # must not keep counting down and close Steam once time is available again.
    if paused or remaining > 0:
        return Overtime(), ""
    active = (not paused) and kind == "spend" and remaining <= 0 and bool(key)
    blocked = barred or set()
    if active and (lockdown or key in blocked):
        if state.key == key and state.stage >= 4:
            return Overtime(key, state.started or now, 4), "again"
        return Overtime(key, now, 4), "blocked"
    if not active:
        if (hold or dialog_open) and state.started is not None and not paused:
            elapsed = now - state.started
            if elapsed >= CLOSE_AFTER and state.stage < 3:
                return Overtime(state.key, state.started, 3), "close"
            return state, ""
        return Overtime(), ""
    if state.started is not None and state.key and key and state.key != key:
        return Overtime(key, state.started, state.stage or 1), "flash"
    if state.key != key or state.started is None:
        if dialog_open:
            return state, ""
        return Overtime(key, now, 1), "warn"
    elapsed = now - state.started
    if elapsed >= CLOSE_AFTER and state.stage < 3:
        return Overtime(key, state.started, 3), "close"
    if state.stage >= 3 and state.key == key:
        return state, "again"
    if elapsed >= SECOND_WARNING and state.stage < 2:
        return Overtime(key, state.started, 2), "warn-again"
    return state, ""


def advance_warning(
    kind: str,
    remaining: float,
    already_warned: bool,
    dialog_open: bool,
    on_reward: bool,
) -> WarningDecision:
    if not on_reward:
        return WarningDecision(already_warned if dialog_open else False, False)
    if kind != "spend":
        return WarningDecision(already_warned, False)
    show = remaining <= 0 and not already_warned and not dialog_open
    return WarningDecision(True if show else already_warned, show)
