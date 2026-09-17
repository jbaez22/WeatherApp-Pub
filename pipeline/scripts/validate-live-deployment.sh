#!/usr/bin/env bash
# validate-live-deployment.sh — post-deploy validation that bypasses both
# caching layers this project has, so a deploy is confirmed against LIVE
# data, not a cached copy that happens to still look healthy.
#
# Genesis: docs/WeatherApp-BrowserCacheMemory-Details-V1.md section 7. The
# 2026-07-24 CSP-drift incident deployed cleanly, and BOTH the automated
# curl check and a manual browser check said "it works" — one because curl
# has no CSP engine, the other because the browser was serving a 24h-cached
# copy of config.js that still matched the (also-stale) CSP. This script is
# the automated version of that doc's section 7.4 recipe.
#
# What this checks (two independent cache layers):
#   1. Static-asset / CSP consistency (the browser/CDN cache layer) —
#      after confirming the CloudFront invalidation from this deploy has
#      actually completed, asserts config.js's API_BASE_URL host and
#      index.html's CSP connect-src name the SAME host. This is a
#      deterministic proxy for "would a real browser's CSP block this,"
#      not a substitute for one — curl cannot enforce CSP itself. See the
#      LIMITATION note below.
#   2. Live OpenWeatherMap confirmation (the DynamoDB 15-min cache layer) —
#      calls a city unlikely to have been queried in the last 15 minutes
#      and confirms via CloudWatch Logs that the Lambda actually reached
#      OpenWeatherMap (logger line "Fetching One Call 3.0 data for <city>"
#      only appears on a genuine cache-miss path), not just that DynamoDB
#      returned a plausible-looking cached response.
#
# LIMITATION (documented, not silently glossed over): this script has no
# real browser engine, so it cannot detect a live browser blocking a
# request via CSP the way a user's browser would — only check #1's static
# text comparison catches that class of bug here. A true browser-context
# check needs a headless-browser smoke test (Playwright/Puppeteer) — see
# docs/WeatherApp-BrowserCacheMemory-Details-V1.md section 6.1. That is a
# larger, separate change (new Node/browser-binary dependency in the
# pipeline) and is intentionally NOT part of this script.
#
# Required environment variables (injected by CodeBuild project, or export
# manually to run locally):
#   FRONTEND_DOMAIN            — e.g. weather.craftingnewtech.com
#   API_CUSTOM_DOMAIN           — e.g. https://api.weather.craftingnewtech.com
#   CLOUDFRONT_DISTRIBUTION_ID  — the distribution this deploy just invalidated
#   LAMBDA_FUNCTION_NAME        — e.g. weather-dashboard-handler-production
#
# Manual run:
#   export FRONTEND_DOMAIN=weather.craftingnewtech.com
#   export API_CUSTOM_DOMAIN=https://api.weather.craftingnewtech.com
#   export CLOUDFRONT_DISTRIBUTION_ID=E2JTYSEFHNW8MQ
#   export LAMBDA_FUNCTION_NAME=weather-dashboard-handler-production
#   bash pipeline/scripts/validate-live-deployment.sh

set -uo pipefail   # -e intentionally omitted — we collect results before exiting

: "${FRONTEND_DOMAIN:?Set FRONTEND_DOMAIN (e.g. weather.craftingnewtech.com)}"
: "${API_CUSTOM_DOMAIN:?Set API_CUSTOM_DOMAIN (e.g. https://api.weather.craftingnewtech.com)}"
: "${CLOUDFRONT_DISTRIBUTION_ID:?Set CLOUDFRONT_DISTRIBUTION_ID}"
: "${LAMBDA_FUNCTION_NAME:?Set LAMBDA_FUNCTION_NAME}"

# A handful of real, low-traffic cities to rotate through so consecutive
# pipeline runs don't keep re-hitting the same 15-minute DynamoDB cache
# entry. Picking by the current minute means back-to-back runs within the
# same minute could collide — acceptable, since step 2 below tries the
# next candidate on a cache hit rather than treating that as fatal.
CITY_POOL=("Reykjavik" "Ulaanbaatar" "Timbuktu" "Vaduz" "Bhutan" "Nuuk" "Apia" "Suva")

INVALIDATION_WAIT_MAX_SECONDS=120
INVALIDATION_POLL_INTERVAL=5
LOG_POLL_MAX_SECONDS=30
LOG_POLL_INTERVAL=3

PASS=0
FAIL=0

