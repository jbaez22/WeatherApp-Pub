# Bootstrap Deployment Issues — Root Causes and Resolutions

This document records every error encountered during the initial CloudFormation bootstrap
deployment of the Weather Dashboard, the root cause of each, and the fix applied.
It is intended as a reference for future deployments, environment resets, and
portfolio reviewers who want to understand the operational reality of bootstrapping
a nested-stack AWS project.

---

## Summary

| # | Stack / Phase | Error | Severity | Status |
|---|---|---|---|---|
| 1 | SsmStack | `GeneralServiceException` on SSM PutParameter (illegal tag character) | Deploy blocker | Fixed in `07-ssm.yml` |
| 2 | StorageStack | `AlreadyExists` on S3 ArtifactsBucket (bootstrap pre-created same name) | Deploy blocker | Fixed in `02-storage.yml` + `master.yml` |
| 3 | CdnStack | `InvalidRequest` on CloudFront distribution (S3 ACLs disabled, log delivery rejected) | Deploy blocker | Fixed in `02-storage.yml` |
| 4 | Lambda | OWM API returning `401` after key was set (warm container cached placeholder) | Runtime error | Resolved by forcing Lambda cold start |

All four issues were discovered across two failed stack deployments before a clean
`CREATE_COMPLETE` was achieved on the third attempt.

---

## Issue 1 — SSM Tag Value: Illegal Character (Comma)

### Stack
`SsmStack` → `OpenWeatherMapApiKey` (`AWS::SSM::Parameter`)

### Symptom
```
GeneralServiceException — CREATE_FAILED on OpenWeatherMapApiKey
```

### Root Cause
SSM Parameter tags have a stricter allowed character set than standard AWS resource tags.
Allowed characters: `a-z A-Z 0-9 _ . : / = + - @` and spaces.
The `RotationNote` tag value contained a comma:

```yaml
# Before (broken):
RotationNote: 'Update via aws ssm put-parameter --overwrite, not via CFN'
#                                                              ^ comma not allowed
```

The CloudFormation error message was `GeneralServiceException` with no further detail,
making this hard to diagnose without knowing the SSM tag constraint.

### Resolution
Removed the comma from the tag value:

```yaml
# After (fixed — 07-ssm.yml):
RotationNote: 'Rotate via CLI put-parameter --overwrite not via CFN'
```

### Prevention
When writing SSM parameter tags, avoid commas and other punctuation not in the
allowed set. Prefer simple prose with no special characters in SSM tag values.

---

## Issue 2 — S3 ArtifactsBucket Name Conflict (Bootstrap vs. CloudFormation)

### Stack
`StorageStack` → `ArtifactsBucket` (`AWS::S3::Bucket`)

### Symptom
```
Resource of type 'AWS::S3::Bucket' with identifier
'weather-dashboard-artifacts-ABC-EXAMPLE-XXXX-production' already exists.
```

### Root Cause
The bootstrap sequence requires the Lambda deployment ZIP (`lambda.zip`) to exist in S3
**before** `aws cloudformation deploy` runs, because the BackendStack (`05-backend.yml`)
creates the Lambda function pointing to that S3 key during the same deploy.

To satisfy this ordering requirement, Step 6 of the bootstrap pre-creates the artifacts
bucket with `aws s3 mb`, uploads `lambda.zip`, and then runs `cfn deploy`. However,
`02-storage.yml` also contained an `AWS::S3::Bucket` resource with the same computed
name (`${ProjectName}-artifacts-${AccountId}-${Environment}`), causing CloudFormation
to fail when it tried to create a bucket that already existed.

This is a classic chicken-and-egg bootstrap problem: the Lambda ZIP must be in S3 before
the stack deploys, but the bucket that holds the ZIP is managed by the same stack.

### Resolution
The artifacts bucket was removed from CloudFormation management entirely. It is now
treated as a **pre-provisioned resource** — created once during account bootstrap and
referenced by name in subsequent deployments via a parameter.

Changes made:

**`02-storage.yml`** — replaced the `ArtifactsBucket` resource with an
`ExistingArtifactsBucketName` parameter. The `ArtifactsBucketPolicy` (TLS enforcement)
is still managed by CloudFormation against the pre-created bucket. Outputs derive the
bucket name and ARN from the parameter:

```yaml
# Before: CloudFormation creates the bucket
ArtifactsBucket:
  Type: AWS::S3::Bucket
  Properties:
    BucketName: !Sub '${ProjectName}-artifacts-${AWS::AccountId}-${Environment}'
    ...

# After: bucket is pre-created; CFN just attaches a TLS policy to it
ExistingArtifactsBucketName:
  Type: String
  Description: Name of the pre-created artifacts bucket (created during bootstrap).

ArtifactsBucketPolicy:
  Type: AWS::S3::BucketPolicy
  Properties:
    Bucket: !Ref ExistingArtifactsBucketName
    ...
```

