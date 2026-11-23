"""Split a smashed-together name into words.

interactivebrokers has no spaces and no capitals, so the letters are matched
against a list of common English words. A name that is already one word, such
as wikipedia or youtube, stays one word. Interactive Brokers is saved with the
spaces, and a later check still matches interactivebrokers in the address.
"""

from __future__ import annotations

from pathlib import Path


# Whole names that must not become You Tube or Git Hub, plus pieces the common
# list does not contain but site names use.
_KEPT = {
    "aseprite",
    "blender",
    "brokers",
    "cursor",
    "discord",
    "facebook",
    "geeks",
    "github",
    "godot",
    "instagram",
    "khan",
    "netflix",
    "notepad",
    "overflow",
    "reddit",
    "slay",
    "spire",
    "spotify",
    "steam",
    "tiktok",
    "twitch",
    "unreal",
    "youtube",
}

_words: set[str] | None = None


def dictionary() -> set[str]:
    global _words
    if _words is not None:
        return _words
    found = set(_KEPT)
    path = Path(__file__).with_name("words.txt")
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            word = line.strip().casefold()
            if len(word) >= 3 and word.isalpha():
                found.add(word)
    except OSError:
        pass
    _words = found
    return found


def split_words(token: str) -> list[str]:
    text = "".join(ch for ch in (token or "").casefold() if ch.isalpha())
    if not text:
        return []
    known = dictionary()
    if text in known or len(text) < 6:
        return [text]
    best_score = [-1] * (len(text) + 1)
    best_count = [0] * (len(text) + 1)
    previous = [-1] * (len(text) + 1)
    best_score[0] = 0
    for start in range(len(text)):
        if best_score[start] < 0:
            continue
        for end in range(start + 3, min(len(text), start + 24) + 1):
            word = text[start:end]
            if word not in known:
                continue
            score = best_score[start] + len(word) * len(word)
            count = best_count[start] + 1
            if score > best_score[end] or (score == best_score[end] and count < best_count[end]):
                best_score[end] = score
                best_count[end] = count
                previous[end] = start
    if best_score[-1] < 0 or best_count[-1] < 2:
        return [text]
    parts: list[str] = []
    index = len(text)
    while index > 0:
        start = previous[index]
        if start < 0:
            return [text]
        parts.append(text[start:index])
        index = start
    parts.reverse()
    return parts


def _letters(text: str) -> str:
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


_keyword_cache: dict[str, tuple[str, str]] = {}


def listed_in(text: str, keyword: str, letters: str | None = None) -> bool:
    """True when the keyword is in the text, ignoring spaces and punctuation."""
    prepared = _keyword_cache.get(keyword)
    if prepared is None:
        token = " ".join((keyword or "").split()).casefold()
        prepared = (token, _letters(token))
        if len(_keyword_cache) > 256:
            _keyword_cache.clear()
        _keyword_cache[keyword] = prepared
    token, compact = prepared
    if not token:
        return False
    folded = (text or "").casefold()
    if token in folded:
        return True
    if len(compact) < 4:
        return False
    haystack = letters if letters is not None else _letters(folded)
    return compact in haystack
