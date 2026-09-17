# WeatherApp Multi-Region — Troubleshooting Guide — V1

**Date:** 2026-07-26
**Author:** Cloud & DevOps Engineering Review
**Scope:** Every real issue hit building and operating the multi-region
failover architecture described in
[`WeatherApp-MultiRegion-ImplePlan-V1.md`](./WeatherApp-MultiRegion-ImplePlan-V1.md),
with the exact diagnostic and fix commands used for each. For a live
verification checklist (not troubleshooting), see
[`WeatherApp-MultiRegion-Runbook-V1.md`](./WeatherApp-MultiRegion-Runbook-V1.md).
For non-multi-region issues (pipeline/cfn-lint/checkov gates, Secrets
Manager rotation, bootstrap-era setup), see `docs/troubleshooting.md` and
`docs/WeatherApp-ImproveDeploys-Plan-TroubleshootingSteps-V1.md` instead.

---

## Issue Index

| - | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------- | ------------- |
| # | Symptom                                                                | Root Cause                                                                            | Status        |
| - | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------- | ------------- |
| 1 | Frontend: "Unable to reach the weather service" in every browser       | CSP `connect-src` never updated for custom domain                                     | **Fixed**     |
| 2 | New multi-region parameters silently stayed disabled after deploy      | Deploy script passes zero `--parameter-overrides`                                     | Fixed         |
| 3 | Stack stuck `UPDATE_ROLLBACK_FAILED` on Secrets replication            | Missing `secretsmanager:Replicate*` IAM action                                        | Fixed         |
| 4 | Retry of the same deploy fails with `AlreadyExists`                    | `DeletionPolicy: Retain` log group survived rollback                                  | Fixed         |
| 5 | `DeploySecondary` fails: `AccessDenied` on `lambda:UpdateFunctionCode` | IAM Resource ARN hardcoded to `${AWS::Region}` (us-east-1 only)                       | Fixed         |
| 6 | Route 53 health check reports `Failure: HTTP 404` from every region    | Health check pointed at custom-domain regional target, not raw `execute-api` hostname | Fixed         |
| 7 | `aws dynamodb update-table --replica-updates` fails once               | Transient `UnrecognizedClientException`                                               | Fixed (retry) |
| 8 | Secondary-region bootstrap deploy fails immediately                    | S3 bucket names are global; template had no region suffix                             | Fixed         |
| - | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------- | ------------- |

---

## Issue 1 — Frontend blocked by stale CSP after custom-domain cutover

**Reported:** 2026-07-26. User-facing error: *"Unable to reach the weather
service. Check your connection and try again."* — reproduced identically on
multiple browsers, laptop, and phone (different networks).

**Root cause:** `d1ceb5b` (2026-07-24, task 3.3) pointed `frontend/js/config.js`
at the new Route 53 failover domain, `api.weather.craftingnewtech.com`. But
`frontend/index.html`'s `Content-Security-Policy` meta tag's `connect-src`
directive was never updated to match — it still only allowlisted
`https://*.execute-api.us-east-1.amazonaws.com` (the raw API Gateway
hostname, written 2026-07-09, before the custom domain existed). Every
browser silently blocked the `fetch()` call at the CSP layer, before any
network request was ever sent — which is why it failed identically on every
device/network and never produced a single failed request in any AWS log.

### Diagnostic commands, in the order used

**1. Find where the error string comes from, to see what triggers it:**
```bash
grep -rn "Unable to reach the weather service" --include="*.js" .
```
Result: `frontend/js/app.js` throws this message specifically when the
`fetch()` call raises a `TypeError` — i.e. a network-layer failure, not an
HTTP error status (404/429 get their own separate messages).

**2. Find the API base URL the frontend actually calls:**
```bash
grep -rn "API_BASE_URL" frontend/
```
Result: `frontend/js/config.js` → `https://api.weather.craftingnewtech.com`.

**3. Rule out DNS and backend reachability (this is the step that first ruled
out a server-side outage):**
```bash
dig +short api.weather.craftingnewtech.com
curl -sS -o /dev/null -w "HTTP %{http_code}  time_total=%{time_total}s\n" \
  --max-time 10 "https://api.weather.craftingnewtech.com/weather?city=London"
```
Result: DNS resolved, `HTTP 200` in ~2.3s — backend was healthy.

