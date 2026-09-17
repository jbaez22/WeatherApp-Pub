# API Documentation — V2
> **Label:** V2 | Supersedes: `docs/api-documentation.md` (V1)
> Effective from: v2.0.0
> Changes: One Call API 3.0 backend, 7-day daily forecast, 48-hour hourly data

---

## What Changed from V1

| Aspect | V1 | V2 |
|---|---|---|
| OWM calls per request | 2 (`/weather` + `/forecast`) | 2 (`/geo/1.0/direct` + `/data/3.0/onecall`) |
| Forecast data | `forecast.list[]` — raw 3-hour intervals | `daily[]` — clean daily summaries (7 days) |
| Hourly data | Not present | `hourly[]` — 48 hours of data |
| City resolution | City name sent directly to OWM `/weather` | Geocoded first → lat/lon → One Call |
| Additional daily fields | None | `pop`, `uvi`, `sunrise`, `sunset`, `humidity`, `wind_speed` |
| Frontend API URL | `/weather?city={city}` | Unchanged — same endpoint, same parameter |

The external API endpoint (`GET /weather?city={city}`) and all error codes are **unchanged**. Only the response body shape changes.

---

## Endpoint Reference

### GET /weather

Identical to V1 — no change to URL, method, parameters, or authentication.

**Request**

```
GET https://{api-id}.execute-api.us-east-1.amazonaws.com/weather?city={city}
```

| Parameter | Type | Required | Constraints |
|-----------|------|----------|-------------|
| `city` | string | Yes | 1–100 chars; letters, spaces, hyphens, periods only |

**Headers:** None required. CORS pre-flight (`OPTIONS`) is handled automatically.

---

## V2 Response — Success (200)

```json
{
  "current": {
    "name": "London",
    "sys": {
      "country": "GB"
    },
    "dt": 1751558400,
    "main": {
      "temp": 18.2,
      "feels_like": 17.1,
      "humidity": 72,
      "pressure": 1008
    },
    "wind": {
      "speed": 6.3
    },
    "visibility": 8000,
    "weather": [
      {
        "icon": "04d",
        "description": "overcast clouds"
      }
    ]
  },
  "daily": [
    {
      "dt": 1751558400,
      "high": 20.5,
      "low": 13.1,
      "icon": "04d",
      "desc": "overcast clouds",
      "pop": 0.35,
      "uvi": 3.2,
      "sunrise": 1751529600,
      "sunset": 1751584800,
      "humidity": 72,
      "wind_speed": 6.3
    }
  ],
  "hourly": [
    {
      "dt": 1751558400,
      "temp": 18.2,
      "feels_like": 17.1,
      "icon": "04d",
      "desc": "overcast clouds",
      "pop": 0.15
    }
  ]
}
```

### Response Field Reference

#### `current` object

Identical field names to V1 — `renderCurrent()` on the frontend requires no changes.

| Field | Type | Description |
|---|---|---|
| `name` | string | City display name (from geocoding, not One Call) |
| `sys.country` | string | ISO 3166-1 alpha-2 country code |
| `dt` | integer | Current observation timestamp (Unix seconds, UTC) |
| `main.temp` | float | Current temperature in Celsius |
| `main.feels_like` | float | Perceived temperature in Celsius |
| `main.humidity` | integer | Relative humidity (%) |
| `main.pressure` | integer | Atmospheric pressure (hPa) |
| `wind.speed` | float | Wind speed in m/s (frontend converts to km/h: × 3.6) |
| `visibility` | integer | Visibility in metres (max 10,000) |
| `weather[0].icon` | string | OWM icon code (e.g. `"01d"`) |
| `weather[0].description` | string | Human-readable condition (e.g. `"clear sky"`) |

#### `daily` array — 7 entries (today + 6 days)

| Field | Type | Description |
|---|---|---|
| `dt` | integer | Noon timestamp for this day (Unix seconds, UTC) |
| `high` | float | Daily maximum temperature (Celsius) |
| `low` | float | Daily minimum temperature (Celsius) |
| `icon` | string | OWM icon code for the dominant condition |
| `desc` | string | Human-readable condition description |
| `pop` | float | Precipitation probability (0.0–1.0); display as `Math.round(pop * 100) + '%'` |
| `uvi` | float | UV index (0–11+); display as `Math.round(uvi)` |
| `sunrise` | integer | Sunrise timestamp (Unix seconds, UTC) |
| `sunset` | integer | Sunset timestamp (Unix seconds, UTC) |
| `humidity` | integer | Average relative humidity for the day (%) |
| `wind_speed` | float | Average wind speed in m/s |

#### `hourly` array — 48 entries (next 48 hours)

| Field | Type | Description |
|---|---|---|
| `dt` | integer | Hour timestamp (Unix seconds, UTC) |
| `temp` | float | Temperature in Celsius |
| `feels_like` | float | Perceived temperature in Celsius |
| `icon` | string | OWM icon code |
| `desc` | string | Human-readable condition description |
| `pop` | float | Precipitation probability (0.0–1.0) |

**Filtering hourly by day:** The frontend selects entries for a given day by comparing `new Date(entry.dt * 1000).toDateString()` against the day card's `dt`. This handles timezone differences correctly since both use the browser's local date string.

