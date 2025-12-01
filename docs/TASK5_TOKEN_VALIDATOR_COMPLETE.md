# Task 5 Complete: Token Validator Lambda

## Overview

Task 5 implements the Token Validator Lambda function that validates GoPro Cloud authentication cookies before sync operations begin. This ensures the system fails fast if cookies are expired, rather than wasting resources attempting downloads that will fail.

## Implementation Summary

### Files Created

1. **lambda_functions/token_validator/handler.py**
   - Main Lambda function handler
   - Cookie validation logic with minimal/full fallback strategy
   - SNS alerting for expired cookies
   - CloudWatch metrics publishing
   - X-Ray tracing integration

2. **lambda_functions/token_validator/__init__.py**
   - Package initialization file

3. **lambda_functions/token_validator/requirements.txt**
   - Dependencies: requests, boto3, aws-xray-sdk

4. **tests/unit/test_token_validator.py**
   - Comprehensive unit tests (29 tests, all passing)
   - Tests for minimal/full cookie fallback
   - Tests for expiration detection (401/403)
   - Tests for error handling and alerting

### Files Modified

1. **cloud_sync/lambda_construct.py**
   - Added `_create_token_validator()` method
   - Configured IAM role with least-privilege permissions
   - Set memory to 256 MB, timeout to 30 seconds
   - Enabled X-Ray tracing and CloudWatch logging

2. **lambda_layer/python/cloud_sync_common/exceptions.py**
   - Added `TokenExpiredError` exception class
   - Inherits from `AuthenticationError`

## Key Features

### 1. Smart Cookie Validation Strategy

Implements the fallback approach from `COOKIE_TESTING_STRATEGY.md`:

**Phase 1: Minimal Cookies (Preferred)**
```python
Cookie: gp_access_token=...; gp_user_id=...
```
- Faster (less data)
- Cleaner approach
- Sufficient for most cases

**Phase 2: Full Cookies (Fallback)**
```python
Cookie: gp_access_token=...; gp_user_id=...; session=...; sessionId=...; ...
```
- Falls back if minimal fails
- Uses entire cookie header from Secrets Manager
- Maximum compatibility

### 2. Cookie Age Tracking

```python
def calculate_cookie_age(credentials: Dict[str, Any]) -> float:
    """Calculate age of cookies in days."""
```

- Tracks days since last cookie update
- Publishes `CookieAgeDays` metric to CloudWatch
- Helps predict when manual refresh will be needed

### 3. Expiration Detection

Detects expired cookies via HTTP status codes:
- **401 Unauthorized**: Cookies expired
- **403 Forbidden**: Cookies invalid
- **200 OK**: Cookies valid

### 4. SNS Alerting

When cookies expire, publishes detailed alert:
```json
{
  "alert_type": "TOKEN_EXPIRATION",
  "severity": "HIGH",
  "message": "Cookies are expired or invalid...",
  "correlation_id": "...",
  "action_required": "Manual cookie refresh required",
  "documentation": "See docs/TOKEN_EXTRACTION_GUIDE.md"
}
```

Subject line: `🔴 GoPro Sync: Cookies Expired - Manual Refresh Required`

### 5. CloudWatch Metrics

Publishes to namespace `CloudSync/TokenValidation`:
- `ValidationSuccess` (Count): Successful validations
- `ValidationFailure` (Count): Failed validations
- `ValidationDuration` (Seconds): Validation latency
- `CookieAgeDays` (None): Age of cookies in days

### 6. Comprehensive Error Handling

- **Missing secret**: Returns 500 with helpful error
- **Expired cookies**: Returns 401, publishes alert
- **API timeout**: Returns 500, logs error
- **Network error**: Returns 500, logs error
- **Unexpected status**: Returns 500 with HTTP code

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                  Token Validator Flow                            │
│                                                                   │
│  EventBridge/StepFunctions                                       │
│         │                                                         │
│         ▼                                                         │
│  ┌──────────────────────────────────────┐                       │
│  │  Lambda: Token Validator              │                       │
│  │  ┌────────────────────────────────┐  │                       │
│  │  │ 1. Retrieve credentials        │  │                       │
│  │  │ 2. Calculate cookie age        │  │                       │
│  │  │ 3. Try minimal cookies         │  │                       │
│  │  │    └─> HTTP 200? ✓            │  │                       │
│  │  │    └─> Failed? Try full       │  │                       │
│  │  │ 4. Publish metrics             │  │                       │
│  │  │ 5. Alert if expired (401/403)  │  │                       │
│  │  └────────────────────────────────┘  │                       │
│  └──────────────────────────────────────┘                       │
│         │           │              │                              │
│         ▼           ▼              ▼                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐                    │
│  │ Secrets  │ │CloudWatch│ │ SNS Topic    │                    │
│  │ Manager  │ │ Metrics  │ │ (Alerts)     │                    │
│  └──────────┘ └──────────┘ └──────────────┘                    │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
```

## IAM Permissions

The Token Validator Lambda has **read-only** Secrets Manager access:

```python
# Secrets Manager (read-only)
- secretsmanager:GetSecretValue  # Read cookies

