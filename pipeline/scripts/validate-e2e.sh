#!/usr/bin/env bash
# validate-e2e.sh — End-to-end validation of the live Weather Dashboard.
#
# Runs after a successful bootstrap and first pipeline execution.
# Checks every layer: CloudFront, API Gateway, S3 access control,
# HTTPS enforcement, security headers, and caching behaviour.
#
# Usage:
#   export CLOUDFRONT_DOMAIN=d1abc123def456.cloudfront.net
#   export API_ENDPOINT=https://abc123def.execute-api.us-east-1.amazonaws.com
#   export WEBSITE_BUCKET=weather-dashboard-website-<account-id>-production
#   export STACK_NAME=weather-dashboard-master-production
#   export LAMBDA_FUNCTION_NAME=weather-dashboard-handler-production
#   bash pipeline/scripts/validate-e2e.sh
#
# Or source your environment first:
#   source <(aws cloudformation describe-stacks --stack-name $STACK_NAME \
#     --query 'Stacks[0].Outputs' --output json | \
#     python3 -c "import sys,json; [print(f'export {o[\"OutputKey\"]}=\"{o[\"OutputValue\"]}\"') for o in json.load(sys.stdin)]")

set -uo pipefail   # -e intentionally omitted — we collect results before exiting

# ── Required environment variables ───────────────────────────────────────────
: "${CLOUDFRONT_DOMAIN:?Set CLOUDFRONT_DOMAIN (e.g. d1abc.cloudfront.net)}"
: "${API_ENDPOINT:?Set API_ENDPOINT (e.g. https://abc.execute-api.us-east-1.amazonaws.com)}"
: "${WEBSITE_BUCKET:?Set WEBSITE_BUCKET (the S3 website bucket name)}"
: "${LAMBDA_FUNCTION_NAME:?Set LAMBDA_FUNCTION_NAME}"

DOMAIN="${DOMAIN:-weather.craftingnewtech.com}"
TEST_CITY="London"
PASS=0
FAIL=0

# ── Helpers ───────────────────────────────────────────────────────────────────
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

http_code() { curl -s -o /dev/null -w "%{http_code}" "$@"; }

echo "============================================================"
echo " Weather Dashboard — End-to-End Validation"
echo "============================================================"
echo " CloudFront : https://$CLOUDFRONT_DOMAIN"
echo " API        : $API_ENDPOINT"
echo " S3 bucket  : $WEBSITE_BUCKET"
echo " Lambda     : $LAMBDA_FUNCTION_NAME"
echo ""

# ── 1. Functional checks ─────────────────────────────────────────────────────
echo "--- 1. Functional ---"

CODE=$(http_code "https://$CLOUDFRONT_DOMAIN")
[ "$CODE" = "200" ] && check "CloudFront home page returns 200" "PASS" \
  || check "CloudFront home page returns 200" "Got HTTP $CODE"

API_RESP=$(curl -s "${API_ENDPOINT}/weather?city=${TEST_CITY}")
echo "$API_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); \
  assert 'current' in d and 'forecast' in d" 2>/dev/null \
  && check "API returns current + forecast keys for '$TEST_CITY'" "PASS" \
  || check "API returns current + forecast keys for '$TEST_CITY'" "Unexpected response: $(echo "$API_RESP" | head -c 100)"

INVALID_RESP=$(curl -s -o /dev/null -w "%{http_code}" "${API_ENDPOINT}/weather?city=ZZZZZNOTACITY99999")
[ "$INVALID_RESP" = "404" ] && check "API returns 404 for invalid city" "PASS" \
  || check "API returns 404 for invalid city" "Got HTTP $INVALID_RESP"

BAD_RESP=$(curl -s -o /dev/null -w "%{http_code}" "${API_ENDPOINT}/weather?city=")
[ "$BAD_RESP" = "400" ] && check "API returns 400 for empty city param" "PASS" \
  || check "API returns 400 for empty city param" "Got HTTP $BAD_RESP"

echo ""

# ── 2. Security checks ───────────────────────────────────────────────────────
echo "--- 2. Security ---"

# S3 direct access must be blocked (OAC enforced)
S3_CODE=$(http_code "https://${WEBSITE_BUCKET}.s3.amazonaws.com/index.html")
[ "$S3_CODE" = "403" ] && check "Direct S3 URL returns 403 (OAC enforced)" "PASS" \
  || check "Direct S3 URL returns 403 (OAC enforced)" "Got HTTP $S3_CODE"

# HTTPS redirect: http:// must redirect to https://
REDIRECT_LOC=$(curl -s -o /dev/null -w "%{redirect_url}" "http://$CLOUDFRONT_DOMAIN")
echo "$REDIRECT_LOC" | grep -q "https://" \
  && check "HTTP redirects to HTTPS" "PASS" \
  || check "HTTP redirects to HTTPS" "No HTTPS redirect found (redirect_url: '$REDIRECT_LOC')"

# HSTS header
HSTS=$(curl -sI "https://$CLOUDFRONT_DOMAIN" | grep -i "strict-transport-security" | head -1)
[ -n "$HSTS" ] \
  && check "HSTS header present" "PASS" \
  || check "HSTS header present" "Header not found"

# X-Frame-Options
XFO=$(curl -sI "https://$CLOUDFRONT_DOMAIN" | grep -i "x-frame-options" | head -1)
echo "$XFO" | grep -qi "deny" \
  && check "X-Frame-Options: DENY" "PASS" \
  || check "X-Frame-Options: DENY" "Got: '$XFO'"

