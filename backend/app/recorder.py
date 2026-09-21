"""Long-term battery history, persisted to SQLite.

The in-memory `/history` ring holds every frame for about ten minutes, which
is the right shape for "what is the device doing right now" and the wrong one
for "how did the charge move over the last day". This keeps a much sparser
series -- one row a minute, plus one at every percentage step -- on disk, so
the curve survives a backend restart and can be drawn over days.

Rows are (t, level, input, output). Keeping the two power totals alongside the
level is what lets a client shade the stretches where the pack was charging
without having to guess from the slope of a downsampled line.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS battery (
    t      INTEGER PRIMARY KEY,
    level  INTEGER NOT NULL,
    input  INTEGER NOT NULL,
    output INTEGER NOT NULL
);
"""

# `ext_level` arrived after the first databases were already on disk, and it is
# nullable on purpose: NULL means "no expansion pack was reporting", which is
# exactly the state of every row written before a B2 was attached, and of every
# row written while one is unplugged. A chart drawing the second curve only
# where the column is populated therefore tells the truth for free.
MIGRATIONS = (
    "ALTER TABLE battery ADD COLUMN ext_level INTEGER",
)


def default_db_path() -> str:
    """Where to keep the database when HISTORY_DB says nothing.

    In the container /data is the mounted volume; outside it there is no such
    directory, and dropping a database into the repo working tree would be
    rude, so fall back to the user's state directory.
    """
    if Path("/data").is_dir():
        return "/data/battery.db"
    return str(Path.home() / ".local" / "state" / "oupes" / "battery.db")


