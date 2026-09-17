# WeatherApp Multi-Region Strategy — V1

**Date:** 2026-07-10 (last reviewed 2026-07-15)  
**Author:** Cloud & DevOps Engineering Review  
**Scope:** Multi-region active-passive failover design for weather.craftingnewtech.com  
**Baseline:** weather-dashboard, single region (us-east-1) — Phases 0 through
4.2 of [`WeatherApp-ImproveDeploys-Plan-V2.md`](../WeatherApp-ImproveDeploys-Plan-V2.md)
complete (Lambda alias + Provisioned Concurrency + CodeDeploy blue/green,
X-Ray tracing, CloudTrail + GuardDuty, Secrets Manager), plus the Infra/App
CodePipeline split from
[`WeatherApp-PipeSplit-ImplePlan-V1.md`](../WeatherApp-PipeSplit-ImplePlan-V1.md)
— `weather-dashboard-infra-pipeline-production` and
`weather-dashboard-app-pipeline-production` now deploy independently, across
13 CloudFormation templates total.

**IMPLEMENTED — 2026-07-24.** This proposal is now fully built, deployed, and
verified in production, including a live failover/failback drill. See
[`WeatherApp-MultiRegion-ImplePlan-V1.md`](./WeatherApp-MultiRegion-ImplePlan-V1.md)
for the command-level implementation record (9 design corrections found
against the actual templates, 6 real gaps found and fixed during
execution) and
[`WeatherApp-MultiRegion-Runbook-V1.md`](./WeatherApp-MultiRegion-Runbook-V1.md)
for the ongoing operational/verification runbook. This document is kept as
the original architectural proposal for historical reference — some details
below (notably the CloudFront Origin Group approach in §3.2/§7) were
corrected during implementation; the ImplePlan is authoritative for what was
actually built.

---

## 1. Why Multi-Region

AWS us-east-1 is the largest and most feature-rich AWS region, but it has experienced notable outages that impacted thousands of services:

| ------------- | --------- | ---------------------------------------------------------------- |
| Date          | Region    | Impact                                                           |
| ------------- | --------- | ---------------------------------------------------------------- |
| November 2021 | us-east-1 | 7+ hour outage — Kinesis, Lambda, API Gateway, DynamoDB affected |
| December 2021 | us-east-1 | Secondary outage — S3, CloudFront, Route 53 degraded             |
| June 2023     | us-east-1 | EC2 and Lambda availability issues                               |
| ------------- | --------- | ---------------------------------------------------------------- |

A single-region deployment means a regional AWS event takes the app offline with no recourse. For a publicly available, production-grade application, this is an unacceptable risk.

---

## 2. Strategy — Active-Passive Failover

### Why not Active-Active?

Active-Active (both regions serving live traffic simultaneously) provides the best resilience but comes with significant complexity and cost: dual-write conflicts in DynamoDB, double the API Gateway and Lambda costs, and complex traffic-splitting logic. For a weather dashboard at this scale, that complexity is not justified.

### Why Active-Passive?

Active-Passive keeps the full secondary stack warm and ready but routes zero production traffic to it under normal conditions. When Route 53 health checks detect a primary region failure, DNS automatically shifts to the secondary within 60 seconds — no manual intervention required.

| ------------------ | ------------- | -------------- |
| Property           | Active-Active | Active-Passive |
| ------------------ | ------------- | -------------- |
| Resilience         | Highest       | High           |
| Complexity         | High          | Medium         |
| Cost               | ~2×           | ~1.4×          |
| RTO                | < 5 seconds   | ~60 seconds    |
| RPO                | Near-zero     | Near-zero      |
| **Recommendation** |               | **✓ This app** |
| ------------------ | ------------- | -------------- |

---

## 3. Architecture

### 3.1 Current Architecture

Single region deployment — all services in us-east-1. A regional AWS outage takes the entire app offline.