# X-Content-Type-Options
XCTO=$(curl -sI "https://$CLOUDFRONT_DOMAIN" | grep -i "x-content-type-options" | head -1)
echo "$XCTO" | grep -qi "nosniff" \
  && check "X-Content-Type-Options: nosniff" "PASS" \
  || check "X-Content-Type-Options: nosniff" "Got: '$XCTO'"

# API key must not appear in any frontend file via API
SOURCES=$(curl -s "https://$CLOUDFRONT_DOMAIN/js/config.js")
echo "$SOURCES" | grep -q "appid\|api_key\|apikey" \
  && check "No API key in config.js" "FAIL — key-like string found" \
  || check "No API key in config.js" "PASS"

# CORS: API must reject requests from disallowed origins
CORS_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "Origin: https://evil.example.com" \
  "${API_ENDPOINT}/weather?city=${TEST_CITY}")
[[ "$CORS_CODE" =~ ^[2345][0-9][0-9]$ ]] \
  && check "CORS probe received response (HTTP $CORS_CODE)" "PASS" \
  || check "CORS probe received response" "Got HTTP $CORS_CODE — network failure or unreachable"
CORS_HEADER=$(curl -sI \
  -H "Origin: https://evil.example.com" \
  "${API_ENDPOINT}/weather?city=${TEST_CITY}" | grep -i "access-control-allow-origin" | head -1)
echo "$CORS_HEADER" | grep -qi "evil.example.com" \
  && check "CORS blocks disallowed origin" "FAIL — evil.example.com allowed" \
  || check "CORS blocks disallowed origin" "PASS"

echo ""

# ── 3. Caching checks ────────────────────────────────────────────────────────
echo "--- 3. Caching (DynamoDB TTL) ---"

# Make two consecutive requests — second should be a cache hit
curl -s "${API_ENDPOINT}/weather?city=${TEST_CITY}" > /dev/null
sleep 1
RESP2=$(curl -s "${API_ENDPOINT}/weather?city=${TEST_CITY}")
echo "$RESP2" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'current' in d" 2>/dev/null \
  && check "Second request (cache hit path) returns valid response" "PASS" \
  || check "Second request (cache hit path) returns valid response" "Invalid response"

# Verify DynamoDB item exists
CITY_LOWER=$(echo "$TEST_CITY" | tr '[:upper:]' '[:lower:]')
DYNAMO_ITEM=$(aws dynamodb get-item \
  --table-name WeatherCache \
  --key "{\"city\":{\"S\":\"$CITY_LOWER\"}}" \
  --region us-east-1 \
  --query 'Item.ttl.N' \
  --output text 2>/dev/null)
if [ -n "$DYNAMO_ITEM" ] && [ "$DYNAMO_ITEM" != "None" ]; then
  TTL_IN=$(( DYNAMO_ITEM - $(date +%s) ))
  [ "$TTL_IN" -gt 0 ] && [ "$TTL_IN" -lt 1800 ] \
    && check "DynamoDB cache item exists with valid TTL (~${TTL_IN}s remaining)" "PASS" \
    || check "DynamoDB cache item TTL is in expected range (0–1800s)" "TTL: $TTL_IN seconds"
else
  check "DynamoDB cache item exists for '$CITY_LOWER'" "FAIL — item not found"
fi

echo ""

# ── 4. Observability checks ──────────────────────────────────────────────────
echo "--- 4. Observability ---"

LOG_GROUP="/aws/lambda/$LAMBDA_FUNCTION_NAME"
LOG_CHECK=$(aws logs describe-log-groups \
  --log-group-name-prefix "$LOG_GROUP" \
  --region us-east-1 \
  --query 'logGroups[0].logGroupName' \
  --output text 2>/dev/null)
[ "$LOG_CHECK" = "$LOG_GROUP" ] \
  && check "Lambda CloudWatch log group exists" "PASS" \
  || check "Lambda CloudWatch log group exists" "Not found: $LOG_GROUP"

DASHBOARD_CHECK=$(aws cloudwatch list-dashboards \
  --region us-east-1 \
  --query "DashboardEntries[?DashboardName=='weather-dashboard-production'].DashboardName" \
  --output text 2>/dev/null)
[ -n "$DASHBOARD_CHECK" ] \
  && check "CloudWatch dashboard exists" "PASS" \
  || check "CloudWatch dashboard exists" "Dashboard 'weather-dashboard-production' not found"

ALARM_COUNT=$(aws cloudwatch describe-alarms \
  --alarm-name-prefix "weather-dashboard" \
  --region us-east-1 \
  --query 'length(MetricAlarms)' \
  --output text 2>/dev/null)
[ "${ALARM_COUNT:-0}" -ge 4 ] \
  && check "4 CloudWatch alarms configured" "PASS" \
  || check "4 CloudWatch alarms configured" "Found: ${ALARM_COUNT:-0}"

echo ""

# ── 5. Infrastructure checks ─────────────────────────────────────────────────
echo "--- 5. Infrastructure ---"

cfn-lint infrastructure/cloudformation/*.yml > /dev/null 2>&1 \
  && check "cfn-lint: 0 errors on all templates" "PASS" \
  || check "cfn-lint: 0 errors on all templates" "Errors found — run cfn-lint manually"

STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME:-weather-dashboard-master-production}" \
  --region us-east-1 \
  --query 'Stacks[0].StackStatus' \
  --output text 2>/dev/null)
[[ "$STACK_STATUS" =~ ^(CREATE_COMPLETE|UPDATE_COMPLETE)$ ]] \
  && check "Master stack status: $STACK_STATUS" "PASS" \
  || check "Master stack status is CREATE/UPDATE_COMPLETE" "Got: $STACK_STATUS"

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
  echo " All checks passed."
  echo "============================================================"
  exit 0
fi
