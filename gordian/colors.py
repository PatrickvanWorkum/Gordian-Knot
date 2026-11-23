"""Hex and HSV for the Shift+` color editor.

Saved colors live in config.json under "colors". A hand-picked color wins over
the theme color for that same widget. "background" is shared by the window and
the title strip.
"""

from __future__ import annotations


def normalize_hex(value: str) -> str | None:
    text = value.strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3 and all(character in "0123456789abcdefABCDEF" for character in text):
        text = "".join(character * 2 for character in text)
    if len(text) != 6 or any(character not in "0123456789abcdefABCDEF" for character in text):
        return None
    return f"#{text.lower()}"


def rgb_to_hex(red: int, green: int, blue: int) -> str:
    return f"#{red:02x}{green:02x}{blue:02x}"


def hex_to_rgb(value: str) -> tuple[int, int, int] | None:
    hex_color = normalize_hex(value)
    if hex_color is None:
        return None
    return tuple(int(hex_color[index : index + 2], 16) for index in (1, 3, 5))


def rgb_to_hsv(red: int, green: int, blue: int) -> tuple[float, float, float]:
    red_f, green_f, blue_f = red / 255, green / 255, blue / 255
    high = max(red_f, green_f, blue_f)
    low = min(red_f, green_f, blue_f)
    delta = high - low
    value = high
    saturation = 0 if high == 0 else delta / high
    if delta == 0:
        hue = 0.0
    elif high == red_f:
        hue = ((green_f - blue_f) / delta) % 6
    elif high == green_f:
        hue = (blue_f - red_f) / delta + 2
    else:
        hue = (red_f - green_f) / delta + 4
    return hue * 60, saturation, value


def chosen_color(saved: dict[str, str], key: str, automatic: str) -> str:
    return saved.get(key, automatic)


def hsv_to_rgb(hue: float, saturation: float, value: float) -> tuple[int, int, int]:
    hue = hue % 360
    chroma = value * saturation
    match = chroma * (1 - abs((hue / 60) % 2 - 1))
    base = value - chroma
    if hue < 60:
        red, green, blue = chroma, match, 0
    elif hue < 120:
        red, green, blue = match, chroma, 0
    elif hue < 180:
        red, green, blue = 0, chroma, match
    elif hue < 240:
        red, green, blue = 0, match, chroma
    elif hue < 300:
        red, green, blue = match, 0, chroma
    else:
        red, green, blue = chroma, 0, match
    return (
        round((red + base) * 255),
        round((green + base) * 255),
        round((blue + base) * 255),
    )
