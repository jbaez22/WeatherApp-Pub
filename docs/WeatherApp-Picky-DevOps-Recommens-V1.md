# WeatherApp — Infrastructure & Pipeline Recommendations (V1)

**Review date:** 2026-07-16
**Reviewer stance:** Senior DevOps / Cloud Infrastructure audit against this
project's own established standards (see `~/.claude/instructions/*.md` —
`cost-optimization.md`, `secrets-hygiene.md`, `pre-push-validation.md`,
`code-quality.md`, `documentation-standards.md`). Every finding below was
verified against the live repository and, where applicable, live AWS state
(`aws budgets describe-budgets`, `aws sns list-subscriptions-by-topic`) —
none of this is guessed from file contents alone.

Findings are ordered worst-first. Each entry has: what's wrong, the exact
evidence, the most likely reason it happened, and concrete steps to fix it.

---

## Summary

| ---- | ---------------------------------------------------------------- | ------ | ---------------------------------------------------- |
| Sev  | Finding                                                          | Status | Primary location                                     |
| ---- | ---------------------------------------------------------------- | ------ | ---------------------------------------------------- |
| CRIT | `AlarmTopic` SNS topic has zero subscribers                      | Open   | `infrastructure/cloudformation/09-monitoring.yml`    |
| CRIT | No AWS Budgets alert configured                                  | Open   | Account-level (no IaC resource anywhere)             |
| MED  | No secret-scanning gate (gitleaks/truffleHog)                    | Open   | Pipeline-wide                                        |
| MED  | No Python lint/type-check gates (ruff, mypy)                     | Open   | `Makefile`, `pipeline/buildspec/*.yml`               |
| MED  | No `npm audit` gate for `diagrams/interactive`                   | Open   | `pipeline/buildspec/deploy-app.yml`                  |
| MED  | README doc-index links the superseded API doc, not V2            | Open   | `README.md:227`                                      |
| MED  | Buildspec naming breaks the project's own `-infra-`/`-app-` rule | Open   | `pipeline/buildspec/validate-deployment.yml`         |
| LOW  | Stale comments reference pre-split buildspec filenames           | Open   | `Makefile`, `pipeline/scripts/validate-templates.sh` |
| LOW  | pip-audit gate label overstates what it actually filters         | Open   | `pipeline/scripts/validate-templates.sh`             |
| LOW  | `ssm_secrets.py` contains zero SSM code — misleading name        | Open   | `backend/lambda/ssm_secrets.py`                      |
| ---- | ---------------------------------------------------------------- | ------ | ---------------------------------------------------- |

---

## 1. [CRITICAL] `AlarmTopic` SNS topic has zero subscribers

**Finding:** All four CloudWatch alarms — `lambda-errors`, `lambda-near-timeout`,
`api-5xx`, `api-4xx-spike` (`09-monitoring.yml:93-174`) — publish to
`AlarmTopic` (`weather-dashboard-alarms-production`) via `AlarmActions` /
`OKActions`. Live check confirms it has **no subscriptions at all**:

```bash
aws sns list-subscriptions-by-topic \
  --topic-arn arn:aws:sns:us-east-1:123456789012:weather-dashboard-alarms-production
# → empty
```

Every other project SNS topic (`app-deploy-approval`, `deploy-approval`,
`key-rotation`, `security-findings`) has `you@example.com` confirmed
as a subscriber. Only the alarm topic was skipped.

**Why it happened:** `09-monitoring.yml` itself documents this as a manual
step, never automated as a CloudFormation `AWS::SNS::Subscription` resource
(the project has none of those anywhere — every subscription is done by
hand):

```yaml
# ── SNS Topic for Alarm Notifications ────────────────────────────────────
# Subscribe your email after bootstrap:
#   aws sns subscribe --topic-arn <AlarmTopicArn> \
#     --protocol email --notification-endpoint your@email.com
```

The other four topics all gate something that blocks forward progress if
ignored — a pipeline approval action literally can't proceed, a rotation
notice needs a human glance — so subscribing to them got noticed and done
during setup. `AlarmTopic` is purely passive: nothing breaks or waits on it,
so the one-line manual bootstrap step in the comment was never actually run.

**Action steps:**

1. Subscribe immediately (unblocks live alerting today):
   ```bash
   aws sns subscribe \
     --topic-arn arn:aws:sns:us-east-1:123456789012:weather-dashboard-alarms-production \
     --protocol email --notification-endpoint you@example.com
   ```
