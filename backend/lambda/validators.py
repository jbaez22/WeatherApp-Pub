"""
Input validation for the Weather Dashboard Lambda.

All external input must pass through validate_city() before any downstream
call is made. The whitelist regex matches the same pattern enforced in the
frontend so both layers agree on what is acceptable.
"""

import re

# Whitelist: letters, spaces, hyphens, periods, commas — 1 to 100 characters.
# Comma is allowed so "City, State" / "City, Country" free-text queries
# (e.g. "Woodbourne, NY") pass validation instead of being rejected outright
# - the /suggest autocomplete is the precise disambiguation path, this
# regex just needs to not block the common manual-typing case.
# Matches the regex in frontend/js/app.js so the two layers stay in sync.
_CITY_PATTERN = re.compile(r'^[a-zA-Z\s\-\.,]{1,100}$')

# Narrower pattern for a single geocoded field (city display name) - no
# comma, since name/country arrive as already-split fields from a chosen
# /suggest result rather than free text.
_LOCATION_NAME_PATTERN = re.compile(r'^[a-zA-Z\s\-\.]{1,100}$')

# ISO 3166-1 alpha-2 country code, as returned by OWM geocoding.
_COUNTRY_CODE_PATTERN = re.compile(r'^[A-Za-z]{2}$')

_MAX_LENGTH = 100


class ValidationError(ValueError):
    """Raised when input fails validation."""


def validate_city(raw: str) -> str:
    """
    Validate and normalise a raw city string from the query string.

    Returns the trimmed, lowercase city string on success.
    Raises ValidationError with a user-safe message on failure.
    """
    if not isinstance(raw, str):
        raise ValidationError("City parameter must be a string.")

    trimmed = raw.strip()

    if not trimmed:
        raise ValidationError("City parameter is required and cannot be empty.")

    if len(trimmed) > _MAX_LENGTH:
        raise ValidationError(
            f"City name must not exceed {_MAX_LENGTH} characters."
        )

    if not _CITY_PATTERN.match(trimmed):
        raise ValidationError(
            "City name may only contain letters, spaces, hyphens, periods, and commas."
        )

    return trimmed.lower()


def validate_location_name(raw: str, field_label: str) -> str:
    """
    Validate a single geocoded field (city name or state) arriving as its
    own query-string parameter, e.g. from a /suggest result the user picked.

    Returns the trimmed string on success. Raises ValidationError with a
    user-safe message on failure.
    """
    if not isinstance(raw, str):
        raise ValidationError(f"'{field_label}' parameter must be a string.")

    trimmed = raw.strip()

    if not trimmed:
        raise ValidationError(f"'{field_label}' parameter is required.")

    if len(trimmed) > _MAX_LENGTH:
        raise ValidationError(
            f"'{field_label}' must not exceed {_MAX_LENGTH} characters."
        )

    if not _LOCATION_NAME_PATTERN.match(trimmed):
        raise ValidationError(
            f"'{field_label}' may only contain letters, spaces, hyphens, and periods."
        )

    return trimmed


def validate_country_code(raw: str) -> str:
    """
    Validate an ISO 3166-1 alpha-2 country code, as returned by OWM
    geocoding and passed back from a chosen /suggest result.

    Returns the uppercased code on success, or an empty string if `raw` is
    empty (country is optional - some OWM results omit it).
    Raises ValidationError with a user-safe message on failure.
    """
    if not isinstance(raw, str):
        raise ValidationError("'country' parameter must be a string.")

    trimmed = raw.strip()

    if not trimmed:
        return ""

    if not _COUNTRY_CODE_PATTERN.match(trimmed):
        raise ValidationError("'country' must be a 2-letter country code.")

    return trimmed.upper()


def validate_coordinates(lat: str, lon: str) -> tuple[float, float]:
    """
    Validate raw lat/lon strings from the query string.

    Returns (lat, lon) as floats on success.
    Raises ValidationError with a user-safe message on failure.
    """
    for name, raw, lo, hi in (
        ("lat", lat, -90.0, 90.0),
        ("lon", lon, -180.0, 180.0),
    ):
        if not isinstance(raw, str) or not raw.strip():
            raise ValidationError(f"'{name}' parameter is required.")
        try:
            value = float(raw.strip())
        except ValueError:
            raise ValidationError(f"'{name}' must be a number.")
        if not (lo <= value <= hi):
            raise ValidationError(
                f"'{name}' must be between {lo} and {hi}."
            )

    return float(lat.strip()), float(lon.strip())
