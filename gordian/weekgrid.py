from __future__ import annotations

import tkinter as tk
from datetime import datetime

from gordian.schedule import DAYS, Block, block_caption, block_fill, clock_label, explode, parse_clock, week_average_ratio


LETTERS = ("M", "T", "W", "T", "F", "S", "S")


class WeekSchedule(tk.Frame):
    """The schedule tab: days across, minutes down.

Drag on an empty hour to create a block, drag an edge to resize it, and click
the middle to edit it. The dialog that follows asks for the password. Each
block keeps the pastel it was given, and a name when one was typed. Locked
blocks and Lockdown blocks use the locked color. The label is the name, then
Lockdown, Locked, or the ratio.

Click a day letter to open that day on its own. Today stays filled in, so the
day you are reading and the day it actually is can be different. Week, in the
corner, or a second click on the open day, brings every day back.
"""

    def __init__(
        self,
        parent,
        on_create,
        on_edit,
        on_resize,
        bg: str,
        fg: str,
        muted: str,
        line: str,
        block_color: str,
        locked_color: str,
        preview_color: str,
    ) -> None:
        super().__init__(parent, bg=bg)
        self.on_create = on_create
        self.on_edit = on_edit
        self.on_resize = on_resize
        self.bg = bg
        self.fg = fg
        self.muted = muted
        self.line = line
        self.block_color = block_color
        self.locked_color = locked_color
        self.preview_color = preview_color
        self.now_color = fg
        self.pastel_overrides: dict[int, str] = {}
        self.gutter = 52
        self.header_h = 32
        self.hour_px = 48
        self.blocks: tuple[Block, ...] = ()
        self.view_day: str | None = None
        self._hits: list[tuple[float, float, float, float, int]] = []
        self._color_hits: list[tuple[float, float, float, float, str]] = []
        self._drag = None
        self._placed = False
        self._now_job: str | None = None
        self.header = tk.Canvas(self, height=self.header_h, bg=bg, highlightthickness=0, bd=0)
        self.body = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, yscrollincrement=24)
        self.scroll = tk.Scrollbar(self, command=self.body.yview)
        self.body.configure(yscrollcommand=self.scroll.set)
        self.header.grid(row=0, column=0, sticky="ew")
        self.body.grid(row=1, column=0, sticky="nsew")
        self.scroll.grid(row=1, column=1, sticky="ns")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.body.bind("<Configure>", self._draw)
        self.body.bind("<ButtonPress-1>", self._press)
        self.body.bind("<B1-Motion>", self._motion)
        self.body.bind("<ButtonRelease-1>", self._release)
        self.body.bind("<Motion>", self._hover)
        self.body.bind("<MouseWheel>", self._wheel)
        self.header.bind("<MouseWheel>", self._wheel)
        self.header.bind("<ButtonPress-1>", self._header_press)
        self.header.bind("<Motion>", self._header_hover)
        self.bind("<Map>", self._start_now_clock)
        self.bind("<Unmap>", self._stop_now_clock)

    def set_blocks(self, blocks: tuple[Block, ...] | list[Block]) -> None:
        self.blocks = tuple(blocks)
        self._drag = None
        self._draw()

    def palette_color(self, name: str) -> str:
        if name == "now":
            return self.now_color
        if name == "today":
            return self.block_color
        if name == "locked":
            return self.locked_color
        if name == "preview":
            return self.preview_color
        if name == "canvas":
            return self.bg
        if name.startswith("pastel:"):
            index = int(name.split(":", 1)[1])
            return self.pastel_overrides.get(index, block_fill(index))
        return self.bg

    def set_palette_color(self, name: str, value: str) -> None:
        if name == "now":
            self.now_color = value
        elif name == "today":
            self.block_color = value
        elif name == "locked":
            self.locked_color = value
        elif name == "preview":
            self.preview_color = value
        elif name == "canvas":
            self.bg = value
            self.header.configure(bg=value)
            self.body.configure(bg=value)
            self.configure(bg=value)
        elif name.startswith("pastel:"):
            index = int(name.split(":", 1)[1])
            self.pastel_overrides[index] = value
        else:
            return
        self._draw()

    def color_target(self, canvas: tk.Canvas, x: int, y: int) -> str:
        cx = canvas.canvasx(x) if canvas is self.body else float(x)
        cy = canvas.canvasy(y) if canvas is self.body else float(y)
        for left, top, right, bottom, key in reversed(self._color_hits):
            if canvas is self.body and key.startswith("header:"):
                continue
            if canvas is self.header and not key.startswith("header:"):
                continue
            if left <= cx <= right and top <= cy <= bottom:
                return key.removeprefix("header:")
        return "canvas"

    def _wheel(self, event) -> None:
        self.body.yview_scroll(int(-event.delta / 120), "units")

    def _week_columns(self, width: int) -> list[tuple[float, float]]:
        inner = max(width - self.gutter, 7)
        size = inner / 7
        return [(self.gutter + index * size, self.gutter + (index + 1) * size) for index in range(7)]

    def _columns(self) -> list[tuple[float, float]]:
        width = max(self.body.winfo_width(), 2)
        if self.view_day is None:
            return self._week_columns(width)
        chosen = DAYS.index(self.view_day)
        columns = [(float(width + 8), float(width + 8)) for _ in DAYS]
        columns[chosen] = (float(self.gutter), float(width))
        return columns

    def show_day(self, day: str | None) -> None:
        self.view_day = day if day in DAYS else None
        self._draw()

    def _y(self, minute: int | float) -> float:
        return minute / 60 * self.hour_px

    def _start_now_clock(self, _event=None) -> None:
        self._stop_now_clock()
        self._schedule_now_clock()

    def _stop_now_clock(self, _event=None) -> None:
        if self._now_job is not None:
            self.after_cancel(self._now_job)
            self._now_job = None

    def _schedule_now_clock(self) -> None:
        if not self.winfo_ismapped():
            return
        now = datetime.now()
        delay = max(500, (60 - now.second) * 1000 - now.microsecond // 1000)
        self._now_job = self.after(delay, self._now_clock_tick)

    def _now_clock_tick(self) -> None:
        self._now_job = None
        if not self.winfo_ismapped():
            return
        self._draw()
        self._schedule_now_clock()

    def _paint_now(self, columns: list[tuple[float, float]]) -> None:
        now = datetime.now()
        today = now.weekday()
        day = DAYS[today]
        if self.view_day is not None and self.view_day != day:
            return
        left, right = columns[today]
        if right - left < 8:
            return
        minutes = now.hour * 60 + now.minute + now.second / 60.0
        if minutes <= 0 or minutes >= 24 * 60:
            return
        y = self._y(minutes)
        pad = 3
        self.body.create_line(left + pad, y, right - pad, y, fill=self.now_color, width=2)
        self._color_hits.append((left + pad, y - 3, right - pad, y + 3, "now"))

    def _minute(self, y: float) -> int:
        minute = round(self.body.canvasy(y) / self.hour_px * 60)
        return max(0, min(24 * 60, minute))

    def _day_at(self, x: float) -> str | None:
        if self.view_day is not None:
            return self.view_day if x >= self.gutter else None
        for index, (left, right) in enumerate(self._columns()):
            if left <= x <= right:
                return DAYS[index]
        return None

    def _header_day(self, x: float) -> str | None:
        width = max(self.header.winfo_width(), self.body.winfo_width(), 2)
        for index, (left, right) in enumerate(self._week_columns(width)):
            if left <= x <= right:
                return DAYS[index]
        return None

    def _draw(self, _event=None) -> None:
        if getattr(self, "_drawing", False):
            return
        self._drawing = True
        try:
            self._render()
        finally:
            self._drawing = False

    def _render(self) -> None:
        width = max(self.body.winfo_width(), 2)
        height = 24 * self.hour_px
        self.body.configure(scrollregion=(0, 0, width, height))
        self.header.delete("all")
        self.body.delete("all")
        self._hits = []
        self._color_hits = []
        columns = self._columns()
        header_width = max(self.header.winfo_width(), width, 2)
        header_columns = self._week_columns(header_width)
        today = datetime.now().weekday()
        if self.view_day is not None:
            self.header.create_text(
                self.gutter / 2,
                self.header_h / 2,
                text="Week",
                fill=self.fg,
                font=("Tahoma", 8, "underline"),
            )
        else:
            average = week_average_ratio(self.blocks)
            edge = self.gutter - 8
            self.header.create_text(
                edge,
                13,
                text="μ W:R",
                anchor="e",
                fill=self.muted,
                font=("Tahoma", 8),
            )
            self.header.create_text(
                edge,
                25,
                text=average,
                anchor="e",
                fill=self.fg,
                font=("Tahoma", 8, "bold"),
            )
        for index, (left, right) in enumerate(header_columns):
            is_today = index == today
            is_open = self.view_day == DAYS[index]
            day_box = (left + 3, 6, right - 3, self.header_h - 6)
            self.header.create_rectangle(
                *day_box,
                outline=self.fg if is_open and not is_today else self.line,
                width=2 if is_open and not is_today else 1,
                fill=self.block_color if is_today else self.bg,
            )
            if is_today:
                self._color_hits.append((*day_box, "header:today"))
            self.header.create_text(
                (left + right) / 2,
                self.header_h / 2,
                text=LETTERS[index],
                fill=self.bg if is_today else (self.fg if is_open else self.muted),
                font=("Tahoma", 8, "bold" if is_today or is_open else "normal"),
            )
        for index, (left, right) in enumerate(columns):
            if self.view_day is not None and DAYS[index] != self.view_day:
                continue
            self.body.create_rectangle(left, 0, right, height, outline="", fill=self.bg)
            self.body.create_line(right, 0, right, height, fill=self.line)
        for hour in range(24):
            y = self._y(hour * 60)
            self.body.create_line(self.gutter, y, width, y, fill=self.line)
            self.body.create_text(
                self.gutter - 6,
                y + 8,
                text=_hour_label(hour),
                anchor="e",
                fill=self.muted,
                font=("Tahoma", 8),
            )
        drag = self._drag or {}
        for index, block in enumerate(self.blocks):
            if drag.get("mode") == "resize" and drag.get("index") == index:
                continue
            color, ink, color_key = self._block_paint(block)
            label = block_caption(block)
            for day, start, end in _segments(block):
                self._paint_span(columns, day, start, end, color, label, index, ink, color_key)
        if drag.get("mode") == "create":
            self._paint_span(
                columns, drag["day"], drag["start"], drag["end"], self.preview_color, "", None, self.fg, "preview"
            )
        elif drag.get("mode") == "resize":
            block = self.blocks[drag["index"]]
            color, ink, color_key = self._block_paint(block)
            label = block_caption(block)
            for day in block.days:
                self._paint_span(columns, day, drag["start"], drag["end"], color, label, None, ink, color_key)
        self._paint_now(columns)
        if not self._placed and self.body.winfo_height() > 40:
            self._placed = True
            self.after_idle(lambda: self.body.yview_moveto(6 / 24))

    def _block_paint(self, block: Block) -> tuple[str, str, str]:
        if block.locked or block.lockdown:
            return self.locked_color, "#ffffff", "locked"
        fill = self.pastel_overrides.get(block.color, block_fill(block.color))
        return fill, "#243044", f"pastel:{block.color}"

    def _paint_span(
        self,
        columns,
        day: str,
        start: int,
        end: int,
        color: str,
        label: str,
        index: int | None,
        ink: str | None = None,
        color_key: str | None = None,
    ) -> None:
        if end <= start or (self.view_day is not None and day != self.view_day):
            return
        left, right = columns[DAYS.index(day)]
        top = self._y(start)
        bottom = self._y(end)
        pad = 4
        box = (left + pad, top + 1, right - pad, max(top + 8, bottom - 1))
        self.body.create_rectangle(*box, outline="", fill=color)
        if color_key:
            self._color_hits.append((*box, color_key))
        shown = label
        if bottom - top < 32 and "\n" in shown:
            shown = shown.split("\n", 1)[0]
        if shown and bottom - top >= 16:
            self.body.create_text(
                (box[0] + box[2]) / 2,
                (box[1] + box[3]) / 2,
                text=shown,
                fill=ink or self.fg,
                font=("Tahoma", 8, "bold"),
                width=max(24, box[2] - box[0] - 8),
                justify="center",
            )
        if index is not None:
            self._hits.append((box[0], box[1], box[2], box[3], index))

    def _hit(self, x: float, y: float) -> tuple[int, str] | None:
        canvas_y = self.body.canvasy(y)
        for left, top, right, bottom, index in reversed(self._hits):
            if left <= x <= right and top <= canvas_y <= bottom:
                if canvas_y - top <= 6:
                    return index, "start"
                if bottom - canvas_y <= 6:
                    return index, "end"
                return index, "body"
        return None

    def _press(self, event) -> None:
        minute = self._minute(event.y)
        if minute >= 24 * 60:
            minute = 24 * 60 - 1
        hit = self._hit(event.x, event.y)
        if hit is not None:
            index, where = hit
            block = self.blocks[index]
            start = parse_clock(block.start)
            end = parse_clock(block.end, end=True)
            if where in {"start", "end"} and start is not None and end is not None and start < end:
                self._drag = {
                    "mode": "resize",
                    "index": index,
                    "edge": where,
                    "start": start,
                    "end": end,
                    "moved": False,
                }
                return
            self._drag = {"mode": "click", "index": index, "y": event.y, "moved": False}
            return
        day = self._day_at(event.x)
        if day is None or minute >= 24 * 60:
            self._drag = None
            return
        self._drag = {"mode": "create", "day": day, "anchor": minute, "start": minute, "end": minute}

    def _motion(self, event) -> None:
        drag = self._drag
        if not drag:
            return
        minute = min(24 * 60, self._minute(event.y))
        if drag["mode"] == "create":
            start, end = sorted((drag["anchor"], minute))
            drag["start"], drag["end"] = start, end
            self._draw()
            return
        if drag["mode"] == "click" and abs(event.y - drag["y"]) > 4:
            drag["moved"] = True
            return
        if drag["mode"] == "resize":
            drag["moved"] = True
            if drag["edge"] == "start":
                drag["start"] = min(minute, drag["end"] - 1)
            else:
                drag["end"] = max(minute, drag["start"] + 1)
            self._draw()

    def _release(self, _event) -> None:
        drag = self._drag
        self._drag = None
        if not drag:
            self._draw()
            return
        if drag["mode"] == "create" and drag["end"] - drag["start"] >= 1:
            self.on_create(drag["day"], drag["start"], drag["end"])
            return
        if drag["mode"] == "click" and not drag["moved"]:
            self.on_edit(drag["index"])
            return
        if drag["mode"] == "resize" and drag["moved"] and drag["end"] > drag["start"]:
            self.on_resize(drag["index"], drag["start"], drag["end"])
            return
        self._draw()

    def _header_press(self, event) -> None:
        if event.x < self.gutter:
            if self.view_day is not None:
                self.show_day(None)
            return
        day = self._header_day(event.x)
        if day is None:
            return
        self.show_day(None if self.view_day == day else day)

    def _header_hover(self, event) -> None:
        if event.x < self.gutter:
            self.header.configure(cursor="hand2" if self.view_day is not None else "")
            return
        self.header.configure(cursor="hand2" if self._header_day(event.x) else "")

    def _hover(self, event) -> None:
        if self._drag:
            return
        hit = self._hit(event.x, event.y)
        if hit is None:
            self.body.configure(cursor="crosshair")
        elif hit[1] == "body":
            self.body.configure(cursor="hand2")
        else:
            self.body.configure(cursor="sb_v_double_arrow")


def _hour_label(hour: int) -> str:
    return clock_label(hour * 60)


def _segments(block: Block):
    for span in explode([block]):
        yield span.day, span.start, span.end
