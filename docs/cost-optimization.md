# Cost Optimization

The Weather Dashboard was originally designed to run at **$0.00/month** for a typical portfolio project at current AWS Free Tier limits — that target still holds for every Free-Tier-eligible service below. Since then, the two-pipeline split, the two KMS CMKs (Secrets Manager + CloudTrail), and the multi-region failover rollout added real fixed costs that Free Tier doesn't cover. **Current actual total: ~$21.39/month standard, ~$19.26/month with Free Tier discounts** — see `docs/aws-WeatherApp-resources.md` for the generated, per-resource-verified breakdown (this is the authoritative source; the tables below explain the *design decisions*, not a live total, so check that doc rather than hand-adding these numbers when they need to be current).

---

## Free Tier Analysis

| Service                       | Free Tier Limit                                         | Expected Monthly Usage                              | Est. Cost       |
| ----------------------------- | ------------------------------------------------------- | --------------------------------------------------- | --------------- |
| Amazon S3                     | 5 GB storage / 20,000 GET requests / 2,000 PUT requests | < 50 MB / < 1,000 GET / < 100 PUT                   | $0.00           |
| Amazon CloudFront             | 1 TB data transfer / 10 million HTTPS requests          | < 1 GB / < 100,000 requests                         | $0.00           |
| AWS Lambda                    | 1 million requests / 400,000 GB-seconds compute         | < 50,000 requests / < 5,000 GB-sec                  | $0.00           |
| Amazon API Gateway (HTTP API) | 1 million HTTP API calls                                | < 50,000 calls                                      | $0.00           |
| Amazon DynamoDB               | 25 GB storage / 25 RCU / 25 WCU (provisioned)           | < 1 MB storage / on-demand mode                     | $0.00           |
| AWS SSM Parameter Store       | 10,000 GetParameter calls                               | < 500 calls (Lambda cold starts only)               | $0.00           |
| Amazon CloudWatch Logs        | 5 GB ingestion / 5 GB storage                           | < 100 MB                                            | $0.00           |
| AWS CodeCommit                | 5 active users / unlimited repos                        | 1 user                                              | $0.00           |
| AWS CodeBuild                 | 100 build-minutes/month                                 | < 30 min/month                                      | $0.00           |
| AWS CodePipeline              | 1 free active pipeline                                  | 2 pipelines (Infra + App, since the pipeline split) | $1.00           |
| AWS Certificate Manager       | Free for ACM-managed certs on CloudFront                | 1 certificate                                       | $0.00           |
| AWS KMS                       | Not Free Tier — $1.00/mo per CMK                        | 2 CMKs (Secrets Manager + CloudTrail)               | $2.00           |
| **Total compute / data**      |                                                         |                                                     | **$3.00/month** |

**Fixed costs (not Free Tier):**
| Service              | Cost                             |
| -------------------- | -------------------------------- |
| Route 53 hosted zone | ~$0.50/month                     |
| Route 53 DNS queries | < $0.01/month at portfolio scale |
| **Total fixed**      | **~$0.51/month**                 |

---

## Key Cost Reduction Decisions

### 1. DynamoDB Cache (15-minute TTL)

Without caching, every page load triggers an OpenWeatherMap API call **and** a Lambda invocation. With the 15-minute DynamoDB cache:

- At 100 unique users/day, ~10% request the same city within a 15-minute window → ~10 cache hits
- At 1,000 unique users/day (10x portfolio scale), the cache hit ratio increases substantially
- OWM free tier caps at 60 calls/minute and 1M calls/month — the cache ensures the project never approaches these limits

**DynamoDB On-Demand cost for the cache itself:** The first 25 WCU/RCU are free under the legacy provisioned Free Tier. On-Demand mode costs $1.25 per million writes and $0.25 per million reads. At 50,000 requests/month with a 90% cache hit rate: 5,000 writes + 50,000 reads = ~$0.013/month.

### 2. CloudFront CDN for Static Assets

Without CloudFront, every S3 GET request would count against the per-request cost ($0.004/10,000 GET requests). With CloudFront, the edge cache absorbs repeat requests:

- CloudFront 1 TB Free Tier and 10M requests/month Free Tier cover all expected portfolio traffic
- HTML files have `Cache-Control: no-cache, no-store` (always fresh)
- Static assets (CSS, JS, images) have `Cache-Control: max-age=86400` (24-hour edge cache) → reduces origin requests by 80–95% on repeat visits

### 3. Lambda — Reserved Concurrency Cap

`ReservedConcurrentExecutions: 10` caps Lambda at 10 concurrent executions. At 256 MB and 30-second max timeout, the worst-case cost for 10 simultaneous calls is:

```
10 concurrent × 30 sec × 256 MB ÷ 1024 = 75 GB-seconds
```

The Lambda Free Tier provides 400,000 GB-seconds/month. The cap ensures a bot scan or unexpected traffic spike cannot run up Lambda charges.

### 4. API Gateway HTTP API vs. REST API

HTTP API pricing: $1.00 per million requests (after 1M free)  
REST API pricing: $3.50 per million requests

The HTTP API saves 71% on API Gateway costs at any scale above the Free Tier.

### 5. DynamoDB On-Demand vs. Provisioned

