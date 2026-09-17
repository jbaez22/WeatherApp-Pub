#!/usr/bin/env bash
# deploy-canary.sh — trigger a CodeDeploy blue/green traffic shift for the
# weather-handler Lambda's "live" alias.
#
# Called from pipeline/buildspec/deploy-app.yml (App pipeline, Phase 2.2b), after
# `aws lambda update-function-code --publish` has already produced a new
# version. Replaces the direct `aws lambda update-alias` call used before
# Phase 2.2 — CodeDeploy now owns moving the alias, via a 10%/5-minute
# canary shift with automatic rollback on the lambda-errors/api-5xx alarms.
#
# Usage: TARGET_VERSION="$NEW_VERSION" bash scripts/deploy-canary.sh
#
# Required env vars (already set by the DeployProject CodeBuild environment
# — see infrastructure/cloudformation/08-pipeline.yml):
#   LAMBDA_FUNCTION_NAME
#
# Optional env var:
#   TARGET_VERSION — the exact version number to shift traffic to, straight
#   from the caller's own `update-function-code --publish` response. Always
#   pass this from a buildspec that just published a version; it is race-free
#   where re-deriving it here via list-versions-by-function is not (see the
#   comment at its fallback use below). Only omit for a standalone/manual run
#   where no fresher value is available.
#
# APP_NAME/DG_NAME below are NOT threaded in as env vars — they must match
# infrastructure/cloudformation/05-backend.yml's WeatherCodeDeployApp /
# WeatherDeploymentGroup resource names exactly. Update both places
# together if either ever changes.

set -euo pipefail

FUNCTION_NAME="${LAMBDA_FUNCTION_NAME:?LAMBDA_FUNCTION_NAME must be set}"
ALIAS_NAME="live"
APP_NAME="weather-dashboard-production"
DG_NAME="weather-dashboard-handler-dg-production"
PRE_HOOK_NAME="weather-dashboard-pre-traffic-hook-production"
POST_HOOK_NAME="weather-dashboard-post-traffic-hook-production"

# Every AWS CLI call below passes --region "$REGION" explicitly - never rely
# on ambient region env vars alone. CodeBuild always auto-injects its own
# AWS_REGION set to the *CodeBuild project's* home region (us-east-1, where
# every project in this pipeline actually runs), and that takes precedence
# over a custom AWS_DEFAULT_REGION in the CLI's own resolution order - even
# when AWS_DEFAULT_REGION is correctly set to us-west-2 by the secondary
# buildspec. Confirmed 2026-07-29: deploy-app-secondary.yml's call into this
# script silently operated against us-east-1 instead of us-west-2 because of
# exactly this precedence, and shifted PRIMARY's alias to an unrelated,
# months-old secondary-region version number - a real production outage.
# AWS_DEFAULT_REGION (explicit secondary override) wins if set; otherwise
# AWS_REGION (CodeBuild's own auto-injected value, correct for the primary
# project); us-east-1 is the last-resort fallback for a standalone/manual run.
REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-us-east-1}}"

echo "=== Fetching Lambda version info ==="
echo "  Region: $REGION"

CURRENT_VERSION=$(aws lambda get-alias \
  --function-name "$FUNCTION_NAME" \
  --name "$ALIAS_NAME" \
  --region "$REGION" \
  --query 'FunctionVersion' --output text)

# Prefer a TARGET_VERSION passed in by the caller — the buildspecs pass the
# exact version number returned by their own `update-function-code
# --publish` call, which is authoritative and race-free. Falling back to
# list-versions-by-function's last entry is a last resort for standalone/
# manual invocation only: that list read can lag a just-published version
# by several seconds (confirmed 2026-07-29 — a version published 4s earlier
# didn't yet appear via Versions[-1], so CURRENT_VERSION and TARGET_VERSION
# both resolved to the OLD version, producing an invalid CodeDeploy AppSpec
# with CurrentVersion == TargetVersion).
if [ -z "${TARGET_VERSION:-}" ]; then
  TARGET_VERSION=$(aws lambda list-versions-by-function \
    --function-name "$FUNCTION_NAME" \
    --region "$REGION" \
    --query 'Versions[-1].Version' --output text)
fi

echo "  Current version : $CURRENT_VERSION"
echo "  Target version  : $TARGET_VERSION"

if [ "$CURRENT_VERSION" == "$TARGET_VERSION" ]; then
  echo "No new version to deploy. Exiting."
  exit 0
