# Architecture Decisions

Architecture Decision Records (ADRs) for the Weather Dashboard. Each record documents the problem, the options considered, the choice made, and why.

> **V2 ADRs** (ADR-013 through ADR-015) document decisions for the V2 feature set: 7-day forecast, expandable daily cards, and 48-hour hourly data.

---

## ADR-001: IaC Tool — CloudFormation over Terraform / CDK

**Status:** Accepted

**Context:** The project needs to provision ~10 AWS resource types in a reproducible, auditable way. The main options were HashiCorp Terraform, AWS CDK, AWS SAM, and AWS CloudFormation.

**Decision:** AWS CloudFormation (YAML, nested stacks).

**Rationale:**
- Demonstrates deep AWS-native knowledge — a differentiator from the majority of portfolios that use Terraform
- Zero third-party state backend required (state lives in the CloudFormation service)
- Native integration with CodePipeline (`cloudformation deploy`), no extra plugin layer
- Nested stacks keep each concern isolated (IAM, storage, CDN, backend, pipeline, monitoring) and allow independent updates
- CDK generates CloudFormation, so understanding raw templates is the deeper skill

**Consequence:** Template verbosity is higher than Terraform or CDK. Accepted — readability of YAML is appropriate for a portfolio that will be read by humans.

---

## ADR-002: Runtime — Python 3.11 for Lambda

**Status:** Accepted

**Context:** Lambda supports Node.js, Python, Java, Go, Ruby, and custom runtimes. The backend logic is straightforward: validate input, check cache, call HTTP API, store result.

**Decision:** Python 3.11.

**Rationale:**
- `requests` and `boto3` are both first-class Python libraries with extensive documentation
- Python 3.11 is an AWS-supported managed runtime with security patches until 2026-11; there is no need to manage the runtime layer
- Cold start for a Python Lambda with a small dependency set is 200–400 ms, acceptable for weather lookups
- Unit testing with `moto` + `requests-mock` is mature and well-understood

**Consequence:** If cold-start latency ever becomes an issue, migrating to a Lambda SnapStart-compatible runtime (Java) or provisioned concurrency would address it. Not a concern at portfolio traffic.

---

## ADR-003: API Layer — HTTP API over REST API

**Status:** Accepted

**Context:** API Gateway offers two products: REST API (v1) and HTTP API (v2). The endpoint is a single GET route with no authentication, no usage plans, and no request transformation.

**Decision:** API Gateway HTTP API (v2).

**Rationale:**
- ~70% lower cost per million requests vs. REST API
- Native CORS configuration at the API level — no Lambda proxy boilerplate
- Lower latency (lighter overhead per request)
- REST API features not needed here: request/response mapping, API keys, usage plans, caching (handled in DynamoDB instead)

**Consequence:** If future requirements need API Gateway caching, request transformation, or WAF integration, migration to REST API would be needed. Currently not required.

---

## ADR-004: Database — DynamoDB On-Demand with TTL

**Status:** Accepted

**Context:** The backend needs a cache to avoid calling OpenWeatherMap on every request. Options considered: DynamoDB, ElastiCache (Redis), and no cache.

**Decision:** DynamoDB On-Demand with a `ttl` attribute (15-minute TTL).

**Rationale:**
- Serverless — no cluster to manage, no always-on cost
- DynamoDB Free Tier includes 25 GB storage and enough read/write capacity for portfolio-level traffic
- TTL auto-deletion is built in — no scheduled cleanup job required
- A single GetItem / PutItem pair is sufficient for the cache pattern
- ElastiCache Redis requires a VPC, NAT Gateway, and minimum cluster node — adds ~$15–30/month and significant operational complexity

**Consequence:** DynamoDB GetItem latency (~1–5 ms) is higher than Redis (~0.1 ms). Acceptable — the 200–400 ms OWM call dominates.

---

## ADR-005: Source Control — CodeCommit over GitHub / GitLab

**Status:** Accepted

**Context:** The project requires a Git repository. GitHub is the most popular option; GitLab and Bitbucket are alternatives. The project constraint is AWS-native services only.

