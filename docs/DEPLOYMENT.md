# Cloud Sync Application - Deployment Guide

## Overview

This guide provides step-by-step instructions for deploying the Cloud Sync Application to AWS. Follow the checklist carefully to ensure a successful deployment.

## Prerequisites

### Required Tools
- [ ] AWS CLI v2 installed and configured
- [ ] AWS CDK v2 installed (`npm install -g aws-cdk`)
- [ ] Python 3.12 installed
- [ ] Node.js 18+ installed
- [ ] jq installed (for JSON parsing in scripts)
- [ ] Git installed

### AWS Account Setup
- [ ] AWS account with appropriate permissions
- [ ] AWS credentials configured (`aws configure`)
- [ ] CDK bootstrapped in target region (`cdk bootstrap`)

### GoPro Setup
- [ ] GoPro Developer account created
- [ ] OAuth 2.0 application registered
- [ ] Client ID and Client Secret obtained
- [ ] Initial OAuth flow completed (refresh token obtained)

## Pre-Deployment Checklist

### Phase 1: Infrastructure Foundation
- [ ] DynamoDB table will be created (gopro-sync-tracker)
- [ ] S3 bucket will be created with lifecycle policy
- [ ] KMS key will be created for encryption
- [ ] VPC infrastructure (optional, based on config)

### Phase 2: Security
- [ ] GoPro credentials stored in Secrets Manager
  ```bash
  aws secretsmanager create-secret \
    --name gopro/credentials \
    --secret-string '{
      "provider": "gopro",
      "refresh_token": "YOUR_REFRESH_TOKEN",
      "access_token": "YOUR_ACCESS_TOKEN",
      "user_id": "YOUR_USER_ID",
      "token_timestamp": "2025-11-12T00:00:00Z"
    }'
  ```
- [ ] IAM roles will be created automatically by CDK

### Phase 3: Lambda Functions
- [ ] Lambda Layer with shared utilities will be deployed
- [ ] Media Authenticator Lambda will be deployed
- [ ] Media Lister Lambda will be deployed
- [ ] Video Downloader Lambda will be deployed

### Phase 4: Orchestration
- [ ] Step Functions state machine will be created
- [ ] EventBridge scheduler will be configured
- [ ] CloudWatch log groups will be created

## Deployment Steps

### 1. Clone and Setup

```bash
# Clone repository
git clone <repository-url>
cd cloud-sync-application

# Install Python dependencies
pip install -r requirements.txt

# Install CDK dependencies
npm install
```

### 2. Configure Environment

```bash
# Set environment variables
export AWS_REGION=us-east-1
export ENVIRONMENT=dev

# Review configuration
cat cloud_sync/config.py
```

### 3. Review Changes

```bash
# Synthesize CloudFormation template
cdk synth CloudSyncStack-dev

# Review differences (if updating existing stack)
cdk diff CloudSyncStack-dev
```

### 4. Deploy Stack

```bash
# Deploy with approval prompts
cdk deploy CloudSyncStack-dev

# Or deploy without prompts (use with caution)
cdk deploy CloudSyncStack-dev --require-approval never
```

### 5. Verify Deployment

```bash
# Get stack outputs
aws cloudformation describe-stacks \
  --stack-name CloudSyncStack-dev \
  --query 'Stacks[0].Outputs' \
  --output table

# Verify state machine exists
aws stepfunctions list-state-machines \
  --query 'stateMachines[?name==`gopro-sync-orchestrator`]'

# Verify EventBridge rule
aws events describe-rule \
  --name gopro-sync-daily-schedule

# Check Lambda functions
aws lambda list-functions \
  --query 'Functions[?starts_with(FunctionName, `media-`) || starts_with(FunctionName, `video-`)]'
```

## Post-Deployment Validation

### 1. Manual Execution Test

```bash
# Make script executable
chmod +x scripts/trigger_sync.sh

# Trigger manual execution
./scripts/trigger_sync.sh dev

# Or use AWS CLI directly
STATE_MACHINE_ARN=$(aws cloudformation describe-stacks \
  --stack-name CloudSyncStack-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue' \
  --output text)

aws stepfunctions start-execution \
  --state-machine-arn "$STATE_MACHINE_ARN" \
  --input '{"provider": "gopro", "manual_trigger": true}'
```