**4. Check CORS explicitly (a common false-positive cause of this exact
error), including the actual preflight:**
```bash
curl -sS -D - -o /dev/null --max-time 10 -X OPTIONS \
  "https://api.weather.craftingnewtech.com/weather?city=London" \
  -H "Origin: https://weather.craftingnewtech.com" \
  -H "Access-Control-Request-Method: GET"

curl -sS -D - -o /dev/null --max-time 10 \
  "https://api.weather.craftingnewtech.com/weather?city=London" \
  -H "Origin: https://weather.craftingnewtech.com"
```
Result: `204` on preflight, `200` on the real call, correct
`access-control-allow-origin` header both times — CORS was fine, ruled out.

**5. Confirm the upstream OpenWeatherMap dependency itself is reachable
(rules out a third-party outage):**
```bash
curl -sS -o /dev/null -w "HTTP %{http_code}  time_total=%{time_total}s\n" \
  --max-time 10 "https://api.openweathermap.org/data/3.0/onecall?lat=51.5&lon=-0.12"
```
Result: `HTTP 401` (auth required, as expected with no key) — confirms
network path, DNS, and TLS to OWM all work.

**6. Pull the Lambda's own application logs for errors:**
```bash
aws logs filter-log-events \
  --log-group-name "/aws/lambda/weather-dashboard-handler-production" \
  --start-time $(( $(date +%s) - 24*3600 ))000 \
  --filter-pattern "ERROR" --max-items 50 \
  --query 'events[].[timestamp,message]' --output text
```
Result: empty — no errors logged in 24 hours.

**7. Pull the API Gateway access logs for the actual request history —
the step that proved the request never reached AWS at all:**
```bash
aws logs describe-log-groups --log-group-name-prefix "/aws/apigateway" \
  --query 'logGroups[].logGroupName' --output text

aws logs filter-log-events \
  --log-group-name "/aws/apigateway/weather-dashboard-production" \
  --start-time $(( $(date +%s) - 7*24*3600 ))000 \
  --filter-pattern "\"/weather\"" --max-items 100 \
  --query 'events[].message' --output text
```
Result: 26 `/weather` requests in 7 days, all `200` except one `400`/`404`
(bad input, unrelated), **zero** `/locate` requests, **zero** 5xx errors,
**zero** timeouts — no record of the failure ever reaching the server side.

**8. Rule out a regional DNS failover problem (health checks across every
AWS checker region):**
```bash
aws route53 list-health-checks \
  --query 'HealthChecks[].{Id:Id,FQDN:HealthCheckConfig.FullyQualifiedDomainName}'

aws route53 get-health-check-status --health-check-id <health-check-id> \
  --query 'HealthCheckObservations[].{Region:Region,Status:StatusReport.Status}'
```
Result: `Success: HTTP Status Code 200` from all 16 checker regions —
backend globally healthy, not a failover/DNS issue.

**9. Since the request never reaches AWS at all, check what's actually
being served to the browser — this is the step that found the bug:**
```bash
curl -sS "https://weather.craftingnewtech.com/" | grep -n "script\|config.js"
curl -sS -D - --max-time 10 "https://weather.craftingnewtech.com/js/config.js"
curl -sS "https://weather.craftingnewtech.com/" | sed -n '1,25p'
```
The last command printed the live `Content-Security-Policy` meta tag:
```
connect-src 'self' https://*.execute-api.us-east-1.amazonaws.com;
```
— which does not match `config.js`'s `api.weather.craftingnewtech.com`.
**This is the root cause.**

**10. Confirm the mismatch's timeline against git history:**
```bash
git log --oneline -3 -- frontend/index.html
git log --oneline -3 -- frontend/js/config.js
git log -1 --format="%H %ad %s" --date=format:"%Y-%m-%d %H:%M %Z" d1ceb5b
git log --format="%H %ad %s" --date=format:"%Y-%m-%d %H:%M %Z" -- frontend/index.html
```
Confirmed `config.js` was repointed to the custom domain on **2026-07-24
15:47 (Friday)**, while `index.html`'s CSP was last touched **2026-07-09** —
the CSP had been stale since the moment the domain cutover shipped.

