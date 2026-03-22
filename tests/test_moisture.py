"""Tests for rachio/moisture.py."""

import pytest
from rachio.models import WeatherData, Zone
from rachio.moisture import (
    estimate_moisture,
    daily_depletion_rate,
    moisture_status,
    moisture_color,
    compute_eto_hargreaves,
)


def make_zone(
    depth_of_water: float = 0.5,
    saturated_depth: float = 0.56,
    available_water: float = 0.17,
    root_depth: float = 6.0,
    mad: float = 0.5,
    crop_coeff: float = 0.8,
    nozzle_rate: float = 1.5,
    efficiency: float = 0.8,
    area_sqft: float = 500,
    last_watered_date: int = 0,
) -> Zone:
    """Helper to create a minimal Zone for testing."""
    return Zone(
        id="zone-test-1",
        device_id="device-1",
        name="Test Zone",
        zone_number=1,
        enabled=True,
        area_sqft=area_sqft,
        root_depth_inches=root_depth,
        available_water=available_water,
        saturated_depth_inches=saturated_depth,
        depth_of_water_inches=depth_of_water,
        management_allowed_depletion=mad,
        crop_coefficient=crop_coeff,
        nozzle_rate_inhr=nozzle_rate,
        efficiency=efficiency,
        runtime_seconds=3600,
        last_watered_date=last_watered_date,
        max_runtime_seconds=10800,
    )


def make_weather(
    temp_f: float = 75,
    humidity: float = 0.5,
    wind_speed: float = 3.0,
    cloud_cover: float = 0.3,
    dew_point_f: float = 50,
) -> WeatherData:
    """Helper to create a WeatherData object."""
    return WeatherData(
        device_id="device-1",
        timestamp=1711000000,
        temp_f=temp_f,
        humidity=humidity,
        wind_speed_mph=wind_speed,
        cloud_cover=cloud_cover,
        dew_point_f=dew_point_f,
        precip_probability=0.0,
        precip_inches=0.0,
        forecast_daily=[],
    )


class TestComputeEtoHargreaves:
    def test_positive_eto(self):
        """ETo is positive for normal conditions."""
        eto = compute_eto_hargreaves(
            temp_high_f=85, temp_low_f=65,
            dew_point_f=55, wind_speed_mph=3.0,
            cloud_cover=0.2, latitude=37.0, day_of_year=180,
        )
        assert eto > 0

    def test_zero_for_negative_delta(self):
        """ETo is zero when high temp == low temp (TD=0)."""
        eto = compute_eto_hargreaves(
            temp_high_f=70, temp_low_f=70,
            dew_point_f=55, wind_speed_mph=3.0,
            cloud_cover=0.2, latitude=37.0, day_of_year=180,
        )
        assert eto == 0.0

    def test_higher_temp_gives_higher_eto(self):
        eto_warm = compute_eto_hargreaves(
            temp_high_f=95, temp_low_f=70,
            dew_point_f=55, wind_speed_mph=3.0,
            cloud_cover=0.2, latitude=37.0, day_of_year=180,
        )
        eto_cool = compute_eto_hargreaves(
            temp_high_f=75, temp_low_f=55,
            dew_point_f=40, wind_speed_mph=3.0,
            cloud_cover=0.2, latitude=37.0, day_of_year=180,
        )
        assert eto_warm > eto_cool


class TestEstimateMoisture:
    def test_starts_from_zone_depth(self):
        """Moisture uses zone depthOfWater as starting point."""
        zone = make_zone(depth_of_water=0.45)
        moisture = estimate_moisture(zone)
        # field_capacity = 0.17 * 6 * 0.9 = 0.918
        # moisture = 0.45 / 0.918 * 100 ≈ 49%
        assert 44 <= moisture <= 55

    def test_falls_back_to_field_capacity_when_no_depth(self):
        """When depthOfWater is 0, starts at 80% of field capacity."""
        zone = make_zone(depth_of_water=0.0, last_watered_date=0)
        moisture = estimate_moisture(zone, weather=None)
        # field_capacity = 0.17 * 6 * 0.9 = 0.918; water_depth = 0.918 * 0.80 = 0.734
        # moisture = 0.734 / 0.918 * 100 ≈ 80%
        assert 75 <= moisture <= 85

    def test_depleted_by_eto_over_days(self):
        """Days without water deplete moisture via ETo."""
        zone = make_zone(depth_of_water=0.8)
        weather = make_weather(temp_f=85, humidity=0.4, dew_point_f=50)
        moisture = estimate_moisture(zone, weather, latitude=37.0, days_since_last_watered=5)
        # After 5 days of ETo depletion, should be noticeably lower than 100%
        assert moisture < 90

    def test_moisture_never_negative(self):
        """Moisture clamps at 0 minimum."""
        zone = make_zone(depth_of_water=0.01)
        weather = make_weather(temp_f=100, humidity=0.1, dew_point_f=30)
        # Many hot dry days
        moisture = estimate_moisture(zone, weather, latitude=37.0, days_since_last_watered=30)
        assert moisture >= 0

    def test_moisture_clamped_at_100(self):
        """Moisture never exceeds 100%."""
        zone = make_zone(depth_of_water=2.0)  # very high
        moisture = estimate_moisture(zone)
        assert moisture <= 100

    def test_uses_days_since_watered_from_zone(self):
        """days_since_last_watered computed from last_watered_date when not provided."""
        now_ms = int(__import__("time").time() * 1000)
        three_days_ago = now_ms - 3 * 24 * 3600 * 1000
        zone = make_zone(depth_of_water=0.5, last_watered_date=three_days_ago)
        moisture = estimate_moisture(zone)
        # Should deplete over 3 days
        assert moisture < 65  # below the starting 55% from depth alone


class TestDailyDepletionRate:
    def test_depletion_formula(self):
        """Daily depletion = ETo * Kc."""
        zone = make_zone(crop_coeff=0.8)
        rate = daily_depletion_rate(zone, 0.2)
        assert abs(rate - 0.16) < 0.001

    def test_higher_kc_more_depletion(self):
        zone_low = make_zone(crop_coeff=0.5)
        zone_high = make_zone(crop_coeff=0.9)
        assert daily_depletion_rate(zone_high, 0.2) > daily_depletion_rate(zone_low, 0.2)


class TestMoistureStatus:
    def test_critical_when_very_low(self):
        zone = make_zone(mad=0.5)
        assert moisture_status(10, zone) == "critical"

    def test_low_above_critical(self):
        zone = make_zone(mad=0.5)
        assert moisture_status(30, zone) == "low"

    def test_adequate_above_mad(self):
        zone = make_zone(mad=0.5)
        assert moisture_status(60, zone) == "adequate"

    def test_good_near_saturation(self):
        zone = make_zone(mad=0.5)
        assert moisture_status(88, zone) == "good"

    def test_saturated_at_95_plus(self):
        zone = make_zone(mad=0.5)
        assert moisture_status(97, zone) == "saturated"


class TestMoistureColor:
    def test_returns_valid_hex_color(self):
        zone = make_zone()
        color = moisture_color(20, zone)
        assert color.startswith("#")
        assert len(color) == 7
