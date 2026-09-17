/**
 * Weather App — main application script
 *
 * Security:
 *  - Input validated with whitelist regex before any API call.
 *  - All DOM updates via textContent / setAttribute; never innerHTML.
 *  - API key lives in Lambda + SSM Parameter Store; never in this file.
 *  - CSP meta tag in index.html restricts script/style/connect sources.
 */

import CONFIG from './config.js';
import { CATEGORIES as AQI_CATEGORIES, renderAirQualityData } from './aqi_gauge.js';

/* ── Constants ──────────────────────────────────────────────────────────── */
const CITY_REGEX  = /^[a-zA-Z\s\-\.]{1,100}$/;
const ICON_BASE   = 'https://openweathermap.org/img/wn/';
const LOCAL_ICONS = new Set([
  '01d','01n','02d','02n','03d','03n','04d','04n',
  '09d','09n','10d','10n','11d','11n','13d','13n','50d','50n',
]);

/* ── State ──────────────────────────────────────────────────────────────── */
let currentUnit      = 'F';   // 'C' | 'F'
let currentWeather   = null;  // raw Celsius data from API
let forecastData     = [];    // V2 daily array (7 entries, Celsius)
let hourlyData       = [];    // V2 hourly array (48 entries, Celsius)
let expandedDayIndex = null;  // index of the currently open forecast card

/* ── DOM refs ────────────────────────────────────────────────────────────── */
const form          = document.getElementById('search-form');
const cityInput     = document.getElementById('city-input');
const locateBtn     = document.getElementById('locate-btn');
const loadingEl     = document.getElementById('loading');
const errorCard     = document.getElementById('error-card');
const errorMsg      = document.getElementById('error-message');
const weatherCard   = document.getElementById('weather-card');
const forecastSec   = document.getElementById('forecast-section');
const forecastGrid  = document.getElementById('forecast-grid');
const btnCelsius    = document.getElementById('btn-celsius');
const btnFahrenheit = document.getElementById('btn-fahrenheit');
const hourlyStrip   = document.getElementById('hourly-strip');
const aqiSec        = document.getElementById('aqi-section');
const aqiToggle     = document.getElementById('aqi-toggle');
const aqiBody       = document.getElementById('aqi-body');
const aqiGaugeMount = document.getElementById('aqi-gauge-mount');
const aqiLocation   = document.getElementById('aqi-location');
const aqiBadge      = document.getElementById('aqi-summary-badge');
const aqiBadgeLabel = document.getElementById('aqi-summary-label');
const aqiPollutant  = document.getElementById('aqi-pollutant');
const aqiTimestamp  = document.getElementById('aqi-timestamp');

const AQI_BADGE_CLASSES = AQI_CATEGORIES.map((c) => `aqi-badge--${c.badgeClass}`);

/* ── Utility ─────────────────────────────────────────────────────────────── */
function cToF(c) { return Math.round(c * 9 / 5 + 32); }

function formatTemp(celsius) {
  const value = currentUnit === 'C' ? Math.round(celsius) : cToF(celsius);
  return value.toString();
}

function formatDate(unixSeconds) {
  const d = new Date(unixSeconds * 1000);
  const date = d.toLocaleDateString('en-US', {
    weekday: 'long', year: 'numeric', month: 'short', day: '2-digit',
  });
  const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
  return `${date} · ${time}`;
}

function iconUrl(code) {
  if (LOCAL_ICONS.has(code)) return `/assets/icons/${code}.svg`;
  return `${ICON_BASE}${code}@2x.png`;
}

function shortDay(unixSeconds) {
  return new Date(unixSeconds * 1000).toLocaleDateString('en-US', { weekday: 'short' });
}

function windDir(deg) {
  const dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  return dirs[Math.round(deg / 45) % 8];
}

/* ── Visibility helpers ──────────────────────────────────────────────────── */
function show(el) { el.hidden = false; }
function hide(el) { el.hidden = true;  }

function setLoading(active) {
  if (active) {
    show(loadingEl);
    hide(errorCard);
    hide(weatherCard);
    hide(forecastSec);
    hide(aqiSec);
  } else {
    hide(loadingEl);
  }
}

function showError(message) {
  errorMsg.textContent = message;
  show(errorCard);
  hide(weatherCard);
  hide(forecastSec);
  hide(aqiSec);
}

