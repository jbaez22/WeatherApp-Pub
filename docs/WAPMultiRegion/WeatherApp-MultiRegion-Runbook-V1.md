# WeatherApp Multi-Region — Verification Runbook — V1

**Date:** 2026-07-24
**Author:** Cloud & DevOps Engineering Review
**Scope:** Command-level verification checklist for every resource created by
[`WeatherApp-MultiRegion-ImplePlan-V1.md`](./WeatherApp-MultiRegion-ImplePlan-V1.md).
Use this to confirm what's actually live in AWS, independent of the AWS
Console (which only shows the currently-selected region — the #1 source of
"I don't see anything" confusion when checking us-west-2 resources while the
console is still pointed at us-east-1).
**Status as of this writing:** The entire multi-region implementation plan
is complete, including a real, live failover/failback drill (tasks 4.1/4.2)
— confirmed DNS shifting to us-west-2, the failover CloudWatch alarm
actually firing, automatic failback with zero manual DNS action, and the
alarm clearing. See the implementation plan's own tracking notes for the
full sequence.

---

## 1. Quick Reference

| -------------------------------- | ---------------------------------------------------------------- |
| Item                             | Value                                                            |
| -------------------------------- | ---------------------------------------------------------------- |
| AWS Account ID                   | `123456789012`                                                   |
| Primary region                   | `us-east-1`                                                      |
| Secondary (failover) region      | `us-west-2`                                                      |
| Primary API endpoint             | `https://a1b2c3d4e5.execute-api.us-east-1.amazonaws.com`         |
| Secondary API endpoint           | `https://f6g7h8i9j0.execute-api.us-west-2.amazonaws.com`         |
| Future failover domain (Phase 3) | `api.weather.craftingnewtech.com`                                |
| Primary master stack             | `weather-dashboard-master-production` (us-east-1)                |
| Secondary stack                  | `weather-dashboard-secondary-production` (us-west-2)             |
| Secondary bootstrap stack        | `weather-dashboard-bootstrap-production` (us-west-2)             |
| Secondary audit stack            | `weather-dashboard-audit-production` (us-west-2, GuardDuty only) |
| Infra pipeline                   | `weather-dashboard-infra-pipeline-production` (us-east-1 only)   |
| App pipeline                     | `weather-dashboard-app-pipeline-production` (us-east-1 only)     |
| -------------------------------- | ---------------------------------------------------------------- |

**Console region selector reminder:** CloudFormation, GuardDuty, CodeBuild,
and most other per-region service consoles only show resources in whatever
region is selected top-right. Switch to **US West (Oregon)** before looking
for anything us-west-2-specific — Route 53, IAM, and S3 bucket *listings*
are the main global/console-wide exceptions.

---

## 2. Application Health

```bash
# Primary region
curl -s -w '\nHTTP %{http_code}\n' https://a1b2c3d4e5.execute-api.us-east-1.amazonaws.com/health
curl -s -w '\nHTTP %{http_code}\n' 'https://a1b2c3d4e5.execute-api.us-east-1.amazonaws.com/weather?city=Austin'

# Secondary region
curl -s -w '\nHTTP %{http_code}\n' https://f6g7h8i9j0.execute-api.us-west-2.amazonaws.com/health
curl -s -w '\nHTTP %{http_code}\n' 'https://f6g7h8i9j0.execute-api.us-west-2.amazonaws.com/weather?city=Austin'
```
Expect `{"status": "ok", "region": "us-east-1"}` / `"us-west-2"` respectively,
both HTTP 200. The `region` field in the response is the definitive signal of
which region actually served that specific request.

---

## 3. DynamoDB Global Tables (task 1.5)

```bash
aws dynamodb describe-table --region us-east-1 --table-name WeatherCache \
  --query 'Table.{Status:TableStatus,Replicas:Replicas[].{Region:RegionName,Status:ReplicaStatus}}'
```
Expect `Status: ACTIVE` and one replica entry for `us-west-2` also `ACTIVE`.

Console: DynamoDB (either region) → Tables → `WeatherCache` → **Global tables** tab.

---

## 4. API Gateway Custom Domain + Certificate (task 1.4)

```bash
aws acm list-certificates --region us-east-1 \
  --query 'CertificateSummaryList[?DomainName==`api.weather.craftingnewtech.com`]'
aws apigatewayv2 get-domain-names --region us-east-1 \
  --query 'Items[?DomainName==`api.weather.craftingnewtech.com`]'
```
Expect cert `Status: ISSUED`. The custom domain itself won't resolve via DNS
until Phase 3's Route 53 records exist — that's expected, not a bug.

