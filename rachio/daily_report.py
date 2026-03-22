"""Daily report generation: charts and HTML dashboard."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from rachio.models import ZoneState

DATA_DIR = Path(__file__).parent.parent / "data"
DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"
MOISTURE_CHART_FILE = DATA_DIR / "moisture_history.png"
ZONE_SUMMARY_FILE = DATA_DIR / "zone_summary.png"


@dataclass
class ReportData:
    """Aggregated report data for dashboard rendering."""
    generated_at: str = ""
    zones: List[ZoneState] = field(default_factory=list)
    history: List[dict] = field(default_factory=list)
    total_monthly_gallons: float = 0.0
    days: int = 30

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "zones": [z.to_dict() for z in self.zones],
            "total_monthly_gallons": round(self.total_monthly_gallons, 1),
            "days": self.days,
        }


# ---------------------------------------------------------------------------
# History loading
# ---------------------------------------------------------------------------

def load_history(days: int = 30) -> List[dict]:
    """Load zone history from JSONL file."""
    history_file = DATA_DIR / "history.jsonl"
    if not history_file.exists():
        return []

    cutoff = datetime.now() - timedelta(days=days)
    cutoff_ts = int(cutoff.timestamp() * 1000)
    result: List[dict] = []

    with open(history_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if record.get("timestamp", 0) >= cutoff_ts:
                    result.append(record)
            except json.JSONDecodeError:
                continue

    # Sort by timestamp ascending
    result.sort(key=lambda r: r.get("timestamp", 0))
    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(states: Dict[str, ZoneState], history: List[dict], days: int = 30) -> ReportData:
    """Build a ReportData object from collected zone states and history."""
    total_gal = sum(s.monthly_gallons for s in states.values())
    return ReportData(
        generated_at=datetime.now().isoformat(),
        zones=list(states.values()),
        history=history,
        total_monthly_gallons=total_gal,
        days=days,
    )


# ---------------------------------------------------------------------------
# Moisture history chart
# ---------------------------------------------------------------------------

ZONE_COLORS = [
    "#3b82f6", "#22c55e", "#f97316", "#a855f7",
    "#ec4899", "#14b8a6", "#eab308", "#ef4444",
]


def render_moisture_chart(history: List[dict], output_path: Optional[Path] = None) -> Path:
    """Render a line chart of moisture % over time, one line per zone.

    Saves to `data/moisture_history.png`.
    """
    if not history:
        # Empty chart
        output_path = output_path or MOISTURE_CHART_FILE
        fig, ax = plt.subplots(figsize=(12, 5))
        ax.text(0.5, 0.5, "No moisture history available yet", ha="center", va="center", fontsize=14)
        ax.axis("off")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        return output_path

    # Group by zone_id
    by_zone: Dict[str, Dict[str, List]] = {}
    for record in history:
        zid = record.get("zone_id")
        if zid not in by_zone:
            by_zone[zid] = {"timestamps": [], "moisture": [], "names": []}
        ts = record.get("timestamp", 0)
        if ts:
            by_zone[zid]["timestamps"].append(datetime.fromtimestamp(ts / 1000))
            by_zone[zid]["moisture"].append(record.get("moisture_pct", 0))
            by_zone[zid]["names"].append(record.get("zone_name", zid))

    fig, ax = plt.subplots(figsize=(14, 6))

    for i, (zid, data) in enumerate(by_zone.items()):
        if not data["timestamps"]:
            continue
        color = ZONE_COLORS[i % len(ZONE_COLORS)]
        name = data["names"][0] if data["names"] else zid[:8]
        ax.plot(
            data["timestamps"],
            data["moisture"],
            label=name,
            color=color,
            linewidth=1.8,
            marker="o",
            markersize=2,
        )

    # Dashed optimal threshold line
    ax.axhline(y=80, color="#22c55e", linestyle="--", linewidth=1.2, label="Optimal (80%)")
    ax.axhline(y=50, color="#f97316", linestyle="--", linewidth=1.2, label="MAD threshold (50%)")

    ax.set_xlabel("Date")
    ax.set_ylabel("Moisture (% of field capacity)")
    ax.set_title("Zone Moisture History")
    ax.set_ylim(0, 105)
    ax.legend(loc="upper right", fontsize=8, ncol=2)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=5))
    fig.autofmt_xdate()
    ax.grid(True, alpha=0.3)

    output_path = output_path or MOISTURE_CHART_FILE
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return output_path


def render_zone_summary(states: Dict[str, ZoneState], output_path: Optional[Path] = None) -> Path:
    """Render a horizontal bar chart of current moisture vs target per zone.

    Saves to `data/zone_summary.png`.
    """
    if not states:
        output_path = output_path or ZONE_SUMMARY_FILE
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.text(0.5, 0.5, "No zone data available", ha="center", va="center", fontsize=14)
        ax.axis("off")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        return output_path

    names = [s.zone_name for s in states.values()]
    current = [s.moisture_pct for s in states.values()]
    targets = [s.target_moisture_pct for s in states.values()]

    y_pos = list(range(len(names)))
    colors = [ZONE_COLORS[i % len(ZONE_COLORS)] for i in range(len(names))]

    fig, ax = plt.subplots(figsize=(10, max(4, len(names) * 0.8 + 1)))

    bar_height = 0.35
    bars1 = ax.barh([y - bar_height / 2 for y in y_pos], current, height=bar_height, label="Current", color=colors)
    bars2 = ax.barh([y + bar_height / 2 for y in y_pos], targets, height=bar_height, label="Target", color="#6b7280", alpha=0.5)

    ax.set_yticks(y_pos)
    ax.set_yticklabels(names)
    ax.set_xlabel("Moisture (% of field capacity)")
    ax.set_title("Current vs Target Moisture by Zone")
    ax.set_xlim(0, 110)
    ax.legend(loc="lower right")
    ax.grid(True, axis="x", alpha=0.3)

    # Add value labels
    for bar, val in zip(bars1, current):
        ax.text(val + 1, bar.get_y() + bar.get_height() / 2, f"{val:.0f}%", va="center", fontsize=8)

    plt.tight_layout()
    output_path = output_path or ZONE_SUMMARY_FILE
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return output_path


# ---------------------------------------------------------------------------
# HTML Dashboard
# ---------------------------------------------------------------------------

def _moisture_bar_color(pct: float) -> str:
    if pct >= 85:
        return "#22c55e"
    elif pct >= 60:
        return "#eab308"
    elif pct >= 40:
        return "#f97316"
    else:
        return "#ef4444"


def _format_ts(ts: int) -> str:
    if not ts:
        return "Never"
    dt = datetime.fromtimestamp(ts / 1000)
    now = datetime.now()
    diff = now - dt
    if diff < timedelta(hours=1):
        return f"{int(diff.total_seconds() / 60)} min ago"
    elif diff < timedelta(days=1):
        return f"{int(diff.total_seconds() / 3600)} hrs ago"
    elif diff < timedelta(days=2):
        return "Yesterday"
    elif diff < timedelta(days=7):
        return f"{diff.days} days ago"
    else:
        return dt.strftime("%b %d")


def render_html_dashboard(report: ReportData, output_path: Optional[Path] = None) -> Path:
    """Render the HTML dashboard and save to dashboard/index.html."""
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    output_path = output_path or (DASHBOARD_DIR / "index.html")

    zone_cards_html = ""
    for i, zone in enumerate(report.zones):
        color = _moisture_bar_color(zone.moisture_pct)
        bar_color = _moisture_bar_color(zone.moisture_pct)
        last_watered = _format_ts(zone.last_watered_ts)
        next_sched = _format_ts(zone.next_schedule_ts) if zone.next_schedule_ts else "Not scheduled"
        gal = f"{zone.monthly_gallons:,.0f}" if zone.monthly_gallons else "0"

        zone_cards_html += f"""
        <div class="zone-card" style="--zone-color: {ZONE_COLORS[i % len(ZONE_COLORS)]}">
            <div class="zone-header">
                <h2 class="zone-name">{zone.zone_name}</h2>
                <span class="zone-moisture-value" style="color:{color}">{zone.moisture_pct:.0f}%</span>
            </div>
            <div class="moisture-bar-container">
                <div class="moisture-bar" style="width:{zone.moisture_pct}%; background:{bar_color}"></div>
                <div class="target-line" style="left:{zone.target_moisture_pct}%"></div>
            </div>
            <div class="zone-meta">
                <div class="meta-item"><span class="meta-label">Last Watered</span><span class="meta-value">{last_watered}</span></div>
                <div class="meta-item"><span class="meta-label">Next Scheduled</span><span class="meta-value">{next_sched}</span></div>
                <div class="meta-item"><span class="meta-label">Monthly Use</span><span class="meta-value">{gal} gal</span></div>
                <div class="meta-item"><span class="meta-label">Daily Depletion</span><span class="meta-value">{zone.daily_depletion_inches:.3f} in</span></div>
            </div>
        </div>
        """

    total_gal = f"{report.total_monthly_gallons:,.0f}"
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S PDT")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Rachio Dashboard</title>
<meta http-equiv="refresh" content="1800">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #111827;
    --card-bg: #1f2937;
    --card-border: #374151;
    --text-primary: #f9fafb;
    --text-secondary: #9ca3af;
    --accent: #3b82f6;
    --accent-green: #22c55e;
    --accent-yellow: #eab308;
    --accent-orange: #f97316;
    --accent-red: #ef4444;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text-primary); padding: 20px; }}
  .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--card-border); }}
  h1 {{ font-size: 1.5rem; color: var(--accent); }}
  .header-meta {{ font-size: 0.85rem; color: var(--text-secondary); text-align: right; }}
  .summary-bar {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .summary-card {{ background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 12px; padding: 16px; }}
  .summary-label {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-secondary); margin-bottom: 4px; }}
  .summary-value {{ font-size: 1.5rem; font-weight: 700; color: var(--accent-green); }}
  .section-title {{ font-size: 1.1rem; margin-bottom: 12px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.05em; }}
  .zones-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; margin-bottom: 32px; }}
  .zone-card {{ background: var(--card-bg); border: 1px solid var(--card-border); border-left: 4px solid var(--zone-color, var(--accent)); border-radius: 12px; padding: 16px; }}
  .zone-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
  .zone-name {{ font-size: 1rem; font-weight: 600; }}
  .zone-moisture-value {{ font-size: 1.4rem; font-weight: 700; }}
  .moisture-bar-container {{ position: relative; height: 12px; background: #374151; border-radius: 6px; overflow: visible; margin-bottom: 12px; }}
  .moisture-bar {{ height: 100%; border-radius: 6px; transition: width 0.5s ease; }}
  .target-line {{ position: absolute; top: -4px; bottom: -4px; width: 2px; background: white; opacity: 0.7; }}
  .zone-meta {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
  .meta-item {{ display: flex; flex-direction: column; gap: 2px; }}
  .meta-label {{ font-size: 0.7rem; text-transform: uppercase; color: var(--text-secondary); }}
  .meta-value {{ font-size: 0.9rem; font-weight: 500; }}
  .chart-container {{ background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 12px; padding: 20px; margin-bottom: 32px; }}
  .chart-container canvas {{ max-height: 350px; }}
  @media (prefers-color-scheme: light) {{
    :root {{ --bg: #f3f4f6; --card-bg: #ffffff; --card-border: #e5e7eb; --text-primary: #111827; --text-secondary: #6b7280; }}
  }}
</style>
</head>
<body>
<div class="header">
  <h1>🌊 Rachio Dashboard</h1>
  <div class="header-meta">
    <div>Updated: {generated}</div>
    <div style="font-size:0.75rem; margin-top:4px;">Auto-refreshes every 30 min</div>
  </div>
</div>

<div class="summary-bar">
  <div class="summary-card">
    <div class="summary-label">Total Monthly Water</div>
    <div class="summary-value">{total_gal} gal</div>
  </div>
  <div class="summary-card">
    <div class="summary-label">Active Zones</div>
    <div class="summary-value">{len(report.zones)}</div>
  </div>
  <div class="summary-card">
    <div class="summary-label">Report Period</div>
    <div class="summary-value">{report.days} days</div>
  </div>
</div>

<div class="section-title">Zones</div>
<div class="zones-grid">
{zone_cards_html}
</div>

<div class="chart-container">
  <div class="section-title">Moisture History ({report.days} days)</div>
  <canvas id="moistureChart"></canvas>
</div>

<script>
// Moisture history chart from inline data
const historyData = {json.dumps(report.history)};
const zoneMap = {{}};
historyData.forEach(r => {{ zoneMap[r.zone_id] = r.zone_name || r.zone_id; }});

// Group by zone
const byZone = {{}};
historyData.forEach(r => {{
  const zid = r.zone_id;
  if (!byZone[zid]) byZone[zid] = {{ labels: [], data: [] }};
  byZone[zid].labels.push(new Date(r.timestamp).toLocaleDateString());
  byZone[zid].data.push(r.moisture_pct);
}});

const zoneColors = {ZONE_COLORS};
const zids = Object.keys(byZone);
const datasets = zids.map((zid, i) => ({{
  label: zoneMap[zid] || zid,
  data: byZone[zid].data,
  borderColor: zoneColors[i % zoneColors.length],
  backgroundColor: zoneColors[i % zoneColors.length] + '33',
  tension: 0.3,
  fill: false,
  pointRadius: 2,
}}));

const ctx = document.getElementById('moistureChart').getContext('2d');
new Chart(ctx, {{
  type: 'line',
  data: {{ labels: byZone[zids[0]]?.labels || [], datasets }},
  options: {{
    responsive: true,
    scales: {{
      y: {{ min: 0, max: 105, title: {{ display: true, text: 'Moisture %' }} }},
      x: {{ title: {{ display: true, text: 'Date' }} }}
    }},
    plugins: {{
      annotation: {{
        annotations: {{
          optimal: {{ type: 'line', yMin: 80, yMax: 80, borderColor: '#22c55e', borderDash: [5,5], label: {{ display: true, content: 'Optimal 80%' }} }},
          mad: {{ type: 'line', yMin: 50, yMax: 50, borderColor: '#f97316', borderDash: [5,5] }}
        }}
      }},
      legend: {{ position: 'bottom' }}
    }}
  }}
}});
</script>
</body>
</html>"""

    with open(output_path, "w") as f:
        f.write(html)

    return output_path


def generate_full_report(states: Dict[str, ZoneState]) -> ReportData:
    """Generate everything: load history, build report, render charts + HTML."""
    history = load_history(30)
    report = generate_report(states, history, days=30)

    render_moisture_chart(history)
    render_zone_summary(states)
    render_html_dashboard(report)

    return report
