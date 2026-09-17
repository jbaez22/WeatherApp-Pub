"""
Integration tests for backend/lambda/weather_handler.py

All collaborators (cache, secrets, weather_client) are mocked so the
handler's orchestration logic can be tested in full isolation.

Covers: OPTIONS preflight, validation failure, cache hit, cache miss +
successful live fetch, SSM failure, city not found, weather client error,
cache write failure (non-fatal), and response shape / CORS headers.
"""

import json
import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

from weather_handler import handler

_SAMPLE_WEATHER = {
    "current": {
        "name": "Austin",
        "sys": {"country": "US"},
        "dt": 1751500000,
        "main": {"temp": 32.0, "feels_like": 35.0, "humidity": 55, "pressure": 1008},
        "weather": [{"description": "sunny", "icon": "01d"}],
        "wind": {"speed": 3.0},
        "visibility": 10000,
    },
    "daily": [
        {
            "dt": 1751500000,
            "high": 36.0,
            "low": 28.0,
            "icon": "01d",
            "desc": "sunny",
            "pop": 0.05,
            "uvi": 8.5,
            "sunrise": 1751480000,
            "sunset": 1751530000,
            "humidity": 55,
            "wind_speed": 3.0,
        }
    ],
    "hourly": [
        {
            "dt": 1751500000,
            "temp": 32.0,
            "feels_like": 35.0,
            "icon": "01d",
            "desc": "sunny",
            "pop": 0.05,
            "humidity": 55,
            "wind_speed": 3.0,
            "wind_deg": 180,
            "pressure": 1008,
            "precip_mm": 0.25,
        }
    ],
}


def _event(city: str | None = "Austin", method: str = "GET", route_key: str = "GET /weather") -> dict:
    """Build a minimal API Gateway HTTP API proxy event."""
    return {
        "routeKey": route_key,
        "requestContext": {"http": {"method": method}},
        "queryStringParameters": {"city": city} if city is not None else {},
    }


def _locate_event(lat: str = "40.71", lon: str = "-74.01") -> dict:
    """Build a /locate proxy event."""
    return {
        "routeKey": "GET /locate",
        "requestContext": {"http": {"method": "GET"}},
        "queryStringParameters": {"lat": lat, "lon": lon},
    }


class TestOptionsPreFlight:
    def test_options_returns_200(self):
        response = handler(_event(method="OPTIONS"), None)
        assert response["statusCode"] == 200

    def test_options_body_is_empty_object(self):
        response = handler(_event(method="OPTIONS"), None)
        assert json.loads(response["body"]) == {}


class TestInputValidation:
    def test_missing_city_returns_400(self):
        response = handler(_event(city=None), None)
        assert response["statusCode"] == 400

    def test_empty_city_returns_400(self):
        response = handler(_event(city=""), None)
        assert response["statusCode"] == 400

    def test_invalid_characters_returns_400(self):
        response = handler(_event(city="<script>"), None)
        assert response["statusCode"] == 400

    def test_400_body_contains_error_flag(self):
        response = handler(_event(city=""), None)
        body = json.loads(response["body"])
        assert body.get("error") is True

    def test_400_body_contains_message(self):
        response = handler(_event(city=""), None)
        body = json.loads(response["body"])
        assert "message" in body


class TestCacheHit:
    def test_cache_hit_returns_200(self):
        with patch("weather_handler.get_cached_weather", return_value=_SAMPLE_WEATHER):
            response = handler(_event(), None)
        assert response["statusCode"] == 200

    def test_cache_hit_body_matches_cached_data(self):
        with patch("weather_handler.get_cached_weather", return_value=_SAMPLE_WEATHER):
            response = handler(_event(), None)
        assert json.loads(response["body"]) == _SAMPLE_WEATHER

    def test_cache_hit_skips_api_call(self):
        with patch("weather_handler.get_cached_weather", return_value=_SAMPLE_WEATHER), \
             patch("weather_handler.fetch_weather") as mock_fetch:
            handler(_event(), None)
        mock_fetch.assert_not_called()