### 2. Verify Components

#### Authentication
- [ ] Media Authenticator Lambda invoked successfully
- [ ] Secrets Manager accessed without errors
- [ ] Auth token retrieved and validated
- [ ] CloudWatch logs show successful authentication

#### Media Listing
- [ ] Media Lister Lambda invoked successfully
- [ ] GoPro API queried successfully
- [ ] DynamoDB queried for sync status
- [ ] New videos identified correctly

#### Video Download
- [ ] Video Downloader Lambda invoked successfully
- [ ] Videos streamed from GoPro to S3
- [ ] Multipart upload used for large files
- [ ] DynamoDB updated with sync status
- [ ] CloudWatch metrics published

#### Orchestration
- [ ] State machine execution completed
- [ ] All states transitioned correctly
- [ ] Error handling worked as expected
- [ ] X-Ray traces captured

### 3. Check Resources

```bash
# DynamoDB table
aws dynamodb describe-table \
  --table-name gopro-sync-tracker

# S3 bucket
aws s3 ls s3://$(aws cloudformation describe-stacks \
  --stack-name CloudSyncStack-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`S3BucketName`].OutputValue' \
  --output text)

# CloudWatch log groups
aws logs describe-log-groups \
  --log-group-name-prefix /aws/lambda/media-

aws logs describe-log-groups \
  --log-group-name-prefix /aws/states/gopro-sync
```

### 4. Verify Scheduled Execution

```bash
# Check EventBridge rule status
aws events describe-rule \
  --name gopro-sync-daily-schedule \
  --query '{Name: Name, State: State, ScheduleExpression: ScheduleExpression}'

# List recent executions
aws stepfunctions list-executions \
  --state-machine-arn "$STATE_MACHINE_ARN" \
  --max-results 5
```

## Monitoring Setup (Phase 5)

After deployment, set up monitoring:

- [ ] Review CloudWatch Logs for errors
- [ ] Check X-Ray service map
- [ ] Verify metrics are being published
- [ ] Test SNS notifications (when configured)
- [ ] Create CloudWatch dashboard
- [ ] Configure CloudWatch alarms

## Secrets Rotation Setup (Phase 6)

The secrets rotation system is automatically deployed and configured:

- [ ] Verify secrets rotator Lambda function is deployed
- [ ] Check EventBridge rule is enabled (monthly schedule)
- [ ] Review rotation CloudWatch alarms
- [ ] Test manual rotation:
  ```bash
  ./scripts/trigger_rotation.sh dev
  ```
- [ ] Verify rotation metrics in CloudWatch dashboard
- [ ] Configure SNS email subscription for rotation alerts

For detailed information, see [SECRETS_ROTATION.md](SECRETS_ROTATION.md)

## Troubleshooting

### Common Issues

#### 1. CDK Bootstrap Error
```bash
# Bootstrap CDK in your account/region
cdk bootstrap aws://ACCOUNT-ID/REGION
```

#### 2. Secrets Manager Access Denied
```bash
# Verify secret exists
aws secretsmanager describe-secret --secret-id gopro/credentials

# Check IAM permissions
aws iam get-role-policy \
  --role-name CloudSyncStack-dev-LambdasMediaAuthenticatorRole* \
  --policy-name *
```

#### 3. Lambda Timeout
- Check Lambda memory allocation (1024 MB for downloader)
- Review CloudWatch logs for specific errors
- Verify network connectivity (VPC configuration if enabled)

#### 4. State Machine Execution Failed
```bash
# Get execution details
aws stepfunctions describe-execution \
  --execution-arn <EXECUTION_ARN>

# View execution history
aws stepfunctions get-execution-history \
  --execution-arn <EXECUTION_ARN> \
  --max-results 100
```

#### 5. GoPro API Authentication Failed
- Verify refresh token is valid
- Check token expiration
- Manually refresh token if needed
- Update Secrets Manager with new token

## Rollback Procedure

If issues arise after deployment:

### 1. Disable Scheduled Execution
```bash
# Disable EventBridge rule
aws events disable-rule --name gopro-sync-daily-schedule
```