# CloudWatch
- logs:CreateLogGroup
- logs:CreateLogStream
- logs:PutLogEvents
- cloudwatch:PutMetricData  # Only CloudSync/TokenValidation namespace

# SNS
- sns:Publish  # Alert on expiration

# X-Ray
- xray:PutTraceSegments
- xray:PutTelemetryRecords

# VPC (if enabled)
- ec2:CreateNetworkInterface
- ec2:DescribeNetworkInterfaces
- ec2:DeleteNetworkInterface
```

## Test Coverage

**29 tests, all passing:**

1. **Handler Tests (6 tests)**
   - Success with minimal cookies
   - Success with full cookie fallback
   - Token expiration detection
   - Missing secret handling
   - API timeout handling

2. **Cookie Age Tests (3 tests)**
   - Recent cookies
   - Old cookies
   - Missing timestamp

3. **Cookie Extraction Tests (7 tests)**
   - Extract existing cookie
   - Extract non-existent cookie
   - Extract first/last cookie
   - Handle spaces
   - Extract gp_access_token
   - Extract gp_user_id

4. **Validation Tests (6 tests)**
   - Minimal cookies success
   - Full cookies fallback
   - 401/403 expiration detection
   - Unexpected HTTP codes
   - Missing cookies

5. **API Call Tests (3 tests)**
   - Successful API call
   - Timeout handling
   - Connection error handling

6. **Credentials Retrieval Tests (2 tests)**
   - Successful retrieval
   - Secret not found

7. **Alert Publishing Tests (3 tests)**
   - Successful alert
   - No topic configured
   - SNS error handling

## Integration with Existing System

### Used by Step Functions
The Token Validator will be the first state in the Step Functions workflow:

```
ValidateTokens (Token Validator Lambda)
    ↓ (if valid)
ListMedia (Media Lister Lambda)
    ↓
DownloadVideos (Video Downloader Lambda)
```

### Shares Infrastructure
- **Lambda Layer**: Uses shared utilities (logging, metrics, correlation)
- **Secrets Manager**: Reads from same `gopro/credentials` secret
- **SNS Topic**: Publishes to existing `gopro-sync-alerts` topic
- **VPC**: Optionally deploys in same VPC as other functions
- **CloudWatch**: Uses consistent logging and metrics patterns

## Response Format

### Success Response
```json
{
  "statusCode": 200,
  "valid": true,
  "cookie_age_days": 5.2,
  "validation_method": "minimal_cookies",
  "duration_seconds": 0.234,
  "correlation_id": "abc123..."
}
```

### Expired Response
```json
{
  "statusCode": 401,
  "valid": false,
  "error": "TokenExpiredError",
  "message": "Cookies are expired or invalid. Manual refresh required...",
  "correlation_id": "abc123..."
}
```

### Error Response
```json
{
  "statusCode": 500,
  "valid": false,
  "error": "AuthenticationError",
  "message": "Failed to retrieve credentials...",
  "correlation_id": "abc123..."
}
```

## Usage Examples

### Invoke Directly (Testing)
```bash
aws lambda invoke \
  --function-name token-validator \
  --payload '{}' \
  /tmp/response.json

cat /tmp/response.json | jq '.'
```

### Invoke from Step Functions
```json
{
  "Comment": "Validate tokens before sync",
  "StartAt": "ValidateTokens",
  "States": {
    "ValidateTokens": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:...:function:token-validator",
      "Next": "CheckValidation",
      "ResultPath": "$.validation"
    },
    "CheckValidation": {
      "Type": "Choice",
      "Choices": [{
        "Variable": "$.validation.valid",
        "BooleanEquals": true,
        "Next": "ListMedia"
      }],
      "Default": "NotifyExpiration"
    }
  }
}
```

## Monitoring

### CloudWatch Logs
```bash
# View recent logs
aws logs tail /aws/lambda/token-validator --follow

# Search for validation failures
aws logs filter-log-events \
  --log-group-name /aws/lambda/token-validator \
  --filter-pattern "ValidationFailure"
```

### CloudWatch Metrics
```bash
# Check validation success rate
aws cloudwatch get-metric-statistics \
  --namespace CloudSync/TokenValidation \
  --metric-name ValidationSuccess \
  --start-time 2025-12-01T00:00:00Z \
  --end-time 2025-12-01T23:59:59Z \
  --period 3600 \
  --statistics Sum

# Monitor cookie age
aws cloudwatch get-metric-statistics \
  --namespace CloudSync/TokenValidation \
  --metric-name CookieAgeDays \
  --start-time 2025-12-01T00:00:00Z \
  --end-time 2025-12-01T23:59:59Z \
  --period 3600 \
  --statistics Average
