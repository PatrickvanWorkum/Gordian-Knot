"""The small logo a website publishes, shown beside its name.

The picture is the site's own favicon. It is kept under
%APPDATA%\\GordianKnot\\favicons so the next visit does not download it again.
A site with no logo is left blank.
"""

from __future__ import annotations

import io
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

from PIL import Image

from gordian.address import page_host
from gordian.paths import data_dir


_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 GordianKnot"
_LOCK = threading.Lock()
_MEMORY: dict[str, Image.Image | None] = {}
_LINK = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_REL = re.compile(r"""rel\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def favicon_image(url: str):
    host = page_host(url)
    if not host or "." not in host:
        return None
    with _LOCK:
        if host in _MEMORY:
            return _MEMORY[host]
    cached = _read_cache(host)
    if cached is not None:
        with _LOCK:
            _MEMORY[host] = cached
        return cached
    image = _download_icon(host)
    with _LOCK:
        _MEMORY[host] = image
    if image is not None:
        _write_cache(host, image)
    return image


def icon_hrefs(html: str) -> list[str]:
    found: list[str] = []
    for tag in _LINK.findall(html or ""):
        rel = _REL.search(tag)
        href = _HREF.search(tag)
        if rel is None or href is None:
            continue
        kinds = rel.group(1).casefold().replace("-", " ").split()
        if "icon" not in kinds:
            continue
        link = href.group(1).strip()
        if link and not link.casefold().split("?", 1)[0].endswith(".svg"):
            found.append(link)
    return found


def _download_icon(host: str):
    page = f"https://{host}/"
    for target in (urljoin(page, "/favicon.ico"), urljoin(page, "/favicon.png")):
        image = _fetch_image(target)
        if image is not None:
            return image
    html = _fetch_text(page)
    for href in icon_hrefs(html):
        image = _fetch_image(urljoin(page, href))
        if image is not None:
            return image
    return None


def _fetch_text(url: str) -> str:
    data = _fetch_bytes(url, 80_000)
    if not data:
        return ""
    return data.decode("utf-8", errors="ignore")


def _fetch_image(url: str):
    if not url.casefold().startswith(("https://", "http://")):
        return None
    if url.casefold().split("?", 1)[0].endswith(".svg"):
        return None
    data = _fetch_bytes(url, 500_000)
    if not data:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        return None
    image = image.convert("RGBA")
    if max(image.size) > 64:
        image.thumbnail((64, 64), Image.Resampling.LANCZOS)
    return image


def _fetch_bytes(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _AGENT})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            chunks = []
            total = 0
            while total < limit:
                block = response.read(min(8192, limit - total))
                if not block:
                    break
                chunks.append(block)
                total += len(block)
            return b"".join(chunks)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return b""


def _cache_path(host: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in ".-" else "_" for ch in host.casefold())
    return data_dir() / "favicons" / f"{safe}.png"


def _read_cache(host: str):
    path = _cache_path(host)
    if not path.exists():
        return None
    try:
        image = Image.open(path)
        image.load()
        return image.convert("RGBA")
    except Exception:
        return None


def _write_cache(host: str, image) -> None:
    path = _cache_path(host)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="PNG")
    except OSError:
        return