/* ── Input validation ────────────────────────────────────────────────────── */
function validateCity(raw) {
  const trimmed = raw.trim();
  if (!trimmed) return { ok: false, reason: 'Please enter a city name.' };
  if (!CITY_REGEX.test(trimmed)) {
    return {
      ok: false,
      reason: 'City name may only contain letters, spaces, hyphens, and periods.',
    };
  }
  return { ok: true, value: trimmed };
}

/* ── API call ────────────────────────────────────────────────────────────── */
async function fetchWeather(city) {
  const url = new URL(`${CONFIG.API_BASE_URL}/weather`);
  url.searchParams.set('city', city);

  const resp = await fetch(url.toString(), {
    method:  'GET',
    headers: { 'Content-Type': 'application/json' },
  });

  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      if (body && body.message) detail = body.message;
    } catch { /* response body may not be JSON */ }
    throw new Error(detail);
  }

  return resp.json();
}

/* ── Render current weather ──────────────────────────────────────────────── */
function renderCurrent(data) {
  document.getElementById('city-name').textContent    = data.name + ', ' + data.sys.country;
  document.getElementById('weather-date').textContent = formatDate(data.dt);
  document.getElementById('current-temp').textContent = formatTemp(data.main.temp);
  document.getElementById('current-unit').textContent = currentUnit;
  document.getElementById('weather-desc').textContent = data.weather[0].description;
  document.getElementById('feels-like').textContent   = formatTemp(data.main.feels_like) + '°' + currentUnit;
  document.getElementById('humidity').textContent     = data.main.humidity + '%';
  document.getElementById('wind-speed').textContent   = Math.round(data.wind.speed * 3.6) + ' km/h';
  document.getElementById('pressure').textContent     = data.main.pressure + ' hPa';

  const visKm = data.visibility != null
    ? (data.visibility / 1000).toFixed(1) + ' km'
    : 'N/A';
  document.getElementById('visibility').textContent = visKm;

  const pop = forecastData[0]?.pop ?? 0;
  document.getElementById('precipitation').textContent = Math.round(pop * 100) + '%';

  const icon = document.getElementById('weather-icon');
  icon.src = iconUrl(data.weather[0].icon);
  icon.alt = data.weather[0].description;
}

/* ── Render forecast grid ────────────────────────────────────────────────── */
function renderForecast() {
  while (forecastGrid.firstChild) {
    forecastGrid.removeChild(forecastGrid.firstChild);
  }

  forecastData.forEach((day, i) => {
    const card = document.createElement('div');
    card.className = 'forecast-card';
    card.setAttribute('role', 'button');
    card.setAttribute('tabindex', '0');
    card.setAttribute('aria-expanded', 'false');
    card.setAttribute('aria-controls', 'hourly-strip');
    card.dataset.dayIndex = i;

    // Header row: day name + chevron
    const headerEl = document.createElement('div');
    headerEl.className = 'forecast-card__header';

    const dayEl = document.createElement('p');
    dayEl.className = 'forecast-card__day';
    dayEl.textContent = shortDay(day.dt);

    const chevron = document.createElement('span');
    chevron.className = 'forecast-card__chevron';
    chevron.setAttribute('aria-hidden', 'true');

    headerEl.appendChild(dayEl);
    headerEl.appendChild(chevron);

    const img = document.createElement('img');
    img.className = 'forecast-card__icon';
    img.src    = iconUrl(day.icon);
    img.alt    = day.desc;
    img.width  = 52;
    img.height = 52;

    const tempEl = document.createElement('p');
    tempEl.className = 'forecast-card__temp';
    const highSpan = document.createElement('span');
    highSpan.className = 'forecast-high';
    highSpan.textContent = `${formatTemp(day.high)}°`;
    const lowSpan = document.createElement('span');
    lowSpan.className = 'forecast-low';
    lowSpan.textContent = ` / ${formatTemp(day.low)}°`;
    tempEl.appendChild(highSpan);
    tempEl.appendChild(lowSpan);

    const descEl = document.createElement('p');
    descEl.className = 'forecast-card__desc';
    descEl.textContent = day.desc;

    const metaEl = document.createElement('p');
    metaEl.className = 'forecast-card__meta';
    metaEl.textContent = `${Math.round(day.pop * 100)}% · UV ${Math.round(day.uvi)}`;

    card.appendChild(headerEl);
    card.appendChild(img);
    card.appendChild(tempEl);
    card.appendChild(descEl);
    card.appendChild(metaEl);

    forecastGrid.appendChild(card);
  });
}

