# Troubleshooting

Common issues and fixes for the Weather Dashboard. Start with the section that matches where the failure occurred.

**For pipeline monitoring (confirming a run triggered/is running/completed),
CodeDeploy canary failures, Secrets Manager/rotation issues, and routine
operational procedures**, see `docs/WeatherApp-runbook.md` §5, §10, and §11
instead — this document predates the current 6-stage pipeline and Secrets
Manager migration and is being kept focused on bootstrap-era / one-time
setup issues rather than duplicated here. **For the full history of every
real issue hit building the CodeDeploy canary + Secrets Manager +
CloudTrail/GuardDuty setup**, see
`docs/WeatherApp-ImproveDeploys-Plan-TroubleshootingSteps-V1.md`.

---

## Pipeline Failures

### cfn-lint gate fails

**Symptom:** `pre_build` phase aborts with cfn-lint errors.

**Check:** Run locally to see the exact error:
```bash
cfn-lint infrastructure/cloudformation/*.yml
```

**Common causes:**

| ---------- | -------------------------------------------------------------------------- | ------------------------------------------------------------- |
| Error code | Cause                                                                      | Fix                                                           |
| ---------- | -------------------------------------------------------------------------- | ------------------------------------------------------------- |
| `E3012`    | Wrong property type (e.g. Lambda Tags as an object instead of an array)    | Change to `[{Key: ..., Value: ...}]` format                   |
| `E1029`    | Variable reference (`${Foo}`) outside an `Fn::Sub` context                 | Wrap the string in `!Sub`, or use plain text                  |
| `E1019`    | Parameter name in `Fn::Sub` that does not exist in `Parameters`            | Check spelling against the Parameters section                 |
| `W3037`    | Invalid IAM action (e.g. `codebuild:TagResource` is not a real IAM action) | Remove or replace with the correct action                     |
| `W3002`    | Local `TemplateURL` path (in `master.yml`)                                 | Safe to ignore — these paths are for `cfn package` input only |
| `W3005`    | Redundant `DependsOn` (already implied by a `Ref` or `GetAtt`)             | Remove the explicit `DependsOn`                               |
| ---------- | -------------------------------------------------------------------------- | ------------------------------------------------------------- |

---

### checkov gate fails

**Symptom:** `pre_build` phase aborts with checkov `FAILED` findings.

**Check locally:**
```bash
checkov -d infrastructure/cloudformation --framework cloudformation --compact --severity HIGH
```

**Common checks and fixes:**

| ------------- | ----------------------------------------------- | ----------------------------------------------------- |
| Check ID      | Meaning                                         | Fix                                                   |
| ------------- | ----------------------------------------------- | ----------------------------------------------------- |
| `CKV_AWS_111` | IAM policy allows `*` resource                  | Scope to specific resource ARNs                       |
| `CKV_AWS_108` | IAM policy allows data write without constraint | Add resource-level restrictions                       |
| `CKV_AWS_18`  | S3 bucket access logging not enabled            | Add `LoggingConfiguration` or suppress if intentional |
| `CKV_AWS_21`  | S3 versioning not enabled                       | Enable versioning or suppress for website bucket      |
| ------------- | ----------------------------------------------- | ----------------------------------------------------- |

To suppress a known false positive in a template:
```yaml
Metadata:
  checkov:
    skip:
      - id: CKV_AWS_18
        comment: "CloudFront access logs are written to this bucket — self-logging not required"
```

---

### pip-audit gate fails

**Symptom:** `pre_build` phase aborts; output shows CVEs in `requirements.txt`.

**Fix:**
```bash
pipx run pip-audit -r backend/lambda/requirements.txt
```

For each vulnerable package, find the patched version:
```bash
pip index versions requests   # list all available versions
```

Then update `backend/lambda/requirements.txt` and re-run tests:
```bash
pip install -r backend/lambda/requirements.txt
python3 -m pytest backend/tests/ -v
```

---

### pytest gate fails (coverage below 80%)

**Symptom:** Build aborts with `FAILED: Coverage threshold not met`.

**Check:**
```bash
python3 -m pytest backend/tests/ --cov=backend/lambda --cov-report=term-missing --cov-fail-under=80
```

The `--cov-report=term-missing` flag shows which lines are not covered. Add tests for the uncovered branches, then re-run.

---

### Pipeline stuck in "In Progress"