Provisioned capacity requires committing to a minimum WCU/RCU — wasted money at near-zero traffic. On-Demand billing pays only for actual reads and writes. At portfolio scale, On-Demand DynamoDB has **$0 baseline cost** (the first 25 WCU/25 RCU under Free Tier covers all expected usage).

---

## Cost at Scale

If the project were to receive significantly higher traffic (e.g. featured on a portfolio site that goes viral):

| Monthly Requests   | Lambda | API Gateway | DynamoDB | CloudFront | Total (approx.)      |
| ------------------ | ------ | ----------- | -------- | ---------- | -------------------- |
| 50,000 (portfolio) | $0.00  | $0.00       | $0.00    | $0.00      | ~$3.51 (fixed floor) |
| 500,000            | $0.00  | $0.00       | ~$0.06   | $0.00      | ~$3.57               |
| 1,000,000          | $0.00  | $0.00       | ~$0.13   | $0.00      | ~$3.64               |
| 5,000,000          | ~$0.40 | ~$4.00      | ~$0.63   | ~$0.60     | ~$9.14               |
| 10,000,000         | ~$2.00 | ~$9.00      | ~$1.25   | ~$1.60     | ~$17.36              |

Notes:
- Assumes 256 MB Lambda / 500 ms average duration
- Assumes 15-minute cache reduces Lambda invocations to ~10% of CloudFront requests
- CloudFront: $0.0085/GB after 1 TB Free Tier, assuming 1 KB average response
- "Fixed floor" = Route 53 (~$0.51) + second CodePipeline + 2 KMS CMKs (~$3.00, see Free Tier Analysis above); excludes the separate ~$4.22/mo multi-region addition below, which doesn't scale with traffic

---

## Cost Monitoring

Three CloudWatch alarms are configured in `09-monitoring.yml` that indirectly signal unexpected cost:

| Alarm             | Threshold                         | Cost implication                       |
| ----------------- | --------------------------------- | -------------------------------------- |
| Lambda error rate | > 5 errors/5 min                  | May indicate retry storms              |
| Lambda duration   | > 24 seconds (80% of 30s timeout) | Slow calls → higher GB-seconds cost    |
| API Gateway 5xx   | > 10 errors/5 min                 | May indicate Lambda timeouts or errors |

For explicit cost monitoring, enable AWS Cost Explorer and set a billing alert at $5/month:

```bash
aws budgets create-budget \
  --account-id ABC-EXAMPLE-XXXX \
  --budget '{
    "BudgetName": "weather-dashboard-monthly",
    "BudgetLimit": {"Amount": "5", "Unit": "USD"},
    "TimeUnit": "MONTHLY",
    "BudgetType": "COST"
  }' \
  --notifications-with-subscribers '[{
    "Notification": {
      "NotificationType": "ACTUAL",
      "ComparisonOperator": "GREATER_THAN",
      "Threshold": 80
    },
    "Subscribers": [{"SubscriptionType": "EMAIL", "Address": "you@example.com"}]
  }]'
```

---

## Multi-Region Failover Cost (us-west-2)

As of `WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md`, the app runs
active-passive across two regions. Additional monthly cost, verified
against the actual deployed resources (not just estimated):

| ---------------------------------------------------------------------- | ---------------- |
| Addition                                                               | Monthly          |
| ---------------------------------------------------------------------- | ---------------- |
| Lambda in us-west-2 (passive — health checks only)                     | $0.05            |
| API Gateway in us-west-2 (passive)                                     | $0.01            |
| DynamoDB Global Tables replication writes                              | ~$0.15           |
| Secrets Manager secret replica                                         | $0.40            |
| GuardDuty detector in us-west-2 (billed per region, unlike CloudTrail) | $3.00            |
| Second regional artifacts bucket (us-west-2)                           | ~$0.01           |
| Route 53 health check (1 endpoint)                                     | $0.50            |
| CloudWatch alarm for failover status                                   | $0.10            |
| **Total additional**                                                   | **~$4.22/month** |
| ---------------------------------------------------------------------- | ---------------- |

Dominated by the second GuardDuty detector — GuardDuty, unlike the
already-multi-region CloudTrail trail, is priced and scoped per region.
This is still one of the best value investments in the stack relative to
the multiple real us-east-1 outages (Nov 2021, Dec 2021, Jun 2023) that
motivated the whole effort.

## Services Intentionally NOT Used

| Service                      | Why avoided                                                     | Approximate cost saved |
| ---------------------------- | --------------------------------------------------------------- | ---------------------- |
| AWS WAF on CloudFront        | Not justified at portfolio scale                                | ~$5/month base         |
| VPC + NAT Gateway            | Lambda doesn't need VPC; no VPC resources in the stack          | ~$32/month             |
| ElastiCache (Redis)          | DynamoDB TTL cache is sufficient; Redis needs a VPC cluster     | ~$15–30/month          |
| EC2 / Fargate                | All compute is Lambda (pay-per-invocation)                      | ~$8–30/month           |
| Provisioned Concurrency      | Cold starts are acceptable at < 500 ms                          | ~$1–5/month            |
| CloudTrail (dedicated trail) | Management events are free in CloudTrail; no data events needed | ~$2/month              |
