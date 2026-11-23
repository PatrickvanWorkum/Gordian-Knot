"""Week blocks: a ratio for some hours, Locked, and Lockdown.

A block is one or more days plus a start and end, down to the minute. A block
that passes midnight is stored as one range and split per day when it is drawn
or checked. Overlaps are resolved when you save: Overwrite keeps the leftover
edges of the old block, Replace drops the old block on the days you picked.

Locked and Lockdown are different. Locked minimizes a reward window and uses
the default ratio. Lockdown closes unproductive programs for those hours and
stops Scan from adding new ones. A block can carry a ratio and Lockdown
together. active_block is the block that sets the ratio. lockdown_active is
true when any matching block has Lockdown ticked, even if another block won
the ratio.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from gordian.formatting import format_ratio, format_ratio_round, parse_ratio


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = {
    "mon": "Mon",
    "tue": "Tue",
    "wed": "Wed",
    "thu": "Thu",
    "fri": "Fri",
    "sat": "Sat",
    "sun": "Sun",
}
DAY_ALIASES = {
    "mon": "mon",
    "monday": "mon",
    "tue": "tue",
    "tues": "tue",
    "tuesday": "tue",
    "wed": "wed",
    "wednesday": "wed",
    "thu": "thu",
    "thur": "thu",
    "thurs": "thu",
    "thursday": "thu",
    "fri": "fri",
    "friday": "fri",
    "sat": "sat",
    "saturday": "sat",
    "sun": "sun",
    "sunday": "sun",
}


# Soft fills so neighbouring blocks can be told apart. Locked blocks stay red.
BLOCK_COLORS = (
    "#a8c5e2",
    "#b7d7c5",
    "#f0c9b0",
    "#d4c4e8",
    "#f3e3a4",
    "#f0b8c4",
    "#a9ddd6",
)


@dataclass(frozen=True)
class Block:
    days: tuple[str, ...]
    start: str
    end: str
    locked: bool
    ratio: str
    lockdown: bool = False
    name: str = ""
    color: int = 0

    def to_json(self) -> dict:
        return {
            "days": list(self.days),
            "start": self.start,
            "end": self.end,
            "locked": self.locked,
            "ratio": self.ratio,
            "lockdown": self.lockdown,
            "name": self.name,
            "color": self.color,
        }


def parse_clock(text: str, *, end: bool = False) -> int | None:
    parts = text.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if minute < 0 or minute > 59 or hour < 0:
        return None
    if hour == 24 and minute == 0 and end:
        return 24 * 60
    if hour > 23:
        return None
    return hour * 60 + minute


def format_clock(minutes: int) -> str:
    if minutes == 24 * 60:
        return "24:00"
    hour, minute = divmod(max(0, minutes), 60)
    return f"{hour:02d}:{minute:02d}"


def parse_blocks(value) -> tuple[Block, ...]:
    if not isinstance(value, list):
        return ()
    blocks = []
    for item in value:
        block = _parse_block(item)
        if block is None:
            continue
        # Older saves have no colour, so each block takes the next pastel.
        if not (isinstance(item, dict) and isinstance(item.get("color"), int) and not isinstance(item.get("color"), bool)):
            block = replace(block, color=len(blocks) % len(BLOCK_COLORS))
        blocks.append(block)
    return tuple(blocks)


def block_fill(color: int) -> str:
    return BLOCK_COLORS[int(color) % len(BLOCK_COLORS)]


def next_block_color(blocks: tuple[Block, ...] | list[Block]) -> int:
    colors = [block.color for block in blocks]
    if not colors:
        return 0
    return (max(colors) + 1) % len(BLOCK_COLORS)


def block_caption(block: Block) -> str:
    name = " ".join((block.name or "").split())
    detail = block_mark(block)
    if name and detail:
        return f"{name}\n{detail}"
    return name or detail


def active_block(blocks: tuple[Block, ...] | list[Block], moment: datetime) -> Block | None:
    locked = None
    rated = None
    for block in blocks:
        if not _matches(block, moment):
            continue
        if block.locked and locked is None:
            locked = block
        elif not block.locked and rated is None:
            rated = block
    return locked or rated


def lockdown_active(blocks: tuple[Block, ...] | list[Block], moment: datetime) -> bool:
    return any(block.lockdown and _matches(block, moment) for block in blocks)


def day_label(days: tuple[str, ...]) -> str:
    chosen = tuple(day for day in DAYS if day in days)
    if chosen == DAYS:
        return "Every day"
    if chosen == DAYS[:5]:
        return "Weekdays"
    if chosen == DAYS[5:]:
        return "Weekend"
    return " ".join(DAY_NAMES[day] for day in chosen)


def week_average_ratio(blocks: tuple[Block, ...] | list[Block]) -> str:
    """Time-weighted W:R across every scheduled span in the week."""
    total = 0.0
    weighted = 0.0
    for span in explode(blocks):
        parsed = parse_ratio(span.ratio)
        if not parsed:
            continue
        work, reward = parsed
        if reward <= 0:
            continue
        minutes = float(span.end - span.start)
        if minutes <= 0:
            continue
        total += minutes
        weighted += minutes * (work / reward)
    if total < 1:
        return "—"
    return format_ratio_round(weighted / total)


def block_mark(block: Block) -> str:
    if block.lockdown:
        return "Lockdown"
    if block.locked:
        return "Locked"
    return block.ratio


def describe_block(block: Block) -> str:
    return f"{day_label(block.days)}   {block.start}–{block.end}   {block_mark(block)}"


def clock_label(minutes: int) -> str:
    if minutes >= 24 * 60:
        minutes = 0
    hour, minute = divmod(max(0, minutes), 60)
    suffix = "am" if hour < 12 else "pm"
    shown = hour % 12 or 12
    if minute == 0:
        return f"{shown}{suffix}"
    return f"{shown}:{minute:02d}{suffix}"


def typed_clock(minutes: int, *, end: bool = False) -> str:
    """The text shown in a time field. Midnight at the end of a day stays 24:00."""
    if end and minutes >= 24 * 60:
        return "24:00"
    return clock_label(minutes)


def parse_typed_clock(text: str, *, end: bool = False) -> int | None:
    """Read a time the user typed: 6:01am, 6:01 pm, 6am, 18:01, or 24:00."""
    raw = "".join(text.strip().casefold().split())
    if not raw:
        return None
    suffix = None
    for tail, name in (("am", "am"), ("pm", "pm"), ("a", "am"), ("p", "pm")):
        if raw.endswith(tail) and raw[:-len(tail)][:1].isdigit():
            suffix = name
            raw = raw[: -len(tail)]
            break
    if ":" in raw:
        parts = raw.split(":")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            return None
        hour = int(parts[0])
        minute = int(parts[1])
    elif raw.isdigit():
        hour = int(raw)
        minute = 0
    else:
        return None
    if minute > 59 or hour < 0:
        return None
    if suffix is None:
        return parse_clock(f"{hour}:{minute:02d}", end=end)
    if hour < 1 or hour > 12:
        return None
    if hour == 12:
        hour = 0
    if suffix == "pm":
        hour += 12
    return hour * 60 + minute


@dataclass(frozen=True)
class Span:
    day: str
    start: int
    end: int
    locked: bool
    ratio: str
    lockdown: bool = False
    name: str = ""
    color: int = 0


def explode(blocks: tuple[Block, ...] | list[Block]) -> list[Span]:
    spans: list[Span] = []
    for block in blocks:
        start = parse_clock(block.start)
        end = parse_clock(block.end, end=True)
        if start is None or end is None:
            continue
        if start < end:
            pieces = ((day, start, end) for day in block.days)
        else:
            pieces = []
            for day in block.days:
                pieces.append((day, start, 24 * 60))
                nxt = DAYS[(DAYS.index(day) + 1) % 7]
                if end > 0:
                    pieces.append((nxt, 0, end))
        for day, piece_start, piece_end in pieces:
            if piece_end > piece_start:
                spans.append(
                    Span(day, piece_start, piece_end, block.locked, block.ratio, block.lockdown, block.name, block.color)
                )
    return spans


def overlapping_days(
    blocks: tuple[Block, ...] | list[Block],
    block: Block,
    ignore: Block | None = None,
) -> tuple[str, ...]:
    pool = explode(item for item in blocks if item != ignore)
    covered = {(span.day, span.start, span.end) for span in explode([block])}
    hit = []
    for day, start, end in covered:
        if any(span.day == day and span.start < end and start < span.end for span in pool):
            hit.append(day)
    return tuple(day for day in DAYS if day in hit)


def place_block(
    blocks: tuple[Block, ...] | list[Block],
    block: Block,
    mode: str,
    ignore: Block | None = None,
) -> tuple[Block, ...]:
    spans = explode(item for item in blocks if item != ignore)
    for piece in explode([block]):
        spans = _apply_span(spans, piece, mode)
    return _coalesce(spans)


def _apply_span(spans: list[Span], new: Span, mode: str) -> list[Span]:
    kept: list[Span] = []
    for span in spans:
        if span.day != new.day or not (span.start < new.end and new.start < span.end):
            kept.append(span)
            continue
        if mode == "replace":
            continue
        if span.start < new.start:
            kept.append(Span(span.day, span.start, new.start, span.locked, span.ratio, span.lockdown, span.name, span.color))
        if span.end > new.end:
            kept.append(Span(span.day, new.end, span.end, span.locked, span.ratio, span.lockdown, span.name, span.color))
    kept.append(new)
    return kept


def _coalesce(spans: list[Span]) -> tuple[Block, ...]:
    by_day: dict[str, list[Span]] = {day: [] for day in DAYS}
    for span in spans:
        if span.end > span.start:
            by_day[span.day].append(span)
    merged: list[Span] = []
    for day in DAYS:
        items = sorted(by_day[day], key=lambda item: (item.start, item.end))
        for span in items:
            if (
                merged
                and merged[-1].day == day
                and merged[-1].locked == span.locked
                and merged[-1].ratio == span.ratio
                and merged[-1].lockdown == span.lockdown
                and merged[-1].name == span.name
                and merged[-1].color == span.color
                and merged[-1].end >= span.start
            ):
                previous = merged[-1]
                merged[-1] = Span(
                    day,
                    previous.start,
                    max(previous.end, span.end),
                    previous.locked,
                    previous.ratio,
                    previous.lockdown,
                    previous.name,
                    previous.color,
                )
            else:
                merged.append(span)
    groups: dict[tuple, list[str]] = {}
    for span in merged:
        groups.setdefault(
            (span.start, span.end, span.locked, span.ratio, span.lockdown, span.name, span.color),
            [],
        ).append(span.day)
    blocks = []
    for (start, end, locked, ratio, lockdown, name, color), days in groups.items():
        ordered = tuple(day for day in DAYS if day in days)
        blocks.append(Block(ordered, format_clock(start), format_clock(end), locked, ratio, lockdown, name, color))
    blocks.sort(key=lambda item: (item.start, item.end, item.days))
    return tuple(blocks)


def make_block(
    days: list[str],
    start: str,
    end: str,
    locked: bool,
    ratio: str,
    lockdown: bool = False,
    name: str = "",
    color: int = 0,
) -> Block | None:
    chosen = tuple(day for day in DAYS if day in {_canonical_day(item) for item in days})
    if not chosen:
        return None
    start_minutes = parse_clock(start)
    end_minutes = parse_clock(end, end=True)
    if start_minutes is None or end_minutes is None or start_minutes == end_minutes:
        return None
    if locked:
        shown = ""
    else:
        parsed = parse_ratio(ratio)
        if parsed is None:
            return None
        shown = format_ratio(*parsed)
    shown_name = " ".join(str(name or "").split())[:40]
    shown_color = int(color) if isinstance(color, int) and not isinstance(color, bool) else 0
    return Block(
        chosen,
        format_clock(start_minutes),
        format_clock(end_minutes),
        locked,
        shown,
        bool(lockdown),
        shown_name,
        shown_color,
    )


def _parse_block(item) -> Block | None:
    if not isinstance(item, dict):
        return None
    days = item.get("days")
    if not isinstance(days, list):
        return None
    return make_block(
        [str(day) for day in days],
        str(item.get("start") or ""),
        str(item.get("end") or ""),
        bool(item.get("locked")),
        str(item.get("ratio") or ""),
        bool(item.get("lockdown")),
        str(item.get("name") or ""),
        item.get("color") if isinstance(item.get("color"), int) else 0,
    )


def _canonical_day(value: str) -> str:
    return DAY_ALIASES.get(value.strip().casefold(), "")


def _matches(block: Block, moment: datetime) -> bool:
    start = parse_clock(block.start)
    end = parse_clock(block.end, end=True)
    if start is None or end is None or not block.days:
        return False
    minute = moment.hour * 60 + moment.minute
    today = DAYS[moment.weekday()]
    if start == end or (start == 0 and end == 24 * 60):
        return today in block.days
    if start < end:
        return today in block.days and start <= minute < end
    yesterday = DAYS[(moment.weekday() - 1) % 7]
    if today in block.days and minute >= start:
        return True
    return yesterday in block.days and minute < end
