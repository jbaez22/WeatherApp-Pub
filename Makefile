.PHONY: check validate test venv tools clean check-mobile

VENV := backend/.venv
PY := $(VENV)/bin/python3

# Test-only deps go in the venv. CLI tools (cfn-lint, checkov, pip-audit) are
# installed via pipx, not pip — macOS system Python rejects `pip install` for
# these (VersionConflict against the Xcode Command Line Tools Python). See
# `tools` target below. Run `make tools` once per machine, `make venv` per repo.
venv:
	python3 -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r backend/lambda/requirements.txt
	$(PY) -m pip install --quiet pytest pytest-cov moto requests-mock

# One-time per machine, not per repo — safe to re-run, pipx no-ops if current.
tools:
	pipx install cfn-lint || pipx upgrade cfn-lint
	pipx install checkov || pipx upgrade checkov
	pipx install pip-audit || pipx upgrade pip-audit

validate:
	bash pipeline/scripts/validate-templates.sh

test:
	bash pipeline/scripts/run-tests.sh

# check == exactly the two local scripts that already mirror
# pipeline/buildspec/validate.yml and pipeline/buildspec/test.yml.
# This is the full CI gate set for this project today: cfn-lint, checkov,
# cfn syntax check, pip-audit, pytest+80% coverage. Run before every push.
check: validate test
	@echo "All local CI gates passed (cfn-lint, checkov, cfn-syntax, pip-audit, pytest+coverage)."

clean:
	rm -rf $(VENV) coverage.xml coverage_html .pytest_cache backend/lambda/__pycache__ backend/tests/__pycache__

# Tier 2 of the mobile-testing strategy (see docs/tools/mobile-check/) —
# not part of `check` by default: it needs npm + Playwright's browser
# binaries, a heavier one-time setup than the rest of this gate. Run this
# yourself before pushing any change that touches frontend/ HTML/CSS/JS.
check-mobile:
	cd docs/tools/mobile-check && npm install --silent && npx playwright install chromium
	bash docs/tools/mobile-check/run-check.sh
