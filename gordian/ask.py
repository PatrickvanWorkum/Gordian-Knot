"""Decide when a new program or page should be labeled, and when we already know.

The corner question is for something the lists do not mention yet. Reference
sites such as Wikipedia are saved as work without asking. YouTube, Netflix,
and the other familiar reward sites spend without asking.

A YouTube tutorial is the exception. A title like "How to rig a character -
YouTube" asks once, about that video. Work saves that video only. The rest of
YouTube still spends. Spend remembers the video so it does not ask again.

Any other page is named from the address. https://www.interactivebrokers.co.uk
is Interactive Brokers, including when the tab title is only Login. Application
names are split into words the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from gordian.address import capitalise_name, page_host, site_match, website_name
from gordian.words import listed_in
from gordian.scan import matches_list
from gordian.titles import page_label


_PRODUCTIVE = (
    "wikipedia",
    "wiktionary",
    "wikimedia",
    "github",
    "stack overflow",
    "stackoverflow",
    "stack exchange",
    "khan academy",
    "coursera",
    "arxiv",
    "readthedocs",
    "cppreference",
    "w3schools",
    "geeksforgeeks",
    "mdn web docs",
    "developer.mozilla",
    "microsoft learn",
    "docs.python",
    "python documentation",
)

_UNPRODUCTIVE = (
    "youtube",
    "netflix",
    "twitch",
    "tiktok",
    "instagram",
    "reddit",
    "facebook",
    "spotify",
    "disney+",
    "hulu",
    "crunchyroll",
    "prime video",
)

_GENERIC_PAGES = {
    "new tab",
    "new private tab",
    "start page",
    "home",
    "about:blank",
    "google chrome",
    "mozilla firefox",
    "microsoft edge",
    "brave",
    "opera",
    "vivaldi",
    "waterfox",
    "librewolf",
}

_TUTORIAL_PHRASES = (
    "tutorial",
    "how to",
    "how-to",
    "lecture",
    "walkthrough",
    "for beginners",
    "documentation",
)


@dataclass
class AddressGate:
    """The last loaded page, and an address that is still being typed."""

    url: str = ""
    title: str = ""
    typing: str = ""


def page_has_loaded(title: str, url: str) -> bool:
    """A tab title that belongs to a finished page, not the address being typed."""
    page = page_label(title).strip()
    if not page:
        return False
    folded = page.casefold().rstrip(".…")
    if folded in _GENERIC_PAGES or folded in {"loading", "connecting", "waiting", "untitled"}:
        return False
    if folded.startswith(("http://", "https://", "www.")):
        return False
    host = page_host(url)
    bare = host[4:] if host.startswith("www.") else host
    if bare and folded in {host, bare, f"www.{bare}"}:
        return False
    if url and folded == url.strip().casefold().rstrip("/"):
        return False
    return True


def _same_site(left: str, right: str) -> bool:
    from gordian.address import site_token

    first, second = site_token(left), site_token(right)
    return bool(first) and first == second


def loaded_address(gate: AddressGate, title: str, url: str) -> tuple[AddressGate, str]:
    """The address to trust. Empty while a different site is only typed or still loading."""
    seen = (url or "").strip()
    if not seen:
        if gate.url and page_has_loaded(title, gate.url):
            return AddressGate(gate.url, title, ""), gate.url
        return AddressGate("", title, ""), ""
    loaded = page_has_loaded(title, seen)
    same_title = page_label(title) == page_label(gate.title)
    same_site = _same_site(seen, gate.url)
    if seen != gate.url:
        if same_site and (loaded or same_title):
            return AddressGate(seen, title if loaded else gate.title, ""), seen
        if gate.url and same_title:
            return AddressGate(gate.url, gate.title, seen), ""
        if not loaded:
            return AddressGate(seen, title, seen), ""
        if gate.typing and same_title:
            return AddressGate(gate.url, gate.title, seen), ""
        return AddressGate(seen, title, ""), seen
    if gate.typing:
        if loaded:
            return AddressGate(seen, title, ""), seen
        return AddressGate(gate.url, gate.title, gate.typing), ""
    if not loaded:
        return AddressGate(gate.url, gate.title, seen), ""
    return AddressGate(seen, title, ""), seen


@dataclass(frozen=True)
class Ask:
    mode: str
    key: str
    label: str
    target: str
    choice: str = ""
    url: str = ""
    icon_path: str = ""


def consider(process: str, title: str, settings, skipped: set[str] | None = None, path: str = "", fullscreen: bool = False, url: str = "") -> Ask | None:
    name = (process or "").strip()
    if not name:
        return None
    if matches_list(name, settings.ignored_processes):
        return None
    passed = skipped or set()
    if matches_list(name, settings.browser_processes):
        return _page(title, settings, passed, url)
    from gordian.classify import classify

    if classify(name, title or "", settings, path=path, fullscreen=fullscreen) != "neutral":
        return None
    return Ask("app", name, _program_label(name), "app", icon_path=path)


def pin_keyword(
    productive: list[str],
    reward: list[str],
    keyword: str,
    choice: str,
) -> tuple[list[str], list[str]]:
    """Add one exact title or site. A video title does not remove the broader YouTube keyword."""
    key = " ".join((keyword or "").split())
    if not key:
        return list(productive), list(reward)
    folded = key.casefold()
    if choice == "productive":
        reward_left = [item for item in reward if item.strip().casefold() != folded]
        productive_left = list(productive)
        if not any(item.strip().casefold() == folded for item in productive_left):
            productive_left.append(key)
        return productive_left, reward_left
    productive_left = [item for item in productive if item.strip().casefold() != folded]
    reward_left = list(reward)
    if not any(item.strip().casefold() == folded for item in reward_left):
        reward_left.append(key)
    return productive_left, reward_left


def _page(title: str, settings, skipped: set[str], url: str = "") -> Ask | None:
    page = page_label(title)
    match = site_match(url)
    if (not page or page.casefold() in _GENERIC_PAGES) and not match:
        return None
    folded = " ".join(part for part in (title or "", match) if part).casefold()
    if _hit(folded, settings.productive_title_keywords):
        return None
    if "youtube" in folded and page and _tutorial(page) and page.casefold() not in skipped:
        return Ask("tutorial", page, _video_name(page), "site", url=url)
    if _hit(folded, settings.reward_title_keywords):
        return None
    marker = _marker(folded, _UNPRODUCTIVE)
    if marker:
        return Ask("auto", marker, marker, "site", "unproductive")
    marker = _marker(folded, _PRODUCTIVE)
    if marker:
        return Ask("auto", marker, marker, "site", "productive")
    named = website_name(url)
    if named:
        return Ask("site", named, named, "site", url=url)
    site = _site_name(page)
    if not site:
        return None
    return Ask("site", site, site, "site")


def _hit(text: str, keywords: list[str]) -> bool:
    return any(listed_in(text, keyword) for keyword in keywords)


def _marker(text: str, markers: tuple[str, ...]) -> str:
    for marker in markers:
        if marker in text:
            return marker
    return ""


def _tutorial(page: str) -> bool:
    folded = page.casefold()
    if any(phrase in folded for phrase in _TUTORIAL_PHRASES):
        return True
    return re.search(r"\b(course|lesson)\b", folded) is not None


def _video_name(page: str) -> str:
    for suffix in (" - YouTube", " — YouTube", " – YouTube"):
        if page.casefold().endswith(suffix.casefold()):
            return page[: -len(suffix)].strip() or page
    return page


def _site_name(page: str) -> str:
    for separator in (" — ", " – ", " - "):
        if separator in page:
            tail = page.rsplit(separator, 1)[-1].strip()
            if 2 <= len(tail) <= 40 and tail.casefold() not in _GENERIC_PAGES:
                return capitalise_name(tail)
    text = page.strip()
    if len(text) > 80:
        text = text[:80].rstrip()
    return capitalise_name(text)


def _program_label(process: str) -> str:
    name = process.strip()
    if name.casefold().endswith(".exe"):
        name = name[:-4]
    return capitalise_name(name) or process