class TestCacheMissLiveFetch:
    def _patches(self, weather_data=None):
        """Return a context manager that stacks all three patches."""
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch("weather_handler.get_cached_weather", return_value=None))
        stack.enter_context(patch("weather_handler.get_api_key", return_value="test-key"))
        stack.enter_context(
            patch("weather_handler.fetch_weather", return_value=weather_data or _SAMPLE_WEATHER)
        )
        stack.enter_context(patch("weather_handler.set_cached_weather"))
        return stack

    def test_cache_miss_returns_200(self):
        with self._patches():
            response = handler(_event(), None)
        assert response["statusCode"] == 200

    def test_cache_miss_body_contains_weather_data(self):
        with self._patches():
            response = handler(_event(), None)
        assert json.loads(response["body"]) == _SAMPLE_WEATHER

    def test_cache_miss_writes_to_cache(self):
        with patch("weather_handler.get_cached_weather", return_value=None), \
             patch("weather_handler.get_api_key", return_value="key"), \
             patch("weather_handler.fetch_weather", return_value=_SAMPLE_WEATHER), \
             patch("weather_handler.set_cached_weather") as mock_set:
            handler(_event(), None)
        mock_set.assert_called_once_with("austin", _SAMPLE_WEATHER)

    def test_city_normalized_to_lowercase_for_cache(self):
        with patch("weather_handler.get_cached_weather") as mock_get, \
             patch("weather_handler.get_api_key", return_value="key"), \
             patch("weather_handler.fetch_weather", return_value=_SAMPLE_WEATHER), \
             patch("weather_handler.set_cached_weather"):
            mock_get.return_value = None
            handler(_event(city="AUSTIN"), None)
        mock_get.assert_called_once_with("austin")


class TestErrorPaths:
    def test_ssm_failure_returns_503(self):
        from secrets_manager import SecretsError
        with patch("weather_handler.get_cached_weather", return_value=None), \
             patch("weather_handler.get_api_key", side_effect=SecretsError("SSM down")):
            response = handler(_event(), None)
        assert response["statusCode"] == 503

    def test_city_not_found_returns_404(self):
        from weather_client import CityNotFoundError
        with patch("weather_handler.get_cached_weather", return_value=None), \
             patch("weather_handler.get_api_key", return_value="key"), \
             patch("weather_handler.fetch_weather", side_effect=CityNotFoundError("Not found")):
            response = handler(_event(city="Atlantis"), None)
        assert response["statusCode"] == 404

    def test_weather_client_error_returns_502_when_no_stale_cache(self):
        from weather_client import WeatherClientError
        with patch("weather_handler.get_cached_weather", return_value=None), \
             patch("weather_handler.get_api_key", return_value="key"), \
             patch("weather_handler.fetch_weather", side_effect=WeatherClientError("API down")), \
             patch("weather_handler.get_stale_cached_weather", return_value=None):
            response = handler(_event(), None)
        assert response["statusCode"] == 502

    def test_weather_client_error_falls_back_to_stale_cache(self):
        from weather_client import WeatherClientError
        with patch("weather_handler.get_cached_weather", return_value=None), \
             patch("weather_handler.get_api_key", return_value="key"), \
             patch("weather_handler.fetch_weather", side_effect=WeatherClientError("API down")), \
             patch("weather_handler.get_stale_cached_weather", return_value=dict(_SAMPLE_WEATHER)):
            response = handler(_event(), None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["stale"] is True
        assert "stale_reason" in body
        assert body["current"]["name"] == "Austin"

    def test_stale_fallback_not_attempted_unless_owm_fails(self):
        """Confirm the stale-cache fallback path is only reached on
        WeatherClientError — a normal successful fetch must never call it."""
        with patch("weather_handler.get_cached_weather", return_value=None), \
             patch("weather_handler.get_api_key", return_value="key"), \
             patch("weather_handler.fetch_weather", return_value=_SAMPLE_WEATHER), \
             patch("weather_handler.set_cached_weather"), \
             patch("weather_handler.get_stale_cached_weather") as mock_stale:
            response = handler(_event(), None)
        assert response["statusCode"] == 200
        mock_stale.assert_not_called()

    def test_cache_read_error_falls_through_to_live_fetch(self):
        from cache import CacheError
        with patch("weather_handler.get_cached_weather", side_effect=CacheError("DB error")), \
             patch("weather_handler.get_api_key", return_value="key"), \
             patch("weather_handler.fetch_weather", return_value=_SAMPLE_WEATHER), \
             patch("weather_handler.set_cached_weather"):
            response = handler(_event(), None)
        assert response["statusCode"] == 200

    def test_cache_write_error_does_not_affect_response(self):
        from cache import CacheError
        with patch("weather_handler.get_cached_weather", return_value=None), \
             patch("weather_handler.get_api_key", return_value="key"), \
             patch("weather_handler.fetch_weather", return_value=_SAMPLE_WEATHER), \
             patch("weather_handler.set_cached_weather", side_effect=CacheError("write fail")):
            response = handler(_event(), None)
        assert response["statusCode"] == 200


class TestLocateRoute:
    def test_locate_returns_200_with_city(self):
        with patch("weather_handler.get_api_key", return_value="key"), \
             patch("weather_handler.reverse_geocode", return_value="New York, US"):
            response = handler(_locate_event(), None)
        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"city": "New York, US"}

    def test_locate_missing_lat_returns_400(self):
        event = _locate_event()
        event["queryStringParameters"] = {"lon": "-74.01"}
        response = handler(event, None)
        assert response["statusCode"] == 400

    def test_locate_missing_lon_returns_400(self):
        event = _locate_event()
        event["queryStringParameters"] = {"lat": "40.71"}
        response = handler(event, None)
        assert response["statusCode"] == 400

    def test_locate_invalid_lat_returns_400(self):
        response = handler(_locate_event(lat="999"), None)
        assert response["statusCode"] == 400

    def test_locate_ssm_failure_returns_503(self):
        from secrets_manager import SecretsError
        with patch("weather_handler.get_api_key", side_effect=SecretsError("SSM down")):
            response = handler(_locate_event(), None)
        assert response["statusCode"] == 503

    def test_locate_city_not_found_returns_404(self):
        from weather_client import CityNotFoundError
        with patch("weather_handler.get_api_key", return_value="key"), \
             patch("weather_handler.reverse_geocode", side_effect=CityNotFoundError("no city")):
            response = handler(_locate_event(), None)
        assert response["statusCode"] == 404

    def test_locate_client_error_returns_502(self):
        from weather_client import WeatherClientError
        with patch("weather_handler.get_api_key", return_value="key"), \
             patch("weather_handler.reverse_geocode", side_effect=WeatherClientError("API down")):
            response = handler(_locate_event(), None)
        assert response["statusCode"] == 502

    def test_unknown_route_returns_404(self):
        event = _locate_event()
        event["routeKey"] = "GET /unknown"
        response = handler(event, None)
        assert response["statusCode"] == 404


