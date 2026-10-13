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
    fetch_weather_by_coords,
    geonames_suggest_cities,
    reverse_geocode,
    suggest_cities,
)

_GEO_REVERSE_URL = "https://api.openweathermap.org/geo/1.0/reverse"

_API_KEY = "test-key-abc"
_CITY = "london"

_GEO_URL = "https://api.openweathermap.org/geo/1.0/direct"
_ONECALL_URL = "https://api.openweathermap.org/data/3.0/onecall"
_AIR_POLLUTION_URL = "https://api.openweathermap.org/data/2.5/air_pollution"
_GEONAMES_URL = "https://secure.geonames.org/searchJSON"
_GEONAMES_USER = "test-geonames-user"

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


_SUGGEST_RESPONSE = [
    {"name": "Woodbourne", "lat": 41.7343, "lon": -74.6104, "country": "US", "state": "New York"},
    {"name": "Woodbourne", "lat": 41.1998, "lon": -74.6821, "country": "US", "state": "New Jersey"},
]


class TestSuggestCities:
    def test_returns_mapped_fields(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=_SUGGEST_RESPONSE)
            result = suggest_cities("woodbourne", _API_KEY)

        assert len(result) == 2
        first = result[0]
        for field in ("name", "state", "country", "lat", "lon"):
            assert field in first

    def test_name_state_country_values_preserved(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=_SUGGEST_RESPONSE)
            result = suggest_cities("woodbourne", _API_KEY)

        assert result[0]["name"] == "Woodbourne"
        assert result[0]["state"] == "New York"
        assert result[1]["state"] == "New Jersey"

    def test_missing_state_defaults_to_empty_string(self):
        geo = [{"name": "Blenheim", "lat": -41.5, "lon": 173.9, "country": "NZ"}]
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=geo)
            result = suggest_cities("blenheim", _API_KEY)

        assert result[0]["state"] == ""

    def test_empty_result_returns_empty_list_not_error(self):
        """Unlike _geocode()/fetch_weather(), an empty match list while the
        user is still typing is normal, not CityNotFoundError."""
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=[])
            result = suggest_cities("unknowncityxyz", _API_KEY)

        assert result == []

    def test_limit_param_sent_to_geocode(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=_SUGGEST_RESPONSE)
            suggest_cities("woodbourne", _API_KEY, limit=3)

        req = m.request_history[0]
        assert req.qs.get("limit") == ["3"]

    def test_query_sent_as_q_param(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, json=_SUGGEST_RESPONSE)
            suggest_cities("woodbourne", _API_KEY)

        req = m.request_history[0]
        assert req.qs.get("q") == ["woodbourne"]

    def test_401_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, status_code=401)
            with pytest.raises(WeatherClientError, match="authentication"):
                suggest_cities("woodbourne", _API_KEY)

    def test_timeout_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEO_URL, exc=requests.exceptions.Timeout)
            with pytest.raises(WeatherClientError, match="did not respond"):
                suggest_cities("woodbourne", _API_KEY)


