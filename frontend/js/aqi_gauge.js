/**
 * EPA Air Quality dial-gauge renderer.
 *
 * Ports the arc/dome/wedge geometry from the Python mockup generator used
 * to design and validate this widget (docs/mockups/air-quality-mockup-epa.html,
 * gen_gauge.py referenced in docs/WeatherApp-AirQuality-Plan-V1.md #4.6/#6).
 *
 * Built entirely with document.createElementNS + setAttribute, never
 * innerHTML - matches this app's security convention (see app.js's header
 * comment: "All DOM updates via textContent / setAttribute; never innerHTML").
 *
 * Category boundaries/colors here are a fixed EPA standard and must stay
 * in sync with backend/lambda/aqi_calculator.py's _CATEGORY_LABELS - both
 * sides encode the same six ranges independently since one is Python and
 * one is JS, not because the boundaries are expected to differ.
 */

const SVG_NS = 'http://www.w3.org/2000/svg';

const CX = 210;
const CY = 190;
const R_OUT = 170;
const R_IN = 128;
const DOME_R = R_IN * 0.65; // inner circle reduced 35% - leaves a white gap before the ring
const TICK_R = 180;
const BORDER = '#0f172a';
const VIEW_W = 420;
const VIEW_H = 205; // cropped tight to CY - nothing is ever drawn below it

// The 6 EPA AQI categories, low/good -> high/bad, each spanning an equal
// 30deg band across the 180deg sweep (matches the reference gauge design,
// not a value-proportional sweep).
const CATEGORIES = [
  { label: 'Good',                           aqiMin: 0,   aqiMax: 50,  angleStart: 180, angleEnd: 150, color: '#22c55e', badgeClass: 'good' },
  { label: 'Moderate',                       aqiMin: 51,  aqiMax: 100, angleStart: 150, angleEnd: 120, color: '#eab308', badgeClass: 'moderate' },
  { label: 'Unhealthy for Sensitive Groups', aqiMin: 101, aqiMax: 150, angleStart: 120, angleEnd: 90,  color: '#f97316', badgeClass: 'usg' },
  { label: 'Unhealthy',                      aqiMin: 151, aqiMax: 200, angleStart: 90,  angleEnd: 60,  color: '#ef4444', badgeClass: 'unhealthy' },
  { label: 'Very Unhealthy',                 aqiMin: 201, aqiMax: 300, angleStart: 60,  angleEnd: 30,  color: '#a855f7', badgeClass: 'very-unhealthy' },
  { label: 'Hazardous',                      aqiMin: 301, aqiMax: 500, angleStart: 30,  angleEnd: 0,   color: '#7f1d1d', badgeClass: 'hazardous' },
];

const TICKS = [
  { angle: 150, text: '50' },
  { angle: 120, text: '100' },
  { angle: 90, text: '150' },
  { angle: 60, text: '200' },
  { angle: 30, text: '300' },
];

function polar(cx, cy, r, angleDeg) {
  const a = (angleDeg * Math.PI) / 180;
  return [cx + r * Math.cos(a), cy - r * Math.sin(a)];
}

function pointsToPath(points, close) {
  const body = points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' L ');
  return close ? `M ${body} Z` : `M ${body}`;
}

function arcBandPath(cx, cy, rOut, rIn, angleStart, angleEnd, steps = 24) {
  const outerPts = [];
  const innerPts = [];
  for (let i = 0; i <= steps; i++) {
    const t = angleStart + (angleEnd - angleStart) * (i / steps);
    outerPts.push(polar(cx, cy, rOut, t));
    innerPts.push(polar(cx, cy, rIn, t));
  }
  innerPts.reverse();
  return pointsToPath(outerPts.concat(innerPts), true);
}

function domePath(cx, cy, r, steps = 48) {
  const pts = [];
  for (let i = 0; i <= steps; i++) {
    pts.push(polar(cx, cy, r, 180 - 180 * (i / steps)));
  }
  pts.push([cx, cy]);
  return pointsToPath(pts, true);
}

function wedgeMarkerPath(cx, cy, needleAngle, rTip, rBase, halfWidthDeg) {
  const tip = polar(cx, cy, rTip, needleAngle);
  const baseA = polar(cx, cy, rBase, needleAngle - halfWidthDeg);
  const baseB = polar(cx, cy, rBase, needleAngle + halfWidthDeg);
  return pointsToPath([tip, baseA, baseB], true);
}

