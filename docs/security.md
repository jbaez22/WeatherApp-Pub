# Security

This document describes the threat model, security controls, and IAM design for the Weather Dashboard.

---

## Threat Model

### Assets

| Asset                   | Sensitivity                     | Location                                          |
| ----------------------- | ------------------------------- | ------------------------------------------------- |
| OpenWeatherMap API key  | High — paid-rate abuse possible | SSM Parameter Store (SecureString, KMS-encrypted) |
| Weather cache data      | Low — public weather data       | DynamoDB                                          |
| Static frontend files   | Low — public                    | S3 (private, CloudFront-only)                     |
| AWS account credentials | Critical                        | IAM roles only — no long-lived keys in code       |

### Threats Considered

| Threat                                      | Mitigating Control                                                                              |
| ------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| API key leaked via frontend code            | Key never placed in JS; Lambda retrieves it at runtime via SSM                                  |
| API key leaked via response body            | Lambda strips key from all responses; validated in E2E tests                                    |
| Direct S3 bucket access (bypass CloudFront) | S3 is private; bucket policy allows only CloudFront OAC principal                               |
| CORS bypass (cross-origin API calls)        | API Gateway CORS locks `Allow-Origin` to `https://weather.craftingnewtech.com`                  |
| Input injection / SSRF via `city` parameter | Whitelist regex validates input before any downstream call                                      |
| Clickjacking                                | `X-Frame-Options: DENY` on all CloudFront responses                                             |
| MIME sniffing attacks                       | `X-Content-Type-Options: nosniff` on all CloudFront responses                                   |
| Protocol downgrade                          | HSTS (`max-age=63072000; includeSubDomains`) + CloudFront HTTP→HTTPS redirect                   |
| Runaway Lambda cost (bot/DDoS)              | `ReservedConcurrentExecutions: 10` caps Lambda invocations                                      |
| Dependency vulnerabilities                  | `pip-audit` scans `requirements.txt` in every pipeline run; blocks on HIGH/CRITICAL             |
| IaC misconfigurations                       | `checkov` scans all CloudFormation templates on every pipeline run; `cfn-lint` validates syntax |
| Least-privilege violations                  | Eight purpose-scoped IAM roles; no `Action: "*"` or `Resource: "*"` policies                    |

---

## Security Controls by Layer

### 1. Secrets Management

The OpenWeatherMap API key is stored as a `SecureString` in AWS Systems Manager Parameter Store, encrypted with the AWS-managed KMS key for SSM.

- Parameter path: `/weather-dashboard/openweathermap-api-key`
- Lambda retrieves it once per cold start and caches it in the execution context
- The value is never written to CloudWatch Logs, never returned to the client, and never present in any source file

```bash
# How the key is stored (one-time bootstrap step):
aws ssm put-parameter \
  --name "/weather-dashboard/openweathermap-api-key" \
  --value "YOUR_KEY" \
  --type SecureString \
  --region us-east-1
```

### 2. S3 + CloudFront Access Control

S3 bucket `weather-dashboard-website-*` has:
- **Block Public Access** enabled (all four settings)
- No bucket ACLs
- A bucket policy that grants `s3:GetObject` to the CloudFront distribution's OAC principal **only**:
  ```json
  "Condition": {
    "StringEquals": {
      "AWS:SourceArn": "arn:aws:cloudfront::<account-id>:distribution/<id>"
    }
  }
  ```

Any direct HTTP/S request to the S3 regional URL returns **403 Forbidden** — verified by the E2E test suite.

### 3. Transport Security

| Control        | Configuration                                                    |
| -------------- | ---------------------------------------------------------------- |
| HSTS           | `Strict-Transport-Security: max-age=63072000; includeSubDomains` |
| HTTPS redirect | CloudFront viewer protocol policy: Redirect HTTP to HTTPS        |
| TLS version    | CloudFront security policy `TLSv1.2_2021` (minimum TLS 1.2)      |
| Certificate    | ACM certificate for `weather.craftingnewtech.com` (auto-renewed) |

### 4. HTTP Security Headers

Applied via CloudFront ResponseHeadersPolicy to every response:

| Header                      | Value                                 |
| --------------------------- | ------------------------------------- |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains` |
| `X-Frame-Options`           | `DENY`                                |
| `X-Content-Type-Options`    | `nosniff`                             |
| `Referrer-Policy`           | `strict-origin-when-cross-origin`     |
| `X-XSS-Protection`          | `1; mode=block`                       |

### 5. Input Validation

The Lambda function validates the `city` query parameter with a whitelist regex before making any call to OpenWeatherMap or DynamoDB:

```python
import re
CITY_RE = re.compile(r'^[a-zA-Z0-9\s,\-\.]{1,100}$')
```

A city value that does not match returns **400 Invalid Request** immediately, preventing injection or SSRF.

### 6. CORS

API Gateway HTTP API is configured with an explicit CORS policy:

```yaml
CorsConfiguration:
  AllowOrigins:
    - "https://weather.craftingnewtech.com"
  AllowMethods:
    - GET
    - OPTIONS
  AllowHeaders:
    - Content-Type
  MaxAge: 86400
