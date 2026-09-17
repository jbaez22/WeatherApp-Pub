# Runbook

Operational procedures for the Weather Dashboard. For initial deployment, see `docs/deployment-guide.md`.

**Prerequisites for all procedures:** AWS CLI v2, `us-east-1` region, admin-level IAM access.

```bash
export AWS_DEFAULT_REGION=us-east-1
export STACK_NAME=weather-dashboard-master-production
```

---

## 1. Rotate the OpenWeatherMap API Key

**As of Phase 4.2 (2026-07-14), the key lives in AWS Secrets Manager, not
SSM Parameter Store.** The Lambda reads it via `SECRET_NAME`
(`weather-dashboard/openweathermap-api-key`), cached per warm execution
environment — no config change or cold-start trick is needed to pick up a
new value; the next cold start (or the next call after the module-level
cache is cleared by a new deployment) reads it fresh. There are two paths
depending on urgency.

### 1a. Scheduled rotation (automatic, every 90 days)

Fully automated via `AWS::SecretsManager::RotationSchedule` — you'll get an
SNS email from the `weather-dashboard-key-rotation-production` topic
titled *"ACTION REQUIRED: rotate OpenWeatherMap API key"* when it's time.
OpenWeatherMap has no key-generation API, so a human has to act on that
email; the rotation Lambda can't complete on its own. Follow the exact
commands in the email (they include the correct `--client-request-token`),
or see §10's "Secret rotation notification received" below for the general
shape of the 3-step flow (generate → `put-secret-value` on `AWSPENDING` →
`rotate-secret` to resume).

### 1b. Emergency rotation (key suspected exposed — don't wait for 1a)

Bypasses the scheduled rotation flow entirely — sets the real value
directly and immediately, no waiting on the 90-day timer or the
pending/testing stages.

**Step 1 — Generate a new key** at openweathermap.org, wait ~10 minutes for activation.

**Step 2 — Set it directly on the secret** (creates a new `AWSCURRENT`
version immediately; do not paste the key value into chat/logs — run this
yourself):
```bash
aws secretsmanager put-secret-value \
  --secret-id "weather-dashboard/openweathermap-api-key" \
  --secret-string "NEW_KEY_HERE"
```

**Step 3 — Verify** the API is functional with the new key (this will only
succeed if a genuinely fresh Lambda execution environment picks it up — a
warm container may still be using the module-level cached old value for a
few more minutes; if the first check fails, wait and retry rather than
assuming something is broken):
```bash
API_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" \
  --output text)
curl -s "${API_ENDPOINT}/weather?city=London" | python3 -m json.tool | head -5
```

**Step 4 — Confirm the new key is actually what's being served** by
checking the Lambda's own confirmation log line (see §10's log-based
verification pattern) rather than trusting a 200 response alone — a 200
could still be a cached DynamoDB response that never touched the new key:
```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/weather-dashboard-handler-production \
  --start-time $(( ($(date +%s) - 600) * 1000 )) \
  --filter-pattern "Weather credential loaded from Secrets Manager" \
  --query 'events[].message' --output text
```

**Step 5 — Revoke the old key** in the OpenWeatherMap dashboard.

**Note:** the old SSM parameter (`/weather-dashboard/openweathermap-api-key`)
was decommissioned 2026-07-27 — deleted from AWS and removed from
`07-ssm.yml`. See `docs/WeatherApp-ImproveDeploys-Plan-V2.md` §4.2 for the
original migration record.

---

## 2. Roll Back a Failed Deployment

### 2a. Pipeline-triggered deployment (CloudFormation rollback)

CloudFormation automatically rolls back on stack failure. Monitor the rollback:
```bash
aws cloudformation describe-stack-events \
  --stack-name "$STACK_NAME" \
  --query "StackEvents[?ResourceStatus=='ROLLBACK_IN_PROGRESS' || ResourceStatus=='ROLLBACK_COMPLETE']" \
  --output table
```

If the stack is stuck in `ROLLBACK_FAILED`:
```bash
# List resources that could not be rolled back
aws cloudformation describe-stack-events \
  --stack-name "$STACK_NAME" \
  --query "StackEvents[?ResourceStatus=='UPDATE_ROLLBACK_FAILED'].[LogicalResourceId,ResourceStatusReason]" \
  --output table

# Continue rollback, skipping unrecoverable resources (provide specific resource IDs)
aws cloudformation continue-update-rollback \
  --stack-name "$STACK_NAME" \
  --resources-to-skip LogicalResourceId1 LogicalResourceId2
```

### 2b. Manual frontend rollback (revert to previous S3 content)

S3 versioning is enabled on the artifacts bucket but not the website bucket. To revert the frontend, push the previous commit to CodeCommit and let the pipeline redeploy:

```bash
git revert HEAD --no-edit
git push both main
```

### 2c. Emergency: disable the API temporarily

Set Lambda reserved concurrency to zero — API Gateway will return 429 for all requests:
```bash
FUNCTION_NAME=weather-dashboard-handler-production

aws lambda put-function-concurrency \
  --function-name "$FUNCTION_NAME" \
  --reserved-concurrent-executions 0

# Restore (set back to 10 or remove the reservation):
aws lambda put-function-concurrency \
  --function-name "$FUNCTION_NAME" \
  --reserved-concurrent-executions 10
```

### 2d. Baseline Tags (rollback reference points)

Before starting a multi-phase improvement rollout, tag the current commit and
record the deployed Lambda's `CodeSha256` here — gives every later phase a
known-good `git revert` target and a way to confirm via
`aws lambda get-function` whether a rollback actually restored the prior code.

**`pre-improvements-v1`** — 2026-07-13, baseline before
`WeatherApp-ImproveDeploys-Plan-V2.md` rollout (Phases 0-4, WAF/Phase 5
excluded).
- Lambda `CodeSha256`: `QcNpuVE3GtDI78oUJyaEtUO7UfU2nlMtEns0EbwVBKU=`
- Version: `$LATEST`, last modified `2026-07-09T21:08:34Z`
- Confirm current code matches this baseline:
  ```bash
  aws lambda get-function --function-name weather-dashboard-handler-production \
    --query Configuration.CodeSha256
  ```
- Rollback anchor: `git revert` any commit back to this tag, or
  `git checkout pre-improvements-v1 -- backend/ infrastructure/` to restore
  specific paths without a full revert.

---

## 3. Clear the DynamoDB Weather Cache

Clears all cached city entries, forcing fresh OWM lookups on next request.

