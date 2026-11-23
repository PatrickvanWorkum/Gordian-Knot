from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from gordian.colors import chosen_color, normalize_hex
from gordian.classify import Memory, classify, resolve, segment_identity
from gordian.chart import (
    _tick_step,
    application_totals,
    away_spans,
    axis_label,
    clamp_range,
    cumulative_points,
    covered_range,
    data_bounds,
    format_axis_duration,
    pan_range,
    value_at,
    y_ticks,
    zoom_range,
)
from gordian.formatting import format_duration, format_ratio, parse_day, parse_ratio
from gordian.ledger import (
    AWAY_SECONDS,
    Ledger,
    clear_saved_history,
    forget_saved_segments,
    read_saved_segments,
    wall_gap_kind,
)
from gordian.address import capitalise_name, website_name
from gordian.favicon import icon_hrefs
from gordian.words import listed_in, split_words
from gordian.ask import consider, pin_keyword
from gordian.paths import project_root
from gordian.sound import sound_label
from gordian.monitor import close_style, may_close
from gordian.scan import (
    FoundProgram,
    list_programs,
    lnk_target_bytes,
    lock_choices,
    lock_sites,
    programs_from_entries,
    sites_from_urls,
    starting_choice,
)
from gordian.warning import (
    Overtime,
    advance_overtime,
    advance_warning,
    blocked_message,
    overlay_text,
    reward_label,
    visit_key,
    warning_message,
)
from gordian.schedule import (
    active_block,
    block_caption,
    block_fill,
    block_mark,
    format_clock,
    next_block_color,
    lockdown_active,
    make_block,
    overlapping_days,
    parse_blocks,
    parse_typed_clock,
    place_block,
)
from gordian.settings import (
    Milestone,
    default_settings,
    ensure_config,
    parse_config,
    parse_milestones,
    read_colors,
    read_warning_sound,
    write_colors,
    write_warning_sound,
    write_default_config,
    write_edited_lists,
    write_milestones,
    write_ratio,
)
from gordian.titles import activity_name, page_label


def sample(process: str, title: str = "", idle: float = 0):
    return SimpleNamespace(process=process, title=title, idle_seconds=idle)


class FormatTests(unittest.TestCase):
    def test_duration(self) -> None:
        self.assertEqual(format_duration(0), "0m 00s")
        self.assertEqual(format_duration(65), "1m 05s")
        self.assertEqual(format_duration(3661), "1h 01m 01s")
        self.assertEqual(format_duration(-5), "-0m 05s")


class ClassifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = default_settings()

    def test_work_reward_and_titles(self) -> None:
        self.assertEqual(classify("Cursor.exe", "main.py - Cursor", self.settings), "productive")
        self.assertEqual(classify("Godot_v4.5-stable_win64.exe", "Game", self.settings), "productive")
        self.assertEqual(classify("UnrealEditor.exe", "Map", self.settings), "productive")
        self.assertEqual(classify("steam.exe", "Steam", self.settings), "reward")
        self.assertEqual(classify("chrome.exe", "lofi hip hop - YouTube", self.settings), "reward")
        self.assertEqual(classify("chrome.exe", "Docs", self.settings), "neutral")
        self.assertEqual(classify("Cursor.exe", "youtube notes.md - Cursor", self.settings), "productive")
        self.assertEqual(
            classify("firefox.exe", "Smiling Friends - YouTube \u2014 Mozilla Firefox", self.settings),
            "reward",
        )
        self.assertEqual(classify("firefox.exe", "Inbox \u2014 Mozilla Firefox", self.settings), "neutral")

    def test_games_by_install_path_and_fullscreen(self) -> None:
        game = r"D:\Steam\steamapps\common\Satisfactory\FactoryGame.exe"
        self.assertEqual(classify("FactoryGame.exe", "Satisfactory", self.settings, path=game), "reward")
        self.assertEqual(classify("game.exe", "Game", self.settings, fullscreen=True), "reward")
        self.assertEqual(
            classify("chrome.exe", "Docs - Google Chrome", self.settings, fullscreen=True),
            "neutral",
        )
        self.assertEqual(classify("explorer.exe", "Desktop", self.settings, fullscreen=True), "neutral")
        self.assertEqual(classify("Cursor.exe", "Project - Cursor", self.settings, fullscreen=True), "productive")

    def test_browser_pages_are_separate_activities(self) -> None:
        youtube = "Smiling Friends - YouTube \u2014 Mozilla Firefox"
        gmail = "Inbox \u2014 Mozilla Firefox"
        self.assertEqual(page_label(youtube), "Smiling Friends - YouTube")
        self.assertEqual(activity_name("firefox.exe", youtube), "Smiling Friends - YouTube")
        self.assertNotEqual(
            segment_identity("firefox.exe", youtube, self.settings),
            segment_identity("firefox.exe", gmail, self.settings),
        )
        self.assertEqual(segment_identity("Cursor.exe", "Gordian Knot - Cursor", self.settings), "")

    def test_active_work_idle_work_and_idle_steam(self) -> None:
        self.assertEqual(resolve(sample("Cursor.exe", "a", 59), self.settings, Memory(), False).kind, "earn")
        self.assertEqual(resolve(sample("Cursor.exe", "a", 60), self.settings, Memory(), False).kind, "idle")
        self.assertEqual(resolve(sample("steam.exe", "Steam", 500), self.settings, Memory(), False).kind, "spend")

    def test_pause_remembers_steam_and_lock_keeps_spending(self) -> None:
        memory = Memory()
        paused = resolve(sample("steam.exe", "Steam"), self.settings, memory, True)
        self.assertEqual(paused.kind, "paused")
        self.assertEqual(memory.kind, "spend")
        locked = resolve(sample("LockApp.exe", "", 3), self.settings, memory, False)
        self.assertEqual(locked.kind, "spend")
        self.assertTrue(locked.locked)
        self.assertEqual(locked.process, "steam.exe")

    def test_lock_during_work_is_idle(self) -> None:
        memory = Memory()
        resolve(sample("Cursor.exe", "a", 5), self.settings, memory, False)
        locked = resolve(sample("LockApp.exe"), self.settings, memory, False)
        self.assertEqual(locked.kind, "idle")
        self.assertTrue(locked.locked)


