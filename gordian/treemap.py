"""Work and unproductive time packed like a disk map.

The two blocks start the same size. Work then grows linearly with the W:R.
The unproductive block is the time spent on those programs. Once free time is
below zero, that block grows as e raised to the W:R times the overspent minutes.
Programs fill each block in a squarified map, the way a disk map keeps each
file close to a square.
"""

from __future__ import annotations

import math
import tkinter as tk

from gordian.formatting import format_duration


def balance_areas(
    work_seconds: float,
    unproductive_seconds: float,
    remaining_seconds: float,
    work_minutes: float,
    reward_minutes: float,
) -> tuple[float, float]:
    """Green and red block sizes. They start equal, at 1 and 1.

    Green then grows as the work minutes times the W:R. Red is the unproductive
    minutes. Below zero free time, red is multiplied by e raised to the W:R
    times the overspent minutes.
    """
    ratio = work_minutes / reward_minutes if reward_minutes > 0 else 1.0
    worked = max(0.0, work_seconds) / 60.0
    spent = max(0.0, unproductive_seconds) / 60.0
    if worked <= 0 and spent <= 0 and remaining_seconds >= 0:
        return 1.0, 1.0
    green = worked * ratio
    red = spent
    if remaining_seconds < 0:
        factor = math.exp(ratio * (abs(remaining_seconds) / 60.0))
        red = spent * factor if spent > 0 else factor
    return green, red


def pack_squares(items: list[tuple[str, float]], x: float, y: float, width: float, height: float) -> list[tuple]:
    """Pack named amounts into rectangles that stay as square as the box allows.

    Each result is (x, y, width, height, name, value).
    """
    clean = [(str(name), float(value)) for name, value in items if value and float(value) > 0]
    total = sum(value for _name, value in clean)
    if total <= 0 or width <= 1 or height <= 1:
        return []
    area = width * height
    scaled = [(name, value / total * area) for name, value in clean]
    scaled.sort(key=lambda item: item[1], reverse=True)
    return _squarify(scaled, x, y, width, height)


def _worst(areas: list[float], length: float) -> float:
    """Largest side ratio in a row laid along ``length``. Closer to 1 is squarer."""
    if not areas or length <= 0:
        return float("inf")
    side = sum(areas) / length
    if side <= 0:
        return float("inf")
    score = 1.0
    for area in areas:
        other = area / side
        if other <= 0:
            return float("inf")
        score = max(score, max(side, other) / min(side, other))
    return score


def _squarify(items, x, y, width, height):
    if not items or width <= 1 or height <= 1:
        return []
    if len(items) == 1:
        name, area = items[0]
        return [(x, y, width, height, name, area)]
    length = width if width >= height else height
    row = [items[0]]
    best = _worst([items[0][1]], length)
    index = 1
    while index < len(items):
        score = _worst([area for _name, area in row] + [items[index][1]], length)
        if score > best:
            break
        row.append(items[index])
        best = score
        index += 1
    placed, x, y, width, height = _lay(row, x, y, width, height)
    return placed + _squarify(items[index:], x, y, width, height)


def _lay(row, x, y, width, height):
    if not row or width <= 0 or height <= 0:
        return [], x, y, width, height
    total = sum(area for _name, area in row)
    rects = []
    if width <= height:
        side = total / height if height else 0
        cursor = y
        for name, area in row:
            block = area / side if side else 0
            rects.append((x, cursor, side, block, name, area))
            cursor += block
        return rects, x + side, y, max(0.0, width - side), height
    side = total / width if width else 0
    cursor = x
    for name, area in row:
        block = area / side if side else 0
        rects.append((cursor, y, block, side, name, area))
        cursor += block
    return rects, x, y + side, width, max(0.0, height - side)


# One colour for each kind of program, still inside the green and red families.
_WORK_GROUPS = {
    "code": (22, 128, 58),
    "site": (18, 138, 112),
    "tool": (64, 132, 52),
}
_SPEND_GROUPS = {
    "game": (168, 42, 46),
    "store": (138, 36, 78),
    "video": (176, 64, 72),
}
_CODE = ("cursor", "code", "devenv", "godot", "unity", "blender", "rider", "pycharm", "idea64", "aseprite")
_STORES = ("steam", "epic", "gog", "xbox", "battlenet")
_VIDEO = ("yt", "youtube", "netflix", "twitch", "tiktok", "disney", "crunchyroll")


