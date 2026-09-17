"""
Unit tests for backend/lambda/aqi_calculator.py

Pure-function module, no AWS calls - no moto/mocking needed. Covers every
pollutant's breakpoint boundaries, the unit conversion math, the two EPA
methodology discontinuity caps (ozone >300, SO2 >200), dominant-pollutant
selection, category-label boundaries, and the no-recognized-pollutant
error path.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

from aqi_calculator import (
    AqiCalculationError,
    _category_for,
    _interpolate,
    _ugm3_to_ppm,
    calculate_epa_aqi,
)


class TestPM25:
    def test_bottom_of_good_is_aqi_zero(self):
        assert calculate_epa_aqi({"pm2_5": 0.0})["aqi"] == 0

    def test_top_of_good_boundary(self):
        assert calculate_epa_aqi({"pm2_5": 9.0})["aqi"] == 50

    def test_known_moderate_value(self):
        """15.54 ug/m3 hand-verified against the EPA interpolation formula
        to equal AQI 63 - the example value used throughout the approved
        mockups (docs/mockups/air-quality-mockup-epa.html)."""
        result = calculate_epa_aqi({"pm2_5": 15.54})
        assert result["aqi"] == 63
        assert result["category"] == "Moderate"
        assert result["dominant_pollutant"] == "PM2.5"

    def test_top_of_table_is_aqi_500(self):
        assert calculate_epa_aqi({"pm2_5": 325.4})["aqi"] == 500

    def test_above_table_ceiling_caps_at_500_not_exception(self):
        assert calculate_epa_aqi({"pm2_5": 999.0})["aqi"] == 500


class TestPM10:
    def test_top_of_good_boundary(self):
        assert calculate_epa_aqi({"pm10": 54})["aqi"] == 50

    def test_bottom_of_moderate_boundary(self):
        assert calculate_epa_aqi({"pm10": 55})["aqi"] == 51

    def test_above_table_ceiling_caps_at_500(self):
        assert calculate_epa_aqi({"pm10": 10000})["aqi"] == 500


class TestOzone:
    def test_top_of_good_boundary_ppm(self):
        # 0.054 ppm -> ug/m3 for the public-API call
        ugm3 = 0.054 * 48.00 * 1000 / 24.45
        assert calculate_epa_aqi({"o3": ugm3})["aqi"] == 50

    def test_above_8hr_table_ceiling_caps_at_300_not_500(self):
        """The 8-hour ozone table does not define AQI >= 301 (EPA requires
        1-hour data for that range, out of scope here) - must cap at 300,
        not silently extrapolate past the table."""
        result = calculate_epa_aqi({"o3": 100000.0})
        assert result["aqi"] == 300
        assert result["category"] == "Very Unhealthy"


class TestCO:
    def test_top_of_good_boundary_ppm(self):
        ugm3 = 4.4 * 28.01 * 1000 / 24.45
        assert calculate_epa_aqi({"co": ugm3})["aqi"] == 50

    def test_above_table_ceiling_caps_at_500(self):
        assert calculate_epa_aqi({"co": 1_000_000.0})["aqi"] == 500


class TestSO2:
    def test_top_of_good_boundary_ppb(self):
        ugm3 = 35 * 64.07 / 24.45
        assert calculate_epa_aqi({"so2": ugm3})["aqi"] == 50

    def test_above_1hr_table_ceiling_caps_at_200_not_500(self):
        """The 1-hour SO2 table does not define AQI >= 200 (EPA requires a
        24-hour average for that range, out of scope here) - must cap at
        200, not silently extrapolate past the table."""
        result = calculate_epa_aqi({"so2": 100000.0})
        assert result["aqi"] == 200
        assert result["category"] == "Unhealthy"


class TestNO2:
    def test_top_of_good_boundary_ppb(self):
        ugm3 = 53 * 46.01 / 24.45
        assert calculate_epa_aqi({"no2": ugm3})["aqi"] == 50

    def test_above_table_ceiling_caps_at_500(self):
        assert calculate_epa_aqi({"no2": 1_000_000.0})["aqi"] == 500


class TestUnitConversion:
    """Cross-checked against commonly published reference conversion
    factors at 25C/1atm: 1 ppb NO2 = 1.88 ug/m3, 1 ppb SO2 = 2.62 ug/m3,
    1 ppm CO = 1145 ug/m3."""

    def test_no2_conversion_factor(self):
        ppm = _ugm3_to_ppm(1.88, 46.01)
        assert round(ppm * 1000, 2) == pytest.approx(1.0, abs=0.01)

    def test_so2_conversion_factor(self):
        ppm = _ugm3_to_ppm(2.62, 64.07)
        assert round(ppm * 1000, 2) == pytest.approx(1.0, abs=0.01)

    def test_co_conversion_factor(self):
        ppm = _ugm3_to_ppm(1145.0, 28.01)
        assert ppm == pytest.approx(1.0, abs=0.01)


class TestInterpolationHelper:
    _TABLE = [(0.0, 10.0, 0, 50), (10.1, 20.0, 51, 100)]

    def test_exact_bracket_boundary(self):
        assert _interpolate(10.0, self._TABLE) == 50

    def test_midpoint_interpolation(self):
        assert _interpolate(5.0, self._TABLE) == 25

    def test_below_floor_clamps_to_first_bracket(self):
        assert _interpolate(-5.0, self._TABLE) == 0

    def test_above_ceiling_clamps_to_last_bracket_aqi(self):
        assert _interpolate(999.0, self._TABLE) == 100


class TestCategoryFor:
    @pytest.mark.parametrize(
        "aqi,expected",
        [
            (0, "Good"),
            (50, "Good"),
            (51, "Moderate"),
            (100, "Moderate"),
            (101, "Unhealthy for Sensitive Groups"),
            (150, "Unhealthy for Sensitive Groups"),
            (151, "Unhealthy"),
            (200, "Unhealthy"),
            (201, "Very Unhealthy"),
            (300, "Very Unhealthy"),
            (301, "Hazardous"),
            (500, "Hazardous"),
        ],
    )
    def test_category_boundaries(self, aqi, expected):
        assert _category_for(aqi) == expected

    def test_out_of_range_falls_back_to_hazardous(self):
        """Defensive fallback - _interpolate never actually produces a
        value outside 0-500, but this guards against it regardless."""
        assert _category_for(501) == "Hazardous"


class TestDominantPollutantSelection:
    def test_highest_sub_index_wins(self):
        result = calculate_epa_aqi({"pm2_5": 5.0, "o3": 100000.0, "co": 1.0})
        assert result["dominant_pollutant"] == "O3"
        assert result["aqi"] == 300

    def test_nh3_present_but_ignored(self):
        """NH3 is not an EPA NAAQS criteria pollutant - it has no
        breakpoint table and must never affect the result."""
        with_nh3 = calculate_epa_aqi({"pm2_5": 15.54, "nh3": 999999.0})
        without_nh3 = calculate_epa_aqi({"pm2_5": 15.54})
        assert with_nh3 == without_nh3

    def test_single_pollutant_is_trivially_dominant(self):
        result = calculate_epa_aqi({"no2": 10.0})
        assert result["dominant_pollutant"] == "NO2"


class TestErrorHandling:
    def test_no_recognized_pollutant_raises(self):
        with pytest.raises(AqiCalculationError, match="No recognized pollutant"):
            calculate_epa_aqi({})

    def test_only_nh3_raises(self):
        with pytest.raises(AqiCalculationError):
            calculate_epa_aqi({"nh3": 10.0})