class ConfigTests(unittest.TestCase):
    def test_default_file_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            write_default_config(path)
            settings = parse_config(path)
        self.assertEqual(settings.work_minutes, 25)
        self.assertEqual(settings.reward_minutes, 10)
        self.assertEqual(classify("blender.exe", "Scene", settings), "productive")

    def test_edited_lists_keep_the_other_settings(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            write_default_config(path)
            write_edited_lists(
                path,
                ["cursor.exe"],
                ["steam.exe"],
                ["Wikipedia"],
                ["Interactive Brokers"],
                ["firefox.exe"],
                ["explorer.exe"],
                ["\\steamapps\\common\\"],
            )
            settings = parse_config(path)
        self.assertEqual(settings.productive_processes, ["cursor.exe"])
        self.assertEqual(settings.reward_title_keywords, ["Interactive Brokers"])
        self.assertEqual(settings.browser_processes, ["firefox.exe"])
        self.assertEqual(settings.reset_password, "0000")
        self.assertEqual(settings.work_minutes, 25)

    def test_bad_config_explains_the_line(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_config(path)

    def test_repo_config(self) -> None:
        path = project_root() / "config.json"
        if not path.exists():
            self.skipTest("no local config.json (expected on a fresh clone)")
        settings = parse_config(path)
        self.assertEqual(settings.idle_seconds, 60)
        self.assertIn("cursor.exe", settings.productive_processes)


class WarningTests(unittest.TestCase):
    def test_names(self) -> None:
        self.assertEqual(reward_label("steamwebhelper.exe", "Steam"), "Steam")
        self.assertEqual(reward_label("chrome.exe", "lofi - YouTube"), "YouTube")
        self.assertEqual(reward_label("Hades.exe", "Hades"), "Hades")

    def test_message_mentions_overspend(self) -> None:
        self.assertIn("used up", warning_message(0))
        self.assertNotIn("YouTube", warning_message(-120))
        self.assertIn("2m 00s", warning_message(-120))
        self.assertIn("0m 05s", overlay_text(-5, 1, 240))
        self.assertIn("3m 00s", overlay_text(-65, 2, 180))

    def test_warns_once_per_visit_and_again_after_leaving(self) -> None:
        first = advance_warning("spend", 0, False, False, True)
        self.assertTrue(first.show)
        self.assertTrue(first.warned)
        again = advance_warning("spend", -5, first.warned, False, True)
        self.assertFalse(again.show)
        left = advance_warning("earn", 0, again.warned, False, False)
        self.assertFalse(left.warned)
        returned = advance_warning("spend", 0, left.warned, False, True)
        self.assertTrue(returned.show)

    def test_time_left_stays_quiet_until_it_runs_out(self) -> None:
        with_time = advance_warning("spend", 30, False, False, True)
        self.assertFalse(with_time.show)
        self.assertFalse(with_time.warned)
        empty = advance_warning("spend", 0, with_time.warned, False, True)
        self.assertTrue(empty.show)

    def test_open_dialog_and_pause_do_not_stack_warnings(self) -> None:
        blocked = advance_warning("spend", 0, False, True, True)
        self.assertFalse(blocked.show)
        paused = advance_warning("paused", 0, True, False, True)
        self.assertFalse(paused.show)
        self.assertTrue(paused.warned)
        holding_dialog = advance_warning("earn", 0, True, True, False)
        self.assertTrue(holding_dialog.warned)
        self.assertFalse(holding_dialog.show)


class LedgerTests(unittest.TestCase):
    def make(self, folder: str, work: float = 25, reward: float = 10) -> Ledger:
        root = Path(folder)
        return Ledger(root / "state.json", root / "history.jsonl", work, reward)

    def test_rate_and_ignored_kinds(self) -> None:
        with TemporaryDirectory() as folder:
            ledger = self.make(folder)
            now = datetime(2026, 10, 24, 12, 0, 0)
            ledger.date = "2026-10-24"
            ledger.update(now, "earn", "Cursor.exe", "a", 25 * 60, max_delta=100000)
            self.assertAlmostEqual(ledger.earned_seconds, 10 * 60)
            self.assertAlmostEqual(ledger.remaining_seconds, 10 * 60)
            ledger.update(now, "spend", "steam.exe", "Steam", 4 * 60, max_delta=100000)
            self.assertAlmostEqual(ledger.remaining_seconds, 6 * 60)
            self.assertAlmostEqual(ledger.productive_seconds, 25 * 60)
            spent = ledger.spent_seconds
            ledger.update(now, "idle", "Cursor.exe", "a", 30, max_delta=100000)
            ledger.update(now, "neutral", "chrome.exe", "Docs", 30, max_delta=100000)
            ledger.update(now, "paused", "Cursor.exe", "a", 30, max_delta=100000)
            self.assertAlmostEqual(ledger.productive_seconds, 25 * 60)
            self.assertAlmostEqual(ledger.spent_seconds, spent)
            ledger.set_ratio(10, 10)
            self.assertAlmostEqual(ledger.earned_seconds, 10 * 60)
            ledger.update(now, "earn", "Cursor.exe", "a", 10 * 60, max_delta=100000)
            self.assertAlmostEqual(ledger.earned_seconds, 20 * 60)

    def test_gap_does_not_count_and_segments_split_on_switch(self) -> None:
        with TemporaryDirectory() as folder:
            ledger = self.make(folder)
            now = datetime(2026, 10, 24, 12, 0, 0)
            ledger.date = "2026-10-24"
            ledger.update(now, "earn", "Cursor.exe", "a", 4, max_delta=5)
            ledger.update(now, "earn", "Cursor.exe", "b", 30, max_delta=5)
            self.assertEqual(ledger.productive_seconds, 4)
            self.assertEqual(ledger.closed[-1]["seconds"], 4)
            self.assertEqual(ledger.current["seconds"], 0)
            ledger.update(now, "earn", "Cursor.exe", "c", 2, max_delta=5)
            ledger.update(now, "neutral", "chrome.exe", "Docs", 3, max_delta=5)
            self.assertEqual(ledger.closed[-1]["process"], "Cursor.exe")
            self.assertEqual(ledger.current["process"], "chrome.exe")
            self.assertEqual(ledger.current["seconds"], 3)
            self.assertEqual(ledger.productive_seconds, 6)

    def test_pause_keeps_counting_across_windows_and_gaps(self) -> None:
        with TemporaryDirectory() as folder:
            ledger = self.make(folder)
            now = datetime(2026, 10, 24, 12, 0, 0)
            ledger.date = "2026-10-24"
            ledger.update(now, "paused", "pythonw.exe", "Gordian Knot", 4, max_delta=5)
            ledger.update(now, "paused", "SlayTheSpire2.exe", "Slay the Spire 2", 4, max_delta=5)
            self.assertEqual(ledger.closed, [])
            self.assertEqual(ledger.current["process"], "pythonw.exe")
            self.assertAlmostEqual(ledger.current["seconds"], 8)
            ledger.update(now, "paused", "explorer.exe", "Desktop", 90, max_delta=5)
            self.assertEqual(ledger.closed, [])
            self.assertAlmostEqual(ledger.current["seconds"], 98)
            self.assertEqual(ledger.productive_seconds, 0)
            self.assertEqual(ledger.spent_seconds, 0)
            ledger.update(now, "spend", "SlayTheSpire2.exe", "Slay the Spire 2", 4, max_delta=5)
            self.assertEqual(ledger.closed[-1]["kind"], "paused")
            self.assertAlmostEqual(ledger.closed[-1]["seconds"], 98)
            self.assertAlmostEqual(ledger.current["seconds"], 4)
            self.assertAlmostEqual(ledger.spent_seconds, 4)

    def test_sleep_is_recorded_without_moving_the_balance(self) -> None:
        with TemporaryDirectory() as folder:
            ledger = self.make(folder)
            begun = datetime(2026, 10, 24, 12, 0, 0)
            ledger.date = "2026-10-24"
            ledger.update(begun, "earn", "Cursor.exe", "a", 30, max_delta=1000)
            asleep = begun + timedelta(minutes=5)
            awake = asleep + timedelta(minutes=20)
            ledger.note_away(asleep, awake, "sleep")
            self.assertEqual(ledger.productive_seconds, 30)
            self.assertEqual(ledger.spent_seconds, 0)
            self.assertEqual(ledger.closed[0]["kind"], "earn")
            self.assertEqual(ledger.closed[0]["end"], asleep.isoformat(timespec="seconds"))
            self.assertEqual(ledger.closed[1]["kind"], "sleep")
            self.assertAlmostEqual(ledger.closed[1]["seconds"], 20 * 60)
            self.assertIsNone(ledger.current)
            self.assertGreaterEqual(20 * 60, AWAY_SECONDS)

    def test_shutdown_gap_is_kept_with_the_totals(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            updated = datetime.now().replace(microsecond=0) - timedelta(hours=2)
            if updated.date() != date.today():
                updated = datetime.combine(date.today(), datetime.min.time())
            payload = {
                "date": date.today().isoformat(),
                "productive_seconds": 80,
                "spent_seconds": 10,
                "updated_at": updated.isoformat(timespec="seconds"),
                "closed": [],
                "current": {
                    "start": (updated - timedelta(minutes=1)).isoformat(timespec="seconds"),
                    "end": None,
                    "kind": "earn",
                    "process": "Cursor.exe",
                    "title": "a",
                    "identity": "",
                    "seconds": 80,
                },
            }
            (root / "state.json").write_text(json.dumps(payload), encoding="utf-8")
            loaded, notice = Ledger.load(root / "state.json", root / "history.jsonl", 25, 10)
            self.assertIsNone(notice)
            self.assertAlmostEqual(loaded.productive_seconds, 80)
            self.assertAlmostEqual(loaded.spent_seconds, 10)
            self.assertEqual(loaded.closed[-1]["kind"], "shutdown")
            self.assertGreater(loaded.closed[-1]["seconds"], AWAY_SECONDS)

    def test_pause_minute_on_a_work_program_is_counted(self) -> None:
        with TemporaryDirectory() as folder:
            ledger = self.make(folder, work=1, reward=1)
            now = datetime(2026, 11, 7, 20, 0, 0)
            ledger.date = "2026-11-07"
            ledger.update(now, "paused", "Cursor.exe", "Gordian Knot - Cursor", 10 * 60, max_delta=100000)
            start = now + timedelta(minutes=9)
            end = now + timedelta(minutes=10)
            credited = ledger.credit_work(start, end, "Cursor.exe", "Gordian Knot - Cursor")
            self.assertEqual(credited, 60)
            self.assertAlmostEqual(ledger.productive_seconds, 60)
            self.assertAlmostEqual(ledger.remaining_seconds, 60)
            self.assertEqual(ledger.current, None)
            self.assertEqual(ledger.closed[-1]["kind"], "earn")
            self.assertEqual(ledger.closed[-1]["seconds"], 60)
            self.assertAlmostEqual(ledger.closed[0]["seconds"], 9 * 60)
            self.assertEqual(ledger.closed[0]["kind"], "paused")
        from gordian.ui import next_pause_hold

        self.assertTrue(next_pause_hold(True, "productive"))
        self.assertFalse(next_pause_hold(True, "unproductive"))
        self.assertFalse(next_pause_hold(False, "productive"))

    def test_a_counted_minute_inside_a_pause_does_not_bend_the_chart(self) -> None:
        segments = [
            {
                "start": "2026-11-08T12:04:25",
                "end": "2026-11-08T12:34:25",
                "kind": "paused",
                "process": "SlayTheSpire2.exe",
                "title": "Slay the Spire 2",
                "seconds": 1800,
            },
            {
                "start": "2026-11-08T12:32:24",
                "end": "2026-11-08T12:33:24",
                "kind": "earn",
                "process": "Cursor.exe",
                "title": "Chevauchee - Cursor",
                "seconds": 60,
            },
        ]
        points = cumulative_points(segments, datetime(2026, 11, 8, 13, 5))
        times = [point[0] for point in points]
        self.assertEqual(times, sorted(times))
        self.assertAlmostEqual(points[-1][1], 60)
        self.assertAlmostEqual(points[-1][3], 1740)

    def test_midnight_rollover_keeps_the_old_day(self) -> None:
        with TemporaryDirectory() as folder:
            ledger = self.make(folder)
            ledger.date = "2026-10-24"
            ledger.update(datetime(2026, 10, 24, 23, 50), "earn", "Cursor.exe", "a", 100, max_delta=100000)
            ledger.update(datetime(2026, 10, 25, 0, 5), "earn", "Cursor.exe", "a", 5, max_delta=100000)
            self.assertEqual(ledger.date, "2026-10-25")
            self.assertEqual(ledger.productive_seconds, 5)
            self.assertAlmostEqual(ledger.remaining_seconds, 42)
            history = Path(folder) / "history.jsonl"
            lines = [line for line in history.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["productive_seconds"], 100)
            self.assertTrue((Path(folder) / "days" / "2026-10-24.json").exists())

    def test_save_and_load_keep_one_copy_of_the_open_stretch(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            today = date.today()
            now = datetime.combine(today, datetime.min.time()).replace(hour=15)
            ledger = self.make(folder)
            ledger.date = today.isoformat()
            ledger.update(now, "earn", "Cursor.exe", "a", 50, max_delta=1000)
            ledger.save()
            loaded, notice = Ledger.load(root / "state.json", root / "history.jsonl", 25, 10)
            self.assertIsNone(notice)
            self.assertAlmostEqual(loaded.productive_seconds, 50)
            self.assertEqual(len(loaded.closed), 1)
            self.assertIsNone(loaded.current)
            loaded.save()
            again, _notice = Ledger.load(root / "state.json", root / "history.jsonl", 25, 10)
            self.assertEqual(len(again.closed), 1)
            self.assertAlmostEqual(again.productive_seconds, 50)

    def test_saved_state_is_one_segment_per_line_and_round_trips(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            now = datetime.combine(date.today(), datetime.min.time()).replace(hour=15)
            ledger = self.make(folder)
            ledger.date = date.today().isoformat()
            ledger.update(now, "earn", "Cursor.exe", "a", 50, max_delta=1000)
            ledger.update(now + timedelta(seconds=50), "spend", "steam.exe", "Steam", 20, max_delta=1000)
            ledger.save()
            text = (root / "state.json").read_text(encoding="utf-8")
            data = json.loads(text)
            self.assertEqual(data["closed"][0]["process"], "Cursor.exe")
            self.assertEqual(data["current"]["process"], "steam.exe")
            self.assertIn('\n    {"start": ', text)

    def test_background_save_never_replaces_a_newer_save(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            ledger = self.make(folder)
            ledger.date = date.today().isoformat()
            old = ledger._prepared()
            ledger.productive_seconds = 99
            ledger.save()
            ledger._write(*old)
            data = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertAlmostEqual(data["productive_seconds"], 99)

    def test_background_save_lands_on_disk(self) -> None:
        import time

        with TemporaryDirectory() as folder:
            root = Path(folder)
            ledger = self.make(folder)
            ledger.date = date.today().isoformat()
            ledger.productive_seconds = 42
            ledger.save_soon()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and ledger._written_number < ledger._save_number:
                time.sleep(0.02)
            data = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertAlmostEqual(data["productive_seconds"], 42)

    def test_blank_state_loads_the_backup(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            today = date.today()
            now = datetime.combine(today, datetime.min.time()).replace(hour=15)
            ledger = self.make(folder)
            ledger.date = today.isoformat()
            ledger.update(now, "earn", "Cursor.exe", "a", 50, max_delta=1000)
            ledger.save()
            self.assertTrue((root / "state.json.bak").is_file())
            (root / "state.json").write_bytes(b"\x00" * 461)
            loaded, notice = Ledger.load(root / "state.json", root / "history.jsonl", 25, 10)
            self.assertIsNone(notice)
            self.assertAlmostEqual(loaded.productive_seconds, 50)
            self.assertIn(b"productive_seconds", (root / "state.json").read_bytes())

    def test_blank_state_without_a_backup_starts_fresh(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "state.json").write_bytes(b"\x00" * 16)
            loaded, notice = Ledger.load(root / "state.json", root / "history.jsonl", 25, 10)
            self.assertIsNotNone(notice)
            self.assertIn("empty", notice)
            self.assertEqual(loaded.productive_seconds, 0.0)

    def test_load_archives_a_previous_day(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            yesterday = date.today() - timedelta(days=1)
            now = datetime.combine(yesterday, datetime.min.time()).replace(hour=12)
            ledger = self.make(folder)
            ledger.date = yesterday.isoformat()
            ledger.update(now, "earn", "Cursor.exe", "a", 40, max_delta=1000)
            ledger.save()
            loaded, notice = Ledger.load(root / "state.json", root / "history.jsonl", 25, 10)
            self.assertIsNone(notice)
            self.assertEqual(loaded.productive_seconds, 0)
            self.assertAlmostEqual(loaded.remaining_seconds, 16)
            self.assertEqual(loaded.date, date.today().isoformat())
            self.assertTrue((root / "days" / f"{yesterday.isoformat()}.json").exists())
            saved = read_saved_segments(root / "history.jsonl")
            self.assertEqual(sum(item["seconds"] for item in saved if item["kind"] == "earn"), 40)

    def test_clock_changes_are_marked_and_free_time_reset_is_a_line(self) -> None:
        self.assertEqual(wall_gap_kind(-120, 1), "lost")
        self.assertEqual(wall_gap_kind(120, 1), "clock")
        self.assertEqual(wall_gap_kind(120, 110), "sleep")
        self.assertIsNone(wall_gap_kind(10, 1))
        with TemporaryDirectory() as folder:
            ledger = self.make(folder)
            ledger.date = "2026-10-24"
            ledger.earned_bank = 10
            start = datetime(2026, 10, 24, 12, 0, 0)
            ledger.note_clock(start, start + timedelta(minutes=30))
            self.assertEqual(ledger.closed[-1]["kind"], "clock")
            self.assertAlmostEqual(ledger.remaining_seconds, 10)
            ledger.note_lost(start + timedelta(hours=2), 3600)
            self.assertEqual(ledger.closed[-1]["kind"], "lost")
            self.assertAlmostEqual(ledger.closed[-1]["seconds"], 3600)
            ledger.update(datetime(2026, 10, 23, 9, 0), "earn", "Cursor.exe", "a", 5, max_delta=1000)
            self.assertEqual(ledger.date, "2026-10-24")
            ledger.reset()
            self.assertEqual(ledger.remaining_seconds, 0)
            self.assertEqual(ledger.closed[-1]["kind"], "reset")

    def test_saved_day_keeps_time_the_segment_list_dropped(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            day = root / "days"
            day.mkdir()
            payload = {
                "date": "2026-10-29",
                "productive_seconds": 100,
                "spent_seconds": 40,
                "closed": [
                    {
                        "start": "2026-10-29T21:00:00",
                        "end": "2026-10-29T21:01:00",
                        "kind": "earn",
                        "process": "cursor.exe",
                        "title": "Cursor",
                        "seconds": 60,
                    }
                ],
            }
            (day / "2026-10-29.json").write_text(json.dumps(payload), encoding="utf-8")
            (day / "2026-10-29.json.bak").write_text("{}", encoding="utf-8")
            saved = read_saved_segments(root / "history.jsonl")
            self.assertEqual(len(saved), 3)
            self.assertEqual(sum(item["seconds"] for item in saved if item["kind"] == "earn"), 100)
            self.assertEqual(sum(item["seconds"] for item in saved if item["kind"] == "spend"), 40)
            self.assertTrue(saved[0]["start"] < saved[-1]["start"])

    def test_clear_history_removes_saved_days(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "history.jsonl").write_text('{"date": "2026-10-24"}\n', encoding="utf-8")
            (root / "state.json.bak").write_text("{}", encoding="utf-8")
            days = root / "days"
            days.mkdir()
            (days / "2026-10-24.json").write_text(
                json.dumps(
                    {
                        "date": "2026-10-24",
                        "productive_seconds": 10,
                        "spent_seconds": 0,
                        "closed": [
                            {
                                "start": "2026-10-24T12:00:00",
                                "end": "2026-10-24T12:00:10",
                                "kind": "earn",
                                "seconds": 10,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (days / "2026-10-24.json.bak").write_text("{}", encoding="utf-8")
            self.assertEqual(len(read_saved_segments(root / "history.jsonl")), 1)
            clear_saved_history(root / "state.json", root / "history.jsonl")
            self.assertFalse((root / "history.jsonl").exists())
            self.assertFalse((root / "state.json.bak").exists())
            self.assertFalse((days / "2026-10-24.json").exists())
            self.assertFalse((days / "2026-10-24.json.bak").exists())
            self.assertEqual(read_saved_segments(root / "history.jsonl"), [])

    def test_forget_saved_segments_drops_the_cache(self) -> None:
        from gordian import ledger as ledger_module

        with TemporaryDirectory() as folder:
            root = Path(folder)
            days = root / "days"
            days.mkdir()
            (days / "2026-10-24.json").write_text(
                json.dumps(
                    {
                        "date": "2026-10-24",
                        "productive_seconds": 10,
                        "spent_seconds": 0,
                        "closed": [
                            {
                                "start": "2026-10-24T12:00:00",
                                "end": "2026-10-24T12:00:10",
                                "kind": "earn",
                                "seconds": 10,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(len(read_saved_segments(root / "history.jsonl")), 1)
            self.assertTrue(ledger_module._saved_cache)
            forget_saved_segments()
            self.assertEqual(ledger_module._saved_cache, {})
            self.assertEqual(len(read_saved_segments(root / "history.jsonl")), 1)

    def test_browser_pages_split_and_reset_clears_today(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            today = date.today()
            now = datetime.combine(today, datetime.min.time()).replace(hour=15)
            ledger = self.make(folder)
            ledger.date = today.isoformat()
            ledger.update(now, "spend", "firefox.exe", "One - YouTube", 10, max_delta=1000, identity="one - youtube")
            ledger.update(now, "neutral", "firefox.exe", "Inbox", 4, max_delta=1000, identity="inbox")
            self.assertEqual(len(ledger.closed), 1)
            self.assertEqual(ledger.current["identity"], "inbox")
            self.assertEqual(ledger.spent_seconds, 10)
            ledger.reset()
            self.assertEqual(ledger.productive_seconds, 0)
            self.assertEqual(ledger.spent_seconds, 0)
            self.assertEqual([item["kind"] for item in ledger.closed], ["reset"])
            self.assertIsNone(ledger.current)
            ledger.save()
            loaded, _notice = Ledger.load(root / "state.json", root / "history.jsonl", 25, 10)
            self.assertEqual(loaded.spent_seconds, 0)


class ConfigMergeTests(unittest.TestCase):
    def test_ensure_adds_password_without_replacing_lists(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(
                '{"work_minutes": 30, "productive_processes": ["cursor.exe"]}\n',
                encoding="utf-8",
            )
            ensure_config(path)
            settings = parse_config(path)
            self.assertEqual(settings.work_minutes, 30)
            self.assertEqual(settings.productive_processes, ["cursor.exe"])
            self.assertEqual(settings.reset_password, "0000")
            self.assertIn("firefox.exe", settings.browser_processes)


class ChartTests(unittest.TestCase):
    def test_padding_before_midnight_does_not_count_as_the_previous_day(self) -> None:
        midnight = datetime(2026, 10, 30).timestamp()
        points = [(midnight, 0, 0, 0, 0), (midnight + 360, 0, 12, 300, 0)]
        start, end = covered_range(points, midnight - 30, midnight + 390)
        self.assertEqual(datetime.fromtimestamp(start).date().isoformat(), "2026-10-30")
        self.assertEqual(datetime.fromtimestamp(end).date().isoformat(), "2026-10-30")

    def test_lines_climb_only_on_work_and_reward_apps(self) -> None:
        start = datetime(2026, 10, 24, 9, 0, 0)
        segments = [
            _segment(start, start + timedelta(minutes=10), "earn", 600),
            _segment(start + timedelta(minutes=10), start + timedelta(minutes=12), "idle", 0),
            _segment(start + timedelta(minutes=20), start + timedelta(minutes=25), "spend", 300),
            _segment(start + timedelta(minutes=25), start + timedelta(minutes=26), "paused", 0),
        ]
        points = cumulative_points(segments, start + timedelta(minutes=26))
        self.assertEqual(points[0][1:], (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(points[1][0], start + timedelta(minutes=10))
        self.assertEqual(points[1][1:], (600.0, 0.0, 0.0, 0.0))
        self.assertEqual(points[2][1:], (600.0, 0.0, 0.0, 0.0))
        flat = next(item for item in points if item[0] == start + timedelta(minutes=20))
        self.assertEqual(flat[1:], (600.0, 0.0, 0.0, 0.0))
        self.assertEqual(points[-1][0], start + timedelta(minutes=26))
        self.assertEqual(points[-1][1:], (600.0, 300.0, 0.0, 0.0))

    def test_paused_and_not_counting_climb_on_their_own_lines(self) -> None:
        start = datetime(2026, 10, 24, 9, 0, 0)
        segments = [
            _segment(start, start + timedelta(minutes=10), "earn", 600),
            _segment(start + timedelta(minutes=10), start + timedelta(minutes=12), "neutral", 120),
            _segment(start + timedelta(minutes=12), start + timedelta(minutes=15), "paused", 180),
            _segment(start + timedelta(minutes=15), start + timedelta(minutes=16), "idle", 60),
        ]
        points = cumulative_points(segments, start + timedelta(minutes=16))
        self.assertEqual(points[-1][1:], (600.0, 0.0, 180.0, 180.0))
        spans = away_spans(
            [
                _segment(start, start + timedelta(minutes=30), "sleep", 1800),
                _segment(start + timedelta(minutes=30), start + timedelta(hours=2), "shutdown", 5400),
            ]
        )
        self.assertEqual([item[2] for item in spans], ["sleep", "shutdown"])

    def test_application_totals_add_up_time_on_each_program(self) -> None:
        rows = application_totals(
            [
                {"kind": "earn", "process": "Cursor.exe", "title": "", "seconds": 50},
                {"kind": "spend", "process": "steam.exe", "title": "Steam", "seconds": 20},
                {"kind": "paused", "process": "Cursor.exe", "title": "", "seconds": 10},
                {"kind": "sleep", "process": "Windows", "title": "Sleep", "seconds": 100},
            ]
        )
        self.assertEqual(rows[0][0], "Cursor.exe")
        self.assertAlmostEqual(rows[0][1], 60)
        self.assertEqual(rows[1][0], "Steam")
        self.assertAlmostEqual(rows[1][1], 20)

    def test_open_segment_reaches_now(self) -> None:
        start = datetime(2026, 10, 24, 9, 0, 0)
        now = start + timedelta(minutes=5)
        points = cumulative_points([{"start": start.isoformat(), "end": None, "kind": "earn", "seconds": 120}], now)
        self.assertEqual(points[-1][0], now)
        self.assertEqual(points[-1][1:], (120.0, 0.0, 0.0, 0.0))
        midpoint = value_at([(item[0].timestamp(), item[1], item[2]) for item in points], start.timestamp() + 150)
        self.assertAlmostEqual(midpoint[0], 60.0)
        self.assertEqual(midpoint[1], 0.0)

    def test_value_at_matches_a_plain_scan(self) -> None:
        def scan(points, moment):
            if moment <= points[0][0]:
                return tuple(points[0][1:])
            if moment >= points[-1][0]:
                return tuple(points[-1][1:])
            for index in range(1, len(points)):
                start, end = points[index - 1][0], points[index][0]
                if start <= moment <= end:
                    if end == start:
                        return tuple(points[index][1:])
                    fraction = (moment - start) / (end - start)
                    return tuple(a + (b - a) * fraction for a, b in zip(points[index - 1][1:], points[index][1:]))
            return tuple(points[-1][1:])

        points = [(0.0, 0.0), (10.0, 5.0), (10.0, 7.0), (20.0, 7.0), (30.0, 12.0)]
        times = [point[0] for point in points]
        for moment in (-5, 0, 3, 10, 15, 20, 25, 30, 40):
            self.assertEqual(value_at(points, moment, times), scan(points, moment))
            self.assertEqual(value_at(points, moment), scan(points, moment))
        tangled = [(0.0, 0.0), (20.0, 4.0), (10.0, 6.0), (30.0, 9.0)]
        self.assertEqual(value_at(tangled, 15.0), scan(tangled, 15.0))

    def test_snapshot_keeps_every_segment_for_the_chart(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            ledger = Ledger(root / "state.json", root / "history.jsonl", 25, 10)
            now = datetime(2026, 10, 24, 12, 0, 0)
            ledger.date = "2026-10-24"
            ledger.update(now, "earn", "Cursor.exe", "a", 10, max_delta=1000)
            snap = ledger.snapshot()
            self.assertEqual(snap["closed"], [])
            self.assertEqual(len(snap["segments"]), 1)
            self.assertEqual(snap["segments"][0]["kind"], "earn")
            self.assertEqual(snap["segments"][0]["seconds"], 10)

    def test_zoom_holds_the_cursor_and_pan_stays_inside_the_day(self) -> None:
        self.assertEqual(zoom_range(0, 100, 0, 0.5, 30, 0, 100), (0, 50))
        self.assertEqual(zoom_range(0, 100, 1, 0.5, 30, 0, 100), (50, 100))
        self.assertEqual(zoom_range(0, 100, 0.5, 0.1, 30, 0, 200), (35, 65))
        self.assertEqual(zoom_range(0, 100, 0.5, 4, 30, 0, 100), (0, 100))
        self.assertEqual(pan_range(10, 40, 5, 0, 100), (15, 45))
        self.assertEqual(pan_range(0, 50, -20, 0, 100), (0, 50))
        self.assertEqual(clamp_range(80, 130, 0, 100), (50, 100))
        start, end = data_bounds([(1_000.0, 0, 0), (1_010.0, 5, 0)])
        self.assertLess(start, 1_000)
        self.assertGreater(end, 1_010)


class RatioAndColorTests(unittest.TestCase):
    def test_format_ratio(self) -> None:
        self.assertEqual(format_ratio(25, 10), "2.5:1")
        self.assertEqual(format_ratio(10, 10), "1:1")
        self.assertEqual(format_ratio(25, 0), "25:0")

    def test_parse_ratio(self) -> None:
        self.assertEqual(parse_ratio("2.5:1"), (2.5, 1))
        self.assertEqual(parse_ratio("25:10"), (25, 10))
        self.assertEqual(parse_ratio("0.5:1"), (1, 2))
        self.assertIsNone(parse_ratio("nope"))

    def test_normalize_hex(self) -> None:
        self.assertEqual(normalize_hex("#abc"), "#aabbcc")
        self.assertEqual(normalize_hex("3DD68C"), "#3dd68c")
        self.assertIsNone(normalize_hex("#gg0000"))
        self.assertIsNone(normalize_hex("#abcd"))

    def test_chosen_color_survives_the_automatic_one(self) -> None:
        saved = {".!label|fg": "#ff00aa"}
        self.assertEqual(chosen_color(saved, ".!label|fg", "#f07178"), "#ff00aa")
        self.assertEqual(chosen_color(saved, ".!label|bg", "#14161a"), "#14161a")

    def test_saved_ratio_and_colors(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            write_default_config(path)
            write_ratio(path, 30, 12)
            settings = parse_config(path)
            self.assertEqual(settings.work_minutes, 30)
            self.assertEqual(settings.reward_minutes, 12)
            write_colors(path, {"background": "#abc", "chart:productive": "#3dd68c", "bad": "nope"})
            self.assertEqual(
                read_colors(path),
                {"background": "#aabbcc", "chart:productive": "#3dd68c"},
            )
            self.assertEqual(parse_config(path).work_minutes, 30)

    def test_warning_sound_is_the_beep_until_a_file_is_saved(self) -> None:
        self.assertEqual(sound_label(""), "Windows beep")
        self.assertEqual(sound_label(r"C:\missing\nope.wav"), "Windows beep")
        with TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "config.json"
            write_default_config(path)
            self.assertEqual(read_warning_sound(path), "")
            wav = root / "ding.wav"
            wav.write_bytes(b"RIFF")
            write_warning_sound(path, str(wav))
            self.assertEqual(read_warning_sound(path), str(wav))
            self.assertEqual(sound_label(str(wav)), "ding.wav")
            write_warning_sound(path, "  ")
            self.assertEqual(read_warning_sound(path), "")

    def test_known_pages_are_sorted_and_a_tutorial_is_one_video(self) -> None:
        settings = default_settings()
        wiki = consider("chrome.exe", "Python - Wikipedia - Google Chrome", settings)
        self.assertIsNotNone(wiki)
        assert wiki is not None
        self.assertEqual((wiki.mode, wiki.choice, wiki.key), ("auto", "productive", "wikipedia"))
        stunt = consider("chrome.exe", "Backflips on motorcycles - YouTube - Google Chrome", settings)
        self.assertIsNone(stunt)
        tutorial = consider("chrome.exe", "How to rig a character - YouTube - Google Chrome", settings)
        self.assertIsNotNone(tutorial)
        assert tutorial is not None
        self.assertEqual(tutorial.mode, "tutorial")
        self.assertEqual(tutorial.label, "How to rig a character")
        self.assertIsNone(
            consider(
                "chrome.exe",
                "How to rig a character - YouTube - Google Chrome",
                settings,
                skipped={tutorial.key.casefold()},
            )
        )
        blog = consider("firefox.exe", "Notes from the field - Garden Journal - Mozilla Firefox", settings)
        self.assertIsNotNone(blog)
        assert blog is not None
        self.assertEqual((blog.mode, blog.key), ("site", "Garden Journal"))
        broker = consider(
            "firefox.exe",
            "Login - Mozilla Firefox",
            settings,
            url="https://www.interactivebrokers.co.uk/sso/Login?status=UNRE",
        )
        self.assertIsNotNone(broker)
        assert broker is not None
        self.assertEqual((broker.mode, broker.key, broker.label), ("site", "Interactive Brokers", "Interactive Brokers"))
        self.assertEqual(website_name("https://www.interactivebrokers.co.uk"), "Interactive Brokers")
        self.assertEqual(website_name("https://en.wikipedia.org/wiki/Knot"), "Wikipedia")
        self.assertEqual(website_name("https://www.youtube.com/watch"), "Youtube")
        self.assertEqual(website_name("https://login.microsoftonline.com/"), "Microsoft Online")
        self.assertEqual(split_words("stackoverflow"), ["stack", "overflow"])
        self.assertTrue(listed_in("login interactivebrokers.co.uk", "Interactive Brokers"))
        self.assertTrue(
            listed_in(
                "Waitomo Village Chalets home of Kiwipaka, Waitomo Caves - Updated Prices — Mozilla Firefox",
                "Waitomo Village Chalets Home Of Kiwipaka Waitomo Caves Updated Prices",
            )
        )
        self.assertEqual(capitalise_name("SlayTheSpire2"), "Slay The Spire 2")
        self.assertIn("/favicon.ico", icon_hrefs('<link rel="icon" href="/favicon.ico">'))
        marked = default_settings()
        marked.reward_title_keywords = ["Interactive Brokers"]
        self.assertEqual(
            classify(
                "firefox.exe",
                "Login - Mozilla Firefox",
                marked,
                url="https://www.interactivebrokers.co.uk/sso/Login",
            ),
            "reward",
        )
        self.assertIsNone(consider("chrome.exe", "New Tab - Google Chrome", settings))
        app = consider("notepad.exe", "Untitled - Notepad", settings)
        self.assertIsNotNone(app)
        assert app is not None
        self.assertEqual((app.mode, app.key, app.label), ("app", "notepad.exe", "Notepad"))
        self.assertIsNone(consider("cursor.exe", "file.py - Cursor", settings))
        from gordian.ask import AddressGate, loaded_address

        gate = AddressGate()
        gate, typed = loaded_address(gate, "New Tab - Google Chrome", "https://example.com")
        self.assertEqual(typed, "")
        self.assertIsNone(consider("chrome.exe", "New Tab - Google Chrome", settings, url=typed))
        gate, ready = loaded_address(gate, "Example Domain - Google Chrome", "https://example.com/")
        self.assertEqual(ready, "https://example.com/")
        opened = consider("chrome.exe", "Example Domain - Google Chrome", settings, url=ready)
        self.assertIsNotNone(opened)
        assert opened is not None
        self.assertEqual(opened.key, "Example")
        gate = AddressGate()
        gate, ready = loaded_address(gate, "Python - Wikipedia - Google Chrome", "https://en.wikipedia.org/wiki/Knot")
        self.assertTrue(ready)
        gate, typed = loaded_address(gate, "Python - Wikipedia - Google Chrome", "https://github.com")
        self.assertEqual(typed, "")
        stayed = consider("chrome.exe", "Python - Wikipedia - Google Chrome", settings, url=typed)
        self.assertIsNotNone(stayed)
        assert stayed is not None
        self.assertEqual(stayed.key, "wikipedia")
        settings.productive_title_keywords = ["Booking"]
        hotel = "Waitomo Village Chalets, Home of KiwiPaka - Updated Prices - Mozilla Firefox"
        self.assertIsNone(
            consider("firefox.exe", hotel, settings, url="https://www.booking.com/hotel/nz/waitomo.html")
        )
        gate = AddressGate("https://www.booking.com/", "Booking.com", "")
        gate, held = loaded_address(gate, hotel, "")
        self.assertEqual(held, "https://www.booking.com/")
        self.assertIsNone(consider("firefox.exe", hotel, settings, url=held))
        gate, cleared = loaded_address(gate, "New Tab - Mozilla Firefox", "")
        self.assertEqual(cleared, "")
        hotel = "Waitomo Village Chalets home of Kiwipaka, Waitomo Caves - Updated Prices — Mozilla Firefox"
        marked = default_settings()
        marked.productive_title_keywords = ["Waitomo Village Chalets Home Of Kiwipaka Waitomo Caves Updated Prices"]
        self.assertIsNone(consider("firefox.exe", hotel, marked))
        staying = AddressGate("https://www.booking.com/hotel/nz/waitomo.html", hotel, "")
        staying, again = loaded_address(staying, hotel, "https://www.booking.com/hotel/nz/waitomo.html?sid=9")
        self.assertIn("booking.com", again)
        self.assertIsNone(consider("firefox.exe", hotel, settings, url=again))
        productive, reward = pin_keyword([], ["youtube"], tutorial.key, "productive")
        self.assertEqual(productive, [tutorial.key])
        self.assertEqual(reward, ["youtube"])

    def test_ratio_label_and_gordian_window(self) -> None:
        from gordian.ui import about_message, is_gordian_window, password_accepted

        text = about_message(25, 10)
        self.assertIn("W:R is work time to reward time", text)
        self.assertIn("W is the work and R is the free time it earns", text)
        self.assertIn("2.5:1", text)
        self.assertIn("Unlock asks for that password once", text)
        self.assertIn("The application logo opens About", text)
        self.assertIn("Cursor and Visual Studio Code share that minute", text)
        self.assertIn("asks whether to end the pause", text)
        self.assertIn("Hide legend puts the line names away", text)
        self.assertIn("Map is the second plot", text)
        self.assertIn("Unproductive YouTube videos share one square, YT", text)
        import math

        from gordian.monitor import _colorref
        from gordian.treemap import balance_areas, group_color, group_youtube, pack_squares, tile_group

        self.assertEqual(_colorref("#14161a"), 0x001A1614)
        self.assertEqual(_colorref("#e7ecf3"), 0x00F3ECE7)

        self.assertEqual(tile_group("Cursor.exe", "work"), tile_group("Code.exe", "work"))
        self.assertEqual(group_color("Cursor.exe", "work"), group_color("Code.exe", "work"))
        self.assertNotEqual(tile_group("Desmos", "work"), tile_group("Cursor.exe", "work"))
        self.assertEqual(tile_group("Steam", "spend"), "store")
        self.assertEqual(tile_group("YT", "spend"), "video")
        self.assertEqual(tile_group("Paused", "pause"), "pause")
        grouped = dict(group_youtube([("One - YouTube", 10), ("Two - YouTube", 5), ("Steam", 8)]))
        self.assertEqual(grouped["YT"], 15)
        self.assertEqual(grouped["Steam"], 8)
        self.assertNotIn("One - YouTube", grouped)

        green, red = balance_areas(0, 0, 30, 1, 1)
        self.assertEqual((green, red), (1, 1))
        grown, spent = balance_areas(30 * 60, 15 * 60, 15 * 60, 1, 1)
        self.assertAlmostEqual(grown, 30)
        self.assertAlmostEqual(spent, 15)
        _still, exploded = balance_areas(0, 60 * 60, -60, 1, 1)
        self.assertAlmostEqual(exploded, 60 * math.exp(1))
        packed = pack_squares([("Cursor", 60), ("Code", 30), ("Notepad", 10)], 0, 0, 200, 124)
        self.assertEqual(len(packed), 3)
        area = sum(item[2] * item[3] for item in packed)
        self.assertAlmostEqual(area, 200 * 124, delta=1)
        largest = max(packed, key=lambda item: item[2] * item[3])
        self.assertLess(max(largest[2], largest[3]) / min(largest[2], largest[3]), 3)
        self.assertLess(largest[2], 180)
        for left, top, block_w, block_h, _name, _value in packed:
            self.assertGreater(block_w, 0)
            self.assertGreater(block_h, 0)
            self.assertGreaterEqual(left, -0.1)
            self.assertGreaterEqual(top, -0.1)
            self.assertLessEqual(left + block_w, 200.1)
            self.assertLessEqual(top + block_h, 124.1)
        from gordian.ui import PauseMinute, _stretch_seconds, advance_pause_minute, repause_for_unproductive

        begin = datetime(2026, 11, 7, 21, 0, 0)
        state, done = advance_pause_minute(PauseMinute(), begin, "productive", "Cursor.exe", "Cursor")
        state, done = advance_pause_minute(state, begin + timedelta(seconds=40), "productive", "Code.exe", "Visual Studio Code")
        self.assertEqual(done, ())
        self.assertEqual(state.process, "Code.exe")
        self.assertAlmostEqual(_stretch_seconds(state.stretches), 40)
        partial = state
        state, done = advance_pause_minute(state, begin + timedelta(seconds=70), "productive", "Code.exe", "Visual Studio Code")
        self.assertEqual(state.since, None)
        self.assertAlmostEqual(sum((end - start).total_seconds() for start, end, _p, _t in done), 60)
        self.assertEqual(done[0][2], "Cursor.exe")
        self.assertEqual(done[-1][2], "Code.exe")
        wiped, done = advance_pause_minute(partial, begin + timedelta(seconds=50), "unproductive", "steam.exe", "Steam")
        self.assertEqual(wiped, PauseMinute())
        self.assertEqual(done, ())
        held, done = advance_pause_minute(
            PauseMinute((), begin, "Cursor.exe", "Cursor"),
            begin + timedelta(seconds=30),
            "other",
            "explorer.exe",
            "File Explorer",
        )
        self.assertIsNone(held.since)
        self.assertAlmostEqual(_stretch_seconds(held.stretches), 30)
        self.assertTrue(repause_for_unproductive(True, False, "unproductive"))
        self.assertFalse(repause_for_unproductive(True, False, "productive"))
        self.assertFalse(repause_for_unproductive(False, False, "unproductive"))
        from gordian.monitor import logo_command

        self.assertEqual(logo_command(0x0100), 0x0100)
        self.assertEqual(logo_command(0x0103), 0x0100)
        self.assertEqual(logo_command(0x0150), 0x0150)
        self.assertEqual(logo_command(0x0160), 0x0160)
        self.assertEqual(logo_command(0xF060), 0)
        self.assertTrue(password_accepted(True, "", "0000"))
        self.assertTrue(password_accepted(False, "0000", "0000"))
        self.assertFalse(password_accepted(False, "", "0000"))
        self.assertTrue(is_gordian_window("pythonw.exe", "Gordian Knot — 19m 04s left"))
        self.assertTrue(is_gordian_window("GordianKnot.exe", ""))
        self.assertFalse(is_gordian_window("pythonw.exe", "Untitled - Notepad"))
        self.assertFalse(is_gordian_window("SnippingTool.exe", "Gordian Knot"))

    def test_typed_clock_accepts_am_pm_and_24_hour(self) -> None:
        self.assertEqual(parse_typed_clock("6:01am"), 6 * 60 + 1)
        self.assertEqual(parse_typed_clock("6:01 am"), 6 * 60 + 1)
        self.assertEqual(parse_typed_clock("10:01pm"), 22 * 60 + 1)
        self.assertEqual(parse_typed_clock("12am"), 0)
        self.assertEqual(parse_typed_clock("12pm"), 12 * 60)
        self.assertEqual(parse_typed_clock("12:30pm"), 12 * 60 + 30)
        self.assertEqual(parse_typed_clock("18:05"), 18 * 60 + 5)
        self.assertEqual(parse_typed_clock("24:00", end=True), 24 * 60)
        self.assertIsNone(parse_typed_clock("24:00"))
        self.assertIsNone(parse_typed_clock("13pm"))
        self.assertIsNone(parse_typed_clock("6:60am"))
        start = parse_typed_clock("6:01am")
        end = parse_typed_clock("10:01am")
        assert start is not None and end is not None
        block = make_block(["mon"], format_clock(start), format_clock(end), False, "1:1")
        self.assertIsNotNone(block)
        assert block is not None
        self.assertEqual((block.start, block.end), ("06:01", "10:01"))

    def test_opening_another_day_keeps_today_marked(self) -> None:
        import tkinter as tk

        from gordian.schedule import DAYS
        from gordian.weekgrid import WeekSchedule

        root = tk.Tk()
        root.withdraw()
        try:
            grid = WeekSchedule(
                root,
                lambda *_args: None,
                lambda *_args: None,
                lambda *_args: None,
                "#111111",
                "#eeeeee",
                "#888888",
                "#333333",
                "#3a6ea5",
                "#8b2e2e",
                "#008000",
            )
            grid.pack(fill="both", expand=True)
            root.geometry("640x480")
            root.update()
            monday = make_block(["mon"], "9:00", "10:00", False, "1:1")
            friday = make_block(["fri"], "9:00", "10:00", False, "1:1")
            assert monday is not None and friday is not None
            grid.set_blocks((monday, friday))
            today = datetime.now().weekday()
            other = DAYS[(today + 1) % 7]
            grid.show_day(other)
            fills = [
                grid.header.itemcget(item, "fill")
                for item in grid.header.find_all()
                if grid.header.type(item) == "rectangle"
            ]
            self.assertEqual(fills.count("#3a6ea5"), 1)
            self.assertEqual(len(grid._hits), 1 if other in {"mon", "fri"} else 0)
            grid.show_day(None)
            self.assertIsNone(grid.view_day)
            self.assertEqual(len(grid._hits), 2)
            self.assertEqual(
                [
                    grid.header.itemcget(item, "fill")
                    for item in grid.header.find_all()
                    if grid.header.type(item) == "rectangle"
                ].count("#3a6ea5"),
                1,
            )
        finally:
            root.destroy()

    def test_ratio_block_and_overnight_lock(self) -> None:
        work = make_block(["Monday", "tue"], "9:00", "17:00", False, "25:10")
        lock = make_block(["sat"], "22:00", "06:00", True, "")
        self.assertIsNotNone(work)
        self.assertIsNotNone(lock)
        assert work is not None and lock is not None
        self.assertEqual(work.ratio, "2.5:1")
        self.assertEqual(work.days, ("mon", "tue"))
        blocks = (work, lock)
        monday = datetime(2026, 10, 26, 10, 0)
        self.assertEqual(active_block(blocks, monday), work)
        self.assertIsNone(active_block(blocks, datetime(2026, 10, 26, 18, 0)))
        self.assertEqual(active_block(blocks, datetime(2026, 10, 24, 23, 0)), lock)
        self.assertEqual(active_block(blocks, datetime(2026, 10, 25, 1, 0)), lock)
        self.assertIsNone(active_block(blocks, datetime(2026, 10, 25, 8, 0)))
        self.assertEqual(parse_blocks([work.to_json(), {"days": ["nope"], "start": "1", "end": "2"}]), (work,))

    def test_lock_wins_when_both_match(self) -> None:
        rated = make_block(["sun"], "0:00", "24:00", False, "3:1")
        locked = make_block(["Sunday"], "12:00", "13:00", True, "9:1")
        assert rated is not None and locked is not None
        self.assertTrue(active_block((rated, locked), datetime(2026, 10, 25, 12, 30)).locked)

    def test_overwrite_keeps_the_rest_and_replace_removes_the_block(self) -> None:
        base = make_block(["mon", "wed"], "9:00", "17:00", False, "2.5:1")
        added = make_block(["wed", "fri"], "12:00", "13:00", False, "1:1")
        assert base is not None and added is not None
        self.assertEqual(overlapping_days((base,), added), ("wed",))
        overwritten = {
            (block.days, block.start, block.end, block.ratio)
            for block in place_block((base,), added, "overwrite")
        }
        self.assertIn((("mon",), "09:00", "17:00", "2.5:1"), overwritten)
        self.assertIn((("wed",), "09:00", "12:00", "2.5:1"), overwritten)
        self.assertIn((("wed",), "13:00", "17:00", "2.5:1"), overwritten)
        self.assertIn((("wed", "fri"), "12:00", "13:00", "1:1"), overwritten)
        replaced = {
            (block.days, block.start, block.end, block.ratio)
            for block in place_block((base,), added, "replace")
        }
        self.assertIn((("mon",), "09:00", "17:00", "2.5:1"), replaced)
        self.assertIn((("wed", "fri"), "12:00", "13:00", "1:1"), replaced)
        self.assertFalse(any(block.days == ("wed",) and block.ratio == "2.5:1" for block in place_block((base,), added, "replace")))
        self.assertEqual(overlapping_days((base,), added, ignore=base), ())

    def test_block_name_and_pastel_stay_with_the_block(self) -> None:
        afternoon = make_block(["mon"], "13:00", "17:00", False, "1:1", name="  Afternoon  ", color=0)
        evening = make_block(["mon"], "17:00", "21:00", False, "2:1", name="Evening", color=1)
        self.assertIsNotNone(afternoon)
        self.assertIsNotNone(evening)
        assert afternoon is not None and evening is not None
        self.assertEqual(afternoon.name, "Afternoon")
        self.assertNotEqual(block_fill(afternoon.color), block_fill(evening.color))
        self.assertEqual(block_caption(afternoon), "Afternoon\n1:1")
        placed = place_block((afternoon,), evening, "overwrite")
        kept = {block.name: block.color for block in placed}
        self.assertEqual(kept["Afternoon"], 0)
        self.assertEqual(kept["Evening"], 1)
        self.assertEqual(next_block_color((afternoon, evening)), 2)
        neighbour = make_block(["mon"], "15:00", "17:00", False, "1:1", name="Other", color=2)
        assert neighbour is not None
        split = place_block((afternoon,), neighbour, "overwrite")
        self.assertIn("Afternoon", {block.name for block in split})
        self.assertIn("Other", {block.name for block in split})
        loaded = parse_blocks(
            [
                {"days": ["mon"], "start": "13:00", "end": "17:00", "locked": False, "ratio": "1:1"},
                {"days": ["mon"], "start": "17:00", "end": "21:00", "locked": False, "ratio": "2:1"},
            ]
        )
        self.assertEqual([block.color for block in loaded], [0, 1])
        saved = parse_blocks([afternoon.to_json()])
        self.assertEqual((saved[0].name, saved[0].color), ("Afternoon", 0))

    def test_lockdown_stays_on_the_hours_that_were_chosen(self) -> None:
        block = make_block(["thu"], "18:00", "21:00", False, "25:10", True)
        neighbor = make_block(["thu"], "21:00", "22:00", False, "1:1")
        self.assertIsNotNone(block)
        self.assertIsNotNone(neighbor)
        assert block is not None and neighbor is not None
        self.assertEqual(block.ratio, "2.5:1")
        self.assertEqual(block_mark(block), "Lockdown")
        self.assertTrue(parse_blocks([block.to_json()])[0].lockdown)
        during = datetime(2026, 10, 29, 19, 0)
        before = datetime(2026, 10, 29, 17, 0)
        self.assertTrue(lockdown_active((block,), during))
        self.assertFalse(lockdown_active((block,), before))
        placed = place_block((block,), neighbor, "overwrite")
        marks = {(item.start, item.end, item.lockdown) for item in placed}
        self.assertIn(("18:00", "21:00", True), marks)
        self.assertIn(("21:00", "22:00", False), marks)

    def test_one_program_is_one_bar_and_browser_pages_stay_separate(self) -> None:
        rows = dict(
            application_totals(
                [
                    {"kind": "earn", "process": "pythonw.exe", "title": "Gordian Knot — 1m left", "seconds": 10},
                    {"kind": "earn", "process": "pythonw.exe", "title": "Gordian Knot — 2m left", "seconds": 5},
                    {"kind": "spend", "process": "firefox.exe", "title": "YouTube - Mozilla Firefox", "seconds": 8},
                    {"kind": "neutral", "process": "firefox.exe", "title": "Docs - Mozilla Firefox", "seconds": 3},
                ]
            )
        )
        self.assertEqual(rows["Gordian Knot"], 15)
        self.assertEqual(rows["YouTube"], 8)
        self.assertEqual(rows["Docs"], 3)
        self.assertNotIn("pythonw.exe", rows)

    def test_bonus_is_extra_free_time_and_survives_a_new_ratio(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            ledger = Ledger(root / "state.json", root / "history.jsonl", 25, 10)
            ledger.add_bonus(600)
            ledger.save()
            loaded, notice = Ledger.load(root / "state.json", root / "history.jsonl", 25, 10)
            self.assertIsNone(notice)
            self.assertAlmostEqual(loaded.remaining_seconds, 600)
            loaded.set_ratio(10, 10, recompute=True)
            self.assertAlmostEqual(loaded.remaining_seconds, 600)
            loaded.reset()
            self.assertEqual(loaded.remaining_seconds, 0)

    def test_milestones_round_trip(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            write_default_config(path)
            items = (Milestone("Finish the level", 15, False), Milestone("Sketch the map", 5, True))
            write_milestones(path, items)
            loaded = parse_config(path)
            self.assertEqual(loaded.milestones, items)
            self.assertEqual(parse_milestones([{"title": "  ", "bonus_minutes": 5}, {"title": "Ok", "bonus_minutes": 0}]), ())

    def test_scan_groups_programs_and_locks_the_choice(self) -> None:
        found = programs_from_entries(
            [
                ("Steam", "C:\\Games\\Steam\\steam.exe,0", "C:\\Games\\Steam"),
                ("Steam", "C:\\Games\\Steam\\steam.exe", "C:\\Games\\Steam"),
                ("Visual C++ Redistributable", "C:\\vc.exe", "C:\\vc"),
                ("Cursor", "C:\\cursor.exe", "C:\\cursor"),
            ]
        )
        self.assertEqual([(item.executable, item.reason) for item in found], [("steam.exe", "Steam")])
        productive, reward = lock_choices(
            ["cursor.exe", "steam.exe"],
            ["steam.exe"],
            [("steam.exe", "unproductive"), ("Spotify.exe", "productive")],
        )
        self.assertEqual(productive, ["cursor.exe", "Spotify.exe"])
        self.assertEqual(reward, ["steam.exe"])

    def test_scan_lists_real_programs_and_websites(self) -> None:
        with TemporaryDirectory() as folder:
            steam_dir = Path(folder)
            (steam_dir / "uninstall.exe").write_bytes(b"old")
            (steam_dir / "steam.exe").write_bytes(b"steam")
            found = list_programs(
                [
                    ("Steam", f"{steam_dir / 'uninstall.exe'},0", f"{steam_dir}\nValve"),
                    ("Google Chrome", "C:\\Program Files\\Google\\Chrome\\chrome.exe", "C:\\chrome"),
                    ("Cursor", "C:\\cursor.exe", "C:\\cursor"),
                    ("Visual C++ Redistributable", "C:\\vc.exe", "C:\\vc"),
                ]
            )
        names = [item.executable.casefold() for item in found]
        self.assertIn("steam.exe", names)
        self.assertNotIn("uninstall.exe", names)
        self.assertIn("cursor.exe", names)
        self.assertNotIn("chrome.exe", names)
        sites = sites_from_urls(
            [
                ("https://www.youtube.com/watch?v=1", 5, "Chrome"),
                ("https://github.com/gordian", 2, "Chrome"),
                ("chrome://newtab/", 9, "Chrome"),
            ]
        )
        hosts = [item.executable for item in sites]
        self.assertEqual(hosts[0], "youtube.com")
        self.assertIn("github.com", hosts)
        self.assertTrue(all(item.kind == "site" for item in sites))
        steam = next(item for item in found if item.executable.casefold() == "steam.exe")
        self.assertEqual(
            starting_choice(steam, ["cursor.exe"], ["steam.exe"], [], ["youtube"]),
            "unproductive",
        )
        github = next(item for item in sites if item.executable == "github.com")
        self.assertEqual(starting_choice(github, [], [], [], ["youtube"]), "productive")
        youtube = FoundProgram("youtube.com", "youtube.com", "", "Visited in Chrome", "site")
        self.assertEqual(starting_choice(youtube, [], [], [], ["youtube"]), "unproductive")
        productive_sites, reward_sites = lock_sites(
            ["docs.google.com"],
            ["youtube"],
            [("youtube.com", "productive"), ("github.com", "unproductive")],
        )
        self.assertEqual(productive_sites, ["docs.google.com", "youtube.com"])
        self.assertEqual(reward_sites, ["github.com"])
        path = "C:\\Games\\Steam\\steam.exe"
        payload = path.encode("mbcs") + b"\x00"
        info = bytearray(28 + len(payload))
        info[0:4] = len(info).to_bytes(4, "little")
        info[4:8] = (28).to_bytes(4, "little")
        info[8:12] = (1).to_bytes(4, "little")
        info[16:20] = (28).to_bytes(4, "little")
        info[28:] = payload
        data = bytearray(76 + len(info))
        data[0:4] = (76).to_bytes(4, "little")
        data[20:24] = (2).to_bytes(4, "little")
        data[76:] = info
        self.assertEqual(lnk_target_bytes(bytes(data)), path)
        loose = FoundProgram("Sekiro", "sekiro.exe", "D:\\Games\\Sekiro", "On this drive")
        self.assertEqual(starting_choice(loose, [], [], [], []), "unproductive")
        tool = FoundProgram("Cursor", "Cursor.exe", "C:\\cursor", "Start menu")
        self.assertEqual(starting_choice(tool, [], [], [], []), "productive")

    def test_overtime_warns_then_closes(self) -> None:
        steam = "app:steam.exe"
        state, action = advance_overtime("spend", 0, False, steam, 0, Overtime(), False)
        self.assertEqual(action, "warn")
        held, action = advance_overtime("spend", -1, False, steam, 3, state, False)
        self.assertEqual(action, "")
        self.assertEqual(held.stage, 1)
        early, action = advance_overtime("spend", -1, False, steam, 59, state, False)
        self.assertEqual(action, "")
        again, action = advance_overtime("spend", -1, False, steam, 60, state, False)
        self.assertEqual(action, "warn-again")
        waiting, action = advance_overtime("spend", -1, False, steam, 60, state, True)
        self.assertEqual(action, "warn-again")
        switched, action = advance_overtime("spend", -10, False, "app:vlc.exe", 4, state, False)
        self.assertEqual(action, "flash")
        self.assertEqual(switched.started, state.started)
        self.assertEqual(switched.key, "app:vlc.exe")
        open_still, action = advance_overtime("spend", -1, False, steam, 299, again, False)
        self.assertEqual(action, "")
        closed, action = advance_overtime("spend", -1, False, steam, 300, state, False)
        self.assertEqual(action, "close")
        denied, action = advance_overtime("spend", -20, False, steam, 0, Overtime(), False, {steam})
        self.assertEqual(action, "blocked")
        quiet, action = advance_overtime("spend", -20, False, steam, 1, denied, False, {steam})
        self.assertEqual(action, "again")
        other, action = advance_overtime("spend", -20, False, "app:vlc.exe", 0, Overtime(), False, {steam})
        self.assertEqual(action, "warn")
        locked, action = advance_overtime(
            "spend", -20, False, "app:vlc.exe", 0, Overtime(), False, set(), lockdown=True
        )
        self.assertEqual(action, "blocked")
        self.assertIn("earned back", blocked_message(-20))
        self.assertIn("0m 20s", blocked_message(-20))
        self.assertEqual(visit_key("steam.exe", "Steam", ["chrome.exe"], ["youtube"]), "app:steam.exe")
        self.assertEqual(
            visit_key("chrome.exe", "lofi - YouTube", ["chrome.exe"], ["youtube"]),
            "site:youtube",
        )
        left, action = advance_overtime("earn", 0, False, "", 20, state, False)
        self.assertEqual((left.stage, action), (0, ""))
        hidden, action = advance_overtime("neutral", 0, False, "", 300, state, True)
        self.assertEqual(action, "close")
        spared, action = advance_overtime("spend", 200, False, steam, 300, state, True)
        self.assertEqual((spared.stage, action), (0, ""))
        from gordian.warning import may_force_close

        self.assertFalse(may_force_close(200, False))
        self.assertTrue(may_force_close(0, False))
        self.assertTrue(may_force_close(200, True))
        held_off, action = advance_overtime("neutral", 30, False, "", 300, state, False, hold=True)
        self.assertEqual((held_off.stage, action), (0, ""))
        paused_off, action = advance_overtime("spend", -20, True, steam, 300, state, True)
        self.assertEqual((paused_off.stage, action), (0, ""))
        self.assertFalse(may_close("explorer.exe"))
        self.assertFalse(may_close("pythonw.exe"))
        self.assertTrue(may_close("steam.exe"))
        self.assertTrue(may_close("chrome.exe"))
        self.assertEqual(close_style("chrome.exe"), "tab")
        self.assertEqual(close_style("firefox.exe"), "tab")
        self.assertEqual(close_style("steam.exe"), "window")
        self.assertEqual(close_style("explorer.exe"), "")
        settings = default_settings()
        settings.productive_title_keywords = ["github.com"]
        self.assertEqual(classify("chrome.exe", "Build software better, together - github.com", settings), "productive")
        self.assertEqual(classify("chrome.exe", "lofi hip hop - YouTube", settings), "reward")

    def test_long_spans_label_by_day(self) -> None:
        now = datetime(2026, 10, 26, 20, 0)
        self.assertEqual(axis_label(now.timestamp(), 10 * 86400), now.strftime("%b %d"))
        self.assertGreaterEqual(_tick_step(90 * 86400), 7 * 86400)

    def test_y_axis_uses_a_finer_step_and_a_day_range_splits_the_bars(self) -> None:
        ticks = y_ticks(0, 220 * 3600)
        self.assertGreaterEqual(len(ticks), 4)
        self.assertEqual(ticks[0], 0)
        self.assertTrue(all(format_axis_duration(tick).endswith("h") or tick == 0 for tick in ticks))
        self.assertEqual(parse_day("6th august", 2026), date(2026, 8, 6))
        self.assertEqual(parse_day("17 August", 2026), date(2026, 8, 17))
        self.assertEqual(parse_day("August 6", 2026), date(2026, 8, 6))
        rows = dict(
            application_totals(
                [
                    {
                        "kind": "earn",
                        "process": "Cursor.exe",
                        "title": "Cursor",
                        "start": "2026-08-06T09:00:00",
                        "end": "2026-08-06T12:00:00",
                        "seconds": 3 * 3600,
                    },
                    {
                        "kind": "earn",
                        "process": "Cursor.exe",
                        "title": "Cursor",
                        "start": "2026-08-18T09:00:00",
                        "end": "2026-08-18T12:00:00",
                        "seconds": 3 * 3600,
                    },
                ],
                datetime(2026, 8, 6),
                datetime(2026, 8, 18),
            )
        )
        self.assertAlmostEqual(rows["Cursor"], 3 * 3600)


def _segment(start: datetime, end: datetime, kind: str, seconds: float) -> dict:
    return {"start": start.isoformat(), "end": end.isoformat(), "kind": kind, "seconds": seconds}


if __name__ == "__main__":
    unittest.main()
