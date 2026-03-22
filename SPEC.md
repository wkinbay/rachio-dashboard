# Rachio Dashboard — SPEC.md

## Overview
A daily-updated dashboard for Rachio sprinkler systems. Pulls data from the Rachio API, models zone moisture using a water-balance approach, tracks water consumption, and renders an HTML dashboard with charts.

---

## API Design

### Base URL
`https://api.rach.io/1/public`

### Authentication
- Header: `Authorization: Bearer {RACHIO_API_KEY}`

### Key Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/person/{id}` | Get user info (needed for device list) |
| GET | `/device/{id}` | Get device details |
| GET | `/device/{id}/zone` | List zones for a device |
| GET | `/device/{id}/schedule_rule` | List schedule rules |
| GET | `/device/{id}/schedule_event` | List schedule events (history) |
| GET | `/device/{id}/forecast?units=IMPERIAL` | Weather forecast + ETo |

### Rate Limits
- 3,500 requests/day
- Client should handle 429 with retry-after

---

## Data Model

### Device
```python
@dataclass
class Device:
    id: str
    name: str
    status: str  # ONLINE, OFFLINE
    model: str
    serial_number: str
    latitude: float
    longitude: float
    zones: list[str]  # zone IDs
    schedule_rules: list[str]  # rule IDs
```

### Zone
```python
@dataclass
class Zone:
    id: str
    device_id: str
    name: str
    zone_number: int
    enabled: bool
    area_sqft: float          # yardAreaSquareFeet
    root_depth_inches: float # rootZoneDepth
    available_water: float    # availableWater (inches/inch)
    saturated_depth_inches: float  # saturatedDepthOfWater
    depth_of_water_inches: float   # depthOfWater
    management_allowed_depletion: float  # fraction
    crop_coefficient: float   # customCrop.coefficient
    nozzle_rate_inhr: float  # customNozzle.inchesPerHour
    efficiency: float
    runtime_seconds: int      # runtime
    last_watered_date: int    # epoch ms
    max_runtime_seconds: int  # maxRuntime
```

### ScheduleRule
```python
@dataclass
class ScheduleRule:
    id: str
    device_id: str
    name: str
    enabled: bool
    zones: list[dict]  # [{zoneId, duration, sortOrder}]
    total_duration_seconds: int
    start_date: int     # epoch ms
    cycle_soak: bool
    et_skip: bool
```

### ScheduleEvent
```python
@dataclass
class ScheduleEvent:
    id: str
    device_id: str
    zone_id: str
    schedule_rule_id: str
    type: str   # RUNTIME, MANUAL, etc.
    event_type: str  # WATERING, etc.
    start_time: int  # epoch ms
    end_time: int    # epoch ms
    duration_seconds: int
    bytes_watered: int
```

### WeatherData
```python
@dataclass
class WeatherData:
    device_id: str
    timestamp: int
    et_today: float        # evapotranspiration inches
    et_tomorrow: float
    precip_today: float    # inches
    precip_tomorrow: float
    temp_high_f: float
    temp_low_f: float
    humidity: float
    wind_speed_mph: float
    forecast_daily: list[dict]  # next N days
```

---

## Moisture Model

### Water Balance Approach
```
moisture_% = max(0, min(100, current_water_depth / saturated_depth * 100))

Current water depth evolves daily:
  - Depletion from ETo:  depth -= ETo * Kc * efficiency
  - Replenishment from irrigation: depth += (nozzle_rate * runtime) / area
```

### Practical Field Capacity
- `saturated_depth` from API (`saturatedDepthOfWater`) is the 100% baseline
- `available_water` (`availableWater`) is the fraction that can be held
- `management_allowed_depletion` (MAD) is the fraction we can deplete before needing water

### Initial Moisture (if no data)
- Assume 80% of saturated depth on first run

### Moisture Depletion Rate
```
daily_depletion = ETo_today * crop_coefficient * (1 / efficiency)
```
ETo is in inches/day from the weather API.

---

## Water Consumption Model

```
monthly_gallons = sum over each watering event:
  (nozzle_rate_inches_per_hour * zone_area_sqft / 144) * duration_seconds / 3600 * 7.48052

Where:
  - nozzle_rate_inches_per_hour: from customNozzle.inchesPerHour
  - zone_area_sqft: yardAreaSquareFeet
  - conversion: cubic inches to gallons = 7.48052 / (12*12) per cubic foot... 
  Actually: gallons = (in/hr * sqft / 96.25) * hours
  Simplifies to: gallons = nozzle_rate * area * runtime_sec / 3600 / 96.25
```

---

## Dashboard Layout

### Per-Zone Card
```
┌──────────────────────────────────────┐
│ Zone 4 — Lawn                       │
│ ████████████░░░░ 72%                │
│ Last watered: 2 days ago             │
│ Next scheduled: Tomorrow 5:00 AM     │
│ Monthly: 1,245 gal                   │
└──────────────────────────────────────┘
```

### Moisture History Chart (Line)
- X: dates (last 30 days)
- Y: moisture % (0–100)
- One line per zone, color-coded
- Dashed horizontal line at MAD threshold (e.g., 50%)

### Monthly Water Usage
- Bar chart: total gallons per zone for current month

---

## File Structure
```
/workspace/rachio-dashboard/
├── SPEC.md
├── README.md
├── run_daily.sh
├── rachio/
│   ├── __init__.py
│   ├── api.py          # HTTP client
│   ├── models.py       # dataclasses
│   ├── moisture.py     # moisture estimation
│   ├── water_usage.py  # consumption estimation
│   ├── collector.py    # daily data collection
│   └── daily_report.py # report generation + charts
├── dashboard/
│   └── index.html
├── data/
│   ├── history.jsonl   # one JSON per zone per day
│   ├── moisture_history.png
│   └── zone_summary.png
└── tests/
    ├── test_api.py
    ├── test_moisture.py
    ├── test_water_usage.py
    └── test_collector.py
```

---

## Cron Job
- Runs daily at 8:00 AM PDT via OpenClaw cron tool
- Job name: `rachio-daily`
- Delivery: Discord channel 1485364811326947419
