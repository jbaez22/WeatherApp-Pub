#!/usr/bin/env bash
# rollback-canary.sh — manually stop an in-progress CodeDeploy traffic
# shift and roll the alias back to its previous version.
#
# Usage: bash scripts/rollback-canary.sh <deployment-id> [region]
#
# Automatic rollback already happens if the lambda-errors or api-5xx
# alarms fire during the shift (see WeatherDeploymentGroup's
# AutoRollbackConfiguration in 05-backend.yml) — this script is for the
# case where something looks wrong but the alarms haven't (yet) tripped.
#
# A deployment ID alone does not indicate which region it belongs to, so
# region is resolved the same explicit way as deploy-canary.sh: an optional
# second positional arg wins, then AWS_DEFAULT_REGION, then AWS_REGION, then
# us-east-1 (the pipeline's home region) as a last resort. Never rely on
# ambient CLI/CodeBuild region defaults alone here - see deploy-canary.sh's
# header comment for the 2026-07-29 production incident this caused.

set -euo pipefail

DEPLOYMENT_ID=${1:?Usage: $0 <deployment-id> [region]}
REGION="${2:-${AWS_DEFAULT_REGION:-${AWS_REGION:-us-east-1}}}"

echo "=== Stopping deployment $DEPLOYMENT_ID and rolling back (region: $REGION) ==="
aws deploy stop-deployment \
  --deployment-id "$DEPLOYMENT_ID" \
  --auto-rollback-enabled \
  --region "$REGION"

echo "Rollback initiated. Monitor with:"
echo "  aws deploy get-deployment --deployment-id $DEPLOYMENT_ID --region $REGION --query 'deploymentInfo.status'"
