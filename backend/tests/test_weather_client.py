"""
Unit tests for backend/lambda/weather_client.py — V2.

Uses requests-mock to intercept HTTP calls — no real network traffic.

Covers the two-step fetch pattern:
  Step 1: GET /geo/1.0/direct   — geocode city name to lat/lon + display name
  Step 2: GET /data/3.0/onecall — current, 7-day daily, 48-hour hourly

Error paths covered for both steps: empty geocode list, HTTP 404/401/429/500,
timeout, and connection error. Response shaping: field mapping, 7-day slice,
48-hour slice, optional-field defaults (pop, uvi, visibility).
"""

import sys
import os

import pytest
import requests
import requests_mock as requests_mock_lib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

from weather_client import (
    CityNotFoundError,
    WeatherClientError,
    fetch_weather,
    reverse_geocode,
)

_GEO_REVERSE_URL = "https://api.openweathermap.org/geo/1.0/reverse"

_API_KEY = "test-key-abc"
_CITY = "london"

_GEO_URL = "https://api.openweathermap.org/geo/1.0/direct"
_ONECALL_URL = "https://api.openweathermap.org/data/3.0/onecall"
_AIR_POLLUTION_URL = "https://api.openweathermap.org/data/2.5/air_pollution"

_GEO_RESPONSE = [
    {"name": "London", "lat": 51.5074, "lon": -0.1278, "country": "GB"}
]

_AIR_POLLUTION_RESPONSE = {
    "coord": [-0.1278, 51.5074],
    "list": [
        {
            "dt": 1751500000,
            "main": {"aqi": 2},
            "components": {
                "co": 233.0,
                "no": 0.02,
                "no2": 6.6,
                "o3": 68.5,
                "so2": 1.4,
                "pm2_5": 3.5,
                "pm10": 4.6,
                "nh3": 0.7,
            },
        }
    ],
}


def _make_daily(count: int = 8) -> list:
    return [
        {
            "dt": 1751500000 + i * 86400,
            "temp": {"max": 17.5, "min": 12.0},
            "weather": [{"icon": "02d", "description": "few clouds"}],
            "pop": 0.2,
            "uvi": 3.5,
            "sunrise": 1751480000 + i * 86400,
            "sunset": 1751530000 + i * 86400,
            "humidity": 65,
            "wind_speed": 4.5,
        }
        for i in range(count)
    ]


def _make_hourly(count: int = 48) -> list:
    return [
        {
            "dt": 1751500000 + i * 3600,
            "temp": 15.0,
            "feels_like": 13.0,
            "weather": [{"icon": "02d", "description": "few clouds"}],
            "pop": 0.1,
            "humidity": 70,
            "wind_speed": 3.5,
            "wind_deg": 225,
            "pressure": 1012,
            "rain": {"1h": 0.5},
        }
        for i in range(count)
    ]


_ONECALL_RESPONSE = {
    "lat": 51.5074,
    "lon": -0.1278,
    "timezone": "Europe/London",
    "current": {
        "dt": 1751500000,
        "temp": 15.3,
        "feels_like": 13.1,
        "humidity": 72,
        "pressure": 1012,
        "wind_speed": 4.5,
        "visibility": 10000,
        "weather": [{"icon": "02d", "description": "few clouds"}],
    },
    "daily": _make_daily(8),
    "hourly": _make_hourly(48),
}


def _happy(m, geo=None, onecall=None, air_quality=None):
    """Register all three endpoints for a successful fetch."""
    m.get(_GEO_URL, json=geo if geo is not None else _GEO_RESPONSE)
    m.get(_ONECALL_URL, json=onecall if onecall is not None else _ONECALL_RESPONSE)
    m.get(_AIR_POLLUTION_URL, json=air_quality if air_quality is not None else _AIR_POLLUTION_RESPONSE)