check() {
  local label="$1"; local result="$2"
  if [ "$result" = "PASS" ]; then
    echo "  [PASS] $label"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] $label — $result"
    FAIL=$((FAIL + 1))
  fi
}

echo "============================================================"
echo " Live Deployment Validation — cache-bypassed"
echo "============================================================"
echo " Frontend      : https://$FRONTEND_DOMAIN"
echo " API           : $API_CUSTOM_DOMAIN"
echo " CloudFront    : $CLOUDFRONT_DISTRIBUTION_ID"
echo " Lambda        : $LAMBDA_FUNCTION_NAME"
echo ""

# ── Step 0 — confirm the CDN cache is actually flushed ───────────────────────
# There is no way to bypass CloudFront by going around it — S3 direct access
# is blocked by design (Origin Access Control). The correct way to guarantee
# fresh-from-origin content is to confirm THIS deploy's invalidation reached
# Completed before trusting any content check below.
echo "--- 0. CloudFront invalidation status (CDN cache bypass) ---"

# --no-paginate is required here, not optional: the AWS CLI auto-paginates
# list-invalidations across multiple API calls even with --max-items 1, and
# applies --query to EACH page separately rather than to the final
# combined result. If a later (empty) page exists, its Items[0].Id
# evaluates to null and prints "None" on its own line, concatenated onto
# the real ID from the first page — corrupting the variable into two
# lines instead of one. --no-paginate forces a single API call so the
# query only ever runs once. Confirmed live 2026-07-26: without this flag,
# the command printed "I1FKJNVVN6C0053BN7TZGTBS6S\nNone" for a real,
# valid invalidation ID.
LATEST_INVALIDATION_ID=$(aws cloudfront list-invalidations \
  --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
  --max-items 1 \
  --no-paginate \
  --query 'InvalidationList.Items[0].Id' \
  --output text 2>/dev/null)

if [ -z "$LATEST_INVALIDATION_ID" ] || [ "$LATEST_INVALIDATION_ID" = "None" ]; then
  check "Found a CloudFront invalidation to wait on" "No invalidations found for $CLOUDFRONT_DISTRIBUTION_ID"
else
  ELAPSED=0
  STATUS=""
  while [ "$ELAPSED" -lt "$INVALIDATION_WAIT_MAX_SECONDS" ]; do
    STATUS=$(aws cloudfront get-invalidation \
      --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
      --id "$LATEST_INVALIDATION_ID" \
      --query 'Invalidation.Status' \
      --output text 2>/dev/null)
    [ "$STATUS" = "Completed" ] && break
    sleep "$INVALIDATION_POLL_INTERVAL"
    ELAPSED=$((ELAPSED + INVALIDATION_POLL_INTERVAL))
  done
  [ "$STATUS" = "Completed" ] \
    && check "Invalidation $LATEST_INVALIDATION_ID completed (waited ${ELAPSED}s)" "PASS" \
    || check "Invalidation $LATEST_INVALIDATION_ID reached Completed within ${INVALIDATION_WAIT_MAX_SECONDS}s" "Status: $STATUS"
fi
echo ""

# ── Step 1 — static-asset / CSP consistency (browser cache layer) ───────────
# curl never caches between invocations, so every request here is already
# "cache-bypassed" from the client's perspective. What curl CANNOT do is
# enforce CSP the way a browser does — this check instead verifies the
# same underlying fact statically: does config.js's API_BASE_URL name a
# host that index.html's CSP connect-src actually allows? A real browser
# blocks the fetch() the moment these two disagree; this is the
# deterministic proxy for that without a browser engine.
echo "--- 1. Static config / CSP consistency (browser cache layer) ---"

CONFIG_JS=$(curl -sS --max-time 10 "https://${FRONTEND_DOMAIN}/js/config.js")
INDEX_HTML=$(curl -sS --max-time 10 "https://${FRONTEND_DOMAIN}/")

API_BASE_HOST=$(echo "$CONFIG_JS" \
  | grep -oE "API_BASE_URL:\s*['\"]https?://[^'\"]+" \
  | grep -oE "https?://[^'\"]+" \
  | sed -E 's#https?://##')

if [ -z "$API_BASE_HOST" ]; then
  check "Extracted API_BASE_URL host from config.js" "Could not find API_BASE_URL in config.js"
