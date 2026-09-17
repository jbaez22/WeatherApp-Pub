# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

---

## [3.0.0] - 2026-07-24

> **Multi-region active-passive failover** — us-east-1 primary / us-west-2 secondary, automatic Route 53 DNS failover, live failover/failback drill verified in production, per `docs/WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md`

### Added
- `infrastructure/cloudformation/master-secondary.yml` — new orchestrator deploying only `BackendStack` (Lambda + DynamoDB Global Table replica) and `ApiStack` to us-west-2
- `infrastructure/cloudformation/12-failover-dns.yml` — Route 53 health check + PRIMARY/SECONDARY failover ALIAS records for `api.weather.craftingnewtech.com`
- `infrastructure/cloudformation/09-monitoring.yml`: `Route53HealthCheckAlarm` — fires when the primary health check goes unhealthy, reusing the existing `AlarmTopic`
- `backend/lambda/weather_handler.py`: `GET /health` route + `_handle_health()` — dependency-free liveness check that Route 53 polls every 30 seconds
- `infrastructure/cloudformation/06-api.yml`: `ApiCertificate`/`ApiCustomDomain`/`ApiMapping` — a regional custom domain in each region so both answer under the same FQDN
- `infrastructure/cloudformation/00-bootstrap.yml`: `BucketNameSuffix` parameter — lets the same template create a differently-named artifacts bucket in a second region (S3 bucket names are globally unique)
- `infrastructure/cloudformation/07-ssm.yml`: `ReplicaRegions` on `ApiKeySecret` via native Secrets Manager cross-region replication
- DynamoDB Global Tables enabled on `WeatherCache` via `aws dynamodb update-table --replica-updates` (CLI, not a CloudFormation resource-type change)
- `pipeline/buildspec/deploy-infra-secondary.yml`, `deploy-app-secondary.yml` + a conditional `DeploySecondary` stage on both the Infra and App pipelines
- `frontend/js/config.js`: `API_BASE_URL` now points at the failover domain `https://api.weather.craftingnewtech.com` instead of a region-specific API Gateway URL
- Live failover/failback drill executed and verified in production — documented step-by-step in `docs/WAPMultiRegion/WeatherApp-MultiRegion-Runbook-V1.md` §10

### Changed
- `infrastructure/cloudformation/01-iam.yml`: `LambdaExecutionRole`'s DynamoDB/Secrets Sids and `AppCodeBuildRole`'s Lambda-update/CodeDeploy Sids widened to cover both regions (were pinned to `${AWS::Region}`, which broke the secondary-region deploy)
- `infrastructure/cloudformation/master.yml`: added secondary-region params/passthrough; now exposes `ApiRegionalDomainName`/`ApiRegionalHostedZoneId` at the root

### Fixed
- `secretsmanager:ReplicateSecretToRegions`/`RemoveRegionsFromReplication` missing from `CloudFormationDeployRole` — caused a cascading rollback failure where the IAM stack's own rollback revoked the permission before the SSM stack's rollback could use it; patched live, then added to the source template
- Route 53 health check returning 404 — it was targeting the API Gateway custom domain's regional endpoint, which requires Host-header/SNI matching a direct health-check connection can't satisfy; pointed it at the raw `execute-api` hostname instead
- JMESPath chained bracket filters (`ResourceRecordSets[?Name==x][?Type=='A']`) silently return an empty list rather than composing as sequential filters — runbook commands fixed to use one combined `&&` filter

### Notes
- Additional monthly cost: ~$4.22 (dominated by the second GuardDuty detector, billed per region unlike CloudTrail) — see `docs/cost-optimization.md`
- Tagged `v3.0.0` at completion

---

## [2.2.0] - 2026-07-14

> **CI/CD pipeline split** — single CodePipeline split into independent Infra and App pipelines, per `docs/WeatherApp-PipeSplit-ImplePlan-V1.md`