**Decision:** AWS CodeCommit.

**Rationale:**
- Hard constraint: no third-party platforms (GitHub, GitLab, etc.)
- CodeCommit integrates natively with CodePipeline via EventBridge (push event → pipeline trigger, no polling)
- IAM controls access — no separate GitHub token management
- Free for ≤ 5 active users

**Consequence:** CodeCommit has fewer community integrations (no Dependabot, no GitHub Actions ecosystem). Accepted — CI/CD is fully self-contained in CodeBuild/CodePipeline.

---

## ADR-006: CloudFront Origin Access — OAC over OAI

**Status:** Accepted

**Context:** CloudFront needs to read from the private S3 bucket. Two mechanisms exist: Origin Access Identity (OAI, legacy) and Origin Access Control (OAC, current).

**Decision:** Origin Access Control (OAC) with SigV4 signing.

**Rationale:**
- AWS recommends OAC for all new distributions; OAI is in maintenance mode
- OAC uses SigV4 request signing (same credential model as all other AWS services)
- OAC supports S3 server-side encryption with KMS (OAI does not support SSE-KMS buckets)
- CloudFormation `AWS::CloudFront::OriginAccessControl` resource is GA

**Consequence:** The S3 bucket policy must reference the distribution ARN, which creates a dependency: distribution must be created before the bucket policy. Solved by placing the bucket policy in `03-cdn.yml` (not `02-storage.yml`), where the distribution ARN is available as a resource reference.

---

## ADR-007: Secrets — SSM Parameter Store over Secrets Manager

**Status:** Superseded by ADR-019 (2026-07-14) — see that entry for why the
tradeoff below was revisited. Kept here as the historical record of the
original decision, not rewritten.

**Context:** The OpenWeatherMap API key must be stored securely and retrieved by Lambda at runtime. Options: SSM Parameter Store, AWS Secrets Manager, Lambda environment variable (plaintext), KMS-encrypted environment variable.

**Decision:** SSM Parameter Store (`SecureString`).

**Rationale:**
- `SecureString` parameters are encrypted with KMS — same security as Secrets Manager
- SSM free tier: 10,000 API calls/month; no per-secret charge
- Secrets Manager costs $0.40/secret/month — unnecessary overhead for a single key
- Secrets Manager's primary differentiator is automatic rotation, which is not available for third-party API keys without a custom rotation Lambda
- Lambda retrieves the key once per cold start via `ssm:GetParameter` with `WithDecryption=True`

**Consequence:** Manual rotation required (update SSM parameter, deploy new Lambda to pick up the change on next cold start). See `docs/WeatherApp-runbook.md` for the rotation procedure. *(As of ADR-019, superseded — rotation is now automated via Secrets Manager.)*

---

## ADR-008: Frontend Hosting — S3 + CloudFront over Amplify / EC2

**Status:** Accepted

**Context:** The frontend is a static site (HTML, CSS, vanilla JS). Hosting options: S3 + CloudFront, AWS Amplify, EC2 with Nginx.

**Decision:** S3 + CloudFront (manual configuration via CloudFormation).

**Rationale:**
- Demonstrates explicit understanding of S3 website hosting, OAC, cache policies, and security headers — a deeper skill signal than Amplify (which hides all of this)
- Amplify creates its own CloudFormation stack internally; it is a managed abstraction, not a portfolio differentiator
- EC2 is always-on — violates the serverless constraint and adds ~$8–20/month
- CloudFront + S3 is Free Tier compatible at portfolio traffic

**Consequence:** Manual cache invalidation on every deployment (handled in `pipeline/scripts/invalidate-cloudfront.sh`). Acceptable — the script runs as the last CodeBuild step. The script uses `--invalidation-batch` with `CallerReference` set to the CodeBuild build ID, making each invalidation idempotent (re-running the same build will not create duplicate invalidations).

---

## ADR-009: CI/CD — CodePipeline + CodeBuild over GitHub Actions

**Status:** Accepted

**Context:** The project needs automated testing, security scanning, and deployment on every push. GitHub Actions, Jenkins, and CircleCI were excluded by the AWS-native constraint.