class TestFetchWeatherHappyPath:
    def test_returns_current_daily_hourly_keys(self):
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            result = fetch_weather(_CITY, _API_KEY)

        assert "current" in result
        assert "daily" in result
        assert "hourly" in result

    def test_current_name_comes_from_geocode(self):
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            result = fetch_weather(_CITY, _API_KEY)

        assert result["current"]["name"] == "London"

    def test_current_country_comes_from_geocode(self):
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            result = fetch_weather(_CITY, _API_KEY)

        assert result["current"]["sys"]["country"] == "GB"

    def test_current_temp_comes_from_onecall(self):
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            result = fetch_weather(_CITY, _API_KEY)

        assert result["current"]["main"]["temp"] == 15.3

    def test_daily_trimmed_to_seven_entries(self):
        """OWM returns 8 daily entries; contract exposes only 7."""
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            result = fetch_weather(_CITY, _API_KEY)

        assert len(result["daily"]) == 7

    def test_hourly_capped_at_forty_eight_entries(self):
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            result = fetch_weather(_CITY, _API_KEY)

        assert len(result["hourly"]) == 48

    def test_daily_entry_has_all_required_fields(self):
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            result = fetch_weather(_CITY, _API_KEY)

        day = result["daily"][0]
        for field in ("dt", "high", "low", "icon", "desc", "pop", "uvi",
                      "sunrise", "sunset", "humidity", "wind_speed"):
            assert field in day, f"daily[0] missing field '{field}'"

    def test_daily_maps_temp_max_to_high_and_min_to_low(self):
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            result = fetch_weather(_CITY, _API_KEY)

        assert result["daily"][0]["high"] == 17.5
        assert result["daily"][0]["low"] == 12.0

    def test_hourly_entry_has_all_required_fields(self):
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            result = fetch_weather(_CITY, _API_KEY)

        hour = result["hourly"][0]
        for field in ("dt", "temp", "feels_like", "icon", "desc", "pop",
                      "humidity", "wind_speed", "wind_deg", "pressure", "precip_mm"):
            assert field in hour, f"hourly[0] missing field '{field}'"

    def test_api_key_sent_to_onecall(self):
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            fetch_weather(_CITY, _API_KEY)

        req = next(r for r in m.request_history if _ONECALL_URL in r.url)
        assert req.qs.get("appid") == [_API_KEY]

    def test_units_metric_sent_to_onecall(self):
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            fetch_weather(_CITY, _API_KEY)

        req = next(r for r in m.request_history if _ONECALL_URL in r.url)
        assert req.qs.get("units") == ["metric"]

    def test_minutely_and_alerts_excluded_from_onecall(self):
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            fetch_weather(_CITY, _API_KEY)

        req = next(r for r in m.request_history if _ONECALL_URL in r.url)
        exclude_val = req.qs.get("exclude", [""])[0]
        assert "minutely" in exclude_val
        assert "alerts" in exclude_val

    def test_api_key_sent_to_geocode(self):
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            fetch_weather(_CITY, _API_KEY)

        req = next(r for r in m.request_history if _GEO_URL in r.url)
        assert req.qs.get("appid") == [_API_KEY]

    def test_city_name_sent_to_geocode(self):
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            fetch_weather(_CITY, _API_KEY)

        req = next(r for r in m.request_history if _GEO_URL in r.url)
        assert req.qs.get("q") == [_CITY]


class TestResponseShapingEdgeCases:
    def test_missing_visibility_defaults_to_10000(self):
        current_no_vis = {k: v for k, v in _ONECALL_RESPONSE["current"].items()
                         if k != "visibility"}
        onecall = {**_ONECALL_RESPONSE, "current": current_no_vis}

        with requests_mock_lib.Mocker() as m:
            _happy(m, onecall=onecall)
            result = fetch_weather(_CITY, _API_KEY)

        assert result["current"]["visibility"] == 10000

    def test_missing_pop_in_daily_defaults_to_zero(self):
        daily = [{k: v for k, v in day.items() if k != "pop"} for day in _make_daily(8)]
        onecall = {**_ONECALL_RESPONSE, "daily": daily}

        with requests_mock_lib.Mocker() as m:
            _happy(m, onecall=onecall)
            result = fetch_weather(_CITY, _API_KEY)

        assert result["daily"][0]["pop"] == 0

    def test_missing_uvi_in_daily_defaults_to_zero(self):
        daily = [{k: v for k, v in day.items() if k != "uvi"} for day in _make_daily(8)]
        onecall = {**_ONECALL_RESPONSE, "daily": daily}

        with requests_mock_lib.Mocker() as m:
            _happy(m, onecall=onecall)
            result = fetch_weather(_CITY, _API_KEY)

        assert result["daily"][0]["uvi"] == 0

    def test_missing_pop_in_hourly_defaults_to_zero(self):
        hourly = [{k: v for k, v in h.items() if k != "pop"} for h in _make_hourly(48)]
        onecall = {**_ONECALL_RESPONSE, "hourly": hourly}

        with requests_mock_lib.Mocker() as m:
            _happy(m, onecall=onecall)
            result = fetch_weather(_CITY, _API_KEY)

        assert result["hourly"][0]["pop"] == 0

    def test_fewer_than_seven_daily_entries_returned_as_is(self):
        onecall = {**_ONECALL_RESPONSE, "daily": _make_daily(3)}

        with requests_mock_lib.Mocker() as m:
            _happy(m, onecall=onecall)
            result = fetch_weather(_CITY, _API_KEY)

        assert len(result["daily"]) == 3

    def test_missing_country_in_geo_defaults_to_empty_string(self):
        geo = [{"name": "London", "lat": 51.5074, "lon": -0.1278}]

        with requests_mock_lib.Mocker() as m:
            _happy(m, geo=geo)
            result = fetch_weather(_CITY, _API_KEY)

        assert result["current"]["sys"]["country"] == ""