```bash
TABLE_NAME=WeatherCache

# Scan all items and delete each one
aws dynamodb scan \
  --table-name "$TABLE_NAME" \
  --projection-expression "city" \
  --output json | \
python3 -c "
import sys, json, subprocess
items = json.load(sys.stdin)['Items']
for item in items:
    city = item['city']['S']
    subprocess.run([
        'aws', 'dynamodb', 'delete-item',
        '--table-name', '$TABLE_NAME',
        '--key', '{\"city\":{\"S\":\"' + city + '\"}}'
    ])
    print(f'Deleted: {city}')
print(f'{len(items)} items deleted.')
"
```

To clear a single city:
```bash
aws dynamodb delete-item \
  --table-name "$TABLE_NAME" \
  --key '{"city":{"S":"london"}}'
```

---

## 4. Force a CloudFront Cache Invalidation

Invalidates all cached objects immediately. Use after a frontend deployment if CloudFront is serving stale content.

```bash
CF_DOMAIN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDomain'].OutputValue" \
  --output text)
DIST_ID=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?DomainName=='$CF_DOMAIN'].Id" --output text)

aws cloudfront create-invalidation \
  --distribution-id "$DIST_ID" \
  --paths "/*"
```

Monitor invalidation progress:
```bash
INVALIDATION_ID=$(aws cloudfront list-invalidations \
  --distribution-id "$DIST_ID" \
  --query "InvalidationList.Items[0].Id" \
  --output text)

aws cloudfront get-invalidation \
  --distribution-id "$DIST_ID" \
  --id "$INVALIDATION_ID" \
  --query "Invalidation.Status"
```

Status transitions: `InProgress` → `Completed` (typically < 60 seconds).

---

## 5. Trigger and Monitor a Pipeline Run

```bash
export INFRA_PIPELINE=weather-dashboard-infra-pipeline-production
export APP_PIPELINE=weather-dashboard-app-pipeline-production
```

Split into two independent pipelines since the pipeline split
(`docs/WeatherApp-PipeSplit-ImplePlan-V1.md`): **Infra** (6 stages: Source
→ Validate → ApproveDeploy → Deploy → DeploySecondary → ValidateDeployment —
CloudFormation stack changes only, no Lambda/frontend/CDN actions) and **App**
(7 stages: Source → Validate → Test → ApproveDeploy → Deploy →
DeploySecondary → ValidateDeployment — Lambda code, frontend, CDN, never
touches a CloudFormation stack). `DeploySecondary` was added by the
multi-region rollout (`WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md`)
and only runs when a secondary region is configured. `Deploy`
on the App pipeline publishes a new Lambda version and shifts traffic to
it via a CodeDeploy canary (10% for 5 minutes, watched against the
`lambda-errors`/`api-5xx` alarms, with a pre-traffic hook test call before
any real traffic shifts — see `docs/WeatherApp-Canary-Implementation-V1.md`
for the full mechanism). `git push both main` never changes regardless of
which pipeline(s) end up running — a filter Lambda inspects the changed
paths on every push and decides.

### 5a. Confirm a push actually triggered the right pipeline(s)

A Lambda filter (`weather-dashboard-pipeline-filter-production`)
classifies every push's changed paths as infra
(`infrastructure/cloudformation/**`, `pipeline/**`), app
(`backend/lambda/**`, `frontend/**`, `diagrams/interactive/**`,
`scripts/**`), docs (skipped entirely — `docs/`, `diagrams/*.md`, a
top-level `*.md` file, or a non-functional root file like `.gitignore`/
`.gitattributes`), or an unrecognized path (fail-safe: treated as both).
A `.gitignore`-only edit used to fall into the fail-safe and trigger both
pipelines for nothing — fixed 2026-07-16, see
`project_pipeline_filter_gitignore_bug` in project memory. Confirm which
way a push went:

```bash
# Did a new execution start on either pipeline after your push?
aws codepipeline list-pipeline-executions --pipeline-name "$INFRA_PIPELINE" \
  --max-items 1 --query 'pipelineExecutionSummaries[0].{id:pipelineExecutionId,status:status,start:startTime}' \
  --output table
aws codepipeline list-pipeline-executions --pipeline-name "$APP_PIPELINE" \
  --max-items 1 --query 'pipelineExecutionSummaries[0].{id:pipelineExecutionId,status:status,start:startTime}' \
  --output table

# See the filter's own routing decision (its most recent invocation)
STREAM=$(aws logs describe-log-streams \
  --log-group-name /aws/lambda/weather-dashboard-pipeline-filter-production \
  --order-by LastEventTime --descending --max-items 1 \
  --query 'logStreams[0].logStreamName' --output text)
aws logs get-log-events \
  --log-group-name /aws/lambda/weather-dashboard-pipeline-filter-production \
  --log-stream-name "$STREAM" --query 'events[].message' --output text
```
Expect one of: `Docs-only commit — skipping both pipelines` (no new
execution on either), `infra-only` / `app-only` (one new execution on the
matching pipeline), or `combined-infra-first` (Infra starts immediately;
the App pipeline is *held*, not started yet — see 5f).

### 5b. Check whether it's running, and which stage

