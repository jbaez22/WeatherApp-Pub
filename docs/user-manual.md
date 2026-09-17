# User Manual

**Weather Dashboard** — a serverless weather application at [https://weather.craftingnewtech.com](https://weather.craftingnewtech.com)

---

## Getting Started

No account or login is required. Open the URL in any modern browser (Chrome, Firefox, Safari, Edge).

---

## Searching for a City

1. Click the search field at the top of the page.
2. Type the name of any city (e.g. `London`, `New York`, `Tokyo`, `São Paulo`).
3. Press **Enter** or click the search button.

**Tips:**
- You can include a country code for unambiguous results: `Paris, FR` or `Springfield, US`
- City names are not case-sensitive (`london` and `London` both work)
- Special characters and accented letters are supported (`München`, `São Paulo`)
- The most common English spelling of a city usually works; if a city is not found, try an alternative spelling or add the country code

If the city is not recognised, a "city not found" message appears. Try a nearby major city or check the spelling.

---

## Current Weather

After a successful search, the **Current Weather** card shows:

| Field | Description |
|-------|-------------|
| Temperature | Actual air temperature |
| Feels Like | Apparent temperature accounting for wind chill or heat index |
| Humidity | Relative humidity as a percentage |
| Wind | Wind speed |
| Pressure | Atmospheric pressure in hPa |
| Visibility | Horizontal visibility (maximum displayed: 10 km) |
| Conditions | Short weather description (e.g. "overcast clouds", "light rain") |
| Weather icon | Visual representation of current conditions |
| Sunrise / Sunset | Local sunrise and sunset times |

---

## Celsius / Fahrenheit Toggle

A **C° / F°** toggle button appears next to the temperature. Click it to switch between Celsius and Fahrenheit. The conversion happens instantly in the browser — no new network request is made.

The toggle affects:
- The current temperature and feels-like temperature
- All forecast temperatures

Your preference is not saved between sessions; the dashboard always starts in Celsius.

---

## 5-Day Forecast

Below the current weather card, a row of forecast panels shows the weather at 3-hour intervals for the next 5 days (up to 40 intervals).

Each forecast panel shows:
- Date and time
- Temperature
- Weather icon
- Short description
- Wind speed
- Humidity

---

## Data Freshness

Weather data is cached for **15 minutes**. If you search for the same city within 15 minutes of a previous search, you will see the same data (from cache). After 15 minutes, the next search fetches fresh data from OpenWeatherMap.

The response from the API includes a `cache_expires_in` field that shows how many seconds remain until the cache entry expires. This is visible in the browser developer tools (Network tab → `/weather?city=...` → Response).

---

## Supported Browsers

| Browser | Minimum Version |
|---------|----------------|
| Chrome / Chromium | 90+ |
| Firefox | 88+ |
| Safari | 14+ |
| Edge | 90+ |

The dashboard does not support Internet Explorer.

---

## Mobile

The layout is fully responsive. On screens narrower than 768 px, the forecast panels stack vertically. All features work identically on mobile.

---

## Frequently Asked Questions

**Q: Why does the temperature look wrong?**  
A: Data comes from OpenWeatherMap. If the temperature seems inaccurate, it may be due to the observation station nearest to the city you searched. Try a more specific location (e.g. include the country code).

**Q: Why does the same city show the same data for multiple searches?**  
A: The backend caches weather data for 15 minutes to reduce latency. The data is fresh as of your first search within that window.

**Q: Can I search by ZIP code or coordinates?**  
A: Currently only city names are supported.

**Q: Is the weather API key visible in the browser?**  
A: No. The API key is stored in AWS SSM Parameter Store and retrieved by the backend Lambda function at runtime. It never appears in any JavaScript file, response body, or browser network request.

**Q: How is my data used?**  
A: No personal data is collected. The only data sent to the server is the city name you search for. It is used solely to retrieve and cache weather data. No analytics, tracking, or cookies are used.

---

## Troubleshooting

**The page won't load**  
- Check your internet connection.
- Try a hard refresh: `Ctrl+Shift+R` (Windows/Linux) or `Cmd+Shift+R` (Mac).
- If the issue persists, the service may be temporarily unavailable. Check back in a few minutes.

**Search returns "City not found"**  
- Check spelling.
- Try adding a country code: `Springfield, US`.
- Try a different name for the city (e.g. `Köln` vs `Cologne`).

**Temperature toggle doesn't work**  
- Ensure JavaScript is enabled in your browser.
- Try a hard refresh.

**Forecast shows no data**  
- This can happen if OpenWeatherMap does not have forecast data for a very small locality. Search for the nearest major city instead.