class TestFetchWeatherByCoords:
    _LAT = 41.7343
    _LON = -74.6104

    def test_skips_geocode_call_entirely(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_ONECALL_URL, json=_ONECALL_RESPONSE)
            m.get(_AIR_POLLUTION_URL, json=_AIR_POLLUTION_RESPONSE)
            fetch_weather_by_coords(self._LAT, self._LON, "Woodbourne", "US", _API_KEY)

        urls_called = {r.url.split("?")[0] for r in m.request_history}
        assert _GEO_URL not in urls_called

    def test_display_name_and_country_pass_through(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_ONECALL_URL, json=_ONECALL_RESPONSE)
            m.get(_AIR_POLLUTION_URL, json=_AIR_POLLUTION_RESPONSE)
            result = fetch_weather_by_coords(self._LAT, self._LON, "Woodbourne", "US", _API_KEY)

        assert result["current"]["name"] == "Woodbourne"
        assert result["current"]["sys"]["country"] == "US"

    def test_lat_lon_sent_to_onecall(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_ONECALL_URL, json=_ONECALL_RESPONSE)
            m.get(_AIR_POLLUTION_URL, json=_AIR_POLLUTION_RESPONSE)
            fetch_weather_by_coords(self._LAT, self._LON, "Woodbourne", "US", _API_KEY)

        req = next(r for r in m.request_history if _ONECALL_URL in r.url)
        assert req.qs.get("lat") == [str(self._LAT)]
        assert req.qs.get("lon") == [str(self._LON)]

    def test_onecall_error_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_ONECALL_URL, status_code=500)
            m.get(_AIR_POLLUTION_URL, json=_AIR_POLLUTION_RESPONSE)
            with pytest.raises(WeatherClientError, match="unexpected error"):
                fetch_weather_by_coords(self._LAT, self._LON, "Woodbourne", "US", _API_KEY)

    def test_air_quality_failure_is_non_fatal(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_ONECALL_URL, json=_ONECALL_RESPONSE)
            m.get(_AIR_POLLUTION_URL, status_code=500)
            result = fetch_weather_by_coords(self._LAT, self._LON, "Woodbourne", "US", _API_KEY)

        assert "epa_aqi" not in result
        assert "current" in result


_GEONAMES_RESPONSE = {
    "geonames": [
        {
            "name": "Woodbourne", "adminName1": "New York", "countryCode": "US",
            "lat": "41.761057", "lng": "-74.597721",
        },
        {
            "name": "Woodbourne", "adminName1": "New Jersey", "countryCode": "US",
            "lat": "41.20454", "lng": "-74.6429401",
        },
    ],
    "totalResultsCount": 2,
}