**Easier option:** `docs/tools/check-pipeline-status.sh` (see #5g below)
does this in one command, for both pipelines, and won't show a stale
prior-execution's stage status as if it were current — the raw commands
below can. Kept here as the no-script fallback and for understanding the
underlying mechanism.

```bash
aws codepipeline get-pipeline-state --name "$INFRA_PIPELINE" \
  --query "stageStates[].{stage:stageName,status:latestExecution.status,execId:latestExecution.pipelineExecutionId}" \
  --output table
aws codepipeline get-pipeline-state --name "$APP_PIPELINE" \
  --query "stageStates[].{stage:stageName,status:latestExecution.status,execId:latestExecution.pipelineExecutionId}" \
  --output table
```

**Known lag:** this can show a stage as stale or `InProgress` for
30-90+ seconds after the underlying CodeBuild project has actually
finished. If a stage looks stuck, cross-check the build directly instead
of waiting on the pipeline view to catch up:

```bash
# Infra: weather-dashboard-infra-validate-production, -infra-deploy-production,
#        -infra-validate-deployment-production
# App:   weather-dashboard-app-validate-production, -app-test-production,
#        -app-deploy-production, -app-validate-deployment-production
BUILD_ID=$(aws codebuild list-builds-for-project \
  --project-name weather-dashboard-infra-deploy-production \
  --query 'ids[0]' --output text)
aws codebuild batch-get-builds --ids "$BUILD_ID" \
  --query 'builds[0].{status:buildStatus,phase:currentPhase}' --output table
```

To watch continuously:
```bash
watch -n 10 "aws codepipeline get-pipeline-state --name '$INFRA_PIPELINE' \
  --query 'stageStates[*].[stageName,latestExecution.status]' --output table"
```

### 5c. Approve (or reject) a pending deploy

**Easier option:** `docs/tools/check-pipeline-status.sh` (see #5g below)
prints this exact command with a live token already filled in whenever a
pipeline is genuinely waiting on approval, and its `--interactive` mode
can submit the approval/rejection itself after you type `approve` or
`reject`. Kept here as the no-script fallback.

Both pipelines have their own `ApproveDeploy` gate and their own SNS
topic — approving one does **not** approve the other.

```bash
# Infra pipeline
TOKEN=$(aws codepipeline get-pipeline-state --name "$INFRA_PIPELINE" \
  --query "stageStates[?stageName=='ApproveDeploy'].actionStates[0].latestExecution.token" \
  --output text)
aws codepipeline put-approval-result \
  --pipeline-name "$INFRA_PIPELINE" --stage-name ApproveDeploy --action-name ManualApproval \
  --result "summary=Reviewed and approved,status=Approved" \
  --token "$TOKEN"

# App pipeline (same shape, different topic/pipeline)
TOKEN=$(aws codepipeline get-pipeline-state --name "$APP_PIPELINE" \
  --query "stageStates[?stageName=='ApproveDeploy'].actionStates[0].latestExecution.token" \
  --output text)
aws codepipeline put-approval-result \
  --pipeline-name "$APP_PIPELINE" --stage-name ApproveDeploy --action-name ManualApproval \
  --result "summary=Reviewed and approved,status=Approved" \
  --token "$TOKEN"
```

Subscribe once per topic if you haven't already (each pipeline has its
own — subscribing to one does not cover the other):
```bash
aws sns subscribe --topic-arn arn:aws:sns:us-east-1:123456789012:weather-dashboard-deploy-approval-production \
  --protocol email --notification-endpoint you@example.com
aws sns subscribe --topic-arn arn:aws:sns:us-east-1:123456789012:weather-dashboard-app-deploy-approval-production \
  --protocol email --notification-endpoint you@example.com
```

**Only approve/reject an execution you have actually reviewed and
intended to act on** — never as a way to "unstick" a pipeline without
understanding why it's stuck; diagnose first (see §10 below). This
matters more now than it did with a single pipeline: approving a *stale*
execution (one whose Source snapshot predates a fix already pushed after
it) can silently **revert** that fix the moment its Deploy stage runs —
if you're not sure an execution is current, check which commit its Source
stage actually pulled before approving anything:
```bash
aws codepipeline list-action-executions --pipeline-name "$INFRA_PIPELINE" \
  --query 'actionExecutionDetails[?stageName==`Source`] | [0].output.executionResult.externalExecutionId' \
  --output text
```

### 5d. Confirm a run actually completed, and that it did what you expect

```bash
EXEC_ID=<execution-id-from-5a-or-5b>

aws codepipeline get-pipeline-execution --pipeline-name "$INFRA_PIPELINE" \
  --pipeline-execution-id "$EXEC_ID" --query 'pipelineExecution.status' --output text
```
Terminal values: `Succeeded`, `Failed`, `Stopped`, `Superseded`. Green
pipeline stages alone don't prove the deploy did what you think — always
also verify production directly:
```bash
curl -s -o /dev/null -w "Site HTTP %{http_code}\n" "https://weather.craftingnewtech.com/"
aws lambda get-alias --function-name weather-dashboard-handler-production \
  --name live --query '{version:FunctionVersion,routing:RoutingConfig}' --output json
```
`routing` should be `null` (canary complete, 100% on the new version) and
`version` should be a real integer higher than before the deploy — this
only changes after an **App** pipeline `Deploy`; an Infra-only deploy
never touches the Lambda function at all.

### 5e. Trigger a run manually (no code push)

Rarely needed — most changes should go through `git push both main` so
they're reviewable in source control. Use this only for re-running an
existing commit (e.g. after fixing an IAM permission out-of-band):
```bash
aws codepipeline start-pipeline-execution --name "$INFRA_PIPELINE"
# or
aws codepipeline start-pipeline-execution --name "$APP_PIPELINE"
```
Manually starting the App pipeline this way does **not** go through the
infra-before-app coordination check in 5f — only do this if you're certain
the Infra side is already in the state the App change depends on.

### 5f. The infra-before-app coordination mechanism

When a single commit touches both infra and app paths, the filter Lambda
starts the Infra pipeline immediately and writes an SSM parameter holding
that commit's hash — the App pipeline is *not* started yet:
```bash
aws ssm get-parameter \
  --name /weather-dashboard/pipeline-coordination/pending-app-release-production \
  --query 'Parameter.Value' --output text
```
`ParameterNotFound` is the normal idle state — nothing pending. A value
present means an App release is queued behind an in-flight Infra deploy.
Once the Infra pipeline reaches `SUCCEEDED`, an EventBridge rule
(`weather-dashboard-infra-release-app-production`) invokes a release
Lambda (`weather-dashboard-pipeline-release-production`) that starts the
App pipeline and clears the parameter:
```bash
aws logs tail /aws/lambda/weather-dashboard-pipeline-release-production --since 30m
```
If the Infra pipeline instead ends in `FAILED` or `STOPPED`, the release
Lambda clears the parameter **without** starting the App pipeline. Fix the
Infra deploy and re-push to retry the whole combined change — don't
manually start the App pipeline in this case; its half of the change may
depend on the infra change that didn't land.

### 5g. Check status and approve deploys without the console — `check-pipeline-status.sh`

`docs/tools/check-pipeline-status.sh` is a self-contained wrapper over
5b/5c above — one command instead of remembering the raw `aws
codepipeline` calls, and it never confuses a stale prior-execution's
per-stage status with the current run's (cross-checks each stage's
`latestExecution.pipelineExecutionId` against the pipeline's own most
recent execution and labels anything that doesn't match — a real mistake
hit three separate times building this tool; see
`WeatherApp-BrowserCacheMemory-Details-V1.md` #7.6 for the class of
confusion this same pattern produces elsewhere). No environment variables
needed — just active AWS CLI credentials, same as any command above.

