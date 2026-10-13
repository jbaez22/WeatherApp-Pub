"""
AWS Lambda handler — Weather Dashboard entry point.

Orchestrates the request lifecycle:
  1. Validate city input
  2. Check DynamoDB cache
  3. On cache miss: fetch from OpenWeatherMap, write to cache
  4. Return structured JSON response with CORS headers

This module's only responsibility is orchestration. All business logic
lives in the imported modules so each can be tested in isolation.
"""

import json
import logging
import os
from typing import Any

# Phase 3: must run before any other import that uses boto3 or requests —
# patch_all() instruments those libraries' HTTP/client calls so DynamoDB
# and OWM calls show up as X-Ray segments. cache.py, secrets_manager.py, and
# weather_client.py are all imported below this line for exactly that
# reason; do not move this import block down.
from aws_xray_sdk.core import patch_all

patch_all()

from cache import CacheError, get_cached_weather, get_stale_cached_weather, set_cached_weather
from secrets_manager import SecretsError, get_api_key, get_geonames_username
from validators import (
    ValidationError,
    validate_city,
    validate_coordinates,
    validate_country_code,
    validate_location_name,
)
from weather_client import (
    CityNotFoundError,
    WeatherClientError,
    fetch_weather,
    fetch_weather_by_coords,
    geonames_suggest_cities,
    reverse_geocode,
)

# /suggest fires on every debounced keystroke - don't call OWM for a query
# this short, it can't narrow anything down yet. Mirrors the frontend's own
# minimum-length gate so the behavior is consistent even if that JS check
# is ever bypassed (devtools, a different client).
_SUGGEST_MIN_LENGTH = 2
_SUGGEST_LIMIT = 5

# Log level is configurable via Lambda environment variable.
# logging.basicConfig() is a no-op here: the Lambda Python runtime already
# attaches its own handler to the root logger before this module ever
# runs, and basicConfig() only takes effect when the root logger has no
# existing handlers. Setting the level directly on the root logger is what
# actually controls what gets emitted — every child logger in this project
# (cache.py, secrets_manager.py, weather_client.py) uses logging.getLogger(__name__)
# with no level of its own, so it inherits this root level via propagation.
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.getLogger().setLevel(getattr(logging, log_level, logging.INFO))
logger = logging.getLogger(__name__)

