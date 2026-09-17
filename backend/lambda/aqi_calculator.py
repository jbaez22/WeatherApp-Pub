"""
US EPA Air Quality Index (AQI) calculator.

Converts raw pollutant concentrations (as returned by OpenWeatherMap's Air
Pollution API, all in ug/m3) into the US EPA's 0-500 AQI scale, per
40 CFR Part 58 Appendix G. Breakpoints verified against
https://aqs.epa.gov/aqsweb/documents/codetables/aqi_breakpoints.html
(includes the May 2024 revision - PM2.5's "Good" threshold was lowered
from 12.0 to 9.0 ug/m3).

Scope note - "instant reading", not NowCast (see
docs/WeatherApp-AirQuality-Plan-V1.md #4.2): EPA's PM2.5/PM10/O3/CO
breakpoints are officially defined against rolling averages (24h, 24h,
8h, 8h respectively), but OpenWeatherMap's Air Pollution API only
returns a single instantaneous reading. This module applies the EPA
breakpoint formula directly to that instantaneous value rather than a
true rolling average - a deliberate, documented accuracy trade-off.
Swapping in NowCast-style averaging later only requires changing what
gets passed into calculate_epa_aqi() - this module's interface does not
need to change.

Two EPA methodology discontinuities, handled explicitly below rather
than silently mishandled:
  - Ozone: the 8-hour breakpoint table does not define AQI values >= 301
    (EPA requires switching to 1-hour ozone data instead). Out of scope
    here - concentrations above the 8-hour table's ceiling are capped at
    AQI 300 rather than computed.
  - SO2: the 1-hour breakpoint table does not define AQI values >= 200
    (EPA requires switching to a 24-hour SO2 average instead). Out of
    scope here - concentrations above the 1-hour table's ceiling are
    capped at AQI 200 rather than computed.
Both caps are rare in practice (they require already-severe pollution
events) and are documented, not silent.

Also not implemented: EPA's official per-pollutant concentration
truncation rules (e.g. PM10 truncated to a whole integer before lookup).
Skipping truncation only affects which side of a breakpoint boundary a
concentration within ~1 unit of it lands on - not a functional
correctness issue, just a minor precision difference from official
station-reported values.
"""

from typing import Any

# Molar volume at 25C, 1 atm - the standard condition EPA/AirNow use for
# converting real-time gas concentrations. Matches commonly published
# conversion factors exactly (1 ppb NO2 = 1.88 ug/m3, 1 ppb SO2 = 2.62 ug/m3,
# 1 ppm CO = 1145 ug/m3), used here as a cross-check on the formula below.
_MOLAR_VOLUME_25C_L_PER_MOL = 24.45

_MOLAR_MASS_G_PER_MOL = {
    "o3": 48.00,
    "co": 28.01,
    "so2": 64.07,
    "no2": 46.01,
}

# Each row: (concentration_low, concentration_high, aqi_low, aqi_high)
_PM25_BREAKPOINTS_UGM3 = [
    (0.0, 9.0, 0, 50),
    (9.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 125.4, 151, 200),
    (125.5, 225.4, 201, 300),
    (225.5, 325.4, 301, 500),
]

_PM10_BREAKPOINTS_UGM3 = [
    (0, 54, 0, 50),
    (55, 154, 51, 100),
    (155, 254, 101, 150),
    (255, 354, 151, 200),
    (355, 424, 201, 300),
    (425, 604, 301, 500),
]

# 8-hour table only - does not define AQI >= 301, see module docstring.
_O3_BREAKPOINTS_PPM = [
    (0.000, 0.054, 0, 50),
    (0.055, 0.070, 51, 100),
    (0.071, 0.085, 101, 150),
    (0.086, 0.105, 151, 200),
    (0.106, 0.200, 201, 300),
]

_CO_BREAKPOINTS_PPM = [
    (0.0, 4.4, 0, 50),
    (4.5, 9.4, 51, 100),
    (9.5, 12.4, 101, 150),
    (12.5, 15.4, 151, 200),
    (15.5, 30.4, 201, 300),
    (30.5, 50.4, 301, 500),
]

# 1-hour table only - does not define AQI >= 200, see module docstring.
_SO2_BREAKPOINTS_PPB = [
    (0, 35, 0, 50),
    (36, 75, 51, 100),
    (76, 185, 101, 150),
    (186, 304, 151, 200),
]

_NO2_BREAKPOINTS_PPB = [
    (0, 53, 0, 50),
    (54, 100, 51, 100),
    (101, 360, 101, 150),
    (361, 649, 151, 200),
    (650, 1249, 201, 300),
    (1250, 2049, 301, 500),
]