### Added
- `infrastructure/cloudformation/08b-app-pipeline.yml` — new Application pipeline: Source → Validate (pip-audit) → Test (pytest) → ApproveDeploy → Deploy (Lambda + canary + PC + frontend + CDN) → ValidateDeployment (live smoke test)
- `infrastructure/cloudformation/01-iam.yml`: `AppCodeBuildRole`, `PipelineReleaseLambdaRole` — new least-privilege roles for the App pipeline's CodeBuild projects and the infra-before-app coordination Lambda
- `pipeline/buildspec/validate-infra.yml`, `validate-app.yml`, `deploy-infra.yml`, `deploy-app.yml`, `validate-infra-deployment.yml` — replace the combined `validate.yml`/`deploy.yml` with infra-only and app-only variants
- `PipelineReleaseFunction` (Lambda) + `InfraPipelineStateRule` (EventBridge) — release a held App pipeline execution once the Infra pipeline reaches a terminal state, coordinated via a one-parameter SSM flag (`/weather-dashboard/pipeline-coordination/pending-app-release-production`) written by the extended `PipelineFilterFunction`
- `weather-dashboard-app-deploy-approval-production` (SNS) — the App pipeline's own manual-approval notification topic, independent of the Infra pipeline's

### Changed
- `infrastructure/cloudformation/08-pipeline.yml`: `Test` stage/project removed (moved to the App pipeline); `PipelineFilterFunction` rewritten to classify changed paths as infra/app/docs and route to one pipeline, both (queuing the App release), or neither
- `infrastructure/cloudformation/01-iam.yml`: `CodeBuildServiceRole` renamed to `InfraCodeBuildRole`, trimmed to Infra-only actions (CloudFormation ops + drift detection); `CodePipelineServiceRole` and `PipelineFilterLambdaRole` updated to cover both pipelines
- Pipeline renamed `weather-dashboard-pipeline-production` → `weather-dashboard-infra-pipeline-production`; its 3 CodeBuild projects and log groups renamed to the `weather-dashboard-infra-*` prefix, matching the App pipeline's `weather-dashboard-app-*` naming
- `pipeline/buildspec/validate-infra.yml`/`validate-app.yml`: Python bumped 3.11 → 3.14 (pure static analysis, no runtime-parity constraint); `deploy-app.yml`: Node bumped 20 → 24 (frontend build only, unrelated to the Lambda runtime, which stays on 3.11)

### Fixed
- `CodePipelineServiceRole`'s SNS-publish permission was scoped only to the Infra pipeline's approval topic — the App pipeline's `ApproveDeploy` action failed outright with a `PermissionError` before anyone could approve or reject it; added the second topic's ARN
- `InfraCodeBuildRole`'s drift-check permissions were missing `cloudformation:DetectStackResourceDrift` — a distinct action `DetectStackDrift` requires under the hood to inspect each stack resource; the Infra pipeline's `ValidateDeployment` stage failed with `AccessDenied` until added

### Removed
- `pipeline/buildspec/validate.yml`, `deploy.yml` — superseded by the infra/app split variants
- Orphaned `TestLogGroup` (`/aws/codebuild/weather-dashboard-test-production`) and the 3 pre-rename CodeBuild log groups — all `DeletionPolicy: Retain`, manually deleted after their replacements were confirmed live

---

## [2.1.0] - 2026-07-03

> **Hourly redesign** — Weather Underground-style table · 9 columns · full-width layout

### Added
- `frontend/index.html`: `#hourly-strip` placeholder `<div>` added inside the forecast section; rendered by JS, hidden until a day card is clicked
- `frontend/js/app.js`: `windDir(deg)` helper — converts wind degrees to 8-point compass label (`N`, `NE`, `E`, `SE`, `S`, `SW`, `W`, `NW`) using `Math.round(deg / 45) % 8`
- `frontend/css/styles.css`: `.hourly-table-wrap`, `.hourly-table`, `.ht__*` cell classes — WU-style table with blue `thead`, alternating row stripes, hover highlight, and `overflow-x: auto` wrapper for horizontal scroll on narrow viewports