class TestAirQualityIntegration:
    def test_epa_aqi_present_on_success(self):
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            result = fetch_weather(_CITY, _API_KEY)

        assert "epa_aqi" in result
        for field in ("aqi", "category", "dominant_pollutant", "dt"):
            assert field in result["epa_aqi"], f"epa_aqi missing field '{field}'"

    def test_epa_aqi_values_computed_correctly(self):
        """pm2_5=3.5 is the dominant pollutant in _AIR_POLLUTION_RESPONSE -
        cross-checked against backend/tests/test_aqi_calculator.py's own
        breakpoint tests rather than re-deriving the expected AQI by hand
        here."""
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            result = fetch_weather(_CITY, _API_KEY)

        from aqi_calculator import calculate_epa_aqi
        expected = calculate_epa_aqi(_AIR_POLLUTION_RESPONSE["list"][0]["components"])
        assert result["epa_aqi"]["aqi"] == expected["aqi"]
        assert result["epa_aqi"]["category"] == expected["category"]
        assert result["epa_aqi"]["dominant_pollutant"] == expected["dominant_pollutant"]

    def test_both_forecast_and_air_quality_endpoints_are_called(self):
        """Confirms the concurrent fetch actually issues both requests,
        not just one."""
        with requests_mock_lib.Mocker() as m:
            _happy(m)
            fetch_weather(_CITY, _API_KEY)

        urls_called = {r.url.split("?")[0] for r in m.request_history}
        assert _ONECALL_URL in urls_called
        assert _AIR_POLLUTION_URL in urls_called

    def test_air_quality_500_is_non_fatal(self):
        """The core guarantee: an air-quality failure must never affect
        the rest of the response."""
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=_GEO_RESPONSE)
            m.get(_ONECALL_URL, json=_ONECALL_RESPONSE)
            m.get(_AIR_POLLUTION_URL, status_code=500)
            result = fetch_weather(_CITY, _API_KEY)

        assert "epa_aqi" not in result
        assert "current" in result
        assert "daily" in result
        assert "hourly" in result
        assert result["current"]["main"]["temp"] == 15.3

    def test_air_quality_timeout_is_non_fatal(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=_GEO_RESPONSE)
            m.get(_ONECALL_URL, json=_ONECALL_RESPONSE)
            m.get(_AIR_POLLUTION_URL, exc=requests.exceptions.Timeout)
            result = fetch_weather(_CITY, _API_KEY)

        assert "epa_aqi" not in result
        assert "current" in result

    def test_air_quality_connection_error_is_non_fatal(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=_GEO_RESPONSE)
            m.get(_ONECALL_URL, json=_ONECALL_RESPONSE)
            m.get(_AIR_POLLUTION_URL, exc=requests.exceptions.ConnectionError)
            result = fetch_weather(_CITY, _API_KEY)

        assert "epa_aqi" not in result
        assert "current" in result

    def test_air_quality_empty_list_is_non_fatal(self):
        """Malformed/unexpected response shape (empty list) must be
        caught, not raised as an unhandled IndexError."""
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=_GEO_RESPONSE)
            m.get(_ONECALL_URL, json=_ONECALL_RESPONSE)
            m.get(_AIR_POLLUTION_URL, json={"list": []})
            result = fetch_weather(_CITY, _API_KEY)

        assert "epa_aqi" not in result
        assert "current" in result

    def test_air_quality_missing_components_is_non_fatal(self):
        """Malformed/unexpected response shape (no recognized pollutant
        keys) must be caught, not raised as an unhandled error."""
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=_GEO_RESPONSE)
            m.get(_ONECALL_URL, json=_ONECALL_RESPONSE)
            m.get(_AIR_POLLUTION_URL, json={"list": [{"dt": 123, "components": {}}]})
            result = fetch_weather(_CITY, _API_KEY)

        assert "epa_aqi" not in result
        assert "current" in result

    def test_air_quality_401_is_non_fatal(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=_GEO_RESPONSE)
            m.get(_ONECALL_URL, json=_ONECALL_RESPONSE)
            m.get(_AIR_POLLUTION_URL, status_code=401)
            result = fetch_weather(_CITY, _API_KEY)

        assert "epa_aqi" not in result
        assert "current" in result


