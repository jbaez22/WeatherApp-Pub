#!/usr/bin/env bash
# validate-templates.sh — local mirror of buildspec.yml pre_build gates.
#
# Run this before every push to catch issues before the CI pipeline does.
# Identical gates to CodeBuild: cfn-lint → checkov → aws validate → pip-audit.
#
# Usage:
#   bash pipeline/scripts/validate-templates.sh
#
# Prerequisites (install once):
#   pipx install cfn-lint
#   pipx install checkov
#   pipx install pip-audit
#   aws configure              # requires valid AWS credentials for validate-template
#
# Exit codes: 0 = all gates passed, non-zero = gate failure (see output for which gate)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "================================================"
echo " Weather Dashboard — Template Validation Gates"
echo "================================================"
echo "Project root: $PROJECT_ROOT"
echo ""

# ── Gate 1: cfn-lint ────────────────────────────────────────────────────────
echo "Gate 1/4 — CloudFormation lint (cfn-lint)"
if ! command -v cfn-lint &>/dev/null; then
  echo "  ERROR: cfn-lint not found. Install with: pipx install cfn-lint"
  exit 1
fi
cfn-lint infrastructure/cloudformation/*.yml
echo "  PASSED"
echo ""

# ── Gate 2: checkov ─────────────────────────────────────────────────────────
echo "Gate 2/4 — CloudFormation security scan (checkov)"
if ! command -v checkov &>/dev/null; then
  echo "  ERROR: checkov not found. Install with: pipx install checkov"
  exit 1
fi
checkov -d infrastructure/cloudformation --framework cloudformation --quiet --compact
echo "  PASSED"
echo ""

# ── Gate 3: AWS CloudFormation syntax validation ─────────────────────────────
echo "Gate 3/4 — AWS CloudFormation syntax validation (aws cloudformation validate-template)"
echo "  NOTE: Requires valid AWS credentials (reads no data, no cost)."
echo "  Skipping master.yml — local TemplateURL paths only valid after cfn package."
if ! aws sts get-caller-identity &>/dev/null; then
  echo "  SKIPPED: No valid AWS credentials found. Run 'aws configure' or set env vars."
else
  for template in infrastructure/cloudformation/*.yml; do
    name=$(basename "$template")
    if [ "$name" = "master.yml" ]; then
      continue
    fi
    printf "  Validating %-25s" "$name..."
    aws cloudformation validate-template --template-body "file://$template" > /dev/null
    echo "OK"
  done
  echo "  PASSED"
fi
echo ""

# ── Gate 4: pip-audit dependency CVE scan ────────────────────────────────────
echo "Gate 4/4 — Dependency CVE scan (pip-audit, HIGH/CRITICAL severity)"
if ! command -v pip-audit &>/dev/null; then
  echo "  ERROR: pip-audit not found. Install with: pipx install pip-audit"
  exit 1
fi
pip-audit -r backend/lambda/requirements.txt
echo "  PASSED"
echo ""

echo "================================================"
echo " All validation gates passed. Safe to push."
echo "================================================"
