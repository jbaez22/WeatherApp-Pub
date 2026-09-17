# Local Testing Guide

Test every change locally before pushing to CodeCommit. A push triggers the full pipeline (Validate → Test → Deploy), which takes several minutes. Catching issues locally avoids wasted pipeline runs and the feedback-cycle wait.

---

## Why the frontend and the backend are tested differently

This project has a **split architecture**. The frontend (HTML/CSS/JS) runs in your browser, but it always calls a backend that lives entirely on AWS:

```
Your laptop                          AWS Cloud
─────────────────────────────────    ──────────────────────────────────────
python3 -m http.server 8080          API Gateway → Lambda → DynamoDB
  serves index.html, app.js,           (weather_client.py runs here,
  styles.css to your browser            SSM key lives here, cache lives here)
         │
         │  browser fetch() call
         └────────────────────────────► https://{api-id}.execute-api.us-east-1.amazonaws.com
```

**There is no local backend.** `weather_client.py` only runs inside Lambda on AWS. So "testing locally" in this project covers two distinct scopes:

| What you are testing | How | Requires AWS push? |
|---|---|---|
| Frontend rendering — layout, CSS, JS logic, CSP compliance | `python3 -m http.server 8080` | No — current weather card renders against the live API |
| Full integration — 7-day forecast, hourly strip, V2 data contract | Browser at `localhost:8080` after pipeline completes | **Yes** — the Lambda must serve the V2 response shape |

**Practical rule:** verify the page loads and the current weather card renders before pushing. Verify the full 7-day + hourly flow after the pipeline deploys.

> **Why not open `index.html` directly?** Opening `file://` bypasses Content Security Policy enforcement and hides CORS errors — the exact issues that will surface in production. Always use the HTTP server.

---

## Prerequisites (one-time setup)

### Python test dependencies

Never use the macOS system pip. Use a virtual environment.

**Step 1 — Check if the virtual environment already exists:**

```bash
[ -d .venv ] \
  && echo "FOUND:   .venv -- skip: python3 -m venv .venv" \
  || echo "MISSING: .venv -- run:  python3 -m venv .venv"
```

Create it only if shown as `MISSING`:

```bash
python3 -m venv .venv
```

**Step 2 — Activate the venv** (required every session, whether new or existing):

```bash
source .venv/bin/activate
```

**Step 3 — Check which test packages are already installed:**

```bash
for pkg in pytest pytest-cov moto requests-mock; do
  pip show "$pkg" &>/dev/null \
    && echo "INSTALLED: $pkg $(pip show $pkg | grep ^Version | cut -d' ' -f2)" \
    || echo "MISSING:   $pkg"
done
```

Install only the packages shown as `MISSING`:

```bash
# Run only if any packages above showed MISSING
pip install pytest pytest-cov moto requests-mock
```

**Step 4 — Lambda runtime dependencies:**

`pip install` is idempotent — it prints `Requirement already satisfied` for packages already at the correct version and only fetches what is missing. Safe to run regardless:

```bash
pip install -r backend/lambda/requirements.txt
```

### IaC validation tools

**Check what is already installed before running anything:**

```bash
# Check pipx (required to install the tools below)
command -v pipx &>/dev/null \
  && echo "INSTALLED: pipx $(pipx --version)" \
  || echo "MISSING:   pipx -- install with: brew install pipx"

# Check the three validation tools
for tool in cfn-lint checkov pip-audit; do
  command -v "$tool" &>/dev/null \
    && echo "INSTALLED: $tool $($tool --version 2>&1 | head -1)" \
    || echo "MISSING:   $tool"
done
```

Only run the `pipx install` line for each tool shown as `MISSING`. Skip any tool already showing `INSTALLED` — reinstalling is harmless but unnecessary.

```bash
# Run only the lines for tools shown as MISSING above
pipx install cfn-lint
pipx install checkov
pipx install pip-audit
```

**`pipx ensurepath`** adds `~/.local/bin` to your shell `PATH` so the tools are found without a full path. It is safe to run even if already configured — it is idempotent and will report "All pipx binary directories are already in PATH" if no change is needed:

```bash
pipx ensurepath   # restart terminal afterwards if it reports any changes
```

AWS credentials must be configured for the CloudFormation syntax gate (Gate 3):

```bash
aws configure
```

---

## 1. Frontend — Serve Locally

```bash
cd frontend
python3 -m http.server 8080
```

Open `http://localhost:8080` in your browser.

### What to verify — before pushing

These checks do **not** require the V2 backend to be deployed:

- [ ] Home page (`/`) loads without a blank screen or console errors
- [ ] Current weather card renders — city name, temperature, feels like, humidity, wind, pressure, visibility
- [ ] C°/F° toggle updates the current weather card
- [ ] About page (`/about/`) loads with all styles applied
- [ ] Navigation between Home and About works in both directions
- [ ] No errors in the browser console (`Cmd+Option+J` on Mac)
- [ ] No CSP violations in the console (look for `Content-Security-Policy` errors)

> If the live API still serves V1 data (before the V2 Lambda is deployed), the current weather card renders but the **7-Day Forecast section stays hidden** — this is expected and correct behaviour.

### What to verify — after the pipeline deploys

These checks require the V2 Lambda to be live:

- [ ] 7 forecast cards appear, each showing day name, icon, high/low temp, precip %, and UV index
- [ ] Clicking a day card expands it with a horizontal hourly strip — time, icon, temp, precip %
- [ ] Clicking another day collapses the first and opens the new one
- [ ] Clicking the same open card collapses it
- [ ] Toggling C/F while a card is open updates the hourly temps and keeps the card expanded
- [ ] Tabbing to a forecast card and pressing `Enter` or `Space` expands/collapses it
- [ ] On a narrow window (mobile size), the hourly strip scrolls horizontally