**Decision:** AWS CodePipeline (orchestration) + AWS CodeBuild (compute).

**Rationale:**
- Hard constraint: no third-party CI/CD platforms
- CodePipeline Free Tier: 1 free pipeline per account
- CodeBuild Free Tier: 100 build-minutes/month (sufficient for < 30-minute monthly pipeline usage)
- Native IAM integration — CodeBuild role has exactly the permissions needed; no webhook tokens or third-party secrets
- EventBridge rule (not polling) triggers pipeline on CodeCommit push — zero latency, no polling interval

**Consequence:** CodeBuild YAML (`buildspec.yml`) is slightly more verbose than GitHub Actions workflow YAML. Accepted — the build logic is straightforward and self-documenting.

---

## ADR-010: CloudFront Function for Directory Index Rewriting

**Status:** Accepted

**Context:** The architecture diagram page is served from `/architecture/index.html`. CloudFront's `DefaultRootObject` only applies to the root path. Sub-directory requests to `/architecture/` would return a 403 without additional configuration.

**Decision:** CloudFront Function (cloudfront-js-1.0, viewer-request) that appends `/index.html` to paths ending in `/` or with no file extension.

**Rationale:**
- CloudFront Functions are free (included in CloudFront pricing)
- Sub-millisecond execution at the edge — no latency penalty
- Lambda@Edge would also work but adds cold-start latency, requires us-east-1 deployment, and costs more
- The rewriting logic is simple: a 12-line JavaScript function

**Consequence:** The function runs on every viewer request. Performance impact is negligible; CloudFront Functions run in microseconds on edge nodes.

---

## ADR-011: No VPC for Lambda

**Status:** Accepted

**Context:** Lambda can optionally run inside a VPC. This would allow private connectivity to resources like ElastiCache.

**Decision:** Lambda runs outside a VPC (default configuration).

**Rationale:**
- DynamoDB and SSM Parameter Store are both reached via AWS service endpoints — no VPC required
- VPC Lambda adds 100–500 ms cold-start latency due to ENI attachment
- A VPC would require a NAT Gateway for outbound internet access (OWM API calls) — NAT Gateway costs ~$32/month minimum
- No VPC resources exist in this architecture that require private connectivity

**Consequence:** Lambda can make outbound calls directly to the internet (OWM API). This is expected behavior. All outbound calls are to the known, trusted OpenWeatherMap endpoint.

---

## ADR-012: Lambda Filter to Skip Pipeline on Docs-Only Commits

**Status:** Accepted

**Context:** CodeCommit's EventBridge integration has no native file-path filtering. The AWS CodePipeline trigger filter feature (`GitFilePathFilterCriteria`) only supports `CodeStarSourceConnection` source actions (GitHub, GitLab, Bitbucket) — not CodeCommit. Every push to `main`, including documentation-only changes, triggered the full pipeline (5–8 minutes of build time, CodeBuild minutes consumed).

**Decision:** Insert a Python 3.11 Lambda function (`PipelineFilterFunction`) between EventBridge and CodePipeline. EventBridge invokes the Lambda on every push; the Lambda calls `codecommit:GetDifferences` to inspect changed paths and either starts the pipeline or returns without action.

Docs-only paths that skip the pipeline:
- `docs/**` — all files under the docs directory
- `*.md` — Markdown files at the project root (README, CHANGELOG, ACTION_PLAN)
- `diagrams/*.md` — Mermaid diagram source files

All other paths (frontend/, backend/, infrastructure/, pipeline/, buildspec files) trigger the pipeline as before.

**Rationale:**
- No native AWS solution exists for CodeCommit path filtering (verified against AWS docs)
- The Lambda approach is the standard AWS-recommended workaround for this gap
- Fail-safe design: new-branch pushes and empty diffs always start the pipeline — the function only skips when it can positively identify all changed files as docs
- Cost: Lambda free tier (1M calls/month) far exceeds expected push frequency; no added cost
- IAM: `PipelineFilterLambdaRole` is scoped to `GetDifferences` on this repo and `StartPipelineExecution` on this pipeline only — least-privilege maintained
- Invocation authorization uses a Lambda resource-based policy (`AWS::Lambda::Permission`) rather than an IAM role on the EventBridge rule — this is the correct AWS pattern for EventBridge → Lambda