### Changed
- `backend/lambda/weather_client.py`: hourly shape in `_shape_response()` expanded with five new fields per entry — `humidity` (%), `wind_speed` (m/s), `wind_deg` (°), `pressure` (hPa), `precip_mm` (rain `1h` + snow `1h` combined, rounded to 2 dp; defaults to `0.0` when absent)
- `frontend/js/app.js`: `renderHourly()` replaced horizontal slot cards with a `<table>` (`<thead>` + `<tbody>`); hourly strip moved from inside each narrow forecast card to a full-width panel below the forecast grid; 9 columns: Time · Conditions (icon + description) · Temp · Feels Like · Humidity · Wind · Precip · Amount · Pressure; `collapseCard()` / `expandCard()` target `#hourly-strip` via `hourlyStrip.hidden` instead of per-card `.hourly-panel` max-height animation; `setUnit()` restores `expandedDayIndex` after re-render so the strip stays open and temps update
- `frontend/css/styles.css`: removed `.hourly-panel`, `.hourly-panel.is-open`, `.hourly-slot` and all children; replaced with `.hourly-strip`, `.hourly-strip__label`, `.hourly-strip__empty`, `.hourly-table-wrap`, `.hourly-table`, and `.ht__time / .ht__cond / .ht__icon / .ht__desc / .ht__temp / .ht__feel / .ht__humidity / .ht__wind / .ht__pop / .ht__precip / .ht__pressure`
- `backend/tests/test_weather_client.py`: `_make_hourly()` fixture updated with `humidity`, `wind_speed`, `wind_deg`, `pressure`, `rain` fields; `test_hourly_entry_has_all_required_fields` assertion updated to cover all new fields
- `backend/tests/test_weather_handler.py`: `_SAMPLE_WEATHER` hourly entry updated with `humidity`, `wind_speed`, `wind_deg`, `pressure`, `precip_mm`

### Notes
- Hourly data for days 3–7 shows "no hourly data" message — OWM One Call API 3.0 hourly coverage is 48 hours from the current time
- `precip_mm` combines rain and snow into a single value; shows `0 mm` when dry

---

## [2.0.0] - 2026-07-03

> **V2** — 7-day forecast · Expandable daily cards · 48-hour hourly data · One Call API 3.0

### Added
- `backend/lambda/weather_client.py`: `_geocode(city, api_key)` — calls OWM Geocoding API (`/geo/1.0/direct?limit=1`) to resolve city name to lat/lon + canonical display name; raises `CityNotFoundError` when the response list is empty
- `backend/lambda/weather_client.py`: `_shape_response()` — transforms raw One Call 3.0 response into the V2 contract `{ current, daily[7], hourly[48] }`; defaults optional fields `pop`, `uvi`, `visibility` to `0` / `10000` when absent
- `frontend/js/app.js`: `renderHourly(dayIndex, dayDt)` — filters the 48-hour flat array to entries whose `dt` falls on the same calendar day as the clicked forecast card; renders a horizontal-scroll strip of hourly slots (time · icon · temp · precip%)
- `frontend/js/app.js`: accordion helpers `expandCard()`, `collapseCard()`, `toggleCard()` — single-open-at-a-time accordion with `aria-expanded` tracking and CSS `max-height` open/close animation
- `frontend/js/app.js`: event delegation on `forecast-grid` for click and keyboard (`Enter` / `Space`) — clicking inside the open hourly strip is excluded to allow scrolling without collapsing
- `frontend/css/styles.css`: `.forecast-card__chevron` — CSS-only rotating chevron (border trick, no image file, CSP-safe); `.hourly-panel` / `.hourly-panel.is-open` — `max-height` transition for smooth accordion animation; `.hourly-slot` layout for hourly time/icon/temp/precip%
- `docs/api-documentation-v2.md`, `docs/architecture-decisions.md` (ADR-013 through ADR-015): full V2 contract reference and decision records