**One-shot check** (defaults to both this project's pipelines if no name given):
```bash
bash docs/tools/check-pipeline-status.sh
bash docs/tools/check-pipeline-status.sh weather-dashboard-app-pipeline-production
```

**Continuous monitor** — re-checks every 30 seconds, stops automatically
once every watched pipeline reaches a terminal status
(Succeeded/Failed/Stopped/Superseded), or Ctrl+C to stop early. Exits 0 if
everything Succeeded, 1 otherwise:
```bash
bash docs/tools/check-pipeline-status.sh --watch weather-dashboard-infra-pipeline-production
```

**Pending-approval detection:** whenever a manual-approval action is
currently waiting, the output includes a ready-to-run
`aws codepipeline put-approval-result` command with the real token
already filled in:
```
>>> ACTION NEEDED: 'ApproveDeploy' is waiting on manual approval.
    Approve without opening the console:
      aws codepipeline put-approval-result \
        --pipeline-name weather-dashboard-infra-pipeline-production \
        --stage-name ApproveDeploy \
        --action-name ManualApproval \
        --result summary="Approved via CLI",status=Approved \
        --token "<real-token>"
    (swap status=Approved for status=Rejected to reject it instead)
```

**Interactive approval** — `--interactive` (or `-i`) prompts to type the
exact word `approve` or `reject` (not just `y`/`n` — anything else,
including a blank Enter, safely skips with no action taken) plus an
optional message, then submits the result directly:
```bash
bash docs/tools/check-pipeline-status.sh --interactive weather-dashboard-infra-pipeline-production
```

`--interactive` is deliberately ignored when combined with `--watch` —
prompting for input every 30-second refresh would defeat the point of an
unattended watch loop. Run them as two separate invocations instead (e.g.
two terminal tabs) — a `--watch` monitor and a one-shot `--interactive`
approval don't conflict, since watch only ever makes read-only calls:
```bash
# Tab 1 — continuous monitor
bash docs/tools/check-pipeline-status.sh --watch weather-dashboard-infra-pipeline-production
# Tab 2 — approve when ready
bash docs/tools/check-pipeline-status.sh --interactive weather-dashboard-infra-pipeline-production
```
After approving in Tab 2, Tab 1 picks up the change on its next 30-second
cycle, not instantly — expected, not a bug.

Reusable across projects — the only project-specific part is the
`DEFAULT_PIPELINES` list at the top of the script; everything else is
generic CodePipeline API calls.

---

## 6. View Recent Lambda Errors

```bash
FUNCTION_NAME=weather-dashboard-handler-production
LOG_GROUP="/aws/lambda/$FUNCTION_NAME"

# Last 50 error events
aws logs filter-log-events \
  --log-group-name "$LOG_GROUP" \
  --filter-pattern "ERROR" \
  --start-time "$(date -d '1 hour ago' +%s000 2>/dev/null || date -v-1H +%s000)000" \
  --query "events[*].message" \
  --output text
```

---

## 7. Update the Lambda Function Code

Only needed if you want to deploy a Lambda update without touching other resources. Normally the pipeline handles this.

```bash
# Package Lambda
pip install -r backend/lambda/requirements.txt -t /tmp/lambda-pkg
cp backend/lambda/*.py /tmp/lambda-pkg/
cd /tmp/lambda-pkg && zip -r /tmp/lambda.zip . && cd -

# Upload to artifacts bucket
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ARTIFACTS_BUCKET="weather-dashboard-artifacts-${ACCOUNT_ID}-production"

aws s3 cp /tmp/lambda.zip "s3://$ARTIFACTS_BUCKET/lambda/weather_handler.zip"

# Update function code
FUNCTION_NAME=weather-dashboard-handler-production

aws lambda update-function-code \
  --function-name "$FUNCTION_NAME" \
  --s3-bucket "$ARTIFACTS_BUCKET" \
  --s3-key "lambda/weather_handler.zip"
```

---

## 8. Tear Down the Stack

Destroys all AWS resources created by the **master** CloudFormation stack.
**This is irreversible.** It does **not** touch the standalone audit stack
(`weather-dashboard-audit-production` — CloudTrail + GuardDuty, Phase 4.1)
by design — that stack is deliberately separate so account-level security
tooling survives an application teardown. Tear that down separately, on
purpose, if you actually want to remove it too (see the end of this
section).

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# 1. Empty the website bucket (S3 cannot delete non-empty buckets)
WEBSITE_BUCKET="weather-dashboard-website-${ACCOUNT_ID}-production"
aws s3 rm "s3://$WEBSITE_BUCKET" --recursive

# 2. Empty the artifacts bucket
ARTIFACTS_BUCKET="weather-dashboard-artifacts-${ACCOUNT_ID}-production"
aws s3 rm "s3://$ARTIFACTS_BUCKET" --recursive
# Also delete versioned objects
aws s3api delete-objects \
  --bucket "$ARTIFACTS_BUCKET" \
  --delete "$(aws s3api list-object-versions \
    --bucket "$ARTIFACTS_BUCKET" \
    --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' \
    --output json)" 2>/dev/null || true

# 3. Delete the stack
aws cloudformation delete-stack --stack-name "$STACK_NAME"

# 4. Monitor deletion
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
echo "Stack deleted."

# 5. Delete resources kept alive with DeletionPolicy: Retain (not
#    removed automatically by the stack delete above)
aws ssm delete-parameter --name "/weather-dashboard/openweathermap-api-key"
aws secretsmanager delete-secret \
  --secret-id "weather-dashboard/openweathermap-api-key" \
  --recovery-window-in-days 7   # omit --force-delete-without-recovery unless you're certain

# 6. Delete CloudWatch log groups (DeletionPolicy: Retain)
for lg in \
  /aws/lambda/weather-dashboard-handler-production \
  /aws/lambda/weather-dashboard-pre-traffic-hook-production \
  /aws/lambda/weather-dashboard-post-traffic-hook-production \
  /aws/lambda/weather-dashboard-key-rotation-production \
  /aws/lambda/weather-dashboard-pipeline-filter-production \
  /aws/lambda/weather-dashboard-pipeline-release-production \
  /aws/apigateway/weather-dashboard-production \
  /aws/codebuild/weather-dashboard-infra-validate-production \
  /aws/codebuild/weather-dashboard-infra-deploy-production \
  /aws/codebuild/weather-dashboard-infra-validate-deployment-production \
  /aws/codebuild/weather-dashboard-app-validate-production \
  /aws/codebuild/weather-dashboard-app-test-production \
  /aws/codebuild/weather-dashboard-app-deploy-production \
  /aws/codebuild/weather-dashboard-app-validate-deployment-production
do
  aws logs delete-log-group --log-group-name "$lg" 2>/dev/null || true
done
```

**To also tear down the audit stack** (CloudTrail + GuardDuty, Phase
4.1) — a separate, deliberate decision, not part of the app teardown above:
```bash
# Empty the CloudTrail S3 bucket first (same non-empty-bucket constraint)
aws s3 rm "s3://weather-dashboard-cloudtrail-${ACCOUNT_ID}-production" --recursive
aws cloudformation delete-stack --stack-name weather-dashboard-audit-production
aws cloudformation wait stack-delete-complete --stack-name weather-dashboard-audit-production
```

---

## 9. Error-Budget Freeze Policy (SLO)

SLO targets (per `WeatherApp-Main-Improves-V1.md`): 99.9% availability,
p99 API latency < 1000ms, error rate < 0.1%. The CloudWatch dashboard's
"SLO — Error Rate" and "SLO — API Latency p99" widgets track these against
their annotation lines in real time.

When the 30-day rolling error rate exceeds 50% of the 0.1% SLO budget (i.e.,
sustained error rate > 0.05%), freeze non-critical deploys until the budget
recovers. This is a process control, not something CloudFormation enforces —
an operator has to check and decide. Check current burn rate:

```bash
aws cloudwatch get-metric-data --start-time $(date -u -v-30d +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --metric-data-queries file://scripts/slo-query.json
```

`scripts/slo-query.json` hardcodes the current API Gateway `ApiId`
(`876mh0k6q1`) as a dimension value — if the API Gateway is ever recreated
(new `ApiId`), update that file to match before trusting this query's
output. Confirm the current ID: `aws cloudformation describe-stacks
--stack-name weather-dashboard-master-production --query
"Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue"`.

Resume normal deploys once the rolling error rate drops back under the 0.05%
line. Security-critical fixes are exempt from the freeze — this policy is
about avoiding *additional* deploy risk while the budget is already spent,
not about blocking fixes for the thing spending it.

> The artifacts bucket was pre-created before the stack. After teardown, also delete it manually if no longer needed:
> ```bash
> aws s3 rb "s3://$ARTIFACTS_BUCKET" --force
> ```

---

## 10. Common Troubleshooting

Symptom-first — find the closest match. For the full incident history from
implementing `WeatherApp-ImproveDeploys-Plan-V2.md` (every distinct bug hit
building this pipeline/canary/secrets setup, with root causes), see
`docs/WeatherApp-ImproveDeploys-Plan-TroubleshootingSteps-V1.md`. This
section covers *ongoing operational* symptoms, not that build history.

### Pipeline stuck at `ApproveDeploy` (either pipeline)

Expected — it waits indefinitely for a human. Check who needs to act:
```bash
aws codepipeline get-pipeline-state --name weather-dashboard-infra-pipeline-production \
  --query "stageStates[?stageName=='ApproveDeploy'].actionStates[0].latestExecution"
aws codepipeline get-pipeline-state --name weather-dashboard-app-pipeline-production \
  --query "stageStates[?stageName=='ApproveDeploy'].actionStates[0].latestExecution"
```
If it's been stuck far longer than expected and you don't recognize the
commit, check what actually changed before approving — see §5c's warning
about approving a *stale* execution silently reverting a fix already
pushed after it:
```bash
EXEC_ID=<execution-id>
aws codepipeline get-pipeline-execution --pipeline-name weather-dashboard-infra-pipeline-production \
  --pipeline-execution-id "$EXEC_ID" --query 'pipelineExecution.artifactRevisions[0].revisionSummary'
```

### Infra pipeline failed at `Validate`

Gates 1-3 only (cfn-lint, checkov, `aws cloudformation validate-template`)
— the CVE scan (gate 4, `pip-audit`) moved to the App pipeline's own
Validate stage since the split. Reproduce locally:
```bash
bash pipeline/scripts/validate-templates.sh
```
This still runs all 4 original gates locally regardless of which pipeline
they now live in — narrowing CI blast radius per pipeline doesn't mean
narrowing what you check before pushing.

### App pipeline failed at `Validate`

Just gate 4 (`pip-audit` on `backend/lambda/requirements.txt`) —
reproduce with `pip-audit -r backend/lambda/requirements.txt` locally.

### App pipeline failed at `Test`

Reproduce locally:
```bash
bash pipeline/scripts/run-tests.sh
```
Coverage gate is 80%; a genuinely failing/flaky test will fail identically
locally. If it passes locally but failed in CodeBuild, check the build log
for an environment difference (missing env var, different Python version)
rather than assuming it's the same bug. (The Infra pipeline has no Test
stage since the split — its Deploy never touches application code.)

### Infra pipeline failed at `Deploy`

Get the CodeBuild log directly (faster and more complete than the
CodePipeline console view):
```bash
BUILD_ID=$(aws codebuild list-builds-for-project \
  --project-name weather-dashboard-infra-deploy-production --query 'ids[0]' --output text)
aws logs get-log-events \
  --log-group-name /aws/codebuild/weather-dashboard-infra-deploy-production \
  --log-stream-name "$(echo "$BUILD_ID" | cut -d: -f2)" \
  --query 'events[].message' --output text
```
If it failed inside the CloudFormation update itself (not the buildspec),
get the actual resource-level reason, not just "stack update failed":
```bash
aws cloudformation describe-stack-events --stack-name weather-dashboard-master-production \
  --query "StackEvents[?contains(ResourceStatus, 'FAILED')].{Resource:LogicalResourceId,Reason:ResourceStatusReason}" \
  --output table
# For a nested stack's own resources, use the physical stack name/ARN from
# the parent's Reason field (it reads "Embedded stack arn:...:stack/<name>/...")
```
**Production is not at risk from a failed Infra Deploy stage on its
own** — it never touches Lambda code, the frontend, or CloudFront; those
are exclusively the App pipeline's job. A failed CFN update here means the
*next* infra change can't land until it's fixed, not that the live site is
degraded right now.

### App pipeline failed at `Deploy`

Same pattern, different project — this is the one that publishes a new
Lambda version and runs the CodeDeploy canary shift:
```bash
BUILD_ID=$(aws codebuild list-builds-for-project \
  --project-name weather-dashboard-app-deploy-production --query 'ids[0]' --output text)
aws logs get-log-events \
  --log-group-name /aws/codebuild/weather-dashboard-app-deploy-production \
  --log-stream-name "$(echo "$BUILD_ID" | cut -d: -f2)" \
  --query 'events[].message' --output text
```
**Production is not at risk from a failed Deploy stage on its own** — the
Lambda `live` alias only moves once the CodeDeploy canary and its
pre-traffic hook both pass; a failure before that point (Lambda publish,
zip packaging) never touches the alias. Confirm:
```bash
aws lambda get-alias --function-name weather-dashboard-handler-production \
  --name live --query '{version:FunctionVersion,routing:RoutingConfig}' --output json
```

### CodeDeploy canary failed or rolled back mid-shift

```bash
aws deploy list-deployments --application-name weather-dashboard-production \
  --deployment-group-name weather-dashboard-handler-dg-production \
  --query 'deployments[0]' --output text
aws deploy get-deployment --deployment-id <id> \
  --query 'deploymentInfo.{status:status,error:errorInformation}'
```
`errorInformation` gives the real reason (a failed pre-traffic hook, an
alarm firing, or an IAM permission gap). Check the hook Lambdas' own logs
if the failure is `HOOK_EXECUTION_FAILURE`:
```bash
aws logs tail /aws/lambda/weather-dashboard-pre-traffic-hook-production --since 30m
aws logs tail /aws/lambda/weather-dashboard-post-traffic-hook-production --since 30m
```
The alias auto-reverts to the last known-good version on any canary
failure — no manual rollback needed, just confirm it actually happened
(§5d above).

### Site or API returning 5xx

```bash
# Confirm which layer is failing
curl -s -o /dev/null -w "Site: %{http_code}\n" https://weather.craftingnewtech.com/
curl -s "$(aws cloudformation describe-stacks --stack-name weather-dashboard-master-production \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text)/weather?city=London" \
  -o /dev/null -w "API: %{http_code}\n"

# Recent Lambda errors
aws logs tail /aws/lambda/weather-dashboard-handler-production --since 15m --filter-pattern "ERROR"

# Alarm state
aws cloudwatch describe-alarms \
  --alarm-names weather-dashboard-lambda-errors-production weather-dashboard-api-5xx-production \
  --query 'MetricAlarms[].{name:AlarmName,state:StateValue}' --output table
```
If errors are all `SecretsError`/`ClientError` on Secrets Manager, check
the secret and the Lambda's IAM permission before assuming it's an OWM
outage:
```bash
aws secretsmanager describe-secret --secret-id weather-dashboard/openweathermap-api-key \
  --query '{RotationEnabled:RotationEnabled,LastChangedDate:LastChangedDate}'
```

### Secret rotation notification received (SNS email)

Expected every 90 days — OpenWeatherMap has no key-generation API, so
rotation is human-in-the-loop by design (see
`infrastructure/cloudformation/07-ssm.yml`'s `RotationFunction`). The email
contains the exact commands to run:
```bash
# 1. Generate a new key at openweathermap.org
# 2. Set it on the pending version (command + token are in the email)
aws secretsmanager put-secret-value --secret-id weather-dashboard/openweathermap-api-key \
  --version-stage AWSPENDING --client-request-token <token-from-email> --secret-string "<new-key>"
# 3. Resume rotation
aws secretsmanager rotate-secret --secret-id weather-dashboard/openweathermap-api-key \
  --client-request-token <token-from-email>
```
If rotation fails at the `testSecret` step, the new key likely hasn't
activated yet (OWM keys take ~10 minutes) — wait and retry the
`rotate-secret` call with the same token.

### GuardDuty finding received (SNS email)

```bash
aws guardduty list-findings --detector-id "$(aws guardduty list-detectors --query 'DetectorIds[0]' --output text)" \
  --finding-criteria '{"Criterion":{"severity":{"Gte":4}}}'
aws guardduty get-findings --detector-id <id> --finding-ids <id> --query 'Findings[0]'
```
Triage severity before acting — GuardDuty findings in this account are rare
by design (small, single-application footprint); a Low/Medium finding on a
known, expected pattern (e.g. your own IP recon-scanning during testing)
can usually be archived, but anything High/Critical or unrecognized should
be investigated fully before dismissing.

---

## 11. Routine Operational Procedures

### Weekly
- Check the CloudWatch dashboard (`DashboardUrl` stack output) for the
  SLO widgets (error rate, p99 latency) trending against their target lines.
- Confirm no alarms are in `ALARM` state:
  ```bash
  aws cloudwatch describe-alarms --alarm-name-prefix weather-dashboard \
    --state-value ALARM --query 'MetricAlarms[].AlarmName'
  ```
- Skim both pipelines' execution history for any `Failed` runs that were
  retried without root-causing:
  ```bash
  aws codepipeline list-pipeline-executions --pipeline-name weather-dashboard-infra-pipeline-production \
    --max-items 10 --query "pipelineExecutionSummaries[?status=='Failed']"
  aws codepipeline list-pipeline-executions --pipeline-name weather-dashboard-app-pipeline-production \
    --max-items 10 --query "pipelineExecutionSummaries[?status=='Failed']"
  ```

### Monthly
- Review AWS Cost Explorer / Budgets for this project's tag
  (`Project=weather-dashboard`) against the documented cost estimate in
  `docs/WeatherApp-ImproveDeploys-Plan-V2.md`'s cost tables — investigate
  any unexplained delta.
- Confirm CloudTrail is still logging and GuardDuty still has exactly one
  active detector (both are account-level singletons that should never be
  accidentally duplicated or disabled):
  ```bash
  aws cloudtrail get-trail-status --name weather-dashboard-trail-production --query IsLogging
  aws guardduty list-detectors --query 'DetectorIds'
  ```
- Verify the SNS subscriptions for deploy approval, key rotation, and
  security findings are all still `Confirmed`, not `PendingConfirmation`
  (subscriptions can silently lapse if an inbox changes):
  ```bash
  for topic in deploy-approval key-rotation security-findings; do
    aws sns list-subscriptions-by-topic \
      --topic-arn "arn:aws:sns:us-east-1:123456789012:weather-dashboard-$topic-production" \
      --query 'Subscriptions[].{Endpoint:Endpoint,Status:SubscriptionArn}' --output table
  done
  ```

### Every 90 days (or on suspected exposure)
- OpenWeatherMap API key rotation — see §10's "Secret rotation
  notification received" above; runs automatically via
  `RotationSchedule`, but confirm it actually completed:
  ```bash
  aws secretsmanager describe-secret --secret-id weather-dashboard/openweathermap-api-key \
    --query '{LastRotatedDate:LastRotatedDate,NextRotationDate:NextRotationDate}'
  ```

### Before any infrastructure change
- Run `bash pipeline/scripts/validate-templates.sh` and `bash
  pipeline/scripts/run-tests.sh` locally — never let the pipeline be the
  first place a template or test error surfaces.
- For any CloudFormation change beyond a single, obviously isolated
  resource, dry-run it against production first:
  ```bash
  aws cloudformation package --template-file infrastructure/cloudformation/master.yml \
    --s3-bucket weather-dashboard-artifacts-123456789012-production --s3-prefix cloudformation \
    --output-template-file infrastructure/cloudformation/master-packaged.yml
  aws cloudformation deploy --template-file infrastructure/cloudformation/master-packaged.yml \
    --stack-name weather-dashboard-master-production \
    --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
    --role-arn arn:aws:iam::123456789012:role/weather-dashboard-cfn-deploy-role-production \
    --no-execute-changeset
  # Inspect with describe-change-set --include-property-values, then
  # aws cloudformation delete-change-set when done — never leave a stale
  # change set lying around.
  ```

### Emergency contacts / escalation
- Primary owner: Cloud & DevOps Engineering (this project has no on-call
  rotation — single-operator ownership as of this writing).
- AWS Support: use the account's support plan console for anything beyond
  this runbook's scope (service outages, quota increases, account-level
  issues).

---

## 12. Multi-Region Active Failover

Full implementation detail lives in
[`WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md`](./WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md)
and its companion
[`WAPMultiRegion/WeatherApp-MultiRegion-Runbook-V1.md`](./WAPMultiRegion/WeatherApp-MultiRegion-Runbook-V1.md)
(every verification command, per-region CloudWatch log group, and
CloudFormation/pipeline troubleshooting command used to build this out).
This section is the condensed on-call version: what the alarm means, how to
tell a real outage from a false positive, and — most importantly — what to
do if us-east-1 itself is down.

### Confirm the failover record's TTL

`PrimaryRecord`/`SecondaryRecord` in `12-failover-dns.yml` are Route 53
ALIAS records, which don't expose a settable `TTL` property in
CloudFormation at all (`AliasTarget` and `TTL` are mutually exclusive on an
`AWS::Route53::RecordSet`) — Route 53 always answers ALIAS queries with the
TTL of the underlying AWS resource, and for an API Gateway regional domain
target that's a fixed 60 seconds, set by AWS, not something this project
configures.

**No `aws` CLI command can show this** — confirmed directly:
`aws route53 list-resource-record-sets` returns `"TTL": null` for both
records, since Route 53's stored *configuration* for an ALIAS record has no
TTL field at all. The 60s value only exists as a live DNS *answer* Route 53
computes on the fly, so the only way to observe it is an actual DNS query:
```bash
dig api.weather.craftingnewtech.com +noall +answer
```

**JMESPath gotcha if you go looking for the `null` yourself:** combine both
filter conditions in one `[?...]` with `&&`, not two chained brackets —
`ResourceRecordSets[?Name==x][?Type=='A']` silently returns an empty list
(chained bracket filters don't compose as "filter then filter again" here),
and piping that into `|[0].TTL` then evaluates to `null` too — a syntax bug
that looks identical to the real "no TTL field" answer. Confirmed both
forms live: the query below actually selects the record and still correctly
reports `null`; the chained-bracket form reports `null` from selecting
nothing at all.
```bash
aws route53 list-resource-record-sets --hosted-zone-id ZEXAMPLE0000000000 \
  --query "ResourceRecordSets[?Name == 'api.weather.craftingnewtech.com.' && Type == 'A']|[0].TTL"
```
Expect output like:
```
api.weather.craftingnewtech.com. 60 IN A 203.0.113.10
api.weather.craftingnewtech.com. 60 IN A 203.0.113.20
```
The `60` immediately after the domain name on each line is the TTL in
seconds. If this ever shows something other than 60, the alias target
changed to a different kind of AWS resource (or a plain CNAME crept in
somewhere) — worth investigating, since that number is a real input to the
overall failover RTO in `WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md`'s
success metrics.

### What the alarm means

`weather-dashboard-route53-failover-active-production` fires when the
primary region's Route 53 health check (`GET /health` on the primary API,
polled every 30s) has failed 3 consecutive times. Route 53 has already
switched `api.weather.craftingnewtech.com` to the us-west-2 failover record
by the time this alarm reaches `ALARM` — this is a notification that
failover has happened, not a request to trigger it manually.

### Confirm real outage vs. a false-positive health check blip

```bash
# Current health check status across all AWS checker regions
aws route53 get-health-check-status --health-check-id <id> \
  --query 'HealthCheckObservations[].{Region:Region,Status:StatusReport.Status}'

# Is the primary region's API actually unreachable, or just the health
# check's specific /health route?
curl -s -w '\nHTTP %{http_code}\n' https://876mh0k6q1.execute-api.us-east-1.amazonaws.com/health
curl -s -w '\nHTTP %{http_code}\n' 'https://876mh0k6q1.execute-api.us-east-1.amazonaws.com/weather?city=Austin'

# AWS-side status — check for an actual announced regional event before
# assuming it's your own application
```
Check the [AWS Health Dashboard](https://health.aws.amazon.com/health/status)
for an active us-east-1 event. A single flapping health check with the
primary API otherwise responding normally is more likely a transient
network blip between a specific Route 53 checker location and the API —
let it clear on its own (3 more consecutive successes) rather than taking
action. A primary API that's genuinely unreachable, combined with an
AWS-announced us-east-1 event, is the real scenario this whole plan exists
for — move to the procedure below.

### If us-east-1 is genuinely down — do NOT wait for the pipeline

**This is the single most important thing in this section.** CodeCommit,
CodePipeline, and every CodeBuild project for this project live only in
us-east-1. If us-east-1 has a real regional outage, the `DeploySecondary`
pipeline stages are just as unreachable as everything else there — they are
a steady-state convenience for keeping both regions in sync when nothing is
wrong, **not** the disaster-recovery mechanism. Do not start a pipeline
execution and wait for it; it will not run.

Instead, work directly against us-west-2 via CLI, from any machine with
valid AWS credentials — this has no dependency on us-east-1 being reachable
at all (verified: the operator's IAM user carries no region-restricting
policy conditions; every service-level role used in either region is a
global IAM entity; each region's Lambda only calls same-region service
endpoints at runtime). If CodeCommit itself is unreachable, clone from the
GitHub mirror instead — this repo already pushes to both remotes:

```bash
git clone https://github.com/jbaez22/WeatherAPP.git
cd WeatherAPP
```

Confirm us-west-2 is actually healthy and already serving (failover should
already be automatic — this just confirms it):
```bash
curl -s -w '\nHTTP %{http_code}\n' https://ag7upj7vr0.execute-api.us-west-2.amazonaws.com/health
curl -s -w '\nHTTP %{http_code}\n' https://api.weather.craftingnewtech.com/health
```

If something in us-west-2 itself also needs a fix or a redeploy while
us-east-1 is down, use direct CLI deploys — the exact commands already
proven working in
`WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md` §2.1-§2.5 (never a
raw CLI deploy against the *primary* stack from this path — that's still
recoverable once us-east-1 returns):
```bash
aws cloudformation package --region us-west-2 \
  --template-file infrastructure/cloudformation/master-secondary.yml \
  --s3-bucket weather-dashboard-artifacts-123456789012-production-us-west-2 \
  --s3-prefix cloudformation \
  --output-template-file infrastructure/cloudformation/master-secondary-packaged.yml

aws cloudformation deploy --region us-west-2 \
  --template-file infrastructure/cloudformation/master-secondary-packaged.yml \
  --stack-name weather-dashboard-secondary-production \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset
```

To update just the Lambda code in us-west-2 without a full stack deploy
(e.g. a hotfix that can't wait for us-east-1 to recover):
```bash
aws s3 cp backend-lambda.zip \
  s3://weather-dashboard-artifacts-123456789012-production-us-west-2/lambda/lambda.zip \
  --region us-west-2
aws lambda update-function-code --region us-west-2 \
  --function-name weather-dashboard-handler-production \
  --s3-bucket weather-dashboard-artifacts-123456789012-production-us-west-2 \
  --s3-key lambda/lambda.zip --publish
```

### Manual override — force traffic back to primary

If automatic failback is undesired for some reason (e.g. primary just
recovered but you want to validate it further before real traffic returns):
```bash
aws route53 update-health-check --health-check-id <id> --disabled
# Route 53 treats a disabled health check as always-healthy, keeping the
# PRIMARY record active regardless of actual status. Re-enable when ready:
aws route53 update-health-check --health-check-id <id> --no-disabled
```

### After the incident

- Confirm automatic failback occurred (no manual DNS change should have
  been needed) once the primary health check returns to `Success`.
- Write up what happened, how long failover lasted, and any gaps found in
  this procedure — append to
  `WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md`'s own incident
  notes if the gap is specific to this multi-region setup, or here if it's
  a general runbook gap.

---

## 13. Cache-Bypassed Live Deployment Validation

Runs automatically as part of the app pipeline's `ValidateDeployment` stage
(`pipeline/buildspec/validate-deployment.yml` calls
`pipeline/scripts/validate-live-deployment.sh`), and can also be run
manually on demand — not just right after a deploy. Confirms two things a
plain `curl` smoke test can miss: that the deployed `config.js`/CSP
actually agree (the class of bug behind the 2026-07-24 "Unable to reach the
weather service" incident), and that OpenWeatherMap was genuinely reached,
not just DynamoDB's 15-minute cache. Full background:
`WeatherApp-BrowserCacheMemory-Details-V1.md` #7.5.

```bash
export FRONTEND_DOMAIN=weather.craftingnewtech.com
export API_CUSTOM_DOMAIN=https://api.weather.craftingnewtech.com
export CLOUDFRONT_DISTRIBUTION_ID=E2JTYSEFHNW8MQ
export LAMBDA_FUNCTION_NAME=weather-dashboard-handler-production
bash pipeline/scripts/validate-live-deployment.sh
```

What it checks, in order:
1. Waits for the deploy's most recent CloudFront invalidation to reach
   `Completed` before trusting any content check.
2. Extracts `config.js`'s `API_BASE_URL` host and confirms it's inside
   `index.html`'s CSP `connect-src` — the static, always-available proxy
   for "would a real browser's CSP block this" (curl has no CSP engine, so
   this checks the same underlying fact directly instead).
3. Calls a rotating pool of low-traffic cities against the live API until
   CloudWatch Logs confirm `"Fetching One Call 3.0 data for <city>"` — proof
   of a genuine OpenWeatherMap fetch, not just a plausible-looking cached
   response.

Notes for a manual run:
- **Credentials:** uses whatever AWS CLI identity is active in the shell.
  Needs `cloudfront:GetInvalidation`/`ListInvalidations` and
  `logs:FilterLogEvents` on the Lambda's log group — the pipeline's
  `AppCodeBuildRole` has both (01-iam.yml's `ReadLambdaLogsForValidation`
  Sid); a personal IAM user with broader existing permissions will almost
  always already have both without any extra grant.
- **Works standalone**, not just right after a deploy — with no fresh
  deploy in flight, step 1 just finds whatever invalidation last ran
  (already `Completed`), so it passes quickly rather than failing.
- **Runtime:** roughly 30-90 seconds.

### Do I need to invalidate CloudFront myself first?

The script's step 1 only **checks** the most recent invalidation — it never
**creates** one itself (deliberately: see #7.6 in the cache-memory doc for
why coupling those two would be the wrong default). Whether you need to
invalidate manually first depends on how the content you're testing got
there:

- **Just re-checking the currently-live site** (no new local changes) — no
  extra step needed. The real app pipeline's `deploy-app.yml` already runs
  `pipeline/scripts/invalidate-cloudfront.sh` unconditionally in its own
  `post_build` phase, on every deploy, right before `ValidateDeployment`
  runs — so there is always a real, relevant, already-completed
  invalidation for the script to find.
- **You manually synced new frontend content to S3 yourself**, bypassing
  the pipeline (e.g. a direct `aws s3 sync frontend/ s3://<bucket>/ ...`) —
  **yes**, invalidate first. Nothing does it for you outside the pipeline.
  Run, in order:
  ```bash
  aws s3 sync frontend/ "s3://$WEBSITE_BUCKET/" --delete
  bash pipeline/scripts/invalidate-cloudfront.sh
  bash pipeline/scripts/validate-live-deployment.sh
  ```
  Skipping the invalidation step here means the validation script will find
  an old, unrelated invalidation (already `Completed`) and pass quickly —
  which looks like success but proves nothing about the content you just
  synced, since CloudFront's edge may still be serving the previous copy.

**Known limitation:** no real browser engine, so it cannot detect a live
browser blocking a request via CSP the way a user's browser would — only
the static config/CSP check (step 2) catches that class of bug here. See
`WeatherApp-BrowserCacheMemory-Details-V1.md` #6.1 for the (not yet
implemented) headless-browser smoke test that would close this gap.

**Two gotchas already fixed, if the script ever needs modifying — full
detail in `WeatherApp-BrowserCacheMemory-Details-V1.md` #7.6:**
- City names in the filter pattern must be lowercased
  (`validate_city()` lowercases before logging; CloudWatch Logs filter
  patterns are case-sensitive).
- `aws cloudfront list-invalidations` needs `--no-paginate` — otherwise
  `--query` runs per-page during auto-pagination and a later empty page's
  `null` corrupts the result into two lines.
