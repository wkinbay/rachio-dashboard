"""Thin HTTP client for Rachio API v1.

Key discovery: Rachio does NOT expose separate zone/schedule_rule/schedule_event
endpoints. Instead, all zone and schedule data is embedded directly in the device
object returned by GET /person/{person_id}. The only live-data endpoint is
GET /device/{device_id}/forecast (weather).
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx

BASE_URL = "https://api.rach.io/1/public"
TIMEOUT = 30.0
RETRIES = 3
RETRY_DELAY = 5.0


class RachioError(Exception):
    """Base exception for Rachio API errors."""
    pass


class RateLimitError(RachioError):
    """Raised when rate limit is exceeded."""
    pass


class NotFoundError(RachioError):
    """Raised when a resource is not found."""
    pass


class AuthenticationError(RachioError):
    """Raised when authentication fails."""
    pass


class RachioClient:
    """Low-level client for Rachio API v1.

    Authentication requires the environment variable RACHIO_API_KEY.

    Important: zones and schedule rules are EMBEDDED in the device object
    returned by get_person_full(). Do NOT call /device/{id}/zone or
    /device/{id}/schedule_rule — those endpoints do not exist (404).
    """

    def __init__(self, api_key: Optional[str] = None, client: Optional[httpx.Client] = None):
        self.api_key = api_key or os.environ.get("RACHIO_API_KEY", "")
        if not self.api_key:
            raise ValueError("RACHIO_API_KEY environment variable is not set")
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: Optional[dict] = None) -> dict | list:
        """Make a GET request with retry logic."""
        url = f"{BASE_URL}{path}"
        for attempt in range(RETRIES):
            try:
                with httpx.Client() as c:
                    resp = c.get(url, headers=self._headers(), params=params, timeout=TIMEOUT)
                return self._handle_response(resp)
            except (RateLimitError, httpx.TimeoutException) as e:
                if attempt < RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    raise

    def _handle_response(self, resp: httpx.Response) -> dict | list:
        if resp.status_code == 401:
            raise AuthenticationError("Invalid or missing API key")
        if resp.status_code == 404:
            raise NotFoundError(f"Resource not found: {resp.url}")
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", RETRY_DELAY))
            raise RateLimitError(f"Rate limited. Retry after {retry_after}s")
        if not resp.is_success:
            raise RachioError(f"API error {resp.status_code}: {resp.text}")
        return resp.json()

    # -------------------------------------------------------------------------
    # Person
    # -------------------------------------------------------------------------

    def get_person_id(self) -> str:
        """Get the current user's person ID.

        Calls GET /person/info which returns {"id": "..."}.
        """
        return self._get("/person/info")["id"]

    def get_person_full(self) -> dict:
        """Get the full person object including all devices, zones, and schedule rules.

        Calls GET /person/{person_id}. All data is embedded in the returned dict:
          - person["devices"]       → list of device dicts
          - device["zones"]         → list of zone dicts (no separate API call exists)
          - device["scheduleRules"] → list of schedule rule dicts
        """
        person_id = self.get_person_id()
        return self._get(f"/person/{person_id}")

    def get_devices(self) -> list[dict]:
        """Get all devices for the authenticated user.

        Zones and schedule rules are already embedded in each device dict.
        """
        person = self.get_person_full()
        return person.get("devices", [])

    # -------------------------------------------------------------------------
    # Zones (always embedded in device object — no separate API call)
    # -------------------------------------------------------------------------

    @staticmethod
    def get_zones(device: dict) -> list[dict]:
        """Extract zones from a device dict (already populated by get_person_full)."""
        return device.get("zones", [])

    @staticmethod
    def get_zone(device: dict, zone_id: str) -> dict:
        """Find a specific zone by ID within a device dict."""
        for z in device.get("zones", []):
            if z["id"] == zone_id:
                return z
        raise NotFoundError(f"Zone {zone_id} not found")

    # -------------------------------------------------------------------------
    # Schedule Rules (always embedded in device object — no separate API call)
    # -------------------------------------------------------------------------

    @staticmethod
    def get_schedule_rules(device: dict) -> list[dict]:
        """Extract regular schedule rules from a device dict."""
        return device.get("scheduleRules", [])

    @staticmethod
    def get_flex_schedule_rules(device: dict) -> list[dict]:
        """Extract flex schedule rules from a device dict."""
        return device.get("flexScheduleRules", [])

    @staticmethod
    def get_all_schedule_rules(device: dict) -> list[dict]:
        """Extract both regular and flex schedule rules."""
        return device.get("scheduleRules", []) + device.get("flexScheduleRules", [])

    # -------------------------------------------------------------------------
    # Weather / Forecast
    # -------------------------------------------------------------------------

    def get_watering_events(self, device_id: str, start_time_ms: int, end_time_ms: int) -> list[dict]:
        """Get watering events for a device within a time range.

        Returns events with topic=WATERING and subType in SCHEDULE_COMPLETED,
        ZONE_COMPLETED, etc. Each event's summary field contains the duration
        in text form (e.g. "ZoneName ran for 7 minutes.").

        Args:
            device_id: The device UUID
            start_time_ms: Start of range in epoch milliseconds
            end_time_ms: End of range in epoch milliseconds

        Returns:
            List of event dicts
        """
        return self._get(
            f"/device/{device_id}/event",
            params={"startTime": start_time_ms, "endTime": end_time_ms},
        )

    def get_forecast(self, device_id: str, units: str = "US") -> dict:
        """Get weather forecast for a device location.

        Returns current conditions + forecast dicts with temperature, humidity,
        windSpeed, precipProbability, etc. No ETo field — calculate locally.

        units: "US" or "METRIC" (not "IMPERIAL").
        """
        return self._get(f"/device/{device_id}/forecast", params={"units": units})
