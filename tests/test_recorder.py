"""Tests for the persisted battery series behind /battery/history."""
from __future__ import annotations

import pytest

from app.recorder import BatteryRecorder


def attrs(level, inp=0, out=0):
    return {
        "battery_level": level,
        "total_input_power": inp,
        "total_output_power": out,
    }


@pytest.fixture
def rec(tmp_path):
    r = BatteryRecorder(tmp_path / "battery.db", sample_interval_s=60, min_interval_s=5)
    yield r
    r.close()


class TestSampling:
    def test_first_sample_is_always_kept(self, rec):
        assert rec.record(1000.0, attrs(90)) is True

    def test_repeated_frames_are_dropped_until_the_interval_passes(self, rec):
        # The device streams at 1 Hz; storing all of it would be 86k rows a day
        # for a value that moves a few dozen times.
        assert rec.record(1000.0, attrs(90)) is True
        assert rec.record(1001.0, attrs(90)) is False
        assert rec.record(1059.0, attrs(90)) is False
        assert rec.record(1060.0, attrs(90)) is True

    def test_a_percentage_step_is_recorded_early(self, rec):
        # The edge is the interesting part of the curve, so it does not wait
        # for the next minute boundary -- only for the anti-jitter floor.
        rec.record(1000.0, attrs(90))
        assert rec.record(1002.0, attrs(89)) is False
        assert rec.record(1006.0, attrs(89)) is True

    def test_frames_without_a_level_are_ignored(self, rec):
        assert rec.record(1000.0, {"total_output_power": 200}) is False
        assert rec.record(1000.0, attrs("nonsense")) is False

    def test_out_of_range_levels_are_ignored(self, rec):
        assert rec.record(1000.0, attrs(-1)) is False
        assert rec.record(1000.0, attrs(255)) is False

    def test_a_disabled_recorder_never_writes(self, tmp_path):
        off = BatteryRecorder("")
        assert off.enabled is False
        assert off.record(1000.0, attrs(90)) is False
        assert off.series(24, 100, now=1000.0)["enabled"] is False

    def test_an_unopenable_path_disables_instead_of_raising(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        bad = BatteryRecorder(blocker / "sub" / "battery.db")
        assert bad.record(1000.0, attrs(90)) is False
        assert bad.enabled is False


class TestPersistence:
    def test_the_series_survives_a_reopen(self, tmp_path):
        first = BatteryRecorder(tmp_path / "b.db", sample_interval_s=60)
        first.record(1000.0, attrs(90))
        first.close()

        second = BatteryRecorder(tmp_path / "b.db", sample_interval_s=60)
        out = second.series(24, 100, now=1010.0)
        assert out["count"] == 1
        assert out["items"][0]["level"] == 90.0
        # It also picks the sampling clock back up where it left off, so a
        # restart loop cannot spam a row per launch.
        assert second.record(1001.0, attrs(90)) is False
        second.close()


class TestSeries:
    def test_buckets_average_the_samples_they_cover(self, rec):
        for i, level in enumerate([90, 80, 70, 60]):
            rec.record(1000.0 + i * 60, attrs(level, inp=100, out=200))
        # 4 minutes of data into 2 buckets of 2 minutes each.
        out = rec.series(hours=4 / 60, points=2, now=1000.0 + 4 * 60)
        assert out["bucket_s"] == 120
        assert [i["level"] for i in out["items"]] == [85.0, 65.0]
        assert out["items"][0]["in"] == 100
        assert out["items"][0]["out"] == 200

    def test_samples_older_than_the_window_are_left_out(self, rec):
        rec.record(1000.0, attrs(90))
        rec.record(1000.0 + 7200, attrs(50))
        out = rec.series(hours=1, points=60, now=1000.0 + 7200)
        assert [i["level"] for i in out["items"]] == [50.0]
        # ...but the client is still told how far back the data goes, so it can
        # say "only 2h recorded" instead of implying the pack sat at 50%.
        assert out["oldest"] == 1000.0

    def test_min_and_max_span_the_window(self, rec):
        for i, level in enumerate([90, 74, 99]):
            rec.record(1000.0 + i * 60, attrs(level))
        out = rec.series(hours=1, points=60, now=1000.0 + 180)
        assert (out["min"], out["max"]) == (74, 99)

    def test_an_empty_window_reports_no_points_rather_than_failing(self, rec):
        out = rec.series(hours=1, points=60, now=1000.0)
        assert out["enabled"] is True
        assert out["count"] == 0
        assert out["items"] == []
        assert out["min"] is None


class TestRetention:
    def test_the_expansion_level_is_stored_alongside_the_pack(self, rec):
        rec.record(1000.0, {**attrs(90), "expansion_battery_level": 70})
        item = rec.series(1, 60, now=1060.0)["items"][0]
        assert (item["level"], item["ext"]) == (90.0, 70.0)

    def test_rows_without_an_expansion_pack_carry_no_ext_key(self, rec):
        # Absent, not zero: a client drawing 0 % for a pack that was never
        # there would invent a dead battery.
        rec.record(1000.0, attrs(90))
        series = rec.series(1, 60, now=1060.0)
        assert "ext" not in series["items"][0]
        assert series["has_expansion"] is False

    def test_an_expansion_step_is_recorded_early_on_its_own(self, rec):
        # The two packs move independently, so the expansion stepping has to
        # open the sampling window even while the main level sits still.
        rec.record(1000.0, {**attrs(90), "expansion_battery_level": 70})
        assert rec.record(1002.0, {**attrs(90), "expansion_battery_level": 71}) is False
        assert rec.record(1006.0, {**attrs(90), "expansion_battery_level": 71}) is True

    def test_an_out_of_range_expansion_level_is_dropped_not_the_row(self, rec):
        # A bad second reading must not cost us the pack's own sample.
        assert rec.record(1000.0, {**attrs(90), "expansion_battery_level": 255}) is True
        assert "ext" not in rec.series(1, 60, now=1060.0)["items"][0]

    def test_a_database_written_before_the_column_existed_still_opens(self, tmp_path):
        # The migration has to run against real pre-existing files, which is
        # the whole reason ext_level is nullable.
        import sqlite3

        path = tmp_path / "battery.db"
        old = sqlite3.connect(path)
        old.execute(
            "CREATE TABLE battery (t INTEGER PRIMARY KEY, level INTEGER NOT NULL,"
            " input INTEGER NOT NULL, output INTEGER NOT NULL)"
        )
        old.execute("INSERT INTO battery VALUES (1000, 90, 0, 0)")
        old.commit()
        old.close()

        rec = BatteryRecorder(path, sample_interval_s=60, min_interval_s=5)
        try:
            assert rec.record(1060.0, {**attrs(88), "expansion_battery_level": 70}) is True
            items = rec.series(1, 60, now=1100.0)["items"]
            assert "ext" not in items[0]  # the legacy row
            assert items[-1]["ext"] == 70.0
        finally:
            rec.close()

    def test_rows_past_the_retention_window_are_dropped(self, tmp_path):
        r = BatteryRecorder(
            tmp_path / "b.db", sample_interval_s=60, retention_days=1 / 24
        )
        r.record(1000.0, attrs(90))
        # Pruning is hourly, so it only runs on a write an hour or more later.
        r.record(1000.0 + 7200, attrs(50))
        out = r.series(hours=24, points=100, now=1000.0 + 7200)
        assert [i["level"] for i in out["items"]] == [50.0]
        r.close()
