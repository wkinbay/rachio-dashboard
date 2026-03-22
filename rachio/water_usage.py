"""Water consumption estimation for Rachio zones."""

from __future__ import annotations

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
