"""Dataclasses for Rachio API objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Device:
    id: str
    name: str
    status: str
    model: str
    serial_number: str
    latitude: float
    longitude: float
    zone_ids: list[str] = field(default_factory=list)
    schedule_rule_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict) -> Device:
        return cls(
            id=data["id"],
            name=data["name"],
            status=data.get("status", "UNKNOWN"),
            model=data.get("model", ""),
            serial_number=data.get("serialNumber", ""),
            latitude=float(data.get("latitude", 0.0)),
            longitude=float(data.get("longitude", 0.0)),
        )


@dataclass
class Zone:
    id: str
    device_id: str
    name: str
    zone_number: int
    enabled: bool
    area_sqft: float
    root_depth_inches: float
    available_water: float  # inches/inch of soil
    saturated_depth_inches: float
    depth_of_water_inches: float
    management_allowed_depletion: float  # fraction (0-1)
    crop_coefficient: float
    nozzle_rate_inhr: float  # inches per hour
    efficiency: float
    runtime_seconds: int
    last_watered_date: int = 0  # epoch ms
    last_watered_duration: int = 0  # seconds
    max_runtime_seconds: int = 0
    image_url: str = ""

    @classmethod
    def from_api(cls, data: dict, device_id: str) -> Zone:
        crop = data.get("customCrop", {}) or {}
        nozzle = data.get("customNozzle", {}) or {}
        return cls(
            id=data["id"],
            device_id=device_id,
            name=data.get("name", "Unknown Zone"),
            zone_number=data.get("zoneNumber", 0),
            enabled=data.get("enabled", True),
            area_sqft=float(data.get("yardAreaSquareFeet", 0)),
            root_depth_inches=float(data.get("rootZoneDepth", 6.0)),
            available_water=float(data.get("availableWater", 0.17)),
            saturated_depth_inches=float(data.get("saturatedDepthOfWater", 0.56)),
            depth_of_water_inches=float(data.get("depthOfWater", 0.0)),
            management_allowed_depletion=float(data.get("managementAllowedDepletion", 0.5)),
            crop_coefficient=float(crop.get("coefficient", 0.8)),
            nozzle_rate_inhr=float(nozzle.get("inchesPerHour", 1.5)),
            efficiency=float(data.get("efficiency", 0.8)),
            runtime_seconds=int(data.get("runtime", 0)),
            last_watered_date=int(data.get("lastWateredDate", 0)),
            last_watered_duration=int(data.get("lastWateredDuration", 0)),
            max_runtime_seconds=int(data.get("maxRuntime", 0)),
            image_url=data.get("imageUrl", ""),
        )

    def last_watered_datetime(self) -> Optional[datetime]:
        if not self.last_watered_date:
            return None
        return datetime.fromtimestamp(self.last_watered_date / 1000)

    def field_capacity_water_depth_inches(self) -> float:
        """Practical field capacity (availableWater × rootDepth × 0.9)."""
        return self.available_water * self.root_depth_inches * 0.9

    def mad_threshold_inches(self) -> float:
        """Water depth at management-allowed-depletion threshold."""
        return self.field_capacity_water_depth_inches() * (1 - self.management_allowed_depletion)


@dataclass
class ScheduleRule:
    id: str
    device_id: str
    name: str
    enabled: bool
    zones: list[dict]  # [{zoneId: str, duration: int, sortOrder: int}]
    total_duration_seconds: int
    start_date: int = 0  # epoch ms
    cycle_soak: bool = False
    et_skip: bool = False
    schedule_job_types: list[str] = field(default_factory=list)  # e.g. ["INTERVAL_2", "DAY_OF_WEEK_3"]
    operator: str = ""
    summary: str = ""

    @classmethod
    def from_api(cls, data: dict, device_id: str) -> ScheduleRule:
        zones_data = data.get("zones", []) or []
        total = sum(z.get("duration", 0) for z in zones_data)
        return cls(
            id=data["id"],
            device_id=device_id,
            name=data.get("name", "Unnamed Schedule"),
            enabled=data.get("enabled", False),
            zones=zones_data,
            total_duration_seconds=data.get("totalDuration", total),
            start_date=int(data.get("startDate", 0)),
            cycle_soak=data.get("cycleSoak", False),
            et_skip=data.get("etSkip", False),
            schedule_job_types=data.get("scheduleJobTypes", []),
            operator=data.get("operator", ""),
            summary=data.get("summary", ""),
        )

    def interval_days(self) -> int | None:
        """Return interval in days if this is an INTERVAL_N rule, else None."""
        for jt in self.schedule_job_types:
            if jt.startswith("INTERVAL_"):
                try:
                    return int(jt.split("_")[1])
                except (IndexError, ValueError):
                    pass
        return None

    def run_days_of_week(self) -> list[int]:
        """Return 0=Sun, 1=Mon, ... 6=Sat for DAY_OF_WEEK_N rules."""
        days = []
        for jt in self.schedule_job_types:
            if jt.startswith("DAY_OF_WEEK_"):
                try:
                    days.append(int(jt.split("_")[2]))
                except (IndexError, ValueError):
                    pass
        return days


@dataclass
class WeatherData:
    device_id: str
    timestamp: int  # current observation time (epoch s)
    temp_f: float
    humidity: float  # fraction 0-1
    wind_speed_mph: float
    cloud_cover: float  # fraction 0-1
    dew_point_f: float
    precip_probability: float  # fraction 0-1
    precip_inches: float
    forecast_daily: list[dict] = field(default_factory=list)  # list of daily forecast dicts

    @classmethod
    def from_api(cls, data: dict, device_id: str) -> WeatherData:
        current = data.get("current", {}) or {}
        forecast = data.get("forecast", []) or []
        daily_forecasts = []
        for day in forecast:
            daily_forecasts.append({
                "time": day.get("time"),
                "tempHigh": day.get("temperatureMax"),
                "tempLow": day.get("temperatureMin"),
                "humidity": day.get("humidity"),
                "windSpeed": day.get("windSpeed"),
                "cloudCover": day.get("cloudCover"),
                "precipProbability": day.get("precipProbability"),
                "precip": day.get("calculatedPrecip", 0),
            })
        return cls(
            device_id=device_id,
            timestamp=int(current.get("time", 0)),
            temp_f=float(current.get("currentTemperature", 60)),
            humidity=float(current.get("humidity", 0.5)),
            wind_speed_mph=float(current.get("windSpeed", 0)),
            cloud_cover=float(current.get("cloudCover", 0)),
            dew_point_f=float(current.get("dewPoint", 50)),
            precip_probability=float(current.get("precipProbability", 0)),
            precip_inches=float(current.get("precipIntensity", 0)),
            forecast_daily=daily_forecasts,
        )

    def temp_c(self) -> float:
        return (self.temp_f - 32) * 5 / 9

    def mean_temp_c(self, high: float, low: float) -> float:
        return ((high - 32) * 5 / 9 + (low - 32) * 5 / 9) / 2


@dataclass
class ZoneState:
    zone_id: str
    zone_name: str
    moisture_pct: float  # % of field capacity
    target_moisture_pct: float
    last_watered_ts: int = 0  # epoch ms
    next_schedule_ts: int = 0  # epoch ms
    monthly_gallons: float = 0.0
    daily_depletion_inches: float = 0.0
    days_since_watered: int = 0

    def to_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "zone_name": self.zone_name,
            "moisture_pct": round(self.moisture_pct, 1),
            "target_moisture_pct": round(self.target_moisture_pct, 1),
            "last_watered_ts": self.last_watered_ts,
            "next_schedule_ts": self.next_schedule_ts,
            "monthly_gallons": round(self.monthly_gallons, 1),
            "daily_depletion_inches": round(self.daily_depletion_inches, 4),
            "days_since_watered": self.days_since_watered,
        }