```
                           User (Browser / Mobile)
                                      │
                                      │  HTTPS
                                      ▼
┌─────────────────────────────────────▼────────────────────┐
│  AWS Cloud — us-east-1  (SINGLE REGION — current state)  │
│                                                          │
│                           ┌───────────────────┐          │
│                           │ CloudFront        │          │
│                           │ CDN · HTTPS · OAC │          │
│                           └─────────┬─────────┘          │
│                                     │                    │
│                             ┌───────┴────────┐           │
│                             ▼                ▼           │
│                      ┌─────────────┐   ┌───────────┐     │
│                      │ API Gateway │   │ S3 Bucket │     │
│                      │ HTTP API v2 │   │ Frontend  │     │
│                      └──────┬──────┘   └───────────┘     │
│                             │                            │
│              ┌─────────────────────────────┐             │
│              │ Lambda alias "live"         │─────────────┼──▶  OpenWeatherMap
│              │ Provisioned Concurrency: 1  │  (on cache miss)  External API
│              │ X-Ray tracing: active       │             │
│              │ CodeDeploy: canary 10%/5min │             │
│              └──────────────┬──────────────┘             │
│                             │                            │
│         ┌───────────────────┴───────────────────┐        │
│         ▼                   ▼                   ▼        │
│ ┌──────────────┐   ┌─────────────────┐   ┌─────────────┐ │
│ │ DynamoDB     │   │ Secrets Manager │   │ CloudWatch  │ │
│ │ WeatherCache │   │ OWM API Key     │   │ Logs+Alarms │ │
│ └──────────────┘   └─────────────────┘   └─────────────┘ │
│                                                          │
└──────────────────────────────────────────────────────────┘

  Single point of failure: if us-east-1 is degraded, the app goes offline.
```

---

### 3.2 Target Architecture

Active-passive multi-region deployment. Route 53 health checks detect a primary failure and shift
traffic to us-west-2 automatically within 60–90 seconds. DynamoDB Global Tables keep the cache
warm in both regions at all times.

```
                       User (Browser / Mobile)
                                  │
                                  │  HTTPS
                                  ▼
┌─────────────────────────────────▼─────────────────────────────────┐
│  Amazon Route 53  (Global — 100% SLA)                             │
│                                                                   │
│                ┌────────────────┴────────────────┐                │
│                ▼                                 ▼                │
│ ┌─────────────────────────────┐   ┌─────────────────────────────┐ │
│ │ PRIMARY record              │   │ FAILOVER record             │ │
│ │ weather.craftingnewtech.com │   │ weather.craftingnewtech.com │ │
│ │ → CloudFront (us-east-1)    │   │ → CloudFront (us-west-2)    │ │
│ │ Health Check: GET /health   │   │ Activates automatically     │ │
│ └─────────────────────────────┘   └─────────────────────────────┘ │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
                 ┬                                 ┬
                 │  Normal traffic                 │  Failover traffic
                 ▼                                 ▼
                 └────────────────┬────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────▼─────────────────────────────────┐
│  Amazon CloudFront  (Global — unaffected by any single region)    │
│  Origin Group: Primary → us-east-1 API GW                         │
│                Fallback → us-west-2 API GW                        │
└────────────────┬─────────────────────────────────┬────────────────┘
                 │                                 │
                 ▼                                 ▼
      ┌─────────────────────┐          ┌──────────────────────┐
      │ PRIMARY REGION      │          │ FAILOVER REGION      │
      │ us-east-1  ✓ active │          │ us-west-2  ○ standby │
      │                     │          │                      │
      │ API Gateway v2      │          │ API Gateway v2       │
      │ Lambda (handler)    │          │ Lambda (handler)     │
      │ Secrets Manager     │          │ Secrets Manager      │
      │                     │          │                      │
      │ DynamoDB ◄──────────┼──────────► DynamoDB             │
      │ WeatherCache        │          │ WeatherCache         │
      └─────────────────────┘  < 1s    └──────────────────────┘
                 │              Global Tables replication
                 │
                 └────────────────────────────────────────▶  OpenWeatherMap
                                                             (on cache miss, either region)   External API

  RTO: ~60–90 seconds  |  RPO: < 1 second  |  Additional cost: ~$4.81/month
```

---

## 4. Component Breakdown

