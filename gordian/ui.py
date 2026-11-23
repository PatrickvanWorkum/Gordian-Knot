"""The window, the tray, and the once-a-second tick that ties the rest together.

App._tick is the loop: read the foreground window, classify it, apply the
schedule, add the seconds to the ledger, save, then warn or close if free
time is gone. The three tiles are the current kind, work today, and free time
or overspent. The chart and the Apps / Activity / Schedule / Milestone tabs
sit under them.

Dialogs are separate windows. Close and Escape hide this window to the tray.
Colors, from the logo menu or Shift+`, edits a widget's color, and a click on text
also edits its font. Warning sound, also on that menu, picks a .wav or leaves
the Windows beep. Both are stored in config.json. Night is the dark
palette. The light palette is the usual one.
"""

from __future__ import annotations

import fnmatch
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pystray
import tkinter as tk
from tkinter import filedialog, font as tkfont
from PIL import Image, ImageTk

from gordian import __version__
from gordian.chart import AppBars, CumulativeChart, application_totals, covered_range
from gordian.treemap import UsageMap, balance_areas, group_youtube
from gordian.classify import Decision, Memory, classify, resolve, segment_identity
from gordian.colors import chosen_color, hex_to_rgb, hsv_to_rgb, normalize_hex, rgb_to_hex, rgb_to_hsv
from gordian.formatting import format_duration, format_ratio, parse_day, parse_ratio, plain_number
from gordian.ledger import (
    AWAY_SECONDS,
    Ledger,
    clear_saved_history,
    forget_saved_segments,
    read_saved_segments,
    wall_gap_kind,
)
from gordian.monitor import (
    acquire_single_instance,
    alert,
    close_program,
    flash_window,
    get_sample,
    hold_inactive,
    minimize_window,
    pin_window,
    release_owner,
    set_title_theme,
    stick_window,
    window_rect,
    prepare_windows,
    warn,
    watch_shutdown,
    cancel_pending_closes,
    install_logo_menu,
)
from gordian.paths import data_dir, log_exception, project_root
from gordian.startmenu import install_start_menu_soon
from gordian.settings import (
    Milestone,
    Settings,
    default_settings,
    ensure_config,
    parse_config,
    read_ask_skip,
    read_colors,
    read_fonts,
    read_night,
    read_warning_sound,
    write_ask_skip,
    write_colors,
    write_fonts,
    write_night,
    write_warning_sound,
    write_default_config,
    write_edited_lists,
    write_milestones,
    write_password,
    write_ratio,
    write_scan_lists,
    write_schedule,
)
from gordian.address import page_host
from gordian.ask import AddressGate, consider, loaded_address, pin_keyword
from gordian.favicon import favicon_image
from gordian.icons import icon_image
from gordian.scan import lock_choices, lock_sites, scan_computer, starting_choice
from gordian.sound import play_warning, sound_label
from gordian.schedule import (
    DAYS,
    DAY_NAMES,
    Block,
    active_block,
    lockdown_active,
    format_clock,
    make_block,
    next_block_color,
    overlapping_days,
    parse_clock,
    parse_typed_clock,
    place_block,
    typed_clock,
)
from gordian.titles import activity_name
from gordian.warning import (
    CLOSE_AFTER,
    Overtime,
    advance_overtime,
    may_force_close,
    lockout_message,
    overlay_text,
    schedule_lockdown_message,
    reward_label,
    visit_key,
)
from gordian.weekgrid import WeekSchedule


BG = "#f0f0f0"
CARD = "#ffffff"
TEXT = "#000000"
MUTED = "#404040"
LINE = "#a0a0a0"
GREEN = "#008000"
AMBER = "#9a5b00"
RED = "#a40000"
IDLE = "#555555"
PAUSE = "#8a6d00"
FONT = "Tahoma"
DAY_FONT = "Segoe UI"

NIGHT = {
    "bg": "#14161a",
    "card": "#1e2229",
    "text": "#e7ecf3",
    "muted": "#9aa3b2",
    "line": "#2c3340",
    "green": "#3dd68c",
    "amber": "#f0a05a",
    "red": "#f07178",
    "idle": "#8b97a8",
    "pause": "#d2b56b",
    "grid": "#2c3340",
    "sleep": "#6a7384",
    "shutdown": "#6e2b32",
    "bar": "#6f8fbf",
    "active": "#3a4458",
}

FONT_FAMILIES = (
    "Segoe UI",
    "Tahoma",
    "Arial",
    "Verdana",
    "Calibri",
    "Georgia",
    "Times New Roman",
    "Consolas",
    "Courier New",
    "Cambria",
)

KIND_COLORS = {
    "earn": GREEN,
    "spend": AMBER,
    "idle": IDLE,
    "neutral": MUTED,
    "paused": PAUSE,
    "locked": RED,
}

STATUS_WORDS = {
    "earn": "Earning",
    "spend": "Spending",
    "idle": "Idle",
    "neutral": "Not counting",
    "paused": "Paused",
    "locked": "Locked",
}

LOG_LABELS = {
    "earn": "earning",
    "spend": "spending",
    "idle": "idle",
    "neutral": "other",
    "paused": "paused",
    "locked": "locked",
    "sleep": "sleep",
    "shutdown": "shutdown",
    "clock": "missing",
    "lost": "lost",
    "reset": "reset",
}


from gordian.app_icon import apply_tk_window_icon, draw_icon, ensure_application_icon


def password_accepted(unlocked: bool, typed: str, expected: str) -> bool:
    """An unlocked app skips the password. A locked app needs the real one."""
    return bool(unlocked) or typed == expected


@dataclass(frozen=True)
class PauseMinute:
    """Work counted toward ending a pause. Only an unproductive app clears it."""

    stretches: tuple = ()
    since: datetime | None = None
    process: str = ""
    title: str = ""


def _stretch_seconds(stretches) -> float:
    total = 0.0
    for start, end, _process, _title in stretches:
        total += max(0.0, (end - start).total_seconds())
    return total


def advance_pause_minute(
    state: PauseMinute, wall: datetime, focus: str, process: str, title: str
) -> tuple[PauseMinute, tuple]:
    """Keep a work minute going across work programs.

    focus is "productive", "unproductive", or "other". Switching from Cursor to
    Visual Studio Code keeps the minute. An unproductive program clears it.
    Anything else holds the minute where it is. The returned stretches are the
    minute that just completed.
    """
    if focus == "unproductive":
        return PauseMinute(), ()
    if focus != "productive":
        if state.since is None or wall <= state.since:
            return PauseMinute(state.stretches), ()
        held = state.stretches + ((state.since, wall, state.process, state.title),)
        return PauseMinute(held), ()
    if state.since is not None:
        open_seconds = (wall - state.since).total_seconds()
        if open_seconds > 90:
            return PauseMinute(state.stretches, wall, process, title), ()
        if _stretch_seconds(state.stretches) + max(0.0, open_seconds) >= 60:
            need = max(0.0, 60 - _stretch_seconds(state.stretches))
            cut = state.since + timedelta(seconds=need)
            done = state.stretches + ((state.since, cut, state.process, state.title),)
            return PauseMinute(), done
        if state.process.casefold() != (process or "").casefold():
            closed = state.stretches + ((state.since, wall, state.process, state.title),)
            return PauseMinute(closed, wall, process, title), ()
        return PauseMinute(state.stretches, state.since, process, title), ()
    return PauseMinute(state.stretches, wall, process, title), ()


def repause_for_unproductive(latched: bool, paused: bool, focus: str) -> bool:
    """A pause the user asked for comes back on an unproductive program."""
    return bool(latched) and not paused and focus == "unproductive"


def next_pause_hold(held: bool, focus: str) -> bool:
    """An answered pause question stays quiet until an unproductive program."""
    if focus == "unproductive":
        return False
    return held


def is_gordian_window(process: str, title: str) -> bool:
    """True when the foreground window is this app."""
    name = (process or "").casefold()
    if name == "gordianknot.exe":
        return True
    if name not in {"python.exe", "pythonw.exe"}:
        return False
    return (title or "").casefold().startswith("gordian knot")


def about_message(work_minutes: float, reward_minutes: float) -> str:
    work = plain_number(work_minutes)
    reward = plain_number(reward_minutes)
    return (
        f"{work} minutes of work earns {reward} minutes of free time, counted as you go. "
        "Work counts while an allowlisted program is in front and you have used the keyboard or mouse recently. "
        "Steam, YouTube, and games spend that time even if you step away.\n\n"
        "Pause holds the balance still until you press Resume. "
        "A minute on work programs, while you are using them, asks whether to end the pause and count that minute, including the free time it earns. "
        "That question appears only while Pause is on. Answering it keeps that question quiet until an unproductive program, and then work again. "
        "Count it ends the pause, and an unproductive program pauses again until you press Resume. "
        "Cursor and Visual Studio Code share that minute. It starts over only on an unproductive program, and the pause comes back. "
        "Minimizing this window does not end the pause or restart its clock.\n\n"
        "A warning appears when Steam, YouTube, or a game is in front and free time is used up. "
        "It plays the Windows beep. Warning sound on the logo menu can play a .wav instead. "
        "A new program or website asks in the corner whether it counts as work. "
        "Typing an address does not ask. The question waits until that page has loaded. "
        "Wikipedia and other reference sites count as work on their own. "
        "YouTube stays unproductive, and a tutorial video can count as work without changing the rest of YouTube. "
        "Closing the window leaves the watcher running in the tray.\n\n"
        "The green line is productive time and the purple line is Steam, YouTube, and games. "
        "Gold is paused time and grey is time that is not counting. Click a line's name to hide it. "
        "Grey bands are sleep and dark red bands are a shut down. Both are saved and the balance stays put through them. "
        "Setting the clock forward draws a spotted pink band for the missing time. Setting it back draws a solid pink line with the time that was lost. "
        "Free time carries past midnight. Reset time clears it and draws a solid light blue line. "
        "Scroll to zoom, drag to move, and Fit or a double-click shows the whole day. Reload rereads the saved days. "
        "Hide legend puts the line names away. Map is the second plot, in the same place as the time series. "
        "It draws work in green and unproductive time in red. The two blocks start the same size. "
        "Work grows linearly with the W:R. The red block is the time spent on unproductive programs. "
        "When free time is below zero, the red block grows as e raised to the W:R times the overspent minutes. "
        "Programs fill each block in squares, the way a disk map shows files. "
        "Unproductive YouTube videos share one square, YT.\n\n"
        "Apps is the total time on each program. One program is one bar. Browser pages stay separate.\n\n"
        "W:R is work time to reward time. W is the work and R is the free time it earns. "
        "2.5:1 means 2.5 minutes of work earns 1 minute of free time, and 25:10 is that same W:R. "
        "An empty box shows W:R until a ratio is entered. "
        "The W:R under the squares changes how much free time work earns, and it asks for the password. "
        "Unlock asks for that password once. Reset time, the schedule, lists, Scan, and milestones then skip it until you press Lock. "
        "Closing the app locks it again. "
        "The application logo opens About, Reset time, Clear history, Change password, Unlock, Warning sound, and Colors.\n\n"
        "Schedule is the week. Drag down a day to set the hours, then enter a name, the W:R, and any other days. "
        "Blocks cycle through pastel colours. "
        "Lockdown on a block keeps unproductive programs closed for those hours, and new ones cannot be added.\n\n"
        "Scan lists installed programs that look like games, stores, or media. Mark each one productive or not, then lock that choice in with the password.\n\n"
        "Milestone is a list of tasks. Uncomplete and completed tasks show their bonus time. "
        "Marking one done asks for the password and adds that bonus to free time.\n\n"
        "Colors on the logo menu, or Shift+`, lets you click anything and change its color with the wheel or a hex code. "
        "Warning sound on that menu chooses the alert.\n\n"
        f"Gordian Knot {__version__}. Reset time asks for the password and can also clear every saved day. "
        "Clear history does that on its own. Lists, the schedule, and the password stay. "
        "YouTube and other titled pages count inside the browser. Fullscreen games and games in known install folders count too."
    )


def format_segment(segment: dict, today: date | None = None) -> str:
    try:
        moment = datetime.fromisoformat(segment["start"])
        if today is not None and moment.date() != today:
            stamp = f"{moment.strftime('%b')} {moment.day} {moment.strftime('%H:%M')}"
        else:
            stamp = moment.strftime("%H:%M")
    except (TypeError, ValueError):
        stamp = "--:--"
    label = LOG_LABELS.get(segment.get("kind", ""), str(segment.get("kind", "")))
    clock = format_duration(float(segment.get("seconds") or 0))
    shown = activity_name(segment.get("process") or "", segment.get("title") or "")
    if len(shown) > 52:
        shown = shown[:51] + "..."
    return f"{stamp}  {clock:>10}  {label:<9}  {shown}"