**Consequence:** Docs commits no longer consume CodeBuild minutes or appear as pipeline executions in the console. Filter decisions (skip or start, with the changed path list) are logged to `/aws/lambda/weather-dashboard-pipeline-filter-production` for auditability. If CodeCommit source support is ever added to CodePipeline's native trigger filters, this Lambda can be removed and replaced with a `Triggers` block in the pipeline CloudFormation resource.

---

## ADR-013 [V2]: OpenWeatherMap One Call API 3.0 over Free-Tier Forecast Endpoint

**Status:** Accepted

**Context:** V1 uses two free-tier endpoints — `/data/2.5/weather` (current) and `/data/2.5/forecast` (5-day/3-hour intervals) — and buckets the 3-hour intervals into daily summaries client-side. This approach has three limitations: the bucketing logic is fragile (relies on date string comparison), the maximum honest daily range is 5 days (not 7), and hourly data is not available as a clean array.

**Decision:** Replace the two free-tier calls with a geocoding step + One Call API 3.0 (`/data/3.0/onecall`).

**Rationale:**
- One Call 3.0 returns native daily summaries (`daily[8]`) with accurate min/max temps, precipitation probability, UV index, sunrise/sunset — no client-side bucketing needed
- One Call 3.0 returns a clean 48-hour hourly array (`hourly[48]`) enabling the expandable hourly strip feature
- The geocoding pre-step (`/geo/1.0/direct`) is free tier and resolves the city name to lat/lon without adding meaningful latency (~50 ms)
- Cost: first 1,000 calls/day are free; the 15-minute DynamoDB cache means ~4 unique calls/hour per city; ~250 simultaneously active cities would be needed to approach 1,000 calls/day — effectively $0.00/month for a portfolio project
- The same API key works for both the old and new endpoints — no re-provisioning needed

**Consequence:** One Call API 3.0 requires a payment method on file with OpenWeatherMap even at zero cost. The backend response contract changes from `{ current, forecast.list[] }` to `{ current, daily[], hourly[] }`. V1 cache entries (15-min TTL) become stale on deploy but expire naturally — no manual flush needed.

---

## ADR-014 [V2]: Two-Step Geocoding (Geo API → One Call) over City-Name Lookup

**Status:** Accepted

**Context:** One Call API 3.0 requires latitude and longitude — it does not accept a city name directly. Three options were considered for resolving a city name to coordinates: (1) use the OWM Geocoding API (`/geo/1.0/direct`), (2) use the legacy `/data/2.5/weather` endpoint to get lat/lon as a side effect, (3) use a third-party geocoding service (Google Maps, Nominatim).

**Decision:** OWM Geocoding API (`/geo/1.0/direct?q={city}&limit=1`).

**Rationale:**
- Same API key, same vendor, same rate-limit bucket — no new credentials or dependencies
- Free tier with no separate quota (counted within the same OWM account)
- Returns the official display name and country code used in the UI — more reliable than inferring these from One Call's `timezone` field
- The legacy `/data/2.5/weather` call would work as a geocoding proxy but would waste an API call on data we already get from One Call, and it couples the solution to the deprecated V1 endpoint
- Third-party geocoding services add a new vendor, a new API key, and a new failure mode

**Consequence:** Each uncached request makes two API calls sequentially (geocode → One Call) instead of two in parallel (as in V1). The geocode call adds ~50 ms of latency. Total Lambda execution time for a cache miss increases from ~300 ms to ~350 ms — acceptable. The city display name shown in the UI now comes from OWM's geocoding database, which may differ slightly from the user's input (e.g. "NYC" → "New York City").

---

## ADR-015 [V2]: Accordion-Style Expandable Cards over Modal / Separate Page

**Status:** Accepted

**Context:** The hourly data for a selected day needs a UI container. Three patterns were considered: (1) accordion panel expanding inline beneath the forecast card, (2) modal overlay showing full-day detail, (3) separate detail page navigated to on card click.

