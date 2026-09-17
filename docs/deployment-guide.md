# Deployment Guide — Weather Dashboard

> **Audience:** The engineer deploying this project to AWS for the first time.
> **Account:** Existing AWS account, us-east-1, AWS CLI already configured with admin credentials.
> **Time:** ~25 minutes end-to-end (includes ACM certificate DNS propagation wait).

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Set Environment Variables](#2-set-environment-variables)
3. [Request ACM Certificate](#3-request-acm-certificate)
4. [Bootstrap — S3 Artifacts Bucket](#4-bootstrap--s3-artifacts-bucket)
5. [Bootstrap — Lambda Deployment Package](#5-bootstrap--lambda-deployment-package)
6. [Bootstrap — Package CloudFormation Templates](#6-bootstrap--package-cloudformation-templates)
7. [Bootstrap — Deploy Master Stack](#7-bootstrap--deploy-master-stack)
8. [Post-Bootstrap — Set OWM API Key](#8-post-bootstrap--set-owm-api-key)
9. [Post-Bootstrap — Update Frontend Config](#9-post-bootstrap--update-frontend-config)
10. [Push to CodeCommit — Trigger First Pipeline Run](#10-push-to-codecommit--trigger-first-pipeline-run)
11. [Verify the Deployment](#11-verify-the-deployment)
12. [Ongoing Update Workflow](#12-ongoing-update-workflow)
13. [Re-deploying After a Failed Bootstrap (ROLLBACK_COMPLETE)](#13-re-deploying-after-a-failed-bootstrap-rollback_complete)
14. [Rollback Procedures](#14-rollback-procedures)
15. [Teardown](#15-teardown)
16. [Multi-Region Bootstrap (us-west-2)](#16-multi-region-bootstrap-us-west-2)

---

## 1. Prerequisites

### AWS CLI
```bash
aws --version       # requires 2.x
aws sts get-caller-identity   # must return your account ID, no error
```

Expected output:
```json
{
    "UserId": "AIDAXXXXXXXXXXXXXXXXX",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/your-admin-user"
}
```

### Local tools

> **macOS note:** Do not use the system Python shipped with Xcode Command Line
> Tools (`/Library/Developer/CommandLineTools/.../python3.9`). It is locked down
> and will throw a `VersionConflict` error on any `pip install`. Use Homebrew
> Python and `pipx` for CLI tools instead (see installation steps below).

#### Check versions (after installation)
```bash
python3 --version     # expect 3.11+ from Homebrew, not Apple's 3.9
cfn-lint --version
pip-audit --version
checkov --version
git --version
```

#### Install CLI tools (macOS — one-time)
```bash
# 1. Homebrew Python (if not already installed)
brew install python@3.11

# 2. pipx — manages CLI tools in isolated environments, avoids system pip conflicts
brew install pipx
pipx ensurepath   # adds ~/.local/bin to PATH; restart your terminal after this

# 3. IaC and security scanning tools
pipx install cfn-lint
pipx install pip-audit

# 4. CloudFormation security scanner (checkov — Python, no Ruby required)
# checkov is also used by the /security-scan skill, keeping local and pipeline
# security scanning consistent. cfn_nag (the previous tool) is incompatible
# with Ruby 4.0 due to an abandoned dependency (kwalify, last updated 2009).
pipx install checkov
```

#### Python virtualenv for tests and Lambda packaging
```bash
# Create once at the project root; activate before running tests or packaging Lambda
python3 -m venv .venv
source .venv/bin/activate
pip install --quiet pytest pytest-cov moto requests-mock boto3

# Verify
python3 -m pytest --version
```

> The `.venv/` directory is in `.gitignore` and is never committed.

### OpenWeatherMap API key
Sign up at https://openweathermap.org/api and obtain a free API key.
New keys activate within 2 hours. Have it ready for Step 8.

### Domain DNS access
You need to add records to `craftingnewtech.com` (either via Route 53 console
or your registrar). This is required for ACM certificate validation (Step 3)
and the final DNS cut-over (Step 7).

---

## 2. Set Environment Variables

Run these once at the start of your terminal session. All subsequent steps
reference these variables — copy-paste them exactly.

```bash
export AWS_DEFAULT_REGION="us-east-1"
export PROJECT="weather-dashboard"
export ENVIRONMENT="production"
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export DOMAIN="weather.craftingnewtech.com"

# Derived values (computed automatically from the above)
export ARTIFACTS_BUCKET="${PROJECT}-artifacts-${ACCOUNT_ID}-${ENVIRONMENT}"
export WEBSITE_BUCKET="${PROJECT}-website-${ACCOUNT_ID}-${ENVIRONMENT}"
export STACK_NAME="${PROJECT}-master-${ENVIRONMENT}"

echo "Account ID      : $ACCOUNT_ID"
echo "Artifacts bucket: $ARTIFACTS_BUCKET"
echo "Website bucket  : $WEBSITE_BUCKET"
echo "Stack name      : $STACK_NAME"
```

---

## 3. ACM Certificate — Handled Automatically (Route 53)

CloudFront requires a TLS certificate in **us-east-1**. Because `craftingnewtech.com`
is managed in Route 53, the CloudFormation template creates and validates the
certificate automatically during the master stack deployment (Step 7). No manual
certificate steps are needed.

**What CloudFormation does for you:**
1. Calls ACM to request a certificate for `weather.craftingnewtech.com`
2. Adds the DNS validation CNAME record to the Route 53 hosted zone automatically
3. Waits for ACM to validate the record and issue the certificate (typically 2–5 minutes)
4. Passes the issued certificate ARN to the CloudFront distribution

All you need is the Route 53 Hosted Zone ID, which you collect here:

```bash
HOSTED_ZONE_ID=$(aws route53 list-hosted-zones-by-name \
  --dns-name "craftingnewtech.com." \
  --query "HostedZones[0].Id" \
  --output text | sed 's|/hostedzone/||')

echo "Hosted Zone ID: $HOSTED_ZONE_ID"
# This value is passed as --parameter-overrides HostedZoneId=$HOSTED_ZONE_ID in Step 7
```

> **CloudFormation limitation:** Automatic certificate creation only works when
> the domain is managed in Route 53. If your domain is at another registrar
> (GoDaddy, Namecheap, etc.) you must create and validate the certificate manually
> before deploying the stack — see the section below.

---

### (Non-Route 53 only) Manual certificate steps

Skip this section if `craftingnewtech.com` is in Route 53.

```bash
# Request the certificate
ACM_CERT_ARN=$(aws acm request-certificate \
  --domain-name "$DOMAIN" \
  --validation-method DNS \
  --region us-east-1 \
  --query CertificateArn \
  --output text)

echo "Certificate ARN: $ACM_CERT_ARN"

# Get the CNAME validation record
aws acm describe-certificate \
  --certificate-arn "$ACM_CERT_ARN" \
  --region us-east-1 \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord'
```

Log in to your registrar's DNS console and add the CNAME record shown above.
Then wait for issuance:

```bash
aws acm wait certificate-validated \
  --certificate-arn "$ACM_CERT_ARN" \
  --region us-east-1 \
  && echo "Certificate ISSUED."
```

Pass `AcmCertificateArn=$ACM_CERT_ARN` and omit `HostedZoneId` in Step 7.
Leave `HostedZoneId` blank — CloudFormation will skip automatic cert creation
and use the ARN you supply.

---

## 4. Bootstrap — S3 Artifacts Bucket

The Lambda deployment ZIP must exist in S3 **before** the master stack deploys
(CloudFormation creates the Lambda function pointing to that S3 key during the
same deploy run). This creates a chicken-and-egg dependency: the Lambda ZIP
needs a bucket, but the bucket would normally be managed by the same stack.

**Design decision:** The artifacts bucket is managed by a dedicated
`00-bootstrap.yml` stack, deployed separately and permanently. This cleanly
separates bootstrap infrastructure from the application stack. The master stack
references the bucket by name via parameter — it never tries to create it.

Deploying `00-bootstrap.yml` first:
- Eliminates the `AlreadyExists` conflict (the master stack never touches the bucket resource)
- Makes bootstrap fully reproducible across accounts and environments
- Keeps all bucket configuration (versioning, encryption, block-public) in CloudFormation

### 4a. Deploy the bootstrap stack (clean account / first time)

```bash
aws cloudformation deploy \
  --template-file infrastructure/cloudformation/00-bootstrap.yml \
  --stack-name weather-dashboard-bootstrap-production \
  --parameter-overrides \
    ProjectName=weather-dashboard \
    Environment=production \
  --region us-east-1

echo "Bootstrap stack deployed."
aws cloudformation describe-stacks \
  --stack-name weather-dashboard-bootstrap-production \
  --region us-east-1 \
  --query 'Stacks[0].Outputs' \
  --output table
```

The bootstrap stack has `DeletionPolicy: Retain` on the bucket — it is
intentionally permanent and must **not** be deleted as part of application
teardown or re-deployment cycles.

### 4b. Adopt an existing bucket (existing account only)

If the bucket was previously created with `aws s3 mb` (e.g., this account),
import it into the bootstrap stack instead of creating it:

```bash
# Step 1 — create the import change set (minimal template, no outputs yet)
aws cloudformation create-change-set \
  --change-set-type IMPORT \
  --stack-name weather-dashboard-bootstrap-production \
  --template-body file://infrastructure/cloudformation/00-bootstrap.yml \
  --parameters \
    ParameterKey=ProjectName,ParameterValue=weather-dashboard \
    ParameterKey=Environment,ParameterValue=production \
  --resources-to-import \
    '[{"ResourceType":"AWS::S3::Bucket","LogicalResourceId":"ArtifactsBucket","ResourceIdentifier":{"BucketName":"'"${ARTIFACTS_BUCKET}"'"}}]' \
  --change-set-name ImportArtifactsBucket \
  --region us-east-1

# Step 2 — execute the import
aws cloudformation execute-change-set \
  --change-set-name ImportArtifactsBucket \
  --stack-name weather-dashboard-bootstrap-production \
  --region us-east-1

aws cloudformation wait stack-create-complete \
  --stack-name weather-dashboard-bootstrap-production \
  --region us-east-1 && echo "Bootstrap stack created — bucket adopted."

# Step 3 — update the stack to add outputs (import does not allow adding outputs)
aws cloudformation deploy \
  --template-file infrastructure/cloudformation/00-bootstrap.yml \
  --stack-name weather-dashboard-bootstrap-production \
  --parameter-overrides \
    ProjectName=weather-dashboard \
    Environment=production \
  --region us-east-1

echo "Bootstrap stack outputs added."
```

---

## 5. Bootstrap — Lambda Deployment Package

Build the Lambda ZIP with all dependencies and upload it to the artifacts
bucket. This must happen before the master stack is deployed.

```bash
# Clean build directory
rm -rf /tmp/lambda-pkg /tmp/lambda.zip

# Install Lambda runtime dependencies into the package directory
mkdir -p /tmp/lambda-pkg
pip install \
  -r backend/lambda/requirements.txt \
  -t /tmp/lambda-pkg \
  --quiet

# Copy Lambda source files
cp backend/lambda/*.py /tmp/lambda-pkg/

# Create the deployment ZIP (from inside the package dir — no path prefix)
cd /tmp/lambda-pkg
zip -r /tmp/lambda.zip . --quiet
cd -

echo "Lambda ZIP size: $(du -sh /tmp/lambda.zip | cut -f1)"

# Upload to artifacts bucket
aws s3 cp /tmp/lambda.zip "s3://${ARTIFACTS_BUCKET}/lambda/lambda.zip"
echo "Lambda ZIP uploaded: s3://${ARTIFACTS_BUCKET}/lambda/lambda.zip"
```

---

## 6. Bootstrap — Package CloudFormation Templates

`aws cloudformation package` uploads all nested stack templates to S3 and
produces `master-packaged.yml` with HTTPS S3 URLs replacing the local paths.
This packaged file is what you deploy in Step 7.

```bash
aws cloudformation package \
  --template-file infrastructure/cloudformation/master.yml \
  --s3-bucket "$ARTIFACTS_BUCKET" \
  --s3-prefix cloudformation \
  --output-template-file infrastructure/cloudformation/master-packaged.yml \
  --region us-east-1

echo "Packaged template: infrastructure/cloudformation/master-packaged.yml"
```

---

## 7. Bootstrap — Deploy Master Stack

This is the main deployment step. It creates all 9 nested stacks in
dependency order: DynamoDB → SSM → S3 → IAM → Lambda → API Gateway →
CloudFront → CodePipeline → CloudWatch.

**Duration:** ~10–15 minutes (CloudFront distribution creation is the slowest step).

```bash
# Ensure you have the values from earlier steps
echo "Hosted Zone ID : $HOSTED_ZONE_ID"
echo "Stack name     : $STACK_NAME"

aws cloudformation deploy \
  --template-file infrastructure/cloudformation/master-packaged.yml \
  --stack-name "$STACK_NAME" \
  --region us-east-1 \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --parameter-overrides \
    ProjectName="$PROJECT" \
    Environment="$ENVIRONMENT" \
    HostedZoneId="$HOSTED_ZONE_ID" \
    AlternateDomainName="$DOMAIN" \
  --no-fail-on-empty-changeset
```

> **Note:** `AcmCertificateArn` is omitted — CloudFormation creates and validates
> the certificate automatically because `HostedZoneId` is provided. If your domain
> is NOT in Route 53, pass `AcmCertificateArn="$ACM_CERT_ARN"` and omit
> `HostedZoneId` (see Step 3 non-Route 53 section).

### Monitor progress
```bash
# In a separate terminal, watch stack events as they unfold
aws cloudformation describe-stack-events \
  --stack-name "$STACK_NAME" \
  --region us-east-1 \
  --query 'StackEvents[*].[Timestamp,LogicalResourceId,ResourceStatus]' \
  --output table
```

### Collect stack outputs
When `aws cloudformation deploy` exits with "Successfully created/updated stack":

```bash
# Pull all outputs at once
aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region us-east-1 \
  --query 'Stacks[0].Outputs' \
  --output table

# Save the key values you need for the next steps
API_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" \
  --output text)

CODECOMMIT_URL=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='CodeCommitCloneUrl'].OutputValue" \
  --output text)

CLOUDFRONT_DOMAIN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDomain'].OutputValue" \
  --output text)

ALARM_TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='AlarmTopicArn'].OutputValue" \
  --output text)

echo ""
echo "=== Stack Outputs ==="
echo "API Endpoint    : $API_ENDPOINT"
echo "CodeCommit URL  : $CODECOMMIT_URL"
echo "CloudFront URL  : https://$CLOUDFRONT_DOMAIN"
echo "Website URL     : https://$DOMAIN"
echo "Alarm Topic ARN : $ALARM_TOPIC_ARN"
```

---

## 8. Post-Bootstrap — Set OWM API Key

The CloudFormation stack created an SSM parameter with a placeholder value
(`PLACEHOLDER_SET_REAL_VALUE_AFTER_BOOTSTRAP`). Replace it with your real
OpenWeatherMap API key **before running any smoke tests or invoking the Lambda
function**. This ordering is critical — see the warning below.

> **Security note:** The `--type SecureString` flag encrypts the value with
> the AWS-managed KMS key (`aws/ssm`). It never appears in CloudFormation
> templates, Lambda environment variables, or CloudWatch logs.

```bash
aws ssm put-parameter \
  --name "/weather-dashboard/openweathermap-api-key" \
  --value "YOUR_OWM_API_KEY_HERE" \
  --type SecureString \
  --overwrite \
  --region us-east-1

echo "API key stored in SSM as SecureString."
```

**Verify (value should be masked):**
```bash
aws ssm get-parameter \
  --name "/weather-dashboard/openweathermap-api-key" \
  --with-decryption \
  --region us-east-1 \
  --query 'Parameter.{Name:Name,Type:Type,LastModifiedDate:LastModifiedDate}'
```

> **Warning — Lambda SSM cache:** The Lambda function reads the SSM parameter
> once at cold start and caches it in memory for the lifetime of the container.
> If the Lambda was invoked at any point before you set the real key (e.g., during
> stack deploy validation or an accidental curl), the warm container holds the
> placeholder and will return a 401 from OpenWeatherMap.
>
> **Fix:** Force a cold start immediately after setting the key:
> ```bash
> aws lambda update-function-configuration \
>   --function-name weather-dashboard-handler-production \
>   --environment "Variables={
>     LOG_LEVEL=INFO,
>     TABLE_NAME=WeatherCache,
>     SSM_PARAMETER_NAME=/weather-dashboard/openweathermap-api-key,
>     ALLOWED_ORIGIN=https://weather.craftingnewtech.com
>   }"
> ```
> This evicts all warm containers. The next invocation re-initializes from SSM
> and picks up the real key. No code change or redeployment is needed.

---

## 9. Post-Bootstrap — Update Frontend Config

The API Gateway endpoint URL is now known. Update the frontend config so
the weather app calls the correct endpoint.

```bash
echo "API Endpoint: $API_ENDPOINT"

# Update frontend/js/config.js
cat > frontend/js/config.js << EOF
const CONFIG = {
  API_BASE_URL: '${API_ENDPOINT}',
};
export default CONFIG;
EOF

echo "frontend/js/config.js updated."
cat frontend/js/config.js
```

---

## 10. Push to CodeCommit — Trigger First Pipeline Run

### 10a. Install git-remote-codecommit (one-time)
```bash
pip install git-remote-codecommit
```

### 10b. Add CodeCommit as a git remote
```bash
echo "CodeCommit URL: $CODECOMMIT_URL"

git remote add aws "$CODECOMMIT_URL"

# Verify
git remote -v
```

### 10c. Run local pre-push validation
Before pushing, run the same gates the pipeline will run:
```bash
bash pipeline/scripts/validate-templates.sh
bash pipeline/scripts/run-tests.sh
```

Both must pass before pushing.

### 10d. Push to main
```bash
git add frontend/js/config.js
git commit -m "bootstrap: set API Gateway endpoint in config.js"

git push aws main
```

### 10e. Monitor the pipeline

Since the pipeline split (`docs/WeatherApp-PipeSplit-ImplePlan-V1.md`), there
are two independent pipelines — a commit touching `infrastructure/**` only
triggers Infra, `backend/**`/`frontend/**` only triggers App, and a commit
touching both triggers Infra first, then App once Infra's deploy succeeds
(see `pipeline-topology.md`'s routing rule). Check both by name directly
rather than reading a stack output (neither pipeline's name is exposed at
the `master.yml` root):

```bash
INFRA_PIPELINE="${PROJECT}-infra-pipeline-${ENVIRONMENT}"
APP_PIPELINE="${PROJECT}-app-pipeline-${ENVIRONMENT}"

# Wait for the Infra pipeline to finish
aws codepipeline get-pipeline-state \
  --name "$INFRA_PIPELINE" \
  --region us-east-1 \
  --query 'stageStates[*].[stageName,latestExecution.status]' \
  --output table

# Wait for the App pipeline to finish
aws codepipeline get-pipeline-state \
  --name "$APP_PIPELINE" \
  --region us-east-1 \
  --query 'stageStates[*].[stageName,latestExecution.status]' \
  --output table
```

You can also follow along in the AWS console:
`https://console.aws.amazon.com/codesuite/codepipeline/pipelines`

---

## 11. Verify the Deployment

Run through this checklist after the pipeline completes successfully.

### 11a. CloudFront smoke test (before DNS cut-over)
```bash
echo "Testing via CloudFront domain: https://$CLOUDFRONT_DOMAIN"
curl -s -o /dev/null -w "HTTP %{http_code} — %{url_effective}\n" \
  "https://$CLOUDFRONT_DOMAIN"
# Expected: HTTP 200
```

### 11b. API smoke test
```bash
curl -s "${API_ENDPOINT}/weather?city=London" | python3 -m json.tool | head -20
# Expected: JSON with "current" and "forecast" keys
```

### 11c. Security headers
```bash
curl -sI "https://$CLOUDFRONT_DOMAIN" | grep -E 'strict-transport|x-frame|x-content'
# Expected: HSTS, X-Frame-Options: DENY, X-Content-Type-Options: nosniff
```

### 11d. S3 direct access blocked
```bash
curl -s -o /dev/null -w "%{http_code}" \
  "https://${WEBSITE_BUCKET}.s3.amazonaws.com/index.html"
# Expected: 403 (public access blocked — only CloudFront can read)
```

### 11e. HTTPS redirect
```bash
curl -sI "http://$CLOUDFRONT_DOMAIN" | grep -i location
# Expected: Location: https://...  (301 or 302 redirect to HTTPS)
```

### 11f. Subscribe to alarm notifications
```bash
echo "Alarm Topic ARN: $ALARM_TOPIC_ARN"
aws sns subscribe \
  --topic-arn "$ALARM_TOPIC_ARN" \
  --protocol email \
  --notification-endpoint "joseph.baezb@gmail.com" \
  --region us-east-1
echo "Check your inbox for the SNS confirmation email and click Confirm."
```

### 11g. CloudWatch dashboard
```
https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=weather-dashboard-production
```

### 11h. DNS cut-over (if not using Route 53 auto-record)
If you didn't set `HostedZoneId` in Step 7, add an ALIAS or CNAME record
at your DNS provider now:

| Record                        | Type  | Value                                |
| ----------------------------- | ----- | ------------------------------------ |
| `weather.craftingnewtech.com` | CNAME | `<CloudFront domain>.cloudfront.net` |

After DNS propagates (1–5 minutes for Route 53, up to 48 hours for other
providers), `https://weather.craftingnewtech.com` will serve the dashboard.

---

## 12. Ongoing Update Workflow

All subsequent changes deploy automatically via the pipeline.

```bash
# 1. Make your changes locally
# 2. Run local gates (mirrors CI exactly)
bash pipeline/scripts/validate-templates.sh
bash pipeline/scripts/run-tests.sh

# 3. Commit and push — pipeline triggers within ~30 seconds
git add <changed files>
git commit -m "feat: your change description"
git push aws main

# 4. Monitor (optional)
aws codepipeline get-pipeline-state \
  --name "${PROJECT}-pipeline-${ENVIRONMENT}" \
  --region us-east-1 \
  --query 'stageStates[*].[stageName,latestExecution.status]' \
  --output table
```

### What happens on every push

A Lambda filter runs first and inspects the changed file paths via `codecommit:GetDifferences`:

- **Docs-only commit** (`docs/`, `*.md` at root, `diagrams/*.md`) → **both pipelines skipped**. The Lambda logs `"Docs-only commit — skipping pipeline"` to CloudWatch and returns without starting either CodePipeline.
- **`infrastructure/**` changed** → **Infra pipeline only**.
- **`backend/**`/`frontend/**` changed** → **App pipeline only**.
- **Both changed in the same commit** → Infra pipeline runs first; the App pipeline is held (via `PipelineReleaseLambdaRole`'s coordination flag) until Infra's own Deploy stage succeeds — see `pipeline-topology.md`'s routing rule.
- **Edge cases** (new branch, empty diff) → both pipelines run as a fail-safe.

Filter Lambda logs: CloudWatch → Log groups → `/aws/lambda/weather-dashboard-pipeline-filter-production`

### What the Infra pipeline does (`infrastructure/**` changes)
1. **Gate 1** — `cfn-lint` on all 15 CloudFormation templates
2. **Gate 2** — `checkov` security rules on all templates
3. **Gate 3** — `aws cloudformation validate-template` (AWS-side check)
4. **Deploy** — `cfn package` + `cfn deploy` against the primary region (us-east-1)
5. **DeploySecondary** *(only if a secondary region is configured)* — same stack set re-applied with `--region us-west-2`, per the multi-region rollout (`WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md`)

### What the App pipeline does (`backend/**`/`frontend/**` changes)
1. **Gate 4** — `pip-audit` CVE scan on Lambda dependencies
2. **Gate 5** — `pytest` unit tests, ≥ 80% coverage required
3. **Deploy** — new Lambda ZIP built/uploaded, version published, CodeDeploy canary traffic shift, Provisioned Concurrency set on the `live` alias, frontend `aws s3 sync` (assets: max-age 1 day, HTML: no-cache), architecture-diagram app built and synced to `/architecture/`, CloudFront cache invalidated
4. **DeploySecondary** *(only if a secondary region is configured)* — publishes a Lambda version and shifts traffic in us-west-2; no frontend sync/CDN invalidation (the secondary region is API/Lambda-only, passive standby)

Any gate failure stops that pipeline — nothing deploys until all gates pass. A failure in one pipeline does not affect the other's independently-scoped resources.

---

## 13. Re-deploying After a Failed Bootstrap (ROLLBACK_COMPLETE)

If the initial `aws cloudformation deploy` fails and the stack ends up in
`ROLLBACK_COMPLETE`, you cannot update it — you must delete it and start over.
Before re-running Step 7, check for **retained resources** that survived the
rollback. These have `DeletionPolicy: Retain` in the templates and will cause
"already exists" conflicts if not deleted first.

### Step 1 — Delete the failed stack

```bash
aws cloudformation delete-stack \
  --stack-name "$STACK_NAME" \
  --region us-east-1

aws cloudformation wait stack-delete-complete \
  --stack-name "$STACK_NAME" \
  --region us-east-1 \
  && echo "Stack deleted."
```

### Step 2 — Identify and delete retained resources

Run these checks and delete any resources that exist with placeholder or empty content:

```bash
# Check WebsiteBucket
aws s3 ls "s3://weather-dashboard-website-${AWS_ACCOUNT_ID}-production" 2>&1 || echo "Not found — OK"

# If it exists and is empty (or only has bootstrap content), delete it:
aws s3 rb "s3://weather-dashboard-website-${AWS_ACCOUNT_ID}-production" --force

# Check CloudWatch log groups
aws logs describe-log-groups \
  --log-group-name-prefix "/aws/lambda/weather-dashboard" \
  --region us-east-1 \
  --query 'logGroups[*].logGroupName'

aws logs describe-log-groups \
  --log-group-name-prefix "/aws/apigateway/weather-dashboard" \
  --region us-east-1 \
  --query 'logGroups[*].logGroupName'

# Delete both log groups (they will be re-created by the new deploy)
aws logs delete-log-group \
  --log-group-name "/aws/lambda/weather-dashboard-handler-production" \
  --region us-east-1 2>&1 || echo "Not found — OK"

aws logs delete-log-group \
  --log-group-name "/aws/apigateway/weather-dashboard-production" \
  --region us-east-1 2>&1 || echo "Not found — OK"

# Check SSM parameter
aws ssm get-parameter \
  --name "/weather-dashboard/openweathermap-api-key" \
  --region us-east-1 2>&1 || echo "Not found — OK"

# If it exists with only the placeholder value, delete it:
aws ssm delete-parameter \
  --name "/weather-dashboard/openweathermap-api-key" \
  --region us-east-1 2>&1 || echo "Not found — OK"
```

> **Important:** Do NOT delete these resources if they contain real production
> data (logs, real API key). If they hold real content, use CloudFormation
> resource import to re-adopt them instead of deleting and recreating.

### Step 3 — Verify the artifacts bucket is intact

The artifacts bucket is pre-created and should survive any stack rollback.
Verify the Lambda ZIP is still there before re-running Step 7:

```bash
aws s3 ls "s3://${ARTIFACTS_BUCKET}/lambda/lambda.zip" && echo "ZIP ready."
```

If missing, re-run Steps 5–6 to rebuild and re-upload the ZIP.

### Step 4 — Re-deploy

Proceed from Step 7. The fixes applied to `02-storage.yml` and `07-ssm.yml`
prevent recurrence of all three original deployment failures.

---

## 14. Rollback Procedures

### Rollback Lambda to previous version
```bash
# Lambda publishes a new version on every pipeline deploy.
# List versions to find the previous one:
LAMBDA_NAME="${PROJECT}-handler-${ENVIRONMENT}"

aws lambda list-versions-by-function \
  --function-name "$LAMBDA_NAME" \
  --region us-east-1 \
  --query 'Versions[*].[Version,LastModified,CodeSize]' \
  --output table

# Roll back to a specific version (e.g., version 3):
PREVIOUS_VERSION="3"
aws lambda update-function-code \
  --function-name "$LAMBDA_NAME" \
  --s3-bucket "$ARTIFACTS_BUCKET" \
  --s3-key "lambda/lambda.zip" \
  --region us-east-1
# Then update function configuration to point to the previous version alias if needed
```

### Rollback CloudFormation stack
```bash
# CloudFormation keeps the previous template. To roll back:
aws cloudformation cancel-update-stack \
  --stack-name "$STACK_NAME" \
  --region us-east-1
# (Only works while update is IN_PROGRESS)

# If stack is in ROLLBACK_FAILED state:
aws cloudformation continue-update-rollback \
  --stack-name "$STACK_NAME" \
  --region us-east-1
```

### Rollback frontend (restore previous S3 content)
```bash
# If you enabled S3 versioning (artifacts bucket has it, website bucket does not).
# To restore: re-run a previous pipeline build, or sync from a local backup.
# Quickest path: revert the git commit and push to trigger a new pipeline run.
git revert HEAD
git push aws main
```

### Clear DynamoDB weather cache
```bash
# Force all clients to get fresh data from OpenWeatherMap:
aws dynamodb scan \
  --table-name WeatherCache \
  --region us-east-1 \
  --projection-expression "city" \
  --query 'Items[*].city.S' \
  --output text | tr '\t' '\n' | while read city; do
    aws dynamodb delete-item \
      --table-name WeatherCache \
      --key "{\"city\":{\"S\":\"$city\"}}" \
      --region us-east-1
    echo "Deleted cache entry: $city"
  done
```

### Rotate the OWM API key
```bash
# Replace the key — Lambda picks it up on the next cold start.
aws ssm put-parameter \
  --name "/weather-dashboard/openweathermap-api-key" \
  --value "YOUR_NEW_API_KEY" \
  --type SecureString \
  --overwrite \
  --region us-east-1

echo "API key updated. Lambda will use the new key after its next cold start."
echo "To force an immediate cold start, update the Lambda's env vars with a no-op change:"
aws lambda update-function-configuration \
  --function-name "${PROJECT}-handler-${ENVIRONMENT}" \
  --environment "Variables={LOG_LEVEL=INFO,TABLE_NAME=WeatherCache,SSM_PARAMETER_NAME=/weather-dashboard/openweathermap-api-key,ALLOWED_ORIGIN=https://${DOMAIN},KEY_ROTATED=$(date +%s)}" \
  --region us-east-1 > /dev/null
echo "Lambda cold-started. New key is active."
```

---

## 15. Teardown

> **Warning:** This permanently deletes all AWS resources except those with
> `DeletionPolicy: Retain` (SSM parameter, CloudWatch log groups, S3 buckets).
> S3 buckets must be emptied before deletion.

```bash
# 1. Empty the S3 buckets (CloudFormation cannot delete non-empty buckets)
aws s3 rm "s3://${WEBSITE_BUCKET}" --recursive
aws s3 rm "s3://${ARTIFACTS_BUCKET}" --recursive

# 2. Delete the master stack (deletes all nested stacks in reverse order)
aws cloudformation delete-stack \
  --stack-name "$STACK_NAME" \
  --region us-east-1

echo "Waiting for stack deletion (~5 minutes)..."
aws cloudformation wait stack-delete-complete \
  --stack-name "$STACK_NAME" \
  --region us-east-1 \
  && echo "Stack deleted."

# 3. Delete the SSM parameter (DeletionPolicy: Retain keeps it after stack delete)
aws ssm delete-parameter \
  --name "/weather-dashboard/openweathermap-api-key" \
  --region us-east-1

# 4. Delete the artifacts bucket (was pre-created in bootstrap, not deleted by CFN)
aws s3 rb "s3://${ARTIFACTS_BUCKET}" --force

# 5. Delete the ACM certificate (only after CloudFront distribution is deleted)
aws acm delete-certificate \
  --certificate-arn "$ACM_CERT_ARN" \
  --region us-east-1

echo "All resources removed."
```

---

## 16. Multi-Region Bootstrap (us-west-2)

Full command-level detail (including the design corrections found along
the way — a bucket-naming collision, a health-check Host-header mixup,
and three IAM region-pinning gaps) lives in
[`WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md`](./WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md).
This section is the condensed bootstrap sequence for setting up the
secondary region from scratch.

**Prerequisite:** the primary region (sections 1-11 above) must already be
fully deployed and healthy — the secondary region reuses the primary's
global IAM roles, DynamoDB table, and Secrets Manager secret via native
cross-region features, it doesn't recreate them.

```bash
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# 1. Bootstrap the secondary region's own artifacts bucket (Lambda code
#    must be in the same region as the function; S3 names are globally
#    unique, so this needs a distinct suffix from the primary bucket)
aws cloudformation deploy --region us-west-2 \
  --template-file infrastructure/cloudformation/00-bootstrap.yml \
  --stack-name weather-dashboard-bootstrap-production \
  --parameter-overrides ProjectName=weather-dashboard Environment=production BucketNameSuffix=-us-west-2

WEST_BUCKET="weather-dashboard-artifacts-${ACCOUNT_ID}-production-us-west-2"

# 2. Copy the Lambda deployment ZIP from the primary bucket
aws s3 cp "s3://weather-dashboard-artifacts-${ACCOUNT_ID}-production/lambda/lambda.zip" \
  "s3://${WEST_BUCKET}/lambda/lambda.zip" --source-region us-east-1 --region us-west-2

# 3. Package and deploy the secondary-region stack (Lambda + API Gateway only —
#    IAM/S3/CloudFront/pipelines are global or primary-only by design)
aws cloudformation package --region us-west-2 \
  --template-file infrastructure/cloudformation/master-secondary.yml \
  --s3-bucket "$WEST_BUCKET" --s3-prefix cloudformation \
  --output-template-file infrastructure/cloudformation/master-secondary-packaged.yml

aws cloudformation deploy --region us-west-2 \
  --template-file infrastructure/cloudformation/master-secondary-packaged.yml \
  --stack-name weather-dashboard-secondary-production \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    ArtifactsBucketName="$WEST_BUCKET" \
    LambdaZipKey=lambda/lambda.zip \
    HostedZoneId=<your-hosted-zone-id>

# 4. Enable DynamoDB Global Tables (CLI, not CloudFormation — a resource
#    Type change from AWS::DynamoDB::Table would force a replace/data-loss)
aws dynamodb update-table --region us-east-1 --table-name WeatherCache \
  --replica-updates 'Create={RegionName=us-west-2}'

# 5. Deploy GuardDuty (standalone, operator credentials, this region only —
#    CloudTrail is already multi-region from us-east-1, don't duplicate it)
aws cloudformation deploy --region us-west-2 \
  --template-file infrastructure/cloudformation/11-audit.yml \
  --stack-name weather-dashboard-audit-production \
  --capabilities CAPABILITY_IAM --parameter-overrides DeployCloudTrail=false

# 6. Enable Secrets Manager replication and widen the multi-region IAM Sids
#    (redeploy the PRIMARY master stack with these two new parameters set —
#    both default to '' / disabled, so this is what turns them on)
aws cloudformation package \
  --template-file infrastructure/cloudformation/master.yml \
  --s3-bucket "weather-dashboard-artifacts-${ACCOUNT_ID}-production" --s3-prefix cloudformation \
  --output-template-file infrastructure/cloudformation/master-packaged.yml

aws cloudformation deploy \
  --template-file infrastructure/cloudformation/master-packaged.yml \
  --stack-name weather-dashboard-master-production \
  --role-arn "arn:aws:iam::${ACCOUNT_ID}:role/weather-dashboard-cfn-deploy-role-production" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --parameter-overrides SecondaryRegion=us-west-2 SecondaryArtifactsBucketName="$WEST_BUCKET"

# 7. Deploy the Route 53 Failover DNS stack (standalone, operator credentials)
#    - capture both regions' regional API domain outputs first
PRIMARY_DOMAIN=$(aws cloudformation describe-stacks --stack-name weather-dashboard-master-production \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiRegionalDomainName`].OutputValue' --output text)
PRIMARY_ZONE=$(aws cloudformation describe-stacks --stack-name weather-dashboard-master-production \
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
    HostedZoneId=<your-hosted-zone-id> \
    PrimaryDomainName="$PRIMARY_DOMAIN" PrimaryHostedZoneId="$PRIMARY_ZONE" \
    PrimaryHealthCheckDomainName=<primary-raw-execute-api-hostname> \
    SecondaryDomainName="$SECONDARY_DOMAIN" SecondaryHostedZoneId="$SECONDARY_ZONE"

# 8. Point the frontend at the new failover domain, then commit/push as normal
#    (frontend/js/config.js: API_BASE_URL = 'https://api.weather.craftingnewtech.com')
```

**Verify everything** using
[`WAPMultiRegion/WeatherApp-MultiRegion-Runbook-V1.md`](./WAPMultiRegion/WeatherApp-MultiRegion-Runbook-V1.md)
— it has a check for every resource created above, plus the full
failover/failback drill procedure.