class BatteryRecorder:
    """Append-only battery series with a sampling floor and a retention cap.

    Every failure mode here is non-fatal: a read-only volume, a corrupt file or
    a missing parent directory disables persistence and leaves the telemetry
    stream untouched. Losing the chart is an annoyance; losing the live readout
    because the disk filled up would be a bug.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        sample_interval_s: float = 60.0,
        min_interval_s: float = 5.0,
        retention_days: float = 30.0,
    ) -> None:
        self.path = str(path) if path else ""
        self.sample_interval_s = max(1.0, float(sample_interval_s))
        # A step in the percentage is worth recording early -- that is the edge
        # the chart draws -- but not at 1 Hz, so steps still respect a floor.
        self.min_interval_s = max(1.0, min(float(min_interval_s), self.sample_interval_s))
        self.retention_days = max(0.0, float(retention_days))
        self.enabled = bool(self.path)
        self._db: sqlite3.Connection | None = None
        # /battery/history is served from a worker thread while frames keep
        # arriving on the event loop, so the connection is shared explicitly
        # and every statement goes through this lock.
        self._lock = threading.Lock()
        self._last_t: float = 0.0
        self._last_level: int | None = None
        self._last_ext_level: int | None = None
        self._last_prune: float = 0.0

    # ------------------------------------------------------------------ setup

    def _connect_locked(self) -> sqlite3.Connection | None:
        if self._db is not None or not self.enabled:
            return self._db
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(
                self.path, timeout=5.0, isolation_level=None, check_same_thread=False
            )
            # WAL so a reader hitting /battery/history never blocks the writer
            # that is servicing the telemetry stream.
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.executescript(SCHEMA)
            for statement in MIGRATIONS:
                try:
                    db.execute(statement)
                except sqlite3.OperationalError:
                    pass  # already applied
        except Exception:
            _LOGGER.warning(
                "battery history disabled: cannot open %s", self.path, exc_info=True
            )
            self.enabled = False
            return None
        self._db = db
        row = db.execute(
            "SELECT t, level, ext_level FROM battery ORDER BY t DESC LIMIT 1"
        ).fetchone()
        if row:
            self._last_t, self._last_level = float(row[0]), int(row[1])
            self._last_ext_level = None if row[2] is None else int(row[2])
        _LOGGER.info("battery history at %s", self.path)
        return db

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                try:
                    self._db.close()
                finally:
                    self._db = None

    # ----------------------------------------------------------------- writes

    def record(self, t: float, attrs: dict[str, Any]) -> bool:
        """Store one sample if it is due. Returns whether a row was written."""
        if not self.enabled:
            return False
        level = _as_int(attrs.get("battery_level"))
        if level is None or not 0 <= level <= 100:
            return False
        ext_level = _as_int(attrs.get("expansion_battery_level"))
        if ext_level is not None and not 0 <= ext_level <= 100:
            ext_level = None

        elapsed = t - self._last_t
        # Either pack stepping is an edge worth catching early: with a B2
        # attached the two levels move at their own pace, and sampling only on
        # the main pack's steps would draw the expansion curve as a staircase
        # of whatever happened to land on the minute.
        stepped = (
            self._last_level is not None and level != self._last_level
        ) or (
            self._last_ext_level is not None and ext_level != self._last_ext_level
        )
        if elapsed < (self.min_interval_s if stepped else self.sample_interval_s):
            return False

        row = (
            int(t),
            level,
            _as_int(attrs.get("total_input_power")) or 0,
            _as_int(attrs.get("total_output_power")) or 0,
            ext_level,
        )
        with self._lock:
            db = self._connect_locked()
            if db is None:
                return False
            try:
                # Same-second collisions are the poller catching up, not new data.
                db.execute(
                    "INSERT OR REPLACE INTO battery (t, level, input, output, ext_level)"
                    " VALUES (?, ?, ?, ?, ?)",
                    row,
                )
            except Exception:
                _LOGGER.warning("battery history write failed", exc_info=True)
                return False
            self._last_t, self._last_level = t, level
            self._last_ext_level = ext_level
            self._prune_locked(db, t)
        return True

    def _prune_locked(self, db: sqlite3.Connection, now: float) -> None:
        if self.retention_days <= 0 or now - self._last_prune < 3600:
            return
        self._last_prune = now
        cutoff = int(now - self.retention_days * 86400)
        try:
            db.execute("DELETE FROM battery WHERE t < ?", (cutoff,))
        except Exception:
            _LOGGER.debug("battery history prune failed", exc_info=True)

    # ------------------------------------------------------------------ reads

    def series(self, hours: float, points: int, now: float | None = None) -> dict[str, Any]:
        """Downsampled window, newest last.

        Buckets are averaged rather than sampled so a 30-day view still shows
        the shape of each day instead of whichever row happened to land on the
        boundary. Empty buckets are simply absent: a gap in the data is real
        information (the backend was down) and the client draws it as a break.
        """
        now = time.time() if now is None else now
        span = max(60.0, hours * 3600.0)
        points = max(2, int(points))
        bucket = max(1, int(span / points))
        since = int(now - span)

        empty = {
            "enabled": self.enabled,
            "hours": hours,
            "bucket_s": bucket,
            "count": 0,
            "items": [],
            "has_expansion": False,
            "min": None,
            "max": None,
            "oldest": None,
        }
        with self._lock:
            db = self._connect_locked()
            if db is None:
                return empty
            try:
                rows = db.execute(
                    "SELECT (t / ?) * ? AS b,"
                    "       AVG(level), AVG(input), AVG(output), MIN(level), MAX(level),"
                    "       AVG(ext_level)"
                    "  FROM battery WHERE t >= ?"
                    " GROUP BY b ORDER BY b",
                    (bucket, bucket, since),
                ).fetchall()
                oldest = db.execute("SELECT MIN(t) FROM battery").fetchone()[0]
            except Exception:
                _LOGGER.warning("battery history read failed", exc_info=True)
                return empty

        items = []
        for r in rows:
            item = {
                "t": int(r[0]),
                "level": round(float(r[1]), 1),
                "in": int(round(float(r[2]))),
                "out": int(round(float(r[3]))),
            }
            # Absent rather than null: a bucket with no expansion reading is a
            # break in that curve, and the client already draws gaps for the
            # main series the same way.
            if r[6] is not None:
                item["ext"] = round(float(r[6]), 1)
            items.append(item)
        return {
            "enabled": True,
            "hours": hours,
            "bucket_s": bucket,
            "count": len(items),
            "items": items,
            "has_expansion": any("ext" in i for i in items),
            "min": min(int(r[4]) for r in rows) if rows else None,
            "max": max(int(r[5]) for r in rows) if rows else None,
            "oldest": float(oldest) if oldest is not None else None,
        }


def _as_int(value: Any) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
