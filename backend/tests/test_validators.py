"""
Unit tests for backend/lambda/validators.py

Covers: happy path, every rejection case, whitespace trimming,
case normalisation, and boundary lengths.
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

from validators import ValidationError, validate_city, validate_coordinates


class TestValidateCityHappyPath:
    def test_simple_city_returns_lowercase(self):
        assert validate_city("London") == "london"

    def test_already_lowercase_unchanged(self):
        assert validate_city("paris") == "paris"

    def test_mixed_case_normalised(self):
        assert validate_city("New York") == "new york"

    def test_leading_trailing_whitespace_stripped(self):
        assert validate_city("  Berlin  ") == "berlin"

    def test_city_with_hyphen(self):
        assert validate_city("San-Jose") == "san-jose"

    def test_city_with_period(self):
        assert validate_city("St. Louis") == "st. louis"

    def test_exactly_100_characters_accepted(self):
        city = "a" * 100
        assert validate_city(city) == city.lower()

    def test_single_character_city(self):
        assert validate_city("A") == "a"


class TestValidateCityRejections:
    def test_empty_string_raises(self):
        with pytest.raises(ValidationError, match="required"):
            validate_city("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValidationError, match="required"):
            validate_city("   ")

    def test_101_characters_raises(self):
        with pytest.raises(ValidationError, match="100"):
            validate_city("a" * 101)

    def test_digits_rejected(self):
        with pytest.raises(ValidationError, match="letters"):
            validate_city("City123")

    def test_script_injection_rejected(self):
        with pytest.raises(ValidationError, match="letters"):
            validate_city("<script>alert(1)</script>")

    def test_sql_injection_rejected(self):
        with pytest.raises(ValidationError, match="letters"):
            validate_city("' OR 1=1 --")

    def test_semicolon_rejected(self):
        with pytest.raises(ValidationError, match="letters"):
            validate_city("London; DROP TABLE cities")

    def test_ampersand_rejected(self):
        with pytest.raises(ValidationError, match="letters"):
            validate_city("London&Paris")

    def test_equals_sign_rejected(self):
        with pytest.raises(ValidationError, match="letters"):
            validate_city("city=name")

    def test_non_string_type_raises(self):
        with pytest.raises(ValidationError, match="string"):
            validate_city(None)

    def test_integer_type_raises(self):
        with pytest.raises(ValidationError, match="string"):
            validate_city(42)


class TestValidateCoordinatesHappyPath:
    def test_valid_coordinates_returned_as_floats(self):
        lat, lon = validate_coordinates("40.71", "-74.01")
        assert lat == 40.71
        assert lon == -74.01

    def test_boundary_values_accepted(self):
        assert validate_coordinates("90", "180") == (90.0, 180.0)
        assert validate_coordinates("-90", "-180") == (-90.0, -180.0)

    def test_zero_coordinates_accepted(self):
        assert validate_coordinates("0", "0") == (0.0, 0.0)

    def test_whitespace_stripped(self):
        lat, lon = validate_coordinates("  51.5  ", "  -0.12  ")
        assert lat == 51.5
        assert lon == -0.12


class TestValidateCoordinatesRejections:
    def test_empty_lat_raises(self):
        with pytest.raises(ValidationError, match="'lat'"):
            validate_coordinates("", "-74.01")

    def test_empty_lon_raises(self):
        with pytest.raises(ValidationError, match="'lon'"):
            validate_coordinates("40.71", "")

    def test_non_numeric_lat_raises(self):
        with pytest.raises(ValidationError, match="number"):
            validate_coordinates("abc", "-74.01")

    def test_non_numeric_lon_raises(self):
        with pytest.raises(ValidationError, match="number"):
            validate_coordinates("40.71", "xyz")

    def test_lat_too_high_raises(self):
        with pytest.raises(ValidationError, match="'lat'"):
            validate_coordinates("90.1", "0")

    def test_lat_too_low_raises(self):
        with pytest.raises(ValidationError, match="'lat'"):
            validate_coordinates("-90.1", "0")

    def test_lon_too_high_raises(self):
        with pytest.raises(ValidationError, match="'lon'"):
            validate_coordinates("0", "180.1")

    def test_lon_too_low_raises(self):
        with pytest.raises(ValidationError, match="'lon'"):
            validate_coordinates("0", "-180.1")
