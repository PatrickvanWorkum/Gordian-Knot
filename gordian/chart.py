"""The cumulative lines and the Apps bars. Both are a tk Canvas, not matplotlib.

Productive time climbs the green line, unproductive time the purple one.
Time series keeps the full cumulative history. Tracker plots free time left,
coloured from empty (red) to plenty (green), with a yellow tint over paused
stretches. Tracker hides the legend. Sleep is a grey band and shutdown a dark
red one. Reset time is a solid light blue line. The wheel zooms toward the
cursor, dragging pans, and Fit puts the whole range back.

One ordinary program is one bar. A browser is one bar per page title.
"""

from __future__ import annotations

import math
import time
import tkinter as tk
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from functools import lru_cache
from tkinter import font as tkfont

from gordian.clock import local_now
from gordian.colors import hex_to_rgb, rgb_to_hex
from gordian.ledger import untangle_segments
from gordian.settings import BROWSERS
from gordian.titles import activity_name


SERIES = (
    ("productive", "Productive", 1),
    ("unproductive", "Unproductive", 2),
)


def cumulative_points(
    segments: list[dict],
    now: datetime,
    *,
    prepared: list[dict] | None = None,
) -> list[tuple]:
    rows = []
    for segment in prepared if prepared is not None else untangle_segments(segments):
        start = _parse_time(segment.get("start"))
        if start is None:
            continue
        end = _parse_time(segment.get("end")) or now
        if end > now:
            end = now
        if end < start:
            end = start
        rows.append((start, end, segment.get("kind"), float(segment.get("seconds") or 0)))
    rows.sort(key=lambda row: row[0])
    if not rows:
        return [(now, 0.0, 0.0, 0.0, 0.0)]

    points = [(rows[0][0], 0.0, 0.0, 0.0, 0.0)]
    productive = 0.0
    unproductive = 0.0
    paused = 0.0
    not_counting = 0.0
    for start, end, kind, seconds in rows:
        if points[-1][0] < start:
            points.append((start, productive, unproductive, paused, not_counting))
        if kind == "earn":
            productive += seconds
        elif kind == "spend":
            unproductive += seconds
        elif kind == "paused":
            paused += seconds
        elif kind in ("idle", "neutral", "locked"):
            not_counting += seconds
        if end < points[-1][0]:
            end = points[-1][0]
        points.append((end, productive, unproductive, paused, not_counting))
    while points and points[-1][0] > now:
        points.pop()
    if not points:
        return [(now, 0.0, 0.0, 0.0, 0.0)]
    if points[-1][0] < now:
        points.append((now, *points[-1][1:]))
    return points


def balance_points(
    segments: list[dict],
    now: datetime,
    work_minutes: float,
    reward_minutes: float,
    remaining_now: float | None = None,
    *,
    prepared: list[dict] | None = None,
) -> list[tuple[datetime, float, str]]:
    """Free time left over wall time: (moment, remaining_seconds, activity)."""
    rate = (reward_minutes / work_minutes) if work_minutes > 0 else 0.0
    events: list[tuple[datetime, datetime | None, str, float]] = []
    for segment in prepared if prepared is not None else untangle_segments(segments):
        start = _parse_time(segment.get("start"))
        if start is None:
            continue
        kind = segment.get("kind")
        if kind == "reset":
            events.append((start, None, "reset", 0.0))
            continue
        if kind not in ("earn", "spend", "paused"):
            continue
        end = _parse_time(segment.get("end")) or now
        if end > now:
            end = now
        if end < start:
            end = start
        events.append((start, end, kind, float(segment.get("seconds") or 0)))
    events.sort(key=lambda row: row[0])
    if not events:
        return [(now, float(remaining_now or 0.0), "paused")]

    remaining = 0.0
    points: list[tuple[datetime, float, str]] = []
    for start, end, kind, seconds in events:
        if kind == "reset":
            remaining = 0.0
            points.append((start, remaining, "reset"))
            continue
        activity = kind
        if not points or points[-1][0] < start:
            points.append((start, remaining, activity))
        if kind == "earn":
            remaining += seconds * rate
        elif kind == "spend":
            remaining -= seconds
        if end is None:
            continue
        if end < points[-1][0]:
            end = points[-1][0]
        points.append((end, remaining, activity))
    while points and points[-1][0] > now:
        points.pop()
    if not points:
        tip = float(remaining_now or 0.0)
        return [(now, tip, "paused")]
    if points[-1][0] < now:
        points.append((now, points[-1][1], points[-1][2]))
    if remaining_now is not None and points:
        delta = float(remaining_now) - points[-1][1]
        if abs(delta) > 0.5:
            points = [(moment, value + delta, activity) for moment, value, activity in points]
    return points


def pause_spans(segments: list[dict], now: datetime | None = None) -> list[tuple[datetime, datetime]]:
    wall = now or local_now()
    spans = []
    for segment in segments:
        if segment.get("kind") != "paused":
            continue
        start = _parse_time(segment.get("start"))
        end = _parse_time(segment.get("end")) or wall
        if start is None or end <= start:
            continue
        if end > wall:
            end = wall
        spans.append((start, end))
    return spans


def blend_hex(start: str, end: str, amount: float) -> str:
    left = hex_to_rgb(start) or (164, 0, 0)
    right = hex_to_rgb(end) or (61, 214, 140)
    amount = max(0.0, min(1.0, amount))
    return rgb_to_hex(
        int(round(left[0] + (right[0] - left[0]) * amount)),
        int(round(left[1] + (right[1] - left[1]) * amount)),
        int(round(left[2] + (right[2] - left[2]) * amount)),
    )


def balance_color(remaining: float, empty: str, full: str, scale: float) -> str:
    if remaining <= 0:
        return empty
    if scale <= 0:
        return full
    return blend_hex(empty, full, min(1.0, remaining / scale))


def away_spans(segments: list[dict], now: datetime | None = None) -> list[tuple[datetime, datetime, str]]:
    wall = now or local_now()
    spans = []
    for segment in segments:
        kind = segment.get("kind")
        if kind not in ("sleep", "shutdown", "clock"):
            continue
        start = _parse_time(segment.get("start"))
        end = _parse_time(segment.get("end"))
        if start is None or end is None or end <= start:
            continue
        if end > wall:
            end = wall
        if end <= start:
            continue
        spans.append((start, end, kind))
    return spans


def instant_marks(segments: list[dict]) -> list[tuple[datetime, str, float]]:
    """Point events: lost time from a clock set back, and a free-time reset."""
    marks = []
    for segment in segments:
        kind = segment.get("kind")
        if kind not in ("lost", "reset"):
            continue
        start = _parse_time(segment.get("start"))
        if start is None:
            continue
        marks.append((start, kind, float(segment.get("seconds") or 0)))
    return marks


def _title_stem(title: str) -> str:
    text = " ".join((title or "").split())
    for separator in (" — ", " – ", " - "):
        if separator in text:
            return text.split(separator, 1)[0].strip()
    return text


def program_label(process: str, titles: list[str]) -> str:
    stems: dict[str, str] = {}
    for title in titles:
        stem = _title_stem(title)
        if stem:
            stems.setdefault(stem.casefold(), stem)
    if len(stems) == 1:
        return next(iter(stems.values()))
    return process or "Unknown window"