```

Requests from `localhost`, `http://` origins, or any third-party domain are rejected by browser CORS enforcement.

### 7. Supply Chain Security

Every CodePipeline run executes:

1. **`pip-audit`** — scans `backend/lambda/requirements.txt` against PyPI advisory database; any HIGH or CRITICAL CVE aborts the build
2. **`checkov`** — scans all 15 CloudFormation templates for security anti-patterns (overly permissive IAM, missing encryption, public S3 access, etc.) using 1,000+ rules
3. **`cfn-lint`** — validates template syntax and resource properties

All three gates run in the `pre_build` phase with `on-failure: ABORT`, so a vulnerability or misconfiguration never reaches the deploy stage.

### 8. Threat Detection (GuardDuty)

Amazon GuardDuty runs as a standalone stack (`11-audit.yml`, deployed under
operator credentials, not the pipeline's `CloudFormationDeployRole` — see
ADR-018) in **both** us-east-1 and us-west-2 as of the multi-region rollout
(`WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md`). Unlike the
CloudTrail management-event trail (a single multi-region trail already
covers every region from us-east-1), GuardDuty detectors are scoped per
region and billed per region — the us-west-2 detector is a genuinely
separate resource. Both detectors forward findings to the same
`weather-dashboard-security-findings-production` SNS topic via an
EventBridge rule on the default event bus.

### 9. Multi-Region Secrets and IAM

The OpenWeatherMap API key (§1) replicates to us-west-2 via Secrets
Manager's native `ReplicaRegions` — no pipeline scripting, no second
rotation schedule (rotation only ever runs against the primary secret;
replication propagates the new value automatically). The replica uses the
AWS-managed KMS key in that region rather than a second CMK — not worth
the added cost/complexity for this secret given IAM already scopes access
tightly.

