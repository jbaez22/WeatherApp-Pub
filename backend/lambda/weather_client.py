"""
OpenWeatherMap API client for the Weather Dashboard Lambda — V2.

Two-step fetch per request (on cache miss):
  1. Geocoding API  (/geo/1.0/direct)   — resolve city name → lat/lon + display name
  2. One Call API 3.0 (/data/3.0/onecall) — current, 7-day daily, 48-hour hourly
     + the Air Pollution API (/data/2.5/air_pollution), run concurrently with
     step 2 once lat/lon is known — see docs/WeatherApp-AirQuality-Plan-V1.md #4.5.

Returns a single dict matching the V2 contract expected by frontend/js/app.js:
  { current: {...}, daily: [...7], hourly: [...48], epa_aqi: {...} | absent }

suggest_cities() is OWM's own geocoding read repurposed for the search-box
autocomplete dropdown (higher limit, no onecall/air-quality calls) - but OWM
only does whole-word name matching, not prefix search (confirmed live,
docs/WeatherApp-GeoNames-Suggest-Upgrade-Plan-V1.md #1), so
geonames_suggest_cities() is the one actually wired into GET /suggest - see
that plan doc for the full rationale. fetch_weather_by_coords() lets the
handler skip the forward-geocode step entirely once the user has picked an
exact suggestion, avoiding the risk of a second free-text geocode resolving
to a different same-named place than the one actually picked.

The API key / GeoNames username are injected as parameters — this module
never reads from Secrets Manager directly, keeping it independently testable.
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

# NOT https://api.geonames.org - that hostname is HTTP-only. GeoNames'
# free-tier TLS endpoint lives on a dedicated subdomain (confirmed live,
# valid Let's Encrypt cert for secure.geonames.org, 2026-10-13) - using
# api.geonames.org here would send the GeoNames username in plaintext.
_GEONAMES_URL = "https://secure.geonames.org/searchJSON"

# GeoNames signals almost all errors with HTTP 200 and an embedded
# {"status": {"value": <code>, "message": ...}} object rather than a real
# HTTP error status (confirmed against GeoNames' own exception-code docs,
# not assumed) - see _safe_get_geonames().
_GEONAMES_AUTH_ERROR_CODE = 10
_GEONAMES_NO_RESULT_CODE = 15
_GEONAMES_RATE_LIMIT_CODES = {18, 19, 20}  # daily / hourly / weekly credits


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


def _safe_get_geonames(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """
    Execute a GET request against the GeoNames API and return the parsed
    JSON body.

    Unlike _safe_get() (OWM), GeoNames reports almost all failures as
    HTTP 200 with an embedded {"status": {"value": <code>, "message": ...}}
    object instead of a real HTTP error status - so a 2xx response alone
    doesn't mean success here. Raises WeatherClientError on network
    failures, a genuine non-2xx HTTP response, or a GeoNames status code
    that represents a real failure (auth, rate limit, etc). The
    "no result found" status code is treated as a normal empty match, not
    a failure - returns {} so the caller's `.get("geonames", [])` yields []
    the same way a real empty `geonames` list would.
    """
    try:
        response = requests.get(url, params=params, timeout=_TIMEOUT_SECONDS)
    except Timeout as exc:
        logger.error("Timeout calling GeoNames: %s", url)
        raise WeatherClientError(
            "The location service did not respond in time. Please try again."
        ) from exc
    except RequestException as exc:
        logger.error("Network error calling GeoNames: %s", exc)
        raise WeatherClientError(
            "Unable to reach the location service. Please try again."
        ) from exc

    if not response.ok:
        logger.error("GeoNames returned unexpected HTTP status %d", response.status_code)
        raise WeatherClientError(
            f"Location service returned an unexpected error (HTTP {response.status_code})."
        )

    body = response.json()
    status = body.get("status")
    if status is None:
        return body

    code = status.get("value")
    message = status.get("message", "")

    if code == _GEONAMES_NO_RESULT_CODE:
        return {}

    if code == _GEONAMES_AUTH_ERROR_CODE:
        logger.error("GeoNames authorization error: %s", message)
        raise WeatherClientError("Location service authentication failed.")

    if code in _GEONAMES_RATE_LIMIT_CODES:
        logger.warning("GeoNames rate limit hit: %s", message)
        raise WeatherClientError(
            "Location service rate limit reached. Please try again shortly."
        )

    logger.error("GeoNames returned status code %s: %s", code, message)
    raise WeatherClientError(f"Location service returned an unexpected error: {message}")


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


def suggest_cities(query: str, api_key: str, limit: int = 5) -> list[dict[str, Any]]:
    """
    Return up to `limit` geocoding matches for a partial/ambiguous city
    query, for the search-box autocomplete dropdown.

    NOT currently wired into GET /suggest - OWM's geocoding only does
    whole-word name matching, not prefix search, so it can't actually power
    an autocomplete-as-you-type dropdown (confirmed live,
    docs/WeatherApp-GeoNames-Suggest-Upgrade-Plan-V1.md #1 - e.g.
    "Woodbourne" matches but "Woodb" does not). geonames_suggest_cities()
    below is what GET /suggest actually calls. Kept here, unused, only in
    case a future fallback-on-GeoNames-failure decision wants it (that plan
    doc's SEC.2 decided against this for now).

    Each result: {"name": str, "state": str, "country": str, "lat": float, "lon": float}
    `state` is "" when OWM doesn't report one (most non-US locations).

    Unlike _geocode(), an empty result list is a normal "no matches yet"
    outcome while the user is still typing, not an error - returns []
    rather than raising CityNotFoundError.
    """
    logger.info("Suggesting cities for query: %s", query)
    results = _safe_get(_GEO_URL, {"q": query, "limit": limit, "appid": api_key})

    return [
        {
            "name":    geo["name"],
            "state":   geo.get("state", ""),
            "country": geo.get("country", ""),
            "lat":     geo["lat"],
            "lon":     geo["lon"],
        }
        for geo in results
    ]


def geonames_suggest_cities(query: str, username: str, limit: int = 5) -> list[dict[str, Any]]:
    """
    Return up to `limit` GeoNames prefix-search matches for a partial city
    query - the function actually wired into GET /suggest.

    Uses GeoNames' name_startsWith parameter, which does true prefix
    matching (unlike OWM's suggest_cities() above) - this is the whole
    reason this function exists, see
    docs/WeatherApp-GeoNames-Suggest-Upgrade-Plan-V1.md.

    Each result: {"name": str, "state": str, "country": str, "lat": float, "lon": float}
    `state` is "" when GeoNames doesn't report one (most non-US locations).
    GeoNames returns lat/lng as strings under a differently-named "lng" key
    (not "lon") - both are normalised here to match the contract shared
    with suggest_cities() and the frontend.

    An empty/no-match result is a normal "no matches yet" outcome while the
    user is still typing, not an error - returns [] rather than raising.
    """
    logger.info("Suggesting cities (GeoNames) for query: %s", query)
    body = _safe_get_geonames(_GEONAMES_URL, {
        "name_startsWith": query,
        "maxRows":         limit,
        "featureClass":    "P",
        "style":           "FULL",
        "username":        username,
    })

    results = body.get("geonames", [])

    return [
        {
            "name":    geo["name"],
            "state":   geo.get("adminName1", ""),
            "country": geo.get("countryCode", ""),
            "lat":     float(geo["lat"]),
            "lon":     float(geo["lng"]),
        }
        for geo in results
    ]


def _fetch_and_shape(
    lat: float, lon: float, geo_name: str, geo_country: str, api_key: str, log_label: str
) -> dict[str, Any]:
    """
    Shared second half of the fetch: One Call API 3.0 (current + daily +
    hourly) and the Air Pollution API run concurrently against an
    already-resolved lat/lon, since both only depend on that - see
    docs/WeatherApp-AirQuality-Plan-V1.md #4.5 for why this must not be
    sequential. Used by both fetch_weather() (geocodes first) and
    fetch_weather_by_coords() (lat/lon already known from a /suggest pick).

    Returns the V2 response contract: { current, daily, hourly, epa_aqi }
    - epa_aqi is omitted entirely if the air-quality fetch/calc failed.
    """
    # NOTE: pipeline/scripts/validate-live-deployment.sh's live-deployment
    # smoke test does an exact CloudWatch Logs filter-pattern substring match
    # on "Fetching One Call 3.0 data for {city}" to prove a real upstream
    # OpenWeatherMap call happened (not a cache hit) - keep that exact phrase
    # intact if this message is ever reworded again.
    logger.info("Fetching One Call 3.0 data for %s (%.4f, %.4f) + air quality", log_label, lat, lon)
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


def fetch_weather(city: str, api_key: str) -> dict[str, Any]:
    """
    Fetch current weather, 7-day daily forecast, 48-hour hourly data, and
    the EPA Air Quality Index for `city`.

    Step 1: Geocode city name → lat/lon + display name  (/geo/1.0/direct)
    Step 2: see _fetch_and_shape().

    Raises CityNotFoundError, WeatherClientError (from the forecast call
    only - the air-quality call never raises, see _fetch_air_quality).
    """
    lat, lon, geo_name, geo_country = _geocode(city, api_key)
    return _fetch_and_shape(lat, lon, geo_name, geo_country, api_key, log_label=city)


def fetch_weather_by_coords(
    lat: float, lon: float, geo_name: str, geo_country: str, api_key: str
) -> dict[str, Any]:
    """
    Fetch weather for an exact (lat, lon) the caller already resolved -
    e.g. the user picked a specific result from /suggest. Skips the
    forward-geocode step entirely, which also skips its disambiguation
    risk (re-geocoding a free-text name could resolve to a different
    same-named place than the one the user actually picked).

    `geo_name`/`geo_country` are used only for display in the response -
    the weather itself is keyed purely by lat/lon.
    """
    return _fetch_and_shape(
        lat, lon, geo_name, geo_country, api_key, log_label=f"{geo_name}, {geo_country}"
    )