2. Confirm the subscription via the email SNS sends (subscriptions sit
   `PendingConfirmation` until clicked — verify with
   `list-subscriptions-by-topic` afterward, look for a real `SubscriptionArn`
   instead of `PendingConfirmation`).
3. Consider a follow-up hardening task (not urgent): add a second,
   non-email endpoint (e.g. a Slack webhook via a small SNS-to-Slack Lambda,
   or PagerDuty integration) so alarm delivery doesn't depend on one inbox.
4. Optional process fix: since this project already tags every new SNS
   topic's ARN as a stack Output, add a runbook checklist line ("confirm
   `aws sns list-subscriptions-by-topic` shows a live subscriber") to
   whatever post-deploy verification step already exists for new
   infrastructure, so a future new topic can't silently ship unsubscribed.

---

## 2. [CRITICAL] No AWS Budgets alert configured

**Finding:** `aws budgets describe-budgets --account-id <account>` returns
no budgets at all for this account. This project's own global cost standard
(`cost-optimization.md`) requires "an AWS Budgets alert (or equivalent) with
SNS notification thresholds at 50%, 80%, and 100% of expected monthly
spend... set up in the same phase as the first billable resource, not
retroactively after a surprise bill." This project has been live and
billable (currently ~$17/mo per `docs/aws-WeatherApp-resources.md`) for
weeks with no automated spend guardrail.

**Why it happened:** Cost visibility for this project has been handled
manually instead — the `docs/aws-WeatherApp-resources.md` /
`.xlsx` resource-inventory tool and a documented "review AWS Cost Explorer /
Budgets for this project's tag" runbook step (`WeatherApp-runbook.md:792`)
cover cost *estimation and periodic review*, but nothing in the 12-phase
build plan ever had a discrete task to stand up an actual `AWS::Budgets::Budget`
resource. It's a real gap in the plan, not a deliberate decision to skip it.

**Action steps:**

1. Add a new resource to an appropriate template (either a new
   `infrastructure/cloudformation/12-budget.yml` nested stack, or fold into
   `11-audit.yml` alongside the other account-level operational tooling,
   since a Budget is also an account-level singleton per
   `network-security-baseline.md`'s guidance to keep account-level tooling
   undeletable by an app-stack teardown):
   ```yaml
   MonthlyBudget:
     Type: AWS::Budgets::Budget
     Properties:
       Budget:
         BudgetName: !Sub '${ProjectName}-monthly-${Environment}'
         BudgetType: COST
         TimeUnit: MONTHLY
         BudgetLimit:
           Amount: 25          # headroom above the current ~$17/mo estimate
           Unit: USD
         CostFilters:
           TagKeyValue:
             - !Sub 'user:Project$${ProjectName}'
       NotificationsWithSubscribers:
         - Notification:
             NotificationType: ACTUAL
             ComparisonOperator: GREATER_THAN
             Threshold: 50
           Subscribers:
             - SubscriptionType: SNS
               Address: !Ref AlarmTopic     # reuse existing topic once #1 is fixed
         - Notification:
             NotificationType: ACTUAL
             ComparisonOperator: GREATER_THAN
             Threshold: 80
           Subscribers:
             - SubscriptionType: SNS
               Address: !Ref AlarmTopic
         - Notification:
             NotificationType: ACTUAL
             ComparisonOperator: GREATER_THAN
             Threshold: 100
           Subscribers:
             - SubscriptionType: SNS
               Address: !Ref AlarmTopic
   ```
2. `cfn-lint` + `checkov` it like every other template, `aws cloudformation
   validate-template` before wiring into `master.yml`.
