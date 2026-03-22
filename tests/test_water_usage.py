"""Tests for rachio/water_usage.py."""

import pytest
from rachio.models import ScheduleRule, Zone
from rachio.water_usage import (
    estimate_monthly_consumption,
    estimate_event_gallons,
)


def make_zone(
    area_sqft: float = 500,
    nozzle_rate: float = 1.5,
    runtime_seconds: int = 3600,
) -> Zone:
    return Zone(
        id="zone-test-1",
        device_id="device-1",
        name="Test Zone",
        zone_number=1,
        enabled=True,
        area_sqft=area_sqft,
        root_depth_inches=6.0,
        available_water=0.17,
        saturated_depth_inches=0.56,
        depth_of_water_inches=0.5,
        management_allowed_depletion=0.5,
        crop_coefficient=0.8,
        nozzle_rate_inhr=nozzle_rate,
        efficiency=0.8,
        runtime_seconds=runtime_seconds,
        last_watered_date=0,
        max_runtime_seconds=10800,
    )


def make_interval_rule(zone_id: str, duration: int, interval_days: int) -> ScheduleRule:
    return ScheduleRule(
        id="rule-1",
        device_id="device-1",
        name="Every N days",
        enabled=True,
        zones=[{"zoneId": zone_id, "duration": duration, "sortOrder": 1}],
        total_duration_seconds=duration,
        schedule_job_types=[f"INTERVAL_{interval_days}"],
    )


def make_dow_rule(zone_id: str, duration: int, days: list[int]) -> ScheduleRule:
    job_types = [f"DAY_OF_WEEK_{d}" for d in days]
    return ScheduleRule(
        id="rule-2",
        device_id="device-1",
        name="Days of week",
        enabled=True,
        zones=[{"zoneId": zone_id, "duration": duration, "sortOrder": 1}],
        total_duration_seconds=duration,
        schedule_job_types=job_types,
    )


class TestEstimateMonthlyConsumption:
    def test_interval_rule(self):
        """Monthly estimate from INTERVAL_N rule."""
        zone = make_zone(area_sqft=500, nozzle_rate=1.5)
        rule = make_interval_rule("zone-test-1", duration=1800, interval_days=2)
        gal = estimate_monthly_consumption(zone, rules=[rule])
        # 1800s = 0.5 hr. Runs ~15 times/month (30/2).
        # gal/hr = 1.5 * 500 / 96.25 = 7.792
        # Expected: 7.792 * 0.5 * 15 = 58.4
        assert gal > 0
        assert gal < 200

    def test_day_of_week_rule(self):
        """Monthly estimate from DAY_OF_WEEK rule."""
        zone = make_zone(area_sqft=500, nozzle_rate=1.5)
        rule = make_dow_rule("zone-test-1", duration=600, days=[3, 5, 0])
        gal = estimate_monthly_consumption(zone, rules=[rule])
        # 600s = 0.167 hr. 3 days/week * 4.3 = ~13 runs/month
        # 7.792 * 0.167 * 13 ≈ 17 gal
        assert gal > 0
        assert gal < 100

    def test_from_zone_runtime_fallback(self):
        """Falls back to zone.runtime_seconds when no rules."""
        zone = make_zone(runtime_seconds=3600)
        gal = estimate_monthly_consumption(zone)
        assert gal > 0

    def test_zero_if_no_data(self):
        """Zero if no rules and no runtime."""
        zone = make_zone(runtime_seconds=0)
        gal = estimate_monthly_consumption(zone)
        assert gal == 0.0

    def test_ignores_disabled_rules(self):
        """Disabled rules don't contribute to consumption."""
        zone = make_zone()
        rule = make_interval_rule("zone-test-1", duration=3600, interval_days=1)
        rule.enabled = False
        gal = estimate_monthly_consumption(zone, rules=[rule])
        assert gal == 0.0

    def test_ignores_other_zones(self):
        """Rules for other zones are ignored."""
        zone = make_zone()
        rule = make_interval_rule("other-zone", duration=3600, interval_days=1)
        gal = estimate_monthly_consumption(zone, rules=[rule])
        assert gal == 0.0


class TestEstimateEventGallons:
    def test_basic_calculation(self):
        zone = make_zone()
        gal = estimate_event_gallons(zone, 3600)
        # 1.5 in/hr * 500 sqft / 96.25 = 7.792 gal/hr
        assert abs(gal - 7.79) < 0.1

    def test_short_duration(self):
        zone = make_zone()
        gal = estimate_event_gallons(zone, 60)
        assert gal < 1.0

    def test_zero_duration(self):
        zone = make_zone()
        gal = estimate_event_gallons(zone, 0)
        assert gal == 0.0
