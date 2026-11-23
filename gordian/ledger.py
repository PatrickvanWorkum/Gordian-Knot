"""Today's totals, the open stretch of time, and the files that survive a restart.

earned_bank grows only on an earn tick, at the ratio in effect for that tick.
set_ratio changes the rate used from now on. Passing recompute=True rewrites
the bank from every productive second; the window never does that.

bonus_seconds is free time from milestones. remaining is the bank plus the
bonus, minus spent seconds. Midnight archives the day and clears today's work.
The free-time balance carries over. Reset time clears that balance and leaves
a mark the chart draws as a light blue line.

state.json is the live day. history.jsonl is one summary line per finished
day. days\\YYYY-MM-DD.json is that day's segments, written once, and the chart
reads those files so a finished day stays visible after midnight. Sleep,
shutdown, and a clock set forward are shaded and do not change the totals.
A clock set back is a mark with the lost seconds. Reset time is a mark too.
"""

from __future__ import annotations

import json
import threading
import time
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta
from pathlib import Path

from gordian.paths import atomic_write, log_exception

AWAY_SECONDS = 45.0


def wall_gap_kind(wall_seconds: float, monotonic_seconds: float) -> str | None:
    """How to draw a jump between two ticks.

    lost: the laptop clock moved backward.
    clock: the clock moved forward while this app kept ticking, so that stretch
    was never lived.
    sleep: both clocks jumped, which is the machine sleeping.
    """
    if wall_seconds <= -AWAY_SECONDS:
        return "lost"
    mono = max(0.0, monotonic_seconds)
    if wall_seconds - mono >= AWAY_SECONDS and mono < AWAY_SECONDS:
        return "clock"
    if wall_seconds >= AWAY_SECONDS:
        return "sleep"
    return None
SEGMENT_KEEP = 8000
_saved_cache: dict[str, tuple] = {}


