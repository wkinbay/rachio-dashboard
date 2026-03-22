# Rachio Sprinkler Dashboard

A daily-updated dashboard for Rachio sprinkler systems. Pulls data from the Rachio API, models zone moisture using a water-balance approach, tracks water consumption, and renders an HTML dashboard with charts.

## Features

- **Per-zone moisture estimation** using a water-balance model (ETo-based depletion + irrigation replenishment)
- **Monthly water consumption** tracking per zone
- **Historical moisture charts** (30-day line chart)
- **Zone summary** bar chart comparing current vs target moisture
- **HTML dashboard** with dark mode, auto-refresh every 30 min
- **Daily cron job** at 8:00 AM PDT

## Setup

### 1. Get a Rachio API Key

1. Open the Rachio app
2. Go to **Settings → API Access**
3. Generate an API token
4. Copy the key

### 2. Configure Environment Variable

```bash
export RACHIO_API_KEY="your-key-here"
```

Add this to your shell profile (`~/.zshrc`, `~/.bashrc`) or a `.env` file so it's always available.

### 3. Install Dependencies

```bash
source /workspace/.venv/bin/activate
pip install httpx matplotlib
```

### 4. Run Manually

```bash
cd /workspace/rachio-dashboard
source /workspace/.venv/bin/activate
./run_daily.sh
```

Or interactively:

```bash
cd /workspace/rachio-dashboard
source /workspace/.venv/bin/activate
python -c "
import sys; sys.path.insert(0, '.')
from rachio.collector import run
from rachio.daily_report import generate_full_report
states = run()
report = generate_full_report(states)
print(f'Dashboard updated: {len(report.zones)} zones')
"
```

### 5. View the Dashboard

Open `/workspace/rachio-dashboard/dashboard/index.html` in a browser.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `RACHIO_API_KEY` | Yes | Your Rachio API token |

## File Structure

```
rachio-dashboard/
├── SPEC.md                  # Design specification
├── README.md                # This file
├── run_daily.sh            # Daily runner script
├── rachio/
│   ├── __init__.py
│   ├── api.py               # Rachio API HTTP client
│   ├── models.py            # Dataclasses (Device, Zone, etc.)
│   ├── moisture.py           # Water-balance moisture model
│   ├── water_usage.py       # Gallon consumption estimation
│   ├── collector.py          # Daily data collection job
│   └── daily_report.py       # Report + chart generation
├── dashboard/
│   └── index.html           # Rendered dashboard (auto-generated)
├── data/
│   ├── history.jsonl        # 30-day zone history (auto-generated)
│   ├── moisture_history.png # Moisture chart (auto-generated)
│   └── zone_summary.png     # Zone bar chart (auto-generated)
└── tests/                   # Unit tests
```

## Cron Job

A cron job is registered via OpenClaw to run daily at 8:00 AM PDT:

```
Job: rachio-daily
Schedule: Daily at 8:00 AM PDT
Delivery: Discord channel 1485364811326947419
Payload: Run /workspace/rachio-dashboard/run_daily.sh and report any errors
```

## Moisture Model

The moisture model uses a **water-balance approach**:

1. **Starting point**: Zone's `depthOfWater` from the API (or 80% of saturation if not available)
2. **Depletion**: Daily ETo (evapotranspiration) from Rachio's weather data, adjusted by crop coefficient and efficiency
3. **Replenishment**: Each watering event adds water based on nozzle rate × runtime × efficiency
4. **Output**: Moisture as a percentage of the zone's practical field capacity

### Key Parameters

| Parameter | Source | Default |
|-----------|--------|---------|
| `rootZoneDepth` | API | 6 inches |
| `availableWater` | API | 0.17 in/in |
| `managementAllowedDepletion` | API | 50% |
| `customCrop.coefficient` | API | 0.8 |
| `customNozzle.inchesPerHour` | API | 1.5 in/hr |
| `efficiency` | API | 0.8 |

## Water Consumption Model

```
gallons = (nozzle_rate_inhr × area_sqft / 96.25) × runtime_hours
```

Where 96.25 is the conversion factor from (in/hr × sqft) to gallons/hr.

## Running Tests

```bash
cd /workspace/rachio-dashboard
source /workspace/.venv/bin/activate
pytest tests/ -v
```

## Troubleshooting

**"RACHIO_API_KEY environment variable is not set"**

Ensure the env var is exported in the shell running the script, or set it inline:

```bash
RACHIO_API_KEY="your-key" python -c "from rachio.api import RachioClient; ..."
```

**Dashboard shows "No moisture history available yet"**

This is normal on the first run. History accumulates over subsequent days.

**Rate limiting (429 errors)**

Rachio's API allows 3,500 requests/day. The client retries on 429 with exponential backoff.