# CORS origin must match the deployed CloudFront domain.
_ALLOWED_ORIGIN = os.environ.get(
    "ALLOWED_ORIGIN", "https://weather.craftingnewtech.com"
)

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": _ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "GET,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Build a Lambda proxy response dict with CORS headers."""
    return {
        "statusCode": status_code,
        "headers": {**_CORS_HEADERS, "Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _error(status_code: int, message: str) -> dict[str, Any]:
    return _response(status_code, {"error": True, "message": message})


def _handle_weather(query_params: dict[str, str]) -> dict[str, Any]:
    """
    Handle GET /weather?city=<name>
        or  GET /weather?lat=<f>&lon=<f>&name=<name>&country=<code>

    The lat/lon form is used when the user picked an exact result from
    GET /suggest - it skips the forward-geocode step entirely (see
    weather_client.fetch_weather_by_coords), avoiding the disambiguation
    risk of a second free-text geocode on `name` landing on a different
    same-named place than the one actually picked.
    """
    if query_params.get("lat") or query_params.get("lon"):
        return _handle_weather_by_coords(query_params)

    raw_city = query_params.get("city", "")

    try:
        city = validate_city(raw_city)
    except ValidationError as exc:
        logger.warning("Validation failed: %s", exc)
        return _error(400, str(exc))

    try:
        cached = get_cached_weather(city)
    except CacheError:
        cached = None

    if cached is not None:
        return _response(200, cached)

    try:
        api_key = get_api_key()
    except SecretsError as exc:
        logger.error("Weather credential retrieval failed: %s", exc)
        return _error(503, "Weather service is temporarily unavailable.")

    try:
        weather_data = fetch_weather(city, api_key)
    except CityNotFoundError:
        return _error(404, f"City '{city}' was not found. Please check the spelling.")
    except WeatherClientError as exc:
        logger.error("Weather client error: %s — attempting stale cache fallback", exc)
        stale = get_stale_cached_weather(city)
        if stale is not None:
            stale["stale"] = True
            stale["stale_reason"] = "Live weather data is temporarily unavailable; showing last known values."
            return _response(200, stale)
        return _error(502, str(exc))

    try:
        set_cached_weather(city, weather_data)
    except CacheError:
        pass

    return _response(200, weather_data)


def _handle_weather_by_coords(query_params: dict[str, str]) -> dict[str, Any]:
    """Handle the exact-match GET /weather?lat=&lon=&name=&country= form."""
    try:
        lat, lon = validate_coordinates(
            query_params.get("lat", ""), query_params.get("lon", "")
        )
        name = validate_location_name(query_params.get("name", ""), "name")
        country = validate_country_code(query_params.get("country", ""))
    except ValidationError as exc:
        logger.warning("Validation failed: %s", exc)
        return _error(400, str(exc))

    # Fixed-precision cache key keeps near-identical coordinates (e.g. two
    # clicks on the same suggestion) hitting the same cache entry, the same
    # way the city-name cache key normalises case.
    cache_key = f"{lat:.4f},{lon:.4f}"

    try:
        cached = get_cached_weather(cache_key)
    except CacheError:
        cached = None

    if cached is not None:
        return _response(200, cached)

    try:
        api_key = get_api_key()
    except SecretsError as exc:
        logger.error("Weather credential retrieval failed: %s", exc)
        return _error(503, "Weather service is temporarily unavailable.")

    try:
        weather_data = fetch_weather_by_coords(lat, lon, name, country, api_key)
    except CityNotFoundError:
        return _error(404, "Weather data was not found for that location.")
    except WeatherClientError as exc:
        logger.error("Weather client error: %s — attempting stale cache fallback", exc)
        stale = get_stale_cached_weather(cache_key)
        if stale is not None:
            stale["stale"] = True
            stale["stale_reason"] = "Live weather data is temporarily unavailable; showing last known values."
            return _response(200, stale)
        return _error(502, str(exc))

    try:
        set_cached_weather(cache_key, weather_data)
    except CacheError:
        pass

    return _response(200, weather_data)


def _handle_suggest(query_params: dict[str, str]) -> dict[str, Any]:
    """
    Handle GET /suggest?q=<partial city name> - powers the search-box
    autocomplete dropdown.

    Backed by GeoNames (geonames_suggest_cities), not OpenWeatherMap -
    OWM's geocoding only does whole-word name matching, not prefix search,
    so it can't power an autocomplete-as-you-type dropdown (confirmed live,
    docs/WeatherApp-GeoNames-Suggest-Upgrade-Plan-V1.md #1). No fallback to
    OWM's suggest_cities() on a GeoNames failure - that plan doc's SEC.2
    deliberately chose the same simple non-fatal degradation already used
    everywhere else in this app over a second code path to maintain.

    Degrades gracefully rather than erroring: a query shorter than
    _SUGGEST_MIN_LENGTH or an upstream failure both return an empty
    suggestions list (200) instead of a 4xx/5xx, since the dropdown is a
    non-essential assist - the user can still submit the search form
    directly either way.
    """
    raw_q = query_params.get("q", "")
    trimmed = raw_q.strip() if isinstance(raw_q, str) else ""

    if len(trimmed) < _SUGGEST_MIN_LENGTH:
        return _response(200, {"suggestions": []})

    try:
        query = validate_city(trimmed)
    except ValidationError as exc:
        logger.warning("Suggest validation failed: %s", exc)
        return _error(400, str(exc))

    try:
        geonames_username = get_geonames_username()
    except SecretsError as exc:
        logger.error("GeoNames credential retrieval failed: %s", exc)
        return _response(200, {"suggestions": []})

    try:
        suggestions = geonames_suggest_cities(query, geonames_username, limit=_SUGGEST_LIMIT)
    except WeatherClientError as exc:
        logger.warning("Suggest fetch failed (non-fatal): %s", exc)
        return _response(200, {"suggestions": []})

    return _response(200, {"suggestions": suggestions})


def _handle_health() -> dict[str, Any]:
    """Handle GET /health — used by the Route 53 failover health check."""
    return _response(200, {"status": "ok", "region": os.environ.get("AWS_REGION", "unknown")})


def _handle_locate(query_params: dict[str, str]) -> dict[str, Any]:
    """Handle GET /locate?lat=<lat>&lon=<lon>."""
    raw_lat = query_params.get("lat", "")
    raw_lon = query_params.get("lon", "")

    try:
        lat, lon = validate_coordinates(raw_lat, raw_lon)
    except ValidationError as exc:
        logger.warning("Coordinate validation failed: %s", exc)
        return _error(400, str(exc))

    try:
        api_key = get_api_key()
    except SecretsError as exc:
        logger.error("Weather credential retrieval failed: %s", exc)
        return _error(503, "Weather service is temporarily unavailable.")

    try:
        city = reverse_geocode(lat, lon, api_key)
    except CityNotFoundError:
        return _error(404, "No city found at the given coordinates.")
    except WeatherClientError as exc:
        logger.error("Reverse geocode error: %s", exc)
        return _error(502, str(exc))

    return _response(200, {"city": city})


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda handler entry point.

    Expected trigger: API Gateway HTTP API (proxy integration).
    Routes:
      GET /weather?city=<name>                        — fetch weather by city name
      GET /weather?lat=&lon=&name=&country=            — fetch weather for an exact /suggest pick
      GET /suggest?q=<partial name>                    — autocomplete suggestions for the search box
      GET /locate?lat=<f>&lon=<f>                      — reverse geocode coordinates to city name
      GET /health                                      — liveness/region check (Route 53 failover)
    """
    http_method = (event.get("requestContext", {}).get("http", {}).get("method") or
                   event.get("httpMethod", "GET")).upper()
    if http_method == "OPTIONS":
        return _response(200, {})

    route_key = event.get("routeKey", "")
    query_params = event.get("queryStringParameters") or {}

    if route_key == "GET /weather":
        return _handle_weather(query_params)
    if route_key == "GET /suggest":
        return _handle_suggest(query_params)
    if route_key == "GET /locate":
        return _handle_locate(query_params)
    if route_key == "GET /health":
        return _handle_health()

    return _error(404, "Not found.")