### 2. Stop Running Executions
```bash
# List running executions
aws stepfunctions list-executions \
  --state-machine-arn "$STATE_MACHINE_ARN" \
  --status-filter RUNNING

# Stop execution if needed
aws stepfunctions stop-execution \
  --execution-arn <EXECUTION_ARN>
```

### 3. Rollback Stack
```bash
# Checkout previous version
git checkout <previous-commit>

# Redeploy
cdk deploy CloudSyncStack-dev
```

### 4. Investigate and Fix
- Review CloudWatch logs
- Check X-Ray traces
- Verify configuration
- Test fixes in dev environment

### 5. Redeploy
```bash
# After fixes
git checkout main
cdk deploy CloudSyncStack-dev

# Re-enable EventBridge rule
aws events enable-rule --name gopro-sync-daily-schedule
```

## Environment-Specific Deployments

### Development
```bash
cdk deploy CloudSyncStack-dev
```

### Staging
```bash
cdk deploy CloudSyncStack-staging
```

### Production
```bash
# Extra caution for production
cdk diff CloudSyncStack-prod
cdk deploy CloudSyncStack-prod --require-approval broadening
```

## Cost Monitoring

After deployment, monitor costs:

```bash
# Get cost estimate
aws ce get-cost-and-usage \
  --time-period Start=2025-11-01,End=2025-11-30 \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --filter file://cost-filter.json

# cost-filter.json
{
  "Tags": {
    "Key": "Project",
    "Values": ["CloudSync"]
  }
}
```

Expected monthly costs:
- Storage (100GB): $20-50
- Lambda executions: $150
- Step Functions: $0.75
- Other services: $1
- **Total: ~$171/month**

## Security Checklist

- [ ] All S3 buckets have public access blocked
- [ ] KMS encryption enabled for S3 and DynamoDB
- [ ] IAM roles follow least privilege principle
- [ ] Secrets Manager used for credentials
- [ ] VPC endpoints configured (if using VPC)
- [ ] CloudTrail logging enabled
- [ ] X-Ray tracing enabled

## Compliance Checklist

- [ ] Data encryption at rest and in transit
- [ ] Access logging enabled
- [ ] Audit trail maintained
- [ ] Backup and recovery tested
- [ ] Disaster recovery plan documented

## Next Steps

After successful deployment:

1. **Phase 5: Monitoring and Alerting**
   - Create SNS topic for alerts
   - Configure CloudWatch alarms
   - Set up CloudWatch dashboard
   - Test notification delivery

2. **Operational Readiness**
   - Document runbooks
   - Train operations team
   - Set up on-call rotation
   - Create incident response procedures

3. **Optimization**
   - Monitor performance metrics
   - Optimize Lambda memory allocation
   - Tune retry configurations
   - Review and optimize costs

## Support

For issues or questions:
- Check CloudWatch Logs
- Review X-Ray traces
- Consult documentation in `docs/`
- Contact development team

## Appendix

### Useful Commands

```bash
# List all stacks
aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE

# Get stack resources
aws cloudformation list-stack-resources --stack-name CloudSyncStack-dev

# View CloudWatch logs
aws logs tail /aws/lambda/media-authenticator --follow

# Export state machine definition
aws stepfunctions describe-state-machine \
  --state-machine-arn "$STATE_MACHINE_ARN" \
  --query 'definition' \
  --output text | jq '.'

# Test Lambda function directly
aws lambda invoke \
  --function-name media-authenticator \
  --payload '{"provider": "gopro", "action": "authenticate"}' \
  response.json
```

### Stack Outputs Reference

| Output Key | Description | Usage |
|------------|-------------|-------|
| StateMachineArn | State machine ARN | Manual execution, monitoring |
| StateMachineConsoleUrl | Console URL | Quick access to state machine |
| EventBridgeRuleName | Scheduler rule name | Enable/disable scheduling |
| DynamoDBTableName | Sync tracker table | Query sync status |
| S3BucketName | Archive bucket | Access stored videos |

---

**Document Version:** 1.0  
**Last Updated:** November 12, 2025  
**Phase:** 4 - Workflow Orchestration
