# WeatherApp — Project Evolution

A short, milestone-level history of how this project grew — from a single-region
weather lookup to a two-pipeline, multi-region, actively-drilled production
system. Each entry below corresponds to a real git tag, in chronological order,
so the sequence can be checked out and compared directly (`git checkout <tag>`).

For a full line-by-line technical changelog (every file added/changed/fixed
per release), see [`CHANGELOG.md`](../CHANGELOG.md) at the project root — this
document is the "why did it grow this way" narrative; that one is the
"exactly what changed" record.

---

## Timeline

| ------------------------------- | ---------- | ------------------------------------------------------- |
| Tag                             | Date       | Milestone                                               |
| ------------------------------- | ---------- | ------------------------------------------------------- |
| `v1.0.4`                        | 2026-07-03 | First stable release — current weather + 5-day forecast |
| `v1.0.5`                        | 2026-07-03 | Pipeline filter — skip CI on docs-only commits          |
| `v2.0.0`                        | 2026-07-03 | 7-day forecast + hourly strip, One Call API 3.0         |
| `pre-improvements-v1`           | 2026-07-13 | Baseline before canary/blue-green deploy work           |
| `post-pipeline-split-v1-stable` | 2026-07-14 | Canary deploys + two-pipeline split (Infra/App)         |
| `pre-multiregion-v1`            | 2026-07-16 | Baseline before multi-region failover work              |
| `v3.0.0`                        | 2026-07-24 | Multi-region active-passive failover, drilled live      |
| ------------------------------- | ---------- | ------------------------------------------------------- |

---

## `v1.0.4` — First stable release (2026-07-03)

The initial production-quality baseline: current weather conditions plus a
5-day forecast, a white-card UI, an About page, and a CSP-compliant frontend
(no inline styles or scripts). Backend was a single Lambda calling
OpenWeatherMap's legacy `/weather` + `/forecast` endpoints directly, cached
15 minutes in DynamoDB. Security and cost controls were built in from this
first tag, not retrofitted: HTTPS via CloudFront + ACM, the API key held in
SSM Parameter Store, `cfn-lint` + `checkov` + `pip-audit` as pipeline gates,
and a unit test suite with an 80% coverage floor.

## `v1.0.5` — Pipeline filter (2026-07-03)

CodeCommit's EventBridge integration has no native file-path filtering, so
every push — including documentation-only changes — was triggering a full
5–8 minute pipeline run. Added a Lambda between EventBridge and CodePipeline
that inspects changed paths via `codecommit:GetDifferences` and skips the
pipeline entirely for docs-only commits. This Lambda's role (and the pattern
of routing decisions through it) became the foundation the pipeline split
later built on.

## `v2.0.0` — 7-day forecast, One Call API 3.0 (2026-07-03)

Rewrote the backend around OpenWeatherMap's One Call API 3.0: a two-step
geocode → One Call flow replacing the two-parallel-call V1 shape, extending
the forecast from 5 days to 7 and adding a 48-hour hourly strip per day.
Frontend gained expandable/collapsible daily cards with a hovering hourly
table underneath, keyboard-accessible and unit-toggle-aware. No
infrastructure changes — this was a pure backend contract + frontend
rendering release.

## `pre-improvements-v1` → `post-pipeline-split-v1-stable` — Canary deploys and the two-pipeline split (2026-07-13 → 2026-07-14)

The biggest structural change to the deploy process since the project
started. Two things landed in this window:

1. **Safer Lambda deploys** — CodeDeploy blue/green canary traffic shifting
   (10% for 5 minutes, watched against error-rate/5xx alarms, with a
   pre-traffic hook test call) replaced an all-at-once `$LATEST` update,
   which also required moving the function off `$LATEST` as a prerequisite
   (Provisioned Concurrency and CodeDeploy blue/green can't target it).
2. **Infra/App pipeline split** — one CodePipeline handling both
   infrastructure and application changes meant a bad app-code push could
   touch IAM/networking, and infra changes waited on irrelevant app-only
   gates. Split into an **Infra pipeline** (CloudFormation stack changes
   only) and an **App pipeline** (Lambda/frontend/CDN only), each with its
   own least-privilege deploy role, automatically routed by which paths a
   commit touches — see `pipeline-topology.md`. A commit touching both
   triggers Infra first, holding the App pipeline via a small coordination
   Lambda until Infra's deploy succeeds.

`post-pipeline-split-v1-stable` marks this work fully implemented, deployed,
and verified — the reference point for any future rollback/comparison.

## `pre-multiregion-v1` → `v3.0.0` — Multi-region active-passive failover (2026-07-16 → 2026-07-24)

Motivated by multiple real AWS us-east-1 outages (Nov 2021, Dec 2021, Jun
2023) — a single-region serverless app has no answer to "the whole region is
down." Added a passive standby in us-west-2: DynamoDB Global Tables
replication, native Secrets Manager cross-region secret replication, a
region-scoped GuardDuty detector, and Route 53 Failover routing driven by a
health check against a dependency-free `GET /health` endpoint. Both
pipelines grew a conditional `DeploySecondary` stage that deploys to
us-west-2 whenever a secondary region is configured.

This was the most operationally involved release: two separate cascading
CloudFormation rollback failures were hit and fixed live (a rollback in one
nested stack revoking a permission another nested stack's own rollback still
needed), a Route 53 health check silently 404ing because it targeted a
Host-header-routed custom domain instead of the raw regional endpoint, and a
JMESPath chained-filter gotcha that made DNS TTL checks look broken when they
weren't. Full narrative in
`docs/WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md`.

Unlike every prior milestone, this one was also **exercised, not just
deployed** — a live failover (primary region simulated down, traffic
verified serving from us-west-2) and failback drill ran against the real
production stack, documented step-by-step in
`docs/WAPMultiRegion/WeatherApp-MultiRegion-Runbook-V1.md` §10.

---

## What's next

No planned work is currently tracked beyond `v3.0.0`.
