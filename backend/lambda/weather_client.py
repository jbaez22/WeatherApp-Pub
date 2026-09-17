"""
OpenWeatherMap API client for the Weather Dashboard Lambda — V2.

Two-step fetch per request (on cache miss):
  1. Geocoding API  (/geo/1.0/direct)   — resolve city name → lat/lon + display name
  2. One Call API 3.0 (/data/3.0/onecall) — current, 7-day daily, 48-hour hourly
     + the Air Pollution API (/data/2.5/air_pollution), run concurrently with
     step 2 once lat/lon is known — see docs/WeatherApp-AirQuality-Plan-V1.md #4.5.

Returns a single dict matching the V2 contract expected by frontend/js/app.js:
  { current: {...}, daily: [...7], hourly: [...48], epa_aqi: {...} | absent }

The API key is injected as a parameter — this module never reads from SSM directly,
keeping it independently testable.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests
from requests.exceptions import RequestException, Timeout

from aqi_calculator import AqiCalculationError, calculate_epa_aqi

logger = logging.getLogger(__name__)

_GEO_URL = "https://api.openweathermap.org/geo/1.0/direct"
_GEO_REVERSE_URL = "https://api.openweathermap.org/geo/1.0/reverse"
_ONECALL_URL = "https://api.openweathermap.org/data/3.0/onecall"
_AIR_POLLUTION_URL = "https://api.openweathermap.org/data/2.5/air_pollution"
_TIMEOUT_SECONDS = 10
_UNITS = "metric"  # Always fetch Celsius; C→F conversion happens client-side.


class WeatherClientError(RuntimeError):
    """Raised when the OpenWeatherMap API returns an error or is unreachable."""


class CityNotFoundError(WeatherClientError):
    """Raised when geocoding returns no results for the given city name."""


def _safe_get(url: str, params: dict[str, Any]) -> Any:
    """
    Execute a GET request and return the parsed JSON body.

    Raises WeatherClientError on network failures or non-2xx responses.
    Raises CityNotFoundError specifically for HTTP 404.
    The `appid` param is intentionally excluded from all log messages.
    """
    try:
        response = requests.get(url, params=params, timeout=_TIMEOUT_SECONDS)
    except Timeout as exc:
        logger.error("Timeout calling OpenWeatherMap: %s", url)
        raise WeatherClientError(
            "The weather service did not respond in time. Please try again."
        ) from exc
    except RequestException as exc:
        logger.error("Network error calling OpenWeatherMap: %s", exc)
        raise WeatherClientError(
            "Unable to reach the weather service. Please try again."
        ) from exc

    if response.status_code == 404:
        raise CityNotFoundError("City not found.")

    if response.status_code == 401:
        logger.error("OpenWeatherMap returned 401 — check the API key in SSM.")
        raise WeatherClientError("Weather service authentication failed.")

    if response.status_code == 429:
        logger.warning("OpenWeatherMap rate limit hit.")
        raise WeatherClientError(
            "Weather service rate limit reached. Please try again shortly."
        )

    if not response.ok:
        logger.error(
            "OpenWeatherMap returned unexpected status %d", response.status_code
        )
        raise WeatherClientError(
            f"Weather service returned an unexpected error (HTTP {response.status_code})."
        )

    return response.json()


def _geocode(city: str, api_key: str) -> tuple[float, float, str, str]:
    """
    Resolve a city name to coordinates and display metadata.

    Returns (lat, lon, display_name, country).
    Raises CityNotFoundError if the geocoding API returns an empty result list.
    """
    params = {"q": city, "limit": 1, "appid": api_key}
    logger.info("Geocoding city: %s", city)
    results = _safe_get(_GEO_URL, params)

    if not results:
        raise CityNotFoundError(
            f"City '{city}' was not found. Please check the spelling."
        )

    geo = results[0]
    return geo["lat"], geo["lon"], geo["name"], geo.get("country", "")


def _shape_response(
    geo_name: str, geo_country: str, onecall: dict[str, Any]
) -> dict[str, Any]:
    """
    Transform the raw One Call API 3.0 response into the V2 frontend contract.

    current  — same field names as V1 so renderCurrent() needs no changes
    daily    — 7 entries with native min/max, pop, uvi, sunrise, sunset
    hourly   — 48 entries; frontend filters by day to build the hourly strip
    """
    current_raw = onecall["current"]

    current = {
        "name": geo_name,
        "sys": {"country": geo_country},
        "dt": current_raw["dt"],
        "main": {
            "temp":       current_raw["temp"],
            "feels_like": current_raw["feels_like"],
            "humidity":   current_raw["humidity"],
            "pressure":   current_raw["pressure"],
        },
        "wind":       {"speed": current_raw["wind_speed"]},
        "visibility": current_raw.get("visibility", 10000),
        "weather":    current_raw["weather"],
    }

    daily = [
        {
            "dt":         day["dt"],
            "high":       day["temp"]["max"],
            "low":        day["temp"]["min"],
            "icon":       day["weather"][0]["icon"],
            "desc":       day["weather"][0]["description"],
            "pop":        day.get("pop", 0),
            "uvi":        day.get("uvi", 0),
            "sunrise":    day["sunrise"],
            "sunset":     day["sunset"],
            "humidity":   day["humidity"],
            "wind_speed": day["wind_speed"],
        }
        for day in onecall.get("daily", [])[:7]
    ]

    hourly = [
        {
            "dt":         hour["dt"],
            "temp":       hour["temp"],
            "feels_like": hour["feels_like"],
            "icon":       hour["weather"][0]["icon"],
            "desc":       hour["weather"][0]["description"],
            "pop":        hour.get("pop", 0),
            "humidity":   hour.get("humidity", 0),
            "wind_speed": hour.get("wind_speed", 0.0),
            "wind_deg":   hour.get("wind_deg", 0),
            "pressure":   hour.get("pressure", 0),
            "precip_mm":  round(
                hour.get("rain", {}).get("1h", 0.0)
                + hour.get("snow", {}).get("1h", 0.0),
                2,
            ),
        }
        for hour in onecall.get("hourly", [])[:48]
    ]

    return {"current": current, "daily": daily, "hourly": hourly}


def _fetch_air_quality(lat: float, lon: float, api_key: str) -> dict[str, Any] | None:
    """
    Fetch pollutant concentrations for (lat, lon) and compute the EPA AQI.

    Non-fatal by design: returns None on any failure (network error, or a
    response missing the expected shape) instead of raising, so a problem
    here never affects the rest of fetch_weather()'s response - the Air
    Quality widget is simply omitted client-side, the same resilience
    pattern as the OWM-down stale-cache fallback in weather_handler.py.
    """
    try:
        response = _safe_get(_AIR_POLLUTION_URL, {
            "lat": lat,
            "lon": lon,
            "appid": api_key,
        })
        entry = response["list"][0]
        result = calculate_epa_aqi(entry["components"])
        result["dt"] = entry["dt"]
        logger.info(
            "EPA AQI computed: %d (%s, dominant=%s)",
            result["aqi"], result["category"], result["dominant_pollutant"],
        )
        return result
    except (WeatherClientError, AqiCalculationError, KeyError, IndexError) as exc:
        logger.warning("Air-quality fetch/calc failed (non-fatal): %s", exc)
        return None


def reverse_geocode(lat: float, lon: float, api_key: str) -> str:
    """
    Resolve coordinates to a city name using the OWM reverse geocoding API.

    Returns a display string e.g. "New York, US".
    Raises CityNotFoundError if no location matches.
    Raises WeatherClientError on network / API errors.
    """
    logger.info("Reverse geocoding (%.4f, %.4f)", lat, lon)
    results = _safe_get(_GEO_REVERSE_URL, {"lat": lat, "lon": lon, "limit": 1, "appid": api_key})

    if not results:
        raise CityNotFoundError("No city found at the given coordinates.")

    geo = results[0]
    name = geo.get("name", "")
    country = geo.get("country", "")
    return f"{name}, {country}" if country else name


def fetch_weather(city: str, api_key: str) -> dict[str, Any]:
    """
    Fetch current weather, 7-day daily forecast, 48-hour hourly data, and
    the EPA Air Quality Index for `city`.

    Step 1: Geocode city name → lat/lon + display name  (/geo/1.0/direct)
    Step 2: One Call API 3.0 (current + daily + hourly) and the Air
            Pollution API (EPA AQI) run concurrently, since both only
            depend on the lat/lon resolved in Step 1 - see
            docs/WeatherApp-AirQuality-Plan-V1.md #4.5 for why this must
            not be sequential.

    Returns the V2 response contract: { current, daily, hourly, epa_aqi }
    - epa_aqi is omitted entirely if the air-quality fetch/calc failed.
    Raises CityNotFoundError, WeatherClientError (from the forecast call
    only - the air-quality call never raises, see _fetch_air_quality).
    """
    lat, lon, geo_name, geo_country = _geocode(city, api_key)

    # NOTE: pipeline/scripts/validate-live-deployment.sh's live-deployment
    # smoke test does an exact CloudWatch Logs filter-pattern substring match
    # on "Fetching One Call 3.0 data for {city}" to prove a real upstream
    # OpenWeatherMap call happened (not a cache hit) - keep that exact phrase
    # intact if this message is ever reworded again.
    logger.info("Fetching One Call 3.0 data for %s (%.4f, %.4f) + air quality", city, lat, lon)
    with ThreadPoolExecutor(max_workers=2) as executor:
        onecall_future = executor.submit(_safe_get, _ONECALL_URL, {
            "lat":     lat,
            "lon":     lon,
            "appid":   api_key,
            "units":   _UNITS,
            "exclude": "minutely,alerts",
        })
        air_quality_future = executor.submit(_fetch_air_quality, lat, lon, api_key)

        onecall = onecall_future.result()
        epa_aqi = air_quality_future.result()

    response = _shape_response(geo_name, geo_country, onecall)
    if epa_aqi is not None:
        response["epa_aqi"] = epa_aqi
    return response
