#!/usr/bin/env bash
# deploy-infrastructure.sh — deploy the master CloudFormation stack.
#
# Called by buildspec.yml build phase after `aws cloudformation package`
# has run. Uses `aws cloudformation deploy` which handles both initial
# stack creation and updates, and exits 0 when there are no changes.
#
# Required environment variables (injected by CodeBuild project):
#   ARTIFACTS_BUCKET     — S3 bucket containing the packaged master template
#   CFN_DEPLOY_ROLE_ARN  — IAM role CloudFormation assumes during the update
#   ENVIRONMENT          — production | staging
#
# Can also be run locally for manual deployments (set the env vars first):
#   export ARTIFACTS_BUCKET=weather-dashboard-artifacts-123456789-production
#   export CFN_DEPLOY_ROLE_ARN=arn:aws:iam::123456789:role/weather-dashboard-cfn-deploy-role-production
#   export ENVIRONMENT=production
#   bash pipeline/scripts/deploy-infrastructure.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# ── Validate required env vars ────────────────────────────────────────────────
: "${ARTIFACTS_BUCKET:?ERROR: ARTIFACTS_BUCKET is not set}"
: "${CFN_DEPLOY_ROLE_ARN:?ERROR: CFN_DEPLOY_ROLE_ARN is not set}"
: "${ENVIRONMENT:?ERROR: ENVIRONMENT is not set}"

STACK_NAME="weather-dashboard-master-${ENVIRONMENT}"
PACKAGED_TEMPLATE="infrastructure/cloudformation/master-packaged.yml"

if [ ! -f "$PACKAGED_TEMPLATE" ]; then
  echo "ERROR: $PACKAGED_TEMPLATE not found."
  echo "Run 'aws cloudformation package' first (or let buildspec.yml do it)."
  exit 1
fi

echo "================================================"
echo " CloudFormation Stack Deploy"
echo "================================================"
echo "Stack   : $STACK_NAME"
echo "Template: $PACKAGED_TEMPLATE"
echo "Env     : $ENVIRONMENT"
echo ""

# ── Deploy stack ──────────────────────────────────────────────────────────────
# --no-fail-on-empty-changeset: exits 0 when there are no infrastructure changes
# (avoids false pipeline failures on code-only deploys).
#
# On first deploy, ALL required parameters (AcmCertificateArn, etc.) must be
# set. For updates, CloudFormation uses the previously stored parameter values
# for any parameter not listed in --parameter-overrides.
#
# Bootstrap (first deploy only) — run manually with full parameter overrides:
#   aws cloudformation deploy \
#     --template-file infrastructure/cloudformation/master-packaged.yml \
#     --stack-name weather-dashboard-master-production \
#     --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
#     --role-arn "$CFN_DEPLOY_ROLE_ARN" \
#     --parameter-overrides \
#       AcmCertificateArn=arn:aws:acm:us-east-1:ACCOUNT:certificate/CERT-ID \
#       HostedZoneId=ZXXXXXXXXXXXXX \
#       Environment=production \
#     --no-fail-on-empty-changeset
#
# Subsequent pipeline runs (no parameter-overrides needed — CFN keeps previous values):
aws cloudformation deploy \
  --template-file "$PACKAGED_TEMPLATE" \
  --stack-name "$STACK_NAME" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --role-arn "$CFN_DEPLOY_ROLE_ARN" \
  --no-fail-on-empty-changeset

echo ""
echo "Stack '$STACK_NAME' is up to date."