function relativeLuminance(hex) {
  const clean = hex.replace('#', '');
  const r = parseInt(clean.slice(0, 2), 16);
  const g = parseInt(clean.slice(2, 4), 16);
  const b = parseInt(clean.slice(4, 6), 16);
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

function textColorFor(bgHex) {
  return relativeLuminance(bgHex) >= 150 ? '#0f172a' : '#ffffff';
}

/** Returns the CATEGORIES entry an AQI value falls in; clamps to Hazardous
 * for any out-of-range input rather than throwing. */
function categoryFor(aqi) {
  return CATEGORIES.find((c) => aqi >= c.aqiMin && aqi <= c.aqiMax)
    || CATEGORIES[CATEGORIES.length - 1];
}

function needleAngleFor(aqi, category) {
  const span = category.aqiMax - category.aqiMin;
  const frac = span === 0 ? 0 : (aqi - category.aqiMin) / span;
  return category.angleStart + (category.angleEnd - category.angleStart) * frac;
}

function svgEl(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, value));
  return el;
}

function svgText(attrs, content) {
  const el = svgEl('text', attrs);
  el.textContent = content;
  return el;
}

/**
 * Build the gauge as a real <svg> DOM element (never innerHTML).
 * `aqi` must be a US EPA AQI value, 0-500.
 */
function buildGaugeElement(aqi) {
  const category = categoryFor(aqi);
  const needleAngle = needleAngleFor(aqi, category);
  const textColor = textColorFor(category.color);
  const fontFamily = 'Inter, sans-serif';

  const svg = svgEl('svg', {
    viewBox: `0 0 ${VIEW_W} ${VIEW_H}`,
    width: String(VIEW_W),
    height: String(VIEW_H),
  });

  // Dome first, so the ring + wedge sit visually on top of it.
  svg.appendChild(svgEl('path', {
    d: domePath(CX, CY, DOME_R),
    fill: category.color,
    stroke: BORDER,
    'stroke-width': '2',
    'stroke-linejoin': 'round',
  }));

  // Color bands (ring) - fills only, no per-segment border.
  CATEGORIES.forEach((band) => {
    svg.appendChild(svgEl('path', {
      d: arcBandPath(CX, CY, R_OUT, R_IN, band.angleStart, band.angleEnd),
      fill: band.color,
    }));
  });

  // One clean outline traced around the whole ring, on top of the fills,
  // so there are no internal lines between color segments.
  svg.appendChild(svgEl('path', {
    d: arcBandPath(CX, CY, R_OUT, R_IN, 180, 0, 48),
    fill: 'none',
    stroke: BORDER,
    'stroke-width': '2',
    'stroke-linejoin': 'round',
  }));

  TICKS.forEach((tick) => {
    const [x, y] = polar(CX, CY, TICK_R, tick.angle);
    svg.appendChild(svgText({
      x: x.toFixed(1),
      y: y.toFixed(1),
      'text-anchor': 'middle',
      'dominant-baseline': 'middle',
      'font-size': '13',
      'font-weight': '600',
      fill: '#475569',
      'font-family': fontFamily,
    }, tick.text));
  });

  // Small black pyramid/wedge marker, in the white gap between the dome
  // and the ring, pointing at the value's angle.
  svg.appendChild(svgEl('path', {
    d: wedgeMarkerPath(CX, CY, needleAngle, R_IN - 4, DOME_R + 4, 7),
    fill: '#000000',
    stroke: '#000000',
    'stroke-width': '1',
    'stroke-linejoin': 'round',
  }));

  svg.appendChild(svgText({
    x: String(CX),
    y: String(CY - 35),
    'text-anchor': 'middle',
    'font-size': '30',
    'font-weight': '700',
    fill: textColor,
    'font-family': fontFamily,
  }, String(aqi)));

  // "Unhealthy for Sensitive Groups" is far longer than the other five
  // labels and clips against the SVG's own viewBox edge at the normal
  // font size - scale down for long labels rather than letting it clip.
  const labelFontSize = category.label.length > 20 ? '10' : '13';

  svg.appendChild(svgText({
    x: String(CX),
    y: String(CY - 12),
    'text-anchor': 'middle',
    'font-size': labelFontSize,
    'font-weight': '600',
    fill: textColor,
    'font-family': fontFamily,
  }, category.label));

  return svg;
}

function formatAqiTimestamp(unixSeconds) {
  return new Date(unixSeconds * 1000).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
    timeZoneName: 'short',
  });
}

/**
 * Build everything app.js's renderAirQuality() needs from a backend
 * epa_aqi payload: { aqi, category, dominant_pollutant, dt }.
 *
 * dominant_pollutant/dt come straight from the API response (not
 * derivable from the gauge geometry); the category label/badge class
 * are re-derived locally from the numeric aqi so they always agree with
 * exactly what the gauge itself draws.
 */
function renderAirQualityData(epaAqi) {
  const category = categoryFor(epaAqi.aqi);
  return {
    svgElement: buildGaugeElement(epaAqi.aqi),
    pollutantText: `Pollutant: ${epaAqi.dominant_pollutant}`,
    timestampText: `Updated ${formatAqiTimestamp(epaAqi.dt)}`,
    badgeLabel: category.label,
    badgeClass: `aqi-badge--${category.badgeClass}`,
  };
}

export { CATEGORIES, buildGaugeElement, categoryFor, renderAirQualityData };
