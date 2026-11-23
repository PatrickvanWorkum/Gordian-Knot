"""Find installed programs and the sites you actually visit.

Programs come from the uninstall registry, Start menu and Desktop shortcuts,
and a walk of every fixed and removable drive. Browsers are not listed as
exes. Their history databases are copied out (the live file is locked) and
turned into sites. youtube.com is always included. Junk launchers, installers,
and Windows system exes are skipped. An icon is read when Windows has one.

The Work / Spend choice in the dialog is only a suggestion until Lock in.
Programs already on the productive list start as Work. Reward-list programs,
hinted games, and anything found only by walking a drive start as Spend.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import sqlite3
import string
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class FoundProgram:
    name: str
    executable: str
    path: str
    reason: str
    kind: str = "program"
    icon: str = ""


HINTS = (
    ("steamapps", "a Steam game"),
    ("steam", "Steam"),
    ("epic games", "Epic"),
    ("epicgames", "Epic"),
    ("easyanticheat", "a game"),
    ("battlenet", "Battle.net"),
    ("battle.net", "Battle.net"),
    ("gog galaxy", "GOG"),
    ("galaxyclient", "GOG"),
    ("ubisoft", "Ubisoft"),
    ("uplay", "Ubisoft"),
    ("origin.exe", "EA"),
    ("eadesktop", "EA"),
    ("electronic arts", "EA"),
    ("riot client", "Riot"),
    ("league of legends", "Riot"),
    ("valorant", "Riot"),
    ("xbox", "Xbox"),
    ("minecraft", "Minecraft"),
    ("roblox", "Roblox"),
    ("discord", "Discord"),
    ("spotify", "Spotify"),
    ("twitch", "Twitch"),
    ("netflix", "Netflix"),
    ("paradox", "Paradox"),
    ("chrome.exe", "a browser"),
    ("firefox.exe", "a browser"),
    ("msedge.exe", "a browser"),
    ("brave.exe", "a browser"),
    ("opera.exe", "a browser"),
)

SKIP_NAMES = (
    "redistributable",
    "visual c++",
    "windows sdk",
    "webview",
    "runtime",
    "driver",
    "update for",
    ".net ",
    "python",
)


def executable_name(text: str) -> str:
    cleaned = (text or "").replace("/", "\\").split(",")[0].strip().strip('"')
    base = cleaned.split("\\")[-1].strip()
    if base.casefold().endswith(".exe"):
        return base
    return ""


def classify_candidate(name: str, path: str) -> str | None:
    folded_name = name.casefold()
    if any(skip in folded_name for skip in SKIP_NAMES):
        return None
    blob = f"{name} {path}".casefold()
    for needle, reason in HINTS:
        if needle in blob:
            return reason
    return None


def programs_from_entries(entries: list[tuple[str, str, str]]) -> list[FoundProgram]:
    found: dict[str, FoundProgram] = {}
    for name, icon, location in entries:
        display = " ".join((name or "").split())
        if not display:
            continue
        reason = classify_candidate(display, f"{icon} {location}")
        if reason is None:
            continue
        executable = executable_name(icon) or executable_name(location)
        if not executable:
            continue
        key = executable.casefold()
        if key in found:
            continue
        found[key] = FoundProgram(display, executable, " ".join((location or icon or "").split()), reason)
    return sorted(found.values(), key=lambda item: item.name.casefold())


def matches_list(name: str, patterns: list[str]) -> bool:
    folded = name.casefold()
    return any(fnmatch.fnmatch(folded, pattern.casefold()) for pattern in patterns if pattern.strip())


def _specific(pattern: str, executable: str) -> bool:
    folded = pattern.strip().casefold()
    if folded in {"*", "*.*", "*.exe"}:
        return False
    return fnmatch.fnmatch(executable.casefold(), folded)


def lock_choices(
    productive: list[str],
    reward: list[str],
    choices: list[tuple[str, str]],
) -> tuple[list[str], list[str]]:
    productive_left = list(productive)
    reward_left = list(reward)
    for executable, choice in choices:
        if choice == "productive":
            reward_left = [item for item in reward_left if not _specific(item, executable)]
            if not matches_list(executable, productive_left):
                productive_left.append(executable)
        elif choice == "unproductive":
            productive_left = [item for item in productive_left if not _specific(item, executable)]
            if not matches_list(executable, reward_left):
                reward_left.append(executable)
    return productive_left, reward_left


def scan_installed() -> list[FoundProgram]:
    try:
        import winreg
    except ImportError:
        return []
    entries: list[tuple[str, str, str]] = []
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, path in roots:
        entries.extend(_read_uninstall(winreg, hive, path))
    return programs_from_entries(entries)


def _read_uninstall(winreg, hive, path: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    try:
        key = winreg.OpenKey(hive, path)
    except OSError:
        return rows
    try:
        count = winreg.QueryInfoKey(key)[0]
        for index in range(count):
            try:
                name = winreg.EnumKey(key, index)
                sub = winreg.OpenKey(key, name)
            except OSError:
                continue
            try:
                if _reg_dword(winreg, sub, "SystemComponent") == 1:
                    continue
                display = _reg_str(winreg, sub, "DisplayName")
                if not display:
                    continue
                icon = _reg_str(winreg, sub, "DisplayIcon")
                location = _reg_str(winreg, sub, "InstallLocation")
                publisher = _reg_str(winreg, sub, "Publisher")
                rows.append((display, icon, f"{location}\n{publisher}"))
            finally:
                winreg.CloseKey(sub)
    finally:
        winreg.CloseKey(key)
    return rows


def _reg_str(winreg, key, name: str) -> str:
    try:
        value, kind = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    if kind == winreg.REG_SZ or kind == getattr(winreg, "REG_EXPAND_SZ", 2):
        return str(value)
    return ""


BROWSERS = frozenset(
    {
        "firefox.exe",
        "chrome.exe",
        "msedge.exe",
        "brave.exe",
        "opera.exe",
        "vivaldi.exe",
        "waterfox.exe",
        "librewolf.exe",
    }
)

JUNK_WORDS = (
    "uninstall",
    "unins",
    "setup",
    "installer",
    "updater",
    "update",
    "crash",
    "redist",
    "helper",
    "report",
    "vcredist",
    "dxsetup",
    "proxy",
    "service",
    "container",
    "broker",
    "register",
    "elevation",
    "maintenance",
    "crashpad",
    "pingsender",
    "arpproducticon",
    "cache-gen",
    "browsercore",
    "wrapper",
    "wizard",
    "scanner",
    "xboxutil",
    "svc",
)

GENERIC_FOLDERS = frozenset(
    {
        "bin",
        "x64",
        "x86",
        "drivers",
        "temp",
        "lib",
        "libexec",
        "resources",
        "locales",
        "redist",
        "core",
        "app",
        "program",
        "sdk",
        "win64",
        "win32",
        "engine",
        "system32",
        "syswow64",
    }
)

SYSTEM_EXECUTABLES = frozenset(
    {
        "csrss.exe",
        "lsass.exe",
        "services.exe",
        "svchost.exe",
        "winlogon.exe",
        "dwm.exe",
        "smss.exe",
        "msiexec.exe",
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "conhost.exe",
        "rundll32.exe",
        "dllhost.exe",
        "taskhostw.exe",
        "sihost.exe",
        "explorer.exe",
    }
)

BROWSER_FOLDERS = (
    "\\google\\chrome\\",
    "\\microsoft\\edge\\",
    "\\mozilla firefox\\",
    "\\bravesoftware\\",
    "\\opera software\\",
    "\\vivaldi\\",
)

SKIP_ROOTS = frozenset(
    {
        "windows",
        "users",
        "programdata",
        "$recycle.bin",
        "system volume information",
        "recovery",
        "perflogs",
        "documents and settings",
    }
)

DEEP_ROOTS = frozenset(
    {
        "program files",
        "program files (x86)",
        "games",
        "steam",
        "steamlibrary",
        "epic games",
        "gog games",
        "ubisoft",
        "xboxgames",
    }
)

SITE_HINTS = (
    "youtube",
    "netflix",
    "twitch",
    "reddit",
    "tiktok",
    "instagram",
    "facebook",
    "twitter",
    "x.com",
    "discord",
    "spotify",
    "steam",
)


def is_browser(executable: str) -> bool:
    return executable.casefold() in BROWSERS


def is_junk(executable: str) -> bool:
    folded = executable.casefold()
    if folded in SYSTEM_EXECUTABLES:
        return True
    stem = Path(executable).stem.casefold()
    return any(word in stem for word in JUNK_WORDS)


def _blocked_path(path: str) -> bool:
    folded = path.replace("/", "\\").casefold()
    if "\\windows\\" in folded or "\\winsxs\\" in folded or "\\windowsapps\\" in folded:
        return True
    return any(folder in folded for folder in BROWSER_FOLDERS)


def _name_matches(folder_name: str, executable: str) -> bool:
    folded = "".join(ch for ch in folder_name.casefold() if ch.isalnum())
    stem = "".join(ch for ch in Path(executable).stem.casefold() if ch.isalnum())
    if len(folded) < 2 or len(stem) < 2:
        return False
    return stem in folded or folded in stem


def _listing_name(folder: str, executable: str) -> str:
    path = Path(folder)
    folded_parts = [part.casefold() for part in path.parts]
    if "steamapps" in folded_parts and "common" in folded_parts:
        index = folded_parts.index("common")
        if index + 1 < len(path.parts):
            return path.parts[index + 1]
    for candidate in (path, path.parent, path.parent.parent):
        if candidate.name.casefold() in GENERIC_FOLDERS or len(candidate.name) < 2:
            continue
        if _name_matches(candidate.name, executable):
            return candidate.name
    return ""


def _site_hit(pattern: str, site: str) -> bool:
    left = pattern.strip().casefold()
    right = site.strip().casefold()
    if not left or left in {"*", "*.*", "*.exe"}:
        return False
    return left == right or left in right or right in left


def lock_sites(
    productive: list[str],
    reward: list[str],
    choices: list[tuple[str, str]],
) -> tuple[list[str], list[str]]:
    productive_left = list(productive)
    reward_left = list(reward)
    for site, choice in choices:
        if choice == "productive":
            reward_left = [item for item in reward_left if not _site_hit(item, site)]
            if not any(_site_hit(item, site) for item in productive_left):
                productive_left.append(site)
        elif choice == "unproductive":
            productive_left = [item for item in productive_left if not _site_hit(item, site)]
            if not any(_site_hit(item, site) for item in reward_left):
                reward_left.append(site)
    return productive_left, reward_left


def starting_choice(
    program: FoundProgram,
    productive: list[str],
    reward: list[str],
    productive_sites: list[str],
    reward_sites: list[str],
) -> str:
    if program.kind == "site":
        if any(_site_hit(item, program.executable) for item in productive_sites):
            return "productive"
        if any(_site_hit(item, program.executable) for item in reward_sites):
            return "unproductive"
        if any(hint in program.executable.casefold() for hint in SITE_HINTS):
            return "unproductive"
        return "productive"
    if matches_list(program.executable, productive):
        return "productive"
    if program.reason == "On this drive" or matches_list(program.executable, reward):
        return "unproductive"
    if program.reason not in {"Installed", "Start menu"}:
        return "unproductive"
    return "productive"


def list_programs(entries: list[tuple[str, str, str]]) -> list[FoundProgram]:
    found: dict[str, FoundProgram] = {}
    for name, icon, location in entries:
        display = " ".join((name or "").split())
        if not display or any(skip in display.casefold() for skip in SKIP_NAMES):
            continue
        if display.casefold().startswith(("uninstall", "install ")):
            continue
        folder, publisher = _split_location(location)
        executable, icon_path = _best_executable(icon, folder, display)
        if not executable or is_browser(executable) or is_junk(executable) or _blocked_path(icon_path or folder):
            continue
        key = executable.casefold()
        if key in found:
            continue
        reason = classify_candidate(f"{display} {publisher}", f"{icon} {folder}") or "Installed"
        found[key] = FoundProgram(display, executable, folder or icon_path, reason, "program", icon_path)
    return sorted(found.values(), key=lambda item: item.name.casefold())


def sites_from_urls(rows: list[tuple[str, int, str]]) -> list[FoundProgram]:
    totals: dict[str, tuple[int, str]] = {}
    for url, visits, browser in rows:
        host = _host(url)
        if not host:
            continue
        count, label = totals.get(host, (0, browser))
        totals[host] = (count + max(visits, 1), label or browser)
    if "youtube.com" not in totals:
        totals["youtube.com"] = (0, "")
    found = []
    for host, (count, browser) in totals.items():
        if browser:
            reason = f"Visited in {browser}"
        elif host == "youtube.com":
            reason = "Watched as a page title"
        else:
            reason = "A website"
        found.append(FoundProgram(host, host, "", reason, "site", ""))
    found.sort(key=lambda item: (-totals[item.executable][0], item.name))
    return found[:80]


def domains_in(path: Path, browser: str) -> list[tuple[str, int, str]]:
    temporary = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite")
    temporary.close()
    try:
        shutil.copy2(path, temporary.name)
        connection = sqlite3.connect(temporary.name)
        try:
            if browser == "Firefox":
                query = "SELECT url, visit_count FROM moz_places ORDER BY visit_count DESC LIMIT 400"
            else:
                query = "SELECT url, visit_count FROM urls ORDER BY visit_count DESC LIMIT 400"
            return [(str(url), int(visits or 0), browser) for url, visits in connection.execute(query)]
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return []
    finally:
        try:
            os.remove(temporary.name)
        except OSError:
            pass


def history_databases() -> list[tuple[str, Path]]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    roaming = Path(os.environ.get("APPDATA", ""))
    roots = (
        ("Chrome", local / "Google" / "Chrome" / "User Data"),
        ("Edge", local / "Microsoft" / "Edge" / "User Data"),
        ("Brave", local / "BraveSoftware" / "Brave-Browser" / "User Data"),
        ("Vivaldi", local / "Vivaldi" / "User Data"),
        ("Opera", roaming / "Opera Software" / "Opera Stable"),
    )
    found: list[tuple[str, Path]] = []
    for label, root in roots:
        if (root / "History").is_file():
            found.append((label, root / "History"))
        if not root.is_dir():
            continue
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            history = child / "History"
            folded = child.name.casefold()
            if history.is_file() and (folded == "default" or folded.startswith("profile")):
                found.append((label, history))
    firefox = roaming / "Mozilla" / "Firefox" / "Profiles"
    if firefox.is_dir():
        for database in firefox.glob("*/places.sqlite"):
            found.append(("Firefox", database))
    return found


def fixed_drives() -> list[str]:
    try:
        import ctypes
    except ImportError:
        return []
    kernel32 = ctypes.windll.kernel32
    kernel32.GetLogicalDrives.restype = ctypes.c_uint32
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetDriveTypeW.restype = ctypes.c_uint
    bits = kernel32.GetLogicalDrives()
    drives = []
    for index, letter in enumerate(string.ascii_uppercase):
        if not bits & (1 << index):
            continue
        root = f"{letter}:\\"
        if kernel32.GetDriveTypeW(root) in (2, 3):
            drives.append(root)
    return drives


def programs_on_drive(root: str) -> list[FoundProgram]:
    found: list[FoundProgram] = []
    try:
        children = list(os.scandir(root))
    except OSError:
        return found
    for child in children:
        try:
            if not child.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        folded = child.name.casefold()
        if folded in SKIP_ROOTS:
            continue
        depth = 3 if folded in DEEP_ROOTS or folded.startswith("steam") else 1
        found.extend(_programs_under(child.path, depth))
    return found


def shortcut_programs() -> list[FoundProgram]:
    folders = []
    program_data = os.environ.get("PROGRAMDATA", "")
    app_data = os.environ.get("APPDATA", "")
    user = os.environ.get("USERPROFILE", "")
    if program_data:
        folders.append(Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    if app_data:
        folders.append(Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    if user:
        folders.append(Path(user) / "Desktop")
    found: list[FoundProgram] = []
    for folder in folders:
        if not folder.is_dir():
            continue
        for link in folder.rglob("*.lnk"):
            target = lnk_target(link)
            executable = executable_name(target)
            target_file = target.split(",")[0].strip('"')
            if not executable or is_browser(executable) or is_junk(executable) or _blocked_path(target_file):
                continue
            if not os.path.isfile(target_file):
                continue
            name = link.stem
            if name.casefold().startswith(("uninstall", "install ")) or name.casefold().endswith(" - shortcut"):
                name = Path(executable).stem if name.casefold().endswith(" - shortcut") else name
            if name.casefold().startswith(("uninstall", "install ")):
                continue
            found.append(FoundProgram(name, executable, str(Path(target).parent), "Start menu", "program", target))
    return found


def browser_sites() -> list[FoundProgram]:
    rows: list[tuple[str, int, str]] = []
    for browser, database in history_databases():
        rows.extend(domains_in(database, browser))
    return sites_from_urls(rows)


def scan_computer() -> tuple[list[FoundProgram], dict]:
    from gordian.icons import icon_image

    programs = list_programs(_registry_entries())
    seen = {item.executable.casefold() for item in programs}
    for extra in shortcut_programs() + _drive_programs():
        key = extra.executable.casefold()
        if key in seen:
            continue
        seen.add(key)
        programs.append(extra)
    programs.sort(key=lambda item: item.name.casefold())
    rows = programs + browser_sites()
    icons = {}
    for item in rows:
        if not item.icon or item.icon in icons:
            continue
        try:
            image = icon_image(item.icon)
            if image is not None:
                icons[item.icon] = image.resize((16, 16))
        except Exception:
            continue
    return rows, icons


def lnk_target(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return lnk_target_bytes(data)


def lnk_target_bytes(data: bytes) -> str:
    if len(data) < 76 or data[:4] != b"L\x00\x00\x00":
        return ""
    flags = int.from_bytes(data[20:24], "little")
    offset = 76
    if flags & 1:
        if offset + 2 > len(data):
            return ""
        offset += 2 + int.from_bytes(data[offset : offset + 2], "little")
    if not flags & 2 or offset + 20 > len(data):
        return ""
    header_size = int.from_bytes(data[offset + 4 : offset + 8], "little")
    if header_size >= 36 and offset + 32 <= len(data):
        unicode_offset = int.from_bytes(data[offset + 28 : offset + 32], "little")
        if unicode_offset:
            start = offset + unicode_offset
            return _utf16_text(data, start)
    local_offset = int.from_bytes(data[offset + 16 : offset + 20], "little")
    if not local_offset:
        return ""
    start = offset + local_offset
    end = data.find(b"\x00", start)
    if end < 0:
        return ""
    return data[start:end].decode("mbcs", errors="ignore")


def _registry_entries() -> list[tuple[str, str, str]]:
    try:
        import winreg
    except ImportError:
        return []
    entries: list[tuple[str, str, str]] = []
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, path in roots:
        entries.extend(_read_uninstall(winreg, hive, path))
    return entries


def _drive_programs() -> list[FoundProgram]:
    found: list[FoundProgram] = []
    for drive in fixed_drives():
        found.extend(programs_on_drive(drive))
    return found


def _programs_under(folder: str, depth: int) -> list[FoundProgram]:
    found: list[FoundProgram] = []
    if depth < 0:
        return found
    try:
        children = list(os.scandir(folder))
    except OSError:
        return found
    exes = []
    folders = []
    for child in children:
        try:
            if child.is_file(follow_symlinks=False) and child.name.casefold().endswith(".exe"):
                exes.append(child)
            elif child.is_dir(follow_symlinks=False):
                folders.append(child)
        except OSError:
            continue
    chosen = _main_exe(exes, Path(folder).name)
    if chosen is not None and not is_browser(chosen.name) and not is_junk(chosen.name) and not _blocked_path(chosen.path):
        label = _listing_name(folder, chosen.name)
        if label and not label.casefold().startswith(("uninstall", "install ")):
            found.append(FoundProgram(label, chosen.name, folder, "On this drive", "program", chosen.path))
    if depth == 0:
        return found
    for child in folders:
        if child.name.casefold() in {"windows", "winsxs", "node_modules", ".git"}:
            continue
        found.extend(_programs_under(child.path, depth - 1))
    return found


def _main_exe(entries, folder_name: str):
    usable = [entry for entry in entries if not is_junk(entry.name) and not is_browser(entry.name)]
    if not usable:
        return None
    folded = "".join(ch for ch in folder_name.casefold() if ch.isalnum())
    for entry in usable:
        stem = "".join(ch for ch in Path(entry.name).stem.casefold() if ch.isalnum())
        if stem and (stem in folded or folded in stem):
            return entry
    return max(usable, key=lambda entry: entry.stat().st_size if entry.is_file() else 0)


def _best_executable(icon: str, folder: str, display: str) -> tuple[str, str]:
    icon_path = _file_path(icon)
    executable = executable_name(icon)
    if executable and not is_junk(executable) and not is_browser(executable):
        return executable, icon_path or folder
    if folder and os.path.isdir(folder):
        try:
            entries = [entry for entry in os.scandir(folder) if entry.is_file() and entry.name.casefold().endswith(".exe")]
        except OSError:
            entries = []
        chosen = _main_exe(entries, display)
        if chosen is not None:
            return chosen.name, chosen.path
    return "", icon_path


def _split_location(location: str) -> tuple[str, str]:
    folder, _, publisher = (location or "").partition("\n")
    return folder.strip(), publisher.strip()


def _file_path(text: str) -> str:
    return (text or "").replace("/", "\\").split(",")[0].strip().strip('"')


def _host(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"}:
        return ""
    host = (parsed.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    if not host or host in {"localhost", "newtab"} or host.endswith(".local"):
        return ""
    return host


def _utf16_text(data: bytes, start: int) -> str:
    if start < 0 or start >= len(data):
        return ""
    end = start
    while end + 1 < len(data) and data[end : end + 2] != b"\x00\x00":
        end += 2
    return data[start:end].decode("utf-16le", errors="ignore")


def _reg_dword(winreg, key, name: str) -> int:
    try:
        value, kind = winreg.QueryValueEx(key, name)
    except OSError:
        return 0
    if kind == winreg.REG_DWORD:
        return int(value)
    return 0
