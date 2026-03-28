"""Daily data collection job for Rachio zones."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from rachio.api import RachioClient, RachioError
from rachio.moisture import compute_eto_hargreaves, daily_depletion_rate, estimate_moisture
from rachio.models import Device, ScheduleRule, WeatherData, Zone, ZoneState
from rachio.water_usage import actual_monthly_gallons

log = logging.getLogger("rachio.collector")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

LOCK_FILE = Path(__file__).parent.parent / "data" / "collector.lock"
HISTORY_FILE = Path(__file__).parent.parent / "data" / "history.jsonl"
DATA_DIR = Path(__file__).parent.parent / "data"


def acquire_lock(lock_path: Path) -> int:
    """Acquire an exclusive lock file to prevent concurrent runs."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise RuntimeError("Another collector process is already running")
    return fd


def release_lock(fd: int, lock_path: Path) -> None:
    """Release the lock file."""
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    try:
        lock_path.unlink()
    except OSError:
        pass


def load_history(days: int = 30) -> List[dict]:
    """Load zone history from the JSONL file.

    Returns one dict per zone per day, sorted newest first.
    """
    history: List[dict] = []
    if not HISTORY_FILE.exists():
        return history

    cutoff = datetime.now() - timedelta(days=days)
    cutoff_ts = int(cutoff.timestamp() * 1000)

    with open(HISTORY_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                record_ts = record.get("timestamp", 0)
                if record_ts >= cutoff_ts:
                    history.append(record)
            except json.JSONDecodeError:
                continue

    return history


def append_history(record: dict) -> None:
    """Append a zone state record to the history file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")


def prune_history(days: int = 30) -> int:
    """Remove records older than `days` from history file."""
    if not HISTORY_FILE.exists():
        return 0

    cutoff = datetime.now() - timedelta(days=days)
    cutoff_ts = int(cutoff.timestamp() * 1000)

    all_records: List[dict] = []
    removed = 0
    with open(HISTORY_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if record.get("timestamp", 0) >= cutoff_ts:
                    all_records.append(record)
                else:
                    removed += 1
            except json.JSONDecodeError:
                continue

    with open(HISTORY_FILE, "w") as f:
        for record in all_records:
            f.write(json.dumps(record) + "\n")

    return removed


def next_schedule_ts(rules: List[ScheduleRule], zone_id: str) -> int:
    """Estimate the next scheduled watering time for a zone.

    Works from the schedule rule definitions (INTERVAL_N or DAY_OF_WEEK_N).
    Returns 0 if no schedule applies or none are in the future.
    """
    now = datetime.now()
    now_ts = int(now.timestamp() * 1000)

    for rule in rules:
        if not rule.enabled:
            continue
        # Check if this rule covers this zone
        zone_ids = [z.get("zoneId") for z in rule.zones]
        if zone_id not in zone_ids:
            continue

        interval_days = rule.interval_days()
        if interval_days:
            # INTERVAL_N: compute next occurrence from startDate or last watered
            start_ts = rule.start_date or now_ts
            if start_ts < now_ts:
                # How many intervals have passed since start?
                days_since_start = (now_ts - start_ts) / (24 * 3600 * 1000)
                intervals_elapsed = int(days_since_start / interval_days)
                next_interval = intervals_elapsed + 1
                next_ts = int(start_ts + next_interval * interval_days * 24 * 3600 * 1000)
            else:
                next_ts = start_ts
            return next_ts

        run_days = rule.run_days_of_week()
        if run_days:
            # DAY_OF_WEEK_N: find next matching weekday
            current_dow = now.weekday()  # 0=Mon ... 6=Sun
            # Rachio uses 0=Sun ... 6=Sat; convert
            for offset in range(1, 8):
                candidate_dow = (current_dow + offset) % 7
                # convert to Rachio convention
                rachio_dow = (candidate_dow + 1) % 7
                if rachio_dow in run_days:
                    next_day = now + timedelta(days=offset)
                    # Use startDate time of day as proxy (midnight)
                    return int(next_day.replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ).timestamp() * 1000)

    return 0


def collect_daily_state() -> Dict[str, ZoneState]:
    """Collect current state for all zones.

    Returns:
        Dict mapping zone_id -> ZoneState
    """
    client = RachioClient()

    # Get full person object with all devices/zones/schedules embedded
    person = client.get_person_full()
    devices_data = person.get("devices", [])

    states: Dict[str, ZoneState] = {}

    for dev_data in devices_data:
        device = Device.from_api(dev_data)
        device_id = device.id
        lat = device.latitude or 37.0

        # Zones are embedded in the device object
        zones_data = RachioClient.get_zones(dev_data)
        zones = [Zone.from_api(z, device_id) for z in zones_data]

        # Schedule rules are embedded in the device object
        all_rules_data = RachioClient.get_all_schedule_rules(dev_data)
        rules = [ScheduleRule.from_api(r, device_id) for r in all_rules_data]

        # Weather forecast
        try:
            forecast_data = client.get_forecast(device_id)
            weather = WeatherData.from_api(forecast_data, device_id)
        except RachioError as e:
            log.warning("Could not fetch weather for device %s: %s", device_id, e)
            weather = None

        # Fetch actual watering events for the current billing period (this month)
        now = datetime.now()
        bill_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        bill_start_ms = int(bill_start.timestamp() * 1000)
        now_ms = int(now.timestamp() * 1000)
        try:
            all_events = client.get_watering_events(device_id, bill_start_ms, now_ms)
        except RachioError as e:
            log.warning("Could not fetch watering events for device %s: %s", device_id, e)
            all_events = []

        for zone in zones:
            # Days since last watered
            days_since = 0
            if zone.last_watered_date > 0:
                now_ms = int(datetime.now().timestamp() * 1000)
                days_since = max(0, int((now_ms - zone.last_watered_date) / (24 * 3600 * 1000)))

            # Moisture estimate
            moisture = estimate_moisture(zone, weather, latitude=lat)
            target = (1 - zone.management_allowed_depletion) * 100

            # Next scheduled
            next_sched = next_schedule_ts(rules, zone.id)

            # Monthly consumption (actual from watering events this month)
            monthly_gal = actual_monthly_gallons(zone, all_events, bill_start_ms, now_ms)

            # Daily depletion
            daily_depl = 0.0
            if weather:
                doy = datetime.now().timetuple().tm_yday
                et = compute_eto_hargreaves(
                    temp_high_f=weather.temp_f + 10,
                    temp_low_f=weather.temp_f - 10,
                    dew_point_f=weather.dew_point_f,
                    wind_speed_mph=weather.wind_speed_mph,
                    cloud_cover=weather.cloud_cover,
                    latitude=lat,
                    day_of_year=doy,
                )
                daily_depl = daily_depletion_rate(zone, et)

            state = ZoneState(
                zone_id=zone.id,
                zone_name=zone.name,
                moisture_pct=moisture,
                target_moisture_pct=round(target, 1),
                last_watered_ts=zone.last_watered_date,
                next_schedule_ts=next_sched,
                monthly_gallons=monthly_gal,
                daily_depletion_inches=daily_depl,
                days_since_watered=days_since,
            )
            states[zone.id] = state

    return states


def run() -> Dict[str, ZoneState]:
    """Main entry point: acquire lock, collect state, persist history."""
    lock_fd = acquire_lock(LOCK_FILE)
    try:
        log.info("Starting daily Rachio collection")
        states = collect_daily_state()
        now_ts = int(time.time() * 1000)

        for zone_id, state in states.items():
            record = state.to_dict()
            record["timestamp"] = now_ts
            append_history(record)
            log.info("Zone %s: moisture=%.1f%%, last_watered=%d days ago, monthly=%.1f gal",
                     state.zone_name, state.moisture_pct,
                     state.days_since_watered, state.monthly_gallons)

        removed = prune_history(30)
        log.info("Collection complete. %d zones. Pruned %d old records.", len(states), removed)
        return states

    finally:
        release_lock(lock_fd, LOCK_FILE)
