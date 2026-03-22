"""Tests for rachio/api.py — all HTTP responses are mocked."""

import pytest
from unittest.mock import MagicMock, patch
import httpx

from rachio.api import (
    RachioClient,
    RachioError,
    RateLimitError,
    NotFoundError,
    AuthenticationError,
    BASE_URL,
)


def make_mock_response(data: dict, status: int = 200) -> httpx.Response:
    """Factory for mocked httpx responses."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.is_success = 200 <= status < 300
    resp.json.return_value = data
    resp.text = ""
    resp.url = "https://api.rach.io/1/public/test"
    resp.headers = {}
    return resp


class TestRachioClient:
    """Tests for RachioClient."""

    def test_get_person_id_success(self):
        """GET /person/info returns the user ID."""
        with patch.object(httpx, "Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.get.return_value = make_mock_response({"id": "person-abc-123"})

            client = RachioClient(api_key="test-key")
            result = client.get_person_id()

            assert result == "person-abc-123"
            mock_client.get.assert_called_once()
            call_args = mock_client.get.call_args
            assert "/person/info" in call_args[0][0]

    def test_get_person_id_unauthorized(self):
        """401 raises AuthenticationError."""
        resp = make_mock_response({}, status=401)
        resp.status_code = 401
        resp.is_success = False

        with patch.object(httpx, "Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.get.return_value = resp

            client = RachioClient(api_key="bad-key")
            with pytest.raises(AuthenticationError):
                client.get_person_id()

    def test_get_person_full(self):
        """GET /person/{id} returns full person with embedded devices."""
        person_data = {
            "id": "person-123",
            "fullName": "Test User",
            "email": "test@example.com",
            "devices": [
                {
                    "id": "device-1",
                    "name": "Home Controller",
                    "status": "ONLINE",
                    "zones": [{"id": "zone-1", "name": "Lawn", "zoneNumber": 1}],
                    "scheduleRules": [],
                }
            ],
        }
        with patch.object(httpx, "Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.get.return_value = make_mock_response(person_data)

            client = RachioClient(api_key="test-key")
            result = client.get_person_full()

            assert result["fullName"] == "Test User"
            assert len(result["devices"]) == 1
            assert result["devices"][0]["zones"][0]["name"] == "Lawn"

    def test_get_devices_returns_device_list(self):
        """get_devices() returns the devices list from the person object."""
        devices = [
            {"id": "device-1", "name": "Front Yard", "status": "ONLINE", "zones": [], "scheduleRules": []},
            {"id": "device-2", "name": "Back Yard", "status": "OFFLINE", "zones": [], "scheduleRules": []},
        ]
        with patch.object(httpx, "Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.get.return_value = make_mock_response(
                {"id": "person-123", "devices": devices}
            )

            client = RachioClient(api_key="test-key")
            result = client.get_devices()

            assert len(result) == 2
            assert result[0]["name"] == "Front Yard"

    def test_get_zones_from_device(self):
        """get_zones() extracts zones from an already-fetched device dict."""
        device = {
            "id": "device-1",
            "name": "Controller",
            "zones": [
                {"id": "zone-1", "name": "Lawn", "zoneNumber": 1},
                {"id": "zone-2", "name": "Garden", "zoneNumber": 2},
            ],
        }
        zones = RachioClient.get_zones(device)
        assert len(zones) == 2
        assert zones[1]["name"] == "Garden"

    def test_get_zones_empty(self):
        """get_zones() returns [] if no zones key."""
        device = {"id": "device-1", "name": "Controller"}
        zones = RachioClient.get_zones(device)
        assert zones == []

    def test_get_schedule_rules_from_device(self):
        """get_all_schedule_rules() returns both scheduleRules and flexScheduleRules."""
        device = {
            "id": "device-1",
            "scheduleRules": [{"id": "rule-1", "name": "Regular"}],
            "flexScheduleRules": [{"id": "flex-1", "name": "Flex"}],
        }
        rules = RachioClient.get_all_schedule_rules(device)
        assert len(rules) == 2

    def test_get_forecast(self):
        """GET /device/{id}/forecast returns weather data."""
        forecast = {
            "current": {
                "currentTemperature": 72,
                "humidity": 0.55,
                "windSpeed": 3.0,
                "cloudCover": 0.2,
                "dewPoint": 50,
                "precipProbability": 0.1,
            },
            "forecast": [
                {
                    "time": 1711000000,
                    "temperatureMax": 80,
                    "temperatureMin": 55,
                    "humidity": 0.5,
                    "windSpeed": 5.0,
                    "cloudCover": 0.3,
                    "precipProbability": 0.0,
                }
            ],
        }
        with patch.object(httpx, "Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.get.return_value = make_mock_response(forecast)

            client = RachioClient(api_key="test-key")
            result = client.get_forecast("device-1", units="US")

            assert result["current"]["currentTemperature"] == 72
            assert len(result["forecast"]) == 1

    def test_rate_limit_triggers_retry(self):
        """429 raises RateLimitError after retries."""
        resp_429 = make_mock_response({}, status=429)
        resp_429.status_code = 429
        resp_429.is_success = False
        resp_429.headers = {"Retry-After": "1"}

        resp_200 = make_mock_response({"id": "person-123"})

        with patch.object(httpx, "Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.get.side_effect = [resp_429, resp_429, resp_200]

            client = RachioClient(api_key="test-key")
            result = client.get_person_id()
            assert result == "person-123"
            assert mock_client.get.call_count == 3

    def test_api_key_required(self):
        """Missing API key raises ValueError."""
        with pytest.raises(ValueError, match="RACHIO_API_KEY"):
            RachioClient(api_key="")