# EPA's six official AQI categories.
_CATEGORY_LABELS = [
    (0, 50, "Good"),
    (51, 100, "Moderate"),
    (101, 150, "Unhealthy for Sensitive Groups"),
    (151, 200, "Unhealthy"),
    (201, 300, "Very Unhealthy"),
    (301, 500, "Hazardous"),
]

# Human-readable names for the dominant-pollutant field in the response.
_POLLUTANT_LABELS = {
    "pm2_5": "PM2.5",
    "pm10": "PM10",
    "o3": "O3",
    "co": "CO",
    "so2": "SO2",
    "no2": "NO2",
}


class AqiCalculationError(RuntimeError):
    """Raised when no recognized pollutant concentration is provided."""


def _ugm3_to_ppm(ugm3: float, molar_mass_g_per_mol: float) -> float:
    """Convert ug/m3 to ppm at 25C/1atm (standard air-quality convention)."""
    return ugm3 * _MOLAR_VOLUME_25C_L_PER_MOL / (molar_mass_g_per_mol * 1000)


def _interpolate(concentration: float, breakpoints: list[tuple[float, float, int, int]]) -> int:
    """
    Apply the EPA linear interpolation formula:

        AQI = ((AQI_high - AQI_low) / (C_high - C_low)) * (C - C_low) + AQI_low

    Concentrations below the table's floor clamp to the lowest bracket.
    Concentrations above the table's ceiling clamp to the highest AQI
    value in the table (see module docstring for the O3/SO2 caveat this
    causes at very high concentrations).
    """
    if concentration <= breakpoints[0][0]:
        return breakpoints[0][2]
    if concentration > breakpoints[-1][1]:
        return breakpoints[-1][3]

    c_low, c_high, aqi_low, aqi_high = next(
        bp for bp in breakpoints if bp[0] <= concentration <= bp[1]
    )
    return round((aqi_high - aqi_low) / (c_high - c_low) * (concentration - c_low) + aqi_low)


def _category_for(aqi: int) -> str:
    for lo, hi, label in _CATEGORY_LABELS:
        if lo <= aqi <= hi:
            return label
    return _CATEGORY_LABELS[-1][2]


def calculate_epa_aqi(components: dict[str, float]) -> dict[str, Any]:
    """
    Compute the US EPA AQI (0-500) from raw pollutant concentrations.

    `components` must use OpenWeatherMap's Air Pollution API field names
    and units (all ug/m3): pm2_5, pm10, o3, co, so2, no2. An `nh3` key is
    accepted in the input without error but never contributes to the
    result - ammonia is not an EPA NAAQS criteria pollutant, so it has no
    AQI breakpoint table.

    The overall AQI is the maximum of each pollutant's individual
    sub-index (the EPA-standard rule), and `dominant_pollutant` names
    whichever pollutant produced that maximum.

    Returns {"aqi": int, "category": str, "dominant_pollutant": str}.
    Raises AqiCalculationError if none of the six recognized pollutant
    keys are present in `components`.
    """
    sub_indices: dict[str, int] = {}

    if "pm2_5" in components:
        sub_indices["pm2_5"] = _interpolate(components["pm2_5"], _PM25_BREAKPOINTS_UGM3)
    if "pm10" in components:
        sub_indices["pm10"] = _interpolate(components["pm10"], _PM10_BREAKPOINTS_UGM3)
    if "o3" in components:
        ppm = _ugm3_to_ppm(components["o3"], _MOLAR_MASS_G_PER_MOL["o3"])
        sub_indices["o3"] = _interpolate(ppm, _O3_BREAKPOINTS_PPM)
    if "co" in components:
        ppm = _ugm3_to_ppm(components["co"], _MOLAR_MASS_G_PER_MOL["co"])
        sub_indices["co"] = _interpolate(ppm, _CO_BREAKPOINTS_PPM)
    if "so2" in components:
        ppb = _ugm3_to_ppm(components["so2"], _MOLAR_MASS_G_PER_MOL["so2"]) * 1000
        sub_indices["so2"] = _interpolate(ppb, _SO2_BREAKPOINTS_PPB)
    if "no2" in components:
        ppb = _ugm3_to_ppm(components["no2"], _MOLAR_MASS_G_PER_MOL["no2"]) * 1000
        sub_indices["no2"] = _interpolate(ppb, _NO2_BREAKPOINTS_PPB)

    if not sub_indices:
        raise AqiCalculationError("No recognized pollutant concentrations provided.")

    dominant_key = max(sub_indices, key=lambda k: sub_indices[k])
    aqi = sub_indices[dominant_key]

    return {
        "aqi": aqi,
        "category": _category_for(aqi),
        "dominant_pollutant": _POLLUTANT_LABELS[dominant_key],
    }
