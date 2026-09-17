"""
Input validation for the Weather Dashboard Lambda.

All external input must pass through validate_city() before any downstream
call is made. The whitelist regex matches the same pattern enforced in the
frontend so both layers agree on what is acceptable.
"""

import re

# Whitelist: letters, spaces, hyphens, periods — 1 to 100 characters.
# Matches the regex in frontend/js/app.js so the two layers stay in sync.
_CITY_PATTERN = re.compile(r'^[a-zA-Z\s\-\.]{1,100}$')

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
            "City name may only contain letters, spaces, hyphens, and periods."
        )

    return trimmed.lower()


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