```

### X-Ray Traces
View distributed traces showing:
- Secrets Manager retrieval latency
- API test call duration
- Overall validation latency
- Error traces

## Design Decisions

### 1. Read-Only Secrets Access
Unlike media_authenticator (which has write access for OAuth refresh), Token Validator only reads secrets. It cannot modify credentials, making it safer and following least-privilege principles.

### 2. Minimal Cookies First
Try minimal cookies before full header to:
- Reduce network overhead
- Identify minimum requirements
- Log which approach works (for future optimization)

### 3. Cookie Age Tracking
Track age to predict when manual refresh will be needed:
- Alert after 60 days (proactive)
- Alert on expiration (reactive)
- Historical data helps understand token lifespan

### 4. Fail Fast
Validation happens before listing/downloading to avoid wasted:
- API calls
- Step Functions state transitions
- Lambda invocations
- Time and money

### 5. Correlation IDs
Every validation includes correlation ID for:
- Tracking across services
- Debugging failed syncs
- X-Ray distributed tracing

## Known Limitations

1. **Manual Cookie Refresh Required**: When cookies expire, manual browser extraction is needed
2. **Unknown Expiration Time**: Cookie lifespan varies (typically 1-4 weeks)
3. **API Structure Changes**: If GoPro changes API, validation may fail
4. **No Proactive Refresh**: Cannot automatically refresh cookies (unofficial API limitation)

## Future Enhancements

1. **Proactive Expiration Alerts**: Alert 7 days before expected expiry (based on historical data)
2. **Community API Monitoring**: Check community resources for API changes
3. **Automated Cookie Collection**: Headless browser automation (if complexity is warranted)
4. **Cookie Lifespan Learning**: Build model to predict expiration based on usage patterns

## Compliance with Requirements

This implementation satisfies these requirements from the requirements document:

- **2.1**: Secure credential storage (Secrets Manager with encryption)
- **2.2**: Token validation with test API call
- **2.3**: Expiration detection via 401/403 responses
- **2.4**: SNS alerts when tokens expire
- **2.5**: CloudWatch metrics for token health
- **7.6**: X-Ray tracing for distributed tracing
- **11.5**: Token expiration monitoring
- **11.6**: Alert mechanisms for manual refresh

## Cost Analysis

### Monthly Costs (Daily Validation + Sync Executions)

| Component | Usage | Cost |
|-----------|-------|------|
| Lambda invocations | ~60/month (daily + manual tests) | $0.00 |
| Lambda duration | 30s × 256MB × 60 | $0.00 |
| CloudWatch metrics | 4 custom metrics | $1.20 |
| SNS notifications | ~1-2/month (only on expiration) | $0.00 |
| CloudWatch Logs | 30 day retention | $0.01 |
| **Total** | | **~$1.21/month** |

Very cost-effective for the reliability it provides.

## Testing Results

```
✅ 29 tests passed
⚠️ 84 deprecation warnings (datetime.utcnow())
📊 Test coverage: Token validator functions well-tested
```

### Test Breakdown
- ✅ Handler success cases (minimal + full fallback)
- ✅ Token expiration detection (401, 403)
- ✅ Error handling (missing secret, timeouts, network errors)
- ✅ Cookie extraction logic (all edge cases)
- ✅ Cookie age calculation
- ✅ API call handling
- ✅ SNS alerting

## Next Steps

With Token Validator complete, you can now proceed to:

1. **Task 6**: Implement Media Lister Lambda
   - Lists new videos from GoPro Cloud
   - Filters already-synced videos via DynamoDB
   - Returns list of videos to download

2. **Task 8**: Implement Step Functions State Machine
   - Orchestrates ValidateTokens → ListMedia → DownloadVideos
   - Handles errors and retries
   - Generates summary and alerts

3. **Task 10**: Implement CloudWatch Monitoring
   - Dashboard with validation metrics
   - Alarms for token expiration
   - Alarms for high failure rates

## Validation Checklist

- [x] Lambda function created with correct configuration
- [x] IAM role configured with least-privilege permissions
- [x] Unit tests implemented and passing (29/29)
- [x] Cookie fallback strategy implemented
- [x] Expiration detection working (401/403)
- [x] SNS alerting configured
- [x] CloudWatch metrics publishing
- [x] X-Ray tracing enabled
- [x] Error handling comprehensive
- [x] CDK construct updated
- [x] TokenExpiredError exception added
- [x] Documentation complete

## Documentation References

- **Token Extraction Guide**: `docs/TOKEN_EXTRACTION_GUIDE.md`
- **Cookie Testing Strategy**: `docs/COOKIE_TESTING_STRATEGY.md`
- **Task 3.3 Quick Start**: `docs/TASK_3.3_QUICK_START.md`
- **Deployment Guide**: `docs/DEPLOYMENT.md`

---

**Task 5 Status: Complete ✅**

**Next Task**: Task 6 - Media Lister Lambda