class TestGeonamesSuggestCities:
    """
    GeoNames' prefix search is the whole point of this upgrade (confirmed
    live: OWM's suggest_cities() can only match whole words, not partial
    prefixes like "woodb" - see
    docs/WeatherApp-GeoNames-Suggest-Upgrade-Plan-V1.md #1). These tests
    cover GeoNames' different error-signaling shape (HTTP 200 + an embedded
    status object, not real 401/429) and its different field names
    (lng/str instead of lon/float).
    """

    def test_returns_mapped_fields(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json=_GEONAMES_RESPONSE)
            result = geonames_suggest_cities("woodb", _GEONAMES_USER)

        assert len(result) == 2
        for field in ("name", "state", "country", "lat", "lon"):
            assert field in result[0]

    def test_name_state_country_values_preserved(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json=_GEONAMES_RESPONSE)
            result = geonames_suggest_cities("woodb", _GEONAMES_USER)

        assert result[0]["name"] == "Woodbourne"
        assert result[0]["state"] == "New York"
        assert result[1]["state"] == "New Jersey"

    def test_lat_lng_normalised_to_lat_lon_floats(self):
        """GeoNames returns lat/lng as strings under a differently-named
        'lng' key - both must be normalised to match suggest_cities()'s
        (OWM) float lat/lon contract that the frontend expects."""
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json=_GEONAMES_RESPONSE)
            result = geonames_suggest_cities("woodb", _GEONAMES_USER)

        assert result[0]["lat"] == 41.761057
        assert isinstance(result[0]["lat"], float)
        assert result[0]["lon"] == -74.597721
        assert isinstance(result[0]["lon"], float)

    def test_missing_state_defaults_to_empty_string(self):
        geo = {"geonames": [{"name": "Blenheim", "countryCode": "NZ", "lat": "-41.5", "lng": "173.9"}]}
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json=geo)
            result = geonames_suggest_cities("blenheim", _GEONAMES_USER)

        assert result[0]["state"] == ""

    def test_query_sent_as_name_starts_with_param(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json=_GEONAMES_RESPONSE)
            geonames_suggest_cities("woodb", _GEONAMES_USER)

        req = m.request_history[0]
        assert req.qs.get("name_startswith") == ["woodb"]

    def test_username_sent_as_username_param(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json=_GEONAMES_RESPONSE)
            geonames_suggest_cities("woodb", _GEONAMES_USER)

        req = m.request_history[0]
        assert req.qs.get("username") == [_GEONAMES_USER]

    def test_feature_class_restricted_to_populated_places(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json=_GEONAMES_RESPONSE)
            geonames_suggest_cities("woodb", _GEONAMES_USER)

        req = m.request_history[0]
        # requests_mock lowercases both query keys and values in .qs.
        assert req.qs.get("featureclass") == ["p"]

    def test_style_full_requested_for_admin_name(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json=_GEONAMES_RESPONSE)
            geonames_suggest_cities("woodb", _GEONAMES_USER)

        req = m.request_history[0]
        # requests_mock lowercases both query keys and values in .qs.
        assert req.qs.get("style") == ["full"]

    def test_limit_sent_as_max_rows_param(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json=_GEONAMES_RESPONSE)
            geonames_suggest_cities("woodb", _GEONAMES_USER, limit=3)

        req = m.request_history[0]
        assert req.qs.get("maxrows") == ["3"]

    def test_no_result_status_code_returns_empty_list_not_error(self):
        """Status code 15 ('no result found') is GeoNames' normal 'nothing
        matched yet' response while the user is still typing - not an
        error, same semantics as suggest_cities() (OWM)."""
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json={"status": {"message": "no result found", "value": 15}})
            result = geonames_suggest_cities("zzzznotreal", _GEONAMES_USER)

        assert result == []

    def test_empty_geonames_list_returns_empty_list(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json={"geonames": [], "totalResultsCount": 0})
            result = geonames_suggest_cities("zzzznotreal", _GEONAMES_USER)

        assert result == []

    def test_auth_error_status_code_raises_weather_client_error(self):
        """Status code 10 ('authorization exception') is what GeoNames
        returns - still HTTP 200 - if 'Free Web Services' was never
        enabled on the account (see plan doc #3's account gotcha)."""
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json={"status": {"message": "user account not enabled", "value": 10}})
            with pytest.raises(WeatherClientError, match="authentication"):
                geonames_suggest_cities("woodb", _GEONAMES_USER)

    def test_daily_limit_status_code_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json={"status": {"message": "daily limit exceeded", "value": 18}})
            with pytest.raises(WeatherClientError, match="rate limit"):
                geonames_suggest_cities("woodb", _GEONAMES_USER)

    def test_hourly_limit_status_code_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json={"status": {"message": "hourly limit exceeded", "value": 19}})
            with pytest.raises(WeatherClientError, match="rate limit"):
                geonames_suggest_cities("woodb", _GEONAMES_USER)

    def test_weekly_limit_status_code_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json={"status": {"message": "weekly limit exceeded", "value": 20}})
            with pytest.raises(WeatherClientError, match="rate limit"):
                geonames_suggest_cities("woodb", _GEONAMES_USER)

    def test_unrecognized_status_code_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, json={"status": {"message": "invalid parameter", "value": 14}})
            with pytest.raises(WeatherClientError, match="unexpected error"):
                geonames_suggest_cities("woodb", _GEONAMES_USER)

    def test_http_500_raises_weather_client_error(self):
        """A genuine non-2xx HTTP response (vs. GeoNames' usual HTTP-200
        status-object errors) must still be caught."""
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, status_code=500)
            with pytest.raises(WeatherClientError, match="unexpected error"):
                geonames_suggest_cities("woodb", _GEONAMES_USER)

    def test_timeout_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, exc=requests.exceptions.Timeout)
            with pytest.raises(WeatherClientError, match="did not respond"):
                geonames_suggest_cities("woodb", _GEONAMES_USER)

    def test_connection_error_raises_weather_client_error(self):
        with requests_mock_lib.Mocker() as m:
            m.get(_GEONAMES_URL, exc=requests.exceptions.ConnectionError)
            with pytest.raises(WeatherClientError, match="Unable to reach"):
                geonames_suggest_cities("woodb", _GEONAMES_USER)