---

## GET /health

Added for the multi-region failover rollout
(`WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md`). Liveness/region
check — this is what Route 53's failover health check polls every 30
seconds against the primary region.

```
GET https://{api-id}.execute-api.{region}.amazonaws.com/health
```

No parameters. Never touches DynamoDB or Secrets Manager — always a fast,
dependency-free response.

**Response (200):**
```json
{"status": "ok", "region": "us-east-1"}
```
`region` reflects the Lambda's own execution region (`AWS_REGION`), so this
is the definitive way to tell which region actually served a given request
— useful when troubleshooting through the failover domain.

**As of the multi-region rollout, the frontend calls a stable failover
domain instead of a region-specific URL:**
```
https://api.weather.craftingnewtech.com
```
Route 53 Failover routing transparently points this at us-east-1 (normal
operation) or us-west-2 (during a primary-region outage) — the client
never needs to know which region actually answered. The raw
`{api-id}.execute-api.{region}.amazonaws.com` URLs above still work
directly (used for regional health checks and debugging) but are no
longer what the deployed frontend calls.

---

## Error Responses

All error codes and shapes are **unchanged from V1**.

| HTTP Status | `error` | `message` | Cause |
|---|---|---|---|
| 400 | `true` | `"City parameter is required..."` | Missing or empty city |
| 400 | `true` | `"City name may only contain..."` | Invalid characters in city name |
| 404 | `true` | `"City not found..."` | Geocoding returned no results |
| 429 | `true` | `"Weather service rate limit..."` | OWM rate limit hit |
| 502 | `true` | `"Unable to reach the weather service..."` | OWM API unreachable |
| 503 | `true` | `"Weather service temporarily unavailable..."` | SSM credential fetch failed |

```json
{
  "error": true,
  "message": "City not found. Please check the spelling and try again."
}
```

---

## Backend Implementation Notes

### Two-call flow (V2)

```
1. GET /geo/1.0/direct?q={city}&limit=1&appid={key}
   → returns: [{ "name": "London", "lat": 51.51, "lon": -0.13, "country": "GB" }]
   → raises CityNotFoundError if list is empty

2. GET /data/3.0/onecall?lat={lat}&lon={lon}&exclude=minutely,alerts&units=metric&appid={key}
   → returns: { "current": {...}, "daily": [...8], "hourly": [...48] }
```

### Response shaping in Lambda

The Lambda transforms the raw One Call response before caching and returning:

```python
return {
    "current": {
        "name": geo_name,           # from geocode step
        "sys": {"country": geo_country},
        "dt": onecall["current"]["dt"],
        "main": {
            "temp":       onecall["current"]["temp"],
            "feels_like": onecall["current"]["feels_like"],
            "humidity":   onecall["current"]["humidity"],
            "pressure":   onecall["current"]["pressure"],
        },
        "wind":       {"speed": onecall["current"]["wind_speed"]},
        "visibility": onecall["current"].get("visibility", 10000),
        "weather":    onecall["current"]["weather"],
    },
    "daily": [
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
        for day in onecall["daily"][:7]
    ],
    "hourly": [
        {
            "dt":         hour["dt"],
            "temp":       hour["temp"],
            "feels_like": hour["feels_like"],
            "icon":       hour["weather"][0]["icon"],
            "desc":       hour["weather"][0]["description"],
            "pop":        hour.get("pop", 0),
        }
        for hour in onecall["hourly"][:48]
    ],
}
```

### Cache

The DynamoDB cache key and TTL (15 minutes) are **unchanged**. The cache now stores the V2 response shape. Any V1-format entries in the cache will expire naturally within 15 minutes of deployment — no manual flush required.

---

## Frontend Rendering Notes

### Displaying `pop` (precipitation probability)

```javascript
const precipPct = Math.round(day.pop * 100) + '%';
// e.g. 0.35 → "35%"
```

### Displaying `uvi` (UV index)

```javascript
const uviDisplay = Math.round(day.uvi);
// e.g. 6.5 → 7
```

### Displaying `sunrise` / `sunset`

```javascript
const sunrise = new Date(day.sunrise * 1000).toLocaleTimeString('en-US', {
  hour: 'numeric', minute: '2-digit', hour12: true
});
// e.g. "6:23 AM"
```

### Hourly strip — filtering by day

```javascript
function getHoursForDay(hourlyData, dayDt) {
  const dayStr = new Date(dayDt * 1000).toDateString();
  return hourlyData.filter(h => new Date(h.dt * 1000).toDateString() === dayStr);
}
```

### C°/F° toggle with hourly panel open

When the unit toggle fires, if a card is currently expanded, re-render the hourly panel with the new unit in addition to updating the forecast cards.

---

## OWM One Call API 3.0 — Subscription Notes

- **Plan:** One Call by Call
- **Free tier:** 1,000 calls/day (no charge under this limit)
- **With 15-min DynamoDB cache:** ~4 unique API calls/hour per city; 250 simultaneously active cities needed to approach 1,000 calls/day
- **Effective cost for this portfolio project:** $0.00/month
- **Activation:** openweathermap.org → Billing → One Call by Call → add payment method
- **Key:** Same API key as V1 — no new key required
- **Docs:** https://openweathermap.org/api/one-call-3