/* ── Render air quality (EPA gauge widget) ───────────────────────────────── */
function hideAirQuality() {
  hide(aqiSec);
}

function renderAirQuality(epaAqi, locationText) {
  const rendered = renderAirQualityData(epaAqi);

  aqiLocation.textContent = locationText;

  aqiBadge.classList.remove(...AQI_BADGE_CLASSES);
  aqiBadge.classList.add(rendered.badgeClass);
  aqiBadgeLabel.textContent = rendered.badgeLabel;

  while (aqiGaugeMount.firstChild) {
    aqiGaugeMount.removeChild(aqiGaugeMount.firstChild);
  }
  aqiGaugeMount.appendChild(rendered.svgElement);

  aqiPollutant.textContent = rendered.pollutantText;
  aqiTimestamp.textContent = rendered.timestampText;

  show(aqiSec);
}

/* ── Accordion helpers ───────────────────────────────────────────────────── */
function collapseCard(card) {
  card.classList.remove('forecast-card--expanded');
  card.setAttribute('aria-expanded', 'false');
  hourlyStrip.hidden = true;
}

function expandCard(card, index) {
  card.classList.add('forecast-card--expanded');
  card.setAttribute('aria-expanded', 'true');
  renderHourly(index, forecastData[index].dt);
  hourlyStrip.hidden = false;
}

function toggleCard(index) {
  const cards = forecastGrid.querySelectorAll('.forecast-card');
  if (expandedDayIndex === index) {
    collapseCard(cards[index]);
    expandedDayIndex = null;
    return;
  }
  if (expandedDayIndex !== null && cards[expandedDayIndex]) {
    collapseCard(cards[expandedDayIndex]);
  }
  expandedDayIndex = index;
  expandCard(cards[index], index);
}