### Changed
- `backend/lambda/weather_client.py`: `fetch_weather()` rewritten — two-step flow: geocode → One Call 3.0; previous two-call flow (V1 `/data/2.5/weather` + `/data/2.5/forecast`) removed along with `_build_params()` and `_BASE_URL`
- `backend/tests/test_weather_client.py`: full rewrite — 37 tests across four classes (`TestFetchWeatherHappyPath`, `TestResponseShapingEdgeCases`, `TestGeocodingErrors`, `TestOnecallErrors`); mocks `geo/1.0/direct` and `data/3.0/onecall` endpoints; coverage remains 100%
- `backend/tests/test_weather_handler.py`: `_SAMPLE_WEATHER` updated to V2 shape (`daily[]`, `hourly[]` in place of `forecast.list[]`); all 22 handler tests unchanged and passing
- `frontend/js/app.js`: `parseForecast()` removed — no longer needed; `forecastData` assigned directly from `data.daily`; `hourlyData` added to state from `data.hourly`; forecast title hardcoded to `"7-Day Forecast"`; each forecast card adds `pop` + `uvi` meta line; `setUnit()` re-expands the previously open card after re-rendering so hourly temps update to the new unit
- `frontend/css/styles.css`: `.forecast-card` gains `cursor: pointer`, hover and expanded border highlight; `.forecast-card__header` flex row for day name + chevron
- `frontend/index.html`: section `aria-label` updated to `"7-day forecast"`; `role="list"` removed from `forecast-grid` (cards are now `role="button"`)

### Notes
- No infrastructure changes (CloudFormation, API Gateway, DynamoDB, SSM unchanged)
- V1 DynamoDB cache entries (15-min TTL) expire naturally after deploy — no manual flush required
- OWM One Call API 3.0 subscription required; same API key as V1; first 1,000 calls/day free

---

## [1.0.5] - 2026-07-03

### Added
- `infrastructure/cloudformation/01-iam.yml`: new `PipelineFilterLambdaRole` — least-privilege IAM role for the filter Lambda; scoped to `codecommit:GetDifferences` on the project repo and `codepipeline:StartPipelineExecution` on the project pipeline
- `infrastructure/cloudformation/08-pipeline.yml`: `PipelineFilterFunction` — inline Python 3.11 Lambda that intercepts every CodeCommit push, inspects changed file paths via `GetDifferences`, and skips the pipeline when all changes are docs-only (`docs/**`, `*.md` at root, `diagrams/*.md`); fail-safe: new-branch pushes and empty diffs always start the pipeline
- `infrastructure/cloudformation/08-pipeline.yml`: `FilterFunctionLogGroup` — pre-created CloudWatch log group with retention policy for the filter Lambda; `FilterFunctionEventBridgePermission` — resource-based policy allowing EventBridge to invoke the filter Lambda
- `infrastructure/cloudformation/master.yml`: wires `PipelineFilterLambdaRoleArn` into the PipelineStack

### Changed
- `infrastructure/cloudformation/08-pipeline.yml`: EventBridge rule target changed from CodePipeline directly to the filter Lambda; `EventBridgePipelineRoleArn` parameter removed (no longer needed — Lambda invocation uses a resource policy, not an IAM role on the rule)

---

## [1.0.4] - 2026-07-03

### Added
- `docs/local-testing.md`: full pre-push local testing guide covering frontend HTTP server, unit tests, and all four IaC validation gates; includes a consolidated pre-push checklist and troubleshooting section

### Bug Fix
- `frontend/about/index.html`: moved all page-specific styles out of inline `<style>` block into `frontend/css/styles.css` — inline styles were silently blocked by Content-Security-Policy (`style-src 'self'` has no `'unsafe-inline'`), leaving the About page unstyled
- Replaced two `style="…"` inline attributes with proper CSS classes (`about-section__body`)
- Added About-page responsive breakpoints into the existing `@media (max-width: 640px)` block in `styles.css`
- `README.md`: quickstart step 5 corrected from `open frontend/index.html` to `python3 -m http.server 8080` — the file:// protocol bypasses CSP and hides CORS errors

---

## [1.0.3] - 2026-07-02

### Added
- `frontend/about/index.html` — new About page with four sections: How to Use (4-step guide), About This App (architecture overview), Built With (12 AWS services grid), Privacy & Security (5 data-handling facts)
- About page matches main page nav, header, footer, design tokens, and typography exactly
- Nav "About" link now active-highlighted when on the About page; "Home" link de-highlighted

---

## [1.0.2] - 2026-07-02