else
  check "Extracted API_BASE_URL host from config.js ($API_BASE_HOST)" "PASS"

  CONNECT_SRC_LINE=$(echo "$INDEX_HTML" | grep -oE "connect-src[^;]*;")
  echo "$CONNECT_SRC_LINE" | grep -qF "$API_BASE_HOST" \
    && check "CSP connect-src allows API_BASE_URL host ($API_BASE_HOST)" "PASS" \
    || check "CSP connect-src allows API_BASE_URL host ($API_BASE_HOST)" "connect-src is: ${CONNECT_SRC_LINE:-<not found>}"
fi
echo ""

# ── Step 2 — live OpenWeatherMap confirmation (DynamoDB cache layer) ────────
# The Lambda has no cache-bypass parameter, so the only way to force a real
# upstream call is to request a city unlikely to have a live (< 15 min)
# DynamoDB entry. "Fetching One Call 3.0 data for <city>" only appears in
# the Lambda logs on an actual cache-miss path to OpenWeatherMap — a
# fast/plausible-looking JSON response alone does not prove that happened.
echo "--- 2. Live OpenWeatherMap confirmation (DynamoDB cache layer) ---"

LOG_GROUP="/aws/lambda/${LAMBDA_FUNCTION_NAME}"
LIVE_FETCH_CONFIRMED="false"
TRIED_CITIES=()

for CITY in "${CITY_POOL[@]}"; do
  TRIED_CITIES+=("$CITY")
  REQUEST_START_EPOCH_MS=$(( $(date +%s) * 1000 - 5000 ))  # 5s lookback margin

  HTTP_CODE=$(curl -sS --max-time 10 -o /tmp/live_weather_resp.json -w "%{http_code}" \
    "${API_CUSTOM_DOMAIN}/weather?city=${CITY}")

  if [ "$HTTP_CODE" != "200" ]; then
    echo "    ${CITY}: HTTP $HTTP_CODE, trying next candidate"
    continue
  fi

  # validators.py's validate_city() lowercases the city before it's ever
  # passed to fetch_weather() (backend/lambda/validators.py:47), so the
  # Lambda's own log line is always lowercase regardless of the request's
  # casing — e.g. "Fetching One Call 3.0 data for reykjavik", never
  # "...Reykjavik". CloudWatch Logs filter patterns are case-sensitive
  # substring matches, so the filter must search on the same lowercased
  # form or it silently never matches, even on a genuine cache miss.
  CITY_LOWER=$(echo "$CITY" | tr '[:upper:]' '[:lower:]')

  ELAPSED=0
  FOUND_FETCH_LINE=""
  while [ "$ELAPSED" -lt "$LOG_POLL_MAX_SECONDS" ]; do
    FOUND_FETCH_LINE=$(aws logs filter-log-events \
      --log-group-name "$LOG_GROUP" \
      --start-time "$REQUEST_START_EPOCH_MS" \
      --filter-pattern "\"Fetching One Call 3.0 data for ${CITY_LOWER}\"" \
      --query 'events[0].message' \
      --output text 2>/dev/null)
    [ -n "$FOUND_FETCH_LINE" ] && [ "$FOUND_FETCH_LINE" != "None" ] && break
    sleep "$LOG_POLL_INTERVAL"
    ELAPSED=$((ELAPSED + LOG_POLL_INTERVAL))
  done

  if [ -n "$FOUND_FETCH_LINE" ] && [ "$FOUND_FETCH_LINE" != "None" ]; then
    check "Live OpenWeatherMap fetch confirmed for '$CITY' (HTTP 200 + Lambda log match)" "PASS"
    LIVE_FETCH_CONFIRMED="true"
    break
  else
    echo "    ${CITY}: HTTP 200 but no 'Fetching One Call 3.0' log line within ${LOG_POLL_MAX_SECONDS}s — likely a cache hit, trying next candidate"
  fi
done

if [ "$LIVE_FETCH_CONFIRMED" != "true" ]; then
  check "Live OpenWeatherMap fetch confirmed for at least one candidate city" \
    "All candidates (${TRIED_CITIES[*]}) returned cache hits or failed — could not confirm a genuine upstream call"
fi
echo ""

# ── Summary ──────────────────────────────────────────────────────────────────
TOTAL=$((PASS + FAIL))
echo "============================================================"
echo " Results: $PASS/$TOTAL passed"
if [ "$FAIL" -gt 0 ]; then
  echo " $FAIL check(s) FAILED — review the [FAIL] lines above."
  echo "============================================================"
  exit 1
else
  echo " All checks passed — deploy verified against live, cache-bypassed data."
  echo "============================================================"
  exit 0
fi