**`master.yml`** — passes the computed name to `StorageStack`:

```yaml
StorageStack:
  Parameters:
    ExistingArtifactsBucketName: !Sub '${ProjectName}-artifacts-${AWS::AccountId}-${Environment}'
```

### Prevention
Any S3 bucket that must exist **before** a CloudFormation stack deploys should not be
managed as an `AWS::S3::Bucket` resource in that same stack. Treat it as a bootstrap
resource (pre-provisioned via CLI) and reference it as a parameter. This pattern is
common for pipeline artifact buckets in production AWS deployments.

---

## Issue 3 — CloudFront Log Delivery Rejected (S3 ACLs Disabled by Default)

### Stack
`CdnStack` → `WeatherDashboardDistribution` (`AWS::CloudFront::Distribution`)

### Symptom
```
Invalid request provided: AWS::CloudFront::Distribution:
The S3 bucket that you specified for CloudFront logs does not enable ACL access:
weather-dashboard-website-ABC-EXAMPLE-XXXX-production.s3.amazonaws.com
(Service: CloudFront, Status Code: 400)
```

### Root Cause
Since April 2023, AWS creates new S3 buckets with **`BucketOwnerEnforced`** as the
default Object Ownership setting, which **completely disables ACLs** on the bucket.

CloudFront access logging uses ACLs to grant its log-delivery service principal write
access to the destination bucket. When ACLs are disabled, CloudFront rejects the
`CreateDistribution` call at provisioning time with a 400 error.

The `WebsiteBucket` in `02-storage.yml` did not specify `ObjectOwnership`, so it
inherited the AWS default (`BucketOwnerEnforced`) when created. The `BlockPublicAcls`
setting (which was explicitly set) only blocks *public* ACLs — it does not re-enable
ACLs on its own.

This issue is silent during `cfn-lint` and `checkov` validation because neither tool
checks the S3 Object Ownership default, and the CloudFormation resource itself is valid.
The error only surfaces at AWS API call time during distribution creation.

### Resolution
Added `ObjectOwnership: ObjectWriter` to the `WebsiteBucket` in `02-storage.yml`.
This re-enables ACLs so CloudFront can deliver logs using its canonical user ID, while
`BlockPublicAcls: true` continues to prevent any public ACL grants:

```yaml
# Added to WebsiteBucket (02-storage.yml):
OwnershipControls:
  Rules:
    - ObjectOwnership: ObjectWriter
```

The combination of `ObjectWriter` + `BlockPublicAcls: true` is the correct production
pattern for a CloudFront logging destination — private ACLs are permitted, public ones
are blocked.

### Side Effects of the Failed Deploy
Because `CdnStack` failed mid-deploy, the following resources with `DeletionPolicy: Retain`
survived the master stack rollback and had to be manually deleted before the next
deploy attempt (they would have caused "already exists" conflicts):

| Resource | Name | Action Taken |
|---|---|---|
| `WebsiteBucket` (S3) | `weather-dashboard-website-ABC-EXAMPLE-XXXX-production` | Deleted (empty) |
| `LambdaLogGroup` (CloudWatch) | `/aws/lambda/weather-dashboard-handler-production` | Deleted (empty) |
| `ApiAccessLogGroup` (CloudWatch) | `/aws/apigateway/weather-dashboard-production` | Deleted (empty) |
| `OpenWeatherMapApiKey` (SSM) | `/weather-dashboard/openweathermap-api-key` | Deleted (placeholder value) |

> **Note for future redeployments:** If the stack must be torn down and redeployed
> in the same AWS account, check for these retained resources first and delete them
> if they are empty or hold only placeholder values. Production environments with real
> data should retain these resources and use CloudFormation resource import to
> re-adopt them into the new stack.

### Prevention
Always explicitly set `ObjectOwnership` on any S3 bucket that serves as a CloudFront
log destination. Do not rely on the AWS default:

```yaml
# Required for CloudFront logging target buckets:
OwnershipControls:
  Rules:
    - ObjectOwnership: ObjectWriter
```

Alternatively, point CloudFront logging to a dedicated log bucket (separate from the
website bucket) with explicit ACL settings.

---

## Issue 4 — Lambda Cached SSM Placeholder; OWM Returned 401

### Phase
Post-deploy, Step 8 of the bootstrap sequence (set OWM API key).

### Symptom
After the stack deployed successfully and the OWM API key was written to SSM
as a `SecureString`, smoke test calls to the API Gateway returned:

```json
{ "error": "Weather service authentication failed." }
```

Lambda logs showed:
```
OpenWeatherMap returned 401 — check the API key in SSM.
```