### 4.1 Services That Are Already Global (No Change Needed)

| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Service                      | Why it is already resilient                                                                                                                                  |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Amazon CloudFront            | Operates across 450+ PoPs worldwide — a single region outage does not affect the CDN layer                                                                   |
| Amazon Route 53              | Global DNS with a 100% uptime SLA — not tied to any single AWS region                                                                                        |
| ACM Certificate (CloudFront) | Certificates attached to CloudFront are globally replicated by AWS automatically                                                                             |
| AWS CloudTrail               | Deployed as a multi-region management-event trail (Phase 4.1) — already captures API activity in every region, including us-west-2, with no additional setup |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |

### 4.2 Services Duplicated in the Secondary Region

| --- | ----------------------- | ----------------------------------------------- | ----------------------------------------------- |
| #   | Service                 | Primary (us-east-1)                             | Secondary (us-west-2)                           |
| --- | ----------------------- | ----------------------------------------------- | ----------------------------------------------- |
| 1   | API Gateway v2          | `weather-dashboard-api-production`              | `weather-dashboard-api-production`              |
| 2   | Lambda function + alias | `weather-dashboard-handler-production` / `live` | `weather-dashboard-handler-production` / `live` |
| 3   | DynamoDB                | `WeatherCache`                                  | `WeatherCache`                                  |
| 4   | Secrets Manager Secret  | `weather-dashboard/openweathermap-api-key`      | `weather-dashboard/openweathermap-api-key`      |
| 5   | GuardDuty Detector      | 1 detector (Phase 4.1)                          | 1 detector (new)                                |
| --- | ----------------------- | ----------------------------------------------- | ----------------------------------------------- |

1) Same CloudFormation stack, different region.
2) Same code package and CodeDeploy blue/green setup deployed to both regions.
3) Global Tables — sub-second replication.
4) Native cross-region secret replication (`replicate-secret-to-regions`) — no pipeline scripting needed, unlike the old SSM approach.
5) GuardDuty is scoped per region — us-west-2 needs its own detector to cover that region's activity, unlike CloudTrail.

### 4.3 Services That Stay in Primary Only

| ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Service                                                            | Reason                                                                                                                                                     |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| S3 Website Bucket                                                  | CloudFront serves static files globally via OAC — no regional dependency                                                                                   |
| S3 Artifacts Bucket                                                | CI/CD artifact store — not in the live request path                                                                                                        |
| Provisioned Concurrency (1 unit)                                   | Standby region only serves health checks under normal conditions — a cold start there is a rounding error against the 60-90s RTO, not worth the added cost |
| Secrets Manager rotation Lambda + schedule                         | Rotation only needs to run once against the primary secret — replication (native, cross-region) propagates the new value automatically                     |
| CloudWatch Dashboards + Alarms                                     | Each region has its own CloudWatch; secondary region gets its own alarms, but the SLO dashboard itself stays primary-focused                               |
| Infra CodePipeline (`weather-dashboard-infra-pipeline-production`) | Stays in us-east-1; gains a second Deploy stage that re-runs the same `master.yml` deploy against us-west-2, in sequence after the us-east-1 stage         |
| App CodePipeline (`weather-dashboard-app-pipeline-production`)     | Stays in us-east-1; gains a second Deploy stage that publishes the Lambda version, runs the canary shift, and syncs the frontend in us-west-2              |
| ACM Certificate (API Gateway)                                      | Not needed — CloudFront handles all TLS termination                                                                                                        |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |

---

## 5. Failover Flow

### Normal Operation (Primary Healthy)

```
User → Route 53 → CloudFront → API GW (us-east-1) → Lambda → DynamoDB
```

- Route 53 health check polls `GET /health` on the primary API Gateway every 30 seconds
- CloudFront serves all API requests through the primary origin
- DynamoDB Global Tables continuously replicates every cache write to us-west-2

### During a Regional Failure