There is no stage literally named "Build" anymore, and the two pipelines
have different stage counts: **Infra** is 6 stages (Source, Validate,
ApproveDeploy, Deploy, DeploySecondary, ValidateDeployment) and **App** is
7 stages (Source, Validate, Test, ApproveDeploy, Deploy, DeploySecondary,
ValidateDeployment) — `DeploySecondary` only appears when a secondary
region is configured (`HasSecondaryDeploy` condition).
`ApproveDeploy` is *expected* to sit `InProgress` indefinitely waiting on a
human; every other stage should resolve within a few minutes. See
`docs/WeatherApp-runbook.md` §5 for the full pipeline monitoring procedure,
including the known state-reporting lag and how to check the underlying
CodeBuild project directly when a stage looks stuck.

---

## Lambda / API Errors

### API returns 500 on every request

**Step 1 — Check Lambda logs:**
```bash
aws logs filter-log-events \
  --log-group-name "/aws/lambda/weather-dashboard-handler-production" \
  --filter-pattern "ERROR" \
  --start-time "$(($(date +%s) - 600))000" \
  --query "events[*].message" \
  --output text
```

**Common causes:**

| --------------------------------------------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Log message                                   | Root cause                                              | Fix                                                                                                        |
| --------------------------------------------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `ResourceNotFoundException` (Secrets Manager) | Secret not yet created, or wrong `SECRET_NAME`          | Confirm `aws secretsmanager describe-secret --secret-id weather-dashboard/openweathermap-api-key` resolves |
| `AccessDeniedException` on Secrets Manager    | Lambda IAM role missing `secretsmanager:GetSecretValue` | Redeploy `01-iam.yml`                                                                                      |
| `AccessDeniedException` on DynamoDB           | Lambda IAM role missing `dynamodb:GetItem`/`PutItem`    | Redeploy `01-iam.yml`                                                                                      |
| `requests.exceptions.ConnectionError`         | Lambda cannot reach openweathermap.org                  | Check for any VPC/security-group config accidentally applied                                               |
| `JSONDecodeError`                             | OWM returned non-JSON (e.g. 401 Unauthorized)           | Verify the key in Secrets Manager is correct and active                                                    |
| --------------------------------------------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |

> **As of Phase 4.2 (2026-07-14)** the credential source is AWS Secrets
> Manager, not SSM Parameter Store — see ADR-019 in
> `docs/architecture-decisions.md`. If you're troubleshooting a very old
> deploy still on the pre-migration code, the equivalent SSM-based errors
> were `ParameterNotFound` and `AccessDeniedException` on `ssm:GetParameter`.

### API returns 401 / "Weather service authentication failed" after setting a new key