**Decision:** Accordion panel expanding inline beneath the clicked forecast card.

**Rationale:**
- Keeps the user on the same page — no navigation overhead, no back button required
- Works naturally on both desktop (wide forecast grid) and mobile (single-column stacked cards)
- CSS `max-height` transition provides a smooth open/close animation with no JavaScript animation library
- Only one panel open at a time keeps the layout stable — clicking a new card collapses the previous one
- Modal overlays interrupt the page context and require focus trap logic for accessibility compliance
- A separate detail page would require a new route, URL state management, and a back-navigation pattern — disproportionate complexity for displaying hourly weather

**Consequence:** On narrow viewports, the expanded hourly strip must scroll horizontally to show all hours. This is handled with `overflow-x: auto` on the `.hourly-panel` container. The forecast grid layout must accommodate the expanded card height — solved by making each card a `flex-column` so the panel pushes sibling cards down naturally.

---

## ADR-016 [Deploy]: Lambda Blue/Green Deployment via CodeDeploy + Provisioned Concurrency

**Status:** Accepted (2026-07-14, `WeatherApp-ImproveDeploys-Plan-V2.md` Phase 2)

**Context:** Every deploy previously moved the Lambda `$LATEST` code directly — a bad deploy meant every request hit broken code simultaneously, with no automatic rollback and no protection against cold-start latency spikes on the very first request after a deploy.

**Decision:** Introduce a Lambda alias (`live`) as the sole integration target for API Gateway, with `AWS::CodeDeploy::DeploymentGroup` shifting traffic to each new version via a 10%-for-5-minutes canary (`CodeDeployDefault.LambdaCanary10Percent5Minutes`), watched against the existing `lambda-errors`/`api-5xx` CloudWatch alarms with automatic rollback, plus 1 unit of Provisioned Concurrency on the alias to eliminate cold starts on the routed target.