### Root Cause
The Lambda function caches the SSM parameter value in memory at cold start to
avoid paying the SSM API latency on every invocation. The initialization sequence:

1. CloudFormation deployed `07-ssm.yml` — created the parameter with
   `Value: 'PLACEHOLDER_SET_REAL_VALUE_AFTER_BOOTSTRAP'` (CloudFormation cannot
   create SecureString parameters directly — this is a known AWS limitation).
2. The smoke test was run, triggering a Lambda cold start that read and cached
   the placeholder string.
3. The real OWM key was then written to SSM with `--type SecureString --overwrite`.
4. Subsequent Lambda invocations used the **cached placeholder** (not the new key)
   because the warm container never re-fetched from SSM.

The SSM key itself was confirmed valid by testing it directly against the OWM API:
```bash
curl "https://api.openweathermap.org/data/2.5/weather?q=London&appid=<key>"
# → 200 OK with weather data
```

### Resolution
Forced a Lambda cold start by touching the function configuration (no code change,
no redeployment required). This evicts all warm containers and ensures the next
invocation re-initializes from scratch:

```bash
aws lambda update-function-configuration \
  --function-name weather-dashboard-handler-production \
  --environment "Variables={
    LOG_LEVEL=INFO,
    TABLE_NAME=WeatherCache,
    SSM_PARAMETER_NAME=/weather-dashboard/openweathermap-api-key,
    ALLOWED_ORIGIN=https://weather.craftingnewtech.com
  }"
```

After the forced cold start, the Lambda fetched the real SecureString from SSM,
and the smoke test returned live weather data successfully.

### Prevention
The bootstrap sequence should **always** set the real OWM API key in SSM **before**
running the first smoke test — or at minimum before the first Lambda invocation.
If the key is updated after a Lambda has already cold-started with the placeholder,
run the `update-function-configuration` command above to force a fresh cold start.

The correct bootstrap order for Step 8:
1. Stack reaches `CREATE_COMPLETE`
2. Set real API key in SSM: `aws ssm put-parameter --type SecureString --overwrite`
3. **Then** run the smoke test (first invocation will cold-start with the real key)

---

## Orphaned ACM Certificates

### Context
Each failed `CdnStack` deploy attempt created a new `AWS::CertificateManager::Certificate`
for `weather.craftingnewtech.com` and automatically added its DNS validation CNAME to
Route 53. When the stack rolled back, the ACM certificate was deleted, but the **Route 53
validation CNAME record was not removed**.

After three deploy attempts, two orphaned ACM certificates existed in the account
(from attempts 2 and 3) and one stale validation CNAME remained in Route 53:

```
_5235fbadee3230fa66c5adcc7982214f.weather.craftingnewtech.com.
  → _da68fc5160865186a3cfce39fc19233d.jkddzztszm.acm-validations.aws.
```

### Impact
The stale CNAME had no negative effect on subsequent deployments — ACM reused it to
validate new certificates for the same domain, which is why certificates issued in
subsequent attempts validated in seconds rather than minutes.

### Cleanup (Optional)
After confirming the stack is stable, optionally delete the orphaned certificates:
```bash
# List all certs for the domain
aws acm list-certificates \
  --query "CertificateSummaryList[?DomainName=='weather.craftingnewtech.com']" \
  --output table

# Delete any cert not currently in use (InUse: false)
aws acm delete-certificate --certificate-arn arn:aws:acm:us-east-1:...:certificate/...
```

The active certificate (bound to the CloudFront distribution) will show `InUse: true`
and must not be deleted.

---

## Final Successful Deploy

After applying all three template fixes and cleaning up retained resources, the third
deployment attempt completed with all 9 nested stacks in `CREATE_COMPLETE`:

```
DatabaseStack    → CREATE_COMPLETE
SsmStack         → CREATE_COMPLETE
StorageStack     → CREATE_COMPLETE
IamStack         → CREATE_COMPLETE
BackendStack     → CREATE_COMPLETE
ApiStack         → CREATE_COMPLETE
MonitoringStack  → CREATE_COMPLETE
CdnStack         → CREATE_COMPLETE   ← ~15 min (ACM validation + CloudFront provisioning)
PipelineStack    → CREATE_COMPLETE
─────────────────────────────────────
weather-dashboard-master-production → CREATE_COMPLETE
```

**Live endpoints:**

| Resource | Value |
|---|---|
| Website | `https://weather.craftingnewtech.com` |
| CloudFront domain | `d2cbs1x5b0oe7p.cloudfront.net` |
| API Gateway | `https://ABC-EXAMPLE-XXXX.execute-api.us-east-1.amazonaws.com` |
| CodeCommit | `https://git-codecommit.us-east-1.amazonaws.com/v1/repos/weather-dashboard` |