def tile_group(name: str, side: str) -> str:
    """The family a program belongs to. Pause is its own family."""
    folded = name.casefold().removesuffix(".exe")
    if side == "pause" or folded == "paused":
        return "pause"
    if side == "spend":
        if folded in _VIDEO or any(word in folded for word in _VIDEO):
            return "video"
        if any(word in folded for word in _STORES):
            return "store"
        return "game"
    if any(word in folded for word in _CODE):
        return "code"
    if name.casefold().endswith(".exe"):
        return "tool"
    return "site"


def group_color(name: str, side: str) -> tuple[int, int, int]:
    group = tile_group(name, side)
    if side == "spend":
        return _SPEND_GROUPS.get(group, _SPEND_GROUPS["game"])
    return _WORK_GROUPS.get(group, _WORK_GROUPS["tool"])


def _shift(rgb: tuple[int, int, int], light: float) -> str:
    def channel(value: int) -> int:
        if light >= 0:
            mixed = value + (255 - value) * light
        else:
            mixed = value * (1 + light)
        return max(0, min(255, int(mixed)))

    return f"#{channel(rgb[0]):02x}{channel(rgb[1]):02x}{channel(rgb[2]):02x}"


def group_youtube(rows: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Unproductive YouTube pages share one YT total."""
    spent = 0.0
    kept: list[tuple[str, float]] = []
    for name, value in rows:
        if "youtube" in name.casefold():
            spent += value
        else:
            kept.append((name, value))
    if spent > 0:
        kept.append(("YT", spent))
    return sorted(kept, key=lambda item: (-item[1], item[0].casefold()))


def _light_image(width: float, height: float, rgb: tuple[int, int, int]) -> tk.PhotoImage:
    """A flat colour with a soft light from the top, gentler than a disk map."""
    tile_w = max(1, int(round(width)))
    tile_h = max(1, int(round(height)))
    scale = max(1, max(tile_w, tile_h) // 36)
    sample_w = max(1, math.ceil(tile_w / scale))
    sample_h = max(1, math.ceil(tile_h / scale))
    rows = []
    for y in range(sample_h):
        fy = (y + 0.5) / sample_h
        light = 0.16 * (1.0 - fy) - 0.05 * fy
        color = _shift(rgb, light)
        rows.append("{" + " ".join([color] * sample_w) + "}")
    image = tk.PhotoImage(width=sample_w, height=sample_h)
    image.put(" ".join(rows))
    if scale == 1:
        return image
    zoomed = image.zoom(scale, scale)
    if zoomed.width() == tile_w and zoomed.height() == tile_h:
        return zoomed
    cropped = tk.PhotoImage(width=tile_w, height=tile_h)
    cropped.tk.call(cropped, "copy", zoomed, "-from", 0, 0, min(tile_w, zoomed.width()), min(tile_h, zoomed.height()), "-to", 0, 0)
    return cropped


def _parse_color(color: str) -> tuple[int, int, int]:
    text = (color or "").lstrip("#")
    if len(text) != 6:
        return (210, 181, 107)
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def _ink(color: str) -> str:
    red = int(color[1:3], 16)
    green = int(color[3:5], 16)
    blue = int(color[5:7], 16)
    light = (red * 299 + green * 587 + blue * 114) / 1000
    return "#1a1a1a" if light > 150 else "#f4f4f4"


class UsageMap(tk.Canvas):
    """The two blocks, and the programs packed inside them."""

    def __init__(self, parent, text: str, muted: str, track: str) -> None:
        super().__init__(parent, bg=track, highlightthickness=0, bd=0, height=220)
        self.text_color = text
        self.muted_color = muted
        self.canvas_bg = track
        self.face = ("Tahoma", 8)
        self.earn_rows: list[tuple[str, float]] = []
        self.spend_rows: list[tuple[str, float]] = []
        self.green_area = 1.0
        self.red_area = 1.0
        self.pause_color = "#d2b56b"
        self._tiles: list[tuple[float, float, float, float, str, float]] = []
        self._faces: dict[tuple[int, int, tuple[int, int, int]], tk.PhotoImage] = {}
        self._hover = ""
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _event: self._set_hover(""))

    def set_picture(
        self,
        earn_rows: list[tuple[str, float]],
        spend_rows: list[tuple[str, float]],
        green_area: float,
        red_area: float,
    ) -> None:
        next_earn = list(earn_rows)
        next_spend = list(spend_rows)
        if (
            next_earn == self.earn_rows
            and next_spend == self.spend_rows
            and green_area == self.green_area
            and red_area == self.red_area
        ):
            return
        self.earn_rows = next_earn
        self.spend_rows = next_spend
        self.green_area = green_area
        self.red_area = red_area
        self._draw()

    def _set_hover(self, text: str) -> None:
        if text == self._hover:
            return
        self._hover = text
        self._draw()

    def _on_motion(self, event) -> None:
        for left, top, width, height, name, value in self._tiles:
            if left <= event.x <= left + width and top <= event.y <= top + height:
                self._set_hover(f"{name} · {format_duration(value)}")
                return
        self._set_hover("")

    def _draw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 2)
        height = max(self.winfo_height(), 2)
        self.create_rectangle(0, 0, width, height, fill=self.canvas_bg, outline="")
        footer = 22
        margin = 8
        body_bottom = height - footer
        if body_bottom - margin < 40:
            return
        total = self.green_area + self.red_area
        if total <= 0:
            total = 1.0
        gap = 10
        inner_w = width - margin * 2 - gap
        red_w = inner_w * (self.red_area / total)
        green_w = inner_w - red_w
        top = margin
        block_h = body_bottom - margin
        self._tiles = []
        self._paint_block(margin, top, red_w, block_h, self.spend_rows, "spend", (92, 24, 28), "Unproductive")
        self._paint_block(margin + red_w + gap, top, green_w, block_h, self.earn_rows, "work", (12, 64, 32), "Work")
        caption = self._hover or "Work grows with the W:R. The red block is the unproductive time."
        self.create_text(margin, height - 12, text=caption, fill=self.muted_color, anchor="w", font=self.face)

    def _paint_block(self, x, y, width, height, rows, side: str, frame, title: str) -> None:
        if width < 2 or height < 2:
            return
        border = 2
        self.create_rectangle(x, y, x + width, y + height, fill=_shift(frame, 0), outline="")
        ordered = sorted(((name, value) for name, value in rows if value > 0), key=lambda item: item[1], reverse=True)
        if not ordered or width <= border * 2 + 4 or height <= border * 2 + 4:
            self.create_text(x + width / 2, y + height / 2, text=title, fill=_ink(_shift(frame, 0.2)), font=self.face)
            return
        packed = pack_squares(
            ordered,
            x + border,
            y + border,
            width - border * 2,
            height - border * 2,
        )
        values = {name: value for name, value in ordered}
        gutter = 1.5
        for left, top, block_w, block_h, name, _area in packed:
            tone = self._tile_tone(name, side)
            inset_w = max(1.0, block_w - gutter)
            inset_h = max(1.0, block_h - gutter)
            self._paint_face(left, top, inset_w, inset_h, tone)
            seconds = values.get(name, 0.0)
            self._tiles.append((left, top, inset_w, inset_h, name, seconds))
            if inset_w > 46 and inset_h > 18:
                label = name if len(name) < 22 else name[:21] + "…"
                self.create_text(left + 6, top + 5, text=label, fill=_ink(_shift(tone, 0.35)), anchor="nw", font=self.face)

    def _tile_tone(self, name: str, side: str) -> tuple[int, int, int]:
        if name == "Paused":
            return _parse_color(self.pause_color)
        return group_color(name, side)

    def _paint_face(self, left: float, top: float, width: float, height: float, tone: tuple[int, int, int]) -> None:
        if width < 2 or height < 2:
            return
        if width < 8 or height < 8:
            self.create_rectangle(left, top, left + width, top + height, fill=_shift(tone, 0.04), outline="")
            return
        key = (int(width), int(height), tone)
        face = self._faces.get(key)
        if face is None:
            if len(self._faces) > 80:
                self._faces.clear()
            face = _light_image(width, height, tone)
            self._faces[key] = face
        self.create_image(left, top, image=face, anchor="nw")
