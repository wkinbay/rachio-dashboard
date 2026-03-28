"""Water consumption estimation for Rachio zones."""

from __future__ import annotations

import re
from datetime import datetime

from typing import List, Optional

from rachio.models import ScheduleRule, Zone


# Gallons per cubic foot
GALLONS_PER_CUBIC_FOOT = 7.48052


def estimate_monthly_consumption(
    zone: Zone,
    rules: Optional[List[ScheduleRule]] = None,
) -> float:
    """Estimate monthly water consumption for a zone in gallons.

    Uses the nozzle flow rate and runtime from either a schedule rule
    or actual watering events.

    Calculation:
        gallons = (nozzle_rate_inhr * area_sqft / 96.25) * runtime_hours

    Where 96.25 = inches/hr * sqft → gallons conversion factor:
        1 acre-inch = 27,154 gallons
        1 acre = 43,560 sqft
        So: nozzle_inches/hr * sqft / (43,560 * 12 / 27,154) = nozzle * sqft / 96.25

    Monthly estimate is computed by determining how many runs per month
    from the schedule rule types (INTERVAL_N or DAY_OF_WEEK_N).

    Args:
        zone: Zone with area and nozzle info
        rules: List of schedule rules to compute from (optional)

    Returns:
        Estimated gallons used this month
    """
    area = zone.area_sqft if zone.area_sqft > 0 else 500  # default sqft
    nozzle_rate = zone.nozzle_rate_inhr if zone.nozzle_rate_inhr > 0 else 1.5

    # Gallons per second of runtime
    gal_per_sec = (nozzle_rate * area) / (96.25 * 3600)

    if rules:
        total_monthly_sec = 0.0
        for rule in rules:
            if not rule.enabled:
                continue
            for z in rule.zones:
                if z.get("zoneId") != zone.id:
                    continue
                duration = z.get("duration", 0)
                interval_days = rule.interval_days()
                if interval_days:
                    runs_per_month = 30.0 / interval_days
                else:
                    run_days = rule.run_days_of_week()
                    if run_days:
                        runs_per_month = len(run_days) * 4.3  # ~4.3 weeks/month
                    else:
                        runs_per_month = 1.0
                total_monthly_sec += duration * runs_per_month

        return round(gal_per_sec * total_monthly_sec, 1)

    elif zone.runtime_seconds > 0:
        # Fall back to zone default runtime, assume monthly runs
        hours = zone.runtime_seconds / 3600.0
        return round((nozzle_rate * area / 96.25) * hours, 1)

    return 0.0


def estimate_event_gallons(zone: Zone, duration_seconds: int) -> float:
    """Calculate gallons used for a single watering event.

    Args:
        zone: Zone with area and nozzle info
        duration_seconds: How long the event ran

    Returns:
        Gallons used
    """
    area = zone.area_sqft if zone.area_sqft > 0 else 500
    nozzle_rate = zone.nozzle_rate_inhr if zone.nozzle_rate_inhr > 0 else 1.5
    hours = duration_seconds / 3600.0
    return round((nozzle_rate * area / 96.25) * hours, 2)


# Pattern to extract minutes from event summary strings like:
# "ZoneName ran for 7 minutes." or "ZoneName completed watering at 10:06 PM for 7 minutes."
_MINUTES_PATTERN = re.compile(r"(\d+)\s+minute", re.IGNORECASE)


def actual_monthly_gallons(
    zone: Zone,
    events: list[dict],
    start_time_ms: int,
    end_time_ms: int,
) -> float:
    """Calculate actual water usage for a zone from watering events.

    Only counts events whose summary mentions this zone name and falls
    within the time range.

    Args:
        zone: Zone with area and nozzle info
        events: Full list of watering events from get_watering_events()
        start_time_ms: Start of billing period (epoch ms)
        end_time_ms: End of billing period (epoch ms)

    Returns:
        Gallons actually used in the period
    """
    area = zone.area_sqft if zone.area_sqft > 0 else 500
    nozzle_rate = zone.nozzle_rate_inhr if zone.nozzle_rate_inhr > 0 else 1.5
    gal_per_sec = (nozzle_rate * area) / (96.25 * 3600)

    total_seconds = 0.0
    for event in events:
        if event.get("topic") != "WATERING":
            continue
        sub_type = event.get("subType", "")
        if sub_type not in ("SCHEDULE_COMPLETED", "ZONE_COMPLETED"):
            continue
        event_ms = event.get("eventDate", 0)
        if not (start_time_ms <= event_ms <= end_time_ms):
            continue
        summary = event.get("summary", "")
        if zone.name.lower()[:10] not in summary.lower():
            continue
        m = _MINUTES_PATTERN.search(summary)
        if m:
            total_seconds += int(m.group(1)) * 60

    return round(gal_per_sec * total_seconds, 1)