### Hard-refresh after every change

The browser caches CSS and JS aggressively. After each edit:

```
Cmd + Shift + R   (Mac)
Ctrl + Shift + R  (Windows / Linux)
```

Stop the server with `Ctrl+C` when done.

---

## 2. Backend — Unit Tests

Run the full test suite with coverage reporting:

```bash
# From the project root (with .venv activated)
bash pipeline/scripts/run-tests.sh
```

This mirrors Gate 5 in `buildspec.yml` exactly — same pytest command, same 80% coverage threshold.

### Expected output

```
================================================
 Weather Dashboard — Unit Tests + Coverage
================================================
...
92 passed in 1.09s
...
TOTAL    185      0   100%
================================================
 All tests passed. Coverage >= 80%.
 Coverage report: coverage.xml
================================================
```

A non-zero exit code means a test failed or coverage dropped below 80% — fix it before pushing.

---

## 3. CloudFormation — Template Validation

Run all four IaC gates (only needed when CloudFormation files change):

```bash
bash pipeline/scripts/validate-templates.sh
```

| Gate | Tool | What it checks |
|------|------|----------------|
| 1 | `cfn-lint` | CloudFormation syntax, property names, resource types |
| 2 | `checkov` | Security misconfigurations |
| 3 | `aws cloudformation validate-template` | AWS-side template parsing (requires credentials) |
| 4 | `pip-audit` | Python dependency CVEs (HIGH/CRITICAL) |

Gate 3 is automatically skipped if no valid AWS credentials are found — the other three still run.

### Expected output

```
================================================
 Weather Dashboard — Template Validation Gates
================================================
Gate 1/4 — CloudFormation lint (cfn-lint)          PASSED
Gate 2/4 — CloudFormation security scan (checkov)  PASSED
Gate 3/4 — AWS CloudFormation syntax validation     PASSED
Gate 4/4 — Dependency CVE scan (pip-audit)         PASSED
================================================
 All validation gates passed. Safe to push.
================================================
```

---

## Pre-Push Checklist

Run these in order before every `git push`:

```bash
# 1. Activate venv
source .venv/bin/activate

# 2. Unit tests (always)
bash pipeline/scripts/run-tests.sh

# 3. IaC validation (only if CloudFormation files changed)
bash pipeline/scripts/validate-templates.sh

# 4. Frontend smoke test (if frontend files changed)
cd frontend && python3 -m http.server 8080
# — verify current weather card renders, no console errors, then Ctrl+C —
cd ..

# 5. Push when all gates pass
git push aws main
```

> **Skip rules:**
> - Backend-only Lambda changes with no frontend changes → skip step 4
> - Frontend-only changes with no Python or IaC files changed → skip steps 2 and 3
> - After pushing, run the full integration smoke test (Section 1, post-deploy checklist) once the pipeline completes

---

## What Each Check Covers

| Scope | Local check | What CI does if you skip |
|-------|-------------|--------------------------|
| Frontend rendering | `python3 -m http.server 8080` | Pipeline succeeds but production looks broken |
| CSP violations | Browser console at `localhost:8080` | Styles/scripts silently blocked in production |
| Unit tests | `run-tests.sh` | Pipeline fails at Gate 5, blocks deploy |
| CloudFormation lint | `validate-templates.sh` Gate 1 | Pipeline fails at Gate 1, blocks everything |
| IaC security | `validate-templates.sh` Gate 2 | Pipeline fails at Gate 2, blocks everything |
| Dependency CVEs | `validate-templates.sh` Gate 4 | Pipeline fails at Gate 4, blocks deploy |

---

## Troubleshooting

**Current weather card shows but forecast section is hidden**
The Lambda is still running V1 code. Push your changes (`git push aws main`) and wait for the pipeline to deploy. The forecast section appears automatically once the V2 Lambda is live.

**"Unable to reach the weather service" on page load**
Check the browser console for the actual error. Likely causes: (1) a JavaScript syntax error earlier in app.js preventing the module from loading — check the console for `SyntaxError`; (2) a real network failure (VPN, DNS, or AWS outage) — try `curl` on the API URL directly; (3) a mismatch between the live API response shape and what the frontend expects.

**`python3 -m http.server` shows a directory listing instead of the app**
Make sure you ran `cd frontend` first. The server must be started from inside the `frontend/` directory.

**CSP error in browser console**
Inline `<script>` or `<style>` tags are blocked by policy. Move the code to an external `.js` or `.css` file. See `docs/security.md` for the CSP header definition.

**`import` / module errors in the browser console**
Ensure `<script>` tags that load ES modules use `type="module"`. Opening via `file://` instead of `localhost:8080` also breaks module imports.

**`pytest: command not found`**
The venv is not activated. Run `source .venv/bin/activate` first.

**`cfn-lint: command not found`**
Run `pipx ensurepath` and restart your terminal, or use the full path `~/.local/bin/cfn-lint`.

**Gate 3 fails with `ExpiredTokenException`**
Your AWS credentials have expired. Re-run `aws configure` or refresh your SSO session.

**Coverage dropped below 80%**
New code was added without tests. Add unit tests for the new module before pushing. Run `python3 -m pytest backend/tests/ --cov=backend/lambda --cov-report=html` to get an HTML report at `htmlcov/index.html`.
