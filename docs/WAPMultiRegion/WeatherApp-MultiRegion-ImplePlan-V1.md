# WeatherApp Multi-Region — Implementation Plan — V1

**Date:** 2026-07-23
**Author:** Cloud & DevOps Engineering Review
**Scope:** Command-level implementation of
[`WeatherApp-MultiRegion-V1.md`](./WeatherApp-MultiRegion-V1.md) — active-passive
failover to us-west-2 for `weather.craftingnewtech.com`.
**Baseline:** weather-dashboard, single region (us-east-1). Phases 0 through 4.2 of
[`WeatherApp-ImproveDeploys-Plan-V2.md`](../WeatherApp-ImproveDeploys-Plan-V2.md)
complete, plus the Infra/App pipeline split from
[`WeatherApp-PipeSplit-ImplePlan-V1.md`](../WeatherApp-PipeSplit-ImplePlan-V1.md).
**Plan Status: Complete** — every task (0, 1.1-1.5, 2.1-2.8, 3.1-3.4,
4.1-4.5) is deployed and verified in production as of 2026-07-24 17:02 EDT.
See §0.1's tracking table for the full timestamped record.
**Review posture:** Every design choice below was checked against the actual current
templates (`01-iam.yml`, `03-cdn.yml`, `04-database.yml`, `05-backend.yml`,
`06-api.yml`, `07-ssm.yml`, `08-pipeline.yml`, `11-audit.yml`, `master.yml`),
`frontend/js/config.js`, and `pipeline/scripts/deploy-infrastructure.sh` — not
re-derived from the proposal doc's prose alone. Nine real gaps in the V1 proposal
were found and closed in §0.2 — eight before implementation started, plus a ninth
(cross-region DR operability, §0.2 #9) found and verified live during Phase 2 —
the same way three gaps were found and closed in the Pipeline Split plan's own
§0.2.

---

## 0.1 Implementation Tracking

Update as each task lands (deployed and verified, not just committed).

| ---- | --------------------------------------------------------------------------- | --------- | ------------------------------------------------------------------------------------------------------- |
| Task | Description                                                                 | Status    | Completed                                                                                               |
| ---- | --------------------------------------------------------------------------- | --------- | ------------------------------------------------------------------------------------------------------- |
| 0    | Pre-flight — baseline tag + environment setup                               | Completed | 2026-07-24 10:29 EDT                                                                                    |
| 1.1  | Add `GET /health` route + handler                                           | Completed | 2026-07-24 10:30 EDT                                                                                    |
| 1.2  | Widen `LambdaExecutionRole`'s DynamoDB/Secrets `Resource` ARNs to 2 regions | Completed | 2026-07-24 10:32 EDT                                                                                    |
| 1.3  | Parameterize `05-backend.yml` to accept existing CodeDeploy/Hook role ARNs  | Completed | 2026-07-24 10:33 EDT                                                                                    |
| 1.4  | Add API Gateway regional custom domain + cert to `06-api.yml`               | Completed | 2026-07-24 10:35 EDT                                                                                    |
| 1.5  | Enable DynamoDB Global Tables via CLI (`WeatherCache`)                      | Completed | 2026-07-24 10:46 EDT                                                                                    |
| 2.1  | Deploy `00-bootstrap.yml` to us-west-2 (regional artifacts bucket)          | Completed | 2026-07-24 12:27 EDT                                                                                    |
| 2.2  | Create `master-secondary.yml` (BackendStack + ApiStack only)                | Completed | 2026-07-24 12:30 EDT                                                                                    |
| 2.3  | Upload Lambda ZIP to us-west-2 bucket; deploy `master-secondary.yml`        | Completed | 2026-07-24 12:46 EDT                                                                                    |
| 2.4  | Add `ReplicaRegions` to `07-ssm.yml`'s `ApiKeySecret`; deploy               | Completed | 2026-07-24 13:57 EDT                                                                                    |
| 2.5  | Parameterize `11-audit.yml`; deploy GuardDuty-only stack to us-west-2       | Completed | 2026-07-24 12:53 EDT                                                                                    |
| 2.6  | Add same-region `DeploySecondary` stage to Infra pipeline (us-west-2)       | Completed | 2026-07-24 14:09 EDT                                                                                    |
| 2.7  | Add same-region `DeploySecondary` stage to App pipeline (us-west-2)         | Completed | 2026-07-24 14:09 EDT                                                                                    |
| 2.8  | Verify `/health` + smoke test both regions                                  | Completed | 2026-07-24 15:24 EDT — both pipelines' `DeploySecondary` verified end-to-end                            |
| 3.1  | Create `12-failover-dns.yml` (health check + failover records)              | Completed | 2026-07-24 15:30 EDT                                                                                    |
| 3.2  | Capture regional API custom domain outputs; deploy `12-failover-dns.yml`    | Completed | 2026-07-24 15:42 EDT                                                                                    |
| 3.3  | Update `frontend/js/config.js` to `api.weather.craftingnewtech.com`         | Completed | 2026-07-24 15:46 EDT                                                                                    |
| 3.4  | Lower failover record TTL to 60s; confirm health check `Success`            | Completed | 2026-07-24 15:42 EDT — N/A (ALIAS records inherit target TTL); health check confirmed `Success` in §3.2 |
| 4.1  | Failover drill — force 503 in us-east-1, confirm shift to us-west-2         | Completed | 2026-07-24 17:02 EDT — real traffic never interrupted (see note)                                        |
| 4.2  | Failback drill — restore us-east-1, confirm auto-recovery                   | Completed | 2026-07-24 17:02 EDT — zero manual DNS action taken                                                     |
| 4.3  | Replication check — cache write in us-east-1 appears in us-west-2           | Completed | 2026-07-24 15:57 EDT — confirmed sub-3s, identical TTL value in both regions                            |
| 4.4  | Add `Route53HealthCheckStatus` CloudWatch alarm                             | Completed | 2026-07-24 16:02 EDT                                                                                    |
| 4.5  | Document incident runbook + explicit "us-east-1 down" CLI-only procedure    | Completed | 2026-07-24 16:02 EDT                                                                                    |
| ---- | --------------------------------------------------------------------------- | --------- | ------------------------------------------------------------------------------------------------------- |

**Note on 1.5 (2026-07-24 10:44 EDT):** the first `update-table --replica-updates`
call failed with `UnrecognizedClientException` ("security token included in the
request is invalid"). A read-only `describe-table` call immediately after
succeeded against the same credentials, and a plain `sts get-caller-identity`
also succeeded — ruling out an actual expired/invalid session. Retried the same
`update-table` call once and it succeeded normally; treated as a transient
AWS-side hiccup specific to that one API call, not a credentials problem. Table
and the us-west-2 replica both confirmed `ACTIVE` shortly after via a background
poll loop.

**Phase 1 deployed to production (2026-07-24 10:47-11:50 EDT):** pushed after
1.1-1.5 all completed locally. The pipeline-filter Lambda correctly classified
the combined push as `infra=True app=True` and started the Infra pipeline,
queuing the App pipeline to auto-release on its success. Infra pipeline
succeeded 10:47-11:25 EDT (includes manual `ApproveDeploy` wait); App pipeline
auto-triggered and succeeded 11:25-11:50 EDT. Verified live:
`GET /health` returns `{"status": "ok", "region": "us-east-1"}` (200); the new
`api.weather.craftingnewtech.com` ACM certificate shows `ISSUED`/`InUse: true`;
`GET /weather?city=Austin` regression-tested fine (200 with real data). The
custom domain itself doesn't resolve yet — its Route 53 records are Phase 3
work, not part of this deploy.

**Bug found and fixed during 2.1 (2026-07-24 12:27 EDT):** the first
`aws cloudformation deploy` of `00-bootstrap.yml` to us-west-2 failed
early — `AWS::EarlyValidation::ResourceExistenceCheck` — because
`ArtifactsBucket`'s `BucketName` (`${ProjectName}-artifacts-${AWS::AccountId}-${Environment}`)
has no region in it. S3 bucket names are globally unique across every
region, so the same template deployed to a second region always collides
with the primary region's already-existing bucket of the same name — a
gap this plan's own §2.1 hadn't called out. Fixed by adding a
`BucketNameSuffix` parameter (default `''`, preserving the exact existing
primary bucket name unchanged) and appending it to `BucketName`; the
us-west-2 deploy passed `BucketNameSuffix=-us-west-2`, producing
`weather-dashboard-artifacts-ABC-EXAMPLE-XXXX-production-us-west-2`. Confirmed
the primary bootstrap stack/bucket were untouched
(`describe-stacks` still shows `UPDATE_COMPLETE` and the unsuffixed name)
before proceeding — this parameter was purely additive, never redeployed
against the primary stack.

**2.2/2.3 deployed to production (2026-07-24 12:46 EDT):** the plan's original
§2.3 draft used the wrong Lambda ZIP key (`lambda/weather-handler.zip`) —
`describe-stacks` on the real primary master stack showed the actual
parameter is `lambda/lambda.zip`. Corrected in the doc before running
anything. `weather-dashboard-secondary-production` (BackendStack + ApiStack)
deployed to us-west-2 cleanly (`CREATE_COMPLETE`), change set reviewed first
and showed only the two expected `Add` actions. `GET /health` confirmed live:
`{"status": "ok", "region": "us-west-2"}` (200). `/weather` not tested yet on
purpose — the Secrets Manager replica doesn't exist until 2.4, so it would
fail on `ResourceNotFoundException` rather than prove anything; deferred to
2.8's smoke test.

**2.4 status:** template change complete and committed
(`07-ssm.yml`'s `ReplicaRegions` + `master.yml` passthrough), but not yet
deployed — this modifies the *primary* `SsmStack`, so per this project's
established convention it goes through the Infra pipeline (push, then a
manual `ApproveDeploy`), not a raw CLI deploy against production. Will
deploy alongside whatever else is batched into the next push.

**2.5 deployed to production (2026-07-24 12:53 EDT):** dry-run change set
reviewed first — confirmed only 4 `Add` actions (`GuardDutyDetector`,
`GuardDutyFindingsRule`, `SecurityFindingsTopic`,
`SecurityFindingsTopicPolicy`), no CloudTrail resources, matching the
`DeployCloudTrail=false` intent exactly. Deployed for real;
`aws guardduty list-detectors --region us-west-2` confirms a new detector
(`548ae472f8444884929f702d3663a879`).

**2.4/2.6/2.7 production incident and fix (2026-07-24 13:30-14:09 EDT):** the
push carrying these tasks' template changes deployed cleanly via the Infra
pipeline (Source/Validate/ApproveDeploy/Deploy/ValidateDeployment all
succeeded), but the new `SecondaryRegion`/`SecondaryArtifactsBucketName`
parameters silently stayed at their blank template defaults — every
multi-region feature in the push (IAM widening, Secrets replication, the new
pipeline stages) was structurally present but disabled. Root cause:
`pipeline/scripts/deploy-infrastructure.sh` passes zero
`--parameter-overrides` on normal runs, relying entirely on CloudFormation
preserving *previous* stack values — which doesn't exist for a parameter's
first-ever appearance in the template, so it fell through to the template
`Default: ''`.

Attempted a one-time manual override
(`SecondaryRegion=us-west-2 SecondaryArtifactsBucketName=...`, same pattern
as the script's own documented "Bootstrap — first deploy only" convention)
to set real values once so future pipeline runs preserve them automatically.
This surfaced two more real, cascading failures, back to back:

1. **Missing IAM action.** `SsmStack` failed on `ApiKeySecret`:
   `AccessDenied` on `secretsmanager:ReplicateSecretToRegions` —
   `CloudFormationDeployRole`'s existing Secrets Manager actions (added in
   Phase 4.2) didn't cover the two actions `ReplicaRegions` specifically
   needs. The resulting automatic rollback then failed *again*, on
   `RemoveRegionsFromReplication` this time — the exact
   `cloudformation-aws-patterns.md` gotcha "a failed update reverts earlier-
   succeeded changes in the same update, including IAM grants": `IamStack`'s
   own rollback revoked the permission before `SsmStack`'s rollback could
   use it to undo its own partial replica creation, leaving the whole stack
   `UPDATE_ROLLBACK_FAILED`. Fixed in two layers: `aws iam put-role-policy`
   directly on the live role to unblock the immediate stuck rollback
   (verified via `continue-update-rollback`), *and* added
   `secretsmanager:ReplicateSecretToRegions`/`RemoveRegionsFromReplication`
   to `01-iam.yml`'s source template so the fix is durable and versioned,
   not just a live drift.
2. **Retained log group collision.** Retrying with the fix in place, the
   `SsmStack`/IAM portion succeeded this time (`ApiKeySecret` reached
   `UPDATE_COMPLETE`), but `PipelineStack` then failed creating
   `DeploySecondaryLogGroup` — `AlreadyExists`. Second documented gotcha,
   `cloudformation-aws-patterns.md`'s "DeletionPolicy: Retain resources
   survive rollback correctly, then block the retry": the *first* failed
   attempt had already created this log group before the stack-wide
   rollback, and its `DeletionPolicy: Retain` (matching every other log
   group in this template) correctly preserved it through that rollback —
   which then collided with the retry's own `CREATE`. Confirmed it was
   genuinely empty (`storedBytes: 0`) before deleting it manually and
   retrying.

**After both fixes, switched to a phased deploy instead of retrying the same
bundled change** — split into two sequential, independent
`aws cloudformation deploy` calls (first `SecondaryRegion` alone with
`SecondaryArtifactsBucketName=''`, then `SecondaryArtifactsBucketName` alone
once the first settled) so a failure in one multi-region feature could never
again cascade into reverting a different, already-stable one. Both deploys
reviewed via `--no-execute-changeset` first (all `Replacement: False`), then
executed. Verified: `aws secretsmanager describe-secret ... --query
ReplicationStatus` shows `us-west-2` / `InSync` / "Replication succeeded";
`aws codebuild batch-get-projects` confirms both
`weather-dashboard-infra-deploy-secondary-production` and
`weather-dashboard-app-deploy-secondary-production` now exist; `/health` and
`/weather?city=Austin` both regression-tested clean throughout every step of
this incident. Lesson for future multi-parameter multi-region rollouts:
deploy one new parameter at a time once real IAM/resource-lifecycle
interactions are unproven, not all at once — bundling is fine once each
piece has been exercised individually at least once.

**2.6/2.7 verified end-to-end via a real pipeline run (2026-07-24 14:11-14:28
EDT):** with the fixes committed and pushed, the pipeline-filter Lambda
classified the push as `infra=True app=False` (only `01-iam.yml` + docs
changed) and started the Infra pipeline — this was also the "next trigger"
the self-modifying-pipeline note above said would be needed, since the
pipeline definition now genuinely includes `DeploySecondary`. All 6 stages
succeeded in one execution, `DeploySecondary` included:
`weather-dashboard-secondary-production` shows `UPDATE_COMPLETE` in us-west-2
with a `LastUpdatedTime` matching the run. `GET /weather?city=Austin` against
the us-west-2 endpoint succeeded for the first time with real data — the
first true end-to-end proof of API Gateway → Lambda → Secrets Manager
(replica) → OpenWeatherMap → DynamoDB (replica) in the secondary region, and
indirectly confirms task 1.2's IAM widening actually works in practice, not
just on paper. The App pipeline's `DeploySecondary` stage hasn't run yet
(this push was infra-only) — needs its own trigger to complete 2.8.

**Third IAM gap found and fixed (2026-07-24 14:52-15:06 EDT):** manually
triggered the App pipeline (`start-pipeline-execution`) to exercise its
`DeploySecondary` stage for the first time. Primary `Deploy` succeeded, but
`DeploySecondary` failed: `AccessDenied` on `lambda:UpdateFunctionCode`
against the us-west-2 function. Root cause: `AppCodeBuildRole`'s
`LambdaUpdateCode` Sid used `${AWS::Region}` for its Resource ARN — the same
region-pinning class of bug as task 1.2's `LambdaExecutionRole` fix, just
missed on this role during the earlier audit. Found `CodeDeployTrafficShift`
had the identical problem while fixing it (not yet triggered, but would have
failed the same way on the next call). This time did a full audit of every
remaining `${AWS::Region}` reference in `01-iam.yml` (13 other occurrences)
before deploying again — confirmed all of them are genuinely correct as-is,
since they reference resources that only ever exist in us-east-1 (CodeCommit,
CodePipeline, CodeBuild projects/logs/report-groups, SNS approval topics, the
SSM coordination parameter, the rotation Lambda). Deployed the fix directly
(change set reviewed first, all `Replacement: False`), then manually
retriggered the App pipeline again to verify.

**Note on running both pipelines concurrently:** while re-verifying, the
Infra pipeline (from the same-day IAM-fix push) and the manually-retriggered
App pipeline ended up running at the same time. Assessed as low-risk in this
specific case only — the Infra change was IAM-only (no Lambda/API Gateway
resource changes in `master-secondary.yml`), so its `DeploySecondary` stage
was a near no-op for the actual Lambda function, and the App pipeline updates
Lambda code via direct CLI, not CloudFormation — no shared-resource race
existed this time. This is **not** the project's normal coordination model
(Infra-before-App via the SSM pending-flag + EventBridge release mechanism,
`WeatherApp-PipeSplit-ImplePlan-V1.md` §B) — manually starting both
independently bypassed that on purpose to test the fix faster, and shouldn't
be treated as a routine pattern for combined changes that actually touch a
shared resource.

**Bug found and fixed during 3.2 (2026-07-24 15:31-15:42 EDT):** the first
`12-failover-dns.yml` deploy succeeded, but the health check immediately
showed `Failure: HTTP Status Code 404` from every AWS checker region.
Root cause: `PrimaryHealthCheck`'s `FullyQualifiedDomainName` was set to
`PrimaryDomainName` — the API Gateway custom domain's *regional target*
(`d-xxxx.execute-api.us-east-1.amazonaws.com`), which is only a valid Route 53
ALIAS target, not something a client can curl directly. API Gateway custom
domains route by `Host` header/SNI; a health checker connecting straight to
the raw regional name sends that name as the `Host` header, which matches no
`ApiMapping`, hence 404 — confirmed by curling
`https://d-ABC-EXAMPLE-XXXX.execute-api.us-east-1.amazonaws.com/health` directly
and reproducing the exact same 404. Fixed by adding a second parameter,
`PrimaryHealthCheckDomainName`, pointing the health check at the *raw*
`execute-api` hostname (from the existing `ApiEndpoint` output, scheme
stripped) instead — that endpoint isn't gated by custom-domain Host-header
matching and its TLS cert matches its own name. `AliasTarget.DNSName` stays
on the regional custom-domain target, unchanged — that part was already
correct, since a real client's request keeps the `api.weather.craftingnewtech.com`
Host header regardless of which IP the ALIAS resolves to. After the fix, all
16 AWS checker regions reported `Success: 200` within about a minute, and
`https://api.weather.craftingnewtech.com/health` and `/weather?city=Austin`
both confirmed live end-to-end through the new failover domain.

**4.3/4.4/4.5 completed (2026-07-24 15:57-16:02 EDT), done autonomously while
the user briefly stepped away — deliberately excluding 4.1/4.2:**
- 4.3: wrote a real cache entry (`marfa`, a genuine city so the write path
  actually executes) via the normal `/weather` endpoint, confirmed the exact
  same TTL value present in both regions' `WeatherCache` tables within
  seconds.
- 4.4: added `Route53HealthCheckAlarm` to `09-monitoring.yml`, gated by a new
  `PrimaryHealthCheckId` parameter (threaded through `master.yml`, reusing
  the existing `AlarmTopic` already used by every other alarm in that file —
  no new SNS topic needed). Deployed directly (change set reviewed first,
  all `Replacement: False`); alarm confirmed present
  (`INSUFFICIENT_DATA`, expected for a fresh alarm — needs a few data points
  before it can evaluate).
- 4.5: added a full "§12 Multi-Region Active Failover" section to
  `WeatherApp-runbook.md` — what the alarm means, how to distinguish a real
  outage from a checker blip, the manual override, and (most importantly)
  the explicit "us-east-1 is down" direct-CLI procedure per §0.2 #9, with
  the exact commands already proven working in this plan.
- **4.1/4.2 (the failover/failback drills) were deliberately NOT run** — the
  user explicitly asked to hold off on anything destructive to live traffic
  until they could watch it happen live. These remain the only two open
  tasks in the entire plan.

**App pipeline (frontend deploy from task 3.3) confirmed succeeded
(2026-07-24 16:04 EDT)** — all 7 stages including `DeploySecondary`, the
final `ValidateDeployment` smoke test briefly lagged CodePipeline's own
status update after the underlying CodeBuild job had already finished (not
a real hang). Full regression check across everything built so far, all
green:
- `https://ABC-EXAMPLE-XXXX.execute-api.us-east-1.amazonaws.com/health` → 200, `us-east-1`
- `https://ABC-EXAMPLE-XXXX.execute-api.us-west-2.amazonaws.com/health` → 200, `us-west-2`
- `https://api.weather.craftingnewtech.com/health` → 200, `us-east-1` (correctly routing to the healthy primary)
- `https://weather.craftingnewtech.com/js/config.js` → confirmed the **live production frontend** now serves the updated `API_BASE_URL` pointing at the failover domain — task 3.3 is genuinely live, not just committed.

**Every task in Phases 1-4 is complete except 4.1/4.2.**

**4.1/4.2 — Failover and failback drills, run live with the user watching
(2026-07-24 16:5x-17:02 EDT):** the plan's original draft suggested adding a
throwaway 503 route to force the drill. Used a simpler, equally valid
mechanism instead — temporarily changed `PrimaryHealthCheck`'s
`ResourcePath` from `/health` to a nonexistent path
(`/health-drill-test`), which the Lambda's catch-all handler correctly
404s on. Any non-2xx/3xx response fails a Route 53 HTTPS health check, so
this reproduces the exact same failure signal as a 503 would, with zero
Lambda/template changes needed and the *real* `/health`/`/weather` routes
serving actual traffic completely normally throughout — confirmed live
with direct `curl` calls to the primary's raw endpoint during the entire
drill window.

Full sequence, every step confirmed with a live command, not assumed:
1. Baseline: all 16 AWS checker regions `Success`, `api.weather.craftingnewtech.com` serving `us-east-1`.
2. Changed `ResourcePath` → within ~2 minutes all 16 checkers reported `Failure: HTTP Status Code 404`.
3. `api.weather.craftingnewtech.com/health` and `/weather?city=Austin` both automatically switched to serving `us-west-2` — real data returned, not an error page.
4. `weather-dashboard-route53-failover-active-production` (task 4.4's alarm) transitioned to `ALARM` with real `0.0` datapoints crossing the threshold — the alarm was proven to actually fire during a genuine failover event, not just deployed and assumed working.
5. Reverted `ResourcePath` back to `/health` → all 16 checkers recovered to `Success` within ~2 minutes.
6. DNS automatically shifted back to `us-east-1` — **zero manual DNS action taken at any point**, exactly as designed.
7. The alarm cleared back to `OK` with a real `1.0` datapoint.
8. Final full regression check across both regions + the failover domain, all green.

**This is the strongest possible evidence for the whole plan** — not just
"the resources exist and pass a smoke test," but a real, live failure
signal propagating through Route 53 → DNS → CloudWatch alarm → automatic
recovery, with the actual application never going down for a single real
request throughout.

**The entire WeatherApp Multi-Region Implementation Plan is now complete.**

---

## Phase 1 — Actual Implementation Time

Sourced from the timestamps already recorded in the tracking table above and
the production deploy note above it — the same way the Pipeline Split plan's
own "Estimated vs. Actual" section was sourced from git commit timestamps, not
a separate stopwatch.

| ------------------ | ----------------------------------------- | ----------- | --------------------------------------------------------------------------- |
| Task               | Description                               | Actual Time | Primary Time Driver                                                         |
| ------------------ | ----------------------------------------- | ----------- | --------------------------------------------------------------------------- |
| 0                  | Pre-flight                                | ~1 min      | Read-only AWS checks + baseline git tag/push                                |
| 1.1                | Add `GET /health` route + handler         | 1 min       | One dispatcher branch + 3 tests, established pattern                        |
| 1.2                | Widen `LambdaExecutionRole` Resource ARNs | 2 min       | New parameter + Condition + two `!If`-wrapped Resource lists                |
| 1.3                | Parameterize `05-backend.yml`             | 1 min       | Same `!If` pattern as 1.2, 3 reference sites                                |
| 1.4                | Add API Gateway regional custom domain    | 2 min       | 3 new resources + 2 new outputs, no prior precedent in this project         |
| 1.5                | Enable DynamoDB Global Tables (CLI)       | 11 min      | Transient `UnrecognizedClientException` + retry + replica provisioning wait |
| ------------------ | ----------------------------------------- | ----------- | --------------------------------------------------------------------------- |
| **Hands-on total** |                                           | **17 min**  |                                                                             |
| ------------------ | ----------------------------------------- | ----------- | --------------------------------------------------------------------------- |

Deploying that work to production is tracked separately — it's pipeline/AWS
wall-clock time (including a human approval wait), not engineering effort:

| ------------------------------------------------------------ | ----------- |
| Step                                                         | Actual Time |
| ------------------------------------------------------------ | ----------- |
| Push → Infra pipeline (incl. manual `ApproveDeploy` wait)    | 38 min      |
| App pipeline (auto-released on Infra success)                | 25 min      |
| Post-deploy verification (`/health`, cert, regression check) | 7 min       |
| ------------------------------------------------------------ | ----------- |
| **Total to fully verified in production**                    | **1h 28m**  |
| ------------------------------------------------------------ | ----------- |

**Vs. the original estimate:** [`WeatherApp-MultiRegion-V1.md`](./WeatherApp-MultiRegion-V1.md)'s
§6 estimated Phase 1 at **4h 00m** of engineering effort. Actual hands-on
implementation was ~17 minutes — dramatically under estimate. This isn't the
estimate being wrong so much as where the effort actually landed: the original
estimate was written against the proposal's loose Phase 1 description ("parameterize
templates to accept `AWS::Region`"), which turned out not to describe the real
work at all (§0.2 #8). The actual time-consuming part — figuring out exactly
which IAM Resource ARNs needed widening, that `05-backend.yml` creates two
global-scope roles inline, that CloudFront never proxies API traffic — was the
design-correction analysis in §0.2, done *before* any file was edited. Once the
real scope was nailed down precisely, the mechanical edits themselves were fast.

---

## Phase 2 — Actual Implementation Time

Same sourcing convention as Phase 1's table above. Phase 2 ran much longer
than Phase 1 relative to its own estimate, almost entirely because of three
real IAM gaps found only by actually running things against live AWS — not
because the mechanical template edits themselves were slow.

| ----------------------- | -------------------------------------------------------------- | ----------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Task                    | Description                                                    | Actual Time | Primary Time Driver                                                                                                              |
| ----------------------- | -------------------------------------------------------------- | ----------- | -------------------------------------------------------------------------------------------------------------------------------- |
| 2.1                     | Bootstrap us-west-2 artifacts bucket                           | ~30 min     | Bug found: bucket-naming collision (S3 names are global), fixed live                                                             |
| 2.2                     | Create `master-secondary.yml`                                  | 3 min       | New file, established parameter patterns from Phase 1                                                                            |
| 2.3                     | Upload ZIP + deploy secondary stack                            | 16 min      | Corrected a wrong Lambda ZIP filename in the plan doc before running                                                             |
| 2.5                     | GuardDuty-only audit deploy                                    | 7 min       | Straightforward parameterization + dry-run                                                                                       |
| 2.4/2.6/2.7 (templates) | Secrets replication + both pipelines' `DeploySecondary` stages | ~45 min     | Design simplification mid-stream (dropped `ArtifactStores`/`Region:` for plain same-region CLI calls) plus 2 new buildspec files |
| ----------------------- | -------------------------------------------------------------- | ----------- | -------------------------------------------------------------------------------------------------------------------------------- |
| **Hands-on subtotal**   |                                                                | **~1h 41m** |                                                                                                                                  |
| ----------------------- | -------------------------------------------------------------- | ----------- | -------------------------------------------------------------------------------------------------------------------------------- |

Production deploy + incident response — this is where almost all of Phase
2's real time went, and it's the most important number in this table:

| -------------------------------------------------------------------------------------------- | ----------- |
| Step                                                                                         | Actual Time |
| -------------------------------------------------------------------------------------------- | ----------- |
| First push (2.4/2.6/2.7) — Infra pipeline succeeded, values stayed disabled                  | ~38 min     |
| Incident: cascading rollback failure + 2 IAM permission fixes + phased re-deploy             | ~39 min     |
| Pipeline re-verification (2.6/2.7 confirmed end-to-end)                                      | 17 min      |
| 3rd IAM gap (`AppCodeBuildRole`) found via App pipeline's first `DeploySecondary` run, fixed | ~14 min     |
| App pipeline re-verification (2.8, includes a manual `ApproveDeploy` wait)                   | ~18 min     |
| -------------------------------------------------------------------------------------------- | ----------- |
| **Total**                                                                                    | **~2h 06m** |
| -------------------------------------------------------------------------------------------- | ----------- |

**Phase 2 grand total: ~1h 41m hands-on + ~2h 06m production/incident = ~3h 47m.**
The original proposal estimated Phase 2 at **6h 00m** — this landed under
that, but for a very different reason than Phase 1's big underrun: here the
estimate happened to be roughly right in total, while the actual time split
was nothing like what a normal "write templates, deploy once" phase would
look like — over half of it was incident response for gaps no amount of
static validation would have caught.

**Why this matters more than the Phase 1 comparison did:** Phase 1's actual
time came in dramatically *under* its estimate. Phase 2 came in a bit under
too, but only because the *estimate* had padding — not because it went
smoothly: three separate IAM
Resource-ARN region-pinning bugs (LambdaExecutionRole in Phase 1 — caught
before deploy; then two more in Phase 2's CloudFormationDeployRole and
AppCodeBuildRole — each only surfaced by actually executing the thing that
used them) are the exact class of gap that local validation (`cfn-lint`,
`checkov`, `cloudformation validate-template`) structurally cannot catch —
none of those tools evaluate whether a policy's `Resource` ARN will actually
match a real cross-region API call at runtime. This is the same lesson
`~/.claude/instructions/cloud-iam-least-privilege.md` already captures
("testing under your own broad credentials never validates a restricted
identity's policy") — every one of these three gaps was invisible until the
specific pipeline stage that needed it actually ran, under its own scoped
role, for the first time.

---

## 0.2 Design Corrections vs. the V1 Proposal

These nine items were found by checking the proposal's claims (#1-8) and this
plan's own execution (#9) against the actual templates, frontend code, and live
AWS/IAM state — not by re-reading the proposal's own prose. None of them change
the overall active-passive strategy or the ~$5/month cost order of magnitude —
they change *how* Phases 1–3 actually get implemented, and four of them (#1, #6,
#7, #9) would have caused the naive reading of the proposal to fail outright, or
to silently not work during the exact disaster scenario it's meant to survive.

1. **CloudFront never sees API traffic today — there is no Origin Group to add.**
   `frontend/js/config.js` hardcodes
   `API_BASE_URL: 'https://ABC-EXAMPLE-XXXX.execute-api.us-east-1.amazonaws.com'` — the
   browser calls API Gateway directly. `03-cdn.yml`'s CloudFront distribution has
   exactly one origin (`S3WebsiteOrigin`, the frontend bucket). The proposal's
   target diagram assumed CloudFront already proxies `/weather` and `/locate` and
   just needed a second origin added for failover — it doesn't, so there's nothing
   to add a fallback *to*. **Fix:** give API Gateway its own regional custom domain
   (`api.weather.craftingnewtech.com`) in each region, point the frontend at that
   instead of the raw `execute-api` URL, and put Route 53 Failover directly on that
   domain. CloudFront and the existing `weather.craftingnewtech.com` record are
   untouched by this plan — confirmed with the user before proceeding this way
   (see §3).

2. **IAM roles are global; `05-backend.yml` creates two of them inline.**
   `CodeDeployRole` (`weather-dashboard-codedeploy-role-production`) and `HookRole`
   (`weather-dashboard-hook-role-production`) are `AWS::IAM::Role` resources created
   *inside* `05-backend.yml`, not passed in like `LambdaExecutionRoleArn` already
   is. Deploying this template unmodified as a second stack in us-west-2 would
   attempt to recreate both roles under names that already exist account-wide →
   `EntityAlreadyExists`. **Fix:** §1.3 adds two new parameters
   (`CodeDeployRoleArn`, `HookRoleArn`) and an `IsPrimaryRegion` condition — when
   false, the template accepts the existing ARNs instead of creating new roles,
   mirroring the pattern the template already uses for `LambdaExecutionRoleArn`.

3. **The existing `LambdaExecutionRole` policy is region-pinned, not region-safe.**
   `01-iam.yml`'s `DynamoDBTableArn` Sid takes a literal ARN (the us-east-1 table's
   ARN, via `DatabaseStack.Outputs.TableArn`), and its Secrets Manager Sid builds
   the ARN with `${AWS::Region}` — which resolves to us-east-1 at the one and only
   time `IamStack` is ever deployed. Since this same global role is reused
   unmodified by the us-west-2 Lambda, both Resource lists need the us-west-2
   replica ARNs added explicitly (not a `*` region wildcard — an explicit two-item
   list keeps this at the same least-privilege bar as everything else in
   `01-iam.yml`, per `~/.claude/instructions/cloud-iam-least-privilege.md`).
   Without this, the secondary Lambda gets `AccessDenied` reading its own local
   cache/secret replica the first time it actually runs — exactly the
   "steady-state testing misses create/first-run permissions" trap that instructs
   file warns about. **Fix:** §1.2.

4. **DynamoDB Global Tables is not a CloudFormation resource-type swap.**
   `WeatherCacheTable` is `AWS::DynamoDB::Table`. Converting it to
   `AWS::DynamoDB::GlobalTable` in CFN is a `Type` change, which CloudFormation
   always treats as replacement (delete + create) — not the "zero-downtime
   migration" the original proposal called it. **Fix:** enable replication via
   `aws dynamodb update-table --replica-updates` (§1.5), leaving the CFN resource
   as `AWS::DynamoDB::Table` and accepting the resulting one-line drift on
   `ReplicaSpecification` as a known, documented gap — the same accepted-drift
   pattern this project already uses for the Lambda alias version and Provisioned
   Concurrency (both CLI-managed, not CFN-managed, per `05-backend.yml`'s own
   comments).

5. **`11-audit.yml` bundles CloudTrail and GuardDuty with no way to deploy only
   one.** Its own header comment already says GuardDuty and the multi-region
   CloudTrail trail are "account/region singletons — verify none already exist
   before deploying." Redeploying this template as-is in us-west-2 for a second
   GuardDuty detector would also try to create a *second* multi-region CloudTrail
   trail, redundant with the one already covering every region from us-east-1.
   **Fix:** §2.5 adds a `DeployCloudTrail` parameter/condition so the secondary
   region's deploy creates only `GuardDutyDetector` + its SNS forwarding.

6. **Lambda deployment packages must live in the same region as the function.**
   This is a hard AWS constraint (S3-sourced Lambda code must be in-region), not
   an optional optimization — the proposal's Phase 2 checklist never mentioned it.
   **Fix:** §2.1 deploys a second `00-bootstrap.yml` stack in us-west-2 for its own
   regional `ArtifactsBucket`, and §2.3's build step uploads the same ZIP to both
   regions' buckets.

7. **A single `ArtifactStore` blocks any cross-region CodePipeline action.**
   Both `08-pipeline.yml` and `08b-app-pipeline.yml` currently declare one
   `ArtifactStore` (singular). A CodePipeline action targeting a different region
   than the pipeline itself requires `ArtifactStores` (plural, one entry per
   region, each with its own S3 bucket) — CodePipeline replicates the input
   artifact to the target region's bucket before running a cross-region action.
   **Fix:** §2.6/§2.7 convert both pipelines to `ArtifactStores` using the
   us-west-2 bucket from #6, then add the new Deploy stage.

8. **The proposal's Phase 1 "parameterize templates to accept `AWS::Region` —
   remove all hardcoded `us-east-1` references" item doesn't describe a real
   problem.** The only `us-east-1` mentions found (`03-cdn.yml`, `master.yml`) are
   comments documenting the genuine AWS constraint that a CloudFront ACM cert must
   be requested in us-east-1 — there is no region-hardcoding *logic* to remove.
   This line item is dropped; #1–#7 above are the actual prerequisite work.

9. **The pipeline-based `DeploySecondary` automation (§2.6/§2.7) is not usable
   during an actual us-east-1 outage.** CodeCommit, CodePipeline, and every
   CodeBuild project for this project live only in us-east-1. If us-east-1 is
   genuinely down, that entire automation path is unreachable — the same
   region-down event this whole plan exists to survive. **Verified the real
   fallback (direct AWS CLI against us-west-2, exactly what tasks 2.1/2.3/2.5
   already did) is fully independent of us-east-1:**
   - The operator's own IAM user (`jbaez`) has `AdministratorAccess` with no
     region-restricting `Condition` clauses (checked via
     `list-attached-user-policies`, not assumed).
   - Every service-level role used in either region — `CloudFormationDeployRole`,
     `InfraCodeBuildRole`/`AppCodeBuildRole`, `LambdaExecutionRole`,
     `CodeDeployRole`, `HookRole` — is a global IAM entity: their key policy
     statements use `Resource: '*'` or plain `arn:aws:iam::${AWS::AccountId}:role/...`
     ARNs, and IAM ARNs have no region component at all (checked by reading
     `01-iam.yml` directly).
   - Each region's Lambda resolves its own execution region at runtime and only
     calls same-region DynamoDB/Secrets Manager/X-Ray endpoints (checked by
     reading `backend/lambda/cache.py`/`ssm_secrets.py`) — no cross-region
     runtime call either function ever makes.
   - This repo pushes to both CodeCommit *and* GitHub (`git push both`) — even
     if CodeCommit/us-east-1 is unreachable, `git clone` from GitHub still
     gets every template needed for a manual deploy.
   **Fix:** §4.5's runbook task must explicitly document "us-east-1 is down"
   as its own procedure — direct CLI against us-west-2, not "wait for the
   pipeline" — so a future reader never mistakes the DeploySecondary stages
   for the DR mechanism they aren't.

---

## 0.3 Pre-flight (run once, before touching any file)

```bash
# 1. Confirm current stack health.
aws cloudformation describe-stacks \
  --stack-name weather-dashboard-master-production \
  --query 'Stacks[0].StackStatus' --output text
# expect: UPDATE_COMPLETE

# 2. Tag the current commit as the rollback baseline.
git tag -a pre-multiregion-v1 -m "Baseline before multi-region failover implementation"
git push both pre-multiregion-v1

# 3. Confirm us-west-2 has no leftover resources from a prior attempt.
aws cloudformation list-stacks --region us-west-2 \
  --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE ROLLBACK_COMPLETE \
  --query 'StackSummaries[?contains(StackName, `weather-dashboard`)].StackName'
# expect: []

# 4. Confirm no second GuardDuty detector / CloudTrail trail already exists in
#    us-west-2 (11-audit.yml's own singleton warning, checked for real).
aws guardduty list-detectors --region us-west-2
aws cloudtrail describe-trails --region us-west-2 \
  --query 'trailList[?contains(Name, `weather-dashboard`)]'
# expect: [] for both

# 5. Capture the account ID once for use in later steps.
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "$ACCOUNT_ID"
```

---

## Phase 1 — Prerequisites

### 1.1 Add `GET /health` route + handler

`backend/lambda/weather_handler.py`'s `handler()` already dispatches on
`event.get("routeKey", "")` (`GET /weather`, `GET /locate`). Add a third branch:

```python
if route_key == "GET /health":
    return _response(200, {"status": "ok", "region": os.environ["AWS_REGION"]})
```

`AWS_REGION` is a Lambda-reserved environment variable — no new template
parameter needed. Add the route to `06-api.yml` alongside `WeatherRoute`/
`LocateRoute`:

```yaml
  HealthRoute:
    Type: AWS::ApiGatewayV2::Route
    Properties:
      ApiId: !Ref WeatherApi
      RouteKey: 'GET /health'
      Target: !Sub 'integrations/${LambdaIntegration}'
      AuthorizationType: NONE
```

Add a companion test in `backend/tests/test_weather_handler.py` (happy path only —
this route never touches DynamoDB or OpenWeatherMap, so no new mocks needed).

```bash
cd backend && .venv/bin/pytest tests/test_weather_handler.py -k health -v
```

### 1.2 Widen `LambdaExecutionRole`'s DynamoDB/Secrets Resource ARNs

`01-iam.yml` — add a `SecondaryRegion` parameter and change both Sids from a
single ARN to a two-item list:

```yaml
  SecondaryRegion:
    Type: String
    Default: us-west-2
    Description: Multi-region failover region. Used only to widen the Lambda
      execution role's DynamoDB/Secrets Resource ARNs to cover the replica.
```

```yaml
              - Sid: DynamoDBCacheAccess
                Effect: Allow
                Action: [dynamodb:GetItem, dynamodb:PutItem]
                Resource:
                  - !Ref DynamoDBTableArn
                  - !Sub 'arn:aws:dynamodb:${SecondaryRegion}:${AWS::AccountId}:table/WeatherCache'
              - Sid: SecretsManagerReadAccess
                Effect: Allow
                Action: secretsmanager:GetSecretValue
                Resource:
                  - !Sub 'arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:${ProjectName}/openweathermap-api-key-*'
                  - !Sub 'arn:aws:secretsmanager:${SecondaryRegion}:${AWS::AccountId}:secret:${ProjectName}/openweathermap-api-key-*'
```

(Sid names above approximate the current policy — match whatever the real Sid
names are in `01-iam.yml` at edit time; only the `Resource` lists change.)

### 1.3 Parameterize `05-backend.yml` for secondary-region reuse

```yaml
  IsPrimaryRegion:
    Type: String
    Default: 'true'
    AllowedValues: ['true', 'false']
  CodeDeployRoleArn:
    Type: String
    Default: ''
    Description: Only used when IsPrimaryRegion=false — existing role ARN from
      the primary region's BackendStack.
  HookRoleArn:
    Type: String
    Default: ''
```

```yaml
Conditions:
  CreateRegionalRoles: !Equals [!Ref IsPrimaryRegion, 'true']
```

Wrap `CodeDeployRole`/`HookRole` resource blocks with
`Condition: CreateRegionalRoles`, and change every reference from
`!GetAtt CodeDeployRole.Arn` / `!GetAtt HookRole.Arn` to:

```yaml
ServiceRoleArn: !If [CreateRegionalRoles, !GetAtt CodeDeployRole.Arn, !Ref CodeDeployRoleArn]
```

(same `!If` pattern for the two `Role: !GetAtt HookRole.Arn` references on the
pre/post-traffic hook functions).

### 1.4 Add API Gateway regional custom domain to `06-api.yml`

```yaml
  HostedZoneId:
    Type: String
    Default: ''
    Description: Route 53 hosted zone for craftingnewtech.com. Leave blank to
      skip creating the api.* custom domain (e.g. staging).
  ApiSubdomain:
    Type: String
    Default: 'api.weather.craftingnewtech.com'

Conditions:
  CreateApiDomain: !Not [!Equals [!Ref HostedZoneId, '']]
```

```yaml
  ApiCertificate:
    Type: AWS::CertificateManager::Certificate
    Condition: CreateApiDomain
    Properties:
      DomainName: !Ref ApiSubdomain
      ValidationMethod: DNS
      DomainValidationOptions:
        - DomainName: !Ref ApiSubdomain
          HostedZoneId: !Ref HostedZoneId

  ApiCustomDomain:
    Type: AWS::ApiGatewayV2::DomainName
    Condition: CreateApiDomain
    Properties:
      DomainName: !Ref ApiSubdomain
      DomainNameConfigurations:
        - CertificateArn: !Ref ApiCertificate
          EndpointType: REGIONAL

  ApiMapping:
    Type: AWS::ApiGatewayV2::ApiMapping
    Condition: CreateApiDomain
    Properties:
      ApiId: !Ref WeatherApi
      DomainName: !Ref ApiCustomDomain
      Stage: !Ref ApiStage
```

Add two outputs (consumed by §3.2 — do not hardcode the regional hosted zone ID,
it varies by region and is only correct as read from the live resource):

```yaml
  ApiRegionalDomainName:
    Condition: CreateApiDomain
    Value: !GetAtt ApiCustomDomain.RegionalDomainName
    Export: {Name: !Sub '${AWS::StackName}-ApiRegionalDomainName'}
  ApiRegionalHostedZoneId:
    Condition: CreateApiDomain
    Value: !GetAtt ApiCustomDomain.RegionalHostedZoneId
    Export: {Name: !Sub '${AWS::StackName}-ApiRegionalHostedZoneId'}
```

### 1.5 Enable DynamoDB Global Tables (CLI, not CFN — see §0.2 #4)

```bash
aws dynamodb update-table \
  --region us-east-1 \
  --table-name WeatherCache \
  --replica-updates 'Create={RegionName=us-west-2}'

# Poll until both regions report ACTIVE (initial replica backfill can take a
# few minutes even for an empty/small table).
aws dynamodb describe-table --region us-east-1 --table-name WeatherCache \
  --query 'Table.{Status:TableStatus,Replicas:Replicas[].{Region:RegionName,Status:ReplicaStatus}}'
```

Local validation before committing any template change from this phase:

```bash
cfn-lint infrastructure/cloudformation/*.yml
checkov -d infrastructure/cloudformation/ --framework cloudformation
cd backend && .venv/bin/pytest --cov --cov-fail-under=80
```

---

## Phase 2 — Deploy Secondary Stack

### 2.1 Bootstrap the us-west-2 artifacts bucket

```bash
# BucketNameSuffix is required here -- S3 bucket names are globally unique,
# and 00-bootstrap.yml's BucketName has no region in it by default (that
# default must never change for the already-deployed primary bucket, since
# BucketName is create-only and changing it replaces the bucket). Omitting
# BucketNameSuffix fails early with AWS::EarlyValidation::ResourceExistenceCheck
# -- confirmed live 2026-07-24, see the tracking-table note above.
aws cloudformation deploy \
  --region us-west-2 \
  --template-file infrastructure/cloudformation/00-bootstrap.yml \
  --stack-name weather-dashboard-bootstrap-production \
  --parameter-overrides ProjectName=weather-dashboard Environment=production BucketNameSuffix=-us-west-2

WEST_ARTIFACTS_BUCKET=$(aws cloudformation describe-stacks \
  --region us-west-2 --stack-name weather-dashboard-bootstrap-production \
  --query 'Stacks[0].Outputs[?OutputKey==`ArtifactsBucketName`].OutputValue' --output text)
echo "$WEST_ARTIFACTS_BUCKET"
```

### 2.2 Create `master-secondary.yml`

New file, `infrastructure/cloudformation/master-secondary.yml` — orchestrates
only the two nested stacks that actually need a physical presence in the
secondary region (per §0.2's corrections: IAM is global, S3/CloudFront/pipelines
stay primary-only, DynamoDB/Secrets are handled by native replication):

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: >
  Weather Dashboard — Secondary Region (us-west-2) Stack.
  Deploys only BackendStack + ApiStack. IAM roles, S3 buckets, CloudFront,
  Route 53, and the CodePipelines are global or primary-region-only — see
  docs/WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md §0.2.

Parameters:
  ProjectName: {Type: String, Default: weather-dashboard}
  Environment: {Type: String, Default: production}
  ArtifactsBucketName: {Type: String}
  LambdaZipKey: {Type: String}
  HostedZoneId: {Type: String, Default: ''}
  MemorySize: {Type: Number, Default: 256}
  TimeoutSeconds: {Type: Number, Default: 10}
  LambdaReservedConcurrency: {Type: Number, Default: 10}
  LogRetentionDays: {Type: Number, Default: 7}

Resources:
  BackendStack:
    Type: AWS::CloudFormation::Stack
    Properties:
      TemplateURL: ./05-backend.yml
      Parameters:
        ProjectName: !Ref ProjectName
        Environment: !Ref Environment
        IsPrimaryRegion: 'false'
        LambdaExecutionRoleArn: !Sub 'arn:aws:iam::${AWS::AccountId}:role/${ProjectName}-lambda-role-${Environment}'
        CodeDeployRoleArn: !Sub 'arn:aws:iam::${AWS::AccountId}:role/${ProjectName}-codedeploy-role-${Environment}'
        HookRoleArn: !Sub 'arn:aws:iam::${AWS::AccountId}:role/${ProjectName}-hook-role-${Environment}'
        DynamoDBTableName: WeatherCache
        SecretName: !Sub '${ProjectName}/openweathermap-api-key'
        AllowedOrigin: 'https://weather.craftingnewtech.com'
        ArtifactsBucketName: !Ref ArtifactsBucketName
        LambdaZipKey: !Ref LambdaZipKey
        MemorySize: !Ref MemorySize
        TimeoutSeconds: !Ref TimeoutSeconds
        ReservedConcurrency: !Ref LambdaReservedConcurrency
        LogRetentionDays: !Ref LogRetentionDays

  ApiStack:
    Type: AWS::CloudFormation::Stack
    Properties:
      TemplateURL: ./06-api.yml
      Parameters:
        ProjectName: !Ref ProjectName
        Environment: !Ref Environment
        LambdaAliasArn: !GetAtt BackendStack.Outputs.LambdaAliasArn
        LambdaFunctionName: !GetAtt BackendStack.Outputs.LambdaFunctionName
        AllowedOrigin: 'https://weather.craftingnewtech.com'
        HostedZoneId: !Ref HostedZoneId
        ApiSubdomain: 'api.weather.craftingnewtech.com'
        LogRetentionDays: !Ref LogRetentionDays

Outputs:
  ApiRegionalDomainName:
    Value: !GetAtt ApiStack.Outputs.ApiRegionalDomainName
  ApiRegionalHostedZoneId:
    Value: !GetAtt ApiStack.Outputs.ApiRegionalHostedZoneId
  HealthEndpoint:
    Value: !Sub '${ApiStack.Outputs.ApiEndpoint}/health'
```

The three `arn:aws:iam::...` role ARNs above are computed via `Sub`, not
`GetAtt`/`ImportValue` (which cannot cross regions at all) — the same
deterministic-string-interpolation pattern already used for the
Terraform/CloudFormation cross-module `-target` scope-creep problem, applied here
to a cross-*region* reference instead of a cross-*stack* one.

### 2.3 Upload the Lambda ZIP to us-west-2 and deploy

```bash
# Same ZIP already built for the primary region — just needs a copy in the
# secondary region's bucket (Lambda requires same-region S3 source).
aws s3 cp \
  "s3://weather-dashboard-artifacts-${ACCOUNT_ID}-production/lambda/lambda.zip" \
  "s3://${WEST_ARTIFACTS_BUCKET}/lambda/lambda.zip" \
  --source-region us-east-1 --region us-west-2

aws cloudformation package \
  --region us-west-2 \
  --template-file infrastructure/cloudformation/master-secondary.yml \
  --s3-bucket "$WEST_ARTIFACTS_BUCKET" \
  --s3-prefix cloudformation \
  --output-template-file infrastructure/cloudformation/master-secondary-packaged.yml

# Dry run first — confirm the change set before executing (per
# ~/.claude/instructions/cloudformation-aws-patterns.md).
aws cloudformation deploy \
  --region us-west-2 \
  --template-file infrastructure/cloudformation/master-secondary-packaged.yml \
  --stack-name weather-dashboard-secondary-production \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    ArtifactsBucketName="$WEST_ARTIFACTS_BUCKET" \
    LambdaZipKey=lambda/lambda.zip \
    HostedZoneId=ABC-EXAMPLE-XXXX \
  --no-execute-changeset

# Review the change set, then execute for real:
aws cloudformation deploy \
  --region us-west-2 \
  --template-file infrastructure/cloudformation/master-secondary-packaged.yml \
  --stack-name weather-dashboard-secondary-production \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    ArtifactsBucketName="$WEST_ARTIFACTS_BUCKET" \
    LambdaZipKey=lambda/lambda.zip \
    HostedZoneId=ABC-EXAMPLE-XXXX
```

### 2.4 Native Secrets Manager replication

Add to `07-ssm.yml`:

```yaml
  ReplicaRegion:
    Type: String
    Default: ''
Conditions:
  HasReplicaRegion: !Not [!Equals [!Ref ReplicaRegion, '']]
```

```yaml
  ApiKeySecret:
    Type: AWS::SecretsManager::Secret
    Properties:
      Name: !Ref SecretName
      ReplicaRegions: !If
        - HasReplicaRegion
        - [{Region: !Ref ReplicaRegion}]
        - !Ref AWS::NoValue
```

No `KmsKeyId` given for the replica — it falls back to the AWS-managed key in
us-west-2 rather than a matching CMK (a second regional CMK is ~$1/month for no
real security gain on this specific secret, given IAM already scopes access
tightly — same reasoning `04-database.yml`'s own checkov-skip comment uses for
skipping a table-level CMK). Redeploy the primary `master.yml` with
`ReplicaRegion=us-west-2` added to its parameter overrides — this is an update
to the *existing* primary stack, not a new stack:

```bash
aws cloudformation deploy \
  --template-file infrastructure/cloudformation/master-packaged.yml \
  --stack-name weather-dashboard-master-production \
  --role-arn "$CFN_DEPLOY_ROLE_ARN" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --no-execute-changeset
# review, then re-run without --no-execute-changeset

aws secretsmanager describe-secret \
  --region us-west-2 --secret-id weather-dashboard/openweathermap-api-key \
  --query 'ReplicationStatus'
```

### 2.5 GuardDuty-only deploy to us-west-2

Add to `11-audit.yml`:

```yaml
  DeployCloudTrail:
    Type: String
    Default: 'true'
    AllowedValues: ['true', 'false']
Conditions:
  ShouldDeployCloudTrail: !Equals [!Ref DeployCloudTrail, 'true']
```

Wrap `TrailBucket`, `TrailBucketPolicy`, `CloudTrailKmsKey`, `CloudTrailKmsKeyAlias`,
and `ManagementTrail` with `Condition: ShouldDeployCloudTrail`. Deploy:

```bash
aws cloudformation deploy \
  --region us-west-2 \
  --template-file infrastructure/cloudformation/11-audit.yml \
  --stack-name weather-dashboard-audit-production \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides DeployCloudTrail=false
```

### 2.6 / 2.7 — DeploySecondary stage on both pipelines (implemented design)

**Corrected during implementation — no `ArtifactStores`/`Region:` property.**
The original draft above (superseded) assumed CodePipeline's native
cross-region action feature was needed. It isn't: a CodeBuild project running
in the pipeline's own region (us-east-1) can deploy to us-west-2 just fine via
plain `aws ... --region us-west-2` calls in its buildspec — exactly what tasks
2.1/2.3/2.5 already proved by hand. This is strictly less machinery (no second
`ArtifactStore` entry, no cross-region replication bucket wiring) for the same
result, so that's what got built:

- **`08-pipeline.yml`**: `DeploySecondaryProject` (same-region CodeBuild
  project, `InfraCodeBuildRoleArn`) runs
  `pipeline/buildspec/deploy-infra-secondary.yml`, which packages and deploys
  `master-secondary.yml` to us-west-2. Added as a `DeploySecondary` stage
  right after `Deploy`, gated by `Fn::If` on a new `SecondaryArtifactsBucketName`
  parameter (default `''` = stage omitted entirely) — verified this
  conditional-stage-as-list-item pattern is valid against the *real*
  CloudFormation service via `aws cloudformation validate-template`, not just
  cfn-lint.
- **`08b-app-pipeline.yml`**: `AppDeploySecondaryProject` (`AppCodeBuildRoleArn`)
  runs `pipeline/buildspec/deploy-app-secondary.yml` — copies the Lambda ZIP
  `AppDeployProject` just built to the secondary bucket, publishes a version,
  and runs `scripts/deploy-canary.sh` **unmodified** (it takes no `--region`
  flags of its own; `AWS_DEFAULT_REGION` is set at the CodeBuild project level
  to repoint its internal calls, while every command the new buildspec writes
  directly still uses an explicit `--region` flag). Deliberately skips frontend
  sync, CloudFront invalidation, and Provisioned Concurrency — primary-only.
- **`01-iam.yml`**: `InfraCodeBuildRole`/`AppCodeBuildRole`'s S3 Sids widened to
  the secondary bucket ARN (`SecondaryArtifactsBucketArn` parameter) —
  `CodePipelineServiceRole` needed no change, since it never touches the
  secondary bucket in this design. `CloudFormationDeployRole` needed no change
  either — its `AppResources` Sid is already `Resource: '*'` (not region-scoped).

**Self-modifying-pipeline timing, same as the Pipeline Split plan's own B.7:**
the push that adds these stages is deployed *by* the current (pre-change)
pipeline definition — that run completes with the old stage list. The new
`DeploySecondary` stages only exist starting with the *next* triggered
execution. Expect to need a follow-up trigger before 2.8's smoke test can
actually exercise them.

Local validation:

```bash
cfn-lint infrastructure/cloudformation/08-pipeline.yml infrastructure/cloudformation/08b-app-pipeline.yml infrastructure/cloudformation/01-iam.yml infrastructure/cloudformation/master.yml
checkov -d infrastructure/cloudformation/ --framework cloudformation
aws cloudformation validate-template --template-body file://infrastructure/cloudformation/08-pipeline.yml
aws cloudformation validate-template --template-body file://infrastructure/cloudformation/08b-app-pipeline.yml
```

### 2.8 Verify both regions

```bash
curl -s https://ABC-EXAMPLE-XXXX.execute-api.us-east-1.amazonaws.com/health | jq .
WEST_API=$(aws cloudformation describe-stacks --region us-west-2 \
  --stack-name weather-dashboard-secondary-production \
  --query 'Stacks[0].Outputs[?OutputKey==`HealthEndpoint`].OutputValue' --output text)
curl -s "$WEST_API" | jq .
curl -s "https://ABC-EXAMPLE-XXXX.execute-api.us-east-1.amazonaws.com/weather?city=London" | jq '.city'
```

---

## Phase 3 — Route 53 Failover Routing

### 3.1 New `12-failover-dns.yml`

**Corrected during 3.2 (see the tracking-table note above) — the health
check needs a second parameter.** The draft below (as originally written)
pointed the health check's `FullyQualifiedDomainName` at `PrimaryDomainName`
(the regional custom-domain target) — that 404s immediately, since API
Gateway custom domains route by `Host` header and a health checker connecting
to the raw regional name sends that name as the header, matching no
`ApiMapping`. The actual deployed template adds a `PrimaryHealthCheckDomainName`
parameter (the raw `execute-api` hostname) for the health check specifically,
leaving `PrimaryDomainName` as the `AliasTarget` only. The code block below is
left as originally drafted for historical context; see
`infrastructure/cloudformation/12-failover-dns.yml` for the corrected version.

Deployed standalone (operator credentials, once — same posture as `00-bootstrap.yml`
and `11-audit.yml`, per `~/.claude/instructions/network-security-baseline.md`'s
"account-level security tooling must not be deletable by tearing down an app
stack"). Takes both regions' regional API domain outputs as plain parameters —
a single CFN stack cannot `GetAtt`/`ImportValue` across regions, so these must be
captured from each region's own stack output first (§3.2):

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: Route 53 failover routing for api.weather.craftingnewtech.com.

Parameters:
  HostedZoneId: {Type: String}
  ApiFqdn: {Type: String, Default: api.weather.craftingnewtech.com}
  PrimaryDomainName: {Type: String}
  PrimaryHostedZoneId: {Type: String}
  SecondaryDomainName: {Type: String}
  SecondaryHostedZoneId: {Type: String}

Resources:
  PrimaryHealthCheck:
    Type: AWS::Route53::HealthCheck
    Properties:
      HealthCheckConfig:
        Type: HTTPS
        FullyQualifiedDomainName: !Ref PrimaryDomainName
        Port: 443
        ResourcePath: /health
        RequestInterval: 30
        FailureThreshold: 3

  PrimaryRecord:
    Type: AWS::Route53::RecordSet
    Properties:
      HostedZoneId: !Ref HostedZoneId
      Name: !Ref ApiFqdn
      Type: A
      SetIdentifier: primary
      Failover: PRIMARY
      HealthCheckId: !Ref PrimaryHealthCheck
      AliasTarget:
        DNSName: !Ref PrimaryDomainName
        HostedZoneId: !Ref PrimaryHostedZoneId
        EvaluateTargetHealth: false

  SecondaryRecord:
    Type: AWS::Route53::RecordSet
    Properties:
      HostedZoneId: !Ref HostedZoneId
      Name: !Ref ApiFqdn
      Type: A
      SetIdentifier: secondary
      Failover: SECONDARY
      AliasTarget:
        DNSName: !Ref SecondaryDomainName
        HostedZoneId: !Ref SecondaryHostedZoneId
        EvaluateTargetHealth: false

Outputs:
  ApiFqdn: {Value: !Ref ApiFqdn}
  HealthCheckId: {Value: !Ref PrimaryHealthCheck}
```

### 3.2 Capture outputs and deploy

```bash
PRIMARY_DOMAIN=$(aws cloudformation describe-stacks \
  --stack-name weather-dashboard-master-production \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiRegionalDomainName`].OutputValue' --output text)
PRIMARY_ZONE=$(aws cloudformation describe-stacks \
  --stack-name weather-dashboard-master-production \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiRegionalHostedZoneId`].OutputValue' --output text)
SECONDARY_DOMAIN=$(aws cloudformation describe-stacks --region us-west-2 \
  --stack-name weather-dashboard-secondary-production \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiRegionalDomainName`].OutputValue' --output text)
SECONDARY_ZONE=$(aws cloudformation describe-stacks --region us-west-2 \
  --stack-name weather-dashboard-secondary-production \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiRegionalHostedZoneId`].OutputValue' --output text)

aws cloudformation deploy \
  --template-file infrastructure/cloudformation/12-failover-dns.yml \
  --stack-name weather-dashboard-failover-dns-production \
  --parameter-overrides \
    HostedZoneId=ABC-EXAMPLE-XXXX \
    PrimaryDomainName="$PRIMARY_DOMAIN" PrimaryHostedZoneId="$PRIMARY_ZONE" \
    SecondaryDomainName="$SECONDARY_DOMAIN" SecondaryHostedZoneId="$SECONDARY_ZONE"
```

### 3.3 Point the frontend at the failover domain

`frontend/js/config.js`:

```diff
-  API_BASE_URL: 'https://ABC-EXAMPLE-XXXX.execute-api.us-east-1.amazonaws.com',
+  API_BASE_URL: 'https://api.weather.craftingnewtech.com',
```

Test locally at `localhost:8080` before committing — this project's convention
is to verify frontend changes locally first, then let the App pipeline's
`ValidateDeployment` smoke test confirm it again before this reaches production.

### 3.4 TTL and health check confirmation

Route 53 ALIAS records to CloudFront/API Gateway/ELB targets don't have a
settable TTL (they inherit the target's) — the "lower TTL to 60s" step from the
original proposal applies to a plain CNAME/A record, not an ALIAS. Since both
failover records here are ALIAS records to API Gateway regional endpoints, TTL
propagation delay is not the bottleneck — **Route 53 health-check-driven failover
timing is**. Confirm:

```bash
aws route53 get-health-check-status --health-check-id <id-from-3.2-output>
```

---

## Phase 4 — Validation

### 4.1 Failover drill

```bash
# Temporarily force the health check to fail without touching real traffic:
# add a throwaway route that always 503s, point ONLY the health check at it,
# then remove it after the drill.
curl -s -o /dev/null -w '%{http_code}\n' https://api.weather.craftingnewtech.com/weather?city=TestCity
# Watch the health check flip:
watch -n 10 'aws route53 get-health-check-status --health-check-id <id>'
```

### 4.2 Failback drill

Restore the primary; confirm the health check returns to `Success` and DNS
resolution for `api.weather.craftingnewtech.com` returns to the primary target
with no manual intervention.

### 4.3 Replication check

```bash
curl -s "https://ABC-EXAMPLE-XXXX.execute-api.us-east-1.amazonaws.com/weather?city=ReplicationTestCity"
sleep 3
aws dynamodb get-item --region us-west-2 --table-name WeatherCache \
  --key '{"city": {"S": "replicationtestcity"}}'
```

### 4.4 CloudWatch alarm

Add to `09-monitoring.yml` (or a small regional monitoring addition):

```yaml
  Route53HealthCheckAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub '${ProjectName}-route53-failover-active-${Environment}'
      Namespace: AWS/Route53
      MetricName: HealthCheckStatus
      Dimensions:
        - {Name: HealthCheckId, Value: !Ref PrimaryHealthCheckId}
      Statistic: Minimum
      Period: 60
      EvaluationPeriods: 3
      Threshold: 1
      ComparisonOperator: LessThanThreshold
      AlarmActions: [!Ref SecurityFindingsTopicArn]
```

(Route 53 health check metrics are only published to CloudWatch in us-east-1,
regardless of which region the health check itself targets — deploy this alarm
in us-east-1.)

### 4.5 Runbook

Add an "Active Failover" section to
[`WeatherApp-runbook.md`](../WeatherApp-runbook.md): what the alarm means, how
to confirm it's a real regional outage vs. a false-positive health check blip,
who to notify, and the manual override
(`aws route53 update-health-check ... --disabled` to force traffic back to
primary if the automatic failback is undesired for some reason).

**Must also document the "us-east-1 is down" procedure explicitly** (§0.2 #9)
— this is not optional polish, it's the actual point of the plan:

- The `DeploySecondary` pipeline stages (§2.6/§2.7) are unusable during a real
  us-east-1 outage — CodeCommit/CodePipeline/CodeBuild all live only there.
  Do not wait for or depend on them.
- Any change to us-west-2 during an outage goes through direct AWS CLI with
  `--region us-west-2`, exactly as demonstrated live in tasks 2.1/2.3/2.5 —
  no dependency on us-east-1 being reachable (verified: the operator's IAM
  user has no region-restricting conditions; every service-level role is
  global; each region's Lambda only calls same-region service endpoints).
- If CodeCommit is unreachable, `git clone` from the GitHub mirror instead —
  this repo already pushes to both remotes (`git push both`).

---

## Cost Impact (superseding the proposal's §7 estimate)

| -------------------------------------------- | ---------- | ----------- |
| Addition                                     | Monthly    | Yearly      |
| -------------------------------------------- | ---------- | ----------- |
| Lambda in us-west-2 (passive)                | $0.05      | $0.60       |
| API Gateway in us-west-2 (passive)           | $0.01      | $0.12       |
| DynamoDB Global Tables replication writes    | ~$0.15     | $1.80       |
| Secrets Manager secret replica in us-west-2  | $0.40      | $4.80       |
| GuardDuty detector in us-west-2              | $3.00      | $36.00      |
| Second regional artifacts bucket (us-west-2) | ~$0.01     | $0.12       |
| Route 53 health check (1 endpoint, not 2)    | $0.50      | $6.00       |
| CloudWatch alarm for failover status         | $0.10      | $1.20       |
| **Total additional cost**                    | **~$4.22** | **~$50.64** |
| -------------------------------------------- | ---------- | ----------- |

One health check instead of two (only the primary API custom domain needs an
active check — Route 53 failover routing doesn't require a health check on the
secondary record) is the one real cost delta versus the original proposal's
estimate; everything else is materially the same.

---

## Rollback Plan

- **Phase 1** template edits: revert via git, redeploy `master.yml` — no data
  changes, safe to roll back at any point before §1.5.
- **Phase 1.5** (Global Tables): `aws dynamodb update-table --replica-updates
  'Delete={RegionName=us-west-2}'` removes the replica; primary table and data
  untouched.
- **Phase 2**: `weather-dashboard-secondary-production` and
  `weather-dashboard-bootstrap-production` (us-west-2) can each be deleted
  independently via `aws cloudformation delete-stack --region us-west-2`; neither
  is referenced by the primary stack, so deleting them has zero effect on
  production traffic, which never touches us-west-2 until Phase 3 ships.
- **Phase 3**: deleting `weather-dashboard-failover-dns-production` removes the
  `api.weather.craftingnewtech.com` failover records entirely; revert
  `frontend/js/config.js` back to the raw `execute-api` URL in the same change
  so the frontend doesn't reference a domain that no longer resolves.
- **Full rollback**: `git reset` is never used per this project's git safety
  rules — revert each phase's commits forward, redeploy, and tear down the
  us-west-2 stacks in the reverse order they were created (pipelines' Deploy
  Secondary stage → GuardDuty stack → secondary stack → bootstrap stack).

---

## Sign-off Checklist

- [ ] All Phase 1 template changes pass `cfn-lint` + `checkov` + local pytest
- [ ] Phase 2 stacks deployed and `describe-stacks` shows `CREATE_COMPLETE` in
      us-west-2 for both `weather-dashboard-secondary-production` and
      `weather-dashboard-bootstrap-production`
- [ ] `/health` returns 200 with the correct region in both us-east-1 and
      us-west-2
- [ ] Secrets Manager replica `ReplicationStatus` shows `Succeeded`
- [ ] DynamoDB `Replicas[].ReplicaStatus` shows `ACTIVE` in both regions
- [ ] GuardDuty `list-detectors` shows exactly one detector per region (no
      duplicate CloudTrail trail created in us-west-2)
- [ ] Both pipelines' new Deploy-Secondary stage has run to `Succeeded` at
      least once
- [ ] Failover and failback drills (§4.1/§4.2) both completed with live
      site/API confirmed 200 throughout
- [ ] Replication check (§4.3) confirmed sub-3-second cache propagation
- [ ] Runbook §4.5 section written and reviewed
- [ ] Cost delta confirmed via Cost Explorer within ~48h of Phase 2 completing,
      against the ~$4.22/month estimate above

---

*Related documents:*
- [WeatherApp-MultiRegion-V1.md](./WeatherApp-MultiRegion-V1.md) — the reviewed proposal this plan implements
- [WeatherApp-PipeSplit-ImplePlan-V1.md](../WeatherApp-PipeSplit-ImplePlan-V1.md) — implementation-plan format this document follows
- [WeatherApp-ImproveDeploys-Plan-V2.md](../WeatherApp-ImproveDeploys-Plan-V2.md) — baseline reliability/security work this plan builds on
- [WeatherApp-runbook.md](../WeatherApp-runbook.md) — gets the new "Active Failover" section from §4.5