Three IAM `Resource` ARN Sids in `01-iam.yml` (`LambdaExecutionRole`'s
DynamoDB/Secrets access, `AppCodeBuildRole`'s Lambda/CodeDeploy Sids) are
explicitly widened to list both regions' ARNs — IAM roles are global, but
a `Resource` ARN built with `${AWS::Region}` only ever resolves to
us-east-1 (the only region `01-iam.yml` is deployed in), so without this
the secondary region's Lambda/CodeBuild would get `AccessDenied` reading
its own local resources. Kept as an explicit two-item list rather than a
wildcard region, preserving the same least-privilege bar as every other
Sid in this file.

### 10. Local Security Scanning

A full 11-tool security scan can be run locally at any time using the `/security-scan` Claude Code skill:

```
/security-scan
```

Tools invoked (all available locally via Homebrew/pip):

| Tool           | Scope                                              |
| -------------- | -------------------------------------------------- |
| gitleaks       | Secret leaks in git history and working tree       |
| npm audit      | Node.js dependency CVEs                            |
| Trivy (config) | CloudFormation IaC misconfigurations               |
| Checkov        | Multi-framework IaC scan                           |
| Semgrep        | Python/JS/Bash SAST                                |
| Bandit         | Python-specific SAST (injection, subprocess, eval) |
| ShellCheck     | Bash script warnings and errors                    |
| gosec          | Go SAST (skipped — no Go code)                     |
| tfsec          | Terraform IaC (skipped — CloudFormation project)   |
| cargo audit    | Rust CVEs (skipped — no Rust code)                 |
| cppcheck       | C/C++ analysis (skipped — no C/C++ code)           |

The scan produces a dated Markdown report (`security-scan-report-YYYY-MM-DD.md`) saved to the project root. Run after any dependency update or before pushing a significant change.

---

## IAM Design

Eight purpose-scoped IAM roles are defined in `infrastructure/cloudformation/01-iam.yml`. None uses `Action: "*"` or `Resource: "*"` (except the AWS-mandated `Resource: '*'` on a handful of `List*`/`Describe*`/drift-detection actions that the AWS API itself does not support scoping — see `cicd-iam-policy.md`'s Step 3 table).

### LambdaExecutionRole

| Permission                                        | Resource                                                  | Purpose               |
| ------------------------------------------------- | --------------------------------------------------------- | --------------------- |
| `logs:CreateLogGroup/Stream`, `logs:PutLogEvents` | `/aws/lambda/weather-dashboard-handler-*`                 | Write structured logs |
| `dynamodb:GetItem`, `dynamodb:PutItem`            | WeatherCache table ARN (both regions since Global Tables) | Read/write cache      |
| `ssm:GetParameter`                                | `/weather-dashboard/openweathermap-api-key` ARN           | Retrieve API key      |

### InfraCodeBuildRole

Used by the Infra pipeline's Validate/Deploy/DeploySecondary CodeBuild projects — `cfn-lint`/`checkov` scanning, then `cloudformation:*` deploy actions (create/update nested stacks), S3 read/write on the artifacts bucket, and `iam:PassRole` scoped to the roles CloudFormation itself needs to assume. Never touches Lambda code or the CDN.

### AppCodeBuildRole

Used by the App pipeline's Test/Deploy/DeploySecondary CodeBuild projects — `lambda:UpdateFunctionCode`/publish-version, CodeDeploy traffic-shift actions, S3 sync to the website bucket, and `cloudfront:CreateInvalidation`. Never touches CloudFormation stacks (that split is the whole point of `WeatherApp-PipeSplit-ImplePlan-V1.md`).

### CodePipelineServiceRole

Limited to: starting CodeBuild jobs, reading/writing the artifacts bucket, and publishing pipeline events to EventBridge.

### CloudFormationDeployRole

Used during `cfn deploy` to create/update nested stacks. Scoped to CloudFormation, S3, Lambda, API Gateway, DynamoDB, SSM, CloudFront, and Route 53 operations — all at the project's resource ARN level.

### PipelineFilterLambdaRole

| Permission                                 | Resource                                          | Purpose                                        |
| ------------------------------------------ | ------------------------------------------------- | ---------------------------------------------- |
| `codecommit:GetDifferences`                | Project repo ARN                                  | Inspect changed file paths on each push        |
| `codepipeline:StartPipelineExecution`      | Project pipeline ARN                              | Start the pipeline when non-docs files changed |
| CloudWatch Logs write (via managed policy) | `/aws/lambda/weather-dashboard-pipeline-filter-*` | Write filter decision logs                     |

Used by the pipeline filter Lambda that sits between EventBridge and CodePipeline. Skips the pipeline for docs-only commits (`docs/`, `*.md`, `diagrams/*.md`); starts it for all other changes.

### PipelineReleaseLambdaRole

| ----------------------------------------- | -------------------------------------------- | ---------------------------------------------------------------------- |
| Permission                                | Resource                                     | Purpose                                                                |
| ----------------------------------------- | -------------------------------------------- | ---------------------------------------------------------------------- |
| `ssm:GetParameter`, `ssm:DeleteParameter` | `/weather-dashboard/pipeline-coordination/*` | Read/clear the release-coordination flag written by the Infra pipeline |
| `codepipeline:StartPipelineExecution`     | App pipeline ARN only                        | Start the App pipeline once Infra's own deploy has succeeded           |
| ----------------------------------------- | -------------------------------------------- | ---------------------------------------------------------------------- |

Added by the pipeline split (`WeatherApp-PipeSplit-ImplePlan-V1.md`) to implement "infra deploys first, app pipeline waits for infra's success" ordering from a commit that touches both — see `pipeline-topology.md`'s routing rule.

### EventBridgePipelineRole

Retained in `01-iam.yml` for reference. The EventBridge rule no longer targets CodePipeline directly — it targets the filter Lambda instead, which uses a resource-based policy (`AWS::Lambda::Permission`) for invocation authorization rather than an IAM role on the rule.

---

## Known Accepted Risks

| Risk                                               | Rationale                                                                                                       |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| OpenWeatherMap free-tier key has no IP restriction | OWM free plan does not support IP allowlisting; mitigated by never exposing the key to the browser              |
| No WAF on CloudFront                               | Portfolio-scale traffic does not justify the ~$5/month WAF cost; the Lambda concurrency cap limits blast radius |
| No VPC for Lambda                                  | Lambda does not access VPC resources; adding a VPC would increase cold-start latency and cost                   |

---

## Security Testing

The E2E validation script (`pipeline/scripts/validate-e2e.sh`) performs the following security assertions on every deployment:

- S3 direct URL returns 403
- HTTP redirects to HTTPS
- HSTS header is present
- `X-Frame-Options: DENY` is set
- `X-Content-Type-Options: nosniff` is set
- No API key string present in `config.js`
- CORS probe returns a valid HTTP status (not a network failure or 5xx)
- CORS response does not reflect `evil.example.com` in `Access-Control-Allow-Origin`
