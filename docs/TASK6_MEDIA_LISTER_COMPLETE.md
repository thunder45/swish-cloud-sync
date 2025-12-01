# Task 6 Complete: Media Lister Lambda

## Overview

Task 6 implements the Media Lister Lambda function that discovers new GoPro camera videos/photos to sync. It queries the GoPro Cloud API, filters for actual GoPro camera content (GH*/GO* files), and cross-references with DynamoDB to identify media that needs downloading.

## Implementation Summary

### Files Created/Modified

1. **lambda_functions/media_lister/handler.py** (Complete rewrite)
   - Cookie-based authentication via Secrets Manager
   - GoProProvider integration for API calls
   - DynamoDB batch queries for sync status filtering
   - API structure validation
   - CloudWatch metrics publishing
   - SNS alerting for API changes

2. **tests/unit/test_media_lister.py** (New)
   - 24 comprehensive unit tests, all passing
   - Handler success scenarios
   - Filtering logic tests
   - DynamoDB batch query tests
   - API validation tests
   - Error handling tests

3. **cloud_sync/lambda_construct.py** (Modified)
   - Added Secrets Manager read permissions
   - Added SNS publish permissions
   - Added CloudWatch Metrics permissions
   - Updated environment variables

## Key Features Implemented

### 1. Cookie-Based Authentication

Retrieves cookies from Secrets Manager instead of using OAuth:

```python
credentials = retrieve_credentials()
cookies = credentials.get('cookies')
user_agent = credentials.get('user-agent')

videos = provider.list_media(
    cookies=cookies,
    user_agent=user_agent,
    max_results=1000
)
```

### 2. Smart GoPro Camera Filtering

Only includes actual GoPro camera content:

```python
# Include: GH*.* and GO*.* (GoPro camera naming)
# Exclude: PXL_* (Pixel phone uploads)
# Exclude: Items with no filename
# Exclude: MultiClipEdit automatic edits
```

**Why this matters:**
- User's library has 971 items total
- Mix of GoPro camera files, Pixel phone uploads, edits
- Only want actual GoPro camera content synced
- Reduces unnecessary downloads and storage costs

### 3. DynamoDB Filtering

Queries DynamoDB to skip already-synced content:

```python
# Batch query (handles 100+ items per batch)
sync_statuses = batch_get_sync_status(table, media_ids)

# Filter logic:
# Include if: no record OR status != COMPLETED
# Exclude if: status == COMPLETED
```

**Handles non-completed statuses:**
- IN_PROGRESS → Include (may have failed mid-download)
- FAILED → Include (retry)
- PENDING → Include (not started)
- No record → Include (new video)

### 4. Pagination with Retry Logic

Handles large libraries (971+ items):

```python
# DynamoDB batch limit: 100 items
for i in range(0, len(media_ids), 100):
    batch = media_ids[i:i + 100]
    
    # Exponential backoff for throttling
    for attempt in range(max_retries + 1):
        response = dynamodb.batch_get_item(...)
        
        # Handle unprocessed keys
        if unprocessed_keys:
            time.sleep(retry_delay)
            retry_delay *= 2  # Exponential backoff
```

### 5. API Structure Validation

Validates expected fields to detect API changes:

```python
required_fields = ['media_id', 'filename', 'download_url', 'file_size']

if missing_fields:
    # Alert via SNS
    publish_api_structure_alert(...)
    # Publish metric
    metrics: APIStructureChangeDetected
```

**Why this matters:**
- Unofficial API may change without notice
- Early detection prevents silent failures
- Includes response sample in alert (500 char limit)

### 6. CloudWatch Metrics

Publishes to namespace `CloudSync/MediaListing`:

```python
- MediaListedFromProvider (Count): Total items from API
- NewVideosFound (Count): Items needing sync
- ListingDuration (Seconds): Operation latency
- ListingSuccess/Failure (Count): Success rate
- APIStructureChangeDetected (Count): API changes
```

## Test Coverage

**24 tests, all passing:**

1. **Handler Tests (6 tests)**
   - All new videos scenario
   - Some already synced scenario
   - All already synced scenario
   - IN_PROGRESS/FAILED videos included
   - Missing secret handling
   - Provider error handling

2. **Filtering Tests (5 tests)**
   - All new
   - Some synced
   - All synced
   - Non-COMPLETED statuses included
   - Empty list handling

3. **Batch Query Tests (3 tests)**
   - Single batch (<100 items)
   - Multiple batches (>100 items)
   - Retry logic with unprocessed keys
   - Error handling

4. **Validation Tests (3 tests)**
   - Complete metadata
   - Missing required fields
   - Multiple missing fields

5. **Helper Function Tests (7 tests)**
   - Credential retrieval
   - API structure alerts
   - Long response truncation

## Response Format

### Success Response
```json
{
  "statusCode": 200,
  "provider": "gopro",
  "new_videos": [
    {
      "media_id": "abc123",
      "filename": "GH010001.MP4",
      "download_url": "https://api.gopro.com/media/abc123/download?t=...",
      "file_size": 1073741824,
      "upload_date": "2025-12-01T00:00:00Z",
      "duration": 120,
      "media_type": "video",
      "resolution": "4K"
    }
  ],
  "total_found": 10,
  "new_count": 3,
  "already_synced": 7,
  "duration_seconds": 2.5,
  "correlation_id": "abc123..."
}
```

