#!/usr/bin/env bash
# run-tests.sh — run the full unit test suite with coverage reporting.
#
# Identical to the pytest command in buildspec.yml pre_build Gate 5.
# Run this locally before pushing to catch test failures before CI does.
#
# Usage:
#   bash pipeline/scripts/run-tests.sh
#
# Prerequisites (install once):
#   pip install pytest pytest-cov moto requests-mock
#   pip install -r backend/lambda/requirements.txt
#
# Exit codes: 0 = all tests passed and coverage >= 80%, non-zero = failure

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

COVERAGE_THRESHOLD="${COVERAGE_THRESHOLD:-80}"

# Prefer backend/.venv (created by `make venv`) if it exists — macOS's
# Homebrew Python is PEP 668 externally-managed and blocks plain
# `pip install`, so local runs need an isolated venv. CI is unaffected:
# CodeBuild has no backend/.venv and always installs fresh from
# requirements.txt into its own container, so this falls back to plain
# `python3` there exactly as before.
PYTHON="python3"
if [ -x "$PROJECT_ROOT/backend/.venv/bin/python3" ]; then
  PYTHON="$PROJECT_ROOT/backend/.venv/bin/python3"
fi

echo "================================================"
echo " Weather Dashboard — Unit Tests + Coverage"
echo "================================================"
echo "Project root  : $PROJECT_ROOT"
echo "Test path     : backend/tests/"
echo "Coverage src  : backend/lambda/"
echo "Threshold     : ${COVERAGE_THRESHOLD}% (matches CI)"
echo ""

# Verify test dependencies are available
for pkg in pytest pytest_cov moto requests_mock; do
  "$PYTHON" -c "import $pkg" 2>/dev/null || {
    echo "ERROR: '$pkg' is not installed."
    echo "Install with: make venv (or pip install pytest pytest-cov moto requests-mock)"
    exit 1
  }
done

# Run tests — output is identical to what CodeBuild produces
echo "Using interpreter: $PYTHON"
"$PYTHON" -m pytest backend/tests/ -v \
  --cov=backend/lambda \
  --cov-report=term-missing \
  --cov-report=xml:coverage.xml \
  --cov-fail-under="$COVERAGE_THRESHOLD"

echo ""
echo "================================================"
echo " All tests passed. Coverage >= ${COVERAGE_THRESHOLD}%."
echo " Coverage report: coverage.xml"
echo "================================================"