**11. Confirmation from the browser console (matches the diagnosis exactly):**
```
Connecting to 'https://api.weather.craftingnewtech.com/weather?city=New+York'
violates the following Content Security Policy directive:
"connect-src 'self' https://*.execute-api.us-east-1.amazonaws.com".
The action has been blocked.
```

### Fix

`frontend/index.html`:
```diff
-      connect-src 'self' https://*.execute-api.us-east-1.amazonaws.com;
+      connect-src 'self' https://api.weather.craftingnewtech.com;
```
Verified nothing else in the frontend references the raw `execute-api`
hostname before removing it from the allowlist:
```bash
grep -rn "execute-api" frontend/
```

### Verification

```bash
# Local: confirm the served HTML carries the fix
cd frontend && python3 -m http.server 8080
curl -sS "http://localhost:8080/" | grep -A2 "connect-src"

# Then manually: open http://localhost:8080, search a city, confirm no CSP
# error and real weather data renders.
```

### Deploy

```bash
git add frontend/index.html
git commit -F <message-file>
git push both main

# Confirm the app pipeline (not infra — frontend/** path filter) fired:
aws codepipeline list-pipeline-executions \
  --pipeline-name weather-dashboard-app-pipeline-production --max-items 3 \
  --query 'pipelineExecutionSummaries[].{Status:status,StartTime:startTime,Trigger:trigger}'

aws codepipeline get-pipeline-state \
  --name weather-dashboard-app-pipeline-production \
  --query 'stageStates[].{Stage:stageName,Status:latestExecution.status}'
```

### Prevention

Any future change that repoints `API_BASE_URL` (new domain, new region,
rollback to the raw API Gateway endpoint) must be a single commit that
updates **both** `frontend/js/config.js` and `frontend/index.html`'s CSP
`connect-src` together — they encode the same fact (which host the
frontend is allowed to call) in two different files with no shared
source of truth. Consider a build-time check that greps `config.js`'s
`API_BASE_URL` value and asserts it appears in `index.html`'s
`connect-src`, failing the pipeline's Validate stage if they diverge.

---

## Issue 2 — New multi-region parameters silently defaulted to blank

**Occurred:** 2026-07-24, 13:30-14:09 EDT, during tasks 2.4/2.6/2.7.

**Symptom:** The Infra pipeline deployed cleanly (all stages succeeded), but
every multi-region feature in the push (IAM widening, Secrets replication,
new pipeline stages) stayed structurally present yet disabled.

**Root cause:** `pipeline/scripts/deploy-infrastructure.sh` passes zero
`--parameter-overrides` on normal runs, relying on CloudFormation to
preserve each parameter's *previous* stack value. That preservation doesn't
exist for a parameter's first-ever appearance in the template — it falls
through to the template's `Default: ''` instead.

**Diagnostic commands:**
```bash
aws cloudformation describe-stacks --stack-name weather-dashboard-master-production \
  --query 'Stacks[0].Parameters[?ParameterKey==`SecondaryRegion` || ParameterKey==`SecondaryArtifactsBucketName`]'
```
Confirms whether a parameter is genuinely populated or silently blank.

**Fix:** One-time manual override to set real values once, so future
pipeline runs preserve them automatically:
```bash
aws cloudformation deploy --stack-name weather-dashboard-master-production \
  --template-file infrastructure/cloudformation/master.yml \
  --parameter-overrides SecondaryRegion=us-west-2 SecondaryArtifactsBucketName=<bucket> \
  --no-execute-changeset   # review first — see Issue 3/4 for what this surfaced
```

**Prevention:** For multi-parameter multi-region rollouts, deploy one new
parameter at a time until each has been exercised individually — bundling
several first-appearance parameters together is what turned this into a
cascading three-part incident (Issues 2, 3, 4 below all came from the same
push).

---

## Issue 3 — Cascading rollback failure from a missing IAM action

**Occurred:** 2026-07-24, immediately following Issue 2's manual override.