### Security Fixes
- Deleted `stackError.txt` debug file (contained AWS Account ID and Stack ARNs); added to `.gitignore`
- `invalidate-cloudfront.sh`: switched from `--paths` shorthand to `--invalidation-batch` with `CallerReference=${CODEBUILD_BUILD_ID}` — CloudFront invalidations are now idempotent per build (fixes ShellCheck SC2034)
- `validate-e2e.sh`: `CORS_CODE` variable now used to assert CORS probe received a valid HTTP response before checking headers; removes network-failure false-pass risk (fixes ShellCheck SC2034); removed hardcoded AWS Account ID from usage comment
- `ssm_secrets.py` / `weather_handler.py`: renamed log message strings from `"API key"` to `"weather credential"` to eliminate Semgrep CWE-532 false positives
- `docs/security.md`: replaced hardcoded Account ID in bucket policy example with `<account-id>` placeholder; corrected threat table (`cfn_nag` → `checkov + cfn-lint`); added CORS HTTP-status check to security testing checklist; added Local Security Scanning section documenting the 11-tool `/security-scan` skill
- `docs/architecture-decisions.md`: updated ADR-008 consequence note to reflect idempotent invalidation via `CallerReference`

---

## [1.0.1] - 2026-07-02

### Frontend UI Redesign
- Default temperature unit changed to Fahrenheit (°F); New York auto-loaded on page open
- Weather icon repositioned: icon left, temperature + description right (matching original design)
- 18 custom local SVG weather icons added to `frontend/assets/icons/` (replaces OpenWeatherMap raster PNGs)
- Stats block narrowed to 45% width, right-aligned, changed from 3-column grid to vertical list with horizontal stat rows
- Forecast high/low temperatures coloured red (`#DC2626`) and blue (`#3B82F6`) respectively
- Forecast title made dynamic (`N-Day Forecast` reflecting actual API-returned days)
- Footer icons corrected to match original design: shield+checkmark (Secure & Private), dollar-in-circle (Cost Efficient), padlock (Built with Best Practices); icon colour changed from blue to green (`#22c55e`)
- Wind speed converted from m/s to km/h
- Date format updated to include time and abbreviated month (e.g. `Thu, Jul 02, 2026 · 2:30 PM`)
- Safari `[hidden]` attribute fix: added `[hidden] { display: none !important; }` to prevent flex overrides
- Error card no longer shown on cold page load (cleared default text; explicit `hide(errorCard)` on success path)
- Nav link changed from `Architecture` to `About`
- Search icon changed from magnifier to location pin SVG

---

## [1.0.0] - 2026-07-02

### Phase 12 — Final Documentation
- Added `docs/api-documentation.md` — full endpoint reference, response schema, error codes, CORS policy, caching behaviour
- Added `docs/security.md` — threat model, security controls by layer, IAM design, known accepted risks
- Added `docs/architecture-decisions.md` — 11 ADRs covering IaC tool, runtime, API layer, database, source control, OAC vs OAI, secrets backend, hosting, CI/CD, CloudFront Function, and VPC decision
- Added `docs/runbook.md` — API key rotation, deployment rollback, cache clearing, CloudFront invalidation, Lambda code update, stack teardown
- Added `docs/troubleshooting.md` — pipeline failures (cfn-lint, cfn_nag, pip-audit, pytest), Lambda errors, CloudFront issues, DNS/SSL problems, local dev issues
- Added `docs/cost-optimization.md` — Free Tier analysis, cost at scale table, reasoning behind each cost-reduction decision, billing alert setup
- Added `docs/user-manual.md` — end-user guide: search, current weather, C°/F° toggle, 5-day forecast, FAQ, troubleshooting
- Fixed `README.md` repo structure: `secrets.py` → `ssm_secrets.py`
- Added `validate-e2e.sh` to `README.md` pipeline scripts listing

---

## [0.11.0] - 2026-07-02

### Phase 11 — End-to-End Validation
- Added `pipeline/scripts/validate-e2e.sh` — 20 checks across 5 categories (functional, security, caching, observability, infrastructure)
- Fixed 9 CVEs in `backend/lambda/requirements.txt`: updated `requests`, `urllib3`, `idna`, `boto3`, `botocore`, `certifi`, `charset-normalizer` to latest patched versions
- Verified all 73 unit tests still pass at 100% coverage after dependency update
- `pip-audit` now reports 0 vulnerabilities