/* ── Render hourly strip (WU-style table) ────────────────────────────────── */
function renderHourly(dayIndex, dayDt) {
  while (hourlyStrip.firstChild) hourlyStrip.removeChild(hourlyStrip.firstChild);

  const labelEl = document.createElement('p');
  labelEl.className = 'hourly-strip__label';
  const fullDay = new Date(forecastData[dayIndex].dt * 1000)
    .toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
  labelEl.textContent = `Hourly Forecast — ${fullDay}`;
  hourlyStrip.appendChild(labelEl);

  const dayStr = new Date(dayDt * 1000).toDateString();
  const hours  = hourlyData.filter((h) => new Date(h.dt * 1000).toDateString() === dayStr);

  if (hours.length === 0) {
    const emptyEl = document.createElement('p');
    emptyEl.className = 'hourly-strip__empty';
    emptyEl.textContent = 'No hourly data for this day — hourly coverage spans 48 hours from now.';
    hourlyStrip.appendChild(emptyEl);
    return;
  }

  const wrapEl = document.createElement('div');
  wrapEl.className = 'hourly-table-wrap';

  const table = document.createElement('table');
  table.className = 'hourly-table';
  table.setAttribute('role', 'table');

  // ── thead ──
  const thead = document.createElement('thead');
  const headerRow = document.createElement('tr');
  ['Time', 'Conditions', `Temp (°${currentUnit})`, `Feels Like`, 'Humidity', 'Wind', 'Precip', 'Amount', 'Pressure'].forEach((text) => {
    const th = document.createElement('th');
    th.scope = 'col';
    th.textContent = text;
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  // ── tbody ──
  const tbody = document.createElement('tbody');
  hours.forEach((hour) => {
    const tr = document.createElement('tr');

    // Time
    const tdTime = document.createElement('td');
    tdTime.className = 'ht__time';
    tdTime.textContent = new Date(hour.dt * 1000).toLocaleTimeString('en-US', {
      hour: 'numeric', hour12: true,
    });
    tr.appendChild(tdTime);

    // Conditions — icon + description
    const tdCond = document.createElement('td');
    const condDiv = document.createElement('div');
    condDiv.className = 'ht__cond';
    const img = document.createElement('img');
    img.className = 'ht__icon';
    img.src    = iconUrl(hour.icon);
    img.alt    = hour.desc;
    img.width  = 40;
    img.height = 40;
    const descSpan = document.createElement('span');
    descSpan.className = 'ht__desc';
    descSpan.textContent = hour.desc;
    condDiv.appendChild(img);
    condDiv.appendChild(descSpan);
    tdCond.appendChild(condDiv);
    tr.appendChild(tdCond);

    // Temp
    const tdTemp = document.createElement('td');
    tdTemp.className = 'ht__temp';
    tdTemp.textContent = formatTemp(hour.temp) + '°';
    tr.appendChild(tdTemp);

    // Feels Like
    const tdFeel = document.createElement('td');
    tdFeel.className = 'ht__feel';
    tdFeel.textContent = formatTemp(hour.feels_like) + '°';
    tr.appendChild(tdFeel);

    // Humidity
    const tdHum = document.createElement('td');
    tdHum.className = 'ht__humidity';
    tdHum.textContent = (hour.humidity ?? '—') + (hour.humidity != null ? '%' : '');
    tr.appendChild(tdHum);

    // Wind — compass direction + speed in km/h
    const tdWind = document.createElement('td');
    tdWind.className = 'ht__wind';
    if (hour.wind_speed != null) {
      const dir   = windDir(hour.wind_deg ?? 0);
      const kmh   = Math.round(hour.wind_speed * 3.6);
      tdWind.textContent = `${dir} ${kmh} km/h`;
    } else {
      tdWind.textContent = '—';
    }
    tr.appendChild(tdWind);

    // Precip chance
    const tdPop = document.createElement('td');
    tdPop.className = 'ht__pop';
    tdPop.textContent = Math.round(hour.pop * 100) + '%';
    tr.appendChild(tdPop);

    // Precip amount
    const tdPrecip = document.createElement('td');
    tdPrecip.className = 'ht__precip';
    tdPrecip.textContent = hour.precip_mm != null
      ? (hour.precip_mm > 0 ? hour.precip_mm.toFixed(1) + ' mm' : '0 mm')
      : '—';
    tr.appendChild(tdPrecip);

    // Pressure
    const tdPres = document.createElement('td');
    tdPres.className = 'ht__pressure';
    tdPres.textContent = hour.pressure != null ? hour.pressure + ' hPa' : '—';
    tr.appendChild(tdPres);

    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrapEl.appendChild(table);
  hourlyStrip.appendChild(wrapEl);
}

/* ── Main fetch + render flow ────────────────────────────────────────────── */
async function loadWeather(city) {
  setLoading(true);
  try {
    const data = await fetchWeather(city);

    currentWeather     = data.current;
    forecastData       = Array.isArray(data.daily)  ? data.daily  : [];
    hourlyData         = Array.isArray(data.hourly) ? data.hourly : [];
    expandedDayIndex   = null;
    hourlyStrip.hidden = true;

    setLoading(false);
    hide(errorCard);
    renderCurrent(currentWeather);
    show(weatherCard);

    if (forecastData.length > 0) {
      document.getElementById('forecast-title').textContent = '7-Day Forecast';
      renderForecast();
      show(forecastSec);
    }

    // Non-fatal by design (see backend/lambda/weather_client.py) - epa_aqi
    // is simply absent from the response if the air-quality fetch/calc
    // failed, and the rest of the page above is entirely unaffected.
    if (data.epa_aqi) {
      const locationText = `${currentWeather.name}, ${currentWeather.sys.country}`;
      renderAirQuality(data.epa_aqi, locationText);
    } else {
      hideAirQuality();
    }
  } catch (err) {
    setLoading(false);
    const isNetworkErr = err instanceof TypeError;
    if (isNetworkErr) {
      showError('Unable to reach the weather service. Check your connection and try again.');
    } else if (err.message === 'HTTP 404' || err.message.toLowerCase().includes('not found')) {
      showError('City not found. Please check the spelling and try again.');
    } else if (err.message === 'HTTP 429') {
      showError('Too many requests. Please wait a moment and try again.');
    } else {
      showError('Something went wrong. Please try again shortly.');
    }
  }
}

/* ── Unit toggle ─────────────────────────────────────────────────────────── */
function setUnit(unit) {
  if (currentUnit === unit) return;
  currentUnit = unit;

  btnCelsius.classList.toggle('unit-btn--active',    unit === 'C');
  btnFahrenheit.classList.toggle('unit-btn--active', unit === 'F');
  btnCelsius.setAttribute('aria-pressed',    unit === 'C' ? 'true' : 'false');
  btnFahrenheit.setAttribute('aria-pressed', unit === 'F' ? 'true' : 'false');

  if (currentWeather) renderCurrent(currentWeather);
  if (forecastData.length > 0) {
    const savedIndex = expandedDayIndex;
    expandedDayIndex = null;
    renderForecast();
    if (savedIndex !== null) {
      const cards = forecastGrid.querySelectorAll('.forecast-card');
      if (cards[savedIndex]) {
        expandCard(cards[savedIndex], savedIndex);
        expandedDayIndex = savedIndex;
      }
    }
  }
}

/* ── Event listeners ─────────────────────────────────────────────────────── */
form.addEventListener('submit', (e) => {
  e.preventDefault();
  cityInput.classList.remove('is-invalid');

  const validation = validateCity(cityInput.value);
  if (!validation.ok) {
    cityInput.classList.add('is-invalid');
    cityInput.setAttribute('aria-invalid', 'true');
    showError(validation.reason);
    return;
  }

  cityInput.setAttribute('aria-invalid', 'false');
  hide(errorCard);
  loadWeather(validation.value);
});

cityInput.addEventListener('input', () => {
  cityInput.classList.remove('is-invalid');
  cityInput.removeAttribute('aria-invalid');
});

btnCelsius.addEventListener('click',    () => setUnit('C'));
btnFahrenheit.addEventListener('click', () => setUnit('F'));

aqiToggle.addEventListener('click', () => {
  const expanded = aqiToggle.getAttribute('aria-expanded') === 'true';
  aqiToggle.setAttribute('aria-expanded', String(!expanded));
  aqiBody.hidden = expanded;
});

/* ── Geolocation ─────────────────────────────────────────────────────────── */
locateBtn.addEventListener('click', () => {
  if (!navigator.geolocation) {
    showError("Your browser doesn't support location detection.");
    return;
  }

  locateBtn.classList.add('locate-btn--loading');
  locateBtn.disabled = true;

  navigator.geolocation.getCurrentPosition(
    async (pos) => {
      try {
        const { latitude: lat, longitude: lon } = pos.coords;
        const url = new URL(`${CONFIG.API_BASE_URL}/locate`);
        url.searchParams.set('lat', lat.toFixed(6));
        url.searchParams.set('lon', lon.toFixed(6));

        const resp = await fetch(url.toString(), {
          method: 'GET',
          headers: { 'Content-Type': 'application/json' },
        });

        if (!resp.ok) {
          const body = await resp.json().catch(() => ({}));
          const msg = body.message || `HTTP ${resp.status}`;
          if (resp.status === 404) {
            showError("Couldn't identify a city at your location.");
          } else {
            showError(`Location service error: ${msg}`);
          }
          return;
        }

        const { city } = await resp.json();
        const cityName = city.split(',')[0].trim();
        cityInput.value = cityName;
        cityInput.classList.remove('is-invalid');
        cityInput.removeAttribute('aria-invalid');
        hide(errorCard);
        loadWeather(cityName);
      } catch {
        showError('Location service is unavailable. Enter a city manually.');
      } finally {
        locateBtn.classList.remove('locate-btn--loading');
        locateBtn.disabled = false;
      }
    },
    (err) => {
      locateBtn.classList.remove('locate-btn--loading');
      locateBtn.disabled = false;
      const messages = {
        1: 'Location access was denied. Enter a city manually.',
        2: 'Your location could not be determined. Enter a city manually.',
        3: 'Location request timed out. Enter a city manually.',
      };
      showError(messages[err.code] || 'Location unavailable. Enter a city manually.');
    },
    { timeout: 10000, maximumAge: 60000 },
  );
});

// Expand/collapse forecast cards — event delegation.
forecastGrid.addEventListener('click', (e) => {
  const card = e.target.closest('.forecast-card');
  if (!card) return;
  toggleCard(Number(card.dataset.dayIndex));
});

forecastGrid.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const card = e.target.closest('.forecast-card');
  if (!card) return;
  e.preventDefault();
  toggleCard(Number(card.dataset.dayIndex));
});

/* ── Auto-load default city ─────────────────────────────────────────────── */
cityInput.value = 'New York';
loadWeather('New York');