class App:
    def __init__(
        self,
        settings: Settings,
        ledger: Ledger,
        config_path,
        data_path,
        notice_text: str,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.config_path = config_path
        self.data_path = data_path
        self.notice_text = notice_text
        self.user_note = ""
        self.paused = False
        self._pause_latch = False
        self._pause_minute = PauseMinute()
        self._pause_offer = None
        self._pause_offer_box = None
        self._pause_hold = False
        self.memory = Memory()
        self.reward_warned = False
        self.overtime = Overtime()
        self.over_target = (0, 0, "")
        self._over_box = None
        self._over_label = None
        self._over_balance = None
        self._last_close = 0.0
        self._hold_close_until = 0.0
        self._lockdown_until = 0.0
        self._warning_thread = None
        self.running = True
        self.icon = None
        self.icon_color = None
        self.log_lines = None
        self.menu_status = "Starting"
        self.menu_work = "Work today: 0m 00s"
        self.menu_free = "Free time left: 0m 00s"
        self.activity_view = "feed"
        self.debug_mode = False
        self._color_dialog = None
        self.custom_colors = read_colors(config_path)
        self.saved_fonts = read_fonts(config_path)
        self.night = read_night(config_path)
        self.warning_sound = read_warning_sound(config_path)
        self._ask_skip = read_ask_skip(config_path)
        self._ask_box = None
        self._ask_key = ""
        self._ask_later: set[str] = set()
        self._address_gate = AddressGate()
        self.unlocked = False
        self._header_logo_key = ""
        self._logo_photos: dict = {}
        self._logo_missing: set[str] = set()
        self._logo_wait: dict = {}
        self._bg_widgets: list = []
        self._dialog_bg: list = []
        self.active_block = None
        self.lock_warned = False
        self.last_monotonic = time.monotonic()
        self.last_wall = None
        self.last_save = time.monotonic()
        self._last_render = None
        self._shown_title = ""
        self._history_segments: list[dict] = []
        self._history_loading = False
        try:
            self.config_mtime = config_path.stat().st_mtime
        except OSError:
            self.config_mtime = 0.0

    def run(self) -> None:
        self._build()
        try:
            watch_shutdown(self.root, self._save_quietly)
            self.root.after(300, self._refresh_logo_menu)
            self.root.after(50, self._pump_logo_menu)
            self.root.bind("<Map>", lambda _event: self._refresh_logo_menu(), add="+")
        except Exception:
            log_exception("shutdown hook")
        self._start_tray()
        self.last_monotonic = time.monotonic()
        self.last_save = time.monotonic()
        self.root.after(0, self._load_history_soon)
        self.root.after(200, self.tick)
        self.root.after(300, self.show)
        self.root.mainloop()

    def _load_history_soon(self) -> None:
        """Load finished days off the UI thread so the window can open at once."""
        if self._history_loading:
            return
        self._history_loading = True
        path = self.ledger.history_path

        def work() -> None:
            rows: list[dict] = []
            try:
                rows = read_saved_segments(path)
            except Exception:
                log_exception("history")

            def apply() -> None:
                self._history_segments = rows
                self._history_loading = False
                self._refresh_view()

            try:
                self.root.after(0, apply)
            except tk.TclError:
                return

        threading.Thread(target=work, name="gordian-history", daemon=True).start()

    def _build(self) -> None:
        self.root = tk.Tk()
        self.root.title("Gordian Knot")
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.bind("<Escape>", lambda _event: self.hide())
        self.root.report_callback_exception = self._on_tk_error
        self.root.bind_class("GordianDebug", "<Button-1>", self._debug_click)
        self.root.bind_all("<Map>", self._tag_for_debug, add="+")
        self.root.bind_all("<KeyPress>", self._debug_hotkey, add="+")
        self.root.bind("<Map>", self._on_root_map, add="+")
        self._bg_widgets = [self.root]

        try:
            ico = ensure_application_icon(GREEN)
            self._icon_image = ImageTk.PhotoImage(draw_icon(GREEN))
            if ico is not None:
                apply_tk_window_icon(self.root, ico, GREEN)
            else:
                self.root.iconphoto(True, self._icon_image)
        except Exception:
            log_exception("window icon")

        self.accent = tk.Frame(self.root, bg=MUTED, height=4)
        self.accent.pack(fill="x")

        bar = self._card(self.root)
        bar.pack(fill="x", padx=16, pady=(10, 0))
        heading = tk.Frame(bar, bg=CARD)
        heading.pack(fill="x", padx=12, pady=8)
        self._logo_menu_image = ImageTk.PhotoImage(
            draw_icon(GREEN).resize((22, 22), Image.Resampling.LANCZOS)
        )
        self.logo_button = tk.Label(
            heading,
            image=self._logo_menu_image,
            bg=CARD,
            cursor="hand2",
            bd=0,
            highlightthickness=0,
        )
        self.logo_button.pack(side="left", padx=(0, 8))
        self.logo_button.bind("<Button-1>", self._popup_logo_menu)
        _label(heading, "Gordian Knot", CARD, TEXT, 15, bold=True).pack(side="left")
        self.date_label = _label(heading, "", CARD, MUTED, 10)
        self.date_label.pack(side="left", padx=(10, 0))
        _label(heading, "Current window", CARD, MUTED, 9).pack(side="left", padx=(16, 0))
        self.process_icon = tk.Label(heading, bg=CARD, bd=0, highlightthickness=0)
        self.process_label = _label(heading, "Looking...", CARD, TEXT, 11, bold=True)
        self.process_label.pack(side="left", padx=(6, 8))

        controls = tk.Frame(self.root, bg=BG)
        self._bg_widgets.append(controls)
        controls.pack(fill="x", padx=16, pady=(8, 0))
        self.pause_button = self._button(controls, "Pause", self.toggle_pause)
        self.pause_button.pack(side="left")
        self._button(controls, "Edit lists", self.open_config).pack(side="left", padx=(8, 0))
        self._button(controls, "Scan", self._open_scan).pack(side="left", padx=(8, 0))
        self.night_button = self._button(controls, "Night", self._toggle_night)
        self.night_button.pack(side="left", padx=(8, 0))
        self._button(controls, "Quit", self.shutdown).pack(side="right")

        squares = tk.Frame(self.root, bg=BG)
        self._bg_widgets.append(squares)
        squares.pack(fill="x", padx=16, pady=(8, 0))
        for column in range(3):
            squares.columnconfigure(column, weight=1, uniform="squares")
        status = self._card(squares)
        status.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        work = self._card(squares)
        work.grid(row=0, column=1, sticky="nsew", padx=6)
        free = self._card(squares)
        free.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        self.status_label = _label(status, "Starting", CARD, MUTED, 9, bold=True)
        self.status_label.pack(fill="x", padx=12, pady=(12, 0))
        self.stretch_label = _label(status, "0m 00s", CARD, TEXT, 18, bold=True)
        self.stretch_label.pack(fill="x", padx=12, pady=(4, 14))
        _label(work, "WORK", CARD, MUTED, 9, bold=True).pack(fill="x", padx=12, pady=(12, 0))
        self.work_value = _label(work, "0m 00s", CARD, TEXT, 18, bold=True)
        self.work_value.pack(fill="x", padx=12, pady=(4, 14))
        self.free_caption = _label(free, "FREE", CARD, MUTED, 9, bold=True)
        self.free_caption.pack(fill="x", padx=12, pady=(12, 0))
        self.free_value = _label(free, "0m 00s", CARD, TEXT, 18, bold=True)
        self.free_value.pack(fill="x", padx=12, pady=(4, 14))

        chart_wrap = tk.Frame(self.root, bg=BG)
        self._bg_widgets.append(chart_wrap)
        chart_wrap.pack(fill="x", padx=16, pady=(8, 0))
        chart_head = tk.Frame(chart_wrap, bg=BG)
        self._bg_widgets.append(chart_head)
        chart_head.pack(fill="x")
        self.plot_view = "series"
        self.plot_series = self._button(chart_head, "Time series", lambda: self._show_plot("series"))
        self.plot_series.pack(side="left")
        self.plot_map = self._button(chart_head, "Map", lambda: self._show_plot("map"))
        self.plot_map.pack(side="left", padx=(6, 12))
        self.chart_title = tk.Label(
            chart_head,
            text="",
            bg=BG,
            fg=MUTED,
            anchor="w",
            font=(DAY_FONT, 9),
        )
        self.chart_title.pack(side="left")
        self.ratio_label = tk.Label(
            chart_head,
            text="W:R",
            bg=BG,
            fg=TEXT,
            anchor="w",
            font=(DAY_FONT, 9),
        )
        self.ratio_label.pack(side="left", padx=(16, 6))
        self.ratio_chip = self._button(chart_head, self._ratio_caption(), self.change_ratio)
        self.ratio_chip.pack(side="left")
        self.series_tools = tk.Frame(chart_head, bg=BG)
        self._bg_widgets.append(self.series_tools)
        self.series_tools.pack(side="right")
        self.reload_button = self._button(chart_head, "Reload", self.reload_graph)
        self.reload_button.pack(side="right", padx=(0, 8))
        self._button(self.series_tools, "Fit", lambda: self.chart.fit()).pack(side="right")
        self.legend_button = self._button(self.series_tools, "Hide legend", self._toggle_legend)
        self.legend_button.pack(side="right", padx=(0, 8))
        self._button(self.series_tools, "−", lambda: self.chart.zoom_by(1.25)).pack(side="right", padx=(0, 8))
        self._button(self.series_tools, "+", lambda: self.chart.zoom_by(0.8)).pack(side="right", padx=(0, 8))
        span_row = tk.Frame(chart_wrap, bg=BG)
        self._bg_widgets.append(span_row)
        span_row.pack(fill="x", pady=(6, 0))
        tk.Label(span_row, text="From", bg=BG, fg=TEXT, font=(DAY_FONT, 9)).pack(side="left")
        self.span_from = tk.Entry(
            span_row, width=14, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="sunken", bd=1, font=(FONT, 9)
        )
        self.span_from.pack(side="left", padx=(6, 0), ipady=1)
        tk.Label(span_row, text="to", bg=BG, fg=TEXT, font=(DAY_FONT, 9)).pack(side="left", padx=(8, 0))
        self.span_to = tk.Entry(
            span_row, width=14, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="sunken", bd=1, font=(FONT, 9)
        )
        self.span_to.pack(side="left", padx=(6, 0), ipady=1)
        self._button(span_row, "Show", self._apply_span).pack(side="left", padx=(8, 0))
        self._button(span_row, "All", self._clear_span).pack(side="left", padx=(6, 0))
        self.span_from.bind("<Return>", lambda _event: self._apply_span())
        self.span_to.bind("<Return>", lambda _event: self._apply_span())
        self.span_window = None
        chart_card = self._card(chart_wrap)
        chart_card.pack(fill="x", pady=(6, 0))
        self.chart = CumulativeChart(chart_card, GREEN, "#7851a9", PAUSE, IDLE, "#8b1a1a", "#8d8d8d", TEXT, MUTED, "#d0d0d0")
        self.chart.pack(fill="x", padx=8, pady=(4, 8))
        self.usage = UsageMap(chart_card, TEXT, MUTED, CARD)
        self._style_plots()

        activity = tk.Frame(self.root, bg=BG)
        self._bg_widgets.append(activity)
        activity.pack(fill="both", expand=True, padx=16, pady=(8, 0))
        tabs = tk.Frame(activity, bg=BG)
        self._bg_widgets.append(tabs)
        tabs.pack(fill="x")
        self.apps_tab = self._tab(tabs, "Apps", lambda: self._show_activity("apps"))
        self.feed_tab = self._tab(tabs, "Activity", lambda: self._show_activity("feed"))
        self.schedule_tab = self._tab(tabs, "Schedule", lambda: self._show_activity("schedule"))
        self.milestone_tab = self._tab(tabs, "Milestone", lambda: self._show_activity("milestone"))
        self.apps_tab.pack(side="left")
        self.feed_tab.pack(side="left", padx=(6, 0))
        self.schedule_tab.pack(side="left", padx=(6, 0))
        self.milestone_tab.pack(side="left", padx=(6, 0))
        log_card = self._card(activity)
        log_card.pack(fill="both", expand=True, pady=(6, 0))
        self.empty_log = _label(
            log_card,
            "When you leave a program, that stretch shows up here.",
            CARD,
            MUTED,
            10,
            wrap=460,
        )
        self.log = tk.Listbox(
            log_card,
            bg=CARD,
            fg=TEXT,
            font=("Consolas", 10),
            bd=0,
            highlightthickness=0,
            activestyle="none",
            selectbackground="#0a246a",
            selectforeground="#ffffff",
            height=5,
        )
        self.scroll = tk.Scrollbar(log_card, command=self.log.yview)
        self.log.configure(yscrollcommand=self.scroll.set)
        self.log.bind("<MouseWheel>", lambda event: self.log.yview_scroll(int(-event.delta / 120), "units"))
        self.app_bars = AppBars(log_card, "#3a6ea5", TEXT, MUTED, CARD)
        self.schedule_frame = tk.Frame(log_card, bg=CARD)
        self.week = WeekSchedule(
            self.schedule_frame,
            self._schedule_create,
            self._schedule_edit,
            self._schedule_resize,
            CARD,
            TEXT,
            MUTED,
            LINE,
            "#3a6ea5",
            "#8b2e2e",
            GREEN,
        )
        self.week.pack(fill="both", expand=True)
        self._build_milestones(log_card)
        self._set_log([])
        self._style_tabs()

        self.notice = _label(self.root, "", BG, MUTED, 9, wrap=640)
        self._bg_widgets.append(self.notice)
        self._apply_saved_colors()
        self._apply_saved_fonts()
        if self.night:
            self._apply_theme(True)

        self.root.update_idletasks()
        width, height = 720, 860
        screen_height = self.root.winfo_screenheight()
        if height > screen_height - 80:
            height = max(640, screen_height - 80)
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (screen_height - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(640, 700)

    def _card(self, parent) -> tk.Frame:
        return tk.Frame(parent, bg=CARD, relief="sunken", bd=1, highlightthickness=0)

    def _tab(self, parent, text: str, command) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=TEXT,
            relief="raised",
            bd=2,
            padx=10,
            pady=2,
            font=(FONT, 8),
            cursor="hand2",
            highlightthickness=0,
        )

    def _style_tabs(self) -> None:
        tabs = (
            ("apps", self.apps_tab),
            ("feed", self.feed_tab),
            ("schedule", self.schedule_tab),
            ("milestone", self.milestone_tab),
        )
        for name, tab in tabs:
            active = self.activity_view == name
            tab.configure(
                bg=self._chosen(tab, "bg", CARD if active else BG),
                fg=self._chosen(tab, "fg", TEXT),
                relief="sunken" if active else "raised",
                font=self._saved_font(tab) or (FONT, 8, "bold" if active else "normal"),
            )

    def _style_plots(self) -> None:
        for name, button in (("series", self.plot_series), ("map", self.plot_map)):
            active = self.plot_view == name
            button.configure(
                bg=self._chosen(button, "bg", CARD if active else BG),
                fg=self._chosen(button, "fg", TEXT),
                relief="sunken" if active else "raised",
                font=self._saved_font(button) or (FONT, 8, "bold" if active else "normal"),
            )

    def _show_plot(self, view: str) -> None:
        if view == self.plot_view:
            return
        self.plot_view = view
        self._style_plots()
        if view == "map":
            self.chart.pack_forget()
            self.series_tools.pack_forget()
            self.usage.pack(fill="x", padx=8, pady=(4, 8))
        else:
            self.usage.pack_forget()
            self.chart.pack(fill="x", padx=8, pady=(4, 8))
            self.reload_button.pack_forget()
            self.series_tools.pack(side="right")
            self.reload_button.pack(side="right", padx=(0, 8))
        self._refresh_view()

    def _show_activity(self, view: str) -> None:
        self.activity_view = view
        self._style_tabs()
        self.app_bars.pack_forget()
        self.empty_log.pack_forget()
        self.log.pack_forget()
        self.scroll.pack_forget()
        self.schedule_frame.pack_forget()
        self.milestone_frame.pack_forget()
        if view == "apps":
            self.app_bars.pack(fill="both", expand=True, padx=8, pady=8)
            self._refresh_view()
            return
        if view == "schedule":
            self.schedule_frame.pack(fill="both", expand=True)
            self._fill_schedule()
            return
        if view == "milestone":
            self.milestone_frame.pack(fill="both", expand=True)
            self._fill_milestones()
            return
        self._refresh_view()
        self._pack_feed()

    def _button(self, parent, text: str, command) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=BG,
            fg=TEXT,
            activebackground="#e5e5e5",
            activeforeground=TEXT,
            relief="raised",
            bd=2,
            padx=8,
            pady=1,
            font=(FONT, 8),
            cursor="hand2",
            highlightthickness=0,
        )

    def _ratio_caption(self) -> str:
        block = self.active_block
        if block and block.locked:
            return "Locked"
        raw = block.ratio if block else format_ratio(self.settings.work_minutes, self.settings.reward_minutes)
        return raw.replace(":", " : ")

    def _chart_caption(self) -> str:
        span = covered_range(self.chart.points, self.chart.view_start, self.chart.view_end)
        if span is None:
            return "Today"
        try:
            start = datetime.fromtimestamp(span[0])
            end = datetime.fromtimestamp(span[1])
        except (OSError, OverflowError, ValueError):
            return "Today"
        if (
            end.hour == 0
            and end.minute == 0
            and end.second == 0
            and end.date() > start.date()
        ):
            end = end - timedelta(seconds=1)
        if start.date() == end.date():
            shown = f"{start.strftime('%A, %B')} {start.day}"
        elif start.year == end.year:
            shown = f"{start.strftime('%b')} {start.day} – {end.strftime('%b')} {end.day}"
        else:
            shown = f"{start.strftime('%b')} {start.day}, {start.year} – {end.strftime('%b')} {end.day}, {end.year}"
        return shown

    def _apply_span(self) -> None:
        year = datetime.now().year
        start = parse_day(self.span_from.get(), year)
        end = parse_day(self.span_to.get(), year)
        if start is None or end is None or end < start:
            self._show_note("Use days like 6 Aug and 17 Aug.")
            return
        start_at = datetime(start.year, start.month, start.day)
        end_at = datetime(end.year, end.month, end.day) + timedelta(days=1)
        self.span_window = (start_at, end_at)
        self.chart.set_window(start_at.timestamp(), end_at.timestamp())
        self.chart_title.configure(text=self._chart_caption())
        self._show_note(
            f"{start.strftime('%b')} {start.day} – {end.strftime('%b')} {end.day}. "
            "Each day is separated, and the scale starts at zero for that stretch."
        )

    def _clear_span(self) -> None:
        self.span_window = None
        self.span_from.delete(0, "end")
        self.span_to.delete(0, "end")
        self.chart.set_window(None, None)
        self.chart_title.configure(text=self._chart_caption())
        self._show_note("Showing the whole stretch again.")

    def _toggle_night(self) -> None:
        self.night = not self.night
        self._apply_theme(self.night)
        try:
            write_night(self.config_path, self.night)
            self.config_mtime = self.config_path.stat().st_mtime
        except (OSError, ValueError) as exc:
            self._show_note(f"Could not save night mode ({exc}).")

    def _apply_theme(self, night: bool) -> None:
        global BG, CARD, TEXT, MUTED, LINE, GREEN, AMBER, RED, IDLE, PAUSE
        palette = NIGHT if night else {
            "bg": "#f0f0f0",
            "card": "#ffffff",
            "text": "#000000",
            "muted": "#404040",
            "line": "#a0a0a0",
            "green": "#008000",
            "amber": "#9a5b00",
            "red": "#a40000",
            "idle": "#555555",
            "pause": "#8a6d00",
            "grid": "#d0d0d0",
            "sleep": "#8d8d8d",
            "shutdown": "#8b1a1a",
            "bar": "#3a6ea5",
            "active": "#e5e5e5",
        }
        previous = {
            "bg": BG,
            "card": CARD,
            "text": TEXT,
            "muted": MUTED,
            "line": LINE,
            "green": GREEN,
            "amber": AMBER,
            "red": RED,
            "idle": IDLE,
            "pause": PAUSE,
        }
        BG, CARD, TEXT, MUTED = palette["bg"], palette["card"], palette["text"], palette["muted"]
        LINE, GREEN, AMBER, RED = palette["line"], palette["green"], palette["amber"], palette["red"]
        IDLE, PAUSE = palette["idle"], palette["pause"]
        KIND_COLORS.update(
            {
                "earn": GREEN,
                "spend": AMBER,
                "idle": IDLE,
                "neutral": MUTED,
                "paused": PAUSE,
                "locked": RED,
            }
        )
        mapping = {}
        for key in ("bg", "card", "text", "muted", "line", "green", "amber", "red", "idle", "pause"):
            mapping[previous[key].lower()] = palette[key]
            other = NIGHT[key] if not night else {
                "bg": "#f0f0f0",
                "card": "#ffffff",
                "text": "#000000",
                "muted": "#404040",
                "line": "#a0a0a0",
                "green": "#008000",
                "amber": "#9a5b00",
                "red": "#a40000",
                "idle": "#555555",
                "pause": "#8a6d00",
            }[key]
            mapping[other.lower()] = palette[key]
        mapping["#e5e5e5"] = palette["active"]
        mapping["#3a4458"] = palette["active"]
        mapping["#d0d0d0"] = palette["grid"]
        mapping["#2c3340"] = palette["line"]
        mapping["#8d8d8d"] = palette["sleep"]
        mapping["#6a7384"] = palette["sleep"]
        mapping["#8b1a1a"] = palette["shutdown"]
        mapping["#6e2b32"] = palette["shutdown"]
        mapping["#3a6ea5"] = palette["bar"]
        mapping["#6f8fbf"] = palette["bar"]

        def walk(widget) -> None:
            for option in ("bg", "fg", "activebackground", "activeforeground", "insertbackground", "highlightbackground", "highlightcolor"):
                try:
                    current = str(widget.cget(option))
                except tk.TclError:
                    continue
                mapped = mapping.get(current.lower())
                if mapped and mapped.lower() != current.lower():
                    try:
                        widget.configure(**{option: mapped})
                    except tk.TclError:
                        continue
            for child in widget.winfo_children():
                walk(child)

        walk(self.root)
        self.chart.colors["productive"] = GREEN
        self.chart.colors["paused"] = PAUSE
        self.chart.colors["not_counting"] = IDLE
        self.chart.text_color = TEXT
        self.chart.muted_color = MUTED
        self.chart.grid_color = palette["grid"]
        self.chart.plot_bg = CARD
        self.chart.sleep_color = palette["sleep"]
        self.chart.shutdown_color = palette["shutdown"]
        self.chart.configure(bg=CARD)
        self.app_bars.bar_color = palette["bar"]
        self.app_bars.text_color = TEXT
        self.app_bars.muted_color = MUTED
        self.app_bars.canvas_bg = CARD
        self.app_bars.configure(bg=CARD)
        self.usage.text_color = TEXT
        self.usage.muted_color = MUTED
        self.usage.canvas_bg = CARD
        self.usage.pause_color = PAUSE
        self.usage.configure(bg=CARD)
        self.week.bg = CARD
        self.week.fg = TEXT
        self.week.muted = MUTED
        self.week.line = LINE
        self.week.block_color = palette["bar"]
        self.week.preview_color = GREEN
        self.week.configure(bg=CARD)
        self.week.header.configure(bg=CARD)
        self.week.body.configure(bg=CARD)
        self.week._draw()
        self.night_button.configure(text="Day" if night else "Night")
        self._paint_title_bar()
        self._apply_saved_colors()
        self.chart._draw()
        self.app_bars._draw()
        self.usage._draw()
        self._style_plots()

    def _build_milestones(self, parent) -> None:
        self.milestone_frame = tk.Frame(parent, bg=CARD)
        add_row = tk.Frame(self.milestone_frame, bg=CARD)
        add_row.pack(fill="x", padx=8, pady=(8, 4))
        self.milestone_name = tk.Entry(
            add_row, bg=BG, fg=TEXT, insertbackground=TEXT, relief="sunken", bd=1, font=(FONT, 9)
        )
        self.milestone_name.pack(side="left", fill="x", expand=True, ipady=2)
        self.milestone_bonus = tk.Entry(
            add_row, width=8, bg=BG, fg=TEXT, insertbackground=TEXT, relief="sunken", bd=1, font=(FONT, 9)
        )
        self.milestone_bonus.pack(side="left", padx=(6, 0), ipady=2)
        self._button(add_row, "Add", self._add_milestone).pack(side="left", padx=(6, 0))
        stack = tk.Frame(self.milestone_frame, bg=CARD)
        stack.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        stack.rowconfigure(0, weight=1)
        stack.rowconfigure(1, weight=1)
        stack.columnconfigure(0, weight=1)
        self.open_box = tk.LabelFrame(
            stack, text="Uncomplete", bg=CARD, fg=TEXT, font=(FONT, 9, "bold"), labelanchor="nw"
        )
        self.done_box = tk.LabelFrame(
            stack, text="Completed", bg=CARD, fg=TEXT, font=(FONT, 9, "bold"), labelanchor="nw"
        )
        self.open_box.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        self.done_box.grid(row=1, column=0, sticky="nsew")
        self.open_rows = self._milestone_pane(self.open_box)
        self.done_rows = self._milestone_pane(self.done_box)

    def _milestone_pane(self, parent) -> tk.Frame:
        canvas = tk.Canvas(parent, bg=CARD, highlightthickness=0, bd=0)
        bar = tk.Scrollbar(parent, command=canvas.yview)
        inner = tk.Frame(canvas, bg=CARD)
        canvas.create_window((0, 0), window=inner, anchor="nw", tags="body")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=4)

        def fit(event) -> None:
            canvas.itemconfigure("body", width=event.width)
            region()

        def region(_event=None) -> None:
            bbox = canvas.bbox("all")
            canvas.configure(scrollregion=bbox if bbox else (0, 0, 0, 0))
            content = (bbox[3] - bbox[1]) if bbox else 0
            visible = canvas.winfo_height()
            needed = visible > 1 and content > visible + 2
            if needed and not bar.winfo_ismapped():
                bar.pack(side="right", fill="y", padx=(0, 4), pady=4)
            elif not needed and bar.winfo_ismapped():
                bar.pack_forget()
                canvas.yview_moveto(0)

        def wheel(event) -> None:
            canvas.yview_scroll(int(-event.delta / 120), "units")

        inner.bind("<Configure>", region)
        canvas.bind("<Configure>", fit)
        canvas.bind("<MouseWheel>", wheel)
        inner.bind("<MouseWheel>", wheel)
        inner._wheel = wheel
        return inner

    def _fill_milestones(self) -> None:
        for pane in (self.open_rows, self.done_rows):
            for child in pane.winfo_children():
                child.destroy()
        items = tuple(self.settings.milestones)
        open_count = 0
        done_count = 0
        for index, item in enumerate(items):
            if item.done:
                self._milestone_row(self.done_rows, item, None)
                done_count += 1
            else:
                self._milestone_row(self.open_rows, item, index)
                open_count += 1
        if open_count == 0:
            self._milestone_empty(self.open_rows, "No tasks yet.")
        if done_count == 0:
            self._milestone_empty(self.done_rows, "None finished yet.")

    def _milestone_empty(self, pane, text: str) -> None:
        label = tk.Label(pane, text=text, bg=CARD, fg=MUTED, anchor="w", font=(FONT, 9))
        label.pack(fill="x", padx=4, pady=4)
        label.bind("<MouseWheel>", pane._wheel)

    def _milestone_row(self, pane, item: Milestone, index: int | None) -> None:
        row = tk.Frame(pane, bg=CARD)
        row.pack(fill="x", padx=2, pady=1)
        label = tk.Label(
            row,
            text=f"{item.title}    {plain_number(item.bonus_minutes)}m",
            bg=CARD,
            fg=TEXT,
            anchor="w",
            font=(FONT, 9),
        )
        label.pack(side="left", fill="x", expand=True)
        if index is not None:
            variable = tk.IntVar(value=0)
            tick = tk.Checkbutton(
                row,
                variable=variable,
                command=lambda index=index, variable=variable: self._tick_milestone(index, variable),
                bg=CARD,
                activebackground=CARD,
                selectcolor="#f0f0f0",
                highlightthickness=0,
            )
            tick.pack(side="right")
        for widget in row.winfo_children():
            widget.bind("<MouseWheel>", pane._wheel)
        row.bind("<MouseWheel>", pane._wheel)

    def _add_milestone(self) -> None:
        title = " ".join(self.milestone_name.get().split())
        raw = self.milestone_bonus.get().strip()
        try:
            minutes = float(raw)
        except ValueError:
            minutes = 0
        if not title or minutes <= 0:
            self._show_note("Enter a task and its bonus minutes.")
            return
        items = list(self.settings.milestones) + [Milestone(title[:120], minutes, False)]
        self._store_milestones(items)
        self.milestone_name.delete(0, "end")
        self.milestone_bonus.delete(0, "end")
        self._show_note(f"Added {title}. Mark it done to add {plain_number(minutes)} minutes.")

    def _tick_milestone(self, index: int, variable: tk.IntVar) -> None:
        if not variable.get():
            return
        items = list(self.settings.milestones)
        if index >= len(items) or items[index].done:
            self._fill_milestones()
            return
        item = items[index]
        if not self.unlocked:
            result = self._prompt(
                "Mark done",
                f"Mark {item.title} done and add {plain_number(item.bonus_minutes)} minutes of free time.",
                [("password", "Password")],
                secret={"password"},
            )
            if result is None or not password_accepted(False, result["password"], self.settings.reset_password):
                variable.set(0)
                if result is not None:
                    self._show_note("That password did not match.")
                return
        current = items[index]
        items[index] = Milestone(current.title, current.bonus_minutes, True)
        self.ledger.add_bonus(current.bonus_minutes * 60)
        try:
            self.ledger.save()
        except Exception:
            log_exception("milestone bonus")
        self._store_milestones(items)
        self._show_note(f"{current.title} added {plain_number(current.bonus_minutes)} minutes.")

    def _store_milestones(self, items: list[Milestone]) -> None:
        try:
            write_milestones(self.config_path, items)
            self.config_mtime = self.config_path.stat().st_mtime
        except (OSError, ValueError) as exc:
            self._show_note(f"Could not save the tasks ({exc}).")
            return
        self.settings.milestones = tuple(items)
        if self.activity_view == "milestone":
            self._fill_milestones()

    def _open_scan(self) -> None:
        box, frame, surfaces = self._window("Scan programs")
        color = self._background()
        intro = _label(
            frame,
            "Every program found on this computer is listed, and browsers are listed as the websites inside them. The filled button is the choice that will be saved.",
            color,
            TEXT,
            9,
            wrap=680,
        )
        intro.pack(anchor="w")
        surfaces.append(intro)
        status = _label(frame, "Scanning every drive for programs and websites.", color, MUTED, 9)
        status.pack(anchor="w", pady=(8, 0))
        surfaces.append(status)
        box.bind("<Escape>", lambda _event: box.destroy())
        self._place_window(box, 760)
        holder: dict = {"rows": None, "icons": {}, "error": ""}

        def work() -> None:
            try:
                rows, icons = scan_computer()
                holder["rows"] = rows
                holder["icons"] = icons
            except Exception as exc:
                log_exception("scan")
                holder["error"] = str(exc)

        def poll() -> None:
            try:
                if not box.winfo_exists():
                    return
            except tk.TclError:
                return
            if holder["rows"] is None and not holder["error"]:
                box.after(200, poll)
                return
            self._fill_scan(
                box, frame, surfaces, status, holder["rows"] or [], holder["icons"], holder["error"], color
            )

        threading.Thread(target=work, name="gordian-scan", daemon=True).start()
        box.after(200, poll)
        box.focus_force()
        self.root.wait_window(box)

    def _fill_scan(self, box, frame, surfaces, status, found, icons: dict, error: str, color: str) -> None:
        if error:
            status.configure(text=f"The scan could not finish ({error}).")
            return
        programs = [item for item in found if item.kind != "site"]
        sites = [item for item in found if item.kind == "site"]
        status.configure(
            text=f"{len(programs)} programs and {len(sites)} websites. The filled button is what gets saved."
        )
        canvas = tk.Canvas(frame, bg=color, highlightthickness=0, height=420, bd=0)
        scroll = tk.Scrollbar(frame, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y", pady=(8, 0))
        canvas.pack(fill="both", expand=True, pady=(8, 0))
        inner = tk.Frame(canvas, bg=color)
        surfaces.extend((canvas, inner))
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))

        def wheel(event) -> None:
            canvas.yview_scroll(int(-event.delta / 120), "units")

        canvas.bind("<MouseWheel>", wheel)
        inner.bind("<MouseWheel>", wheel)
        photos: list = []
        box._scan_photos = photos
        blank = ImageTk.PhotoImage(Image.new("RGBA", (16, 16), (0, 0, 0, 0)))
        photos.append(blank)
        choices: list = []

        def add_heading(text: str) -> None:
            heading = _label(inner, text, color, TEXT, 9)
            heading.configure(font=(FONT, 9, "bold"))
            heading.pack(anchor="w", pady=(10, 2))
            surfaces.append(heading)

        if programs:
            add_heading("Programs")
        for program in programs:
            self._scan_row(inner, surfaces, program, color, blank, photos, icons, choices, wheel)
        if sites:
            add_heading("Websites")
        for program in sites:
            self._scan_row(inner, surfaces, program, color, blank, photos, icons, choices, wheel)

        def lock() -> None:
            programs_marked = [(item.executable, variable.get()) for item, variable in choices if item.kind != "site"]
            sites_marked = [(item.executable, variable.get()) for item, variable in choices if item.kind == "site"]
            if not self.unlocked:
                result = self._prompt(
                    "Lock in",
                    "Save these programs and websites. A filled Work button counts as work. A filled Spend button spends free time.",
                    [("password", "Password")],
                    secret={"password"},
                )
                if result is None:
                    return
                if not password_accepted(False, result["password"], self.settings.reset_password):
                    self._show_note("That password did not match.")
                    return
            productive, reward = lock_choices(
                self.settings.productive_processes,
                self.settings.reward_processes,
                programs_marked,
            )
            productive_sites, reward_sites = lock_sites(
                self.settings.productive_title_keywords,
                self.settings.reward_title_keywords,
                sites_marked,
            )
            if lockdown_active(self.settings.schedule, datetime.now()) and _new_unproductive(
                programs_marked,
                sites_marked,
                self.settings.reward_processes,
                self.settings.reward_title_keywords,
            ):
                self._show_note(
                    "Lockdown is on for this part of the schedule. Unproductive programs cannot be added."
                )
                return
            try:
                write_scan_lists(self.config_path, productive, reward, productive_sites, reward_sites)
                self.config_mtime = self.config_path.stat().st_mtime
            except (OSError, ValueError) as exc:
                self._show_note(f"Could not save the lists ({exc}).")
                return
            self.settings.productive_processes = productive
            self.settings.reward_processes = reward
            self.settings.productive_title_keywords = productive_sites
            self.settings.reward_title_keywords = reward_sites
            box.destroy()
            self._show_note("Those programs and websites are saved.")

        buttons = tk.Frame(frame, bg=color)
        buttons.pack(fill="x", pady=(10, 0))
        surfaces.append(buttons)
        self._dialog_button(buttons, "Lock in", lock).pack(side="right")
        self._dialog_button(buttons, "Cancel", box.destroy).pack(side="right", padx=(0, 8))

    def _scan_row(self, inner, surfaces, program, color, blank, photos, icons, choices, wheel) -> None:
        row = tk.Frame(inner, bg=color)
        row.pack(fill="x", pady=3)
        surfaces.append(row)
        row.grid_columnconfigure(1, weight=1)
        image = icons.get(program.icon) if program.icon else None
        if image is not None:
            image = image.resize((16, 16), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            photos.append(photo)
        else:
            photo = blank
        icon = tk.Label(row, image=photo, bg=color, width=16, height=16)
        icon.grid(row=0, column=0, rowspan=2, padx=(0, 8), sticky="w")
        surfaces.append(icon)
        shown = program.name if len(program.name) <= 42 else program.name[:41] + "..."
        name = _label(row, shown, color, TEXT, 9)
        name.grid(row=0, column=1, sticky="w")
        detail = program.executable if program.kind == "site" else f"{program.reason}  ·  {program.executable}"
        meta = _label(row, detail, color, MUTED, 8)
        meta.grid(row=1, column=1, sticky="w")
        surfaces.extend((name, meta))
        initial = starting_choice(
            program,
            self.settings.productive_processes,
            self.settings.reward_processes,
            self.settings.productive_title_keywords,
            self.settings.reward_title_keywords,
        )
        variable = tk.StringVar(value=initial)
        choices.append((program, variable))
        caption = _label(row, "", color, MUTED, 8)
        caption.grid(row=1, column=2, columnspan=2, sticky="e", padx=(8, 0))
        surfaces.append(caption)
        work = tk.Button(row, text="Work", font=(FONT, 8), bd=2, padx=8, pady=1, cursor="hand2")
        spend = tk.Button(row, text="Spend", font=(FONT, 8), bd=2, padx=8, pady=1, cursor="hand2")

        def paint() -> None:
            if variable.get() == "productive":
                work.configure(bg=GREEN, fg="#ffffff", relief="sunken", font=(FONT, 8, "bold"))
                spend.configure(bg=color, fg=MUTED, relief="raised", font=(FONT, 8))
                caption.configure(text="Will count as work", fg=GREEN)
            else:
                spend.configure(bg="#7851a9", fg="#ffffff", relief="sunken", font=(FONT, 8, "bold"))
                work.configure(bg=color, fg=MUTED, relief="raised", font=(FONT, 8))
                caption.configure(text="Will spend free time", fg="#7851a9")

        def choose(value: str) -> None:
            variable.set(value)
            paint()

        work.configure(command=lambda: choose("productive"))
        spend.configure(command=lambda: choose("unproductive"))
        work.grid(row=0, column=2, padx=(12, 4), sticky="e")
        spend.grid(row=0, column=3, sticky="e")
        paint()
        for widget in (row, icon, name, meta, caption, work, spend):
            widget.bind("<MouseWheel>", wheel)

    def _start_tray(self) -> None:
        try:
            self.icon = pystray.Icon("gordian-knot", draw_icon(MUTED), "Gordian Knot", self._menu())
            runner = getattr(self.icon, "run_detached", None)
            if runner is None:
                threading.Thread(target=self.icon.run, daemon=True).start()
            else:
                runner()
        except Exception:
            log_exception("tray")
            self.icon = None
            self._set_notice("The tray icon did not start, so closing this window quits.", RED)
            return

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem(self._safe_label(lambda: self.menu_status), lambda *_args: None, enabled=False),
            pystray.MenuItem(self._safe_label(lambda: self.menu_work), lambda *_args: None, enabled=False),
            pystray.MenuItem(self._safe_label(lambda: self.menu_free), lambda *_args: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Show", self.request_show, default=True),
            pystray.MenuItem("About", self.request_about),
            pystray.MenuItem(self._safe_label(lambda: "Resume" if self.paused else "Pause"), self.request_toggle),
            pystray.MenuItem("Edit lists", self.request_config),
            pystray.MenuItem("Reset time", self.request_reset),
            pystray.MenuItem("Clear history", self.request_clear_history),
            pystray.MenuItem("Change password", self.request_password),
            pystray.MenuItem(self._safe_label(lambda: "Lock" if self.unlocked else "Unlock"), self.request_unlock),
            pystray.MenuItem("Open data folder", self.request_data),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.request_shutdown),
        )

    def _safe_label(self, text_fn):
        def inner(*_args, **_kwargs):
            try:
                return text_fn()
            except Exception:
                return "Gordian Knot"
        return inner

    def _pump_logo_menu(self) -> None:
        command = getattr(self.root, "_pending_logo_command", None)
        if command:
            self.root._pending_logo_command = None
            try:
                self._run_logo_command(int(command))
            except Exception:
                log_exception("logo menu")
        if self.running:
            try:
                self.root.after(50, self._pump_logo_menu)
            except tk.TclError:
                return

    def tick(self) -> None:
        try:
            self._tick()
        except Exception:
            log_exception("tick")
            self._set_notice(
                f"The watcher hit an error and will keep trying. Details: {self.data_path / 'error.log'}",
                RED,
            )
        finally:
            if self.running:
                try:
                    self.root.after(self._interval_ms(), self.tick)
                except tk.TclError:
                    return

    def _tick(self) -> None:
        # Sample, classify, apply the schedule, bank the seconds, then warn or close.
        self._reload_config()
        now_mono = time.monotonic()
        wall = datetime.now()
        if self.last_wall is not None:
            wall_gap = (wall - self.last_wall).total_seconds()
            gap_kind = wall_gap_kind(wall_gap, now_mono - self.last_monotonic)
            if gap_kind == "clock":
                self.ledger.note_clock(self.last_wall + timedelta(seconds=max(0.0, now_mono - self.last_monotonic)), wall)
                self.ledger.save()
                self.last_save = now_mono
            elif gap_kind == "lost":
                self.ledger.note_lost(wall, -wall_gap)
                self.ledger.save()
                self.last_save = now_mono
            elif gap_kind == "sleep":
                self.ledger.note_away(self.last_wall, wall, "sleep")
                self.ledger.save()
                self.last_save = now_mono
        self.last_wall = wall
        delta = now_mono - self.last_monotonic
        self.last_monotonic = now_mono
        sample = get_sample()
        decision = resolve(sample, self.settings, self.memory, self.paused)
        self._follow_schedule(wall)
        decision = self._enforce_lock(sample, decision)
        pause_change = self._steer_pause(sample, wall)
        if pause_change == "resume":
            delta = 0.0
            decision = resolve(sample, self.settings, self.memory, False)
            decision = self._enforce_lock(sample, decision)
        elif pause_change == "repause":
            delta = 0.0
            decision = resolve(sample, self.settings, self.memory, True)
            decision = self._enforce_lock(sample, decision)
        max_delta = max(5.0, self.settings.poll_seconds * 3)
        boundary = self.ledger.update(
            wall,
            decision.kind,
            decision.process,
            decision.title,
            delta,
            max_delta,
            segment_identity(decision.process, decision.title, self.settings),
        )
        if boundary or now_mono - self.last_save >= 2:
            self.ledger.save_soon()
            self.last_save = now_mono
        self._consider_warning(decision, self.ledger.remaining_seconds, sample)
        self._consider_ask(sample)
        self._last_render = (sample, decision)
        self._render(sample, decision)

    def _view_visible(self) -> bool:
        try:
            return self.root.state() in ("normal", "zoomed")
        except tk.TclError:
            return False

    def _toggle_legend(self) -> None:
        self.chart.legend_shown = not self.chart.legend_shown
        self.legend_button.configure(text="Show legend" if not self.chart.legend_shown else "Hide legend")
        self.chart._drawn_key = None
        self.chart._draw()

    def reload_graph(self) -> None:
        forget_saved_segments()
        self._history_segments = []
        self.chart._drawn_key = None
        self._load_history_soon()
        self._show_note("The graph was reloaded.")

    def _refresh_view(self) -> None:
        if self._last_render is None or not self.running:
            return
        try:
            self._render(*self._last_render)
        except Exception:
            log_exception("render")

    def _on_root_map(self, event) -> None:
        if event.widget is self.root:
            self.root.after_idle(self._refresh_view)
            self.root.after_idle(self._paint_title_bar)

    def _save_quietly(self) -> None:
        try:
            self.ledger.save()
        except Exception:
            log_exception("shutdown save")

    def _consider_ask(self, sample) -> None:
        if self.paused:
            self._hide_ask()
            return
        process = getattr(sample, "process", "") or ""
        title = getattr(sample, "title", "") or ""
        url = getattr(sample, "url", "") or ""
        if process.casefold() in {item.casefold() for item in self.settings.browser_processes}:
            self._address_gate, url = loaded_address(self._address_gate, title, url)
        else:
            self._address_gate = AddressGate()
        prompt = consider(
            process,
            title,
            self.settings,
            {item.casefold() for item in self._ask_skip},
            path=getattr(sample, "path", "") or "",
            fullscreen=bool(getattr(sample, "fullscreen", False)),
            url=url,
        )
        if prompt is None:
            if self._ask_open() and (getattr(sample, "process", "") or "").casefold() in {"python.exe", "pythonw.exe"}:
                return
            if process.casefold() not in {item.casefold() for item in self.settings.browser_processes}:
                self._ask_later.clear()
            self._hide_ask()
            return
        if prompt.mode == "auto":
            self._hide_ask()
            if prompt.key.casefold() in self._ask_later:
                return
            self._answer_ask(prompt, prompt.choice)
            return
        if prompt.key.casefold() in self._ask_later:
            return
        self._show_ask(prompt)

    def _answer_ask(self, prompt, choice: str) -> None:
        if choice == "later":
            self._ask_later.add(prompt.key.casefold())
            self._hide_ask()
            return
        if prompt.mode == "tutorial" and choice == "unproductive":
            self._remember_skip(prompt.key)
            self._hide_ask()
            self._show_note(f"{prompt.label} stays unproductive. Other videos are unchanged.")
            return
        try:
            self._save_ask(prompt, choice)
        except (OSError, ValueError) as exc:
            self._ask_later.add(prompt.key.casefold())
            self._hide_ask()
            self._show_note(f"Could not save that choice ({exc}).")
            return
        self._hide_ask()
        if choice == "productive":
            detail = "This video counts as work. Other YouTube still spends." if prompt.mode == "tutorial" else f"{prompt.label} counts as work."
        else:
            detail = f"{prompt.label} spends free time."
        self._show_note(detail)

    def _save_ask(self, prompt, choice: str) -> None:
        if prompt.target == "app":
            productive, reward = lock_choices(
                self.settings.productive_processes,
                self.settings.reward_processes,
                [(prompt.key, choice)],
            )
            sites = self.settings.productive_title_keywords
            reward_sites = self.settings.reward_title_keywords
            self.settings.productive_processes = productive
            self.settings.reward_processes = reward
        else:
            productive = self.settings.productive_processes
            reward = self.settings.reward_processes
            sites, reward_sites = pin_keyword(
                self.settings.productive_title_keywords,
                self.settings.reward_title_keywords,
                prompt.key,
                choice,
            )
            self.settings.productive_title_keywords = sites
            self.settings.reward_title_keywords = reward_sites
        write_scan_lists(self.config_path, productive, reward, sites, reward_sites)
        self.config_mtime = self.config_path.stat().st_mtime

    def _remember_skip(self, key: str) -> None:
        folded = key.casefold()
        if any(item.casefold() == folded for item in self._ask_skip):
            return
        self._ask_skip.append(key)
        try:
            write_ask_skip(self.config_path, self._ask_skip)
            self.config_mtime = self.config_path.stat().st_mtime
        except (OSError, ValueError) as exc:
            self._show_note(f"Could not remember that video ({exc}).")

    def _show_ask(self, prompt) -> None:
        if self._ask_key == prompt.key and self._ask_open():
            return
        self._hide_ask()
        self._ask_key = prompt.key
        self._build_ask(prompt)

    def _ask_open(self) -> bool:
        box = self._ask_box
        if box is None:
            return False
        try:
            return bool(box.winfo_exists())
        except tk.TclError:
            return False

    def _hide_ask(self) -> None:
        box = self._ask_box
        self._ask_box = None
        self._ask_key = ""
        if box is None:
            return
        try:
            box.after_idle(box.destroy)
        except tk.TclError:
            return

    def _show_header_logo(self, sample) -> None:
        url = getattr(sample, "url", "") or ""
        path = getattr(sample, "path", "") or ""
        process = (getattr(sample, "process", "") or "").casefold()
        host = page_host(url)
        browsers = self.settings.browser_processes
        is_browser = any(fnmatch.fnmatch(process, item.strip().casefold()) for item in browsers if item.strip())
        title = getattr(sample, "title", "") or ""
        if is_gordian_window(process, title):
            key = "app:gordian"
        elif host:
            key = "site:" + host
        elif path and not is_browser:
            key = "app:" + path
        else:
            key = ""
        if key == self._header_logo_key:
            return
        self._header_logo_key = key
        try:
            self.process_icon.configure(image="")
            if self.process_icon.winfo_ismapped():
                self.process_icon.pack_forget()
        except tk.TclError:
            return
        if not key:
            return

        def apply(photo, expected=key) -> None:
            if self._header_logo_key != expected:
                return
            try:
                self.process_icon.configure(image=photo, bg=self.process_label.cget("bg"))
                if not self.process_icon.winfo_ismapped():
                    self.process_icon.pack(side="left", before=self.process_label, padx=(6, 0))
            except tk.TclError:
                return

        if key == "app:gordian" or key.startswith("app:"):
            image = draw_icon(GREEN) if key == "app:gordian" else icon_image(path)
            if image is None:
                return
            photo = ImageTk.PhotoImage(image.resize((16, 16), Image.Resampling.LANCZOS))
            self._logo_photos[key] = photo
            apply(photo)
            return
        self._load_logo(key, lambda: favicon_image(url), apply)

    def _attach_logo(self, box, logo, prompt) -> None:
        if prompt.url:
            key = "site:" + (page_host(prompt.url) or prompt.url)
            loader = lambda: favicon_image(prompt.url)
        elif prompt.icon_path:
            key = "app:" + prompt.icon_path
            loader = lambda: icon_image(prompt.icon_path)
        else:
            return

        def apply(photo) -> None:
            try:
                if not box.winfo_exists():
                    return
                logo.configure(image=photo)
                box._logo_photo = photo
            except tk.TclError:
                return

        if key.startswith("app:"):
            image = loader()
            if image is None:
                return
            photo = ImageTk.PhotoImage(image.resize((16, 16), Image.Resampling.LANCZOS))
            self._logo_photos[key] = photo
            apply(photo)
            return
        self._load_logo(key, loader, apply)

    def _load_logo(self, key: str, loader, apply) -> None:
        if key in self._logo_photos:
            apply(self._logo_photos[key])
            return
        if key in self._logo_missing:
            return
        waiting = self._logo_wait.setdefault(key, [])
        waiting.append(apply)
        if len(waiting) > 1:
            return

        def work() -> None:
            image = None
            try:
                image = loader()
            except Exception:
                log_exception("logo")

            def finish() -> None:
                callbacks = self._logo_wait.pop(key, [])
                if image is None:
                    self._logo_missing.add(key)
                    return
                try:
                    photo = ImageTk.PhotoImage(image.resize((16, 16), Image.Resampling.LANCZOS))
                except Exception:
                    self._logo_missing.add(key)
                    return
                self._logo_photos[key] = photo
                for callback in callbacks:
                    callback(photo)

            try:
                self.root.after(0, finish)
            except tk.TclError:
                return

        threading.Thread(target=work, name="gordian-logo", daemon=True).start()

    def _build_ask(self, prompt) -> None:
        color = self._background()
        box = tk.Toplevel(self.root)
        box.withdraw()
        box.title("Gordian Knot")
        box.configure(bg=color)
        box.attributes("-topmost", True)
        box.resizable(False, False)
        try:
            box.iconphoto(False, self._icon_image)
        except tk.TclError:
            pass
        frame = tk.Frame(box, bg=color, padx=12, pady=10)
        frame.pack(fill="both", expand=True)
        if prompt.mode == "tutorial":
            heading = "This looks like a tutorial."
            question = "Count this video as work? Other YouTube stays unproductive."
            work_text = "Work, this video"
        elif prompt.mode == "app":
            heading = prompt.label
            question = "Is this program productive?"
            work_text = "Work"
        else:
            heading = prompt.label
            question = "Is this productive?"
            work_text = "Work"
        head = tk.Frame(frame, bg=color)
        head.pack(anchor="w", fill="x")
        logo = tk.Label(head, bg=color, bd=0, highlightthickness=0)
        if prompt.url or prompt.icon_path:
            blank = ImageTk.PhotoImage(Image.new("RGBA", (16, 16), (0, 0, 0, 0)))
            box._logo_photo = blank
            logo.configure(image=blank)
            logo.pack(side="left", padx=(0, 8))
            self._attach_logo(box, logo, prompt)
        text_col = tk.Frame(head, bg=color)
        text_col.pack(side="left", fill="x", expand=True)
        title = _label(text_col, heading, color, TEXT, 10, bold=True, wrap=260)
        title.pack(anchor="w")
        if prompt.mode == "tutorial":
            name = _label(text_col, prompt.label, color, TEXT, 9, wrap=260)
            name.pack(anchor="w", pady=(4, 0))
        body = _label(frame, question, color, MUTED, 8, wrap=280)
        body.pack(anchor="w", pady=(4, 0))
        actions = tk.Frame(frame, bg=color)
        actions.pack(fill="x", pady=(10, 0))
        work = tk.Button(
            actions,
            text=work_text,
            command=lambda: self._answer_ask(prompt, "productive"),
            bg=GREEN,
            fg="#ffffff",
            activebackground=GREEN,
            activeforeground="#ffffff",
            relief="raised",
            bd=1,
            padx=8,
            font=(FONT, 8, "bold"),
            cursor="hand2",
        )
        spend = tk.Button(
            actions,
            text="Spend",
            command=lambda: self._answer_ask(prompt, "unproductive"),
            bg="#7851a9",
            fg="#ffffff",
            activebackground="#7851a9",
            activeforeground="#ffffff",
            relief="raised",
            bd=1,
            padx=8,
            font=(FONT, 8, "bold"),
            cursor="hand2",
        )
        later = tk.Button(
            actions,
            text="Not now",
            command=lambda: self._answer_ask(prompt, "later"),
            bg=color,
            fg=MUTED,
            activebackground=color,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=6,
            font=(FONT, 8),
            cursor="hand2",
        )
        later.pack(side="left")
        spend.pack(side="right")
        work.pack(side="right", padx=(0, 6))
        self._ask_box = box

        def close() -> None:
            self._answer_ask(prompt, "later")

        box.protocol("WM_DELETE_WINDOW", close)
        box.bind("<Escape>", lambda _event: close())
        box.bind("<Map>", lambda _event: self._paint_title_bar(box), add="+")
        box.update_idletasks()
        width = max(box.winfo_reqwidth(), 320)
        height = max(box.winfo_reqheight(), 120)
        x = max(0, box.winfo_screenwidth() - width - 16)
        box.geometry(f"{width}x{height}+{x}+16")
        box.deiconify()
        box.update_idletasks()
        width = max(box.winfo_reqwidth(), 320)
        height = max(box.winfo_reqheight(), 120)
        box.geometry(f"{width}x{height}+{x}+16")
        box.minsize(width, height)
        try:
            hwnd = int(box.winfo_id())
            release_owner(hwnd)
            self._paint_title_bar(box)
        except Exception:
            log_exception("ask window")
        box.lift()
        box.after_idle(lambda: self._paint_title_bar(box))
        box.after(50, lambda: self._paint_title_bar(box))

    def _reload_config(self) -> None:
        try:
            mtime = self.config_path.stat().st_mtime
        except OSError:
            return
        if mtime == self.config_mtime:
            return
        self.config_mtime = mtime
        try:
            settings = parse_config(self.config_path)
        except ValueError as exc:
            self.notice_text = str(exc)
            return
        self.settings = settings
        self.notice_text = ""
        self.warning_sound = read_warning_sound(self.config_path)
        self._ask_skip = read_ask_skip(self.config_path)
        self.ledger.set_ratio(settings.work_minutes, settings.reward_minutes)

    def _render(self, _sample, decision) -> None:
        kind = decision.kind
        color = KIND_COLORS.get(kind, MUTED)
        shown = activity_name(decision.process, decision.title)
        productive = self.ledger.productive_seconds
        remaining = self.ledger.remaining_seconds
        if remaining < 0:
            balance = f"{format_duration(abs(remaining))} overspent"
            free_menu = f"Overspent: {format_duration(abs(remaining))}"
        else:
            balance = f"{format_duration(remaining)} left"
            free_menu = f"Free time left: {format_duration(remaining)}"
        title = f"Gordian Knot — {balance}"
        if title != self._shown_title:
            self._shown_title = title
            self.root.title(title)
        word = STATUS_WORDS.get(kind, kind)
        self.menu_status = f"{word}: {shown}"
        self.menu_work = f"Work today: {format_duration(productive)}"
        self.menu_free = free_menu
        self._update_tray(color, f"{word} · {shown} · {balance}")
        if self._view_visible():
            self._render_view(decision, shown, color, self.ledger.snapshot(), _sample)

    def _render_view(self, decision, shown: str, color: str, snap: dict, sample=None) -> None:
        # Only runs while the window is on screen; _on_root_map repaints when it comes back.
        kind = decision.kind
        now = datetime.now()
        _update(self.date_label, text=f"{now.strftime('%A, %B')} {now.day}")
        _update(self.accent, bg=self._chosen(self.accent, "bg", color))
        _update(
            self.status_label,
            text=STATUS_WORDS.get(kind, kind),
            fg=self._chosen(self.status_label, "fg", color),
        )
        header_name = shown if len(shown) <= 36 else shown[:35] + "…"
        _update(self.process_label, text=header_name)
        if sample is not None:
            self._show_header_logo(sample)
        seconds = float(snap["current"]["seconds"]) if snap["current"] else 0.0
        if kind == "paused" and snap["current"] and snap["current"].get("start"):
            try:
                started = datetime.fromisoformat(snap["current"]["start"])
                seconds = max(seconds, (now - started).total_seconds())
            except (TypeError, ValueError):
                pass
        _update(self.stretch_label, text=format_duration(seconds), fg=self._chosen(self.stretch_label, "fg", color))

        productive = snap["productive_seconds"]
        remaining = snap["remaining_seconds"]
        _update(
            self.work_value,
            text=format_duration(productive),
            fg=self._chosen(self.work_value, "fg", GREEN if kind == "earn" else TEXT),
        )
        if remaining < 0:
            _update(self.free_caption, text="OVERSPENT", fg=self._chosen(self.free_caption, "fg", RED))
            _update(
                self.free_value,
                text=format_duration(abs(remaining)),
                fg=self._chosen(self.free_value, "fg", RED),
            )
        else:
            value_color = GREEN if remaining > 0 else TEXT
            _update(self.free_caption, text="FREE", fg=self._chosen(self.free_caption, "fg", MUTED))
            _update(
                self.free_value,
                text=format_duration(remaining),
                fg=self._chosen(self.free_value, "fg", value_color),
            )
        _update(self.ratio_chip, text=self._ratio_caption())
        _update(self.pause_button, text="Resume" if self.paused else "Pause")
        segments = list(self._history_segments) + list(snap.get("segments") or [])
        if self.activity_view == "feed":
            logged = [item for item in segments if item.get("end")]
            self._set_log([format_segment(item, now.date()) for item in reversed(logged)])
        if self.activity_view == "apps":
            if self.span_window:
                self.app_bars.set_rows(application_totals(segments, self.span_window[0], self.span_window[1]))
            else:
                self.app_bars.set_rows(application_totals(segments))
        if self.plot_view == "map":
            window = self.span_window
            earn = [item for item in segments if item.get("kind") == "earn" and item.get("process") != "Earlier"]
            spend = [item for item in segments if item.get("kind") == "spend" and item.get("process") != "Earlier"]
            paused = [item for item in segments if item.get("kind") == "paused"]
            if window:
                earn_rows = application_totals(earn, window[0], window[1])
                spend_rows = group_youtube(application_totals(spend, window[0], window[1]))
                pause_rows = application_totals(paused, window[0], window[1])
            else:
                earn_rows = application_totals(earn)
                spend_rows = group_youtube(application_totals(spend))
                pause_rows = application_totals(paused)
            green, red = balance_areas(
                sum(value for _name, value in earn_rows),
                sum(value for _name, value in spend_rows),
                snap["remaining_seconds"],
                self.ledger.work_minutes,
                self.ledger.reward_minutes,
            )
            pause_seconds = sum(value for _name, value in pause_rows)
            if pause_seconds > 0:
                spend_rows = list(spend_rows) + [("Paused", pause_seconds)]
            self.usage.set_picture(earn_rows, spend_rows, green, red)
        self.chart.set_segments(segments, now)
        _update(self.chart_title, text=self._chart_caption())
        note = self.notice_text or self.user_note
        if self.debug_mode:
            debug = "Colors are on. Click anything to change its color. Choose Colors again to leave."
            note = f"{debug}  {note}" if note else debug
        self._set_notice(note, RED if self.notice_text else TEXT)

    def _consider_warning(self, decision, remaining: float, sample) -> None:
        if remaining > 0 and not self._in_lockdown():
            cancel_pending_closes()
        if remaining >= 0 and self.ledger.barred:
            self.ledger.barred = []
            self._save_quietly()
        now_mono = time.monotonic()
        if self._in_lockdown() and not self.paused and (
            decision.kind == "spend" or now_mono < self._lockdown_until
        ):
            if decision.kind == "spend":
                self._close_front(decision, sample)
                self._lockdown_until = time.monotonic() + 2.0
            popping = decision.kind == "spend" and not self._overlay_open()
            self._present_overlay(
                schedule_lockdown_message(),
                flash=popping,
                sound=popping,
            )
            return
        self._lockdown_until = 0.0
        key = ""
        if decision.kind == "spend":
            key = visit_key(
                decision.process,
                decision.title,
                self.settings.browser_processes,
                self.settings.reward_title_keywords,
            )
            self.over_target = (
                int(getattr(sample, "hwnd", 0) or 0),
                int(getattr(sample, "pid", 0) or 0),
                decision.process,
            )
            self._over_balance = (time.monotonic(), remaining)
        now_mono = time.monotonic()
        state, action = advance_overtime(
            decision.kind,
            remaining,
            self.paused,
            key,
            now_mono,
            self.overtime,
            self._overlay_is_foreground(sample),
            set(self.ledger.barred),
            lockdown=False,
            hold=now_mono < self._hold_close_until,
        )
        self.overtime = state
        self.reward_warned = state.stage > 0
        if action == "close" and state.key and state.key not in self.ledger.barred:
            self.ledger.barred.append(state.key)
            self._save_quietly()
        if action in {"close", "blocked", "again"}:
            self._close_front(decision, sample)
        if state.stage <= 0 or state.started is None:
            self._over_balance = None
            self._hide_overlay()
            return
        shown = remaining
        if self._overlay_is_foreground(sample) and self._over_balance is not None:
            marked, base = self._over_balance
            shown = base - (time.monotonic() - marked)
        seconds_left = CLOSE_AFTER - (time.monotonic() - state.started)
        text = overlay_text(shown, state.stage, seconds_left)
        noticeable = action in {"warn", "warn-again", "flash", "close", "blocked"}
        if noticeable or action == "again" or self._overlay_open():
            self._present_overlay(
                text,
                flash=noticeable and not self._overlay_open(),
                sound=noticeable,
            )

    def _overlay_open(self) -> bool:
        box = self._over_box
        if box is None:
            return False
        try:
            return bool(box.winfo_exists())
        except tk.TclError:
            return False

    def _overlay_is_foreground(self, sample) -> bool:
        if not self._overlay_open():
            return False
        hwnd = int(getattr(sample, "hwnd", 0) or 0)
        try:
            return hwnd == int(self._over_box.winfo_id())
        except tk.TclError:
            return False

    def _present_overlay(self, text: str, flash: bool, sound: bool = False) -> None:
        first = not self._overlay_open()
        if first:
            self._build_overlay()
        if sound:
            play_warning(self.warning_sound)
        try:
            self._over_label.configure(text=text)
            hwnd = int(self._over_box.winfo_id())
            if first or flash:
                self._over_box.update_idletasks()
                self._move_overlay(hwnd)
            else:
                pin_window(hwnd)
            if flash:
                flash_window(hwnd)
        except tk.TclError:
            log_exception("warning")

    def _build_overlay(self) -> None:
        color = self._background()
        box = tk.Toplevel(self.root)
        box.withdraw()
        box.title("Gordian Knot")
        box.configure(bg=color)
        box.attributes("-topmost", True)
        box.resizable(False, False)
        try:
            box.iconphoto(False, self._icon_image)
        except tk.TclError:
            pass
        body = tk.Frame(box, bg=color, padx=16, pady=14)
        body.pack(fill="both", expand=True)
        label = _label(body, "", color, TEXT, 9, wrap=360)
        label.pack(anchor="w")
        button = tk.Button(
            body,
            text="OK",
            font=(FONT, 8),
            bd=2,
            padx=14,
            pady=2,
            command=self._hide_overlay,
            cursor="hand2",
        )
        button.pack(anchor="e", pady=(12, 0))
        box.protocol("WM_DELETE_WINDOW", self._hide_overlay)
        box.bind("<Escape>", lambda _event: self._hide_overlay())
        box.update_idletasks()
        self._over_box = box
        self._over_label = label
        try:
            hold_inactive(int(box.winfo_id()))
        except (tk.TclError, OSError):
            log_exception("warning")

    def _move_overlay(self, hwnd: int) -> None:
        box = self._over_box
        if box is None:
            return
        box.update_idletasks()
        width = max(box.winfo_reqwidth(), 420)
        height = max(box.winfo_reqheight(), 120)
        target = window_rect(self.over_target[0])
        if target is None:
            x = max((box.winfo_screenwidth() - width) // 2, 0)
            y = max((box.winfo_screenheight() - height) // 3, 0)
        else:
            left, top, right, bottom = target
            x = left + max((right - left - width) // 2, 0)
            y = top + max((bottom - top - height) // 3, 0)
        stick_window(hwnd, x, y, width, height)

    def _hide_overlay(self) -> None:
        box = self._over_box
        self._over_box = None
        self._over_label = None
        if box is None:
            return
        try:
            box.destroy()
        except tk.TclError:
            pass

    def _warning_open(self) -> bool:
        thread = self._warning_thread
        return thread is not None and thread.is_alive()

    def _update_tray(self, color: str, tooltip: str) -> None:
        if self.icon is None:
            return
        try:
            if self.icon.title != tooltip[:120]:
                self.icon.title = tooltip[:120]
            if color != self.icon_color:
                self.icon_color = color
                self.icon.icon = draw_icon(color)
        except Exception:
            log_exception("tray update")

    def _set_log(self, lines: list[str]) -> None:
        if lines != self.log_lines:
            self.log_lines = list(lines)
            self.log.delete(0, "end")
            for line in lines:
                self.log.insert("end", line)
        if self.activity_view == "feed":
            self._pack_feed()

    def _pack_feed(self) -> None:
        lines = self.log_lines or []
        if not lines:
            self.scroll.pack_forget()
            self.log.pack_forget()
            if not self.empty_log.winfo_ismapped():
                self.empty_log.pack(fill="both", expand=True, padx=12, pady=12)
            return
        self.empty_log.pack_forget()
        if not self.scroll.winfo_ismapped():
            self.scroll.pack(side="right", fill="y")
        if not self.log.winfo_ismapped():
            self.log.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=8)

    def _interval_ms(self) -> int:
        return int(max(0.5, float(self.settings.poll_seconds)) * 1000)

    def _pause_focus(self, sample) -> str:
        process = (getattr(sample, "process", "") or "").strip()
        title = getattr(sample, "title", "") or ""
        category = classify(
            process,
            title,
            self.settings,
            path=getattr(sample, "path", "") or "",
            fullscreen=bool(getattr(sample, "fullscreen", False)),
            url=getattr(sample, "url", "") or "",
        )
        idle = float(getattr(sample, "idle_seconds", 0) or 0)
        if category == "reward":
            return "unproductive"
        if category == "productive" and idle < float(self.settings.idle_seconds):
            return "productive"
        if category == "productive":
            return "idle"
        return "other"

    def _steer_pause(self, sample, wall: datetime) -> str:
        """End a pause after a minute of work, or bring that pause back."""
        focus = self._pause_focus(sample)
        process = (getattr(sample, "process", "") or "").strip()
        title = getattr(sample, "title", "") or ""
        self._pause_hold = next_pause_hold(self._pause_hold, focus)
        if focus == "unproductive":
            self._pause_minute = PauseMinute()
        if self._pause_offer is not None:
            if not self.paused:
                self._answer_pause_offer(False, quiet=True)
                return ""
            if focus == "unproductive":
                self._answer_pause_offer(False, quiet=False)
                return ""
            if not self._pause_offer_open():
                self._show_pause_offer()
            return ""
        if repause_for_unproductive(self._pause_latch, self.paused, focus):
            self.paused = True
            self._pause_hold = False
            self._pause_minute = PauseMinute()
            self.overtime = Overtime()
            self._hold_close_until = 0.0
            self._hide_overlay()
            cancel_pending_closes()
            self._show_note(f"Paused again. {activity_name(process, title)} is unproductive.")
            return "repause"
        if not self.paused or self._pause_hold:
            return ""
        moment = wall
        counted = focus
        if focus == "idle":
            counted = "other"
            idle = float(getattr(sample, "idle_seconds", 0) or 0)
            moment = wall - timedelta(seconds=min(idle, 3600))
        state, pieces = advance_pause_minute(self._pause_minute, moment, counted, process, title)
        self._pause_minute = state
        if not pieces:
            return ""
        if not self.paused:
            self._pause_minute = PauseMinute()
            return ""
        self._pause_offer = pieces
        self._show_pause_offer()
        return ""

    def _show_pause_offer(self) -> None:
        if not self.paused:
            return
        if self._pause_offer_open():
            return
        color = self._background()
        box = tk.Toplevel(self.root)
        box.withdraw()
        box.title("Gordian Knot")
        box.configure(bg=color)
        box.attributes("-topmost", True)
        box.resizable(False, False)
        try:
            box.iconphoto(False, self._icon_image)
        except tk.TclError:
            pass
        frame = tk.Frame(box, bg=color, padx=12, pady=10)
        frame.pack(fill="both", expand=True)
        title = _label(frame, "End the pause?", color, TEXT, 10, bold=True, wrap=280)
        title.pack(anchor="w")
        body = _label(
            frame,
            "You have used work programs for a minute. Count that minute as work, and the free time it earns?",
            color,
            MUTED,
            8,
            wrap=280,
        )
        body.pack(anchor="w", pady=(4, 0))
        actions = tk.Frame(frame, bg=color)
        actions.pack(fill="x", pady=(10, 0))
        keep = tk.Button(
            actions,
            text="Keep paused",
            command=lambda: self._answer_pause_offer(False),
            bg=color,
            fg=MUTED,
            activebackground=color,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=6,
            font=(FONT, 8),
            cursor="hand2",
        )
        count = tk.Button(
            actions,
            text="Count it",
            command=lambda: self._answer_pause_offer(True),
            bg=GREEN,
            fg="#ffffff",
            activebackground=GREEN,
            activeforeground="#ffffff",
            relief="raised",
            bd=1,
            padx=8,
            font=(FONT, 8, "bold"),
            cursor="hand2",
        )
        keep.pack(side="left")
        count.pack(side="right")
        self._pause_offer_box = box
        box.protocol("WM_DELETE_WINDOW", lambda: self._answer_pause_offer(False))
        box.bind("<Escape>", lambda _event: self._answer_pause_offer(False))
        box.bind("<Map>", lambda _event: self._paint_title_bar(box), add="+")
        box.update_idletasks()
        width = max(box.winfo_reqwidth(), 320)
        height = max(box.winfo_reqheight(), 110)
        x = max(0, box.winfo_screenwidth() - width - 16)
        box.geometry(f"{width}x{height}+{x}+16")
        box.deiconify()
        box.minsize(width, height)
        try:
            hwnd = int(box.winfo_id())
            release_owner(hwnd)
            self._paint_title_bar(box)
        except Exception:
            log_exception("pause offer")
        box.lift()
        box.after_idle(lambda: self._paint_title_bar(box))
        box.after(50, lambda: self._paint_title_bar(box))

    def _pause_offer_open(self) -> bool:
        box = self._pause_offer_box
        if box is None:
            return False
        try:
            return bool(box.winfo_exists())
        except tk.TclError:
            return False

    def _answer_pause_offer(self, accept: bool, quiet: bool = True) -> None:
        pieces = self._pause_offer
        self._pause_offer = None
        box = self._pause_offer_box
        self._pause_offer_box = None
        if box is not None:
            try:
                box.destroy()
            except tk.TclError:
                pass
        if quiet:
            self._pause_hold = True
        if not accept or not pieces or not self.paused:
            self._pause_minute = PauseMinute()
            return
        self.ledger.credit_works(
            [
                (start, end, item_process, item_title, segment_identity(item_process, item_title, self.settings))
                for start, end, item_process, item_title in pieces
            ]
        )
        self.paused = False
        self._pause_latch = True
        self.overtime = Overtime()
        self._hold_close_until = 0.0
        self._hide_overlay()
        cancel_pending_closes()
        self._show_note("Pause ended. One minute of work counted. Unproductive programs pause again.")

    def toggle_pause(self) -> None:
        self._pause_offer = None
        box = self._pause_offer_box
        self._pause_offer_box = None
        if box is not None:
            try:
                box.destroy()
            except tk.TclError:
                pass
        self.paused = not self.paused
        self._pause_latch = self.paused
        self._pause_hold = False
        self._pause_minute = PauseMinute()
        self.overtime = Overtime()
        self._hold_close_until = 0.0
        self._hide_overlay()
        cancel_pending_closes()
        self.pause_button.configure(text="Resume" if self.paused else "Pause")

    def open_config(self) -> None:
        try:
            if not self.config_path.exists():
                write_default_config(self.config_path)
        except OSError as exc:
            self.notice_text = f"Could not open the lists ({exc})."
            self._set_notice(self.notice_text, RED)
            return
        self.show()
        self._open_lists()

    def _open_lists(self) -> None:
        groups = (
            ("Work programs", "productive_processes", "Programs that earn free time. * matches the rest of the name."),
            ("Unproductive programs", "reward_processes", "Programs that spend free time."),
            ("Work websites", "productive_title_keywords", "Website names and page titles that count as work."),
            ("Unproductive websites", "reward_title_keywords", "Website names and page titles that spend free time."),
            ("Browsers", "browser_processes", "These are read as the website in front, not as one program."),
            ("Ignored", "ignored_processes", "These windows do not earn or spend."),
            ("Game folders", "game_path_markers", "A program in one of these folders counts as a game."),
        )
        edited = {key: list(getattr(self.settings, key)) for _title, key, _note in groups}
        box, frame, surfaces = self._window("Edit lists")
        color = self._background()
        intro = _label(
            frame,
            "Pick a group, then add or remove a line. Save writes the lists.",
            color,
            TEXT,
            9,
            wrap=640,
        )
        intro.pack(anchor="w")
        surfaces.append(intro)
        body = tk.Frame(frame, bg=color)
        body.pack(fill="both", expand=True, pady=(8, 0))
        surfaces.append(body)
        picked = tk.StringVar(value=groups[0][1])
        names = tk.Listbox(
            body,
            bg=CARD,
            fg=TEXT,
            selectbackground="#3a6ea5",
            selectforeground="#ffffff",
            relief="sunken",
            bd=1,
            highlightthickness=0,
            activestyle="none",
            exportselection=False,
            font=(FONT, 9),
            width=24,
            height=12,
        )
        names.pack(side="left", fill="y")
        surfaces.append(names)
        for title, key, _note in groups:
            names.insert("end", title)
        names.selection_set(0)
        detail = tk.Frame(body, bg=color)
        detail.pack(side="left", fill="both", expand=True, padx=(12, 0))
        surfaces.append(detail)
        note = _label(detail, groups[0][2], color, MUTED, 8, wrap=420)
        note.pack(anchor="w")
        surfaces.append(note)
        items = tk.Listbox(
            detail,
            bg=CARD,
            fg=TEXT,
            selectbackground="#3a6ea5",
            selectforeground="#ffffff",
            relief="sunken",
            bd=1,
            highlightthickness=0,
            activestyle="none",
            exportselection=False,
            font=(FONT, 9),
            height=12,
        )
        items.pack(fill="both", expand=True, pady=(6, 0))
        surfaces.append(items)
        add_row = tk.Frame(detail, bg=color)
        add_row.pack(fill="x", pady=(6, 0))
        surfaces.append(add_row)
        entry = self._field(add_row, 36)
        entry.pack(side="left", fill="x", expand=True)
        error = _label(frame, "", color, RED, 8, wrap=640)
        surfaces.append(error)

        def current_key() -> str:
            selected = names.curselection()
            index = int(selected[0]) if selected else 0
            return groups[index][1]

        def show_group(_event=None) -> None:
            key = current_key()
            for title, group_key, text in groups:
                if group_key == key:
                    note.configure(text=text)
                    break
            items.delete(0, "end")
            for line in edited[key]:
                items.insert("end", line)
            entry.delete(0, "end")
            error.configure(text="")

        def add_line(_event=None) -> None:
            text = " ".join(entry.get().split())
            if not text:
                return
            key = current_key()
            if any(text.casefold() == item.casefold() for item in edited[key]):
                error.configure(text="That line is already in this group.")
                return
            edited[key].append(text)
            items.insert("end", text)
            entry.delete(0, "end")
            error.configure(text="")

        def remove_line(_event=None) -> None:
            selected = list(items.curselection())
            if not selected:
                return
            key = current_key()
            for index in reversed(selected):
                del edited[key][index]
                items.delete(index)
            error.configure(text="")

        names.bind("<<ListboxSelect>>", show_group)
        entry.bind("<Return>", add_line)
        items.bind("<Delete>", remove_line)
        self._dialog_button(add_row, "Add", add_line).pack(side="left", padx=(8, 0))
        self._dialog_button(add_row, "Remove", remove_line).pack(side="left", padx=(8, 0))
        show_group()

        def added(old: list[str], new: list[str]) -> bool:
            known = {item.casefold() for item in old}
            return any(item.casefold() not in known for item in new)

        def save() -> None:
            typed = "" if password is None else password.get()
            if not password_accepted(self.unlocked, typed, self.settings.reset_password):
                error.configure(text="That password is wrong.")
                return
            if lockdown_active(self.settings.schedule, datetime.now()) and (
                added(self.settings.reward_processes, edited["reward_processes"])
                or added(self.settings.reward_title_keywords, edited["reward_title_keywords"])
            ):
                error.configure(text="Lockdown is on for this part of the schedule. Unproductive programs cannot be added.")
                return
            try:
                write_edited_lists(
                    self.config_path,
                    edited["productive_processes"],
                    edited["reward_processes"],
                    edited["productive_title_keywords"],
                    edited["reward_title_keywords"],
                    edited["browser_processes"],
                    edited["ignored_processes"],
                    edited["game_path_markers"],
                )
                self.config_mtime = self.config_path.stat().st_mtime
            except (OSError, ValueError) as exc:
                error.configure(text=f"Could not save the lists ({exc}).")
                return
            self.settings.productive_processes = list(edited["productive_processes"])
            self.settings.reward_processes = list(edited["reward_processes"])
            self.settings.productive_title_keywords = list(edited["productive_title_keywords"])
            self.settings.reward_title_keywords = list(edited["reward_title_keywords"])
            self.settings.browser_processes = list(edited["browser_processes"])
            self.settings.ignored_processes = list(edited["ignored_processes"])
            self.settings.game_path_markers = list(edited["game_path_markers"])
            box.destroy()
            self._show_note("The lists are saved.")

        error.pack(anchor="w", pady=(8, 0))
        actions = tk.Frame(frame, bg=color)
        actions.pack(fill="x", pady=(8, 0))
        surfaces.append(actions)
        password = None
        if not self.unlocked:
            _label(actions, "Password", color, TEXT, 9).pack(side="left")
            password = self._field(actions, 12, secret=True)
            password.pack(side="left", padx=(8, 0))
        self._dialog_button(actions, "Save", save).pack(side="right")
        self._dialog_button(actions, "Cancel", box.destroy).pack(side="right", padx=(0, 8))
        box.bind("<Escape>", lambda _event: box.destroy())
        box.resizable(True, True)
        box.minsize(640, 420)
        self._place_window(box, 720)
        entry.focus_set()
        self.root.wait_window(box)

    def open_data(self) -> None:
        try:
            os.startfile(self.data_path)
        except OSError as exc:
            self.notice_text = f"Could not open the data folder ({exc})."
            self._set_notice(self.notice_text, RED)

    def reset_time(self) -> None:
        self.show()
        choice = self._reset_dialog(clear_history=False)
        if choice is None:
            return
        self._apply_reset(choice)

    def clear_history(self) -> None:
        self.show()
        choice = self._reset_dialog(clear_history=True)
        if choice is None:
            return
        self._apply_reset(True)

    def _reset_dialog(self, clear_history: bool) -> bool | None:
        box, frame, surfaces = self._window("Clear history" if clear_history else "Reset time")
        color = self._background()
        intro = _label(
            frame,
            "Reset today back to zero. Clear history also removes every saved day, so the chart looks newly installed. Lists, the schedule, and the password stay.",
            color,
            TEXT,
            9,
            wrap=420,
        )
        intro.pack(anchor="w")
        surfaces.append(intro)
        chosen = tk.BooleanVar(value=clear_history)
        tick = tk.Checkbutton(
            frame,
            text="Clear all history",
            variable=chosen,
            bg=color,
            fg=TEXT,
            activebackground=color,
            activeforeground=TEXT,
            selectcolor=CARD,
            font=(FONT, 9),
            highlightthickness=0,
            bd=0,
        )
        tick.pack(anchor="w", pady=(10, 0))
        surfaces.append(tick)
        password = None
        if not self.unlocked:
            caption = _label(frame, "Password", color, MUTED, 9)
            caption.pack(anchor="w", pady=(10, 2))
            surfaces.append(caption)
            password = self._field(frame, 28, secret=True)
            password.pack(anchor="w", ipady=4)
        error = _label(frame, "", color, RED, 9, wrap=420)
        error.pack(anchor="w", pady=(8, 0))
        surfaces.append(error)
        result: dict[str, bool | None] = {"value": None}

        def accept(_event=None) -> None:
            typed = "" if password is None else password.get()
            if not password_accepted(self.unlocked, typed, self.settings.reset_password):
                error.configure(text="That password is wrong.")
                return
            result["value"] = bool(chosen.get())
            box.destroy()

        actions = tk.Frame(frame, bg=color)
        actions.pack(fill="x", pady=(12, 0))
        surfaces.append(actions)
        self._dialog_button(actions, "Cancel", box.destroy).pack(side="right")
        self._dialog_button(actions, "OK", accept).pack(side="right", padx=(0, 8))
        box.bind("<Return>", accept)
        box.bind("<Escape>", lambda _event: box.destroy())
        self._place_window(box, 460)
        try:
            box.grab_set()
        except tk.TclError:
            pass
        if password is not None:
            password.focus_set()
        self.root.wait_window(box)
        value = result["value"]
        return None if value is None else bool(value)

    def _apply_reset(self, clear_history: bool) -> None:
        self.ledger.reset()
        if clear_history:
            try:
                clear_saved_history(self.ledger.state_path, self.ledger.history_path)
            except OSError as exc:
                self._show_note(f"The history could not be cleared ({exc}).")
                return
            self.span_window = None
            self.span_from.delete(0, "end")
            self.span_to.delete(0, "end")
            self.chart.user_zoomed = False
            self.chart.set_window(None, None)
        try:
            self.ledger.save()
        except Exception:
            log_exception("reset")
            self._show_note("The time could not be saved.")
            return
        self.reward_warned = False
        self.overtime = Overtime()
        if clear_history:
            self._show_note("All saved days were cleared. Lists and the schedule are unchanged.")
        else:
            self._show_note("Today's work time and free time were reset.")

    def toggle_unlock(self) -> None:
        self.show()
        if self.unlocked:
            self.unlocked = False
            self._paint_unlock()
            self._show_note("Locked. The password is required again.")
            return
        values = self._prompt(
            "Unlock",
            "Enter the password once. It is not asked again until you press Lock.",
            [("password", "Password")],
            secret={"password"},
        )
        if values is None:
            return
        if not password_accepted(False, values["password"], self.settings.reset_password):
            self._show_note("That password is wrong.")
            return
        self.unlocked = True
        self._paint_unlock()
        self._show_note("Unlocked. The password is not asked until you press Lock.")

    def _paint_unlock(self) -> None:
        self._refresh_logo_menu()

    def _logo_menu_items(self) -> list[tuple[int, str]]:
        return [
            (0x0100, "About"),
            (0x0110, "Reset time"),
            (0x0120, "Clear history"),
            (0x0130, "Change password"),
            (0x0140, "Lock" if self.unlocked else "Unlock"),
            (0x0150, "Warning sound"),
            (0x0160, "Colors"),
        ]

    def _refresh_logo_menu(self) -> None:
        try:
            install_logo_menu(self.root, self._logo_menu_items())
        except Exception:
            log_exception("logo menu")

    def _run_logo_command(self, command: int) -> None:
        actions = {
            0x0100: self.show_about,
            0x0110: self.reset_time,
            0x0120: self.clear_history,
            0x0130: self.change_password,
            0x0140: self.toggle_unlock,
            0x0150: self._edit_warning_sound,
            0x0160: self._toggle_debug,
        }
        action = actions.get(command)
        if action is not None:
            action()

    def _popup_logo_menu(self, event) -> None:
        # Run the choice after the menu has closed. Opening a window from the
        # menu itself releases the menu grab and takes the application down.
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="About", command=lambda: self.root.after(0, self.show_about))
        menu.add_command(label="Reset time", command=lambda: self.root.after(0, self.reset_time))
        menu.add_command(label="Clear history", command=lambda: self.root.after(0, self.clear_history))
        menu.add_command(label="Change password", command=lambda: self.root.after(0, self.change_password))
        menu.add_command(
            label="Lock" if self.unlocked else "Unlock",
            command=lambda: self.root.after(0, self.toggle_unlock),
        )
        menu.add_command(label="Warning sound", command=lambda: self.root.after(0, self._edit_warning_sound))
        menu.add_command(label="Colors", command=lambda: self.root.after(0, self._toggle_debug))
        self._logo_popup = menu
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass

    def change_password(self) -> None:
        self.show()
        fields = [("new", "New password"), ("confirm", "Confirm new password")]
        if not self.unlocked:
            fields.insert(0, ("current", "Current password"))
        values = self._prompt(
            "Change password",
            "The password protects Reset time. It is stored in config.json.",
            fields,
        )
        if values is None:
            return
        if not self.unlocked and values["current"] != self.settings.reset_password:
            self._show_note("That password is wrong.")
            return
        if not values["new"]:
            self._show_note("Enter a new password.")
            return
        if values["new"] != values["confirm"]:
            self._show_note("The new passwords do not match.")
            return
        try:
            write_password(self.config_path, values["new"])
        except (OSError, ValueError) as exc:
            self._show_note(f"Could not save the password ({exc}).")
            return
        self.settings.reset_password = values["new"]
        try:
            self.config_mtime = self.config_path.stat().st_mtime
        except OSError:
            self.config_mtime = 0.0
        self._show_note("The password was changed.")

    def change_ratio(self) -> None:
        self.show()
        block = self.active_block
        if block is not None and block.locked:
            index = self._block_index(block)
            if index is not None:
                self._schedule_edit(index)
            return
        current = block.ratio if block is not None else format_ratio(
            self.settings.work_minutes, self.settings.reward_minutes
        )
        fields = [("ratio", "W:R")]
        if not self.unlocked:
            fields.insert(0, ("password", "Password"))
        values = self._prompt(
            "Change ratio",
            "",
            fields,
            secret={"password"},
            initial={"ratio": current},
            placeholder={"ratio": "W:R"},
        )
        if values is None:
            return
        if not password_accepted(self.unlocked, values.get("password", ""), self.settings.reset_password):
            self._show_note("That password is wrong.")
            return
        parsed = parse_ratio(values["ratio"])
        if parsed is None:
            self._show_note("Enter a W:R like 2.5:1.")
            return
        shown = format_ratio(*parsed)
        if block is not None:
            self._replace_block(
                block,
                Block(block.days, block.start, block.end, False, shown, block.lockdown, block.name, block.color),
            )
            self.ledger.set_ratio(*parsed)
            self._show_note(f"The W:R is now {shown}.")
            return
        try:
            write_ratio(self.config_path, parsed[0], parsed[1])
        except (OSError, ValueError) as exc:
            self._show_note(f"Could not save the ratio ({exc}).")
            return
        self.settings.work_minutes = parsed[0]
        self.settings.reward_minutes = parsed[1]
        self.ledger.set_ratio(parsed[0], parsed[1])
        try:
            self.ledger.save()
            self.config_mtime = self.config_path.stat().st_mtime
        except OSError:
            log_exception("ratio")
            self._show_note("The ratio was saved, but today's time could not be written.")
            return
        self.ratio_chip.configure(text=shown)
        self._show_note(f"The W:R is now {shown}.")

    def _in_lockdown(self, moment: datetime | None = None) -> bool:
        return lockdown_active(self.settings.schedule, moment or datetime.now())

    def _close_front(self, decision, sample) -> None:
        if not may_force_close(self.ledger.remaining_seconds, self._in_lockdown()):
            cancel_pending_closes()
            self.overtime = Overtime()
            self._hold_close_until = 0.0
            return
        now_mono = time.monotonic()
        self.over_target = (
            int(getattr(sample, "hwnd", 0) or 0),
            int(getattr(sample, "pid", 0) or 0),
            decision.process,
        )
        if now_mono - self._last_close < 1.2:
            return
        self._last_close = now_mono
        self._hold_close_until = now_mono + 2.0
        hwnd, pid, process = self.over_target
        close_program(hwnd, pid, process or decision.process, tuple(self.settings.browser_processes))

    def _follow_schedule(self, moment: datetime) -> None:
        block = active_block(self.settings.schedule, moment)
        self.active_block = block
        if block is not None and not block.locked:
            parsed = parse_ratio(block.ratio)
            work, reward = parsed if parsed else (self.settings.work_minutes, self.settings.reward_minutes)
        else:
            work, reward = self.settings.work_minutes, self.settings.reward_minutes
        if self.ledger.work_minutes != work or self.ledger.reward_minutes != reward:
            self.ledger.set_ratio(work, reward)

    def _enforce_lock(self, sample, decision: Decision) -> Decision:
        block = self.active_block
        on_reward = decision.kind == "spend" or (self.paused and self.memory.kind == "spend")
        if not (block is not None and block.locked and on_reward):
            if not self._warning_open():
                self.lock_warned = False
            return decision
        try:
            minimize_window(int(getattr(sample, "hwnd", 0) or 0))
        except Exception:
            log_exception("lockout")
        if not self.lock_warned and not self._warning_open():
            self.lock_warned = True
            self._show_lockout(decision)
        if self.paused:
            return decision
        return Decision("locked", decision.process, decision.title)

    def _show_lockout(self, decision: Decision) -> None:
        text = lockout_message(reward_label(decision.process, decision.title))

        def show() -> None:
            try:
                warn(text)
            except Exception:
                log_exception("lockout")

        self._warning_thread = threading.Thread(target=show, name="gordian-lockout", daemon=True)
        self._warning_thread.start()

    def _fill_schedule(self) -> None:
        self.week.set_blocks(self.settings.schedule)

    def _block_index(self, block: Block) -> int | None:
        for index, item in enumerate(self.settings.schedule):
            if item == block:
                return index
        return None

    def _replace_block(self, block: Block, updated: Block) -> None:
        blocks = [updated if item == block else item for item in self.settings.schedule]
        self._store_schedule(blocks)

    def _store_schedule(self, blocks: list[Block] | tuple[Block, ...]) -> bool:
        try:
            write_schedule(self.config_path, blocks)
            self.config_mtime = self.config_path.stat().st_mtime
        except (OSError, ValueError) as exc:
            self._show_note(f"Could not save the schedule ({exc}).")
            return False
        self.settings.schedule = tuple(blocks)
        self._follow_schedule(datetime.now())
        self._fill_schedule()
        return True

    def _schedule_create(self, day: str, start: int, end: int) -> None:
        self._schedule_dialog(day, start, end, None)

    def _schedule_edit(self, index: int) -> None:
        if index < 0 or index >= len(self.settings.schedule):
            return
        block = self.settings.schedule[index]
        start = parse_clock(block.start)
        end = parse_clock(block.end, end=True)
        if start is None or end is None:
            return
        self._schedule_dialog(block.days[0], start, end, block)

    def _schedule_resize(self, index: int, start: int, end: int) -> None:
        if index < 0 or index >= len(self.settings.schedule):
            return
        block = self.settings.schedule[index]
        self._schedule_dialog(block.days[0], start, end, block)

    def _schedule_dialog(self, day: str, start: int, end: int, existing: Block | None) -> None:
        box, frame, surfaces = self._window("Schedule")
        when_row = tk.Frame(frame, bg=self._background())
        when_row.pack(anchor="w")
        surfaces.append(when_row)
        when = _label(when_row, DAY_NAMES[day], self._background(), TEXT, 10, bold=True)
        when.pack(side="left")
        surfaces.append(when)
        start_time = self._field(when_row, 8)
        start_time.pack(side="left", padx=(10, 0))
        start_time.insert(0, typed_clock(start))
        dash = _label(when_row, "–", self._background(), TEXT, 10)
        dash.pack(side="left", padx=6)
        surfaces.append(dash)
        end_time = self._field(when_row, 8)
        end_time.pack(side="left")
        end_time.insert(0, typed_clock(end, end=True))
        name_row = tk.Frame(frame, bg=self._background())
        name_row.pack(anchor="w", fill="x", pady=(10, 0))
        surfaces.append(name_row)
        name_label = _label(name_row, "Name", self._background(), TEXT, 9)
        name_label.pack(side="left")
        surfaces.append(name_label)
        block_name = self._field(name_row, 28)
        block_name.pack(side="left", padx=(8, 0))
        if existing is not None and existing.name:
            block_name.insert(0, existing.name)
        block_color_index = existing.color if existing is not None else next_block_color(self.settings.schedule)
        chosen = {item: (existing is not None and item in existing.days) or item == day for item in DAYS}
        if existing is None:
            chosen[day] = True
        locked = {"on": bool(existing and existing.locked)}
        letters = ("M", "T", "W", "T", "F", "S", "S")
        day_row = tk.Frame(frame, bg=self._background())
        day_row.pack(fill="x", pady=(12, 0))
        surfaces.append(day_row)
        day_buttons: dict[str, tk.Button] = {}

        def paint_day(item: str) -> None:
            fixed = existing is None and item == day
            day_buttons[item].configure(fg=TEXT if chosen[item] or fixed else MUTED)

        def toggle_day(item: str) -> None:
            if existing is None and item == day:
                return
            chosen[item] = not chosen[item]
            paint_day(item)
            refresh_conflict()

        for key, letter in zip(DAYS, letters):
            button = tk.Button(
                day_row,
                text=letter,
                command=lambda key=key: toggle_day(key),
                bg=self._background(),
                fg=TEXT if chosen[key] else MUTED,
                activebackground=self._background(),
                activeforeground=TEXT,
                relief="flat",
                bd=0,
                padx=6,
                font=("Tahoma", 11, "bold"),
                cursor="hand2",
                highlightthickness=0,
            )
            button.pack(side="left")
            day_buttons[key] = button
            surfaces.append(button)

        lock_button = tk.Button(
            day_row,
            text="Locked",
            bg=self._background(),
            fg=TEXT if locked["on"] else MUTED,
            activebackground=self._background(),
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=8,
            font=("Tahoma", 10, "bold"),
            cursor="hand2",
            highlightthickness=0,
        )
        lock_button.pack(side="right")
        surfaces.append(lock_button)
        ratio = self._field(day_row, 8)
        ratio.pack(side="right", padx=(0, 8))
        self._arm_hint(ratio, "W:R")
        ratio_name = _label(day_row, "W:R", self._background(), TEXT, 9)
        ratio_name.pack(side="right", padx=(0, 6))
        surfaces.append(ratio_name)
        if existing is not None and existing.ratio:
            ratio.insert(0, existing.ratio)
        elif existing is None:
            ratio.insert(0, format_ratio(self.settings.work_minutes, self.settings.reward_minutes))
        else:
            self._show_hint(ratio)

        def toggle_lock() -> None:
            locked["on"] = not locked["on"]
            lock_button.configure(fg=TEXT if locked["on"] else MUTED)
            if locked["on"]:
                if not self._entry_value(ratio):
                    self._show_hint(ratio)
                ratio.configure(state="disabled")
            else:
                ratio.configure(state="normal")

        lock_button.configure(command=toggle_lock)
        if locked["on"]:
            ratio.configure(state="disabled")

        lockdown = {"on": bool(existing and existing.lockdown)}
        lockdown_row = tk.Frame(frame, bg=self._background())
        lockdown_row.pack(fill="x", pady=(10, 0))
        surfaces.append(lockdown_row)
        lockdown_flag = tk.BooleanVar(value=lockdown["on"])

        def toggle_lockdown() -> None:
            lockdown["on"] = bool(lockdown_flag.get())

        lockdown_button = tk.Checkbutton(
            lockdown_row,
            text="Lockdown",
            variable=lockdown_flag,
            command=toggle_lockdown,
            bg=self._background(),
            fg=TEXT,
            activebackground=self._background(),
            activeforeground=TEXT,
            selectcolor=CARD,
            font=("Tahoma", 9),
            highlightthickness=0,
            bd=0,
        )
        lockdown_button.pack(side="left")
        surfaces.append(lockdown_button)
        lockdown_note = _label(
            frame,
            "Unproductive programs stay closed during these hours, and new ones cannot be added.",
            self._background(),
            MUTED,
            8,
            wrap=380,
        )
        lockdown_note.pack(anchor="w", pady=(2, 0))
        surfaces.append(lockdown_note)

        password = None
        if not self.unlocked:
            password_label = _label(frame, "Password", self._background(), MUTED, 9)
            password_label.pack(anchor="w", pady=(12, 2))
            surfaces.append(password_label)
            password = self._field(frame, 24, secret=True)
            password.pack(anchor="w", ipady=4)
        warning = _label(frame, "", self._background(), AMBER, 9, wrap=360)
        warning.pack(anchor="w", pady=(8, 0))
        surfaces.append(warning)
        error = _label(frame, "", self._background(), RED, 9, wrap=360)
        error.pack(anchor="w", pady=(4, 0))
        surfaces.append(error)
        actions = tk.Frame(frame, bg=self._background())
        actions.pack(fill="x", pady=(12, 0))
        surfaces.append(actions)

        def selected_days() -> list[str]:
            return [item for item in DAYS if chosen[item]]

        def typed_range() -> tuple[int, int] | None:
            start_minutes = parse_typed_clock(start_time.get())
            end_minutes = parse_typed_clock(end_time.get(), end=True)
            if start_minutes is None or end_minutes is None or start_minutes == end_minutes:
                return None
            return start_minutes, end_minutes

        def draft():
            span = typed_range()
            if span is None:
                return None
            return make_block(
                selected_days(),
                format_clock(span[0]),
                format_clock(span[1]),
                locked["on"],
                "" if locked["on"] else self._entry_value(ratio),
                lockdown["on"],
                block_name.get(),
                block_color_index,
            )

        def refresh_conflict() -> None:
            for child in actions.winfo_children():
                child.destroy()
            span = typed_range()
            probe = (
                make_block(selected_days(), format_clock(span[0]), format_clock(span[1]), False, "1:1")
                if span is not None
                else None
            )
            hits = overlapping_days(self.settings.schedule, probe, existing) if probe is not None else ()
            if hits:
                names = [DAY_NAMES[item] for item in hits]
                if len(names) == 1:
                    warning.configure(text=f"{names[0]} already has a schedule then.")
                else:
                    warning.configure(text=f"{', '.join(names[:-1])} and {names[-1]} already have a schedule then.")
                self._dialog_button(actions, "Replace", lambda: save("replace")).pack(side="right")
                self._dialog_button(actions, "Overwrite", lambda: save("overwrite")).pack(side="right", padx=(0, 8))
            else:
                warning.configure(text="")
                self._dialog_button(actions, "OK", lambda: save("overwrite")).pack(side="right")
            if existing is not None:
                self._dialog_button(actions, "Remove", remove).pack(side="left")

        def typed_password() -> str:
            return "" if password is None else password.get()

        def save(mode: str) -> None:
            if not password_accepted(self.unlocked, typed_password(), self.settings.reset_password):
                error.configure(text="That password is wrong.")
                return
            if not selected_days():
                error.configure(text="Pick at least one day.")
                return
            if typed_range() is None:
                error.configure(text="Enter a time like 6:01am.")
                return
            block = draft()
            if block is None:
                error.configure(text="Enter a W:R like 2.5:1.")
                return
            if self._store_schedule(place_block(self.settings.schedule, block, mode, existing)):
                box.destroy()

        def remove() -> None:
            if existing is None:
                return
            if not password_accepted(self.unlocked, typed_password(), self.settings.reset_password):
                error.configure(text="That password is wrong.")
                return
            if self._store_schedule([item for item in self.settings.schedule if item != existing]):
                box.destroy()

        def on_return(_event=None) -> None:
            span = typed_range()
            if span is None:
                save("overwrite")
                return
            probe = make_block(selected_days(), format_clock(span[0]), format_clock(span[1]), False, "1:1")
            hits = overlapping_days(self.settings.schedule, probe, existing) if probe is not None else ()
            if not hits:
                save("overwrite")

        def times_changed(_event=None) -> None:
            error.configure(text="")
            refresh_conflict()

        start_time.bind("<KeyRelease>", times_changed)
        end_time.bind("<KeyRelease>", times_changed)
        refresh_conflict()
        box.bind("<Return>", on_return)
        box.bind("<Escape>", lambda _event: box.destroy())
        self._place_window(box, 420)
        try:
            box.grab_set()
        except tk.TclError:
            pass
        start_time.focus_set()
        start_time.selection_range(0, "end")
        self.root.wait_window(box)
        self._fill_schedule()

    def _background(self) -> str:
        return self.custom_colors.get("background", BG)

    def _window(self, title: str) -> tuple[tk.Toplevel, tk.Frame, list]:
        color = self._background()
        box = tk.Toplevel(self.root)
        box.title(title)
        box.configure(bg=color)
        box.transient(self.root)
        box.attributes("-topmost", True)
        box.resizable(False, False)
        try:
            box.iconphoto(False, self._icon_image)
        except Exception:
            pass
        frame = tk.Frame(box, bg=color)
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        surfaces: list = [box, frame]
        self._dialog_bg.extend(surfaces)

        def forget(event) -> None:
            if event.widget is box:
                self._dialog_bg = [widget for widget in self._dialog_bg if widget not in surfaces]

        box.bind("<Destroy>", forget)
        box.bind("<Map>", lambda _event: self._paint_title_bar(box), add="+")
        return box, frame, surfaces

    def _arm_hint(self, entry: tk.Entry, hint: str) -> None:
        entry._hint = hint
        entry._hint_on = False

        def show(_event=None) -> None:
            self._show_hint(entry)

        def hide(_event=None) -> None:
            if not getattr(entry, "_hint_on", False):
                return
            state = str(entry.cget("state"))
            if state == "disabled":
                entry.configure(state="normal")
            entry._hint_on = False
            entry.delete(0, "end")
            entry.configure(fg=TEXT)
            if state == "disabled":
                entry.configure(state="disabled")

        entry.bind("<FocusIn>", hide, add="+")
        entry.bind("<FocusOut>", show, add="+")

    def _show_hint(self, entry: tk.Entry) -> None:
        if getattr(entry, "_hint_on", False) or entry.get():
            return
        hint = getattr(entry, "_hint", "")
        if not hint:
            return
        state = str(entry.cget("state"))
        if state == "disabled":
            entry.configure(state="normal")
        entry._hint_on = True
        entry.insert(0, hint)
        entry.configure(fg=MUTED)
        if state == "disabled":
            entry.configure(state="disabled")

    def _entry_value(self, entry: tk.Entry) -> str:
        if getattr(entry, "_hint_on", False):
            return ""
        return entry.get()

    def _field(self, parent, width: int, secret: bool = False) -> tk.Entry:
        color = self._background()
        return tk.Entry(
            parent,
            show="*" if secret else "",
            bg=CARD,
            fg=TEXT,
            insertbackground=TEXT,
            relief="sunken",
            bd=1,
            highlightthickness=0,
            font=(FONT, 9),
            width=width,
        )

    def _dialog_button(self, parent, text: str, command) -> tk.Button:
        color = self._background()
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=color,
            fg=TEXT,
            activebackground="#e5e5e5",
            activeforeground=TEXT,
            relief="raised",
            bd=2,
            padx=10,
            pady=1,
            font=(FONT, 8),
            cursor="hand2",
            highlightthickness=0,
        )
        self._dialog_bg.append(button)
        return button

    def _place_window(self, box: tk.Toplevel, width: int) -> None:
        box.update_idletasks()
        height = box.winfo_reqheight()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + 80
        box.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

    def _show_note(self, text: str) -> None:
        self.user_note = text
        fg = RED if "wrong" in text.casefold() or "could not" in text.casefold() else TEXT
        self._set_notice(text, fg)

    def _set_notice(self, text: str, fg: str) -> None:
        try:
            if not text:
                if self.notice.winfo_ismapped():
                    self.notice.pack_forget()
                return
            _update(self.notice, text=text, fg=self._chosen(self.notice, "fg", fg))
            if not self.notice.winfo_ismapped():
                self.notice.pack(fill="x", padx=16, pady=(4, 10))
        except tk.TclError:
            return

    def show_about(self, *_args) -> None:
        try:
            if self.root.state() != "normal":
                self.show()
        except tk.TclError:
            return
        existing = getattr(self, "_about", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass
        box, frame, surfaces = self._window("About Gordian Knot")
        self._about = box
        message = _label(
            frame,
            about_message(self.settings.work_minutes, self.settings.reward_minutes),
            self._background(),
            TEXT,
            10,
            wrap=420,
        )
        message.pack(anchor="w")
        surfaces.append(message)
        self._dialog_button(frame, "Close", box.destroy).pack(anchor="e", pady=(12, 0))
        box.bind("<Escape>", lambda _event: box.destroy())
        self._place_window(box, 460)
        box.focus_force()

    def _prompt(
        self,
        title: str,
        message: str,
        fields: list[tuple[str, str]],
        secret: set[str] | None = None,
        initial: dict | None = None,
        placeholder: dict | None = None,
    ) -> dict | None:
        box, frame, surfaces = self._window(title)
        result: dict | None = None
        entries: dict[str, tk.Entry] = {}
        color = self._background()
        if message:
            text = _label(frame, message, color, TEXT, 10, wrap=360)
            text.pack(anchor="w")
            surfaces.append(text)
        for key, label in fields:
            caption = _label(frame, label, color, MUTED, 9)
            caption.pack(anchor="w", pady=(10, 2))
            surfaces.append(caption)
            entry = self._field(frame, 28, secret=secret is None or key in secret)
            entry.pack(anchor="w", ipady=4)
            if placeholder and key in placeholder:
                self._arm_hint(entry, placeholder[key])
            if initial and key in initial and str(initial[key]):
                entry.insert(0, str(initial[key]))
            elif placeholder and key in placeholder:
                self._show_hint(entry)
            entries[key] = entry
        error = _label(frame, "", color, RED, 9, wrap=360)
        error.pack(anchor="w", pady=(8, 0))
        surfaces.append(error)

        def accept(_event=None) -> None:
            nonlocal result
            values = {key: self._entry_value(widget) for key, widget in entries.items()}
            if any(not value for value in values.values()):
                error.configure(text="Fill in every field.")
                return
            result = values
            box.destroy()

        def cancel(_event=None) -> None:
            box.destroy()

        actions = tk.Frame(frame, bg=color)
        actions.pack(fill="x", pady=(12, 0))
        surfaces.append(actions)
        self._dialog_button(actions, "Cancel", cancel).pack(side="right")
        self._dialog_button(actions, "OK", accept).pack(side="right", padx=(0, 8))
        box.bind("<Return>", accept)
        box.bind("<Escape>", cancel)
        first = next(iter(entries.values()))
        self._place_window(box, 400)
        try:
            box.grab_set()
        except tk.TclError:
            pass
        first.focus_set()
        self.root.wait_window(box)
        return result

    def _paint_title_bar(self, widget=None) -> None:
        target = widget or self.root
        try:
            if not target.winfo_exists():
                return
            fill = ""
            if self.night:
                try:
                    fill = str(target.cget("bg"))
                except tk.TclError:
                    fill = BG
            set_title_theme(int(target.winfo_id()), self.night, fill)
        except (tk.TclError, Exception):
            return

    def _chosen(self, widget, option: str, automatic: str) -> str:
        return chosen_color(self.custom_colors, f"{widget}|{option}", automatic)

    def _edit_warning_sound(self) -> None:
        box, frame, surfaces = self._window("Warning sound")
        caption = _label(frame, sound_label(self.warning_sound), self._background(), TEXT, 10, bold=True)
        caption.pack(anchor="w")
        surfaces.append(caption)
        hint = _label(
            frame,
            "The Windows beep plays when a warning appears. Choose a .wav file to play that instead.",
            self._background(),
            MUTED,
            9,
            wrap=360,
        )
        hint.pack(anchor="w", pady=(8, 0))
        surfaces.append(hint)
        error = _label(frame, "", self._background(), RED, 9, wrap=360)
        error.pack(anchor="w", pady=(8, 0))
        surfaces.append(error)

        def remember(path: str) -> None:
            try:
                write_warning_sound(self.config_path, path)
                self.config_mtime = self.config_path.stat().st_mtime
            except (OSError, ValueError) as exc:
                error.configure(text=f"Could not save the sound ({exc}).")
                return
            self.warning_sound = path
            caption.configure(text=sound_label(path))
            error.configure(text="")
            play_warning(path)

        def choose() -> None:
            folder = os.path.dirname(self.warning_sound) if self.warning_sound else ""
            options = {"parent": box, "title": "Warning sound", "filetypes": [("Wave audio", "*.wav")]}
            if folder and os.path.isdir(folder):
                options["initialdir"] = folder
            path = filedialog.askopenfilename(**options)
            if not path:
                return
            if not path.casefold().endswith(".wav"):
                error.configure(text="Choose a .wav file.")
                return
            remember(path)

        actions = tk.Frame(frame, bg=self._background())
        actions.pack(fill="x", pady=(12, 0))
        surfaces.append(actions)
        self._dialog_button(actions, "Play", lambda: play_warning(self.warning_sound)).pack(side="left")
        self._dialog_button(actions, "Choose a sound", choose).pack(side="right")
        self._dialog_button(actions, "Windows beep", lambda: remember("")).pack(side="right", padx=(0, 8))
        box.bind("<Escape>", lambda _event: box.destroy())
        self._place_window(box, 420)
        self.root.wait_window(box)

    def _debug_hotkey(self, event) -> str | None:
        if not (event.state & 0x0001):
            return None
        if event.keysym.lower() not in {"grave", "asciitilde", "quoteleft"} and event.keycode != 192:
            return None
        self._toggle_debug()
        return "break"

    def _toggle_debug(self) -> None:
        self.debug_mode = not self.debug_mode
        if self.debug_mode:
            self._set_notice(
                "Colors are on. Click text to change its color and font. Choose Colors again to leave.",
                TEXT,
            )
        else:
            self._set_notice(self.notice_text or self.user_note, RED if self.notice_text else TEXT)

    def _tag_for_debug(self, event) -> None:
        widget = event.widget
        try:
            tags = widget.bindtags()
            if tags and tags[0] != "GordianDebug":
                widget.bindtags(("GordianDebug",) + tuple(tags))
        except tk.TclError:
            return

    def _debug_click(self, event):
        if not self.debug_mode:
            return None
        widget = event.widget
        try:
            if widget.winfo_toplevel() is not self.root:
                return None
        except tk.TclError:
            return None
        self._open_color_editor(widget, event.x, event.y)
        return "break"

    def _open_color_editor(self, widget, x: int, y: int) -> None:
        target = self._color_target(widget, x, y)
        if target is None:
            return
        if self._color_dialog is not None:
            try:
                self._color_dialog.destroy()
            except tk.TclError:
                pass
            self._color_dialog = None
        self._color_dialog = self._build_color_dialog(target)

    def _color_target(self, widget, x: int, y: int) -> dict | None:
        if widget is self.chart:
            name = self.chart.color_target(x, y)
            return {
                "title": f"Chart {name.replace('_', ' ')}",
                "choices": [("color", "Color", self._chart_color(name))],
            "apply": lambda _option, color, name=name: self._apply_chart_color(name, color),
            "font": self._spec_from_face(self.chart.face),
            "apply_font": self._apply_chart_font,
        }
        if widget is self.app_bars:
            name = self.app_bars.color_target(x, y)
            labels = {"bar": "Bar", "text": "Name", "muted": "Time", "canvas": "Background"}
            return {
                "title": f"Apps {labels.get(name, name).lower()}",
                "choices": [("color", labels.get(name, "Color"), self._apps_color(name))],
            "apply": lambda _option, color, name=name: self._apply_apps_color(name, color),
            "font": self._spec_from_face(self.app_bars.face),
            "apply_font": self._apply_apps_font,
        }
        if widget in self._bg_widgets:
            return {
                "title": "Background",
                "choices": [("bg", "Background", self._widget_hex(self.root, "bg"))],
                "apply": lambda _option, color: self._commit_background(color),
            }
        order = ("bg", "fg") if widget.winfo_class() in {"Frame", "Button", "Canvas", "Scrollbar", "Radiobutton"} else ("fg", "bg")
        labels = {"fg": "Text", "bg": "Fill"}
        choices = []
        for option in order:
            try:
                widget.cget(option)
            except tk.TclError:
                continue
            choices.append((option, labels[option], self._widget_hex(widget, option)))
        if not choices:
            return None
        return {
            "title": "Text" if self._font_spec(widget) else "Color",
            "choices": choices,
            "font": self._font_spec(widget),
            "apply": lambda option, color, widget=widget: self._apply_widget_color(widget, option, color),
            "apply_font": lambda spec, widget=widget: self._apply_widget_font(widget, spec),
        }

    def _widget_hex(self, widget, option: str) -> str:
        try:
            current = str(widget.cget(option))
        except tk.TclError:
            return "#888888"
        parsed = normalize_hex(current)
        if parsed:
            return parsed
        try:
            red, green, blue = widget.winfo_rgb(current)
        except tk.TclError:
            return "#888888"
        return rgb_to_hex(red // 256, green // 256, blue // 256)

    def _chart_color(self, name: str) -> str:
        if name in self.chart.colors:
            return self.chart.colors[name]
        named = {
            "shutdown": self.chart.shutdown_color,
            "sleep": self.chart.sleep_color,
            "clock": self.chart.clock_color,
            "lost": self.chart.lost_color,
            "reset": self.chart.reset_color,
            "text": self.chart.text_color,
            "muted": self.chart.muted_color,
            "grid": self.chart.grid_color,
            "plot": self.chart.plot_bg,
        }
        return named.get(name, "#888888")

    def _apps_color(self, name: str) -> str:
        named = {
            "bar": self.app_bars.bar_color,
            "text": self.app_bars.text_color,
            "muted": self.app_bars.muted_color,
            "canvas": self.app_bars.canvas_bg,
        }
        return named.get(name, self.app_bars.canvas_bg)

    def _apply_chart_color(self, name: str, color: str) -> None:
        self.chart.set_palette_color(name, color)
        self._save_color(f"chart:{name}", color)

    def _apply_apps_color(self, name: str, color: str) -> None:
        self.app_bars.set_palette_color(name, color)
        self._save_color(f"apps:{name}", color)

    def _commit_background(self, color: str) -> None:
        self._paint_background(color)
        self._save_color("background", color)

    def _apply_widget_color(self, widget, option: str, color: str) -> None:
        self._paint_option(widget, option, color)
        self._save_color(f"{widget}|{option}", color)

    def _paint_background(self, color: str) -> None:
        widgets = list(self._bg_widgets) + list(self._dialog_bg)
        for widget in widgets:
            try:
                if widget.winfo_exists():
                    self._paint_option(widget, "bg", color)
            except tk.TclError:
                continue

    def _paint_option(self, widget, option: str, color: str) -> None:
        try:
            widget.configure(**{option: color})
        except tk.TclError:
            return
        extra = "activebackground" if option == "bg" else "activeforeground"
        try:
            widget.configure(**{extra: color})
        except tk.TclError:
            return

    def _save_color(self, key: str, color: str) -> None:
        self.custom_colors[key] = color
        try:
            write_colors(self.config_path, self.custom_colors)
            self.config_mtime = self.config_path.stat().st_mtime
        except (OSError, ValueError) as exc:
            self._show_note(f"Could not save the color ({exc}).")

    def _spec_from_face(self, face) -> dict:
        style = face[2] if len(face) > 2 else ""
        return {
            "family": face[0],
            "size": int(face[1]) if len(face) > 1 else 8,
            "bold": "bold" in style,
            "italic": "italic" in style,
            "underline": "underline" in style,
        }

    def _apply_chart_font(self, spec: dict) -> None:
        self.chart.face = self._font_value(spec)
        self.chart._legend_fonts = {}
        self.chart._draw()
        self._store_font("chart|font", spec)

    def _apply_apps_font(self, spec: dict) -> None:
        self.app_bars.face = self._font_value(spec)
        self.app_bars._draw()
        self._store_font("apps|font", spec)

    def _store_font(self, key: str, spec: dict) -> None:
        self.saved_fonts[key] = json.dumps(spec)
        try:
            write_fonts(self.config_path, self.saved_fonts)
            self.config_mtime = self.config_path.stat().st_mtime
        except (OSError, ValueError) as exc:
            self._show_note(f"Could not save the font ({exc}).")

    def _font_spec(self, widget) -> dict | None:
        try:
            raw = widget.cget("font")
        except tk.TclError:
            return None
        try:
            face = tkfont.Font(font=raw)
            actual = face.actual()
        except tk.TclError:
            return None
        size = int(actual.get("size") or 9)
        return {
            "family": actual.get("family") or FONT,
            "size": abs(size),
            "bold": actual.get("weight") == "bold",
            "italic": actual.get("slant") == "italic",
            "underline": bool(actual.get("underline")),
        }

    def _font_value(self, spec: dict):
        styles = []
        if spec.get("bold"):
            styles.append("bold")
        if spec.get("italic"):
            styles.append("italic")
        if spec.get("underline"):
            styles.append("underline")
        size = int(spec.get("size") or 9)
        if styles:
            return (spec.get("family") or FONT, size, " ".join(styles))
        return (spec.get("family") or FONT, size)

    def _saved_font(self, widget):
        raw = self.saved_fonts.get(str(widget))
        if not raw:
            return None
        try:
            spec = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(spec, dict):
            return None
        return self._font_value(spec)

    def _apply_widget_font(self, widget, spec: dict) -> None:
        value = self._font_value(spec)
        try:
            widget.configure(font=value)
        except tk.TclError:
            return
        self._store_font(str(widget), spec)

    def _apply_saved_fonts(self) -> None:
        for key, raw in self.saved_fonts.items():
            try:
                spec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(spec, dict):
                continue
            if key == "chart|font":
                self.chart.face = self._font_value(spec)
                continue
            if key == "apps|font":
                self.app_bars.face = self._font_value(spec)
                continue
            try:
                widget = self.root.nametowidget(key)
                widget.configure(font=self._font_value(spec))
            except (tk.TclError, KeyError):
                continue

    def _apply_saved_colors(self) -> None:
        for key, color in self.custom_colors.items():
            if key == "background":
                self._paint_background(color)
            elif key.startswith("chart:"):
                self.chart.set_palette_color(key.split(":", 1)[1], color)
            elif key.startswith("apps:"):
                self.app_bars.set_palette_color(key.split(":", 1)[1], color)
            elif "|" in key:
                path, option = key.rsplit("|", 1)
                try:
                    widget = self.root.nametowidget(path)
                except tk.TclError:
                    continue
                self._paint_option(widget, option, color)

    def _build_color_dialog(self, target: dict) -> tk.Toplevel:
        box = tk.Toplevel(self.root)
        box.title(target["title"])
        surface = self._background()
        box.configure(bg=surface)
        box.transient(self.root)
        box.attributes("-topmost", True)
        box.resizable(False, False)
        size = 180
        choices = target["choices"]
        selected = {"option": choices[0][0]}
        initial = hex_to_rgb(choices[0][2]) or (0, 128, 0)
        hue, saturation, value = rgb_to_hsv(*initial)
        state = {"h": hue, "s": saturation, "v": value, "lock": False}
        images: dict[str, ImageTk.PhotoImage] = {}

        body = tk.Frame(box, bg=surface)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        choice_row = tk.Frame(body, bg=surface)
        choice_buttons: dict[str, tk.Button] = {}
        if len(choices) > 1:
            choice_row.pack(fill="x")

        square = tk.Canvas(body, width=size, height=size, highlightthickness=0, bd=0, bg=surface)
        square.pack(pady=(8, 8))
        strip = tk.Canvas(body, width=size, height=16, highlightthickness=0, bd=0, bg=surface)
        strip.pack()
        row = tk.Frame(body, bg=surface)
        row.pack(fill="x", pady=(10, 0))
        preview = tk.Frame(row, width=28, height=28, bg=choices[0][2], highlightbackground=LINE, highlightthickness=1)
        preview.pack(side="left")
        preview.pack_propagate(False)
        hex_var = tk.StringVar(value=choices[0][2])
        entry = tk.Entry(
            row,
            textvariable=hex_var,
            bg=surface,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=LINE,
            font=("Consolas", 12),
            width=10,
        )
        entry.pack(side="left", padx=(8, 0), ipady=4)

        def current_hex() -> str:
            return rgb_to_hex(*hsv_to_rgb(state["h"], state["s"], state["v"]))

        def redraw(update_entry: bool = True) -> None:
            pixels = []
            for py in range(size):
                level = 1 - py / (size - 1)
                for px in range(size):
                    pixels.append(hsv_to_rgb(state["h"], px / (size - 1), level))
            field = Image.new("RGB", (size, size))
            field.putdata(pixels)
            images["field"] = ImageTk.PhotoImage(field)
            square.delete("all")
            square.create_image(0, 0, image=images["field"], anchor="nw")
            mark_x = state["s"] * (size - 1)
            mark_y = (1 - state["v"]) * (size - 1)
            square.create_oval(mark_x - 5, mark_y - 5, mark_x + 5, mark_y + 5, outline="white", width=2)

            band_pixels = []
            for _py in range(16):
                for px in range(size):
                    band_pixels.append(hsv_to_rgb(px / (size - 1) * 360, 1, 1))
            band = Image.new("RGB", (size, 16))
            band.putdata(band_pixels)
            images["band"] = ImageTk.PhotoImage(band)
            strip.delete("all")
            strip.create_image(0, 0, image=images["band"], anchor="nw")
            hue_x = state["h"] / 360 * (size - 1)
            strip.create_line(hue_x, 0, hue_x, 16, fill="white", width=2)

            color = current_hex()
            preview.configure(bg=color)
            if update_entry:
                state["lock"] = True
                hex_var.set(color)
                state["lock"] = False
                entry.configure(fg=TEXT)

        def pick_square(event) -> None:
            state["s"] = min(1.0, max(0.0, event.x / (size - 1)))
            state["v"] = min(1.0, max(0.0, 1 - event.y / (size - 1)))
            redraw()

        def pick_hue(event) -> None:
            state["h"] = min(359.999, max(0.0, event.x / (size - 1) * 360))
            redraw()

        def on_hex(*_args) -> None:
            if state["lock"]:
                return
            parsed = normalize_hex(hex_var.get())
            if parsed is None:
                entry.configure(fg=RED)
                return
            entry.configure(fg=TEXT)
            red, green, blue = hex_to_rgb(parsed) or (0, 0, 0)
            state["h"], state["s"], state["v"] = rgb_to_hsv(red, green, blue)
            redraw(update_entry=False)

        def select_choice(option: str, color: str) -> None:
            selected["option"] = option
            red, green, blue = hex_to_rgb(color) or (255, 255, 255)
            state["h"], state["s"], state["v"] = rgb_to_hsv(red, green, blue)
            for key, button in choice_buttons.items():
                button.configure(fg=TEXT if key == option else MUTED)
            redraw()

        font_state = target.get("font")
        if font_state:
            families = list(FONT_FAMILIES)
            if font_state["family"] not in families:
                families.insert(0, font_state["family"])
            font_row = tk.Frame(body, bg=surface)
            font_row.pack(fill="x", pady=(12, 0))
            family_var = tk.StringVar(value=font_state["family"])
            family_menu = tk.OptionMenu(font_row, family_var, *families)
            family_menu.configure(bg=surface, fg=TEXT, font=(FONT, 8), highlightthickness=0)
            family_menu.pack(side="left")
            size_var = tk.Spinbox(font_row, from_=6, to=48, width=4, font=(FONT, 9), bg=CARD, fg=TEXT)
            size_var.delete(0, "end")
            size_var.insert(0, str(font_state["size"]))
            size_var.pack(side="left", padx=(8, 0))
            style_row = tk.Frame(body, bg=surface)
            style_row.pack(fill="x", pady=(4, 0))
            bold_var = tk.BooleanVar(value=font_state["bold"])
            italic_var = tk.BooleanVar(value=font_state["italic"])
            underline_var = tk.BooleanVar(value=font_state["underline"])
            for caption, variable in (("Bold", bold_var), ("Italic", italic_var), ("Underline", underline_var)):
                tk.Checkbutton(
                    style_row,
                    text=caption,
                    variable=variable,
                    bg=surface,
                    fg=TEXT,
                    selectcolor=CARD,
                    activebackground=surface,
                    font=(FONT, 8),
                    highlightthickness=0,
                ).pack(side="left", padx=(0, 8))

        def accept() -> None:
            color = normalize_hex(hex_var.get()) or current_hex()
            if normalize_hex(color) is None:
                entry.configure(fg=RED)
                return
            target["apply"](selected["option"], color)
            if font_state and target.get("apply_font"):
                try:
                    size = int(float(size_var.get()))
                except ValueError:
                    size = font_state["size"]
                target["apply_font"](
                    {
                        "family": family_var.get(),
                        "size": max(6, min(48, size)),
                        "bold": bool(bold_var.get()),
                        "italic": bool(italic_var.get()),
                        "underline": bool(underline_var.get()),
                    }
                )
            box.destroy()

        if len(choices) > 1:
            choice_row.pack(fill="x")
            for option, label, color in choices:
                button = tk.Button(
                    choice_row,
                    text=label,
                    command=lambda option=option, color=color: select_choice(option, color),
                    bg=surface,
                    fg=TEXT if option == selected["option"] else MUTED,
                    activebackground=surface,
                    activeforeground=TEXT,
                    relief="flat",
                    bd=0,
                    padx=8,
                    font=("Tahoma", 10, "bold"),
                    cursor="hand2",
                    highlightthickness=0,
                )
                button.pack(side="left")
                choice_buttons[option] = button
        square.bind("<Button-1>", pick_square)
        square.bind("<B1-Motion>", pick_square)
        strip.bind("<Button-1>", pick_hue)
        strip.bind("<B1-Motion>", pick_hue)
        hex_var.trace_add("write", on_hex)
        actions = tk.Frame(body, bg=surface)
        actions.pack(fill="x", pady=(12, 0))
        self._dialog_button(actions, "Cancel", box.destroy).pack(side="right")
        self._dialog_button(actions, "Apply", accept).pack(side="right", padx=(0, 8))
        box.bind("<Escape>", lambda _event: box.destroy())
        box.bind("<Return>", lambda _event: accept())
        self._dialog_bg.extend((box, body, choice_row, row, actions))

        def forget(event) -> None:
            if event.widget is not box:
                return
            if self._color_dialog is box:
                self._color_dialog = None
            self._dialog_bg = [widget for widget in self._dialog_bg if widget.winfo_exists()]

        box.bind("<Destroy>", forget)
        redraw()
        box.update_idletasks()
        width, height = box.winfo_reqwidth(), box.winfo_reqheight()
        origin_x = min(self.root.winfo_pointerx() + 12, self.root.winfo_screenwidth() - width - 8)
        origin_y = min(self.root.winfo_pointery() + 12, self.root.winfo_screenheight() - height - 8)
        box.geometry(f"+{max(0, origin_x)}+{max(0, origin_y)}")
        entry.focus_set()
        return box

    def show(self, *_args) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(250, self._clear_topmost)
        self.root.focus_force()

    def _clear_topmost(self) -> None:
        if self.running:
            try:
                self.root.attributes("-topmost", False)
            except tk.TclError:
                return

    def hide(self, *_args) -> None:
        if self.icon is None:
            self.shutdown()
            return
        self.root.withdraw()

    def shutdown(self) -> None:
        if not self.running:
            return
        self.running = False
        try:
            self.ledger.save()
        except Exception:
            log_exception("save")
        icon = self.icon
        self.icon = None
        if icon is not None:
            try:
                icon.visible = False
                icon.stop()
            except Exception:
                log_exception("tray stop")
        try:
            self.root.destroy()
        except tk.TclError:
            return

    def request_show(self, *_args) -> None:
        self.root.after(0, self.show)

    def request_about(self, *_args) -> None:
        self.root.after(0, self.show_about)

    def request_toggle(self, *_args) -> None:
        self.root.after(0, self.toggle_pause)

    def request_config(self, *_args) -> None:
        self.root.after(0, self.open_config)

    def request_reset(self, *_args) -> None:
        self.root.after(0, self.reset_time)

    def request_clear_history(self, *_args) -> None:
        self.root.after(0, self.clear_history)

    def request_password(self, *_args) -> None:
        self.root.after(0, self.change_password)

    def request_unlock(self, *_args) -> None:
        self.root.after(0, self.toggle_unlock)

    def request_data(self, *_args) -> None:
        self.root.after(0, self.open_data)

    def request_shutdown(self, *_args) -> None:
        self.root.after(0, self.shutdown)

    def _on_tk_error(self, etype, value, tb) -> None:
        log_exception("window", (etype, value, tb))
        try:
            self._set_notice(
                f"The window hit an error. The watcher is still running. Details: {self.data_path / 'error.log'}",
                RED,
            )
        except Exception:
            return


def _new_unproductive(
    programs: list[tuple[str, str]],
    sites: list[tuple[str, str]],
    reward_processes: list[str],
    reward_sites: list[str],
) -> bool:
    known_programs = {item.casefold() for item in reward_processes}
    known_sites = {item.casefold() for item in reward_sites}
    for name, choice in programs:
        if choice == "unproductive" and name.casefold() not in known_programs:
            return True
    for name, choice in sites:
        folded = name.casefold()
        if choice == "unproductive" and not any(folded in item or item in folded for item in known_sites):
            return True
    return False


def _update(widget, **options) -> None:
    # Re-setting an unchanged option still makes Tk lay out and repaint the widget.
    changed = {key: value for key, value in options.items() if str(widget.cget(key)) != str(value)}
    if changed:
        widget.configure(**changed)


def _label(parent, text: str, bg: str, fg: str, size: int, bold: bool = False, wrap: int = 0) -> tk.Label:
    options = {}
    if wrap:
        options["wraplength"] = wrap
        options["justify"] = "left"
    return tk.Label(
        parent,
        text=text,
        bg=bg,
        fg=fg,
        anchor="w",
        font=("Tahoma", size, "bold" if bold else "normal"),
        **options,
    )


def _handle_exception(etype, value, tb) -> None:
    log_exception("unhandled", (etype, value, tb))


def main() -> None:
    sys.excepthook = _handle_exception
    try:
        from gordian.branding import relaunch_as_branded

        # Become GordianKnot.exe before the mutex, so Task Manager shows the
        # app name instead of Python. The Start menu shortcut is refreshed
        # later in the background so PowerShell cannot freeze startup.
        relaunch_as_branded()
        prepare_windows()
        if not acquire_single_instance():
            alert("Gordian Knot is already running. Look for the window on the taskbar, or the icon in the tray.")
            return
        install_start_menu_soon()
        config_path = project_root() / "config.json"
        if not config_path.exists():
            write_default_config(config_path)
        else:
            ensure_config(config_path)
        notice = ""
        try:
            settings = parse_config(config_path)
        except ValueError as exc:
            settings = default_settings()
            notice = str(exc)
        folder = data_dir()
        ledger, state_notice = Ledger.load(
            folder / "state.json",
            folder / "history.jsonl",
            settings.work_minutes,
            settings.reward_minutes,
        )
        if state_notice:
            notice = f"{notice} {state_notice}".strip()
        App(settings, ledger, config_path, folder, notice).run()
    except Exception:
        log_exception("startup")
        alert(
            "Gordian Knot could not start. "
            "Look for error.log in AppData\\Roaming\\GordianKnot."
        )