```
Step 1  (0 – 30 s)   Route 53 detects primary failure (3 consecutive health check misses)
Step 2  (30 – 60 s)  Route 53 switches the DNS record to the failover CloudFront origin
Step 3  (60 – 90 s)  DNS TTL expires; new requests resolve to the secondary region
Step 4  (ongoing)    Traffic flows: API GW (us-west-2) → Lambda → DynamoDB (us-west-2)
                     Cache is already warm — data was replicated continuously
```

| ------ | ---------- |
| Metric | Target     |
| ------ | ---------- |
| RTO    | ~60–90 s   |
| RPO    | < 1 second |
| ------ | ---------- |

### Recovery (Primary Restored)

Route 53 health checks detect the primary is healthy again. DNS automatically shifts traffic back to us-east-1. No manual action required at any step.

---

## 6. Implementation Plan

### Phase 1 — Prerequisites (Week 1)

- [ ] Add a `/health` endpoint to Lambda returning `{"status": "ok", "region": "us-east-1"}` with HTTP 200
- [ ] Parameterize CloudFormation templates to accept `AWS::Region` — remove all hardcoded `us-east-1` references
- [ ] Enable DynamoDB Global Tables on `WeatherCache` (zero-downtime migration)
- [ ] Add `us-west-2` as a replica region in the Global Table

### Phase 2 — Deploy Secondary Stack (Week 2)

- [ ] Add a `us-west-2` Deploy stage to the **Infra pipeline** (`weather-dashboard-infra-pipeline-production`) after its existing us-east-1 Deploy stage — re-runs the same `master.yml` deploy against the secondary region
- [ ] Add a `us-west-2` Deploy stage to the **App pipeline** (`weather-dashboard-app-pipeline-production`) after its existing us-east-1 Deploy stage — publishes the Lambda version, runs the canary shift, and syncs the frontend in the secondary region
- [ ] Confirm the existing Infra-before-App release coordination (SSM pending-flag + EventBridge, see `WeatherApp-PipeSplit-ImplePlan-V1.md` §B) still gates correctly per region — the App pipeline's us-west-2 stage must never fire ahead of the Infra pipeline's us-west-2 stage succeeding
- [ ] Deploy the full Lambda + API Gateway stack to us-west-2 using the same templates
- [ ] Enable native Secrets Manager cross-region replication (`replicate-secret-to-regions`) to us-west-2 — no pipeline scripting needed, unlike the old SSM approach
- [ ] Enable GuardDuty in us-west-2 (a new per-region detector — GuardDuty is not covered by the existing multi-region CloudTrail trail)
- [ ] Verify `/health` responds correctly in both regions
- [ ] Run smoke tests confirming weather API returns data from both regions

### Phase 3 — Route 53 Failover Routing (Week 3)

- [ ] Create Route 53 health check against the primary endpoint (every 30 s, threshold: 3 failures)
- [ ] Convert `weather.craftingnewtech.com` to a **Failover routing policy**
  - Primary record → CloudFront origin pointing to us-east-1 API Gateway
  - Secondary record → CloudFront origin pointing to us-west-2 API Gateway
- [ ] Lower DNS TTL to 60 seconds to speed up failover propagation
- [ ] Add a CloudFront **Origin Group** with primary and failover origins for an additional layer

### Phase 4 — Validation (Week 3)

- [ ] **Failover drill** — return 503 from us-east-1 Lambda; confirm traffic shifts to us-west-2 within 90 s
- [ ] **Failback drill** — restore us-east-1; confirm traffic shifts back automatically
- [ ] **Replication check** — write a cache entry in us-east-1; confirm it appears in us-west-2 within 2 s
- [ ] Add a CloudWatch alarm for `Route53HealthChecksStatus` to alert when failover is active
- [ ] Document the incident runbook: what to check and who to notify during an active failover

### Estimated Implementation Time