Console: API Gateway → **Custom domain names**; Certificate Manager → **Certificates**.

---

## 5. Secondary Region Stacks (tasks 2.1-2.3)

```bash
aws cloudformation describe-stacks --region us-west-2 --stack-name weather-dashboard-bootstrap-production \
  --query 'Stacks[0].{Status:StackStatus,Outputs:Outputs}'
aws cloudformation describe-stacks --region us-west-2 --stack-name weather-dashboard-secondary-production \
  --query 'Stacks[0].{Status:StackStatus,Outputs:Outputs}'
```
Expect both `UPDATE_COMPLETE` or `CREATE_COMPLETE`.

Console: CloudFormation → switch region to **Oregon** → both stacks listed.

---

## 6. Secrets Manager Cross-Region Replication (task 2.4)

```bash
aws secretsmanager describe-secret --secret-id weather-dashboard/openweathermap-api-key \
  --query '{Name:Name,ReplicationStatus:ReplicationStatus}'
```
Expect one entry: `Region: us-west-2`, `Status: InSync`, `StatusMessage:
"Replication succeeded"`.

Console: Secrets Manager (us-east-1) → the secret → **Replicate secret** section.

---

## 7. GuardDuty in the Secondary Region (task 2.5)

```bash
aws guardduty list-detectors --region us-west-2
aws cloudformation describe-stacks --region us-west-2 --stack-name weather-dashboard-audit-production \
  --query 'Stacks[0].StackStatus'
```
Expect one detector ID returned, stack `CREATE_COMPLETE`/`UPDATE_COMPLETE`.
Confirm no *second* CloudTrail trail was created (the existing us-east-1
multi-region trail already covers us-west-2):
```bash
aws cloudtrail describe-trails --region us-west-2 --query 'trailList[?contains(Name, `weather-dashboard`)]'
```
Expect exactly one trail, `HomeRegion: us-east-1`.

Console: GuardDuty → switch region to Oregon.

---

## 8. Pipeline DeploySecondary Stages (tasks 2.6/2.7)

```bash
aws codebuild batch-get-projects --names weather-dashboard-infra-deploy-secondary-production --query 'projects[0].name'
aws codebuild batch-get-projects --names weather-dashboard-app-deploy-secondary-production --query 'projects[0].name'
```
Expect both project names returned (not `null`).

```bash
# Confirm the stage actually ran and succeeded on the most recent execution
aws codepipeline get-pipeline-state --name weather-dashboard-infra-pipeline-production \
  --query "stageStates[?stageName=='DeploySecondary'].latestExecution"
aws codepipeline get-pipeline-state --name weather-dashboard-app-pipeline-production \
  --query "stageStates[?stageName=='DeploySecondary'].latestExecution"
```

Console: CodePipeline (us-east-1 only — both pipelines live only there) →
open either pipeline → `DeploySecondary` stage should now be visible between
`Deploy` and `ValidateDeployment`.

---

## 9. Route 53 Failover Routing (tasks 3.1-3.4)

