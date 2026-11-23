"""The Gordian ring icon: draw it, write gordian.ico, embed it in GordianKnot.exe."""

from __future__ import annotations

import ctypes
import os
import struct
from ctypes import wintypes
from pathlib import Path

from gordian.paths import data_dir, log_exception

RT_ICON = 3
RT_GROUP_ICON = 14
LANG_NEUTRAL = 0x0409


def draw_icon(color: str):
    from PIL import Image, ImageDraw

    red = int(color[1:3], 16)
    green = int(color[3:5], 16)
    blue = int(color[5:7], 16)
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, 61, 61), fill=(red, green, blue, 255))
    draw.ellipse((20, 20, 43, 43), fill=(20, 22, 26, 255))
    return image


def ensure_application_icon(color: str = "#3dd68c") -> Path | None:
    try:
        from PIL import Image
    except Exception:
        return None
    path = data_dir() / "gordian.ico"
    image = draw_icon(color)
    image.save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    return path


def embed_icon_in_exe(exe: Path, ico: Path) -> None:
    if os.name != "nt" or not exe.is_file() or not ico.is_file():
        return
    data = ico.read_bytes()
    if len(data) < 6 or data[:4] != b"\x00\x00\x01\x00":
        raise ValueError("not a valid .ico file")
    (icon_count,) = struct.unpack_from("<H", data, 4)
    if icon_count < 1:
        raise ValueError("ico has no images")

    entries: list[tuple] = []
    images: list[bytes] = []
    offset = 6
    for index in range(icon_count):
        if offset + 16 > len(data):
            raise ValueError("truncated ico directory")
        entry = struct.unpack_from("<BBBBHHII", data, offset)
        entries.append(entry)
        _width, _height, _colors, _reserved, _planes, _bit_count, nbytes, image_offset = entry
        if image_offset + nbytes > len(data):
            raise ValueError("truncated ico image")
        images.append(data[image_offset : image_offset + nbytes])
        offset += 16

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    begin = kernel32.BeginUpdateResourceW
    begin.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    begin.restype = wintypes.HANDLE
    update = kernel32.UpdateResourceW
    update.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.WORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    update.restype = wintypes.BOOL
    end = kernel32.EndUpdateResourceW
    end.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    end.restype = wintypes.BOOL

    handle = begin(str(exe), False)
    if not handle:
        raise OSError("BeginUpdateResource failed")
    try:
        for index, blob in enumerate(images, start=1):
            buffer = ctypes.create_string_buffer(blob, len(blob))
            if not update(handle, ctypes.c_void_p(RT_ICON), ctypes.c_void_p(index), LANG_NEUTRAL, buffer, len(blob)):
                raise OSError(f"UpdateResource icon failed ({ctypes.get_last_error()})")
        group_body = b""
        for index, entry in enumerate(entries, start=1):
            width, height, colors, reserved, planes, bit_count, nbytes, _image_offset = entry
            w = 0 if width == 256 else width
            h = 0 if height == 256 else height
            group_body += struct.pack("<BBBBHHIH", w, h, colors, reserved, planes, bit_count, nbytes, index)
        group = struct.pack("<HHH", 0, 1, icon_count) + group_body
        group_buffer = ctypes.create_string_buffer(group, len(group))
        if not update(
            handle, ctypes.c_void_p(RT_GROUP_ICON), ctypes.c_void_p(1), LANG_NEUTRAL, group_buffer, len(group)
        ):
            raise OSError(f"UpdateResource group failed ({ctypes.get_last_error()})")
        if not end(handle, False):
            raise OSError(f"EndUpdateResource failed ({ctypes.get_last_error()})")
        handle = None
    finally:
        if handle:
            end(handle, True)


def apply_tk_window_icon(root, ico: Path, color: str = "#3dd68c") -> None:
    path = ico if ico.is_file() else ensure_application_icon(color)
    if path is None or not path.is_file():
        return
    try:
        from PIL import ImageTk

        photo = ImageTk.PhotoImage(draw_icon(color))
        root._gordian_icon_photo = photo  # type: ignore[attr-defined]
        root.iconphoto(True, photo)
    except Exception:
        log_exception("window icon photo")
    if os.name != "nt":
        try:
            root.iconbitmap(str(path))
        except Exception:
            pass
        return
    try:
        root.iconbitmap(str(path))
    except Exception:
        pass
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        LR_LOADFROMFILE = 0x0010
        IMAGE_ICON = 1
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        for size in (16, 32):
            hicon = user32.LoadImageW(
                None, str(path), IMAGE_ICON, size, size, LR_LOADFROMFILE
            )
            if hicon:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL if size == 16 else ICON_BIG, hicon)
    except Exception:
        log_exception("window icon hwnd")