**Symptom:** `SsmStack` failed on `ApiKeySecret`:
`AccessDenied: secretsmanager:ReplicateSecretToRegions`. The automatic
rollback then **also** failed, on `RemoveRegionsFromReplication` — leaving
the stack `UPDATE_ROLLBACK_FAILED`. This is the
`cloudformation-aws-patterns.md` "rollback reverts earlier-succeeded
permissions" gotcha: `IamStack`'s own rollback revoked the permission before
`SsmStack`'s rollback could use it to undo its partial replica creation.

**Diagnostic commands:**
```bash
aws cloudformation describe-stacks --stack-name <stack> --query 'Stacks[0].StackStatus'

aws cloudformation describe-stack-events --stack-name <stack> --max-items 30 \
  --query 'StackEvents[?contains(ResourceStatus, `FAILED`)].{Resource:LogicalResourceId,Status:ResourceStatus,Reason:ResourceStatusReason}'
```

**Fix (two layers):**
```bash
# 1. Unblock the immediate stuck rollback — live drift fix
aws iam put-role-policy --role-name CloudFormationDeployRole \
  --policy-name secrets-replication-unblock --policy-document file://policy.json

aws cloudformation continue-update-rollback --stack-name <stack> \
  --role-arn <deploy-role-arn>

# 2. Durable fix — add the actions to the source template
#    (01-iam.yml: secretsmanager:ReplicateSecretToRegions,
#     secretsmanager:RemoveRegionsFromReplication)
```

**Verification:**
```bash
aws secretsmanager describe-secret --secret-id weather-dashboard/openweathermap-api-key \
  --query '{Name:Name,ReplicationStatus:ReplicationStatus}'
```
Expect `Region: us-west-2`, `Status: InSync`.

**Prevention:** Before any narrowly-scoped apply that grants a new
cross-region IAM action, confirm the action list against
`aws iam simulate-principal-policy` first (see `cicd-iam-policy.md`) — this
gap existed because `CloudFormationDeployRole`'s existing Secrets Manager
actions (added for single-region rotation in an earlier phase) were assumed
to cover replication too, without checking.

---

## Issue 4 — Retained log group blocks the retry

**Occurred:** 2026-07-24, immediately after Issue 3's fix.

**Symptom:** Retrying the same deploy succeeded for the IAM/Secrets portion,
but `PipelineStack` then failed creating `DeploySecondaryLogGroup`:
`AlreadyExists`.

**Root cause:** The first failed attempt had already created this log group
before the stack-wide rollback. Its `DeletionPolicy: Retain` (matching every
other log group in the template) correctly preserved it through that
rollback — which then collided with the retry's own `CREATE`.

**Diagnostic commands:**
```bash
aws logs describe-log-groups --log-group-name-prefix "/aws/codebuild/weather-dashboard" \
  --query 'logGroups[].{Name:logGroupName,StoredBytes:storedBytes}'
```
Confirm the group is genuinely empty (`storedBytes: 0`, i.e. disposable)
before deleting.

**Fix:**
```bash
aws logs delete-log-group --log-group-name <the-colliding-log-group-name>
# then retry the deploy
```

**Prevention:** After any failed multi-resource deploy, check for
`DeletionPolicy: Retain` resources created before the failure point — they
will not be cleaned up automatically and will collide with the next
`CREATE` attempt (`cloudformation-aws-patterns.md`).

---

## Issue 5 — Region-pinned IAM Resource ARN breaks the secondary region

**Occurred:** 2026-07-24, 14:52-15:06 EDT, first real exercise of the App
pipeline's `DeploySecondary` stage.

**Symptom:** Primary `Deploy` stage succeeded; `DeploySecondary` failed:
`AccessDenied` on `lambda:UpdateFunctionCode` against the us-west-2
function.

**Root cause:** `AppCodeBuildRole`'s `LambdaUpdateCode` IAM statement used
`${AWS::Region}` (the deploying pipeline's own home region, always
us-east-1) in its Resource ARN — the same region-pinning bug class as an
earlier `LambdaExecutionRole` fix in task 1.2, just missed on this
particular role during that audit.

