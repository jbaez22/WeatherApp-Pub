"""
AWS Secrets Manager retrieval for the Weather Dashboard Lambda.

The API key is fetched once per Lambda execution environment (module-level
cache) to minimise Secrets Manager API calls and cold-start latency. The
value is never logged or included in any response payload.

The GeoNames username (docs/WeatherApp-GeoNames-Suggest-Upgrade-Plan-V1.md)
follows the identical pattern as its own separate secret/cache/function -
not combined with the OWM key, since the two credentials have independent
lifecycles (the OWM key rotates every 90 days, the GeoNames username never
does).
"""

import logging
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

_SECRET_NAME = os.environ.get(
    "SECRET_NAME",
    "weather-dashboard/openweathermap-api-key",
)

_GEONAMES_SECRET_NAME = os.environ.get(
    "GEONAMES_SECRET_NAME",
    "weather-dashboard/geonames-username",
)

# Module-level cache — survives across warm invocations within the same
# Lambda execution environment.
_cached_api_key: str | None = None
_cached_geonames_username: str | None = None


class SecretsError(RuntimeError):
    """Raised when a credential cannot be retrieved from Secrets Manager."""


def get_api_key() -> str:
    """
    Return the OpenWeatherMap API key from Secrets Manager.

    Uses a module-level cache so subsequent warm invocations skip the
    Secrets Manager call entirely. Raises SecretsError if the secret
    cannot be fetched.
    """
    global _cached_api_key  # noqa: PLW0603

    if _cached_api_key is not None:
        return _cached_api_key

    client = boto3.client("secretsmanager")
    try:
        response = client.get_secret_value(SecretId=_SECRET_NAME)
        _cached_api_key = response["SecretString"]
        logger.info("Weather credential loaded from Secrets Manager.")
        return _cached_api_key
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        logger.error(
            "Secrets Manager ClientError fetching weather credential: %s",
            error_code,
        )
        raise SecretsError(
            f"Failed to retrieve weather credential from Secrets Manager: {error_code}"
        ) from exc
    except BotoCoreError as exc:
        logger.error(
            "BotoCoreError fetching weather credential from Secrets Manager: %s",
            exc,
        )
        raise SecretsError(
            "Unexpected error communicating with Secrets Manager."
        ) from exc


def get_geonames_username() -> str:
    """
    Return the GeoNames web-service username from Secrets Manager.

    Uses a module-level cache so subsequent warm invocations skip the
    Secrets Manager call entirely. Raises SecretsError if the secret
    cannot be fetched.
    """
    global _cached_geonames_username  # noqa: PLW0603

    if _cached_geonames_username is not None:
        return _cached_geonames_username

    client = boto3.client("secretsmanager")
    try:
        response = client.get_secret_value(SecretId=_GEONAMES_SECRET_NAME)
        _cached_geonames_username = response["SecretString"]
        logger.info("GeoNames credential loaded from Secrets Manager.")
        return _cached_geonames_username
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        logger.error(
            "Secrets Manager ClientError fetching GeoNames credential: %s",
            error_code,
        )
        raise SecretsError(
            f"Failed to retrieve GeoNames credential from Secrets Manager: {error_code}"
        ) from exc
    except BotoCoreError as exc:
        logger.error(
            "BotoCoreError fetching GeoNames credential from Secrets Manager: %s",
            exc,
        )
        raise SecretsError(
            "Unexpected error communicating with Secrets Manager."
        ) from exc


def _clear_cache() -> None:
    """Reset the module-level caches. Used only in unit tests."""
    global _cached_api_key, _cached_geonames_username  # noqa: PLW0603
    _cached_api_key = None
    _cached_geonames_username = None