def application_totals(
    segments: list[dict],
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[tuple[str, float]]:
    browsers = {item.casefold() for item in BROWSERS}
    buckets: dict[str, dict] = {}
    for segment in segments:
        if segment.get("kind") in ("sleep", "shutdown", "clock", "lost", "reset"):
            continue
        seconds = _seconds_inside(segment, window_start, window_end)
        if seconds <= 0:
            continue
        process = " ".join(str(segment.get("process") or "").split())
        title = str(segment.get("title") or "")
        if process.casefold() in browsers:
            label = activity_name(process, title)
            key = "page:" + label.casefold()
            bucket = buckets.setdefault(key, {"seconds": 0.0, "label": label})
            bucket["seconds"] += seconds
            continue
        key = "exe:" + (process.casefold() or _title_stem(title).casefold() or "unknown")
        bucket = buckets.setdefault(key, {"seconds": 0.0, "process": process, "titles": []})
        bucket["seconds"] += seconds
        if title:
            bucket["titles"].append(title)
    rows = []
    for bucket in buckets.values():
        if "label" in bucket:
            name = bucket["label"]
        else:
            name = program_label(bucket["process"], bucket["titles"])
        rows.append((name, bucket["seconds"]))
    return sorted(rows, key=lambda item: (-item[1], item[0].casefold()))


def _seconds_inside(segment: dict, window_start: datetime | None, window_end: datetime | None) -> float:
    recorded = float(segment.get("seconds") or 0)
    if window_start is None or window_end is None:
        return recorded
    start = _parse_time(segment.get("start"))
    end = _parse_time(segment.get("end"))
    if start is None or end is None or end <= start:
        return 0.0
    overlap = (min(end, window_end) - max(start, window_start)).total_seconds()
    return max(0.0, overlap)


def y_ticks(low: float, high: float, target: int = 6) -> list[float]:
    span = max(high - low, 1.0)
    step = _Y_STEPS[-1]
    for candidate in _Y_STEPS:
        if span / candidate <= 8:
            step = candidate
            break
    ticks = [0.0] if low <= 0 <= high else []
    value = math.ceil((low - 1e-6) / step) * step
    while value < high - step * 0.15:
        if value >= low and (not ticks or value - ticks[-1] > step * 0.4):
            ticks.append(float(value))
        value += step
    return ticks or [low, high]


_Y_STEPS = (
    60,
    5 * 60,
    10 * 60,
    15 * 60,
    30 * 60,
    3600,
    2 * 3600,
    5 * 3600,
    10 * 3600,
    20 * 3600,
    50 * 3600,
    100 * 3600,
    200 * 3600,
    500 * 3600,
)


def format_axis_duration(seconds: float) -> str:
    if seconds < -0.5:
        return f"-{format_axis_duration(abs(seconds))}"
    total = int(round(max(0.0, seconds)))
    if total <= 0:
        return "0"
    if total % 3600 == 0:
        return f"{total // 3600}h"
    if total >= 3600:
        hours, remainder = divmod(total, 3600)
        minutes = remainder // 60
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if total % 60 == 0:
        return f"{total // 60}m"
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def value_at(points: list[tuple], moment: float, times: list[float] | None = None) -> tuple[float, ...]:
    if not points:
        return (0.0, 0.0, 0.0, 0.0)

    def values(point) -> tuple[float, ...]:
        return tuple(float(item) for item in point[1:])

    if moment <= points[0][0]:
        return values(points[0])
    if moment >= points[-1][0]:
        return values(points[-1])
    if times is not None:
        # times must be sorted; bisect finds the same first matching stretch as the scan below.
        index = bisect_left(times, moment)
        start = points[index - 1][0]
        end = points[index][0]
    else:
        for index in range(1, len(points)):
            start = points[index - 1][0]
            end = points[index][0]
            if start <= moment <= end:
                break
        else:
            return values(points[-1])
    before = values(points[index - 1])
    after = values(points[index])
    if end == start:
        return after
    fraction = (moment - start) / (end - start)
    return tuple(left + (right - left) * fraction for left, right in zip(before, after))


def covered_range(points: list[tuple], view_start: float, view_end: float) -> tuple[float, float] | None:
    """The first and last plotted moment inside the view, ignoring empty padding."""
    if not points:
        return None
    inside = [point[0] for point in points if view_start <= point[0] <= view_end]
    if not inside:
        return points[0][0], points[-1][0]
    return inside[0], inside[-1]


def data_bounds(points: list[tuple], now: float | None = None) -> tuple[float, float]:
    if not points:
        moment = now if now is not None else local_now().timestamp()
        return moment - 60, moment + 60
    start = points[0][0]
    end = points[-1][0]
    if end - start < 60:
        high = end + 30
        if now is not None:
            high = min(high, now + 60.0)
        return start - 30, high
    pad = max(30.0, (end - start) * 0.02)
    high = end + pad
    if now is not None:
        high = min(high, now + 60.0)
    return start - pad, high


def zoom_range(
    start: float,
    end: float,
    anchor_fraction: float,
    factor: float,
    min_span: float,
    bound_start: float,
    bound_end: float,
) -> tuple[float, float]:
    span = max(end - start, 1.0)
    anchor = min(1.0, max(0.0, anchor_fraction))
    moment = start + span * anchor
    new_span = min(max(span * factor, min_span), max(bound_end - bound_start, min_span))
    return clamp_range(moment - new_span * anchor, moment + new_span * (1 - anchor), bound_start, bound_end)


def pan_range(start: float, end: float, shift: float, bound_start: float, bound_end: float) -> tuple[float, float]:
    return clamp_range(start + shift, end + shift, bound_start, bound_end)


def clamp_range(start: float, end: float, bound_start: float, bound_end: float) -> tuple[float, float]:
    span = end - start
    full = bound_end - bound_start
    if span >= full:
        return bound_start, bound_end
    if start < bound_start:
        return bound_start, bound_start + span
    if end > bound_end:
        return bound_end - span, bound_end
    return start, end


def _parse_time(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    return _parse_stamp(value)


@lru_cache(maxsize=8192)
def _parse_stamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class CumulativeChart(tk.Canvas):
    def __init__(
        self,
        parent,
        productive: str,
        unproductive: str,
        paused: str,
        not_counting: str,
        shutdown: str,
        sleep: str,
        text: str,
        muted: str,
        grid: str,
    ) -> None:
        super().__init__(
            parent,
            height=220,
            bg="#ffffff",
            highlightthickness=0,
            bd=0,
            cursor="crosshair",
        )
        self.colors = {
            "productive": productive,
            "unproductive": unproductive,
            "paused": paused,
            "not_counting": not_counting,
        }
        self.shutdown_color = shutdown
        self.sleep_color = sleep
        self.clock_color = "#f2b6d3"
        self.lost_color = "#e45c94"
        self.reset_color = "#6cb6ff"
        self.free_empty = "#a40000"
        self.free_full = "#3dd68c"
        self.pause_tint = "#e8c84a"
        self.text_color = text
        self.muted_color = muted
        self.grid_color = grid
        self.plot_bg = "#ffffff"
        self.face = ("Tahoma", 8)
        self._swatch_hits: list[tuple[int, int, int, int, str]] = []
        self.points: list[tuple] = []
        self.balance_points: list[tuple[float, float, str]] = []
        self.pause_bands: list[tuple[float, float]] = []
        self.shades: list[tuple[float, float, str]] = []
        self.marks: list[tuple[float, str, float]] = []
        self.visible = {key: True for key, _label, _index in SERIES}
        self.legend_shown = True
        self.detail_hover = False
        self.mode = "cumulative"
        self.view_start = 0.0
        self.view_end = 1.0
        self.window: tuple[float, float] | None = None
        self.user_zoomed = False
        self._drag = None
        self._drag_job = None
        self._zoom_job = None
        self._configure_job = None
        self._size = (0, 0)
        self._legend_press = None
        self._hover_x = None
        self._hover_drawn_x = None
        self._hover_job = None
        self._legend_hits: list[tuple[int, int, int, int, str]] = []
        self._legend_fonts: dict = {}
        self._measures: dict[tuple, int] = {}
        self._times: list[float] = []
        self._times_sorted = True
        self._base: tuple[float, ...] | None = None
        self._frame: tuple | None = None
        self._drawn_key: tuple | None = None
        self._structure_key: tuple | None = None
        self._tip_key: tuple | None = None
        self._painted_mode: str | None = None
        self._cumulative_cache: list[tuple] | None = None
        self._balance_cache: list[tuple[float, float, str]] | None = None
        self._pause_cache: list[tuple[float, float]] | None = None
        self._drawn_at = 0.0
        self._wall_now: float | None = None
        self.plot = (58, 48, 12, 28)
        self.bind("<Configure>", self._on_configure)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-1>", self._on_double)
        self.bind("<Motion>", self._on_hover)
        self.bind("<Leave>", lambda _event: self._clear_hover())

    def set_mode(self, mode: str, *, draw: bool = True) -> None:
        chosen = "running" if mode in {"running", "daily"} else "cumulative"
        if chosen == self.mode:
            return
        self.mode = chosen
        self._drawn_key = None
        if self._apply_cached_mode():
            if not self.user_zoomed:
                self.view_start, self.view_end = self._limits()
            if draw:
                self._draw()
            return
        self._structure_key = None
        self._tip_key = None

    def set_segments(
        self,
        segments: list[dict],
        now: datetime,
        *,
        work_minutes: float = 25.0,
        reward_minutes: float = 10.0,
        remaining_seconds: float | None = None,
        draw: bool = True,
    ) -> None:
        last = segments[-1] if segments else {}
        live = float(last.get("seconds") or 0)
        now_tick = int(now.timestamp())
        # Zoom must not be part of this key: including view_start forced a full
        # history rebuild on every tick after the user scrolled the chart.
        structure = (
            len(segments),
            last.get("kind"),
            last.get("start"),
            last.get("end"),
            round(work_minutes, 3),
            round(reward_minutes, 3),
        )
        tip = (int(live), now_tick, int(remaining_seconds or 0))
        if structure == self._structure_key and tip == self._tip_key and self.points:
            if draw and self._painted_mode != self.mode:
                self._draw()
            return
        if (
            structure == self._structure_key
            and self.points
            and last.get("end") is None
            and self._apply_live_tip(last, now, work_minutes, reward_minutes, remaining_seconds)
        ):
            self._tip_key = tip
            self._wall_now = now.timestamp()
            if not self.user_zoomed:
                self.view_start, self.view_end = self._limits()
            if draw:
                if self._painted_mode != self.mode:
                    self._draw()
                else:
                    self._refresh_live()
            return
        self._wall_now = now.timestamp()
        self.shades = [(start.timestamp(), end.timestamp(), kind) for start, end, kind in away_spans(segments, now)]
        self.marks = [
            (moment.timestamp(), kind, seconds)
            for moment, kind, seconds in instant_marks(segments)
            if kind == "reset"
        ]
        prepared = untangle_segments(segments)
        cumulative = cumulative_points(segments, now, prepared=prepared)
        balance = balance_points(segments, now, work_minutes, reward_minutes, remaining_seconds, prepared=prepared)
        self._cumulative_cache = [(item[0].timestamp(), *item[1:]) for item in cumulative]
        self._balance_cache = [(item[0].timestamp(), item[1], item[2]) for item in balance]
        self._pause_cache = [(start.timestamp(), end.timestamp()) for start, end in pause_spans(segments, now)]
        self._apply_cached_mode()
        if not self.user_zoomed:
            self.view_start, self.view_end = self._limits()
        self._structure_key = structure
        self._tip_key = tip
        self._drawn_key = structure + tip + (self.mode,)
        if draw:
            self._draw()

    def _apply_cached_mode(self) -> bool:
        if self.mode == "running":
            if self._balance_cache is None:
                return False
            self.balance_points = list(self._balance_cache)
            self.pause_bands = list(self._pause_cache or [])
            self.points = [(stamp, remaining, 0.0, 0.0, 0.0) for stamp, remaining, _kind in self.balance_points]
        else:
            if self._cumulative_cache is None:
                return False
            self.points = list(self._cumulative_cache)
            self.balance_points = []
            self.pause_bands = []
        self._times = [point[0] for point in self.points]
        self._times_sorted = all(a <= b for a, b in zip(self._times, self._times[1:]))
        return True

    def _apply_live_tip(
        self,
        last: dict,
        now: datetime,
        work_minutes: float,
        reward_minutes: float,
        remaining_seconds: float | None,
    ) -> bool:
        if len(self.points) < 2 or len(self._times) != len(self.points):
            return False
        stamp = now.timestamp()
        kind = last.get("kind")
        live = float(last.get("seconds") or 0)
        if self._cumulative_cache and len(self._cumulative_cache) >= 2:
            base = self._cumulative_cache[-2][1:]
            productive = float(base[0]) if len(base) > 0 else 0.0
            unproductive = float(base[1]) if len(base) > 1 else 0.0
            paused = float(base[2]) if len(base) > 2 else 0.0
            not_counting = float(base[3]) if len(base) > 3 else 0.0
            if kind == "earn":
                productive += live
            elif kind == "spend":
                unproductive += live
            elif kind == "paused":
                paused += live
            elif kind in ("idle", "neutral", "locked"):
                not_counting += live
            self._cumulative_cache[-1] = (stamp, productive, unproductive, paused, not_counting)
        if self._balance_cache and remaining_seconds is not None:
            activity = kind if kind in {"earn", "spend", "paused"} else self._balance_cache[-1][2]
            self._balance_cache[-1] = (stamp, float(remaining_seconds), activity)
        if kind == "paused":
            if self._pause_cache:
                start, _end = self._pause_cache[-1]
                self._pause_cache[-1] = (start, stamp)
            if self.pause_bands:
                start, _end = self.pause_bands[-1]
                self.pause_bands[-1] = (start, stamp)
        if self.mode == "running":
            if remaining_seconds is None or not self.balance_points:
                return False
            activity = kind if kind in {"earn", "spend", "paused"} else self.balance_points[-1][2]
            self.balance_points[-1] = (stamp, float(remaining_seconds), activity)
            self.points[-1] = (stamp, float(remaining_seconds), 0.0, 0.0, 0.0)
            self._times[-1] = stamp
            return True
        if not self._cumulative_cache:
            return False
        self.points[-1] = self._cumulative_cache[-1]
        self._times[-1] = stamp
        return True

    def _refresh_live(self) -> None:
        if self._frame is None or len(self.points) < 2:
            self._draw()
            return
        left, top, right, bottom, y_min, y_max = self._frame
        if self.mode == "running":
            tip = self.balance_points[-1][1] if self.balance_points else 0.0
            if tip > y_max or tip < y_min:
                self._draw()
                return
            self.delete("series_tip")
            self.delete("now")
            self.delete("hover")
            if self.pause_bands:
                self.delete("pause_tint")
                self._draw_pause_tint(left, top, right, bottom)
            self._draw_balance_line(left, top, right, bottom, y_min, y_max, tip_only=True)
            self._draw_now(left, top, right, bottom)
            self._hover_drawn_x = None
            self._draw_hover(left, top, right, bottom, y_min, y_max)
            self._drawn_at = time.monotonic()
            return
        tip = self.points[-1][1:]
        peak = 0.0
        for key, _label, index in SERIES:
            if not self.visible.get(key, True):
                continue
            raw = tip[index - 1] if index - 1 < len(tip) else 0.0
            peak = max(peak, self._separated(raw, index))
        if peak > y_max:
            self._draw()
            return
        self.delete("series_tip")
        self.delete("now")
        self.delete("hover")
        for key, _label, index in reversed(SERIES):
            if self.visible[key]:
                self._draw_series(
                    left, top, right, bottom, y_min, y_max, index, self.colors[key], tip_only=True
                )
        self._draw_now(left, top, right, bottom)
        self._hover_drawn_x = None
        self._draw_hover(left, top, right, bottom, y_min, y_max)
        self._drawn_at = time.monotonic()

    def _pixel_seconds(self) -> float:
        # How long the live end can grow before it moves a whole pixel on either axis.
        if self._frame is None:
            return 0.0
        left, top, right, bottom, y_min, y_max = self._frame
        across = (self.view_end - self.view_start) / max(1, right - left)
        up = (y_max - y_min) / max(1, bottom - top)
        return min(30.0, across, up)

    def set_window(self, start: float | None, end: float | None) -> None:
        self.window = None if start is None or end is None or end <= start else (start, end)
        self.user_zoomed = False
        self.view_start, self.view_end = self._limits()
        self._draw()

    def _limits(self) -> tuple[float, float]:
        if self.window:
            return self.window
        now = self._wall_now if self._wall_now is not None else local_now().timestamp()
        return data_bounds(self.points, now)

    def set_palette_color(self, name: str, value: str) -> None:
        if name in self.colors:
            self.colors[name] = value
        elif name == "shutdown":
            self.shutdown_color = value
        elif name == "sleep":
            self.sleep_color = value
        elif name == "clock":
            self.clock_color = value
        elif name == "lost":
            self.lost_color = value
        elif name == "reset":
            self.reset_color = value
        elif name == "free_empty":
            self.free_empty = value
        elif name == "free_full":
            self.free_full = value
        elif name == "pause_tint":
            self.pause_tint = value
        elif name == "text":
            self.text_color = value
        elif name == "muted":
            self.muted_color = value
        elif name == "grid":
            self.grid_color = value
        elif name == "plot":
            self.plot_bg = value
            self.configure(bg=value)
        self._draw()

    def color_target(self, x: int, y: int) -> str:
        for left, top, right, bottom, key in self._legend_hits + self._swatch_hits:
            if left <= x <= right and top <= y <= bottom:
                return key
        if self.mode == "running" and self._frame is not None:
            left, top, right, bottom, _y_min, _y_max = self._frame
            if left <= x <= right and top <= y <= bottom:
                moment = self.view_start + (self.view_end - self.view_start) * (
                    (x - left) / max(1, right - left)
                )
                for start, end in self.pause_bands:
                    if start <= moment <= end:
                        return "pause_tint"
                remaining = self._balance_value_at(moment)
                return "free_empty" if remaining <= 0 else "free_full"
        return "plot"

    def fit(self) -> None:
        self.user_zoomed = False
        self.view_start, self.view_end = self._limits()
        self._draw()

    def zoom_by(self, factor: float) -> None:
        self._zoom(factor, 0.5, immediate=True)

    def _on_wheel(self, event) -> None:
        self._zoom(0.8 if event.delta > 0 else 1.25, self._anchor_fraction(event.x), immediate=False)

    def _on_configure(self, event) -> None:
        if event.widget is not self:
            return
        size = (int(event.width), int(event.height))
        if size == self._size:
            return
        self._size = size
        if self._configure_job is not None:
            return
        self._configure_job = self.after(16, self._flush_configure)

    def _flush_configure(self) -> None:
        self._configure_job = None
        self._draw()

    def _on_press(self, event) -> None:
        key = self._legend_key(event.x, event.y)
        if key:
            self._legend_press = (key, event.x, event.y)
            self._drag = None
            return
        self._legend_press = None
        self._drag = (event.x, self.view_start, self.view_end)
        self.configure(cursor="fleur")

    def _on_drag(self, event) -> None:
        if self._drag is None:
            return
        origin_x, origin_start, origin_end = self._drag
        width = self._plot_width()
        if width <= 0:
            return
        shift = -(event.x - origin_x) / width * (origin_end - origin_start)
        bounds = self._limits()
        self.view_start, self.view_end = pan_range(origin_start, origin_end, shift, bounds[0], bounds[1])
        self.user_zoomed = not self._view_is_full()
        if self._drag_job is not None:
            return
        self._drag_job = self.after(32, self._flush_drag)

    def _flush_drag(self) -> None:
        self._drag_job = None
        if self._drag is None:
            return
        self._draw()

    def _on_release(self, event) -> None:
        if self._drag_job is not None:
            try:
                self.after_cancel(self._drag_job)
            except tk.TclError:
                pass
            self._drag_job = None
        if self._legend_press:
            key, origin_x, origin_y = self._legend_press
            self._legend_press = None
            if abs(event.x - origin_x) < 6 and abs(event.y - origin_y) < 6 and self._legend_key(event.x, event.y) == key:
                self.visible[key] = not self.visible[key]
                self._draw()
            return
        if self._drag is not None:
            self._drag = None
            self._draw()
        self.configure(cursor="hand2" if self._legend_key(event.x, event.y) else "crosshair")

    def _on_double(self, event) -> None:
        if self._legend_key(event.x, event.y):
            return
        self.fit()

    def _on_hover(self, event) -> None:
        if self._drag is not None:
            return
        self._hover_x = event.x
        cursor = "hand2" if self._legend_key(event.x, event.y) else "crosshair"
        if str(self.cget("cursor")) != cursor:
            self.configure(cursor=cursor)
        if self._hover_x == self._hover_drawn_x:
            return
        if self._hover_job is not None:
            return
        self._hover_job = self.after(33, self._flush_hover)

    def _flush_hover(self) -> None:
        self._hover_job = None
        if self._hover_x == self._hover_drawn_x:
            return
        self._hover_drawn_x = self._hover_x
        self._redraw_hover()

    def _clear_hover(self) -> None:
        if self._hover_job is not None:
            try:
                self.after_cancel(self._hover_job)
            except tk.TclError:
                pass
            self._hover_job = None
        self._hover_x = None
        self._hover_drawn_x = None
        self._redraw_hover()

    def _redraw_hover(self) -> None:
        self.delete("hover")
        if self._frame is not None and self._hover_x is not None:
            self._draw_hover(*self._frame)

    def _legend_key(self, x: int, y: int) -> str | None:
        for left, top, right, bottom, key in self._legend_hits:
            if left <= x <= right and top <= y <= bottom:
                return key
        return None

    def _zoom(self, factor: float, anchor: float, *, immediate: bool = True) -> None:
        bounds = self._limits()
        self.view_start, self.view_end = zoom_range(
            self.view_start,
            self.view_end,
            anchor,
            factor,
            30.0,
            bounds[0],
            bounds[1],
        )
        self.user_zoomed = not self._view_is_full()
        if immediate:
            if self._zoom_job is not None:
                try:
                    self.after_cancel(self._zoom_job)
                except tk.TclError:
                    pass
                self._zoom_job = None
            self._draw()
            return
        if self._zoom_job is not None:
            return
        self._zoom_job = self.after(32, self._flush_zoom)

    def _flush_zoom(self) -> None:
        self._zoom_job = None
        self._draw()

    def _anchor_fraction(self, x: int) -> float:
        left, _top, right, _bottom = self._plot_box()
        width = right - left
        if width <= 0:
            return 0.5
        return (x - left) / width

    def _plot_box(self) -> tuple[int, int, int, int]:
        left, top, right_margin, bottom_margin = self.plot
        width = max(self.winfo_width(), 2)
        height = max(self.winfo_height(), 2)
        return left, top, width - right_margin, height - bottom_margin

    def _plot_width(self) -> int:
        left, _top, right, _bottom = self._plot_box()
        return max(0, right - left)

    def _draw(self) -> None:
        self.delete("all")
        self._frame = None
        self._painted_mode = self.mode
        self._drawn_at = time.monotonic()
        self._base = value_at(self.points, self.window[0], self._sorted_times()) if self.window else None
        width = max(self.winfo_width(), 2)
        show_legend = self.legend_shown and self.mode != "running"
        if show_legend:
            legend_bottom = self._draw_legend(width)
        else:
            self._legend_hits = []
            self._swatch_hits = []
            legend_bottom = 8
        y_min, y_max = self._y_limits() if len(self.points) >= 2 else (0.0, 60.0)
        labels = [format_axis_duration(tick) for tick in y_ticks(y_min, y_max)]
        gutter = max(52, max(self._measure(label, None) for label in labels) + 14)
        self.plot = (gutter, legend_bottom, 12, 28)
        left, top, right, bottom = self._plot_box()
        if right - left < 20 or bottom - top < 20:
            return
        self.create_rectangle(left, top, right, bottom, outline=self.grid_color, fill=self.plot_bg)
        if len(self.points) < 2:
            self.create_text(
                (left + right) / 2,
                (top + bottom) / 2,
                text="The lines appear as the day gets going.",
                fill=self.muted_color,
                font=self.face,
            )
            return

        self._draw_shades(left, top, right, bottom)
        if self.mode == "running":
            self._draw_pause_tint(left, top, right, bottom)
        self._draw_grid(left, top, right, bottom, y_min, y_max)
        self._draw_marks(left, top, right, bottom)
        if self.mode == "running":
            self._draw_zero_line(left, top, right, bottom, y_min, y_max)
            self._draw_balance_line(left, top, right, bottom, y_min, y_max)
        else:
            for key, _label, index in reversed(SERIES):
                if self.visible[key]:
                    self._draw_series(left, top, right, bottom, y_min, y_max, index, self.colors[key])
        self._draw_now(left, top, right, bottom)
        self._frame = (left, top, right, bottom, y_min, y_max)
        self._draw_hover(*self._frame)

    def _measure(self, text: str, size: int | None) -> int:
        key = (self.face, size, text)
        width = self._measures.get(key)
        if width is None:
            font_key = ("axis", self.face) if size is None else (self.face[0], size)
            face = self._legend_fonts.get(font_key)
            if face is None:
                if size is None:
                    face = tkfont.Font(self, font=self.face)
                else:
                    face = tkfont.Font(self, family=self.face[0], size=size)
                self._legend_fonts[font_key] = face
            width = face.measure(text)
            if len(self._measures) > 512:
                self._measures.clear()
            self._measures[key] = width
        return width

    def _view_is_full(self) -> bool:
        start, end = self._limits()
        return abs(self.view_start - start) < 1 and abs(self.view_end - end) < 1

    def _draw_legend(self, width: int) -> int:
        self._legend_hits = []
        self._swatch_hits = []
        if self.mode == "running":
            items = [
                ("free_full", "Free time", self.free_full, False),
                ("free_empty", "None", self.free_empty, False),
                ("pause_tint", "Pause tint", self.pause_tint, False),
            ]
        else:
            items = [(key, label, self.colors[key], True) for key, label, _index in SERIES]
        items.append(("sleep", "Sleep", self.sleep_color, False))
        items.append(("shutdown", "Shut down", self.shutdown_color, False))
        items.append(("clock", "Missing", self.clock_color, False))
        items.append(("reset", "Reset", self.reset_color, False))
        font_size = int(self.face[1]) if len(self.face) > 1 else 8
        measured = self._legend_widths(items, font_size)
        if sum(measured) > width - 8 and font_size > 7:
            font_size = max(7, font_size - 1)
            measured = self._legend_widths(items, font_size)
        total = sum(measured)
        x = max(6, (width - total) / 2)
        y = 11
        for (key, label, color, toggle), item_width in zip(items, measured):
            swatch = color if (not toggle or self.visible.get(key, True)) else self.grid_color
            text_color = self.text_color if (not toggle or self.visible.get(key, True)) else self.muted_color
            if key == "clock":
                self.create_rectangle(x, y - 5, x + 14, y + 5, fill=swatch, outline="", stipple="gray50")
            elif key == "pause_tint":
                self.create_rectangle(x, y - 5, x + 14, y + 5, fill=swatch, outline="", stipple="gray50")
            elif key == "reset":
                self.create_line(x, y, x + 14, y, fill=swatch, width=2)
            elif key in ("sleep", "shutdown"):
                self.create_rectangle(x, y - 5, x + 14, y + 5, fill=swatch, outline="")
            elif key in ("free_full", "free_empty"):
                self.create_line(x, y, x + 14, y, fill=swatch, width=3)
            else:
                self.create_line(x, y, x + 14, y, fill=swatch, width=2)
            self.create_text(x + 18, y, text=label, fill=text_color, anchor="w", font=self._sized_face(font_size))
            if toggle:
                self._legend_hits.append((x - 2, y - 9, x + item_width - 4, y + 9, key))
            else:
                self._swatch_hits.append((x - 2, y - 9, x + item_width - 4, y + 9, key))
            x += item_width
        return y + 14

    def _legend_widths(self, items, font_size: int) -> list[int]:
        return [14 + 6 + self._measure(label, font_size) + 12 for _key, label, _color, _toggle in items]

    def _draw_shades(self, left: int, top: int, right: int, bottom: int) -> None:
        for start, end, kind in self.shades:
            x0 = max(left, self._x(start, left, right))
            x1 = min(right, self._x(end, left, right))
            if x1 - x0 < 1:
                continue
            if kind == "shutdown":
                color = self.shutdown_color
            elif kind == "clock":
                color = self.clock_color
            else:
                color = self.sleep_color
            self.create_rectangle(x0, top, x1, bottom, fill=color, outline="", stipple="gray50")

    def _draw_pause_tint(self, left: int, top: int, right: int, bottom: int) -> None:
        for start, end in self.pause_bands:
            x0 = max(left, self._x(start, left, right))
            x1 = min(right, self._x(end, left, right))
            if x1 - x0 < 1:
                continue
            self.create_rectangle(
                x0, top, x1, bottom, fill=self.pause_tint, outline="", stipple="gray50", tags="pause_tint"
            )

    def _draw_zero_line(self, left: int, top: int, right: int, bottom: int, y_min: float, y_max: float) -> None:
        if y_min >= 0 or y_max <= 0:
            return
        y = self._y(0.0, top, bottom, y_min, y_max)
        self.create_line(left, y, right, y, fill=self.muted_color, dash=(4, 3))

    def _balance_scale(self) -> float:
        values = [point[1] for point in self.balance_points if self.view_start <= point[0] <= self.view_end]
        if not values:
            values = [point[1] for point in self.balance_points]
        high = max(values) if values else 0.0
        return max(high, 30 * 60)

    def _segment_color(self, remaining: float, kind: str) -> str:
        return balance_color(remaining, self.free_empty, self.free_full, self._balance_scale())

    def _draw_balance_line(
        self,
        left: int,
        top: int,
        right: int,
        bottom: int,
        y_min: float,
        y_max: float,
        *,
        tip_only: bool = False,
    ) -> None:
        if len(self.balance_points) < 2:
            return
        scale = self._balance_scale()
        last = len(self.balance_points) - 1
        start_at = last if tip_only else 1
        for index in range(start_at, len(self.balance_points)):
            start_stamp, start_value, _start_kind = self.balance_points[index - 1]
            end_stamp, end_value, _kind = self.balance_points[index]
            if end_stamp < self.view_start or start_stamp > self.view_end:
                continue
            # Clip to the visible window so zoomed segments do not spill into the axis gutter.
            seg_start = max(start_stamp, self.view_start)
            seg_end = min(end_stamp, self.view_end)
            if seg_end <= seg_start:
                continue
            if end_stamp == start_stamp:
                clipped_start = start_value
                clipped_end = end_value
            else:
                span = end_stamp - start_stamp
                t_start = (seg_start - start_stamp) / span
                t_end = (seg_end - start_stamp) / span
                clipped_start = start_value + (end_value - start_value) * t_start
                clipped_end = start_value + (end_value - start_value) * t_end
            x0 = self._x(seg_start, left, right)
            x1 = self._x(seg_end, left, right)
            y0 = self._y(clipped_start, top, bottom, y_min, y_max)
            y1 = self._y(clipped_end, top, bottom, y_min, y_max)
            tags = ("series", "series_tip") if index == last else ("series", "series_body")
            # Cap colour samples so a wide zoom does not create one canvas line per pixel.
            pixel_span = max(1, int(abs(x1 - x0)))
            steps = max(1, min(pixel_span, 48))
            run_color = None
            run_ax = run_ay = 0.0
            last_bx = last_by = 0.0
            for step in range(steps):
                t0 = step / steps
                t1 = (step + 1) / steps
                value1 = clipped_start + (clipped_end - clipped_start) * t1
                color = balance_color(value1, self.free_empty, self.free_full, scale)
                ax = x0 + (x1 - x0) * t0
                ay = y0 + (y1 - y0) * t0
                bx = x0 + (x1 - x0) * t1
                by = y0 + (y1 - y0) * t1
                if bx < left or ax > right:
                    continue
                ax = max(left, min(right, ax))
                bx = max(left, min(right, bx))
                if run_color is None:
                    run_color = color
                    run_ax, run_ay = ax, ay
                    last_bx, last_by = bx, by
                    continue
                if color == run_color:
                    last_bx, last_by = bx, by
                    continue
                self.create_line(run_ax, run_ay, last_bx, last_by, fill=run_color, width=2, tags=tags)
                run_color = color
                run_ax, run_ay = ax, ay
                last_bx, last_by = bx, by
            if run_color is not None:
                self.create_line(run_ax, run_ay, last_bx, last_by, fill=run_color, width=2, tags=tags)

    def _balance_value_at(self, moment: float) -> float:
        if not self.balance_points:
            return 0.0
        if moment <= self.balance_points[0][0]:
            return self.balance_points[0][1]
        if moment >= self.balance_points[-1][0]:
            return self.balance_points[-1][1]
        for index in range(1, len(self.balance_points)):
            start = self.balance_points[index - 1]
            end = self.balance_points[index]
            if start[0] <= moment <= end[0]:
                if end[0] == start[0]:
                    return end[1]
                fraction = (moment - start[0]) / (end[0] - start[0])
                return start[1] + (end[1] - start[1]) * fraction
        return self.balance_points[-1][1]

    def _balance_kind_at(self, moment: float) -> str:
        if not self.balance_points:
            return "paused"
        for index in range(1, len(self.balance_points)):
            start = self.balance_points[index - 1]
            end = self.balance_points[index]
            if start[0] <= moment <= end[0]:
                return end[2]
        return self.balance_points[-1][2]

    def _draw_marks(self, left: int, top: int, right: int, bottom: int) -> None:
        for moment, kind, seconds in self.marks:
            x = self._x(moment, left, right)
            if x < left or x > right:
                continue
            color = self.reset_color
            self.create_line(x, top, x, bottom, fill=color, width=2)
            room_right = right - x > 48
            self.create_text(
                x + 4 if room_right else x - 4,
                top + 10,
                text="Reset",
                fill=color,
                anchor="w" if room_right else "e",
                font=self.face,
            )

    def _draw_grid(self, left: int, top: int, right: int, bottom: int, y_min: float, y_max: float) -> None:
        for value in y_ticks(y_min, y_max):
            y = self._y(value, top, bottom, y_min, y_max)
            self.create_line(left, y, right, y, fill=self.grid_color)
            self.create_text(
                left - 8,
                y,
                text=format_axis_duration(value),
                fill=self.muted_color,
                anchor="e",
                font=self.face,
            )
        span = self.view_end - self.view_start
        if 20 * 3600 <= span <= 21 * 86400:
            self._draw_day_separators(left, top, right, bottom, span)
            return
        step = _tick_step(span)
        first = math.ceil(self.view_start / step) * step
        moment = first
        while moment < self.view_end:
            x = self._x(moment, left, right)
            if left <= x <= right:
                self.create_line(x, top, x, bottom, fill=self.grid_color)
                self.create_text(
                    x,
                    bottom + 12,
                    text=axis_label(moment, span),
                    fill=self.muted_color,
                    font=self.face,
                )
            moment += step

    def _draw_day_separators(self, left: int, top: int, right: int, bottom: int, span: float) -> None:
        start_day = datetime.fromtimestamp(self.view_start).date()
        end_day = datetime.fromtimestamp(max(self.view_start, self.view_end - 1)).date()
        day = start_day
        while day <= end_day:
            midnight = datetime(day.year, day.month, day.day).timestamp()
            next_midnight = midnight + 86400
            if midnight > self.view_start and midnight < self.view_end:
                x = self._x(midnight, left, right)
                self.create_line(x, top, x, bottom, fill=self.muted_color)
            visible_start = max(self.view_start, midnight)
            visible_end = min(self.view_end, next_midnight)
            if visible_end - visible_start > 3600:
                month_once = day == start_day or day.day == 1
                if span > 14 * 86400:
                    label = f"{day.strftime('%b')} {day.day}" if month_once else str(day.day)
                else:
                    label = f"{day.strftime('%a')} {day.day}"
                self.create_text(
                    self._x((visible_start + visible_end) / 2, left, right),
                    bottom + 12,
                    text=label,
                    fill=self.muted_color,
                    font=self.face,
                )
            day += timedelta(days=1)

    def _draw_series(
        self,
        left: int,
        top: int,
        right: int,
        bottom: int,
        y_min: float,
        y_max: float,
        index: int,
        color: str,
        *,
        tip_only: bool = False,
    ) -> None:
        if tip_only:
            if len(self.points) < 2:
                return
            before = self.points[-2]
            after = self.points[-1]
            raw0 = before[index] if index < len(before) else 0.0
            raw1 = after[index] if index < len(after) else 0.0
            x0 = self._x(before[0], left, right)
            x1 = self._x(after[0], left, right)
            y0 = self._y(self._separated(raw0, index), top, bottom, y_min, y_max)
            y1 = self._y(self._separated(raw1, index), top, bottom, y_min, y_max)
            self.create_line(x0, y0, x1, y1, fill=color, width=2, tags=("series", "series_tip"))
            return
        # The lines only rise, so the first and last point in each pixel column draw the same shape.
        coordinates = []
        column = None
        tail = None
        for moment, value in _visible_series(
            self.points, self.view_start, self._live_view_end(), index, self._sorted_times()
        ):
            x = self._x(moment, left, right)
            y = self._y(self._separated(value, index), top, bottom, y_min, y_max)
            if int(x) == column:
                tail = (x, y)
                continue
            if tail is not None:
                coordinates.extend(tail)
                tail = None
            coordinates.extend((x, y))
            column = int(x)
        if tail is not None:
            coordinates.extend(tail)
        if len(coordinates) < 4:
            return
        if len(coordinates) >= 6:
            body = coordinates[:-2]
            tip = coordinates[-4:]
            if len(body) >= 4:
                self.create_line(*body, fill=color, width=2, tags=("series", "series_body"))
            self.create_line(*tip, fill=color, width=2, tags=("series", "series_tip"))
        else:
            self.create_line(*coordinates, fill=color, width=2, tags=("series", "series_tip"))

    def _live_view_end(self) -> float:
        if self._wall_now is None:
            return self.view_end
        return min(self.view_end, self._wall_now + 60.0)

    def _draw_now(self, left: int, top: int, right: int, bottom: int) -> None:
        now = self._wall_now
        if now is None or now < self.view_start or now > self.view_end:
            return
        x = self._x(now, left, right)
        if x < left or x > right:
            return
        self.create_line(x, top, x, bottom, fill=self.text_color, width=2, tags="now")

    def _draw_hover(self, left: int, top: int, right: int, bottom: int, y_min: float, y_max: float) -> None:
        if self._hover_x is None or not (left <= self._hover_x <= right):
            return
        fraction = (self._hover_x - left) / max(1, right - left)
        moment = self.view_start + (self.view_end - self.view_start) * fraction
        self.create_line(self._hover_x, top, self._hover_x, bottom, fill="#6d7888", tags="hover")
        span = self.view_end - self.view_start
        rows: list[tuple[str | None, str, bool]] = [(None, hover_time_label(moment, span), False)]
        if self.mode == "running":
            remaining = self._balance_value_at(moment)
            kind = self._balance_kind_at(moment)
            color = self._segment_color(remaining, kind)
            y = self._y(remaining, top, bottom, y_min, y_max)
            self.create_oval(
                self._hover_x - 3, y - 3, self._hover_x + 3, y + 3, fill=color, outline="", tags="hover"
            )
            if remaining < 0:
                rows.append((color, f"Overspent  {_axis_duration(abs(remaining))}", True))
            else:
                rows.append((color, f"Free  {_axis_duration(remaining)}", True))
            if self.detail_hover:
                if kind == "earn":
                    rows.append((self.colors["productive"], "Increasing", True))
                elif kind == "spend":
                    rows.append((self.colors["unproductive"], "Decreasing", True))
                elif kind == "paused":
                    rows.append((self.colors["paused"], "Paused", True))
                for start, end in self.pause_bands:
                    if start <= moment <= end:
                        rows.append((self.pause_tint, "Pause tint", False))
                        break
        else:
            values = value_at(self.points, moment, self._sorted_times())
            for key, label, index in SERIES:
                if not self.visible[key]:
                    continue
                raw = values[index - 1] if index - 1 < len(values) else 0.0
                value = self._separated(raw, index)
                y = self._y(value, top, bottom, y_min, y_max)
                self.create_oval(
                    self._hover_x - 3,
                    y - 3,
                    self._hover_x + 3,
                    y + 3,
                    fill=self.colors[key],
                    outline="",
                    tags="hover",
                )
                rows.append((self.colors[key], f"{label}  {_axis_duration(value)}", True))
        if self.detail_hover:
            for start, end, kind in self.shades:
                if start <= moment <= end:
                    if kind == "shutdown":
                        color, text = self.shutdown_color, "Shut down"
                    elif kind == "clock":
                        color, text = self.clock_color, "Missing time"
                    else:
                        color, text = self.sleep_color, "Sleep"
                    rows.append((color, text, False))
                    break
            for mark_at, kind, _seconds in self.marks:
                x = self._x(mark_at, left, right)
                if abs(x - self._hover_x) > 6:
                    continue
                if kind == "reset":
                    rows.append((self.reset_color, "Free time reset", True))
        on_left = self._hover_x < (left + right) / 2
        origin = self._hover_x + 10 if on_left else self._hover_x - 10
        anchor = "w" if on_left else "e"
        for row_index, (color, text, line) in enumerate(rows):
            y = top + 10 + row_index * 14
            if color is not None:
                mark = origin if on_left else origin - 16
                if line:
                    self.create_line(mark, y, mark + 14, y, fill=color, width=2, tags="hover")
                else:
                    self.create_rectangle(mark, y - 5, mark + 14, y + 5, fill=color, outline="", tags="hover")
                text_x = mark + 18 if on_left else mark - 4
            else:
                text_x = origin
            self.create_text(text_x, y, text=text, fill=self.text_color, anchor=anchor, font=self.face, tags="hover")

    def _sorted_times(self) -> list[float] | None:
        times = self._times
        if len(times) != len(self.points):
            return None
        return times if self._times_sorted else None

    def _y_limits(self) -> tuple[float, float]:
        if self.mode == "running":
            inside = [point[1] for point in self.balance_points if self.view_start <= point[0] <= self.view_end]
            if not inside:
                inside = [point[1] for point in self.balance_points] or [0.0]
            low = min(inside)
            high = max(inside)
            if high <= low:
                high = low + 60
            pad = max(30.0, (high - low) * 0.08)
            if low >= 0:
                return 0.0, high + pad
            return low - pad, high + pad
        indexes = [index for key, _label, index in SERIES if self.visible[key]]
        if not indexes:
            return 0.0, 60.0
        shown = []
        times = self._sorted_times()
        if times is None:
            inside = [point for point in self.points if self.view_start <= point[0] <= self.view_end]
        else:
            inside = self.points[bisect_left(times, self.view_start) : bisect_right(times, self.view_end)]
        for index in indexes:
            column = [point[index] for point in inside if index < len(point)]
            if column:
                shown.append(self._separated(min(column), index))
                shown.append(self._separated(max(column), index))
        for moment in (self.view_start, self.view_end):
            sample = value_at(self.points, moment, self._sorted_times())
            for index in indexes:
                if index - 1 < len(sample):
                    shown.append(self._separated(sample[index - 1], index))
        if not shown:
            return 0.0, 60.0
        low = min(shown)
        high = max(shown)
        if high <= low:
            high = low + 60
        pad = max(30.0, (high - low) * 0.08)
        return max(0.0, low), high + pad

    def _separated(self, value: float, index: int) -> float:
        if not self.window:
            return value
        base = self._base
        if base is None:
            base = self._base = value_at(self.points, self.window[0], self._sorted_times())
        if index - 1 >= len(base):
            return value
        return max(0.0, value - base[index - 1])

    def _x(self, moment: float, left: int, right: int) -> float:
        span = self.view_end - self.view_start
        if span <= 0:
            return left
        return left + (moment - self.view_start) / span * (right - left)

    def _y(self, value: float, top: int, bottom: int, y_min: float, y_max: float) -> float:
        span = y_max - y_min
        if span <= 0:
            return bottom
        return bottom - (value - y_min) / span * (bottom - top)

    def _sized_face(self, size: int):
        if len(self.face) > 2:
            return (self.face[0], size, self.face[2])
        return (self.face[0], size)


class AppBars(tk.Canvas):
    def __init__(self, parent, bar: str, text: str, muted: str, track: str) -> None:
        super().__init__(parent, bg=track, highlightthickness=0, bd=0, height=140)
        self.rows: list[tuple[str, float]] = []
        self.offset = 0.0
        self.bar_color = bar
        self.text_color = text
        self.muted_color = muted
        self.track_color = track
        self.canvas_bg = track
        self.face = ("Tahoma", 8)
        self._hits: list[tuple[float, float, float, float, str]] = []
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<MouseWheel>", self._on_wheel)

    def set_bar_color(self, value: str) -> None:
        self.set_palette_color("bar", value)

    def set_palette_color(self, name: str, value: str) -> None:
        if name == "bar":
            self.bar_color = value
        elif name == "text":
            self.text_color = value
        elif name == "muted":
            self.muted_color = value
        elif name == "canvas":
            self.canvas_bg = value
            self.configure(bg=value)
        self._draw()

    def color_target(self, x: int, y: int) -> str:
        for left, top, right, bottom, key in self._hits:
            if left <= x <= right and top <= y <= bottom:
                return key
        return "canvas"

    def set_rows(self, rows: list[tuple[str, float]]) -> None:
        next_rows = list(rows)
        if next_rows == self.rows:
            return
        self.rows = next_rows
        self._draw()

    def _on_wheel(self, event) -> None:
        self.offset = max(0.0, self.offset - (event.delta / 120) * 48)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        self._hits = []
        width = max(self.winfo_width(), 2)
        height = max(self.winfo_height(), 2)
        if width < 40 or height < 20:
            return
        if not self.rows:
            self.create_text(
                width / 2,
                height / 2,
                text="Time on each program shows up here.",
                fill=self.muted_color,
                font=self.face,
            )
            return
        gutter = 46
        slot = 96
        group = slot * len(self.rows)
        usable = max(40, width - gutter)
        max_offset = max(0.0, group + 16 - usable)
        self.offset = min(self.offset, max_offset)
        origin = gutter + 8 - self.offset if group + 16 > usable else gutter + (usable - group) / 2
        baseline = height - 42
        longest = self.rows[0][1] or 1.0
        ceiling = 22
        room = max(24, baseline - ceiling)
        for tick in y_ticks(0.0, longest, target=4):
            y = baseline - room * (tick / longest) if longest else baseline
            self.create_line(gutter, y, width - 8, y, fill=self.muted_color, dash=(2, 4))
            self.create_text(gutter - 6, y, text=format_axis_duration(tick), fill=self.muted_color, anchor="e", font=self.face)
        for index, (name, seconds) in enumerate(self.rows):
            left = origin + index * slot + 18
            right = left + slot - 36
            if right < 0 or left > width:
                continue
            bar_height = max(4, room * (seconds / longest))
            top = baseline - bar_height
            center = (left + right) / 2
            self.create_rectangle(left, top, right, baseline, fill=self.bar_color, outline="")
            self._hits.append((left, top, right, baseline, "bar"))
            self.create_text(center, top - 8, text=_axis_duration(seconds), fill=self.muted_color, font=self.face)
            self._hits.append((center - 36, top - 18, center + 36, top, "muted"))
            self.create_text(
                center,
                baseline + 16,
                text=_column_label(name),
                fill=self.text_color,
                font=self.face,
                justify="center",
            )
            self._hits.append((center - 40, baseline + 2, center + 40, baseline + 36, "text"))


def _column_label(name: str, width: int = 16) -> str:
    text = " ".join(name.split())
    if len(text) <= width:
        return text
    split = text.rfind(" ", 0, width + 1)
    if split < 4:
        split = width
    second = text[split:].strip()
    if len(second) > width:
        second = second[: width - 1] + "…"
    return text[:split].strip() + "\n" + second


def _visible_series(
    points: list[tuple],
    view_start: float,
    view_end: float,
    index: int,
    times: list[float] | None = None,
) -> list[tuple[float, float]]:
    def pick(values: tuple[float, ...]) -> float:
        if index - 1 < len(values):
            return values[index - 1]
        return 0.0

    series = [(view_start, pick(value_at(points, view_start, times)))]
    if times is None:
        inside = [point for point in points if view_start < point[0] < view_end]
    else:
        inside = points[bisect_right(times, view_start) : bisect_left(times, view_end)]
    series.extend((point[0], float(point[index]) if index < len(point) else 0.0) for point in inside)
    live_end = view_end
    series.append((live_end, pick(value_at(points, live_end, times))))
    return series


def _axis_duration(seconds: float) -> str:
    total = int(max(0, seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s" if secs and minutes < 10 else f"{minutes}m"
    return f"{secs}s"


def axis_label(moment: float, span: float) -> str:
    stamp = datetime.fromtimestamp(moment)
    if span >= 5 * 86400:
        return stamp.strftime("%b %d")
    if span >= 86400:
        return stamp.strftime("%b %d %H:%M")
    if span < 15 * 60:
        return stamp.strftime("%H:%M:%S")
    return stamp.strftime("%H:%M")


def hover_time_label(moment: float, span: float) -> str:
    """Clock time for the crosshair; always includes hours and minutes."""
    stamp = datetime.fromtimestamp(moment)
    if span >= 86400:
        return stamp.strftime("%b %d %H:%M")
    if span < 15 * 60:
        return stamp.strftime("%H:%M:%S")
    return stamp.strftime("%H:%M")


def _tick_step(span: float) -> float:
    steps = (
        60,
        300,
        900,
        1800,
        3600,
        7200,
        14400,
        21600,
        86400,
        2 * 86400,
        7 * 86400,
        14 * 86400,
        30 * 86400,
    )
    for step in steps:
        if span / step <= 8:
            return step
    return 30 * 86400
