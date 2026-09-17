#!/usr/bin/env bash
# invalidate-cloudfront.sh — create a CloudFront cache invalidation for /*
#
# Called by buildspec.yml post_build after the S3 frontend sync. Clears all
# cached objects so users immediately see the newly deployed frontend.
#
# Required environment variable (injected by CodeBuild project):
#   CLOUDFRONT_DISTRIBUTION_ID — CloudFront distribution to invalidate
#
# Can also be run locally for manual cache busting:
#   export CLOUDFRONT_DISTRIBUTION_ID=E1ABCDEFGHIJKL
#   bash pipeline/scripts/invalidate-cloudfront.sh

set -euo pipefail

: "${CLOUDFRONT_DISTRIBUTION_ID:?ERROR: CLOUDFRONT_DISTRIBUTION_ID is not set}"

# Use the CodeBuild build ID as the caller reference (unique per build).
# Falls back to a timestamp when run locally.
CALLER_REF="${CODEBUILD_BUILD_ID:-manual-$(date +%Y%m%d%H%M%S)}"

echo "=== CloudFront Invalidation ==="
echo "Distribution: $CLOUDFRONT_DISTRIBUTION_ID"
echo "Paths       : /*"
echo ""

INVALIDATION_ID=$(
  aws cloudfront create-invalidation \
    --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
    --invalidation-batch "Paths={Quantity=1,Items=[/*]},CallerReference=${CALLER_REF}" \
    --query 'Invalidation.Id' \
    --output text
)

echo "Invalidation created: $INVALIDATION_ID"
echo "Edge caches will clear within ~30 seconds."
echo "Track status with:"
echo "  aws cloudfront get-invalidation --distribution-id $CLOUDFRONT_DISTRIBUTION_ID --id $INVALIDATION_ID"
