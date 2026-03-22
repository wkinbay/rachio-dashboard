"""Tests for rachio/collector.py."""

import json
import os
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import directly from the package using PYTHONPATH
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from rachio.collector import (
    acquire_lock,
    release_lock,
    load_history,
    prune_history,
    next_schedule_ts,
)
from rachio.models import ScheduleRule


class TestAcquireLock:
    def test_creates_lock_file(self, tmp_path):
        lock_path = tmp_path / "test.lock"
        fd = acquire_lock(lock_path)
        assert lock_path.exists()
        release_lock(fd, lock_path)
        assert not lock_path.exists()

    def test_blocks_concurrent(self, tmp_path):
        lock_path = tmp_path / "test.lock"
        fd1 = acquire_lock(lock_path)
        with pytest.raises(RuntimeError, match="already running"):
            acquire_lock(lock_path)
        release_lock(fd1, lock_path)


class TestLoadHistory:
    def test_filters_old_records(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        now = int(datetime.now().timestamp() * 1000)

        old_ts = int((datetime.now() - timedelta(days=35)).timestamp() * 1000)
        new_ts = int((datetime.now() - timedelta(days=5)).timestamp() * 1000)

        with open(history_file, "w") as f:
            f.write(json.dumps({"zone_id": "z1", "timestamp": old_ts, "moisture_pct": 50}) + "\n")
            f.write(json.dumps({"zone_id": "z1", "timestamp": new_ts, "moisture_pct": 60}) + "\n")

        with patch("rachio.collector.HISTORY_FILE", history_file):
            history = load_history(days=30)

        assert len(history) == 1
        assert history[0]["moisture_pct"] == 60

    def test_empty_file_returns_empty_list(self, tmp_path):
        history_file = tmp_path / "empty.jsonl"
        history_file.touch()
        with patch("rachio.collector.HISTORY_FILE", history_file):
            history = load_history()
        assert history == []


class TestPruneHistory:
    def test_removes_old_records(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        old_ts = int((datetime.now() - timedelta(days=35)).timestamp() * 1000)
        new_ts = int((datetime.now() - timedelta(days=5)).timestamp() * 1000)

        with open(history_file, "w") as f:
            f.write(json.dumps({"zone_id": "z1", "timestamp": old_ts}) + "\n")
            f.write(json.dumps({"zone_id": "z1", "timestamp": new_ts}) + "\n")

        with patch("rachio.collector.HISTORY_FILE", history_file):
            removed = prune_history(days=30)

        assert removed == 1
        with open(history_file) as f:
            remaining = [json.loads(line) for line in f if line.strip()]
        assert len(remaining) == 1


class TestNextScheduleTs:
    def test_returns_nearest_future(self):
        now = datetime.now()
        # Both rules start far in the past; we compute how many intervals
        # have passed and find the next one in the future
        past_ts = int((now - timedelta(days=10)).timestamp() * 1000)
        future_ts = int((now + timedelta(days=2)).timestamp() * 1000)

        rules = [
            # INTERVAL_3: started past, every 3 days. At day -10 from now,
            # intervals elapsed = 10/3 ≈ 3 → next = -10 + 4*3 = +2 days ✓
            ScheduleRule(
                id="rule-1", device_id="d1", name="r1", enabled=True,
                zones=[{"zoneId": "z1", "duration": 600}],
                total_duration_seconds=600,
                start_date=past_ts,
                schedule_job_types=["INTERVAL_3"],
            ),
            # INTERVAL_5: every 5 days, so next run is ~5 days away
            ScheduleRule(
                id="rule-2", device_id="d1", name="r2", enabled=True,
                zones=[{"zoneId": "z1", "duration": 600}],
                total_duration_seconds=600,
                start_date=past_ts,
                schedule_job_types=["INTERVAL_5"],
            ),
        ]
        result = next_schedule_ts(rules, "z1")
        # rule-1 (INTERVAL_3) should give next run ~2 days away
        assert 0 < result < future_ts + 86400 * 1000

    def test_skips_disabled_rules(self):
        now = datetime.now()
        future_ts = int((now + timedelta(days=2)).timestamp() * 1000)

        rules = [
            ScheduleRule(
                id="rule-1", device_id="d1", name="r1", enabled=False,
                zones=[{"zoneId": "z1", "duration": 600}],
                total_duration_seconds=600,
                start_date=future_ts,
            ),
        ]
        result = next_schedule_ts(rules, "z1")
        assert result == 0

    def test_returns_zero_if_no_future(self):
        rules = []
        result = next_schedule_ts(rules, "z1")
        assert result == 0