**Diagnostic commands:**
```bash
aws iam get-role-policy --role-name AppCodeBuildRole --policy-name <policy-name> \
  --query 'PolicyDocument.Statement[?Sid==`LambdaUpdateCode`]'
```
Look for a literal `us-east-1` (or `${AWS::Region}` resolving to it) in a
Resource ARN that needs to match a *different* region's resource.

**Fix:** Widen the Resource ARN to cover both regions explicitly (or use a
wildcard region segment), then re-audit every other `${AWS::Region}`
reference in the same template:
```bash
grep -n '${AWS::Region}' infrastructure/cloudformation/01-iam.yml
```
On this project, that audit found `CodeDeployTrafficShift` had the
identical latent bug (not yet triggered) and fixed it in the same pass; the
other 13 occurrences were confirmed correct as-is (they reference resources
that only ever exist in us-east-1 — CodeCommit, CodePipeline, CodeBuild,
SNS approval topics).

**Prevention:** Any IAM role used by a pipeline stage that operates against
a *secondary* region's resources must never use `${AWS::Region}` for that
resource's ARN — that pseudo-parameter always resolves to the stack's own
deploy region, not the target region of the action being performed.

---

## Issue 6 — Route 53 health check target misconfigured (404 from every region)

**Occurred:** 2026-07-24, 15:31-15:42 EDT, during task 3.2.

**Symptom:** The first `12-failover-dns.yml` deploy succeeded, but the
health check immediately showed `Failure: HTTP Status Code 404` from every
AWS checker region.

**Root cause:** `PrimaryHealthCheck`'s `FullyQualifiedDomainName` was set to
the API Gateway custom domain's *regional target*
(`d-xxxx.execute-api.us-east-1.amazonaws.com`) — a valid Route 53 ALIAS
target, but not something a health checker can address directly. API
Gateway custom domains route by `Host` header/SNI; connecting straight to
the raw regional name sends that name as the `Host` header, which matches
no `ApiMapping`, hence 404.

**Diagnostic commands:**
```bash
aws route53 get-health-check-status --health-check-id <id> \
  --query 'HealthCheckObservations[].{Region:Region,Status:StatusReport.Status}'

# Reproduce directly
curl -sS -o /dev/null -w "%{http_code}\n" \
  "https://d-zf3io5p23g.execute-api.us-east-1.amazonaws.com/health"
```

**Fix:** Added a second parameter, `PrimaryHealthCheckDomainName`, pointing
the health check at the *raw* `execute-api` hostname (from the existing
`ApiEndpoint` output, scheme stripped) instead of the custom domain.
`AliasTarget.DNSName` stays on the regional custom-domain target unchanged —
a real client's request keeps the `api.weather.craftingnewtech.com` Host
header regardless of which IP the ALIAS resolves to; only the *health
checker's own connection* needs the raw hostname.

**Verification:**
```bash
aws route53 get-health-check-status --health-check-id <id> \
  --query 'HealthCheckObservations[].{Region:Region,Status:StatusReport.Status}'
curl -s https://api.weather.craftingnewtech.com/health
```
Expect `Success: HTTP Status Code 200` from all 16 checker regions within
about a minute of the fix deploying.

**Prevention:** A Route 53 health check against an API Gateway custom
domain must always target the raw `execute-api` regional hostname, never
the custom domain name itself — document this explicitly next to the
`PrimaryHealthCheck` resource in `12-failover-dns.yml`.

---

## Issue 7 — Transient `UnrecognizedClientException` enabling Global Tables

**Occurred:** 2026-07-24, 10:44 EDT, task 1.5.