None of this has been implemented yet — the table below is a planning
estimate for hands-on engineering effort, not calendar time. The
"Week 1/2/3" labels above are calendar pacing (review cycles, off-peak
drill scheduling); the hours below are the actual work each phase takes,
estimated the same way as
[`WeatherApp-ImproveDeploys-Plan-V2.md`](../WeatherApp-ImproveDeploys-Plan-V2.md)'s
own Implementation Time tables — weighted by which specific task in each
phase has no direct precedent elsewhere in this project (and is therefore
the most likely source of first-time surprises, per this project's own
track record — see that document's §4.2 "Issues fixed" writeup).

| --------- | ------------------------- | ---------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Phase     | Section                   | Estimated Effort | Primary Time Driver                                                                                                          |
| --------- | ------------------------- | ---------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| 1         | Prerequisites             | 4h 00m           | CloudFormation region-parameterization — removing every hardcoded us-east-1 reference without breaking the existing stack    |
| 2         | Deploy Secondary Stack    | 6h 00m           | Two separate pipelines now each need their own us-west-2 Deploy stage, plus the usual first-deploy debugging buffer for both |
| 3         | Route 53 Failover Routing | 3h 00m           | CloudFront Origin Group configuration — the one piece with no direct precedent elsewhere in this project                     |
| 4         | Validation                | 3h 30m           | Writing the incident runbook takes as long as the drills themselves                                                          |
| **Total** |                           | **16h 30m**      | ~2 business days of focused engineering effort                                                                               |
| --------- | ------------------------- | ---------------- | ---------------------------------------------------------------------------------------------------------------------------- |

### Detailed Activity Breakdown

The phase-level table above is the planning-level number; this is the
same 16h 30m broken into the 20 individual activities behind it, so the
actual footprint — how many templates and resources each one touches —
is visible before work starts, not discovered mid-implementation.

| --------- | ------------------------------------------------------------------- | ----- | ----------------------------------------------------------------------------------------------- | ----------- |
| #         | Activity                                                            | Phase | Files / Resources Touched                                                                       | Effort      |
| --------- | ------------------------------------------------------------------- | ----- | ----------------------------------------------------------------------------------------------- | ----------- |
| 1         | Add `/health` endpoint returning `{status, region}`                 | 1     | `weather_handler.py` + `test_weather_handler.py` (2 files)                                      | 1h 00m      |
| 2         | Parameterize CFN templates to accept `AWS::Region`                  | 1     | `03-cdn.yml`, `master.yml` (2 of 13 templates — the only 2 with hardcoded `us-east-1`)          | 1h 30m      |
| 3         | Enable DynamoDB Global Tables on `WeatherCache`                     | 1     | `04-database.yml` (1 resource: `WeatherCache` table)                                            | 1h 00m      |
| 4         | Add `us-west-2` as a Global Table replica region                    | 1     | `04-database.yml` (same resource as #3)                                                         | 0h 30m      |
| 5         | Add a `us-west-2` Deploy stage to the **Infra pipeline**            | 2     | `08-pipeline.yml` (new stage only touches a subset of its resources)                            | 1h 00m      |
| 6         | Add a `us-west-2` Deploy stage to the **App pipeline**              | 2     | `08b-app-pipeline.yml` (new stage + canary config for the secondary region)                     | 1h 00m      |
| 7         | Verify Infra-before-App release coordination still gates per region | 2     | `08b-app-pipeline.yml`'s `PipelineReleaseFunction` (verification only, no code change expected) | 0h 30m      |
| 8         | Deploy the full stack to `us-west-2`                                | 2     | All 13 templates, ~81 resources (full duplicate stack — see aws-WeatherApp-resources.md)        | 1h 30m      |
| 9         | Enable native Secrets Manager cross-region replication              | 2     | `07-ssm.yml`, `01-iam.yml` (2 templates)                                                        | 1h 00m      |
| 10        | Enable GuardDuty in `us-west-2` (new per-region detector)           | 2     | `11-audit.yml` (1 template)                                                                     | 0h 30m      |
| 11        | Verify `/health` + smoke tests in both regions                      | 2     | 0 templates — operational verification only                                                     | 0h 30m      |
| 12        | Create the Route 53 health check                                    | 3     | `03-cdn.yml` (1 resource)                                                                       | 0h 30m      |
| 13        | Convert the DNS record to a Failover routing policy                 | 3     | `03-cdn.yml` (2 resources: primary + secondary record sets)                                     | 1h 00m      |
| 14        | Lower DNS TTL to 60 seconds                                         | 3     | `03-cdn.yml` (same 2 resources as #13)                                                          | 0h 15m      |
| 15        | Add a CloudFront Origin Group (primary + failover origin)           | 3     | `03-cdn.yml` (1 resource)                                                                       | 1h 15m      |
| 16        | Failover drill — force a 503 in us-east-1, confirm shift            | 4     | 0 templates — operational drill only                                                            | 1h 00m      |
| 17        | Failback drill — restore us-east-1, confirm auto-recovery           | 4     | 0 templates — operational drill only                                                            | 0h 30m      |
| 18        | Replication check — confirm a cache write lands in us-west-2        | 4     | 0 templates — operational verification only                                                     | 0h 30m      |
| 19        | Add a `Route53HealthChecksStatus` CloudWatch alarm                  | 4     | `09-monitoring.yml` (1 resource)                                                                | 0h 30m      |
| 20        | Document the incident runbook                                       | 4     | `WeatherApp-runbook.md` (1 doc file)                                                            | 1h 00m      |
| **Total** |                                                                     |       | **9 of 13 templates touched, ~86 resources created/modified across both regions**               | **16h 30m** |
| --------- | ------------------------------------------------------------------- | ----- | ----------------------------------------------------------------------------------------------- | ----------- |

"9 of 13 templates" counts only the templates that need actual edits
beyond a same-code redeploy — the other 4 (`00-bootstrap.yml`,
`02-storage.yml`, `05-backend.yml`, `06-api.yml`) deploy to `us-west-2`
unmodified as part of activity #8. The "~86 resources" figure is
approximate: ~81 from duplicating the full existing stack (per
[`aws-WeatherApp-resources.md`](../aws-WeatherApp-resources.md)) into
`us-west-2`, plus ~5 net-new resources in the shared/global layer
(the Route 53 health check, 2 failover record sets, the CloudFront
Origin Group, and the new CloudWatch alarm).

---

## 7. Cost Impact

| ---------------------------------------------------------------------- | ---------- | ----------- |
| Addition                                                               | Monthly    | Yearly      |
| ---------------------------------------------------------------------- | ---------- | ----------- |
| Lambda in us-west-2 (passive — minimal invocations)                    | $0.05      | $0.60       |
| API Gateway in us-west-2 (passive — health checks only)                | $0.01      | $0.12       |
| DynamoDB Global Tables replication writes                              | ~$0.15     | $1.80       |
| Secrets Manager secret replica in us-west-2                            | $0.40      | $4.80       |
| GuardDuty detector in us-west-2 (scoped per region, unlike CloudTrail) | $3.00      | $36.00      |
| CodeDeploy blue/green in us-west-2                                     | $0.00      | $0.00       |
| X-Ray tracing in us-west-2 (passive — near-zero traced requests)       | $0.00      | $0.00       |
| Route 53 health checks (2 endpoints × $0.50)                           | $1.00      | $12.00      |
| CloudWatch alarms for failover status                                  | $0.20      | $2.40       |
| **Total additional cost**                                              | **~$4.81** | **~$57.72** |
| ---------------------------------------------------------------------- | ---------- | ----------- |

**Current single-region baseline (Phases 0–4.2 of
[`WeatherApp-ImproveDeploys-Plan-V2.md`](../WeatherApp-ImproveDeploys-Plan-V2.md)
complete):** $13.11/month, $157.32/year — this is the real, verified
current spend, not the earlier `WeatherApp-Improvements-V1.md` estimate
that plan superseded.

**New monthly total (current baseline + multi-region):** ~$17.92/month  
**New yearly total:** ~$215.04/year

Multi-region resilience for **~$4.81/month** — dominated by the second
GuardDuty detector, since GuardDuty (unlike CloudTrail) is priced and
scoped per region — is still one of the best value investments in this
stack relative to the outage risk in §1.

---

## 8. What Multi-Region Does NOT Solve

| ------------------------------------------ | --------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Scenario                                   | Covered?  | Mitigation                                                                                                                          |
| ------------------------------------------ | --------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| AWS us-east-1 full outage                  | ✓ Yes     | Automatic failover to us-west-2                                                                                                     |
| Single service failure (Lambda throttling) | ✓ Yes     | CloudFront origin failover triggers automatically                                                                                   |
| OpenWeatherMap API outage                  | ✓ Partial | Stale cache fallback — live since Phase 1.1 of WeatherApp-ImproveDeploys-Plan-V2.md, not just planned                               |
| CloudFront global outage                   | ✗ Partial | Extremely rare; no practical mitigation at this scale                                                                               |
| Route 53 global outage                     | ✗ No      | Route 53 carries a 100% SLA — treated as accepted risk                                                                              |
| Application bug deployed to both regions   | ✓ Partial | Blue/green canary deploys + ValidateDeployment integration tests — live since Phase 2.2/1.2 of WeatherApp-ImproveDeploys-Plan-V2.md |
| DDoS attack                                | ✗ No      | WAF on CloudFront — still optional/deferred, see WeatherApp-ImproveDeploys-Plan-V2.md's WAF section                                 |
| ------------------------------------------ | --------- | ----------------------------------------------------------------------------------------------------------------------------------- |

---

## 9. Risks and Trade-offs

| --------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Risk                                                                  | Likelihood | Mitigation                                                                                                                                                                                                                                                                      |
| --------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| DynamoDB replication lag on high write burst                          | Low        | Cache TTL is 15 min — brief lag is acceptable for weather data                                                                                                                                                                                                                  |
| Secrets Manager replica out of sync between regions                   | Low        | Native cross-region replication (not pipeline scripting) keeps replicas in sync automatically; add a drift-detection check on the replica's VersionId as defense in depth                                                                                                       |
| Failover drill causes unintended production impact                    | Medium     | Run drills off-peak; use a test city query, not live traffic                                                                                                                                                                                                                    |
| DynamoDB costs increase if write traffic grows                        | Low        | Monitor with Cost Anomaly Detection; revisit at 10× current scale                                                                                                                                                                                                               |
| GuardDuty findings triage doubles (2 regions, 2 detectors)            | Low        | Both detectors forward to the same security-findings SNS topic (Phase 4.1 pattern) — no new alerting pipeline needed                                                                                                                                                            |
| App pipeline's us-west-2 stage races ahead of Infra's us-west-2 stage | Medium     | The existing SSM pending-flag + EventBridge coordination (`WeatherApp-PipeSplit-ImplePlan-V1.md` §B) already gates on the Infra pipeline reaching a terminal state — confirm during Phase 2 activity #7 that this holds per-region, not just per-pipeline, before relying on it |
| --------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |

---

## 10. Success Metrics

| ------------------------------ | ------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| Metric                         | Target       | How to Measure                                                                                                                |
| ------------------------------ | ------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| Failover time                  | < 90 seconds | Route 53 health check → DNS TTL timer during failover drill                                                                   |
| RPO                            | < 1 second   | DynamoDB Global Tables replication lag CloudWatch metric                                                                      |
| Zero data loss during failover | Required     | Verify cache entries in us-west-2 match us-east-1 after drill                                                                 |
| Automatic failback             | Required     | No manual DNS change needed when primary region recovers                                                                      |
| Monthly cost increase          | < $6.00      | AWS Cost Explorer — compare 30-day spend before and after; ~$4.81 expected, budgeted with headroom for the GuardDuty detector |
| ------------------------------ | ------------ | ----------------------------------------------------------------------------------------------------------------------------- |

---

*Related documents:*
- [WeatherApp-ImproveDeploys-Plan-V2.md](../WeatherApp-ImproveDeploys-Plan-V2.md) — reliability, security, and observability improvements (supersedes the deleted WeatherApp-Improvements-V1.md referenced by earlier drafts of this document)
- [aws-WeatherApp-resources.md](../aws-WeatherApp-resources.md) — full AWS resource inventory
- [WeatherApp-runbook.md](../WeatherApp-runbook.md) — operational procedures, including the Phase 4.1 audit-stack pattern this document's GuardDuty/CloudTrail cost estimates are based on