fi

# shellcheck disable=SC2016 # intentional literal string, not expansion —
# matches the literal "$LATEST" text aws lambda get-alias returns.
if [ "$CURRENT_VERSION" == '$LATEST' ]; then
  # Bootstrap case — the alias has never been moved off $LATEST (either
  # this is the very first deploy since Phase 2.1 created the alias, or a
  # prior deploy attempt failed before ever moving it). CodeDeploy's
  # blue/green shift requires CurrentVersion in the AppSpec to be a real
  # published version number — found the hard way:
  # INVALID_LAMBDA_CONFIGURATION, "The Lambda function version in the
  # AppSpec file must be a valid positive integer." $LATEST is not a
  # valid integer, so the canary shift can never start from it. Move the
  # alias directly this one time; every deploy after this one will have a
  # real CURRENT_VERSION and use the normal canary path below.
  echo "Alias is on \$LATEST (first move since it was created) — CodeDeploy"
  echo "can't shift traffic FROM \$LATEST, only between real version numbers."
  echo "Moving the alias directly to $TARGET_VERSION this one time; canary"
  echo "shifts apply starting with the next deploy."
  aws lambda update-alias \
    --function-name "$FUNCTION_NAME" \
    --name "$ALIAS_NAME" \
    --function-version "$TARGET_VERSION" \
    --region "$REGION"
  echo "Alias now on version $TARGET_VERSION (bootstrap move, no canary shift)."
  exit 0
fi

echo "=== Building AppSpec (JSON, not YAML) ==="
# The pre/post-traffic hook Lambdas parse this with Python's stdlib `json`
# module (no PyYAML in the Lambda runtime) — see 05-backend.yml and
# appspec.yml's header comment for why this deliberately does not read/
# template the repo-root appspec.yml file.
REVISION_CONTENT=$(FUNCTION_NAME="$FUNCTION_NAME" ALIAS_NAME="$ALIAS_NAME" \
  CURRENT_VERSION="$CURRENT_VERSION" TARGET_VERSION="$TARGET_VERSION" \
  PRE_HOOK_NAME="$PRE_HOOK_NAME" POST_HOOK_NAME="$POST_HOOK_NAME" \
  python3 -c "
import json
import os

appspec = {
    'version': '0.0',
    'Resources': [
        {
            'WeatherHandlerFunction': {
                'Type': 'AWS::Lambda::Function',
                'Properties': {
                    'Name': os.environ['FUNCTION_NAME'],
                    'Alias': os.environ['ALIAS_NAME'],
                    'CurrentVersion': os.environ['CURRENT_VERSION'],
                    'TargetVersion': os.environ['TARGET_VERSION'],
                },
            },
        },
    ],
    'Hooks': [
        {'BeforeAllowTraffic': os.environ['PRE_HOOK_NAME']},
        {'AfterAllowTraffic': os.environ['POST_HOOK_NAME']},
    ],
}
print(json.dumps(appspec))
")

REVISION_JSON=$(python3 -c "
import json
import sys
content = sys.argv[1]
print(json.dumps({
    'revisionType': 'AppSpecContent',
    'appSpecContent': {'content': content},
}))
" "$REVISION_CONTENT")

echo "=== Creating CodeDeploy deployment ==="
DEPLOYMENT_ID=$(aws deploy create-deployment \
  --application-name "$APP_NAME" \
  --deployment-group-name "$DG_NAME" \
  --revision "$REVISION_JSON" \
  --region "$REGION" \
  --query 'deploymentId' --output text)

echo "  Deployment ID: $DEPLOYMENT_ID"
echo "  Canary phase: 10% traffic to Version $TARGET_VERSION for 5 minutes"
echo "  Watching CloudWatch alarms..."

echo "=== Waiting for deployment to complete ==="
aws deploy wait deployment-successful --deployment-id "$DEPLOYMENT_ID" --region "$REGION"

STATUS=$(aws deploy get-deployment \
  --deployment-id "$DEPLOYMENT_ID" \
  --region "$REGION" \
  --query 'deploymentInfo.status' --output text)

echo "=== Deployment complete — Status: $STATUS ==="

if [ "$STATUS" != "Succeeded" ]; then
  echo "Deployment failed or rolled back. Check the CodeDeploy console, or run:"
  echo "  bash scripts/rollback-canary.sh $DEPLOYMENT_ID $REGION"
  exit 1
fi