**Rationale:**
- A pre-traffic hook Lambda test-invokes the *new version specifically* (not the alias) before any real customer traffic shifts — a broken deploy is caught before a single real request is affected, not after
- Automatic rollback on alarm removes the dependency on a human noticing a bad deploy and manually intervening
- Provisioned Concurrency on the alias (not `$LATEST`, which can't hold it) keeps the routed target warm through every deploy
- CodeDeploy's Lambda blue/green support is a managed AWS feature — no custom traffic-shifting logic to maintain

**Consequence:** Every deploy now goes through a genuine `$LATEST`-can't-be-canaried bootstrap constraint the first time an alias is created (see `docs/WeatherApp-ImproveDeploys-Plan-TroubleshootingSteps-V1.md` items 6/11 for the exact AWS-side gotchas this surfaced) and adds a mandatory 5-minute canary window to every Deploy stage — deploys are no longer instantaneous, but a bad deploy now affects at most 10% of traffic for at most 5 minutes instead of 100% indefinitely. Cost: Provisioned Concurrency is +$3.00/mo.

---

## ADR-017 [Deploy]: X-Ray Distributed Tracing

**Status:** Accepted (2026-07-14, `WeatherApp-ImproveDeploys-Plan-V2.md` Phase 3)

**Context:** Diagnosing a slow or failing request required correlating Lambda logs, DynamoDB behavior, and OpenWeatherMap API latency manually, with no single trace tying them together.

**Decision:** Enable `TracingConfig: Active` on the Lambda function and call `aws_xray_sdk.core.patch_all()` as the first substantive line of `weather_handler.py`, before any other module that uses `boto3` or `requests` is imported.

**Rationale:**
- `patch_all()` instruments `boto3` (DynamoDB) and outbound HTTP calls (OpenWeatherMap) automatically — no manual subsegment code needed
- HTTP API (v2) has no first-class tracing toggle at the API Gateway layer the way REST APIs do, so instrumenting the Lambda code itself is the reliable way to get the full trace map
- Import order matters: `patch_all()` must run before `cache.py`/`secrets_manager.py`/`weather_client.py` are imported, since those modules construct their `boto3`/`requests` clients at import time

**Consequence:** +$0.50/mo (first 100k traces/month are free; this app is far below that volume). Verified end-to-end in production by walking a real trace's segment tree via `aws xray batch-get-traces`, confirming DynamoDB, OWM HTTP, and Secrets Manager subsegments all appear under the Lambda segment.

---

## ADR-018 [Deploy]: CloudTrail + GuardDuty as a Standalone Stack, Not Nested Under the App

**Status:** Accepted (2026-07-14, `WeatherApp-ImproveDeploys-Plan-V2.md` Phase 4.1)

**Context:** Account-level audit/detection tooling (CloudTrail management-event trail, GuardDuty) needed to be added. It could live as a nested stack inside the application's `master.yml`, or as its own independent top-level stack.

**Decision:** Standalone template (`infrastructure/cloudformation/11-audit.yml`), deployed as its own stack (`weather-dashboard-audit-production`) under operator credentials — not through the CI/CD pipeline's `CloudFormationDeployRole`, and not nested under `master.yml`.

**Rationale:**
- Separation of duties: the automated deploy role should never have the power to touch account-wide audit/detection controls
- If the application stack is ever torn down (intentionally or by a mistake in automation), the audit trail and threat detection survive — exactly the scenario audit tooling exists to protect against
- GuardDuty and multi-region CloudTrail trails are account/region singletons — keeping them outside the app's normal deploy cadence avoids any risk of an automated process accidentally trying to recreate or duplicate them

**Consequence:** This stack is **not** touched by the pipeline and **not** torn down by `docs/WeatherApp-runbook.md`'s §8 app teardown procedure — it requires its own deliberate, separate action (documented in that same section). CloudTrail log encryption required introducing this project's first CloudFormation-managed KMS CMK, which surfaced a real IAM gap (`CloudFormationDeployRole` had zero KMS actions) even though this specific stack doesn't use that role — the gap was found because Phase 4.2's Secrets Manager work needed the same KMS actions on the *pipeline's* deploy role shortly after. Cost: GuardDuty +$3.00/mo; CloudTrail and the KMS key are effectively free at this volume.

---

## ADR-019 [Deploy]: Migrate Secrets from SSM Parameter Store to Secrets Manager

**Status:** Accepted (2026-07-14, `WeatherApp-ImproveDeploys-Plan-V2.md` Phase 4.2) — supersedes ADR-007

**Context:** ADR-007 chose SSM Parameter Store specifically because Secrets Manager's automatic-rotation advantage didn't apply to a third-party API key with no rotation API. Revisited because the *manual* rotation ADR-007 accepted as a tradeoff was genuinely being skipped in practice — there was no reminder mechanism, so rotation only happened reactively (a suspected exposure), never proactively on a schedule.

**Decision:** Migrate the OpenWeatherMap API key to AWS Secrets Manager with a custom 4-stage rotation Lambda (`weather-dashboard-key-rotation-production`) on a 90-day `AWS::SecretsManager::RotationSchedule`.

**Rationale:**
- OpenWeatherMap still has no key-generation API, so full automation is impossible — but Secrets Manager's rotation contract can still *notify* an operator via SNS at a fixed cadence and hold the rotation attempt open (via a deliberate `setSecret`-stage failure) until a human acts, which is strictly better than "nothing reminds anyone, ever"
- `RotationEnabled`/`LastRotatedDate` on the secret give a queryable, auditable record that rotation is actually happening — SSM `SecureString` had no equivalent tracking
- The $0.40/mo Secrets Manager cost that ADR-007 called "unnecessary overhead" is trivial in absolute terms; the real cost that mattered was operational (a rotation that never happens), not financial
- A dedicated KMS CMK (rather than the AWS-managed default) was added for the secret's encryption, consistent with this project's practice of using project-specific CMKs (see ADR-018's CloudTrail key) rather than defaults, once a CMK was already being introduced for the first time in this project

**Consequence:** The code cutover (`backend/lambda/ssm_secrets.py`, module name kept for interface stability at the time) and the infrastructure change could not safely land in one step — the old SSM parameter had to stay alive and unreferenced-but-present as a rollback fallback until the new path was confirmed stable in production (see `docs/WeatherApp-ImproveDeploys-Plan-TroubleshootingSteps-V1.md` items 14–19 for the real deploy failures this migration's infrastructure step hit — none were about the secret itself, all were CloudFormation nested-stack/IAM ordering problems specific to this being the project's first Secrets Manager + KMS resource). Cost: +$1.40/mo total (Secrets Manager $0.40 + dedicated KMS CMK $1.00). **Update (2026-07-29):** the module was renamed to `backend/lambda/secrets_manager.py` once the SSM fallback was fully retired — the stale name had caused real confusion during a production incident investigation (a CloudWatch log line reading "SSM ClientError" was assumed to reflect current code, when it actually came from an old pre-migration Lambda version still running).

## ADR-020 [Multi-Region]: Active-Passive Failover to us-west-2, Not Active-Active

**Status:** Accepted and implemented (2026-07-24, `WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md`)

**Context:** us-east-1 outages (Nov 2021, Dec 2021, Jun 2023) took the entire single-region app offline with no recourse. Needed real DR, but the app's actual traffic scale (portfolio-level, not production SaaS) didn't justify active-active's cost/complexity (dual-write conflicts, 2x compute cost, traffic-splitting logic).

**Decision:** Active-passive failover via Route 53 Failover routing on a new `api.weather.craftingnewtech.com` domain, health-checked every 30s against the primary region's `GET /health`. DynamoDB Global Tables and native Secrets Manager cross-region replication keep the standby region's data warm; the standby Lambda runs with no Provisioned Concurrency (a cold start is a rounding error against the ~90s RTO).

**Rationale — corrected mid-implementation, not as originally proposed:**
- The original proposal assumed CloudFront already proxied API traffic and just needed a second origin added for failover. It doesn't — `frontend/js/config.js` calls API Gateway's `execute-api` URL directly. Fix: the *new* `api.weather.craftingnewtech.com` domain gets Route 53 Failover routing directly on API Gateway regional custom domains, bypassing CloudFront for API calls entirely (matching what was already true) rather than trying to retrofit CloudFront into a role it never had.
- The Route 53 health check must target the **raw** `execute-api` hostname, not the custom domain's regional target — API Gateway custom domains route by `Host` header/SNI, so a health checker connecting directly to the regional target name sends the wrong header and 404s. Found live on first deploy, not anticipated in the design.
- IAM roles are global (account-wide), but three separate `Resource` ARN Sids across `01-iam.yml` were pinned to `${AWS::Region}` (always us-east-1, since that's the only region `IamStack` is ever deployed in) — `LambdaExecutionRole`'s DynamoDB/Secrets access, `AppCodeBuildRole`'s `LambdaUpdateCode`/`CodeDeployTrafficShift` Sids. Each was only caught by actually running the specific pipeline stage or Lambda invocation that needed cross-region access for the first time — none of `cfn-lint`/`checkov`/`cloudformation validate-template` can catch a `Resource` ARN that's syntactically valid but scoped to the wrong region.
- S3 bucket names are globally unique — `00-bootstrap.yml`'s `ArtifactsBucket` needed a `BucketNameSuffix` parameter (default `''`, preserving the primary's exact existing name) since the same template can't produce two same-named buckets in two regions.

**Consequence:** ~$4.22/month additional cost (dominated by the second GuardDuty detector, which — unlike the already-multi-region CloudTrail trail — is billed and scoped per region). The `DeploySecondary` pipeline stages added to both CodePipelines are a steady-state convenience for keeping both regions' code in sync, **not** the disaster-recovery mechanism — CodeCommit/CodePipeline/CodeBuild all live only in us-east-1, so a real us-east-1 outage requires falling back to direct AWS CLI against us-west-2 (documented in `WeatherApp-runbook.md` §12). Verified end-to-end with a real, live failover/failback drill (not just a smoke test): forced the health check unhealthy via a nonexistent `ResourcePath` (zero impact to real traffic), confirmed DNS shifted to us-west-2 and the failover CloudWatch alarm actually fired, then confirmed automatic failback with zero manual DNS changes.