3. Deploy, then verify live: `aws budgets describe-budgets` should show the
   new budget; trigger a low-cost sanity check isn't practical, so just
   confirm the resource exists and the SNS subscriber (from finding #1) is
   confirmed.
4. Document the budget amount and threshold rationale in
   `docs/cost-optimization.md`.

---

## 3. [MEDIUM] No secret-scanning gate anywhere

**Finding:** No `.git/hooks/pre-commit` exists in this repo, and no
buildspec runs gitleaks or truffleHog. `secrets-hygiene.md` requires both —
"pre-commit secret scanning... wired into the pre-commit hook AND as a CI
pipeline stage — local hooks can be bypassed with `--no-verify`, so CI is
the real gate."

**Why it happened:** This project's CI/CD gates were added incrementally,
each in response to a concrete need — `cfn-lint`/`checkov` for IaC
correctness/security, `pip-audit` for dependency CVEs, `pytest` for
regressions. No secret has ever actually leaked in this repo's history to
force the issue, so the gate was never prioritized. It's an absence born of
"haven't needed it yet," not a considered trade-off.

**Action steps:**

1. Add `gitleaks` as a new Gate 0 in `pipeline/buildspec/validate-app.yml`
   and `validate-infra.yml` (cheapest possible check, run it first):
   ```yaml
   - echo "=== Gate 0 — Secret scan (gitleaks) ==="
   - curl -sSL https://github.com/gitleaks/gitleaks/releases/latest/download/gitleaks_linux_x64.tar.gz | tar -xz gitleaks
   - ./gitleaks detect --source . --no-git -v
   ```
   (Pin a specific gitleaks version rather than `latest` once you've picked
   one, per this project's version-pinning standard.)
2. Add a local pre-commit hook too (belt-and-suspenders, not the real gate):
   ```bash
   pipx install pre-commit
   # .pre-commit-config.yaml
   repos:
     - repo: https://github.com/gitleaks/gitleaks
       rev: v8.21.2
       hooks:
         - id: gitleaks
   ```
3. Add the new gate to `Makefile`'s `check` target and to
   `pipeline/scripts/validate-templates.sh` so local and CI stay identical,
   per `pre-push-validation.md`.
4. Run once against full git history (`gitleaks detect --source . -v`,
   without `--no-git`) as a one-time historical sweep before trusting the
   gate going forward — confirms nothing's already sitting in old commits.

---

## 4. [MEDIUM] No Python lint/type-check gates (ruff, mypy)

**Finding:** `make check` and the CI pipeline run `cfn-lint` + `checkov`
(IaC only) and `pip-audit` (CVEs) and `pytest` — nothing runs static
analysis against the Lambda's own business logic
(`backend/lambda/*.py`). This project's own `pre-push-validation.md` gate
order explicitly places `ruff format --check` + `ruff check` and `mypy` at
positions 2 and 3, before tests.

**Why it happened:** Same incremental-gate pattern as #3 — the Python
tooling that got added (`cfn-lint`, `checkov`, `pip-audit`) all targets the
CloudFormation/dependency-security side of the project, since that's where
this project's real production incidents happened (see
`docs/WeatherApp-ImproveDeploys-Plan-V2.md`'s five real deploy-attempt
bugs). General code-quality static analysis on the Lambda code itself was
never a pain point, so it was never added.

**Action steps:**

1. Add `pyproject.toml` at repo root with a `[tool.ruff]` section (line
   length, target-version = py311 to match the deployed Lambda runtime).
2. Add a new gate to `pipeline/buildspec/validate-app.yml`:
   ```yaml
   - pip install --quiet ruff mypy
   - echo "=== Gate X — ruff format check ==="
   - ruff format --check backend/lambda/
   - echo "=== Gate X — ruff lint ==="
   - ruff check backend/lambda/
   - echo "=== Gate X — mypy type check ==="
   - mypy backend/lambda/ --ignore-missing-imports
   ```
3. Run `ruff check backend/lambda/ --fix` and `ruff format backend/lambda/`
   locally first — expect some findings on first run; fix or explicitly
   `# noqa` with a reason before wiring the gate to block the pipeline (a
   day-one gate failure that blocks every future push is worse than not
   having the gate).
4. `mypy` will likely need type stubs for `boto3` (`pip install
   boto3-stubs[secretsmanager,dynamodb]`) — add to `requirements.txt` as a
   dev-only dependency.
5. Add both to `Makefile`'s `check` target, matching the global gate order.

---

## 5. [MEDIUM] No `npm audit` gate for `diagrams/interactive`

**Finding:** `pipeline/buildspec/deploy-app.yml:76` runs `npm ci && npm run
build` for the `diagrams/interactive` React/Vite app and ships its output —
but no buildspec anywhere runs `npm audit` against it. Dependency CVE
scanning in this pipeline covers Python (`pip-audit`) only.

**Why it happened:** `diagrams/interactive` is a secondary artifact (an
architecture-diagram viewer), added to the project later than the core
Lambda/CFN work. The CI gate structure was designed around the Python
Lambda as the primary deployable; when the Node build step got wired into
`deploy-app.yml` for packaging purposes, its own dependency-security gate
was never circled back to, unlike `pip-audit` which was part of the
original gate set from early on.

**Action steps:**

1. Add to `pipeline/buildspec/validate-app.yml` (or a dedicated gate if the
   Node install is too slow to bundle with the Python validate stage):
   ```yaml
   - echo "=== Gate X — npm dependency CVE scan ==="
   - cd diagrams/interactive && npm audit --audit-level=high --omit=dev && cd ../..
   ```
2. Add the same command to `Makefile`'s `check` target.
3. Run it once locally first to see current findings before wiring it as a
   blocking gate — same day-one-failure caution as #4.

---

## 6. [MEDIUM] README doc-index links the superseded API doc

**Finding:** `docs/api-documentation-v2.md` explicitly states `Supersedes:
docs/api-documentation.md (V1)`. But `README.md:227`'s own doc-index table
still links `docs/api-documentation.md` as "API Documentation." A reader
following the README's own index lands on the stale V1 doc describing the
pre-migration API (before the One Call API 3.0 / 7-day forecast change).

**Why it happened:** Classic same-fact-in-two-files drift. When
`api-documentation-v2.md` was created, the new file correctly declared its
own supersession relationship — but `README.md` is a separate file, and the
change that created V2 didn't touch it.

**Action steps:**

1. Update `README.md:227` to point at `docs/api-documentation-v2.md`.
2. Add a one-line pointer at the top of `docs/api-documentation.md` itself
   (`> Superseded by docs/api-documentation-v2.md`) so anyone who lands on
   it directly (a bookmark, a search hit, a stale link elsewhere) is
   redirected too — the same courtesy V2 already extends in reverse.
3. Grep the rest of the repo for any other `api-documentation.md` (non-v2)
   references before considering this closed:
   ```bash
   grep -rn "api-documentation\.md" --include="*.md" . | grep -v "api-documentation-v2\|Supersedes\|Superseded"
   ```

---

## 7. [MEDIUM] Buildspec naming breaks the project's own `-infra-`/`-app-` convention

**Finding:** `AppValidateDeploymentProject` (`08b-app-pipeline.yml:272,280`)
points at `pipeline/buildspec/validate-deployment.yml` — a filename with no
`-app-` disambiguator, unlike its siblings `validate-app.yml` /
`deploy-app.yml`. Its Infra-side counterpart is correctly named
`validate-infra-deployment.yml`.

**Why it happened:** This project already did a full naming-consistency
pass on 2026-07-14 (renaming CodeBuild project `Name` properties and
CloudWatch log group names to carry `-infra-`/`-app-` prefixes
consistently, triggered by the user noticing the asymmetry). That pass
covered CloudFormation *resource* names and CodeBuild *project* names —
buildspec *file* names are a different artifact category that fell outside
its scope, even though it's the exact same underlying convention gap.

**Action steps:**

1. Rename `pipeline/buildspec/validate-deployment.yml` →
   `pipeline/buildspec/validate-app-deployment.yml`.
2. Update the single reference at `08b-app-pipeline.yml:280`
   (`BuildSpec: pipeline/buildspec/validate-app-deployment.yml`).
3. Since `AppValidateDeploymentProject`'s `Name` property is itself likely
   already `-infra-`/`-app-` prefixed from the 2026-07-14 pass, this is a
   pure file rename + one-line reference update — no CFN replacement risk,
   unlike a `Name`-property rename (Lambda/CodeBuild project names are
   create-only, buildspec file paths are not).
4. `git mv` (preserves history) rather than delete+recreate.

---

## 8. [LOW] Stale comments reference pre-split buildspec filenames

**Finding:**
- `Makefile`'s `check` target comment: *"the two local scripts that already
  mirror `pipeline/buildspec/validate.yml` and `pipeline/buildspec/test.yml`"*
  — `validate.yml` hasn't existed since the pipeline split; it's now
  `validate-infra.yml` / `validate-app.yml`.
- `pipeline/scripts/validate-templates.sh`'s header: *"local mirror of
  `buildspec.yml` pre_build gates"* — the root `buildspec.yml` is itself
  explicitly marked `SUPERSEDED` in its own header comment.

**Why it happened:** These comments were written once, correctly, when the
pipeline was still a single `buildspec.yml`. When the pipeline was later
split into `pipeline/buildspec/*.yml`, nothing forces a comment to be
revisited — no linter checks prose accuracy, so it silently went stale
while the code around it changed.

**Action steps:**

1. `Makefile`: update the `check:` target's comment to name the actual
   current files (`validate-infra.yml`, `validate-app.yml`, `test.yml`,
   `deploy-infra.yml`, `deploy-app.yml`, `validate-infra-deployment.yml`,
   `validate-app-deployment.yml` after #7's rename).
2. `validate-templates.sh`: update the header comment to say it mirrors
   `pipeline/buildspec/validate-infra.yml` + `validate-app.yml`'s pre-build
   gates, not the superseded root `buildspec.yml`.
3. No functional risk either way — purely a documentation-accuracy fix.

---

## 9. [LOW] pip-audit gate label overstates what it actually filters

**Finding:** `pipeline/scripts/validate-templates.sh` prints `"Gate 4/4 —
Dependency CVE scan (pip-audit, HIGH/CRITICAL severity)"` but the command
itself (`pip-audit -r backend/lambda/requirements.txt`) has no severity
filter — and per this project's own `pre-push-validation.md`, pip-audit
doesn't support a `--severity` flag at all. The label implies low-severity
CVEs are being deliberately excluded; in reality every severity is scanned
and would fail the gate equally.

**Why it happened:** Most likely the intent ("only fail on the serious
stuff") was written into the echo text at the time the script was drafted,
before it was confirmed that pip-audit has no such flag to actually
implement that intent — a gap between what the script's text says and what
the tool can do that was documented elsewhere
(`pre-push-validation.md`) but never fed back into this script's own label.

**Action steps:**

1. Fix the label to match reality: `"Gate 4/4 — Dependency CVE scan
   (pip-audit, all severities)"`.
2. Add `--desc` per the project's own standard for descriptive output:
   ```bash
   pip-audit -r backend/lambda/requirements.txt --desc
   ```
3. Apply the same `--desc` addition to the CI-side gate in
   `pipeline/buildspec/validate-app.yml:21`.
4. If HIGH/CRITICAL-only filtering is actually wanted (not just assumed),
   that requires piping pip-audit's JSON output (`--format json`) through a
   small severity filter, since the tool itself won't do it — decide
   deliberately rather than let the label imply a behavior that isn't real.

---

## 10. [LOW] `ssm_secrets.py` contains zero SSM code — misleading name

**Finding:** `backend/lambda/ssm_secrets.py` calls
`boto3.client("secretsmanager")` and `get_secret_value` exclusively — no
`ssm` client, no Parameter Store call anywhere in the file.

**Why it happened:** The file was originally written when the OpenWeatherMap
API key genuinely lived in SSM Parameter Store. Phase 4.2 (documented in
`docs/WeatherApp-ImproveDeploys-Plan-V2.md`) migrated the credential's
*storage* to Secrets Manager, and the module's *content* was correctly
rewritten to match — but the *filename* itself was a rename that fell
outside that refactor's scope, so it's now a Secrets Manager module wearing
an SSM name.

**Action steps:**

1. `git mv backend/lambda/ssm_secrets.py backend/lambda/secrets_manager.py`
   (preserves history).
2. Update the two import sites (`weather_handler.py` and its test file in
   `backend/tests/`) to `from secrets_manager import get_api_key`.
3. Update `backend/lambda/requirements.txt` / any packaging script that
   references the old filename explicitly, if any (spot-check
   `pipeline/buildspec/deploy-app.yml` and `pipeline/scripts/*.sh`).
4. Run the local test suite (`make test`) to confirm nothing still imports
   the old module path.

---

## Suggested order of attack

1. **#1 and #2 first** — both are live production-safety/cost-safety gaps
   with a same-day fix (a CLI subscribe call, and one new CFN resource).
2. **#6 and #7** — trivial, low-risk, high embarrassment-avoidance value.
3. **#3, #4, #5** — new CI gates. Each needs a "run once locally, see what
   it finds, fix or consciously suppress before making it blocking" pass —
   budget real time for these, don't wire them straight to blocking on day
   one.
4. **#8, #9, #10** — pure hygiene, do opportunistically whenever those
   files are next touched for another reason.