### Error Response
```json
{
  "statusCode": 500,
  "error": "APIError",
  "message": "Failed to list media...",
  "correlation_id": "abc123..."
}
```

## Integration with System

### Used by Step Functions
```
ValidateTokens
    ↓ (if valid)
→ ListMedia ← THIS FUNCTION
    ↓ (new_count > 0)
DownloadVideos
```

### Input from Token Validator
```json
{
  "validation": {
    "valid": true,
    "correlation_id": "..."
  }
}
```

### Output to Video Downloader
```json
{
  "new_videos": [
    {
      "media_id": "...",
      "filename": "...",
      ...
    }
  ]
}
```

## IAM Permissions

```python
# Secrets Manager (read-only)
- secretsmanager:GetSecretValue

# DynamoDB (read-only)
- dynamodb:GetItem
- dynamodb:BatchGetItem
- dynamodb:Query

# CloudWatch
- logs:CreateLogGroup, CreateLogStream, PutLogEvents
- cloudwatch:PutMetricData (CloudSync/MediaListing namespace)

# SNS
- sns:Publish (for API structure alerts)

# X-Ray
- xray:PutTraceSegments, PutTelemetryRecords

# VPC (optional)
- ec2:CreateNetworkInterface, etc.
```

## Performance Considerations

### Large Libraries (971 items)
- **Pagination**: Handles libraries of any size
- **API Calls**: ~10 pages for 971 items = ~20 seconds
- **DynamoDB**: Batch queries (100 items/batch) = ~10 batches
- **Total Time**: ~30-40 seconds for 971-item library

### Memory & Timeout
- **Memory**: 512 MB (handles large responses)
- **Timeout**: 5 minutes (accommodates slow API)
- **Actual Usage**: Typically <1 minute

### Cost
- **Invocations**: ~30/month (daily sync)
- **Duration**: ~30-40 seconds @ 512MB
- **Cost**: ~$0.10/month
- **DynamoDB**: Read capacity minimal
- **Metrics**: ~$1.50/month (5 custom metrics)

## Monitoring

### CloudWatch Logs
```bash
# View logs
aws logs tail /aws/lambda/media-lister --follow

# Search for new videos
aws logs filter-log-events \
  --log-group-name /aws/lambda/media-lister \
  --filter-pattern "Found.*new videos"
```

### CloudWatch Metrics
```bash
# Check success rate
aws cloudwatch get-metric-statistics \
  --namespace CloudSync/MediaListing \
  --metric-name ListingSuccess \
  --start-time 2025-12-01T00:00:00Z \
  --end-time 2025-12-01T23:59:59Z \
  --period 3600 \
  --statistics Sum
```

### X-Ray Traces
- Secrets Manager latency
- GoProProvider API calls
- DynamoDB batch queries
- Overall listing duration

## Known Limitations

1. **Max Results**: 1000 items per execution (configurable)
2. **DynamoDB Throttling**: Handled with exponential backoff
3. **API Rate Limits**: Retry logic with 429 handling
4. **Unknown File Sizes**: Some items return file_size: null (okay, handled in downloader)

## Future Enhancements

1. **Continuation Token**: For libraries >1000 items
2. **Incremental Listing**: Only check new items since last sync
3. **Filter by Date**: Optional date range filtering
4. **Cache API Responses**: Reduce redundant API calls
5. **Parallel DynamoDB Queries**: For very large libraries

## Compliance with Requirements

This implementation satisfies these requirements:

- **1.1**: List videos from GoPro Cloud ✅
- **1.2**: Use provider abstraction interface ✅
- **1.3**: Handle pagination (100 items/page) ✅
- **1.4**: Filter based on DynamoDB sync status ✅
- **1.5**: Return only new videos ✅
- **4.2**: Query DynamoDB efficiently ✅
- **7.6**: X-Ray tracing enabled ✅
- **12.1**: Validate API response structure ✅
- **12.2**: Log complete response on validation failure ✅
- **12.3**: Publish alert when structure differs ✅

## Validation Checklist

- [x] Lambda function deployed with correct configuration
- [x] IAM role with least-privilege permissions
- [x] Unit tests implemented and passing (24/24)
- [x] GoProProvider integration working
- [x] DynamoDB filtering logic correct
- [x] Pagination working for 971-item library
- [x] GH*/GO* filtering working
- [x] API structure validation implemented
- [x] SNS alerting configured
- [x] CloudWatch metrics publishing
- [x] X-Ray tracing enabled
- [x] Error handling comprehensive
- [x] Secrets Manager integration working

## Usage Examples

### Manual Invocation
```bash
aws lambda invoke \
  --function-name media-lister \
  --payload '{"correlation_id": "test-123"}' \
  /tmp/response.json

cat /tmp/response.json | jq '.'
```

### From Step Functions
```json
{
  "ListMedia": {
    "Type": "Task",
    "Resource": "arn:aws:lambda:...:function:media-lister",
    "ResultPath": "$.listing",
    "Next": "CheckNewVideos"
  }
}
```

---

**Task 6 Status: Complete ✅**

**Next Task**: Task 8 - Step Functions State Machine
