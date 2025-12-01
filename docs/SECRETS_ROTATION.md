# Secrets Rotation

This document describes the automatic secrets rotation feature for the Cloud Sync Application.

## Overview

The secrets rotation system automatically refreshes cloud provider credentials stored in AWS Secrets Manager on a monthly schedule. This ensures credentials remain valid and reduces the risk of authentication failures due to expired tokens.

## Architecture

### Components

1. **Secrets Rotator Lambda Function**
   - Runtime: Python 3.12
   - Memory: 256 MB
   - Timeout: 60 seconds
   - Triggers: EventBridge monthly schedule

2. **EventBridge Schedule Rule**
   - Schedule: 1st of every month at 2 AM UTC
   - Cron expression: `0 2 1 * ? *`
   - Note: Uses UTC to avoid DST complications

3. **CloudWatch Monitoring**
   - Metrics: RotationSuccess, RotationFailure, RotationDuration
   - Alarms: Rotation failure detection
   - Dashboard: Rotation status widget

4. **SNS Notifications**
   - Success notifications
   - Failure alerts with error details

## Rotation Process

The rotation process follows these steps:

1. **Retrieve Current Credentials**
   - Fetch credentials from Secrets Manager
   - Extract refresh token and other metadata

2. **Refresh Token**
   - Use OAuth 2.0 refresh token flow
   - Obtain new access token from provider API
   - Update token timestamp

3. **Test New Credentials**
   - Make test API call to verify credentials work
   - List 1 media item to validate authentication

4. **Store Updated Credentials**
   - Update Secrets Manager with new tokens
   - Increment rotation counter
   - Record last rotation timestamp

5. **Publish Metrics and Notifications**
   - Send success/failure metrics to CloudWatch
   - Send notification to SNS topic
   - Log rotation details

## Manual Rotation

To manually trigger rotation for testing or emergency purposes:

```bash
# Trigger rotation for dev environment
./scripts/trigger_rotation.sh dev

# Trigger rotation for prod environment
./scripts/trigger_rotation.sh prod
```

Alternatively, use AWS CLI directly:

```bash
aws lambda invoke \
    --function-name dev-secrets-rotator \
    --payload '{"source": "manual-trigger"}' \
    --cli-binary-format raw-in-base64-out \
    /tmp/rotation-response.json
```

## Monitoring

### CloudWatch Metrics

The rotation function publishes the following metrics to the `CloudSync/SecretsRotation` namespace:

- **RotationSuccess**: Count of successful rotations
- **RotationFailure**: Count of failed rotations
- **RotationDuration**: Time taken to complete rotation (seconds)

All metrics include the dimension `Provider: gopro`.

### CloudWatch Alarms

- **Secrets Rotation Failure**: Triggers when rotation fails
  - Threshold: 1 failure in 1 hour
  - Action: SNS notification to ops team

### CloudWatch Dashboard

The main operations dashboard includes a "Secrets Rotation Status" widget showing:
- Successful rotations (last 24 hours)
- Failed rotations (last 24 hours)
- Average rotation duration

### CloudWatch Logs

Rotation logs are available in:
```
/aws/lambda/{environment}-secrets-rotator
```

Log retention: 30 days

## Troubleshooting

### Rotation Failure

If rotation fails, check the following:

1. **CloudWatch Logs**
   ```bash
   aws logs tail /aws/lambda/dev-secrets-rotator --follow
   ```

2. **Verify Refresh Token**
   - Ensure refresh token in Secrets Manager is valid
   - Check if manual re-authentication is needed

3. **Check API Connectivity**
   - Verify Lambda can reach provider API
   - Check VPC configuration if enabled
   - Verify security group rules

4. **Review IAM Permissions**
   - Secrets Manager read/write permissions
   - SNS publish permissions
   - CloudWatch metrics permissions

### Manual Re-authentication

If the refresh token expires or becomes invalid:

