#!/bin/bash
# Script to manually trigger GoPro sync
# Usage: ./scripts/trigger_sync.sh [environment]

set -e

# Configuration
ENVIRONMENT="${1:-dev}"
REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="CloudSyncStack-${ENVIRONMENT}"

echo "========================================="
echo "GoPro Sync Manual Trigger"
echo "========================================="
echo "Environment: $ENVIRONMENT"
echo "Region: $REGION"
echo "Stack: $STACK_NAME"
echo ""

# Get state machine ARN from CloudFormation outputs
echo "Retrieving state machine ARN..."
STATE_MACHINE_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query 'Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue' \
  --output text \
  --region "$REGION" 2>/dev/null)

if [ -z "$STATE_MACHINE_ARN" ] || [ "$STATE_MACHINE_ARN" == "None" ]; then
  echo "Error: State machine ARN not found"
  echo "Make sure the stack '$STACK_NAME' is deployed in region '$REGION'"
  exit 1
fi

echo "State Machine: $STATE_MACHINE_ARN"
echo ""

# Start execution
echo "Starting execution..."
EXECUTION_ARN=$(aws stepfunctions start-execution \
  --state-machine-arn "$STATE_MACHINE_ARN" \
  --input '{"provider": "gopro", "manual_trigger": true}' \
  --region "$REGION" \
  --query 'executionArn' \
  --output text)

if [ -z "$EXECUTION_ARN" ]; then
  echo "Error: Failed to start execution"
  exit 1
fi

echo "✓ Execution started successfully!"
echo ""
echo "Execution ARN: $EXECUTION_ARN"
echo ""
echo "Console URL:"
echo "https://${REGION}.console.aws.amazon.com/states/home?region=${REGION}#/executions/details/${EXECUTION_ARN}"
echo ""

# Ask if user wants to wait for completion
read -p "Wait for execution to complete? (y/n) " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
  echo "Waiting for execution to complete..."
  echo "(This may take several minutes depending on the number of videos)"
  echo ""
  
  while true; do
    STATUS=$(aws stepfunctions describe-execution \
      --execution-arn "$EXECUTION_ARN" \
      --region "$REGION" \
      --query 'status' \
      --output text)
    
    if [ "$STATUS" == "SUCCEEDED" ]; then
      echo "✓ Execution completed successfully!"
      
      # Get execution output
      OUTPUT=$(aws stepfunctions describe-execution \
        --execution-arn "$EXECUTION_ARN" \
        --region "$REGION" \
        --query 'output' \
        --output text)
      
      echo ""
      echo "Execution Output:"
      echo "$OUTPUT" | jq '.' 2>/dev/null || echo "$OUTPUT"
      break
    elif [ "$STATUS" == "FAILED" ]; then
      echo "✗ Execution failed!"
      
      # Get error details
      ERROR=$(aws stepfunctions describe-execution \
        --execution-arn "$EXECUTION_ARN" \
        --region "$REGION" \
        --query '{error: error, cause: cause}' \
        --output json)
      
      echo ""
      echo "Error Details:"
      echo "$ERROR" | jq '.' 2>/dev/null || echo "$ERROR"
      exit 1
    elif [ "$STATUS" == "TIMED_OUT" ]; then
      echo "✗ Execution timed out!"
      exit 1
    elif [ "$STATUS" == "ABORTED" ]; then
      echo "✗ Execution was aborted!"
      exit 1
    else
      echo -n "."
      sleep 5
    fi
  done
else
  echo "Execution is running in the background."
  echo "Check the console URL above to monitor progress."
fi

echo ""
echo "========================================="