class Ledger:
    def __init__(self, state_path: Path, history_path: Path, work_minutes: float, reward_minutes: float) -> None:
        self.state_path = state_path
        self.history_path = history_path
        self.work_minutes = work_minutes
        self.reward_minutes = reward_minutes
        self.earned_bank = 0.0
        self.bonus_seconds = 0.0
        self.barred: list[str] = []
        self.lockdown = False
        self.date = date.today().isoformat()
        self.productive_seconds = 0.0
        self.spent_seconds = 0.0
        self.closed: list[dict] = []
        self.current: dict | None = None
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._save_number = 0
        self._written_number = 0
        self._pending: tuple[str, int] | None = None
        self._wake = threading.Event()
        self._writer: threading.Thread | None = None

    @property
    def earned_seconds(self) -> float:
        return self.earned_bank + self.bonus_seconds

    def credit_work(self, start: datetime, end: datetime, process: str, title: str, identity: str = "") -> float:
        """Count one finished stretch of work. Used when a pause ends on its own."""
        return self.credit_works([(start, end, process, title, identity)])

    def credit_works(self, pieces) -> float:
        """Count several finished stretches of work and lift them out of a pause."""
        cleaned = []
        total = 0.0
        for start, end, process, title, identity in pieces:
            seconds = round((end - start).total_seconds(), 3)
            if seconds <= 0 or end <= start:
                continue
            cleaned.append((start, end, process, title, identity, seconds))
            total += seconds
        if not cleaned:
            return 0.0
        with self._lock:
            pause = self.current if self.current and self.current.get("kind") == "paused" else None
            if pause is not None:
                self.current = None
            cursor = _parse_stamp(pause.get("start")) if pause else None
            pause_process = (pause or {}).get("process") or "Unknown window"
            pause_title = (pause or {}).get("title") or ""
            for start, end, process, title, identity, seconds in cleaned:
                if cursor is not None and start > cursor:
                    gap = round((start - cursor).total_seconds(), 3)
                    if gap > 0:
                        self.closed.append(
                            {
                                "start": cursor.isoformat(timespec="seconds"),
                                "end": start.isoformat(timespec="seconds"),
                                "kind": "paused",
                                "process": pause_process,
                                "title": pause_title,
                                "identity": "",
                                "seconds": gap,
                            }
                        )
                self.closed.append(
                    {
                        "start": start.isoformat(timespec="seconds"),
                        "end": end.isoformat(timespec="seconds"),
                        "kind": "earn",
                        "process": " ".join((process or "").split())[:300] or "Unknown window",
                        "title": " ".join((title or "").split())[:300],
                        "identity": " ".join((identity or "").split())[:300],
                        "seconds": seconds,
                    }
                )
                self.productive_seconds += seconds
                if self.work_minutes > 0:
                    self.earned_bank += seconds * self.reward_minutes / self.work_minutes
                cursor = end
            self.closed = untangle_segments(self.closed)
            if len(self.closed) > SEGMENT_KEEP:
                del self.closed[:-SEGMENT_KEEP]
            return round(total, 3)

    def add_bonus(self, seconds: float) -> None:
        if seconds <= 0:
            return
        with self._lock:
            self.bonus_seconds += seconds

    @property
    def remaining_seconds(self) -> float:
        return self.earned_seconds - self.spent_seconds

    def set_ratio(self, work_minutes: float, reward_minutes: float, recompute: bool = False) -> None:
        with self._lock:
            self.work_minutes = work_minutes
            self.reward_minutes = reward_minutes
            if recompute:
                if work_minutes > 0:
                    self.earned_bank = self.productive_seconds * reward_minutes / work_minutes
                else:
                    self.earned_bank = 0.0

    def note_away(self, start: datetime, end: datetime, kind: str) -> None:
        if kind not in ("sleep", "shutdown") or end <= start:
            return
        if (end - start).total_seconds() < AWAY_SECONDS:
            return
        with self._lock:
            self._note_away(start, end, kind)

    def note_clock(self, start: datetime, end: datetime) -> None:
        """Shade time the laptop clock inserted. The balance stays put."""
        if end <= start or (end - start).total_seconds() < AWAY_SECONDS:
            return
        with self._lock:
            self._note_away(start, end, "clock")

    def note_lost(self, moment: datetime, seconds: float) -> None:
        """Mark time removed by setting the laptop clock backward."""
        if seconds < AWAY_SECONDS:
            return
        with self._lock:
            self._mark(moment, "lost", seconds, "Lost time")

    def update(
        self,
        now: datetime,
        kind: str,
        process: str,
        title: str,
        delta: float,
        max_delta: float = 5.0,
        identity: str = "",
    ) -> bool:
        with self._lock:
            return self._update(now, kind, process, title, delta, max_delta, identity)

    def snapshot(self) -> dict:
        with self._lock:
            earned = self.earned_seconds
            return {
                "date": self.date,
                "productive_seconds": self.productive_seconds,
                "spent_seconds": self.spent_seconds,
                "earned_seconds": earned,
                "remaining_seconds": earned - self.spent_seconds,
                "closed": [dict(item) for item in self.closed[-30:]],
                "segments": [dict(item) for item in self.closed] + ([dict(self.current)] if self.current else []),
                "current": dict(self.current) if self.current else None,
            }

    def reset(self) -> None:
        with self._lock:
            self.productive_seconds = 0.0
            self.spent_seconds = 0.0
            self.earned_bank = 0.0
            self.bonus_seconds = 0.0
            self.barred = []
            self.lockdown = False
            self.closed = []
            self.current = None
            self._mark(datetime.now(), "reset", 0.0, "Free time reset")

    def save(self) -> None:
        self._write(*self._prepared())

    def save_soon(self) -> None:
        """Hand the save to a writer thread so the tick does not wait on the disk."""
        job = self._prepared()
        with self._pending_lock:
            self._pending = job
            if self._writer is None or not self._writer.is_alive():
                self._writer = threading.Thread(target=self._write_pending, name="gordian-save", daemon=True)
                self._writer.start()
        self._wake.set()

    def _prepared(self) -> tuple[str, int]:
        with self._lock:
            self._save_number += 1
            return state_text(self._payload()), self._save_number

    def _write(self, text: str, number: int) -> None:
        with self._write_lock:
            # A newer synchronous save may already be on disk; never overwrite it with an older one.
            if number <= self._written_number:
                return
            atomic_write(self.state_path, text)
            self._written_number = number

    def _write_pending(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            with self._pending_lock:
                job, self._pending = self._pending, None
            if job is None:
                continue
            try:
                self._write(*job)
            except Exception:
                log_exception("save")

    @classmethod
    def load(
        cls,
        state_path: Path,
        history_path: Path,
        work_minutes: float,
        reward_minutes: float,
    ) -> tuple[Ledger, str | None]:
        ledger = cls(state_path, history_path, work_minutes, reward_minutes)
        backup = state_path.with_name(state_path.name + ".bak")
        if not state_path.exists() and not backup.exists():
            return ledger, None
        try:
            data = _read_state(state_path if state_path.exists() else backup)
        except Exception as first_error:
            data = None
            if state_path.exists() and backup.exists():
                try:
                    data = _read_state(backup)
                except Exception as exc:
                    first_error = exc
            if data is None:
                _park_corrupt(state_path)
                fresh = cls(state_path, history_path, work_minutes, reward_minutes)
                return fresh, f"Saved state could not be read ({first_error}). Started today from zero."
            try:
                atomic_write(state_path, json.dumps(data, indent=2) + "\n")
            except OSError:
                log_exception("state restore")
        try:
            saved_date = _valid_day(str(data.get("date") or ""))
            if saved_date:
                ledger.date = saved_date
            ledger.productive_seconds = _as_float(data.get("productive_seconds"))
            ledger.spent_seconds = _as_float(data.get("spent_seconds"))
            ledger.bonus_seconds = _as_float(data.get("bonus_seconds"))
            barred = data.get("barred") or []
            if isinstance(barred, list):
                ledger.barred = [str(item) for item in barred if str(item).strip()]
            ledger.lockdown = False
            if "earned_seconds" in data:
                ledger.earned_bank = max(0.0, _as_float(data.get("earned_seconds")) - ledger.bonus_seconds)
            elif ledger.work_minutes > 0:
                ledger.earned_bank = ledger.productive_seconds * ledger.reward_minutes / ledger.work_minutes
            closed = data.get("closed") or []
            if isinstance(closed, list):
                ledger.closed = [dict(item) for item in closed if isinstance(item, dict)]
            current = data.get("current")
            if isinstance(current, dict) and _as_float(current.get("seconds")) > 0:
                finished = dict(current)
                if not finished.get("end"):
                    finished["end"] = data.get("updated_at")
                ledger.closed.append(finished)
            ledger.closed = untangle_segments(ledger.closed)[-SEGMENT_KEEP:]
            ledger.current = None
            updated = _parse_stamp(data.get("updated_at"))
            now = datetime.now()
            if updated is not None:
                gap = (now - updated).total_seconds()
                try:
                    if gap >= AWAY_SECONDS:
                        ledger.note_away(updated, now, "shutdown")
                        ledger.save()
                    elif gap <= -AWAY_SECONDS:
                        ledger.note_lost(now, -gap)
                        ledger.save()
                except Exception:
                    log_exception("shutdown gap")
            if ledger.date != date.today().isoformat():
                saved_day = _valid_day(ledger.date)
                today = date.today()
                if saved_day and date.fromisoformat(saved_day) > today:
                    ledger.date = today.isoformat()
                    ledger.save()
                    return ledger, None
                carried = (ledger.earned_bank, ledger.bonus_seconds, ledger.spent_seconds, list(ledger.barred))
                ledger._archive_day()
                fresh = cls(state_path, history_path, work_minutes, reward_minutes)
                fresh.earned_bank, fresh.bonus_seconds, fresh.spent_seconds, fresh.barred = carried
                fresh.save()
                return fresh, None
            return ledger, None
        except Exception as exc:
            _park_corrupt(state_path)
            fresh = cls(state_path, history_path, work_minutes, reward_minutes)
            return fresh, f"Saved state could not be read ({exc}). Started today from zero."

    def _update(
        self,
        now: datetime,
        kind: str,
        process: str,
        title: str,
        delta: float,
        max_delta: float,
        identity: str,
    ) -> bool:
        process = " ".join((process or "").split())[:300] or "Unknown window"
        title = " ".join((title or "").split())[:300]
        identity = " ".join((identity or "").split())[:300]
        boundary = False
        if now.date().isoformat() != self.date:
            previous = _valid_day(self.date)
            # A clock set backward keeps the day already on file. The lost-time mark is separate.
            if previous and now.date() > date.fromisoformat(previous):
                self._rollover(now)
                boundary = True
        holding_pause = (
            kind == "paused"
            and self.current is not None
            and self.current["kind"] == "paused"
        )
        if delta > max_delta and not holding_pause:
            self._close_current(now)
            delta = 0.0
            boundary = True
        elif delta < 0:
            delta = 0.0
        if self._accumulate(now, kind, process, title, delta, identity):
            boundary = True
        return boundary

    def _rollover(self, now: datetime) -> None:
        self._close_current(now)
        self._archive_day()
        self.date = now.date().isoformat()
        self.productive_seconds = 0.0
        self.lockdown = False
        self.closed = []
        self.current = None

    def _archive_day(self) -> None:
        day_earn, day_spend = _kind_totals(self.closed)
        if not (self.productive_seconds or day_spend or self.closed):
            return
        productive = round(day_earn if day_earn > 0 else self.productive_seconds, 3)
        spent = round(day_spend, 3)
        summary = {
            "date": self.date,
            "productive_seconds": productive,
            "spent_seconds": spent,
            "earned_seconds": round(self.earned_seconds, 3),
        }
        _append_history(self.history_path, summary)
        day_path = self.history_path.parent / "days" / f"{self.date}.json"
        if not day_path.exists():
            payload = self._payload()
            payload["productive_seconds"] = productive
            payload["spent_seconds"] = spent
            atomic_write(day_path, json.dumps(payload, indent=2) + "\n")

    def _accumulate(self, now: datetime, kind: str, process: str, title: str, delta: float, identity: str) -> bool:
        holding_pause = kind == "paused" and self.current is not None and self.current["kind"] == "paused"
        same = (
            self.current is not None
            and self.current["kind"] == kind
            and (
                holding_pause
                or (
                    self.current["process"] == process
                    and self.current.get("identity", "") == identity
                )
            )
        )
        started = False
        if not same:
            self._close_current(now)
            self.current = {
                "start": now.isoformat(timespec="seconds"),
                "end": None,
                "kind": kind,
                "process": process,
                "title": title,
                "identity": identity,
                "seconds": 0.0,
            }
            started = True
        elif not holding_pause:
            self.current["title"] = title
        self.current["seconds"] = round(self.current["seconds"] + delta, 3)
        if kind == "earn":
            self.productive_seconds += delta
            # Bank this tick at the current ratio. Later ratio edits do not rewrite it.
            if self.work_minutes > 0:
                self.earned_bank += delta * self.reward_minutes / self.work_minutes
        elif kind == "spend":
            self.spent_seconds += delta
        return started

    def _note_away(self, start: datetime, end: datetime, kind: str) -> None:
        cursor = start
        while cursor.date() < end.date():
            midnight = datetime.combine(cursor.date() + timedelta(days=1), datetime.min.time())
            self._place_away(cursor, midnight, kind)
            self._rollover(midnight)
            cursor = midnight
        self._place_away(cursor, end, kind)

    def _place_away(self, start: datetime, end: datetime, kind: str) -> None:
        seconds = (end - start).total_seconds()
        if seconds <= 0:
            return
        self._close_current(start)
        stamp = start.isoformat(timespec="seconds")
        finish = end.isoformat(timespec="seconds")
        if self.closed:
            last = self.closed[-1]
            if last.get("kind") == kind and last.get("end") == stamp:
                last["end"] = finish
                last["seconds"] = round(float(last.get("seconds") or 0) + seconds, 3)
                return
        self.closed.append(
            {
                "start": stamp,
                "end": finish,
                "kind": kind,
                "process": "Windows",
                "title": {"shutdown": "Shut down", "sleep": "Sleep", "clock": "Missing time"}.get(kind, kind),
                "identity": "",
                "seconds": round(seconds, 3),
            }
        )
        if len(self.closed) > SEGMENT_KEEP:
            del self.closed[:-SEGMENT_KEEP]

    def _mark(self, moment: datetime, kind: str, seconds: float, title: str) -> None:
        stamp = moment.isoformat(timespec="seconds")
        self.closed.append(
            {
                "start": stamp,
                "end": stamp,
                "kind": kind,
                "process": "Windows",
                "title": title,
                "identity": "",
                "seconds": round(max(0.0, seconds), 3),
            }
        )
        if len(self.closed) > SEGMENT_KEEP:
            del self.closed[:-SEGMENT_KEEP]

    def _close_current(self, now: datetime) -> None:
        if not self.current:
            return
        if self.current["seconds"] <= 0:
            self.current = None
            return
        self.current["end"] = now.isoformat(timespec="seconds")
        self.closed.append(self.current)
        if len(self.closed) > SEGMENT_KEEP:
            del self.closed[:-SEGMENT_KEEP]
        self.current = None

    def _payload(self) -> dict:
        return {
            "date": self.date,
            "productive_seconds": round(self.productive_seconds, 3),
            "spent_seconds": round(self.spent_seconds, 3),
            "earned_seconds": round(self.earned_seconds, 3),
            "bonus_seconds": round(self.bonus_seconds, 3),
            "barred": list(self.barred),
            "lockdown": self.lockdown,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "closed": [dict(item) for item in self.closed[-SEGMENT_KEEP:]],
            "current": dict(self.current) if self.current else None,
        }


def clear_saved_history(state_path: Path, history_path: Path) -> None:
    """Remove finished days so the chart matches a fresh install.

    Today's counters are cleared by Ledger.reset. Lists, the schedule, and the
    password live in config.json and are left in place.
    """
    folder = history_path.parent / "days"
    _saved_cache.pop(str(folder), None)
    targets = [
        history_path,
        state_path.with_name(state_path.name + ".bak"),
        state_path.with_name("state.corrupt.json"),
    ]
    if folder.is_dir():
        targets.extend(path for path in folder.iterdir() if path.is_file())
    for path in targets:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise OSError(f"Could not remove {path.name}") from exc


def forget_saved_segments() -> None:
    """Drop the finished-day cache so the next read comes from disk."""
    _saved_cache.clear()


_SAVED_CHECK_SECONDS = 30.0


def read_saved_segments(history_path: Path) -> list[dict]:
    """Segments from finished days, so the chart can draw them after midnight."""
    folder = history_path.parent / "days"
    key = str(folder)
    now = time.monotonic()
    cached = _saved_cache.get(key)
    # Reuse the last load for a while. Midnight rollover and Reload clear the cache.
    if cached is not None and now - cached[2] < _SAVED_CHECK_SECONDS:
        return cached[1]
    if not folder.is_dir():
        _saved_cache[key] = ((), [], now)
        return []
    files = [path for path in sorted(folder.glob("*.json")) if not path.name.endswith(".bak")]
    try:
        stamp = tuple((path.name, path.stat().st_mtime_ns) for path in files)
    except OSError:
        stamp = ()
    if cached is not None and cached[0] == stamp:
        _saved_cache[key] = (stamp, cached[1], now)
        return cached[1]
    rows: list[dict] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            rows.extend(_segments_from_day(data))
    _saved_cache[key] = (stamp, rows, now)
    return rows


def _kind_totals(segments: list[dict]) -> tuple[float, float]:
    earn = 0.0
    spend = 0.0
    for item in segments:
        if not isinstance(item, dict):
            continue
        seconds = float(item.get("seconds") or 0)
        kind = item.get("kind")
        if kind == "earn":
            earn += seconds
        elif kind == "spend":
            spend += seconds
    return earn, spend


def _segments_from_day(data: dict) -> list[dict]:
    closed = [dict(item) for item in (data.get("closed") or []) if isinstance(item, dict) and item.get("start")]
    earn, spend = _kind_totals(closed)
    stored_earn = float(data.get("productive_seconds") or 0)
    stored_spend = float(data.get("spent_seconds") or 0)
    earn_gap = stored_earn - earn
    spend_gap = stored_spend - spend
    if spend_gap > 60:
        if (spend < 1 and stored_spend > 60) or (
            spend > 0 and stored_spend > spend * 1.25 + 300
        ):
            spend_gap = 0
    if earn_gap < 1 and spend_gap < 1:
        return closed
    anchor = _parse_stamp(closed[0].get("start") if closed else None)
    if anchor is None:
        anchor = _parse_stamp(str(data.get("date") or "") + "T00:00:00")
    if anchor is None:
        return closed
    extras = []
    cursor = anchor
    for kind, gap in (("spend", spend_gap), ("earn", earn_gap)):
        if gap < 1:
            continue
        begin = cursor - timedelta(seconds=gap)
        extras.append(
            {
                "start": begin.isoformat(timespec="seconds"),
                "end": cursor.isoformat(timespec="seconds"),
                "kind": kind,
                "process": "Earlier",
                "title": "Earlier",
                "identity": "",
                "seconds": round(gap, 3),
            }
        )
        cursor = begin
    extras.reverse()
    return extras + closed


def state_text(payload: dict) -> str:
    """Indented JSON with one segment per line, built with the fast C encoder."""
    parts = []
    for key, value in payload.items():
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            rows = ",\n".join("    " + json.dumps(item) for item in value)
            shown = "[\n" + rows + "\n  ]"
        else:
            shown = json.dumps(value)
        parts.append(f"  {json.dumps(key)}: {shown}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def _append_history(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dates = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                dates.add(json.loads(line).get("date"))
            except json.JSONDecodeError:
                continue
    if summary.get("date") in dates:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(summary) + "\n")


_SKIP_UNTANGLE = frozenset({"sleep", "shutdown", "clock", "lost", "reset"})


def untangle_segments(segments: list[dict]) -> list[dict]:
    """Split a stretch that contains another so the chart does not draw backward.

    A counted minute can land inside a pause that was already saved. The pause
    keeps the time on either side, and the minute stays where it happened.

    Candidates are found with a start-time index (bisect) so thousands of
    finished days stay cheap — an all-pairs scan freezes the window.
    """
    raw = [dict(item) for item in segments if isinstance(item, dict)]
    count = len(raw)
    if count < 2:
        return raw
    starts = [_parse_stamp(item.get("start")) for item in raw]
    ends = [_parse_stamp(item.get("end")) for item in raw]
    order = sorted(
        (
            index
            for index in range(count)
            if starts[index] is not None and ends[index] is not None and ends[index] > starts[index]
        ),
        key=lambda index: (starts[index], ends[index], index),
    )
    start_keys = [starts[index] for index in order]
    replaced: set[int] = set()
    pieces: list[dict] = []
    for index, item in enumerate(raw):
        start = starts[index]
        end = ends[index]
        if start is None or end is None or end <= start:
            continue
        if item.get("kind") in _SKIP_UNTANGLE:
            continue
        span = (end - start).total_seconds()
        lo = bisect_left(start_keys, start)
        hi = bisect_right(start_keys, end)
        holes: list[tuple[datetime, datetime]] = []
        for pos in range(lo, hi):
            other = order[pos]
            if other == index:
                continue
            inner_start = starts[other]
            inner_end = ends[other]
            if inner_end > end:
                continue
            if (inner_end - inner_start).total_seconds() >= span - 0.5:
                continue
            holes.append((inner_start, inner_end))
        if not holes:
            continue
        replaced.add(index)
        pieces.extend(_pieces_around(item, start, end, holes))
    if not replaced:
        return raw
    result = [item for index, item in enumerate(raw) if index not in replaced]
    result.extend(pieces)
    return result


def _pieces_around(
    outer: dict,
    start: datetime,
    end: datetime,
    holes: list[tuple[datetime, datetime]],
) -> list[dict]:
    span = (end - start).total_seconds()
    seconds = float(outer.get("seconds") or 0)
    merged: list[tuple[datetime, datetime]] = []
    for hole_start, hole_end in sorted(holes, key=lambda item: (item[0], item[1])):
        if merged and hole_start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hole_end))
        else:
            merged.append((hole_start, hole_end))
    pieces = []
    cursor = start
    for hole_start, hole_end in merged:
        duration = (hole_start - cursor).total_seconds()
        if duration > 0.5:
            piece = dict(outer)
            piece["start"] = cursor.isoformat(timespec="seconds")
            piece["end"] = hole_start.isoformat(timespec="seconds")
            piece["seconds"] = round(seconds * duration / span, 3) if span else 0.0
            pieces.append(piece)
        if hole_end > cursor:
            cursor = hole_end
    duration = (end - cursor).total_seconds()
    if duration > 0.5:
        piece = dict(outer)
        piece["start"] = cursor.isoformat(timespec="seconds")
        piece["end"] = end.isoformat(timespec="seconds")
        piece["seconds"] = round(seconds * duration / span, 3) if span else 0.0
        pieces.append(piece)
    return pieces


def _carve(outer: dict, start: datetime, end: datetime, inner_start: datetime, inner_end: datetime) -> list[dict]:
    return _pieces_around(outer, start, end, [(inner_start, inner_end)])


def _parse_stamp(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _valid_day(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return ""
    return value


def _read_state(path: Path) -> dict:
    raw = path.read_bytes()
    if not raw.strip(b"\x00 \t\r\n"):
        raise ValueError("state file was empty")
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("state was not an object")
    return data


def _park_corrupt(path: Path) -> None:
    if not path.exists():
        return
    target = path.with_name("state.corrupt.json")
    try:
        if target.exists():
            target.unlink()
        path.replace(target)
    except OSError:
        return