**Symptom:** The Lambda was invoked (by smoke test, console test, or any HTTP request)
before the real OWM API key was written to the secret — most commonly
right after initial bootstrap, when the secret still holds the placeholder
string `PLACEHOLDER_SET_REAL_VALUE_AFTER_BOOTSTRAP`. The warm container's
module-level cache (`secrets_manager.py`'s `_cached_api_key`) keeps sending
that placeholder to OpenWeatherMap even after you update the secret. OWM
returns 401.

**Confirm the Lambda is serving stale data:**
```bash
# Check current secret value (should be your real key, not the placeholder)
# — run this yourself, don't paste the output back into a shared terminal
aws secretsmanager get-secret-value \
  --secret-id "weather-dashboard/openweathermap-api-key" \
  --query 'SecretString' --output text
```

If the secret holds the real key but the API still returns 401, a warm
Lambda execution environment is caching the old value at the Python
module level (separate from the DynamoDB weather cache — see
`docs/WeatherApp-ImproveDeploys-Plan-TroubleshootingSteps-V1.md`'s
cache-hit-vs-cache-miss note for why a `curl` returning correct-looking
JSON doesn't always prove the credential path actually ran).

**Fix — force Lambda cold start (no code change needed):**
```bash
aws lambda update-function-configuration \
  --function-name weather-dashboard-handler-production \
  --environment "Variables={
    LOG_LEVEL=INFO,
    TABLE_NAME=WeatherCache,
    SECRET_NAME=weather-dashboard/openweathermap-api-key,
    ALLOWED_ORIGIN=https://weather.craftingnewtech.com
  }" \
  --region us-east-1
```

This evicts all warm containers. The next request triggers a cold start that
reads the real key from Secrets Manager. No redeployment is required.

**Prevention:** Always write the real OWM key to Secrets Manager
(`aws secretsmanager put-secret-value`) **before** making any request to
the API — including smoke tests.

### API returns 500 only on first request after a gap (cold start)

Lambda cold starts can occasionally fail if the SSM parameter cache is stale. The function caches the key in the execution context — a Lambda environment that was recycled will fetch fresh on the next cold start. This is normal behavior; a retry will succeed.

### API returns 403 from API Gateway (not Lambda)

API Gateway HTTP API returns 403 when the CORS `Allow-Origin` does not match the request's `Origin` header. If you are testing from a browser running on `localhost`, this is expected behavior. Use `curl` directly or temporarily add your origin to the API Gateway CORS config.

---

## CloudFront / Frontend Issues

### Site shows old content after a deployment

CloudFront caches responses at edge nodes. A `/*` invalidation is triggered automatically by the pipeline. If you deployed manually (not via pipeline), run it yourself:

```bash
CF_DOMAIN=$(aws cloudformation describe-stacks \
  --stack-name weather-dashboard-master-production \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDomain'].OutputValue" \
  --output text)
DIST_ID=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?DomainName=='$CF_DOMAIN'].Id" --output text)
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*"
```

### Sub-directory paths return 403 (e.g. `/architecture/`)

The CloudFront Function that rewrites `/architecture/` to `/architecture/index.html` may not be deployed or may have a syntax error.

**Check:**
```bash
aws cloudfront describe-function --name weather-dashboard-directory-index-production
```

If the function does not exist, redeploy `03-cdn.yml`.

### S3 URL returns 403 but CloudFront URL works

This is correct and expected behavior — the S3 bucket is private and only accessible via CloudFront OAC. If you need to test S3 connectivity directly, use the AWS CLI:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws s3 ls "s3://weather-dashboard-website-${ACCOUNT_ID}-production/"
```

### CloudFront returns 502 Bad Gateway

This indicates CloudFront cannot reach the S3 origin. Most common cause: the S3 bucket was deleted or the OAC configuration is mismatched. Check the CloudFront distribution's origin settings in the AWS Console and compare against `03-cdn.yml`.

---

## DNS / SSL Issues

### `https://weather.craftingnewtech.com` returns ERR_CERT_COMMON_NAME_INVALID

The ACM certificate must be issued in `us-east-1` (not the region where CloudFront is configured). Verify:
```bash
aws acm list-certificates --region us-east-1 \
  --query "CertificateSummaryList[?DomainName=='weather.craftingnewtech.com']"
```

If the certificate is in another region, request a new one in `us-east-1` and update the `AcmCertificateArn` CloudFormation parameter.

### DNS does not resolve `weather.craftingnewtech.com`

Check the Route 53 record:
```bash
aws route53 list-resource-record-sets \
  --hosted-zone-id YOUR_HOSTED_ZONE_ID \
  --query "ResourceRecordSets[?Name=='weather.craftingnewtech.com.']"
```

DNS propagation can take up to 48 hours for a new record, but typically resolves in < 5 minutes if the record was just created.

---

## CloudFormation Stack Issues

### Stack creation fails with `ROLLBACK_COMPLETE`

The stack cannot be updated in this state — it must be deleted and re-created.
Before re-deploying, you must also delete any **retained resources** that
survived the rollback (resources with `DeletionPolicy: Retain`). They will
cause "already exists" errors on the next deploy attempt if left in place.

```bash
# 1. Delete the failed stack
aws cloudformation delete-stack --stack-name weather-dashboard-master-production
aws cloudformation wait stack-delete-complete --stack-name weather-dashboard-master-production

# 2. Delete retained resources (adjust account ID / region as needed)
#    These are safe to delete when they hold only placeholder / empty content.

# WebsiteBucket (S3)
aws s3 rb s3://weather-dashboard-website-123456789012-production --force 2>&1 || echo "Not found"

# Lambda log group (CloudWatch)
aws logs delete-log-group \
  --log-group-name "/aws/lambda/weather-dashboard-handler-production" \
  --region us-east-1 2>&1 || echo "Not found"

# API Gateway access log group (CloudWatch)
aws logs delete-log-group \
  --log-group-name "/aws/apigateway/weather-dashboard-production" \
  --region us-east-1 2>&1 || echo "Not found"

# SSM parameter (only if it still holds the placeholder value)
aws ssm delete-parameter \
  --name "/weather-dashboard/openweathermap-api-key" \
  --region us-east-1 2>&1 || echo "Not found"

# Secrets Manager secret (Phase 4.2 — also DeletionPolicy: Retain; only if
# it still holds the placeholder value, same caveat as the SSM parameter)
aws secretsmanager delete-secret \
  --secret-id "weather-dashboard/openweathermap-api-key" \
  --force-delete-without-recovery \
  --region us-east-1 2>&1 || echo "Not found"

# 3. Verify the artifacts bucket and Lambda ZIP are still intact
aws s3 ls "s3://weather-dashboard-artifacts-123456789012-production/lambda/lambda.zip" \
  && echo "ZIP ready — proceed with re-deploy."

# 4. Re-run the bootstrap deploy from docs/deployment-guide.md Step 7
```

> **Note:** Do NOT delete retained resources if they contain real production data
> (real API key, non-empty log groups with valuable history). Use CloudFormation
> resource import to re-adopt them into the new stack instead.
> See `docs/bootstrap-issues.md` for the complete retained-resources table.

### CloudFormation stack fails — `S3 bucket does not enable ACL access`

**Symptom:** `CdnStack` fails during CloudFront distribution creation with:
```
Invalid request provided: AWS::CloudFront::Distribution:
The S3 bucket that you specified for CloudFront logs does not enable ACL access:
weather-dashboard-website-123456789012-production.s3.amazonaws.com
(Service: CloudFront, Status Code: 400)
```

**Root cause:** AWS S3 now defaults to `BucketOwnerEnforced` Object Ownership,
which disables ACLs entirely. CloudFront access logging requires ACLs to grant
its log-delivery service principal write access to the destination bucket.

**Fix:** The `WebsiteBucket` in `02-storage.yml` must include:
```yaml
OwnershipControls:
  Rules:
    - ObjectOwnership: ObjectWriter
```
This re-enables ACLs while `BlockPublicAcls: true` still prevents public grants.
This fix is already applied in the current templates. If you see this error, you
may be running an older version of `02-storage.yml` — pull the latest and redeploy.

---

### CloudFormation `SsmStack` fails — `GeneralServiceException` on SSM PutParameter

**Symptom:** `OpenWeatherMapApiKey` (in `SsmStack`) reaches `CREATE_FAILED` with:
```
GeneralServiceException
```

**Root cause:** SSM Parameter tags have a restricted character set. Only
`a-z A-Z 0-9 _ . : / = + - @` and spaces are allowed in tag values. A comma
or other punctuation in a tag value causes a `GeneralServiceException` with no
further detail.

**Fix:** Remove commas and special punctuation from all SSM parameter tag values
in `07-ssm.yml`. This fix is already applied in the current templates.

---

### `UPDATE_ROLLBACK_FAILED` — stack is stuck

Find the blocking resource:
```bash
aws cloudformation describe-stack-events \
  --stack-name weather-dashboard-master-production \
  --query "StackEvents[?ResourceStatus=='UPDATE_ROLLBACK_FAILED'].[LogicalResourceId,ResourceStatusReason]" \
  --output table
```

Then continue rollback, skipping the problematic resource:
```bash
aws cloudformation continue-update-rollback \
  --stack-name weather-dashboard-master-production \
  --resources-to-skip LOGICAL_RESOURCE_ID
```

---

## Local Development Issues

### `pytest` not found on macOS

Use the module invocation instead of the binary:
```bash
python3 -m pytest backend/tests/
```

Or install in an explicit virtualenv:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install pytest pytest-cov moto requests-mock
pytest backend/tests/
```

### `pip install pip-audit` blocked by "externally-managed-environment"

macOS Homebrew Python disallows global pip installs. Use `pipx`:
```bash
brew install pipx
pipx run pip-audit -r backend/lambda/requirements.txt
```

### `checkov` command not found

This project uses `checkov` (Python) for CloudFormation security scanning — not
`cfn_nag`. Install it with `pipx` (do not use system pip on macOS):

```bash
brew install pipx
pipx ensurepath   # restart terminal after this
pipx install checkov
checkov --version  # verify installation
```

Run locally to check all CloudFormation templates:
```bash
checkov -d infrastructure/cloudformation --framework cloudformation --compact --severity HIGH
```