class TestHealthRoute:
    def test_health_returns_200(self):
        response = handler(_event(city=None, route_key="GET /health"), None)
        assert response["statusCode"] == 200

    def test_health_body_reports_status_and_region(self, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "us-west-2")
        response = handler(_event(city=None, route_key="GET /health"), None)
        assert json.loads(response["body"]) == {"status": "ok", "region": "us-west-2"}

    def test_health_does_not_touch_cache_or_secrets(self):
        with patch("weather_handler.get_cached_weather") as mock_cache, \
             patch("weather_handler.get_api_key") as mock_secrets:
            handler(_event(city=None, route_key="GET /health"), None)
        mock_cache.assert_not_called()
        mock_secrets.assert_not_called()


class TestResponseShape:
    def test_response_has_status_code(self):
        with patch("weather_handler.get_cached_weather", return_value=_SAMPLE_WEATHER):
            response = handler(_event(), None)
        assert "statusCode" in response

    def test_response_has_headers(self):
        with patch("weather_handler.get_cached_weather", return_value=_SAMPLE_WEATHER):
            response = handler(_event(), None)
        assert "headers" in response

    def test_response_has_body(self):
        with patch("weather_handler.get_cached_weather", return_value=_SAMPLE_WEATHER):
            response = handler(_event(), None)
        assert "body" in response

    def test_cors_origin_header_present(self):
        with patch("weather_handler.get_cached_weather", return_value=_SAMPLE_WEATHER):
            response = handler(_event(), None)
        assert "Access-Control-Allow-Origin" in response["headers"]

    def test_content_type_is_json(self):
        with patch("weather_handler.get_cached_weather", return_value=_SAMPLE_WEATHER):
            response = handler(_event(), None)
        assert response["headers"]["Content-Type"] == "application/json"

    def test_body_is_valid_json_string(self):
        with patch("weather_handler.get_cached_weather", return_value=_SAMPLE_WEATHER):
            response = handler(_event(), None)
        parsed = json.loads(response["body"])
        assert isinstance(parsed, dict)

    def test_security_headers_present(self):
        with patch("weather_handler.get_cached_weather", return_value=_SAMPLE_WEATHER):
            response = handler(_event(), None)
        headers = response["headers"]
        assert "Strict-Transport-Security" in headers
        assert "X-Content-Type-Options" in headers
        assert "X-Frame-Options" in headers