---

## [0.10.0] - 2026-07-02

### Phase 10 — Deployment Guide
- Added `docs/deployment-guide.md` — 14-step bootstrap guide for an existing AWS account in us-east-1
- Documents pre-creation of the artifacts S3 bucket (idempotent pattern to avoid chicken-and-egg with CloudFormation)
- Step 8: stores OpenWeatherMap API key via `aws ssm put-parameter --type SecureString`
- Includes rollback and teardown procedures

---

## [0.9.0] - 2026-07-02

### Phase 9 — Pipeline Scripts
- Added `pipeline/scripts/validate-templates.sh` — local mirror of buildspec gates (cfn-lint, cfn_nag, aws cloudformation validate-template)
- Added `pipeline/scripts/run-tests.sh` — uses `python3 -m pytest` for macOS portability; checks pytest-cov import before running
- Added `pipeline/scripts/deploy-infrastructure.sh` — `cfn package` + `cfn deploy --no-fail-on-empty-changeset` workflow with full parameter documentation
- Added `pipeline/scripts/invalidate-cloudfront.sh` — `cloudfront create-invalidation --paths "/*"` using CODEBUILD_BUILD_ID as caller reference

---

## [0.8.0] - 2026-07-02

### Phase 8 — Build Specification
- Added `buildspec.yml` — CodeBuild build specification with 5 pre_build gates (`on-failure: ABORT`): cfn-lint, cfn_nag (Ruby gem), aws validate-template (all templates except master.yml), pip-audit, pytest --cov-fail-under=80
- Lambda packaging: `pip install -r requirements.txt -t /tmp/lambda-pkg` + zip
- Two-pass S3 sync: assets with `max-age=86400`, HTML with `no-cache`
- Vite build outputs to `dist-architecture/` (configured via `outDir` in `vite.config.js`)
- Removed parameter-store section; env vars injected by CodeBuild project definition in `08-pipeline.yml`

---

## [0.7.0] - 2026-07-01

### Phase 7 — CloudFormation Infrastructure Templates
- Added `infrastructure/cloudformation/01-iam.yml` — 5 least-privilege IAM roles: LambdaExecutionRole, CodeBuildServiceRole, CodePipelineServiceRole, CloudFormationDeployRole, EventBridgePipelineRole
- Added `infrastructure/cloudformation/02-storage.yml` — WebsiteBucket (private, Block Public Access) and ArtifactsBucket (versioned, lifecycle policy, TLS-only bucket policy)
- Added `infrastructure/cloudformation/03-cdn.yml` — CloudFront distribution with OAC, security headers policy, directory index CloudFront Function, optional Route 53 ALIAS record; OAC bucket policy placed here to avoid circular dependency
- Added `infrastructure/cloudformation/05-backend.yml` — Lambda function (Python 3.11, 256 MB, 30s timeout, `ReservedConcurrentExecutions: 10`)
- Added `infrastructure/cloudformation/08-pipeline.yml` — CodeCommit repository, CodeBuild project, CodePipeline (2 stages: Source + Build), EventBridge rule for push-triggered pipeline (no polling)
- Added `infrastructure/cloudformation/09-monitoring.yml` — SNS alarm topic, 4 CloudWatch alarms (Lambda errors, Lambda duration, API 5xx, API 4xx), 5-widget CloudWatch dashboard
- Added `infrastructure/cloudformation/master.yml` — Root nested stack orchestrating all 9 sub-stacks in dependency order
- Fixed cfn-lint W3037: removed invalid IAM actions `apigateway:TagResource` and `codebuild:TagResource`
- Fixed cfn-lint E3012: corrected Lambda Tags from object to array format
- Fixed cfn-lint W3005: removed redundant DependsOn in `08-pipeline.yml`
- Fixed cfn-lint E1029: removed variable reference from CodeCommit description outside Fn::Sub
- Fixed cfn-lint E1019: replaced `LambdaTimeoutSeconds` with `LambdaDurationThresholdMs` parameter in `09-monitoring.yml`

