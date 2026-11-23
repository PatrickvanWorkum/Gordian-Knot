"""config.json, beside the project. Lists, password, schedule, milestones, theme.

ensure_config adds new keys without replacing the lists you already saved.
The _comment string at the top of the file is the same rules, written out for
someone editing the JSON by hand. * in a process name is a wildcard.

write_scan_lists is what Scan's Lock in button stores: productive and reward
processes, plus productive and reward title keywords for sites.
write_edited_lists is the Edit lists window. It stores those four groups and
also browsers, ignored programs, and game folders.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from gordian.schedule import Block, parse_blocks


COMMENT = (
    "Time on a work program counts only while it is the window in front of you "
    "and you have used the keyboard or mouse within idle_seconds. Steam, reward "
    "programs, fullscreen games, and games installed through Steam, Epic, GOG, "
    "or Paradox spend free time whenever they are in front. A browser counts as "
    "reward when its window title contains a reward_title_keyword, such as YouTube, "
    "and as work when the title contains a productive_title_keyword. "
    "With no free time left, a reward program gets a warning, a second warning "
    "1 minute later, and that window closes at 5 minutes. A schedule block can "
    "turn lockdown on for those hours: unproductive programs stay closed and new "
    "ones cannot be added. Time already earned stays at the ratio it was earned under. "
    "reset_password protects Reset time, the ratio, and marking a milestone done. "
    "Change it here or with Change password. * is a wildcard. work_minutes of work "
    "earns reward_minutes of free time. milestones are tasks; bonus_minutes is the "
    "free time added when one is marked done."
)

PRODUCTIVE = (
    "cursor.exe",
    "code.exe",
    "devenv.exe",
    "godot*",
    "unity.exe",
    "blender.exe",
    "rider64.exe",
    "rider.exe",
    "aseprite.exe",
    "unrealeditor*",
)

REWARD_PROCESSES = (
    "steam.exe",
    "steamwebhelper.exe",
)

KEYWORDS = ("youtube",)

BROWSERS = (
    "firefox.exe",
    "chrome.exe",
    "msedge.exe",
    "brave.exe",
    "opera.exe",
    "vivaldi.exe",
    "waterfox.exe",
    "librewolf.exe",
)

GAME_PATH_MARKERS = (
    "\\steamapps\\common\\",
    "\\epic games\\",
    "\\gog galaxy\\",
    "\\gog games\\",
    "\\paradox interactive\\",
    "\\riot games\\",
    "\\xboxgames\\",
    "\\battlenet\\",
    "\\ea games\\",
    "\\origin games\\",
)

IGNORED_PROCESSES = (
    "explorer.exe",
    "searchhost.exe",
    "shellexperiencehost.exe",
    "applicationframehost.exe",
    "systemsettings.exe",
    "textinputhost.exe",
    "lockapp.exe",
    "logonui.exe",
    "python.exe",
    "pythonw.exe",
    "dwm.exe",
    "sihost.exe",
    "startmenuexperiencehost.exe",
    "widgetboard.exe",
    "gamebar.exe",
    "gamebarftserver.exe",
)


@dataclass(frozen=True)
class Milestone:
    title: str
    bonus_minutes: float
    done: bool

    def to_json(self) -> dict:
        return {
            "title": self.title,
            "bonus_minutes": self.bonus_minutes,
            "done": self.done,
        }


def parse_milestones(value) -> tuple[Milestone, ...]:
    if not isinstance(value, list):
        return ()
    items: list[Milestone] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        title = " ".join(str(raw.get("title") or "").split())
        if not title:
            continue
        try:
            minutes = float(raw.get("bonus_minutes") or 0)
        except (TypeError, ValueError):
            continue
        if minutes <= 0:
            continue
        items.append(Milestone(title[:120], minutes, bool(raw.get("done"))))
    return tuple(items)


@dataclass
class Settings:
    idle_seconds: float
    work_minutes: float
    reward_minutes: float
    poll_seconds: float
    productive_processes: list[str]
    reward_processes: list[str]
    reward_title_keywords: list[str]
    productive_title_keywords: list[str]
    browser_processes: list[str]
    game_path_markers: list[str]
    ignored_processes: list[str]
    reset_password: str
    schedule: tuple[Block, ...] = ()
    milestones: tuple[Milestone, ...] = ()


def default_settings() -> Settings:
    return Settings(
        idle_seconds=60,
        work_minutes=25,
        reward_minutes=10,
        poll_seconds=1,
        productive_processes=list(PRODUCTIVE),
        reward_processes=list(REWARD_PROCESSES),
        reward_title_keywords=list(KEYWORDS),
        productive_title_keywords=[],
        browser_processes=list(BROWSERS),
        game_path_markers=list(GAME_PATH_MARKERS),
        ignored_processes=list(IGNORED_PROCESSES),
        reset_password="0000",
        schedule=(),
        milestones=(),
    )


def default_document() -> dict:
    settings = default_settings()
    return {
        "_comment": COMMENT,
        "idle_seconds": settings.idle_seconds,
        "work_minutes": settings.work_minutes,
        "reward_minutes": settings.reward_minutes,
        "poll_seconds": settings.poll_seconds,
        "productive_processes": list(settings.productive_processes),
        "reward_processes": list(settings.reward_processes),
        "reward_title_keywords": list(settings.reward_title_keywords),
        "productive_title_keywords": list(settings.productive_title_keywords),
        "browser_processes": list(settings.browser_processes),
        "game_path_markers": list(settings.game_path_markers),
        "ignored_processes": list(settings.ignored_processes),
        "reset_password": settings.reset_password,
        "schedule": [block.to_json() for block in settings.schedule],
        "milestones": [item.to_json() for item in settings.milestones],
    }


def ensure_config(path: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    merged = default_document()
    for key, value in data.items():
        if key == "_comment":
            continue
        merged[key] = value
    if merged != data:
        path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")


def write_password(path: Path, password: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.json must be a JSON object.")
    data["reset_password"] = password
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_schedule(path: Path, blocks: tuple[Block, ...] | list[Block]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.json must be a JSON object.")
    data["schedule"] = [block.to_json() for block in blocks]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_milestones(path: Path, items: tuple[Milestone, ...] | list[Milestone]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.json must be a JSON object.")
    data["milestones"] = [item.to_json() for item in items]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_process_lists(path: Path, productive: list[str], reward: list[str]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.json must be a JSON object.")
    data["productive_processes"] = list(productive)
    data["reward_processes"] = list(reward)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_edited_lists(
    path: Path,
    productive: list[str],
    reward: list[str],
    productive_sites: list[str],
    reward_sites: list[str],
    browsers: list[str],
    ignored: list[str],
    game_paths: list[str],
) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.json must be a JSON object.")
    data["productive_processes"] = list(productive)
    data["reward_processes"] = list(reward)
    data["productive_title_keywords"] = list(productive_sites)
    data["reward_title_keywords"] = list(reward_sites)
    data["browser_processes"] = list(browsers)
    data["ignored_processes"] = list(ignored)
    data["game_path_markers"] = list(game_paths)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_scan_lists(
    path: Path,
    productive: list[str],
    reward: list[str],
    productive_sites: list[str],
    reward_sites: list[str],
) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.json must be a JSON object.")
    data["productive_processes"] = list(productive)
    data["reward_processes"] = list(reward)
    data["productive_title_keywords"] = list(productive_sites)
    data["reward_title_keywords"] = list(reward_sites)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_ratio(path: Path, work_minutes: float, reward_minutes: float) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.json must be a JSON object.")
    data["work_minutes"] = work_minutes
    data["reward_minutes"] = reward_minutes
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_night(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("night")) if isinstance(data, dict) else False


def read_ask_skip(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("ask_skip") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [item.strip() for item in items if isinstance(item, str) and item.strip()]


def write_ask_skip(path: Path, items: list[str]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.json must be a JSON object.")
    data["ask_skip"] = items[-300:]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_warning_sound(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    value = data.get("warning_sound") if isinstance(data, dict) else ""
    return value.strip() if isinstance(value, str) else ""


def write_warning_sound(path: Path, sound: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.json must be a JSON object.")
    data["warning_sound"] = sound.strip()
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_night(path: Path, night: bool) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.json must be a JSON object.")
    data["night"] = bool(night)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_fonts(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    fonts = data.get("fonts") if isinstance(data, dict) else None
    if not isinstance(fonts, dict):
        return {}
    return {key: value for key, value in fonts.items() if isinstance(key, str) and isinstance(value, str)}


def write_fonts(path: Path, fonts: dict[str, str]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.json must be a JSON object.")
    data["fonts"] = fonts
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_colors(path: Path) -> dict[str, str]:
    from gordian.colors import normalize_hex

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    colors = data.get("colors") if isinstance(data, dict) else None
    if not isinstance(colors, dict):
        return {}
    cleaned = {}
    for key, value in colors.items():
        if isinstance(key, str) and isinstance(value, str):
            hex_color = normalize_hex(value)
            if hex_color:
                cleaned[key] = hex_color
    return cleaned


def write_colors(path: Path, colors: dict[str, str]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.json must be a JSON object.")
    data["colors"] = colors
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_default_config(path: Path, *, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(default_document(), indent=2) + "\n", encoding="utf-8")


def parse_config(path: Path) -> Settings:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"config.json has a syntax error on line {exc.lineno}.") from exc
    if not isinstance(data, dict):
        raise ValueError("config.json must be a JSON object.")
    base = default_settings()
    return Settings(
        idle_seconds=require_number(data, "idle_seconds", base.idle_seconds, minimum=1),
        work_minutes=require_number(data, "work_minutes", base.work_minutes, minimum=1),
        reward_minutes=require_number(data, "reward_minutes", base.reward_minutes, minimum=0),
        poll_seconds=require_number(
            data, "poll_seconds", base.poll_seconds, minimum=0.5, maximum=60
        ),
        productive_processes=require_strings(
            data, "productive_processes", base.productive_processes
        ),
        reward_processes=require_strings(data, "reward_processes", base.reward_processes),
        reward_title_keywords=require_strings(
            data, "reward_title_keywords", base.reward_title_keywords
        ),
        productive_title_keywords=require_strings(
            data, "productive_title_keywords", base.productive_title_keywords
        ),
        browser_processes=require_strings(data, "browser_processes", base.browser_processes),
        game_path_markers=require_strings(data, "game_path_markers", base.game_path_markers),
        ignored_processes=require_strings(data, "ignored_processes", base.ignored_processes),
        reset_password=require_text(data, "reset_password", base.reset_password),
        schedule=parse_blocks(data.get("schedule")),
        milestones=parse_milestones(data.get("milestones")),
    )


def require_number(data: dict, key: str, fallback: float, minimum: float, maximum: float | None = None) -> float:
    if key not in data:
        return float(fallback)
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number.")
    number = float(value)
    if number < minimum:
        raise ValueError(f"{key} must be at least {minimum}.")
    if maximum is not None and number > maximum:
        raise ValueError(f"{key} must be at most {maximum}.")
    return number


def require_text(data: dict, key: str, fallback: str) -> str:
    if key not in data:
        return fallback
    value = data[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def require_strings(data: dict, key: str, fallback: list[str]) -> list[str]:
    if key not in data:
        return list(fallback)
    value = data[key]
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{key} must be a list of names.")
    return [item.strip() for item in value]
