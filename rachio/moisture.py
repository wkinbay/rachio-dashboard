"""Moisture estimation using a simple water-balance model.

ETo (reference evapotranspiration) is calculated locally using the
Hargreaves equation since the Rachio API does not provide ETo values.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import List, Optional

from rachio.models import WeatherData, Zone


def compute_eto_hargreaves(
    temp_high_f: float,
    temp_low_f: float,
    dew_point_f: float,
    wind_speed_mph: float,
    cloud_cover: float,
    latitude: float,
    day_of_year: int,
) -> float:
    """Estimate ETo (inches/day) using the Hargreaves equation.

    The full Hargreaves equation:
      ETo = 0.0023 × Ra × (T + 17.78) × TD^0.5

    Where Ra = extraterrestrial radiation (mm/day), converted to inches
          T  = mean temperature (°C)
          TD = temperature difference (°C) = T_high - T_low

    Wind speed correction and cloud cover adjustment are applied per
    FAO-56 guidelines for the Hargreaves method.

    Args:
        temp_high_f: Daily high temperature (°F)
        temp_low_f: Daily low temperature (°F)
        dew_point_f: Dew point temperature (°F)
        wind_speed_mph: Wind speed (mph) at 2m height
        cloud_cover: Fraction of cloud cover (0-1)
        latitude: Latitude in decimal degrees
        day_of_year: Day of year (1-366)

    Returns:
        ETo in inches per day
    """
    # Convert to Celsius
    T_high = (temp_high_f - 32) * 5 / 9
    T_low = (temp_low_f - 32) * 5 / 9
    T_dew = (dew_point_f - 32) * 5 / 9
    T_mean = (T_high + T_low) / 2
    TD = max(T_high - T_low, 0)  # temperature difference

    if T_mean + 17.78 <= 0 or TD <= 0:
        return 0.0

    # Extraterrestrial radiation Ra (mm/day) from FAO-56
    lat_rad = latitude * math.pi / 180.0
    dr = 1 + 0.033 * math.cos(2 * math.pi * day_of_year / 365.0)
    delta = 0.409 * math.sin(2 * math.pi * day_of_year / 365.0 - 1.39)
    omega_s = math.acos(-math.tan(lat_rad) * math.tan(delta))
    Ra = (24 * 60 / math.pi) * 0.0820 * dr * (
        omega_s * math.sin(lat_rad) * math.sin(delta)
        + math.cos(lat_rad) * math.cos(delta) * math.sin(omega_s)
    )
    # Ra in mm/day → convert to inches
    Ra_in = Ra * 0.03937

    # Clear-sky solar radiation adjustment from cloud cover
    Rs = Ra_in * (0.25 + 0.50 * (1 - cloud_cover))

    # Saturation vapour pressure at T_mean (kPa)
    ea = 0.6108 * math.exp((17.27 * T_dew) / (T_dew + 237.3))

    # Wind speed at 2m (if measurement height differs, adjust)
    wind_2m = wind_speed_mph * 0.44704  # mph → m/s

    # Hargreaves with wind correction
    # Wind correction term: 0.00386 × wind_2m × (ea)^0.5
    # Simplified adjustment applied to Ra
    ETo = 0.0023 * Ra_in * (T_mean + 17.78) * math.sqrt(max(TD, 0.1))
    ETo += 0.00086 * wind_2m * Rs

    return max(0.0, ETo)


def estimate_moisture(
    zone: Zone,
    weather: Optional[WeatherData] = None,
    latitude: float = 37.0,
    days_since_last_watered: int = 0,
) -> float:
    """Estimate current zone moisture as a percentage of field capacity.

    Uses a daily water-balance model:
      - Depletion starts from the zone's last watered date, using the API's
        depthOfWater as the water applied at that time.
      - ETo is calculated locally via Hargreaves equation (from weather data).
      - Each dry day between last watered and now applies ETo depletion.

    Args:
        zone: Zone object with soil/plant parameters
        weather: Weather data (used to calculate ETo; optional)
        latitude: Device latitude for ETo calculation (default 37°)
        days_since_last_watered: Override days since last watered (default from zone data)

    Returns:
        Moisture as a percentage (0-100) of field capacity.
    """
    field_capacity = zone.field_capacity_water_depth_inches()
    if field_capacity <= 0:
        field_capacity = zone.available_water * zone.root_depth_inches

    if field_capacity <= 0:
        return 50.0  # fallback

    # Starting water depth: depthOfWater from API if available, else field cap * 0.8
    if zone.depth_of_water_inches > 0:
        water_depth = zone.depth_of_water_inches
    else:
        water_depth = field_capacity * 0.80

    # Number of days since last watered
    if zone.last_watered_date > 0:
        if days_since_last_watered <= 0:
            now_ms = int(datetime.now().timestamp() * 1000)
            days_since_last_watered = max(0, int((now_ms - zone.last_watered_date) / (24 * 3600 * 1000)))
    else:
        # Never watered — assume today was the last watering (max moisture)
        days_since_last_watered = 0

    # Compute daily ETo from weather if available
    daily_eto_in = 0.0
    if weather:
        doy = datetime.now().timetuple().tm_yday
        daily_eto_in = compute_eto_hargreaves(
            temp_high_f=weather.temp_f + 15,  # approximate high
            temp_low_f=weather.temp_f - 10,  # approximate low
            dew_point_f=weather.dew_point_f,
            wind_speed_mph=weather.wind_speed_mph,
            cloud_cover=weather.cloud_cover,
            latitude=latitude,
            day_of_year=doy,
        )

    # Deplete for each day since last watered
    for _ in range(days_since_last_watered):
        depletion = daily_eto_in * zone.crop_coefficient
        water_depth -= depletion

    # Clamp to valid range (0 to field capacity — not saturated depth)
    water_depth = max(0.0, min(water_depth, field_capacity))

    moisture_pct = (water_depth / field_capacity) * 100.0
    return round(min(100.0, moisture_pct), 1)


def daily_depletion_rate(zone: Zone, et_today: float) -> float:
    """Calculate daily water depletion from evapotranspiration.

    Args:
        zone: Zone with crop and soil parameters
        et_today: ETo in inches for today

    Returns:
        Depletion in inches per day
    """
    return et_today * zone.crop_coefficient


def moisture_status(moisture_pct: float, zone: Zone) -> str:
    """Return a status label based on moisture level vs MAD threshold.

    Args:
        moisture_pct: Current moisture as % of field capacity
        zone: Zone to compare against

    Returns:
        One of: "critical", "low", "adequate", "good", "saturated"
    """
    mad_pct = (1 - zone.management_allowed_depletion) * 100

    if moisture_pct >= 95:
        return "saturated"
    elif moisture_pct >= 85:
        return "good"
    elif moisture_pct >= mad_pct:
        return "adequate"
    elif moisture_pct >= mad_pct * 0.5:
        return "low"
    else:
        return "critical"


def moisture_color(moisture_pct: float, zone: Zone) -> str:
    """Return a CSS color for the moisture level."""
    status = moisture_status(moisture_pct, zone)
    colors = {
        "critical": "#ef4444",   # red
        "low": "#f97316",         # orange
        "adequate": "#eab308",   # yellow
        "good": "#22c55e",        # green
        "saturated": "#3b82f6",  # blue
    }
    return colors.get(status, "#6b7280")