---

## [0.6.0] - 2026-07-01

### Phase 6 — API Gateway
- Added `infrastructure/cloudformation/06-api.yml` — API Gateway HTTP API with CORS restricted to `https://weather.craftingnewtech.com`, Lambda proxy integration, `$default` stage with auto-deploy
- Output: `ApiEndpoint`, `WeatherEndpoint`

---

## [0.5.0] - 2026-07-01

### Phase 5 — Frontend
- Added `frontend/index.html` — semantic HTML5 structure
- Added `frontend/css/styles.css` — responsive white-card layout, blue CTA, mobile-first
- Added `frontend/js/app.js` — city search, weather display, C°/F° toggle, 5-day forecast rendering
- Added `frontend/js/config.js` — API endpoint configuration (no secrets, points to API Gateway URL)
- Added `frontend/assets/` — weather icons and static assets
- Added architecture diagram page (`frontend/architecture/`) built with Vite; outputs to `dist-architecture/`

---

## [0.4.0] - 2026-07-01

### Phase 4 — Unit Tests
- Added `backend/tests/test_weather_handler.py` — handler integration tests (mock API Gateway events)
- Added `backend/tests/test_validators.py` — whitelist regex edge cases
- Added `backend/tests/test_cache.py` — DynamoDB get/put/miss via moto
- Added `backend/tests/test_weather_client.py` — OWM API call + response mapping via requests-mock
- Added `backend/tests/test_secrets.py` — SSM GetParameter mock
- Added `pytest.ini` and `.coveragerc`
- Test suite achieves 100% line coverage across all Lambda modules; enforces ≥ 80% gate

---

## [0.3.0] - 2026-07-01

### Phase 3 — Lambda Backend
- Added `backend/lambda/weather_handler.py` — Lambda entry point; routes API Gateway event to modules
- Added `backend/lambda/validators.py` — input validation with whitelist regex (`^[a-zA-Z0-9\s,\-\.]{1,100}$`)
- Added `backend/lambda/cache.py` — DynamoDB GetItem / PutItem with 15-minute TTL
- Added `backend/lambda/weather_client.py` — OpenWeatherMap API client (current + forecast)
- Added `backend/lambda/ssm_secrets.py` — SSM Parameter Store retrieval with execution-context caching
- Added `backend/lambda/requirements.txt` — pinned dependencies (requests, boto3, botocore, urllib3, certifi, charset-normalizer, idna)
- Added `infrastructure/cloudformation/04-database.yml` — DynamoDB WeatherCache table (On-Demand + TTL enabled)
- Added `infrastructure/cloudformation/07-ssm.yml` — SSM parameter placeholder (type SecureString, value overridden at bootstrap)

---

## [0.2.0] - 2026-07-01

### Phase 2 — Security Baseline
- Established security requirements: API key in SSM Parameter Store only, no secrets in JS, CORS locked to custom domain, least-privilege IAM, OAC-enforced S3 access
- Added input validation requirement (whitelist regex on city parameter)
- Documented security scanning gates: pip-audit (HIGH/CRITICAL CVEs block deploy), cfn_nag (IaC security anti-patterns)

---

## [0.1.0] - 2026-07-01

### Phase 1 — Repository Structure & Documentation
- Created full project directory structure (frontend, backend, infrastructure, pipeline, diagrams, docs)
- Added `README.md` with architecture overview, technology stack table, security highlights, CI/CD pipeline description, quickstart, and documentation/diagram index
- Added `diagrams/architecture.md` — full AWS service architecture (Mermaid)
- Added `diagrams/cicd-pipeline.md` — CodeCommit → Production CI/CD flow (Mermaid)
- Added `diagrams/data-flow.md` — cache hit vs. cache miss request lifecycle (Mermaid)
- Added `diagrams/iam-roles.md` — IAM role-to-service permission map (Mermaid)
- Added `diagrams/network-flow.md` — HTTPS, OAC, and CORS boundary diagram (Mermaid)
- Added `ACTION_PLAN.md` (12-phase living plan) and frozen initial copy `ACTION_PLAN_v1.0_initial.md`
- Added `CHANGELOG.md`