class TestGeocodingErrors:
    def test_empty_geo_result_raises_city_not_found(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=[])
            with pytest.raises(CityNotFoundError):
                fetch_weather("unknowncityxyz", _API_KEY)

    def test_geo_404_raises_city_not_found(self):
        """Geo API returns 404 when the URL is malformed; we surface it as city-not-found."""
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, status_code=404)
            with pytest.raises(CityNotFoundError):
                fetch_weather("unknowncityxyz", _API_KEY)

    def test_geo_401_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, status_code=401)
            with pytest.raises(WeatherClientError, match="authentication"):
                fetch_weather(_CITY, "bad-key")

    def test_geo_429_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, status_code=429)
            with pytest.raises(WeatherClientError, match="rate limit"):
                fetch_weather(_CITY, _API_KEY)

    def test_geo_500_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, status_code=500)
            with pytest.raises(WeatherClientError, match="unexpected error"):
                fetch_weather(_CITY, _API_KEY)

    def test_geo_timeout_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, exc=requests.exceptions.Timeout)
            with pytest.raises(WeatherClientError, match="did not respond"):
                fetch_weather(_CITY, _API_KEY)

    def test_geo_connection_error_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, exc=requests.exceptions.ConnectionError)
            with pytest.raises(WeatherClientError, match="Unable to reach"):
                fetch_weather(_CITY, _API_KEY)


class TestOnecallErrors:
    def test_onecall_401_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=_GEO_RESPONSE)
            m.get(_ONECALL_URL, status_code=401)
            with pytest.raises(WeatherClientError, match="authentication"):
                fetch_weather(_CITY, _API_KEY)

    def test_onecall_429_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=_GEO_RESPONSE)
            m.get(_ONECALL_URL, status_code=429)
            with pytest.raises(WeatherClientError, match="rate limit"):
                fetch_weather(_CITY, _API_KEY)

    def test_onecall_500_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=_GEO_RESPONSE)
            m.get(_ONECALL_URL, status_code=500)
            with pytest.raises(WeatherClientError, match="unexpected error"):
                fetch_weather(_CITY, _API_KEY)

    def test_onecall_timeout_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=_GEO_RESPONSE)
            m.get(_ONECALL_URL, exc=requests.exceptions.Timeout)
            with pytest.raises(WeatherClientError, match="did not respond"):
                fetch_weather(_CITY, _API_KEY)

    def test_onecall_connection_error_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=_GEO_RESPONSE)
            m.get(_ONECALL_URL, exc=requests.exceptions.ConnectionError)
            with pytest.raises(WeatherClientError, match="Unable to reach"):
                fetch_weather(_CITY, _API_KEY)


class TestReverseGeocode:
    _LAT = 40.7128
    _LON = -74.0060

    def test_returns_city_and_country(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_REVERSE_URL, json=[{"name": "New York", "country": "US"}])
            result = reverse_geocode(self._LAT, self._LON, _API_KEY)
        assert result == "New York, US"

    def test_missing_country_returns_name_only(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_REVERSE_URL, json=[{"name": "Somewhere"}])
            result = reverse_geocode(self._LAT, self._LON, _API_KEY)
        assert result == "Somewhere"

    def test_empty_result_raises_city_not_found(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_REVERSE_URL, json=[])
            with pytest.raises(CityNotFoundError):
                reverse_geocode(self._LAT, self._LON, _API_KEY)

    def test_401_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_REVERSE_URL, status_code=401)
            with pytest.raises(WeatherClientError, match="authentication"):
                reverse_geocode(self._LAT, self._LON, _API_KEY)

    def test_429_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_REVERSE_URL, status_code=429)
            with pytest.raises(WeatherClientError, match="rate limit"):
                reverse_geocode(self._LAT, self._LON, _API_KEY)

    def test_500_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_REVERSE_URL, status_code=500)
            with pytest.raises(WeatherClientError, match="unexpected error"):
                reverse_geocode(self._LAT, self._LON, _API_KEY)

    def test_timeout_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_REVERSE_URL, exc=requests.exceptions.Timeout)
            with pytest.raises(WeatherClientError, match="did not respond"):
                reverse_geocode(self._LAT, self._LON, _API_KEY)

    def test_network_error_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_REVERSE_URL, exc=requests.exceptions.ConnectionError)
            with pytest.raises(WeatherClientError, match="Unable to reach"):
                reverse_geocode(self._LAT, self._LON, _API_KEY)
