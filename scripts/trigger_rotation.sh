#!/bin/bash

# Script to manually trigger secrets rotation for testing
# Usage: ./scripts/trigger_rotation.sh [environment]

set -e

ENVIRONMENT=${1:-dev}
FUNCTION_NAME="${ENVIRONMENT}-secrets-rotator"

echo "Triggering secrets rotation for environment: $ENVIRONMENT"
echo "Function name: $FUNCTION_NAME"

# Invoke Lambda function
aws lambda invoke \
    --function-name "$FUNCTION_NAME" \
    --payload '{"source": "manual-trigger"}' \
    --cli-binary-format raw-in-base64-out \
    /tmp/rotation-response.json

echo ""
echo "Response:"
cat /tmp/rotation-response.json | jq '.'

echo ""
echo "Rotation triggered successfully!"
echo "Check CloudWatch Logs for details: /aws/lambda/$FUNCTION_NAME"
