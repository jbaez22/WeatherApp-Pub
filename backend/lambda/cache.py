"""
DynamoDB cache layer for the Weather Dashboard Lambda.

Reads and writes weather data keyed by lowercase city name.
TTL is set to 15 minutes (900 seconds) from the time of the write so
DynamoDB automatically expires stale entries — no cleanup job needed.
"""

import json
import logging
import time
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

_TABLE_NAME = "WeatherCache"
_TTL_SECONDS = 900  # 15 minutes


class CacheError(RuntimeError):
    """Raised when a DynamoDB operation fails unexpectedly."""


def _to_dynamo(obj: Any) -> Any:
    """Convert Python floats to Decimal so DynamoDB's resource API accepts them."""
    return json.loads(json.dumps(obj), parse_float=Decimal)


def _from_dynamo(obj: Any) -> Any:
    """Convert Decimal values back to float after a DynamoDB read."""
    return json.loads(json.dumps(obj, default=float))


def _get_table():
    """Return the DynamoDB Table resource. Isolated for easy mocking in tests."""
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(_TABLE_NAME)


def get_cached_weather(city: str) -> dict[str, Any] | None:
    """
    Return cached weather data for `city`, or None on a cache miss.

    `city` must already be lowercase (enforced by the handler).
    Raises CacheError only on unexpected DynamoDB failures — a cache miss
    is a normal condition and returns None, not an exception.
    """
    try:
        table = _get_table()
        response = table.get_item(Key={"city": city})
        item = response.get("Item")

        if item is None:
            logger.info("Cache miss for city: %s", city)
            return None

        # DynamoDB TTL deletion is not instantaneous; check expiry ourselves.
        if int(item.get("ttl", 0)) < int(time.time()):
            logger.info("Cache expired for city: %s", city)
            return None

        logger.info("Cache hit for city: %s", city)
        return _from_dynamo(item.get("data"))

    except (ClientError, BotoCoreError) as exc:
        logger.error("DynamoDB error on cache read for city %s: %s", city, exc)
        raise CacheError("Failed to read from cache.") from exc


def get_stale_cached_weather(city: str) -> dict[str, Any] | None:
    """
    Return the last cached weather data for `city` regardless of TTL
    expiry. Used only as a fallback when the OWM API call fails — normal
    reads must go through get_cached_weather().

    `city` must already be lowercase. Raises no exceptions on DynamoDB
    failure — a stale-cache read failure just means no fallback is
    available, which the caller treats the same as no stale data found.
    """
    try:
        table = _get_table()
        response = table.get_item(Key={"city": city})
        item = response.get("Item")

        if item is None:
            logger.info("No stale cache available for city: %s", city)
            return None

        logger.info("Serving stale cache for city: %s", city)
        return _from_dynamo(item.get("data"))

    except (ClientError, BotoCoreError) as exc:
        logger.error("DynamoDB error on stale cache read for city %s: %s", city, exc)
        return None


def set_cached_weather(city: str, data: dict[str, Any]) -> None:
    """
    Write weather data to the cache with a 15-minute TTL.

    `city` must already be lowercase. A DynamoDB write failure is logged
    but does not propagate — a cache write failure is non-fatal because
    the caller already has the data to return to the user.
    """
    expiry = int(time.time()) + _TTL_SECONDS
    try:
        table = _get_table()
        table.put_item(
            Item={
                "city": city,
                "data": _to_dynamo(data),
                "ttl": expiry,
                "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        logger.info("Cache written for city: %s (TTL: %d)", city, expiry)
    except (ClientError, BotoCoreError) as exc:
        logger.warning(
            "DynamoDB error on cache write for city %s (non-fatal): %s", city, exc
        )