**Symptom:** `aws dynamodb update-table --replica-updates` failed once with
`UnrecognizedClientException` ("security token included in the request is
invalid").

**Diagnostic commands (ruled out a real credentials problem before
retrying):**
```bash
aws sts get-caller-identity
aws dynamodb describe-table --table-name WeatherCache --query 'Table.TableStatus'
```
Both succeeded immediately against the same credentials — confirmed this
was a transient AWS-side hiccup on that one API call, not an expired/invalid
session.

**Fix:** Retried the identical `update-table` call once; it succeeded
normally.

**Verification:**
```bash
aws dynamodb describe-table --region us-east-1 --table-name WeatherCache \
  --query 'Table.{Status:TableStatus,Replicas:Replicas[].{Region:RegionName,Status:ReplicaStatus}}'
```
Expect `Status: ACTIVE` with a `us-west-2` replica also `ACTIVE`.

**Prevention:** None needed beyond "retry once" — this was a genuine
transient AWS-side error, not a reproducible bug. Documented here only so a
future occurrence isn't mistaken for a credentials issue.

---

## Issue 8 — S3 bucket-naming collision deploying to a second region

**Occurred:** 2026-07-24, 12:27 EDT, task 2.1.

**Symptom:** First `aws cloudformation deploy` of `00-bootstrap.yml` to
us-west-2 failed immediately: `AWS::EarlyValidation::ResourceExistenceCheck`.

**Root cause:** `ArtifactsBucket`'s `BucketName`
(`${ProjectName}-artifacts-${AWS::AccountId}-${Environment}`) has no region
in it. S3 bucket names are globally unique across every region, so
deploying the same template to a second region always collides with the
primary region's already-existing bucket of that exact name.

**Fix:** Added a `BucketNameSuffix` parameter (default `''`, preserving the
existing primary bucket name unchanged) appended to `BucketName`:
```bash
aws cloudformation deploy \
  --region us-west-2 \
  --template-file infrastructure/cloudformation/00-bootstrap.yml \
  --stack-name weather-dashboard-bootstrap-production \
  --parameter-overrides ProjectName=weather-dashboard Environment=production BucketNameSuffix=-us-west-2
```

**Verification (confirm the primary bucket was untouched before proceeding):**
```bash
aws cloudformation describe-stacks --stack-name weather-dashboard-bootstrap-production \
  --query 'Stacks[0].{Status:StackStatus,BucketName:Outputs[?OutputKey==`ArtifactsBucketName`].OutputValue}'
```

**Prevention:** Any S3 bucket name in a CloudFormation template that might
ever be deployed to a second region needs a region-distinguishing parameter
from the start — global uniqueness across regions is easy to miss when a
template has only ever been deployed once.

---

## General-purpose diagnostic commands (reusable across future issues)

```bash
# Stack status + which resource(s) failed
aws cloudformation describe-stacks --stack-name <stack-name> --query 'Stacks[0].StackStatus'
aws cloudformation describe-stack-events --stack-name <stack-name> --max-items 30 \
  --query 'StackEvents[?contains(ResourceStatus, `FAILED`)].{Resource:LogicalResourceId,Status:ResourceStatus,Reason:ResourceStatusReason}'

# Drill into a nested stack's own events (the real error is usually one level deeper)
aws cloudformation describe-stack-resources --stack-name <stack-name> \
  --logical-resource-id <NestedStackLogicalId> --query 'StackResources[0].PhysicalResourceId' --output text
aws cloudformation describe-stack-events --stack-name <physical-id-from-above> --max-items 10 \
  --query 'StackEvents[?LogicalResourceId==`<FailedResourceId>`].{Status:ResourceStatus,Reason:ResourceStatusReason}'

# Stuck UPDATE_ROLLBACK_FAILED — fix the blocking permission, then:
aws cloudformation continue-update-rollback --stack-name <stack-name> --role-arn <deploy-role-arn>

# Pipeline execution status
aws codepipeline list-pipeline-executions --pipeline-name <pipeline-name> --max-items 1 \
  --query 'pipelineExecutionSummaries[0].{Status:status,StartTime:startTime}'
aws codepipeline get-pipeline-state --name <pipeline-name> \
  --query 'stageStates[].{Stage:stageName,Status:latestExecution.status,ExecId:latestExecution.pipelineExecutionId}'

# Route 53 health check status across all AWS checker regions
aws route53 get-health-check-status --health-check-id <id> \
  --query 'HealthCheckObservations[].{Region:Region,Status:StatusReport.Status}'

# Frontend-specific: confirm CSP connect-src matches config.js's API_BASE_URL
grep -n "API_BASE_URL" frontend/js/config.js
grep -n "connect-src" frontend/index.html
```