```bash
# Health check status across all AWS checker regions
HEALTH_CHECK_ID=$(aws cloudformation describe-stacks --stack-name weather-dashboard-failover-dns-production \
  --query 'Stacks[0].Outputs[?OutputKey==`HealthCheckId`].OutputValue' --output text)
aws route53 get-health-check-status --health-check-id "$HEALTH_CHECK_ID" \
  --query 'HealthCheckObservations[].{Region:Region,Status:StatusReport.Status}'
```
Expect `Success: HTTP Status Code 200, OK` from every checker region. If any
show `Failure`, first check whether the health check's `FullyQualifiedDomainName`
is the *raw* `execute-api` hostname, not the custom-domain regional target
— that exact mixup caused a real 404 during initial deployment (see the
implementation plan's task 3.2 note).

```bash
# No "aws" command can show this -- list-resource-record-sets returns
# "TTL": null for both records, since Route 53's stored config for an
# ALIAS record has no TTL field at all (AliasTarget and TTL are mutually
# exclusive). The 60s value only exists as a live DNS answer Route 53
# computes on the fly for API Gateway alias targets, so a real DNS query
# is the only way to observe it. See WeatherApp-runbook.md section 12 for
# the full explanation.
dig api.weather.craftingnewtech.com +noall +answer

# Confirms the AWS CLI's own view has no TTL to check. Combine both
# filter conditions in ONE [?...] with && - chaining two separate bracket
# filters back-to-back ([?Name==x][?Type=='A']) silently returns an empty
# list instead of erroring, which then makes a trailing |[0] pipe evaluate
# to null too - a syntax bug that looks identical to a real "no TTL field"
# null. Confirmed both forms live: the combined filter below actually
# selects the record and still correctly reports null (real answer); the
# chained-bracket form returns null from selecting nothing at all (false
# answer that happens to look the same).
aws route53 list-resource-record-sets --hosted-zone-id ZEXAMPLE0000000000 \
  --query "ResourceRecordSets[?Name == 'api.weather.craftingnewtech.com.' && Type == 'A']|[0].TTL"
```
Expect `60` as the TTL value (second column) on every `dig` answer line, and
a plain `null` from the `aws route53` query above.

```bash
# Which region is currently being served
curl -s https://api.weather.craftingnewtech.com/health
```
The `region` field in the response is the definitive answer — don't infer
it from `dig`'s resolved IP alone.

Console: Route 53 → **Hosted zones** → `craftingnewtech.com` → look for the
two `api.weather.craftingnewtech.com` records (Failover: PRIMARY/SECONDARY);
Route 53 → **Health checks** → the one checking `/health` on the primary's
raw `execute-api` hostname.

---

## 10. Failover/Failback Drill (tasks 4.1/4.2)

**The mechanism:** temporarily repoint the health check's `ResourcePath` at
a path that doesn't exist. The Lambda's catch-all handler returns 404 for
any unmatched route, and Route 53 HTTPS health checks fail on *any*
non-2xx/3xx response — so this reproduces the exact same failure signal a
real 503 would, without touching the Lambda, the template, or any real
route. `/health` and `/weather` keep serving actual production traffic
completely normally for the entire drill. This is safer and simpler than
adding a throwaway 503 route (the original idea in the implementation
plan's own draft) since it needs zero code/template changes and is
trivially reversible with one more CLI call.

**Run this only with someone watching** — it's fully safe to real traffic,
but it's still a live change to a production health check.

### Step 1 — Confirm baseline

```bash
HEALTH_CHECK_ID=b299fddb-4192-42fa-b2d5-2a7063e581f4

aws route53 get-health-check-status --health-check-id "$HEALTH_CHECK_ID" \
  --query 'HealthCheckObservations[].StatusReport.Status'
curl -s -w '\nHTTP %{http_code}\n' https://api.weather.craftingnewtech.com/health
```
Expect all checker regions `Success`, and the failover domain serving
`us-east-1`.

### Step 2 — Force the health check to fail (does not touch real traffic)

```bash
aws route53 update-health-check --health-check-id "$HEALTH_CHECK_ID" \
  --resource-path /health-drill-test

# Confirm real traffic is completely unaffected - only the drill path 404s
curl -s -w '\nHTTP %{http_code}\n' https://a1b2c3d4e5.execute-api.us-east-1.amazonaws.com/health
curl -s -w '\nHTTP %{http_code}\n' 'https://a1b2c3d4e5.execute-api.us-east-1.amazonaws.com/weather?city=Austin'
curl -s -w '\nHTTP %{http_code}\n' https://a1b2c3d4e5.execute-api.us-east-1.amazonaws.com/health-drill-test
```
Expect the first two calls to still return 200 with real data; only the
drill path returns 404.

### Step 3 — Wait for Route 53 to mark it unhealthy, confirm failover

Health checks poll every 30s with a failure threshold of 3, so allow at
least ~90 seconds — in practice it took about 2 minutes for every checker
region to converge:

```bash
until [ "$(aws route53 get-health-check-status --health-check-id "$HEALTH_CHECK_ID" \
  --query 'HealthCheckObservations[?starts_with(StatusReport.Status, `Success`)]' --output text | wc -l)" -eq 0 ]; do
  sleep 15
done
echo "all checkers now report Failure"

# Confirm the actual failover
curl -s -w '\nHTTP %{http_code}\n' https://api.weather.craftingnewtech.com/health
curl -s -w '\nHTTP %{http_code}\n' 'https://api.weather.craftingnewtech.com/weather?city=Austin'
dig api.weather.craftingnewtech.com +noall +answer
```
Expect `"region": "us-west-2"` from both endpoint calls, and different
resolved IPs from `dig` than the baseline.

### Step 4 — Confirm the CloudWatch alarm actually fires

```bash
until STATE=$(aws cloudwatch describe-alarms --alarm-names weather-dashboard-route53-failover-active-production \
  --query 'MetricAlarms[0].StateValue' --output text) && [ "$STATE" = "ALARM" ]; do
  sleep 20
done
aws cloudwatch describe-alarms --alarm-names weather-dashboard-route53-failover-active-production \
  --query 'MetricAlarms[0].{State:StateValue,Reason:StateReason}'
```
Expect `State: ALARM` with real (not stale) datapoints in the reason —
CloudWatch's own metric can lag the raw health-check-observations API by a
minute or two, so don't be surprised if this takes longer than Step 3.

### Step 5 — Restore primary and confirm automatic failback

```bash
aws route53 update-health-check --health-check-id "$HEALTH_CHECK_ID" --resource-path /health

until [ "$(aws route53 get-health-check-status --health-check-id "$HEALTH_CHECK_ID" \
  --query 'HealthCheckObservations[?starts_with(StatusReport.Status, `Failure`)]' --output text | wc -l)" -eq 0 ]; do
  sleep 15
done
echo "all checkers recovered to Success"

# Confirm automatic failback - no manual DNS change of any kind
curl -s -w '\nHTTP %{http_code}\n' https://api.weather.craftingnewtech.com/health
dig api.weather.craftingnewtech.com +noall +answer
```
Expect `"region": "us-east-1"` again, with zero Route 53 *record* changes
made anywhere in this whole procedure — only the health check's
`ResourcePath` was ever touched.

### Step 6 — Confirm the alarm clears

```bash
until STATE=$(aws cloudwatch describe-alarms --alarm-names weather-dashboard-route53-failover-active-production \
  --query 'MetricAlarms[0].StateValue' --output text) && [ "$STATE" = "OK" ]; do
  sleep 20
done
echo "Alarm state: $STATE"
```

### Step 7 — Final full regression check

```bash
curl -s -w '\nHTTP %{http_code}\n' https://a1b2c3d4e5.execute-api.us-east-1.amazonaws.com/health
curl -s -w '\nHTTP %{http_code}\n' https://f6g7h8i9j0.execute-api.us-west-2.amazonaws.com/health
curl -s -w '\nHTTP %{http_code}\n' https://api.weather.craftingnewtech.com/health
curl -s -w '\nHTTP %{http_code}\n' 'https://api.weather.craftingnewtech.com/weather?city=Austin'
```
All four should return 200. This confirms both regions are independently
healthy and the failover domain is correctly serving primary again.

---

## 11. CloudWatch Log Groups

All Lambda/API Gateway log groups exist **in both regions** (same name,
different region). All CodeBuild/pipeline-Lambda log groups exist **only in
us-east-1**, since CodeCommit/CodePipeline/CodeBuild are not multi-region for
this project (see plan §0.2 #9 — this is also why the pipeline itself can't
be the disaster-recovery mechanism if us-east-1 is down).

| ----------------------------------------------------------------------- | -------------- | -------------------------------------------- |
| Log Group                                                               | Region(s)      | Purpose                                      |
| ----------------------------------------------------------------------- | -------------- | -------------------------------------------- |
| `/aws/lambda/weather-dashboard-handler-production`                      | Both           | Main Lambda handler — app logs, errors       |
| `/aws/lambda/weather-dashboard-pre-traffic-hook-production`             | Both           | CodeDeploy BeforeAllowTraffic hook           |
| `/aws/lambda/weather-dashboard-post-traffic-hook-production`            | Both           | CodeDeploy AfterAllowTraffic hook            |
| `/aws/apigateway/weather-dashboard-production`                          | Both           | API Gateway access logs                      |
| `/aws/lambda/weather-dashboard-pipeline-filter-production`              | us-east-1 only | Push-path routing decisions (infra/app)      |
| `/aws/lambda/weather-dashboard-pipeline-release-production`             | us-east-1 only | App pipeline auto-release on Infra success   |
| `/aws/codebuild/weather-dashboard-infra-validate-production`            | us-east-1 only | Infra pipeline — lint/security scan          |
| `/aws/codebuild/weather-dashboard-infra-deploy-production`              | us-east-1 only | Infra pipeline — primary region deploy       |
| `/aws/codebuild/weather-dashboard-infra-deploy-secondary-production`    | us-east-1 only | Infra pipeline — **secondary region deploy** |
| `/aws/codebuild/weather-dashboard-infra-validate-deployment-production` | us-east-1 only | Infra pipeline — post-deploy drift check     |
| `/aws/codebuild/weather-dashboard-app-validate-production`              | us-east-1 only | App pipeline — pip-audit CVE scan            |
| `/aws/codebuild/weather-dashboard-app-test-production`                  | us-east-1 only | App pipeline — pytest + coverage             |
| `/aws/codebuild/weather-dashboard-app-deploy-production`                | us-east-1 only | App pipeline — primary region deploy         |
| `/aws/codebuild/weather-dashboard-app-deploy-secondary-production`      | us-east-1 only | App pipeline — **secondary region deploy**   |
| `/aws/codebuild/weather-dashboard-app-validate-deployment-production`   | us-east-1 only | App pipeline — live smoke test               |
| ----------------------------------------------------------------------- | -------------- | -------------------------------------------- |

**Tail a log group live:**
```bash
aws logs tail /aws/lambda/weather-dashboard-handler-production --region us-east-1 --follow
aws logs tail /aws/lambda/weather-dashboard-handler-production --region us-west-2 --follow
aws logs tail /aws/codebuild/weather-dashboard-infra-deploy-secondary-production --region us-east-1 --follow
```

**Find and read a specific past invocation** (useful when a pipeline stage
already finished and you need the exact error, not a live tail):
```bash
aws logs describe-log-streams --log-group-name <group-name> --region <region> \
  --order-by LastEventTime --descending --max-items 1 --query 'logStreams[0].logStreamName' --output text

# Stream names contain a literal "$LATEST" — always single-quote them, or bash
# will try to expand it as a variable and the next command silently fails.
aws logs get-log-events --log-group-name <group-name> --region <region> \
  --log-stream-name '<stream-name-from-above>' --query 'events[].message' --output text
```

---

## 12. CloudFormation Failure Diagnosis (general reference)

```bash
# 1. Overall stack status
aws cloudformation describe-stacks --stack-name <stack-name> --query 'Stacks[0].StackStatus'

# 2. Which resource(s) failed at the top level
aws cloudformation describe-stack-events --stack-name <stack-name> --max-items 30 \
  --query 'StackEvents[?contains(ResourceStatus, `FAILED`)].{Resource:LogicalResourceId,Status:ResourceStatus,Reason:ResourceStatusReason}'

# 3. Drill into a NESTED stack's own events (the real error is usually here,
#    one level deeper than what the root stack's events show)
aws cloudformation describe-stack-resources --stack-name <stack-name> \
  --logical-resource-id <NestedStackLogicalId> --query 'StackResources[0].PhysicalResourceId' --output text
aws cloudformation describe-stack-events --stack-name <physical-id-from-above> --max-items 10 \
  --query 'StackEvents[?LogicalResourceId==`<FailedResourceId>`].{Status:ResourceStatus,Reason:ResourceStatusReason}'

# 4. All nested stacks' statuses at once (see which succeeded vs. stuck)
aws cloudformation describe-stack-resources --stack-name <stack-name> \
  --query 'StackResources[].{Id:LogicalResourceId,Status:ResourceStatus}'
```

**If a stack is stuck in `UPDATE_ROLLBACK_FAILED`:** the rollback itself
failed, usually on a missing IAM permission needed to undo the partial
change. Command #2 above shows exactly what it's blocked on. Fix the
permission (directly via `aws iam put-role-policy` if urgent, then in the
source template too), then:
```bash
aws cloudformation continue-update-rollback --stack-name <stack-name> --role-arn <deploy-role-arn>
```

---

## 13. Pipeline Execution Status (general reference)

```bash
# Latest execution status
aws codepipeline list-pipeline-executions --pipeline-name <pipeline-name> --max-items 1 \
  --query 'pipelineExecutionSummaries[0].{Status:status,StartTime:startTime}'

# Per-stage breakdown, including which execution ID each stage last ran under
# (useful to tell "this stage's Succeeded is from a PREVIOUS run" apart from
# "this stage genuinely finished for the CURRENT run")
aws codepipeline get-pipeline-state --name <pipeline-name> \
  --query 'stageStates[].{Stage:stageName,Status:latestExecution.status,ExecId:latestExecution.pipelineExecutionId}'

# Manually trigger a run (no new commit needed)
aws codepipeline start-pipeline-execution --name <pipeline-name>
```

---

*Related documents:*
- [WeatherApp-MultiRegion-ImplePlan-V1.md](./WeatherApp-MultiRegion-ImplePlan-V1.md) — the implementation plan this runbook verifies
- [WeatherApp-MultiRegion-V1.md](./WeatherApp-MultiRegion-V1.md) — the original architecture proposal
- [../WeatherApp-runbook.md](../WeatherApp-runbook.md) — the project's main operational runbook; gets a Phase 3/4 "Active Failover" section once those phases ship (plan §4.5)