1. Perform manual OAuth flow to obtain new refresh token
2. Update Secrets Manager secret:
   ```bash
   aws secretsmanager update-secret \
       --secret-id gopro/credentials \
       --secret-string '{
           "refresh_token": "NEW_REFRESH_TOKEN",
           "access_token": "",
           "user_id": "USER_ID",
           "token_timestamp": "",
           "last_rotated": ""
       }'
   ```

3. Trigger manual rotation to verify:
   ```bash
   ./scripts/trigger_rotation.sh dev
   ```

### Common Errors

#### "Invalid credentials or expired refresh token"
- **Cause**: Refresh token has expired or been revoked
- **Solution**: Perform manual re-authentication (see above)

#### "Failed to retrieve credentials"
- **Cause**: Secrets Manager secret not found or inaccessible
- **Solution**: Verify secret exists and Lambda has permissions

#### "Credential test failed"
- **Cause**: New token doesn't work with provider API
- **Solution**: Check provider API status, verify OAuth configuration

## Security Considerations

1. **Least Privilege**: Lambda function has minimal IAM permissions
2. **Encryption**: Secrets Manager encrypts credentials at rest
3. **Audit Trail**: All rotation events logged to CloudWatch
4. **Notifications**: Ops team alerted on failures
5. **Testing**: New credentials validated before storage

## Resilience Features

1. **Dead Letter Queue**: Failed invocations captured in DLQ for manual review
2. **Automatic Rollback**: Storage failures trigger rollback to previous credentials
3. **Retry Logic**: Exponential backoff retry (3 attempts) for transient failures
4. **Credential Backup**: Previous credentials backed up before rotation
5. **DLQ Monitoring**: CloudWatch alarm triggers when messages appear in DLQ

## Configuration

### Environment Variables

The Secrets Rotator Lambda uses these environment variables:

- `SECRET_NAME`: Name of secret in Secrets Manager (default: `gopro/credentials`)
- `SNS_TOPIC_ARN`: ARN of SNS topic for notifications
- `PROVIDER_NAME`: Cloud provider name (default: `gopro`)
- `GOPRO_CLIENT_ID`: OAuth client ID (from environment or Parameter Store)
- `GOPRO_CLIENT_SECRET`: OAuth client secret (from environment or Parameter Store)

### Rotation Schedule

To modify the rotation schedule, update the EventBridge rule in `cloud_sync/secrets_rotation_construct.py`:

```python
rotation_rule = events.Rule(
    self,
    'RotationScheduleRule',
    schedule=events.Schedule.cron(
        minute='0',
        hour='2',  # 2 AM UTC (adjust for local time if needed)
        day='1',   # 1st of month
        month='*',
        year='*'
    ),
)
```

**Note on Timezones**: The schedule uses UTC to avoid complications with daylight saving time (DST). If you need a specific local time:
- Calculate the UTC offset for your timezone
- Be aware that DST changes will shift the local time
- Consider using EventBridge Scheduler (instead of EventBridge Rules) for timezone-aware scheduling

## Cost Estimation

Monthly costs for secrets rotation:

- Lambda invocations: 1 per month × $0.20 per 1M = ~$0.00
- Lambda duration: 60s × 256MB × $0.0000166667 = ~$0.00
- Secrets Manager: Included in existing secret cost
- CloudWatch metrics: 3 metrics × $0.30 = ~$0.90
- SNS notifications: 1 per month × $0.50 per 1M = ~$0.00

**Total: ~$0.90/month**

## Future Enhancements

Potential improvements for the rotation system:

1. **Multi-Provider Support**: Extend to rotate credentials for multiple providers
2. **Rotation History**: Store rotation history in DynamoDB
3. **Gradual Rollout**: Test new credentials before fully committing
4. **Automatic Remediation**: Auto-retry with exponential backoff
5. **Rotation Metrics Dashboard**: Dedicated dashboard for rotation analytics
