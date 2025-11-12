# Design Document

## Overview

The Cloud Sync Application is a serverless, event-driven system built on AWS that automates the synchronization of video content from cloud storage providers to cost-optimized S3 storage tiers. The initial implementation supports GoPro Cloud as the source provider, with an architecture designed for extensibility to additional providers.

### Design Goals

1. **Zero Manual Intervention**: Fully automated discovery, transfer, and lifecycle management
2. **Cost Optimization**: Minimize storage costs through intelligent lifecycle policies (95% cost reduction)
3. **Reliability**: 99.5% successful sync rate with automatic retry and error handling
4. **Security**: Encryption in transit and at rest, least privilege access, secure credential management
5. **Scalability**: Handle 1,000+ videos per sync execution without modification
6. **Extensibility**: Support multiple cloud providers through provider-agnostic interfaces

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Serverless Architecture** | Eliminates infrastructure management, automatic scaling, pay-per-use pricing |
| **Step Functions for Orchestration** | Visual workflow, built-in error handling, retry logic, state management |
| **Streaming Transfer** | Avoid Lambda storage limits, reduce latency, enable large file support |
| **DynamoDB for State** | Single-digit millisecond latency, flexible schema, automatic scaling |
| **S3 Lifecycle Policies** | Automatic cost optimization without code changes |
| **Multipart Upload** | Reliable transfer for large files, parallel chunk upload, resume capability |

## Architecture

### High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        AWS Cloud                                 │
│                                                                   │
│  ┌──────────────┐                                                │
│  │ EventBridge  │ Daily 2 AM CET                                 │
│  │  Scheduler   │                                                │
│  └──────┬───────┘                                                │
│         │                                                         │
│         ▼                                                         │
│  ┌─────────────────────────────────────────────────────┐        │
│  │  Step Functions: gopro-sync-orchestrator            │        │
│  │  ┌──────────────────────────────────────────────┐   │        │
│  │  │ 1. Authenticate → 2. List Media →            │   │        │
│  │  │ 3. Filter New → 4. Download (Map x5) →       │   │        │
│  │  │ 5. Generate Summary                           │   │        │
│  │  └──────────────────────────────────────────────┘   │        │
│  └─────────────────────────────────────────────────────┘        │
│         │           │              │                              │
│         ▼           ▼              ▼                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐                    │
│  │ Lambda:  │ │ Lambda:  │ │ Lambda:      │                    │
│  │ Media    │ │ Media    │ │ Video        │                    │
│  │ Auth     │ │ Lister   │ │ Downloader   │                    │
│  └────┬─────┘ └────┬─────┘ └──────┬───────┘                    │
│       │            │               │                             │
│       ▼            ▼               ▼                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐                    │
│  │ Secrets  │ │ DynamoDB │ │ S3 Bucket    │                    │
│  │ Manager  │ │ Sync     │ │ gopro-       │                    │
│  │          │ │ Tracker  │ │ archive      │                    │
│  └──────────┘ └──────────┘ └──────┬───────┘                    │
│                                    │                             │
│                                    ▼                             │
│                             ┌──────────────┐                    │
│                             │ S3 Lifecycle │                    │
│                             │ Standard(7d) │                    │
│                             │ → Glacier IR │                    │
│                             │ → Deep Arch  │                    │
│                             └──────────────┘                    │
│                                                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ CloudWatch   │  │ CloudWatch   │  │ SNS Topic    │         │
│  │ Logs         │  │ Metrics      │  │ Alerts       │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
│                                                                   │
└───────────────────────────────────────────────────────────────┘
         │                                    │
         ▼                                    ▼
┌─────────────────┐                  ┌─────────────────┐
│  GoPro Cloud    │                  │  Email/Slack    │
│  API            │                  │  Notifications  │
└─────────────────┘                  └─────────────────┘
```


### Component Interaction Flow

```
1. EventBridge triggers Step Functions at scheduled time
2. Step Functions invokes Media Authenticator
   └─> Retrieves/refreshes credentials from Secrets Manager
   └─> Returns auth token
3. Step Functions invokes Media Lister with auth token
   └─> Queries GoPro API for video list (paginated)
   └─> Checks DynamoDB for each video's sync status
   └─> Returns list of new/failed videos
4. Step Functions Map state processes videos in parallel (max 5)
   └─> For each video, invokes Video Downloader
       └─> Updates DynamoDB status to IN_PROGRESS
       └─> Streams video from GoPro to S3 (multipart if >100MB)
       └─> Updates DynamoDB status to COMPLETED
       └─> Publishes CloudWatch metrics
5. Step Functions generates summary
   └─> If failures exist, publishes SNS notification
6. S3 Lifecycle Policy automatically transitions objects
   └─> Day 7: Standard → Glacier Instant Retrieval
   └─> Day 14: Glacier IR → Deep Archive
```

## Components and Interfaces

### Component 1: Media Authenticator (Lambda Function)

**Purpose**: Authenticate with cloud provider APIs and manage credential lifecycle.

**Technical Specifications**:
- **Runtime**: Python 3.12
- **Memory**: 256 MB
- **Timeout**: 30 seconds
- **Concurrency**: 1 (serial authentication)
- **Environment Variables**:
  - `SECRET_NAME`: "gopro/credentials"
  - `TOKEN_EXPIRY_HOURS`: 24

**Input Interface**:
```json
{
  "provider": "gopro",
  "action": "authenticate"
}
```

**Output Interface**:
```json
{
  "statusCode": 200,
  "provider": "gopro",
  "auth_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "user_id": "12345678",
  "expires_at": "2025-11-13T02:00:00Z"
}
```

**Core Logic**:
1. Retrieve secret from Secrets Manager using boto3
2. Parse stored credentials (refresh_token, access_token, timestamp, user_id)
3. Check token expiration (current_time + 24 hours)
4. If expired or missing:
   - Use OAuth 2.0 refresh token flow to obtain new access token
   - Store new access token and refresh token with current timestamp
5. Return authentication headers

**OAuth 2.0 Flow**:
```python
def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    """Refresh access token using OAuth 2.0 refresh token flow"""
    response = requests.post(
        "https://api.gopro.com/v1/oauth2/token",
        json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": os.environ["GOPRO_CLIENT_ID"],
            "client_secret": os.environ["GOPRO_CLIENT_SECRET"]
        }
    )
    
    if response.status_code == 200:
        data = response.json()
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", refresh_token),  # Some APIs return new refresh token
            "expires_in": data["expires_in"],
            "token_type": data["token_type"]
        }
    else:
        raise AuthenticationError(f"Token refresh failed: {response.status_code}")
```

**Error Handling**:
- Invalid credentials (401) → Publish SNS alert, return error, require manual re-authentication
- Refresh token expired → Publish SNS alert, require manual OAuth flow
- Network timeout → Retry 3x with exponential backoff (2s, 4s, 8s)
- Secrets Manager unavailable → Return 500, fail execution

**Dependencies**:
- boto3 (AWS SDK)
- requests (HTTP client)
- AWS Secrets Manager
- GoPro Cloud API


### Component 2: Media Lister (Lambda Function)

**Purpose**: Query cloud provider API for video list and filter for unsynced content.

**Technical Specifications**:
- **Runtime**: Python 3.12
- **Memory**: 512 MB
- **Timeout**: 5 minutes
- **Concurrency**: 1
- **Environment Variables**:
  - `DYNAMODB_TABLE`: "gopro-sync-tracker"
  - `PAGE_SIZE`: 100
  - `MAX_VIDEOS`: 1000

**Input Interface**:
```json
{
  "provider": "gopro",
  "auth_token": "eyJhbGc...",
  "user_id": "12345678",
  "max_videos": 1000
}
```

**Output Interface**:
```json
{
  "statusCode": 200,
  "provider": "gopro",
  "new_videos": [
    {
      "media_id": "abc123",
      "filename": "GH010456.MP4",
      "download_url": "https://api.gopro.com/media/abc123/download",
      "file_size": 524288000,
      "upload_date": "2025-11-10T14:23:45Z",
      "duration": 180
    }
  ],
  "total_found": 150,
  "new_count": 12,
  "already_synced": 138
}
```

**Core Logic**:
1. Initialize pagination (page=1, per_page=100)
2. Loop until all pages retrieved or max_videos reached:
   - Call GoPro API: GET /media/search?page={page}&per_page={per_page}
   - Extract video metadata from response
   - Add to videos list
3. For each video, query DynamoDB:
   - GetItem with PK=media_id
   - If item doesn't exist OR status != "COMPLETED", add to new_videos list
4. Return filtered list with counts

**Optimization**:
- Use BatchGetItem for DynamoDB queries (up to 100 items per request)
- Implement exponential backoff for API rate limits
- Cache DynamoDB results in memory for duplicate checks

**Error Handling**:
- GoPro API rate limit (429) → Wait for Retry-After header, exponential backoff
- DynamoDB throttling → Enable autoscaling, retry with jitter
- Large result set (>1000) → Process first 1000, log warning

**Dependencies**:
- boto3 (DynamoDB client)
- requests (HTTP client)
- GoPro Cloud API


### Component 3: Video Downloader (Lambda Function)

**Purpose**: Stream video from cloud provider to S3 using multipart upload for large files.

**Technical Specifications**:
- **Runtime**: Python 3.12
- **Memory**: 1024 MB (increased for large 4K video streaming)
- **Timeout**: 15 minutes
- **Concurrency**: 5 (parallel downloads)
- **Environment Variables**:
  - `S3_BUCKET`: "gopro-archive-bucket"
  - `DYNAMODB_TABLE`: "gopro-sync-tracker"
  - `MULTIPART_THRESHOLD`: 104857600 (100 MB)
  - `CHUNK_SIZE`: 104857600 (100 MB, optimized for 4K videos)

**Performance Considerations**:
- GoPro 4K videos typically 2-4 GB in size
- With 1024 MB memory and 100 MB chunks, can handle videos up to 10 GB
- Streaming approach avoids Lambda storage limits
- Increased chunk size reduces number of S3 API calls (cost optimization)

**Input Interface**:
```json
{
  "provider": "gopro",
  "media_id": "abc123",
  "filename": "GH010456.MP4",
  "download_url": "https://api.gopro.com/media/abc123/download",
  "file_size": 524288000,
  "auth_token": "eyJhbGc...",
  "upload_date": "2025-11-10T14:23:45Z"
}
```

**Output Interface**:
```json
{
  "statusCode": 200,
  "media_id": "abc123",
  "s3_key": "gopro-videos/2025/11/GH010456.MP4",
  "s3_etag": "\"d41d8cd98f00b204e9800998ecf8427e\"",
  "bytes_transferred": 524288000,
  "transfer_duration_seconds": 87,
  "throughput_mbps": 48.2
}
```

**Core Logic**:

1. **Update Status to IN_PROGRESS**:
```python
dynamodb.update_item(
    Key={'media_id': media_id},
    UpdateExpression='SET #status = :status, #timestamp = :timestamp',
    ExpressionAttributeNames={'#status': 'status', '#timestamp': 'update_timestamp'},
    ExpressionAttributeValues={':status': 'IN_PROGRESS', ':timestamp': current_time}
)
```

2. **Generate S3 Key**:
```python
upload_date = datetime.fromisoformat(event['upload_date'])
s3_key = f"{provider}-videos/{upload_date.year}/{upload_date.month:02d}/{filename}"
```

3. **Stream Transfer Decision**:
```python
if file_size > MULTIPART_THRESHOLD:
    result = multipart_upload_stream(download_url, s3_bucket, s3_key, auth_token)
else:
    result = direct_upload_stream(download_url, s3_bucket, s3_key, auth_token)
```

4. **Multipart Upload Implementation**:
```python
def multipart_upload_stream(download_url, bucket, key, auth_token):
    # Open streaming connection to source
    response = requests.get(download_url, headers={'Authorization': f'Bearer {auth_token}'}, stream=True)
    
    # Initiate multipart upload
    multipart = s3_client.create_multipart_upload(
        Bucket=bucket,
        Key=key,
        StorageClass='STANDARD',
        ServerSideEncryption='aws:kms',
        Tagging='Source=GoPro&AutoSync=True'
    )
    
    upload_id = multipart['UploadId']
    parts = []
    part_number = 1
    bytes_transferred = 0
    
    try:
        # Stream chunks directly to S3
        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            if chunk:
                part = s3_client.upload_part(
                    Bucket=bucket,
                    Key=key,
                    PartNumber=part_number,
                    UploadId=upload_id,
                    Body=chunk
                )
                parts.append({'PartNumber': part_number, 'ETag': part['ETag']})
                bytes_transferred += len(chunk)
                part_number += 1
                
                logger.info(f"Uploaded part {part_number}, bytes: {bytes_transferred}")
        
        # Complete multipart upload
        s3_client.complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={'Parts': parts}
        )
        
        return {'status': 'success', 'bytes': bytes_transferred, 'parts': len(parts)}
        
    except Exception as e:
        logger.error(f"Multipart upload failed: {str(e)}")
        s3_client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        raise e
```

5. **Verify Transfer**:
```python
if bytes_transferred != file_size:
    raise ValueError(f"Size mismatch: expected {file_size}, got {bytes_transferred}")
```

6. **Update Status to COMPLETED**:
```python
dynamodb.update_item(
    Key={'media_id': media_id},
    UpdateExpression='SET #status = :status, s3_key = :key, sync_timestamp = :timestamp',
    ExpressionAttributeValues={
        ':status': 'COMPLETED',
        ':key': s3_key,
        ':timestamp': current_time
    }
)
```

7. **Publish Metrics**:
```python
cloudwatch.put_metric_data(
    Namespace='GoProSync',
    MetricData=[
        {'MetricName': 'VideosSynced', 'Value': 1, 'Unit': 'Count'},
        {'MetricName': 'BytesTransferred', 'Value': bytes_transferred, 'Unit': 'Bytes'},
        {'MetricName': 'TransferDuration', 'Value': duration_seconds, 'Unit': 'Seconds'}
    ]
)
```

**Error Handling**:
- Network interruption → Abort multipart upload, mark FAILED, retry entire video
- S3 API error → Retry 3x with exponential backoff (30s, 60s, 120s)
- File size mismatch → Mark FAILED, send alert, no retry
- Lambda timeout → Increase memory for faster network, or split large files
- Video deleted from source (404) → Mark as COMPLETED with note "source_deleted", skip download
- Source API rate limit (429) → Exponential backoff with jitter, respect Retry-After header

**Idempotency**:
```python
# Add idempotency token to prevent duplicate uploads on retry
idempotency_token = f"{media_id}-{upload_date}"

s3_client.put_object(
    Bucket=bucket,
    Key=key,
    Body=stream,
    Metadata={
        'IdempotencyToken': idempotency_token,
        'SourceMediaId': media_id,
        'SourceProvider': 'gopro'
    }
)

# Before upload, check if object with same idempotency token exists
try:
    existing = s3_client.head_object(Bucket=bucket, Key=key)
    if existing['Metadata'].get('IdempotencyToken') == idempotency_token:
        logger.info(f"Video {media_id} already uploaded, skipping")
        return {'statusCode': 200, 'message': 'Already uploaded'}
except ClientError as e:
    if e.response['Error']['Code'] != '404':
        raise
```

**Dependencies**:
- boto3 (S3, DynamoDB, CloudWatch clients)
- requests (HTTP streaming)


### Component 4: Orchestrator (Step Functions State Machine)

**Purpose**: Coordinate multi-step sync workflow with error handling and parallel execution.

**State Machine Name**: `gopro-sync-orchestrator`

**Execution Trigger**: EventBridge rule with cron expression `cron(0 2 * * ? *)` (2 AM CET daily)

**Timeout Calculation**:
- 1,000 videos / 5 concurrent = 200 batches
- Average 4 GB video at 50 Mbps = ~640 seconds per video
- 200 batches × 640 seconds = 128,000 seconds = ~35 hours
- **Timeout**: 43200 seconds (12 hours) with continuation pattern for larger libraries

**Continuation Pattern**:
For libraries >500 videos, implement pagination:
```json
{
  "batch_size": 500,
  "continuation_token": "last_processed_media_id",
  "total_processed": 500
}
```
Next execution picks up from continuation_token

**State Machine Definition** (Amazon States Language):

```json
{
  "Comment": "GoPro Cloud to S3 Sync Orchestration",
  "StartAt": "AuthenticateProvider",
  "TimeoutSeconds": 7200,
  "States": {
    "AuthenticateProvider": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:${region}:${account}:function:media-authenticator",
      "Parameters": {
        "provider": "gopro",
        "action": "authenticate"
      },
      "ResultPath": "$.auth",
      "Retry": [
        {
          "ErrorEquals": ["Lambda.ServiceException", "Lambda.TooManyRequestsException"],
          "IntervalSeconds": 2,
          "MaxAttempts": 3,
          "BackoffRate": 2.0
        }
      ],
      "Catch": [
        {
          "ErrorEquals": ["States.ALL"],
          "ResultPath": "$.error",
          "Next": "NotifyCriticalFailure"
        }
      ],
      "Next": "ListMedia"
    },
    
    "ListMedia": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:${region}:${account}:function:media-lister",
      "Parameters": {
        "provider": "gopro",
        "auth_token.$": "$.auth.auth_token",
        "user_id.$": "$.auth.user_id",
        "max_videos": 1000
      },
      "ResultPath": "$.media",
      "Retry": [
        {
          "ErrorEquals": ["Lambda.ServiceException"],
          "IntervalSeconds": 2,
          "MaxAttempts": 3,
          "BackoffRate": 2.0
        }
      ],
      "Catch": [
        {
          "ErrorEquals": ["States.ALL"],
          "ResultPath": "$.error",
          "Next": "NotifyCriticalFailure"
        }
      ],
      "Next": "CheckNewVideos"
    },
    
    "CheckNewVideos": {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.media.new_count",
          "NumericGreaterThan": 0,
          "Next": "DownloadVideos"
        }
      ],
      "Default": "NoNewVideos"
    },
    
    "DownloadVideos": {
      "Type": "Map",
      "ItemsPath": "$.media.new_videos",
      "MaxConcurrency": 5,
      "ResultPath": "$.download_results",
      "Parameters": {
        "provider": "gopro",
        "media_id.$": "$$.Map.Item.Value.media_id",
        "filename.$": "$$.Map.Item.Value.filename",
        "download_url.$": "$$.Map.Item.Value.download_url",
        "file_size.$": "$$.Map.Item.Value.file_size",
        "upload_date.$": "$$.Map.Item.Value.upload_date",
        "auth_token.$": "$.auth.auth_token"
      },
      "Iterator": {
        "StartAt": "DownloadVideo",
        "States": {
          "DownloadVideo": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:${region}:${account}:function:video-downloader",
            "Retry": [
              {
                "ErrorEquals": ["NetworkError", "TimeoutError"],
                "IntervalSeconds": 30,
                "MaxAttempts": 3,
                "BackoffRate": 2.0,
                "MaxDelaySeconds": 300
              }
            ],
            "Catch": [
              {
                "ErrorEquals": ["States.ALL"],
                "ResultPath": "$.error",
                "Next": "MarkVideoFailed"
              }
            ],
            "Next": "VideoComplete"
          },
          "VideoComplete": {
            "Type": "Pass",
            "End": true
          },
          "MarkVideoFailed": {
            "Type": "Pass",
            "End": true
          }
        }
      },
      "Next": "GenerateSummary"
    },
    
    "GenerateSummary": {
      "Type": "Pass",
      "Parameters": {
        "execution_id.$": "$$.Execution.Id",
        "total_videos.$": "$.media.new_count",
        "successful_downloads.$": "States.ArrayLength($.download_results[?(@.statusCode==200)])",
        "failed_downloads.$": "States.ArrayLength($.download_results[?(@.statusCode!=200)])",
        "start_time.$": "$$.Execution.StartTime"
      },
      "ResultPath": "$.summary",
      "Next": "CheckForFailures"
    },
    
    "CheckForFailures": {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.summary.failed_downloads",
          "NumericGreaterThan": 0,
          "Next": "NotifyPartialFailure"
        }
      ],
      "Default": "SyncComplete"
    },
    
    "NotifyPartialFailure": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sns:publish",
      "Parameters": {
        "TopicArn": "arn:aws:sns:${region}:${account}:gopro-sync-alerts",
        "Subject": "GoPro Sync Partial Failure",
        "Message.$": "States.Format('Sync completed with {} failures out of {} videos. Execution: {}', $.summary.failed_downloads, $.summary.total_videos, $.summary.execution_id)"
      },
      "Next": "SyncComplete"
    },
    
    "NoNewVideos": {
      "Type": "Succeed",
      "Comment": "No new videos to sync"
    },
    
    "NotifyCriticalFailure": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sns:publish",
      "Parameters": {
        "TopicArn": "arn:aws:sns:${region}:${account}:gopro-sync-alerts",
        "Subject": "GoPro Sync Critical Failure",
        "Message.$": "States.Format('Critical failure in sync execution: {}. Error: {}', $$.Execution.Id, $.error.Cause)"
      },
      "Next": "SyncFailed"
    },
    
    "SyncFailed": {
      "Type": "Fail",
      "Error": "SyncExecutionFailed",
      "Cause": "Critical failure during sync execution"
    },
    
    "SyncComplete": {
      "Type": "Succeed"
    }
  }
}
```

**Retry Strategy**:

| State | Error Type | Interval | Max Attempts | Backoff Rate | Max Delay |
|-------|------------|----------|--------------|--------------|-----------|
| AuthenticateProvider | Lambda.ServiceException | 2s | 3 | 2.0 | - |
| ListMedia | Lambda.ServiceException | 2s | 3 | 2.0 | - |
| DownloadVideo | NetworkError, TimeoutError | 30s | 3 | 2.0 | 300s |

**Backoff Calculation**:
- Attempt 1: Wait 30 seconds
- Attempt 2: Wait 60 seconds (30 × 2.0)
- Attempt 3: Wait 120 seconds (60 × 2.0, capped at 300s max)


## Data Models

### DynamoDB Table: Sync Tracker

**Table Configuration**:
- **Table Name**: `gopro-sync-tracker`
- **Billing Mode**: On-Demand (unpredictable access patterns)
- **Partition Key**: `media_id` (String) - Unique identifier from cloud provider
- **Sort Key**: None
- **Point-in-Time Recovery**: Enabled
- **Encryption**: AWS managed key (SSE)

**Attributes**:

| Attribute | Type | Description | Example |
|-----------|------|-------------|---------|
| `media_id` | String (PK) | Cloud provider video unique ID | `"abc123def456"` |
| `provider` | String | Cloud provider identifier | `"gopro"` |
| `filename` | String | Original filename | `"GH010456.MP4"` |
| `s3_key` | String | S3 object key after upload | `"gopro-videos/2025/11/GH010456.MP4"` |
| `file_size` | Number | Video file size in bytes | `524288000` |
| `upload_date` | String (ISO8601) | Date uploaded to cloud provider | `"2025-11-10T14:23:45Z"` |
| `sync_timestamp` | String (ISO8601) | Date/time synced to S3 | `"2025-11-11T02:15:32Z"` |
| `status` | String | Sync status enum | `"COMPLETED"` |
| `retry_count` | Number | Number of retry attempts | `0` |
| `error_message` | String | Last error message if failed | `"Network timeout"` |
| `duration_seconds` | Number | Video duration | `180` |
| `transfer_duration` | Number | Transfer time in seconds | `87` |
| `throughput_mbps` | Number | Transfer throughput | `48.2` |

**Status Values**:
- `PENDING`: Video identified, not yet started
- `IN_PROGRESS`: Download/upload in progress
- `COMPLETED`: Successfully synced to S3
- `FAILED`: Transfer failed after retries

**Global Secondary Index**:
- **Index Name**: `status-sync_timestamp-index`
- **Partition Key**: `status` (String)
- **Sort Key**: `sync_timestamp` (String)
- **Projection**: ALL
- **Purpose**: Query videos by status, sorted by sync time (e.g., recent failures)

**Access Patterns**:
1. Check if video already synced: `GetItem(media_id)`
2. Mark video as IN_PROGRESS: `UpdateItem(media_id, status=IN_PROGRESS)`
3. Complete sync: `UpdateItem(media_id, status=COMPLETED, s3_key, sync_timestamp)`
4. Query recent failures: `Query(GSI, status=FAILED, sort by sync_timestamp DESC)`
5. Batch check multiple videos: `BatchGetItem([media_id1, media_id2, ...])`

**Example Item**:
```json
{
  "media_id": "abc123def456",
  "provider": "gopro",
  "filename": "GH010456.MP4",
  "s3_key": "gopro-videos/2025/11/GH010456.MP4",
  "file_size": 524288000,
  "upload_date": "2025-11-10T14:23:45Z",
  "sync_timestamp": "2025-11-11T02:15:32Z",
  "status": "COMPLETED",
  "retry_count": 0,
  "duration_seconds": 180,
  "transfer_duration": 87,
  "throughput_mbps": 48.2
}
```

### S3 Bucket: Archive Bucket

**Bucket Configuration**:
- **Bucket Name**: `gopro-archive-bucket-{account-id}` (globally unique)
- **Region**: Same as Lambda functions (minimize data transfer costs)
- **Versioning**: Enabled (protect against accidental deletion)
- **Encryption**: SSE-KMS with customer-managed key
- **Block Public Access**: All settings enabled (Block all public access)
- **Object Lock**: Disabled (not required for this use case)
- **Intelligent-Tiering**: Disabled (using explicit lifecycle policies)

**Folder Structure**:
```
s3://gopro-archive-bucket-{account-id}/
└── gopro-videos/
    ├── 2025/
    │   ├── 11/
    │   │   ├── GH010456.MP4
    │   │   ├── GH010457.MP4
    │   │   └── GH010458.MP4
    │   └── 12/
    │       └── GH010500.MP4
    └── 2026/
        └── 01/
            └── GH010600.MP4
```

**S3 Lifecycle Policy**:
```json
{
  "Rules": [
    {
      "Id": "transition-to-deep-archive",
      "Status": "Enabled",
      "Filter": {
        "Prefix": "gopro-videos/"
      },
      "Transitions": [
        {
          "Days": 7,
          "StorageClass": "GLACIER_IR"
        },
        {
          "Days": 14,
          "StorageClass": "DEEP_ARCHIVE"
        }
      ],
      "NoncurrentVersionTransitions": [
        {
          "NoncurrentDays": 30,
          "StorageClass": "DEEP_ARCHIVE"
        }
      ]
    }
  ]
}
```

**Lifecycle Policy Considerations**:
- **7 days in Standard**: Allows for validation and quick access if issues found
- **7 days in Glacier IR**: Instant retrieval if needed, lower cost than Standard
- **Deep Archive after 14 days**: Lowest cost, 12-48 hour retrieval time
- **Important**: Deep Archive retrieval takes 12-48 hours - ensure this aligns with recovery needs
- **Alternative**: If faster recovery needed, stay in Glacier IR (4x cost but instant retrieval)
- **Cost vs Access Trade-off**: 
  - Deep Archive: $0.00099/GB/month, 12-48h retrieval
  - Glacier IR: $0.004/GB/month, instant retrieval
  - For frequently accessed archives, consider Glacier IR only

**Object Tagging**:
- `Source=GoPro`: Identifies source system
- `AutoSync=True`: Indicates automatic sync
- `UploadDate=YYYY-MM-DD`: Original upload date to cloud provider

**Cost Analysis** (100 GB/month):
- Days 0-7 (Standard): 100 GB × $0.023/GB × 7/30 = $0.54
- Days 7-14 (Glacier IR): 100 GB × $0.004/GB × 7/30 = $0.09
- Days 14+ (Deep Archive): 100 GB × $0.00099/GB × 16/30 = $0.05
- **Total first month**: $0.68
- **Ongoing monthly**: $0.10 (after transition complete)

### Secrets Manager: Provider Credentials

**Secret Configuration**:
- **Secret Name**: `gopro/credentials`
- **Encryption**: AWS managed key (default)
- **Rotation**: Manual (90-day reminder via CloudWatch Events)
- **Access**: Restricted to Media Authenticator Lambda role only

**Secret Structure**:
```json
{
  "provider": "gopro",
  "username": "user@example.com",
  "password": "encrypted_password",
  "jwt_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "user_id": "12345678",
  "token_timestamp": "2025-11-11T02:00:00Z",
  "last_updated": "2025-11-11T02:00:00Z"
}
```

**Rotation Strategy**:
- OAuth 2.0 refresh tokens enable automatic rotation
- Implement Lambda-based rotation function for refresh token renewal
- Rotation schedule: Every 30 days (well before typical 90-day expiry)
- CloudWatch Event triggers rotation Lambda
- Rotation Lambda tests new credentials before completing rotation

**Automatic Rotation Lambda**:
```python
def rotate_secret(event, context):
    """
    Rotate OAuth refresh token
    
    Steps:
    1. Retrieve current secret
    2. Use refresh token to get new access token
    3. Test new credentials with API call
    4. Update secret with new tokens
    5. Verify rotation successful
    """
    secrets_client = boto3.client('secretsmanager')
    
    # Get current secret
    secret = secrets_client.get_secret_value(SecretId='gopro/credentials')
    current_creds = json.loads(secret['SecretString'])
    
    # Refresh access token
    new_tokens = refresh_access_token(current_creds['refresh_token'])
    
    # Test new credentials
    test_result = test_credentials(new_tokens['access_token'])
    if not test_result['success']:
        raise Exception(f"Credential test failed: {test_result['error']}")
    
    # Update secret
    updated_creds = {
        **current_creds,
        'access_token': new_tokens['access_token'],
        'refresh_token': new_tokens.get('refresh_token', current_creds['refresh_token']),
        'token_timestamp': datetime.utcnow().isoformat(),
        'last_rotated': datetime.utcnow().isoformat()
    }
    
    secrets_client.update_secret(
        SecretId='gopro/credentials',
        SecretString=json.dumps(updated_creds)
    )
    
    logger.info("Secret rotation completed successfully")
    return {'statusCode': 200, 'message': 'Rotation successful'}
```

**Rotation Monitoring**:
- CloudWatch alarm for rotation failures
- SNS notification on rotation success/failure
- Metrics: `SecretRotationSuccess`, `SecretRotationFailure`
- Manual fallback: If automatic rotation fails, alert ops team for manual intervention


## Error Handling

### Error Categories

**1. Transient Errors** (Retryable):
- Network timeouts
- HTTP 5xx from cloud provider API
- Lambda throttling (TooManyRequestsException)
- DynamoDB throttling (ProvisionedThroughputExceededException)
- S3 SlowDown errors

**Action**: Retry with exponential backoff (max 3 attempts)

**2. Permanent Errors** (Non-retryable):
- Authentication failure (401 Unauthorized)
- Invalid credentials
- File not found (404)
- Invalid request parameters (400)
- Insufficient permissions (403)

**Action**: Mark as FAILED, send alert, no automatic retry

**3. Partial Failures**:
- Some videos succeed, some fail in Map state
- Individual video download failures

**Action**: Log failures, continue processing remaining, send summary alert

### Retry Configuration

**Lambda Function Retries**:
```python
# Implemented in Lambda code using decorators
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    retry=retry_if_exception_type((NetworkError, TimeoutError))
)
def download_video(url, auth_token):
    # Download logic
    pass
```

**Step Functions Retries**:
- Configured in state machine definition (see Orchestrator section)
- Automatic retry for Lambda service exceptions
- Custom retry for application-specific errors

**Dead Letter Queue**:
- SQS DLQ attached to each Lambda function
- Failed invocations sent to DLQ after exhausting retries
- DLQ retention: 14 days
- CloudWatch alarm triggers when DLQ depth > 0
- Manual investigation required for DLQ messages

**DLQ Message Format**:
```json
{
  "requestId": "abc-123-def",
  "timestamp": "2025-11-11T02:15:32.123Z",
  "functionName": "video-downloader",
  "event": {
    "media_id": "abc123",
    "filename": "GH010456.MP4",
    "provider": "gopro"
  },
  "error": {
    "errorType": "NetworkTimeout",
    "errorMessage": "Connection timeout after 30 seconds",
    "stackTrace": ["..."]
  },
  "retryCount": 3
}
```

**DLQ Redrive Policy**:
```json
{
  "maxReceiveCount": 3,
  "deadLetterTargetArn": "arn:aws:sqs:${region}:${account}:video-downloader-dlq"
}
```

**Manual Intervention Procedures**:
1. Monitor DLQ depth via CloudWatch alarm
2. Retrieve messages from DLQ using AWS Console or CLI
3. Investigate error cause (check logs, verify source availability)
4. Fix underlying issue (update credentials, increase timeout, etc.)
5. Redrive messages from DLQ back to source queue for retry
6. If issue persists, mark videos as FAILED in DynamoDB with manual review flag

### Error Logging

**Structured Log Format**:
```json
{
  "timestamp": "2025-11-11T02:15:32.123Z",
  "level": "ERROR",
  "correlation_id": "abc-123-def",
  "function_name": "video-downloader",
  "event_type": "video_download_failed",
  "media_id": "abc123",
  "filename": "GH010456.MP4",
  "error_type": "NetworkTimeout",
  "error_message": "Connection timeout after 30 seconds",
  "retry_count": 2,
  "stack_trace": "..."
}
```

**Log Levels**:
- **DEBUG**: Detailed diagnostic (disabled in production)
- **INFO**: General informational messages
- **WARN**: Warning conditions (retry attempts)
- **ERROR**: Error events requiring attention
- **CRITICAL**: System-level failures

## Testing Strategy

### Unit Testing

**Scope**: Individual Lambda functions in isolation

**Test Cases**:

**Media Authenticator**:
- Valid credentials return auth token
- Expired token triggers refresh
- Invalid credentials raise exception
- Secrets Manager unavailable handled gracefully
- Token expiration calculation correct

**Media Lister**:
- Pagination retrieves all videos
- DynamoDB filtering excludes completed videos
- API rate limiting handled with backoff
- Empty result set handled correctly
- Large result sets (>1000) truncated with warning

**Video Downloader**:
- Small files (<100MB) use direct upload
- Large files (>100MB) use multipart upload
- Byte count verification catches mismatches
- Network interruption aborts multipart upload
- DynamoDB status updates occur at correct times
- CloudWatch metrics published correctly

**Test Framework**: pytest with moto for AWS service mocking

**Example Test**:
```python
import pytest
from moto import mock_s3, mock_dynamodb
from video_downloader import handler

@mock_s3
@mock_dynamodb
def test_video_downloader_success():
    # Setup mocks
    s3_client = boto3.client('s3')
    s3_client.create_bucket(Bucket='test-bucket')
    
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.create_table(
        TableName='test-tracker',
        KeySchema=[{'AttributeName': 'media_id', 'KeyType': 'HASH'}],
        AttributeDefinitions=[{'AttributeName': 'media_id', 'AttributeType': 'S'}],
        BillingMode='PAY_PER_REQUEST'
    )
    
    # Test event
    event = {
        'media_id': 'test123',
        'filename': 'test.mp4',
        'download_url': 'https://example.com/video.mp4',
        'file_size': 1000000,
        'auth_token': 'test_token'
    }
    
    # Execute
    result = handler(event, {})
    
    # Assertions
    assert result['statusCode'] == 200
    assert result['bytes_transferred'] == 1000000
    
    # Verify DynamoDB update
    item = table.get_item(Key={'media_id': 'test123'})['Item']
    assert item['status'] == 'COMPLETED'
```

### Integration Testing

**Scope**: Multiple components working together

**Test Scenarios**:
1. **End-to-End Sync Flow**:
   - Trigger Step Functions execution
   - Verify authentication succeeds
   - Verify media listing returns expected videos
   - Verify video download completes
   - Verify DynamoDB updated correctly
   - Verify S3 object created with correct tags

2. **Error Recovery**:
   - Simulate network timeout during download
   - Verify retry logic executes
   - Verify DynamoDB status reflects retries
   - Verify alert sent after exhausting retries

3. **Partial Failure Handling**:
   - Simulate failure for 2 out of 5 videos
   - Verify successful videos complete
   - Verify failed videos marked correctly
   - Verify partial failure notification sent

**Test Environment**: Separate AWS account with test resources

**Test Data**: Sample GoPro videos (small files for fast execution)

### Load Testing

**Scope**: System performance under realistic load

**Test Scenarios**:
1. **High Volume Sync**:
   - 1,000 videos in single execution
   - Verify completion within 2 hours
   - Verify no Lambda throttling
   - Verify DynamoDB performance acceptable

2. **Concurrent Executions**:
   - Multiple Step Functions executions running simultaneously
   - Verify no resource contention
   - Verify correct concurrency limits enforced

**Tools**: AWS Step Functions execution history, CloudWatch metrics

**Success Criteria**:
- 99.5% success rate
- Average transfer throughput > 50 Mbps
- No Lambda throttling errors
- DynamoDB latency < 10ms (p99)

### Security Testing

**Scope**: Verify security controls are effective

**Test Cases**:
1. **IAM Permissions**:
   - Verify Lambda roles have least privilege
   - Verify cross-service access restricted
   - Verify no wildcard permissions

2. **Encryption**:
   - Verify S3 objects encrypted at rest
   - Verify data in transit uses TLS 1.2+
   - Verify Secrets Manager encryption enabled

3. **Network Security**:
   - Verify S3 bucket blocks public access
   - Verify no public Lambda endpoints
   - Verify VPC configuration (if applicable)

**Tools**: AWS IAM Access Analyzer, AWS Config Rules, manual review

### Chaos Engineering Testing

**Scope**: Verify system resilience under failure conditions

**Test Scenarios**:

1. **Lambda Failure Injection**:
   - Randomly terminate Lambda executions mid-stream
   - Verify Step Functions retry logic works correctly
   - Verify DynamoDB status reflects partial failures
   - Tool: AWS Fault Injection Simulator (FIS)

2. **Network Latency Injection**:
   - Add artificial latency to provider API calls
   - Verify timeout handling works correctly
   - Verify performance degradation is acceptable
   - Tool: AWS FIS with network latency actions

3. **DynamoDB Throttling**:
   - Simulate DynamoDB throttling errors
   - Verify exponential backoff and retry logic
   - Verify no data loss during throttling
   - Tool: AWS FIS with DynamoDB throttle actions

4. **S3 Service Degradation**:
   - Simulate S3 SlowDown errors
   - Verify multipart upload abort and retry
   - Verify no orphaned multipart uploads
   - Tool: AWS FIS with S3 error injection

5. **Secrets Manager Unavailability**:
   - Simulate Secrets Manager API failures
   - Verify authentication fallback behavior
   - Verify appropriate error notifications sent
   - Tool: AWS FIS with API error injection

6. **Partial Step Functions Execution Failure**:
   - Fail individual Map state iterations
   - Verify successful videos complete
   - Verify failed videos marked correctly
   - Verify partial failure notifications sent
   - Tool: Manual injection via Lambda error throwing

**Chaos Testing Framework**:
```python
# tests/chaos/test_lambda_failure.py
import boto3
import pytest
from datetime import datetime, timedelta

@pytest.mark.chaos
def test_lambda_failure_during_download():
    """Test system behavior when Lambda fails mid-download"""
    
    fis_client = boto3.client('fis')
    
    # Create FIS experiment template
    experiment = fis_client.start_experiment(
        experimentTemplateId='lambda-failure-template',
        tags={'Test': 'ChaosEngineering'}
    )
    
    # Trigger Step Functions execution
    sfn_client = boto3.client('stepfunctions')
    execution = sfn_client.start_execution(
        stateMachineArn='arn:aws:states:...:gopro-sync-orchestrator',
        input='{"provider": "gopro"}'
    )
    
    # Wait for execution to complete
    # ... wait logic ...
    
    # Verify results
    # - Check DynamoDB for correct status
    # - Verify retry attempts logged
    # - Verify no data corruption
    # - Verify alerts sent
    
    assert execution_status == 'SUCCEEDED' or has_partial_failures
    assert all_videos_accounted_for()
    assert no_orphaned_s3_uploads()
```

**Chaos Testing Schedule**:
- Run chaos tests weekly in staging environment
- Run subset of chaos tests before production deployments
- Gradually increase failure injection severity
- Document learnings and system improvements


## Monitoring and Observability

### CloudWatch Logs

**Log Groups**:
- `/aws/lambda/media-authenticator`
- `/aws/lambda/media-lister`
- `/aws/lambda/video-downloader`
- `/aws/states/gopro-sync-orchestrator`

**Log Retention**: 30 days for operational logs, 1 year for audit logs

**Structured Logging Format**:
```json
{
  "timestamp": "2025-11-11T02:15:32.123Z",
  "level": "INFO",
  "correlation_id": "abc-123-def",
  "function_name": "video-downloader",
  "event_type": "video_download_complete",
  "media_id": "abc123",
  "filename": "GH010456.MP4",
  "file_size_bytes": 524288000,
  "s3_bucket": "gopro-archive-bucket",
  "s3_key": "gopro-videos/2025/11/GH010456.MP4",
  "transfer_duration_seconds": 87,
  "throughput_mbps": 48.2
}
```

**CloudWatch Logs Insights Queries**:

**Query 1: Failed Downloads in Last 24 Hours**
```
fields @timestamp, media_id, filename, error_message
| filter level = "ERROR" and event_type = "video_download_failed"
| sort @timestamp desc
| limit 100
```

**Query 2: Average Transfer Throughput**
```
fields media_id, bytes_transferred, transfer_duration_seconds, 
       (bytes_transferred / transfer_duration_seconds / 1048576) as throughput_mbps
| filter event_type = "video_download_complete"
| stats avg(throughput_mbps) as avg_throughput, 
        max(throughput_mbps) as max_throughput, 
        min(throughput_mbps) as min_throughput
```

**Query 3: Slow Transfers (>2 minutes for <500MB)**
```
fields @timestamp, media_id, filename, file_size_bytes, transfer_duration_seconds
| filter event_type = "video_download_complete" 
        and file_size_bytes < 524288000 
        and transfer_duration_seconds > 120
| sort transfer_duration_seconds desc
```

### CloudWatch Metrics

**Custom Metrics** (Namespace: `GoProSync`):

| Metric Name | Unit | Description | Dimensions |
|-------------|------|-------------|------------|
| `VideosSynced` | Count | Videos successfully synced | Provider, Environment |
| `SyncFailures` | Count | Failed sync attempts | Provider, ErrorType, Environment |
| `BytesTransferred` | Bytes | Total bytes transferred | Provider, Environment |
| `TransferDuration` | Seconds | Time to transfer single video | Provider, Environment |
| `TransferThroughput` | Megabits/sec | Network throughput | Provider, Environment |
| `AuthenticationSuccess` | Count | Successful auth attempts | Provider, Environment |
| `AuthenticationFailure` | Count | Failed auth attempts | Provider, Environment |
| `VideosDiscovered` | Count | Total videos found | Provider, Environment |
| `NewVideosFound` | Count | New videos requiring sync | Provider, Environment |

**Built-in Lambda Metrics**:
- `Invocations`: Total function invocations
- `Errors`: Number of errors
- `Duration`: Execution time (p50, p99, max)
- `Throttles`: Number of throttled invocations
- `ConcurrentExecutions`: Concurrent executions
- `IteratorAge`: Age of last record processed (for streams)

**Built-in Step Functions Metrics**:
- `ExecutionTime`: Total execution duration
- `ExecutionsFailed`: Number of failed executions
- `ExecutionsSucceeded`: Number of successful executions
- `ExecutionsTimedOut`: Number of timed out executions

### CloudWatch Alarms

**Alarm Configurations**:

| Alarm Name | Metric | Threshold | Period | Evaluation Periods | Action |
|------------|--------|-----------|--------|-------------------|--------|
| `GoPro-Sync-HighFailureRate` | SyncFailures | > 3 | 5 min | 1 | SNS alert |
| `GoPro-Auth-Failure` | AuthenticationFailure | > 1 | 5 min | 1 | SNS alert |
| `GoPro-Lambda-Errors` | Errors (Lambda) | > 5 | 5 min | 1 | SNS alert |
| `GoPro-Lambda-Throttles` | Throttles (Lambda) | > 1 | 5 min | 1 | SNS alert |
| `GoPro-StepFunction-Failed` | ExecutionsFailed | > 1 | 5 min | 1 | SNS alert |
| `GoPro-DLQ-Messages` | ApproximateNumberOfMessagesVisible (SQS) | > 0 | 5 min | 2 | SNS alert |
| `GoPro-Low-Throughput` | TransferThroughput | < 20 Mbps | 15 min | 2 | SNS alert |

**Alarm Actions**:
- Publish to SNS topic: `gopro-sync-alerts`
- Email subscribers
- Optional: Slack webhook integration

### SNS Notifications

**Topic Configuration**:
- **Topic Name**: `gopro-sync-alerts`
- **Display Name**: "GoPro Sync Alerts"
- **Encryption**: AWS managed key
- **Access Policy**: Restricted to CloudWatch Alarms and Step Functions

**Subscribers**:
- Email: `ops-team@company.com`
- Optional: Slack webhook via Lambda subscription

**Notification Format**:
```json
{
  "AlarmName": "GoPro-Sync-HighFailureRate",
  "AlarmDescription": "More than 3 sync failures detected in 5 minutes",
  "NewStateValue": "ALARM",
  "NewStateReason": "Threshold Crossed: 5 failures in 5 minutes",
  "StateChangeTime": "2025-11-11T02:30:00.000Z",
  "Region": "us-east-1",
  "AlarmArn": "arn:aws:cloudwatch:us-east-1:123456789:alarm:GoPro-Sync-HighFailureRate",
  "Trigger": {
    "MetricName": "SyncFailures",
    "Namespace": "GoProSync",
    "StatisticType": "Statistic",
    "Statistic": "SUM",
    "Period": 300,
    "EvaluationPeriods": 1,
    "ComparisonOperator": "GreaterThanThreshold",
    "Threshold": 3.0
  }
}
```

### CloudWatch Dashboard

**Dashboard Name**: `GoPro-Sync-Operations`

**Widgets**:

1. **Sync Success Rate** (Line graph):
   - Metric: `VideosSynced` vs `SyncFailures`
   - Period: 1 hour
   - Statistic: Sum

2. **Transfer Volume** (Line graph):
   - Metric: `BytesTransferred`
   - Period: 1 hour
   - Statistic: Sum
   - Unit: Gigabytes

3. **Transfer Throughput** (Line graph):
   - Metric: `TransferThroughput`
   - Period: 5 minutes
   - Statistic: Average
   - Unit: Mbps

4. **Lambda Performance** (Multi-line graph):
   - Metrics: Duration (p50, p99, max) for each Lambda
   - Period: 5 minutes

5. **Error Rate** (Stacked area):
   - Metrics: Errors by function
   - Period: 5 minutes
   - Statistic: Sum

6. **Step Functions Executions** (Number):
   - Metrics: ExecutionsSucceeded, ExecutionsFailed
   - Period: 1 day
   - Statistic: Sum

7. **Recent Logs** (Logs widget):
   - Query: Recent errors from all log groups
   - Time range: Last 1 hour

### X-Ray Tracing

**Configuration**:
- Enable X-Ray tracing for all Lambda functions
- Enable X-Ray tracing for Step Functions
- Sampling rate: 100% (all requests traced)

**Trace Analysis**:
- Identify bottlenecks in workflow
- Analyze latency distribution
- Detect anomalies in execution patterns
- Visualize service dependencies

**Enhanced Tracing with Subsegments**:
```python
from aws_xray_sdk.core import xray_recorder

@xray_recorder.capture('download_from_provider')
def download_video(url, auth_token):
    # Create subsegment for provider API call
    subsegment = xray_recorder.begin_subsegment('gopro_api_call')
    subsegment.put_metadata('url', url)
    subsegment.put_annotation('provider', 'gopro')
    
    try:
        response = requests.get(url, headers={'Authorization': f'Bearer {auth_token}'}, stream=True)
        
        # Track Time to First Byte
        ttfb = response.elapsed.total_seconds()
        subsegment.put_metadata('ttfb_seconds', ttfb)
        
        # Publish custom metric
        cloudwatch.put_metric_data(
            Namespace='GoProSync',
            MetricData=[{
                'MetricName': 'TimeToFirstByte',
                'Value': ttfb,
                'Unit': 'Seconds',
                'Dimensions': [{'Name': 'Provider', 'Value': 'gopro'}]
            }]
        )
        
        return response
    finally:
        xray_recorder.end_subsegment()
```

**Correlation IDs**:
```python
# Generate correlation ID at workflow start
correlation_id = str(uuid.uuid4())

# Pass through all Lambda invocations
event['correlation_id'] = correlation_id

# Include in all log messages
logger.info('Processing video', extra={
    'correlation_id': correlation_id,
    'media_id': media_id
})

# Add to X-Ray annotations
xray_recorder.put_annotation('correlation_id', correlation_id)
```

**Service Map**:
```
EventBridge → Step Functions → Lambda (Auth) → Secrets Manager
                            ↓
                            → Lambda (Lister) → GoPro API
                                              → DynamoDB
                            ↓
                            → Lambda (Downloader) → GoPro API
                                                  → S3
                                                  → DynamoDB
                                                  → CloudWatch
```


## Security Architecture

### IAM Roles and Policies

**Role 1: media-authenticator-role**

**Trust Policy**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

**Permissions Policy**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SecretsManagerAccess",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:UpdateSecretValue"
      ],
      "Resource": "arn:aws:secretsmanager:${region}:${account}:secret:gopro/credentials-*"
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:${region}:${account}:log-group:/aws/lambda/media-authenticator:*"
    },
    {
      "Sid": "XRayTracing",
      "Effect": "Allow",
      "Action": [
        "xray:PutTraceSegments",
        "xray:PutTelemetryRecords"
      ],
      "Resource": "*"
    }
  ]
}
```

**Role 2: media-lister-role**

**Permissions Policy**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DynamoDBRead",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:BatchGetItem",
        "dynamodb:Query"
      ],
      "Resource": [
        "arn:aws:dynamodb:${region}:${account}:table/gopro-sync-tracker",
        "arn:aws:dynamodb:${region}:${account}:table/gopro-sync-tracker/index/*"
      ]
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:${region}:${account}:log-group:/aws/lambda/media-lister:*"
    },
    {
      "Sid": "XRayTracing",
      "Effect": "Allow",
      "Action": [
        "xray:PutTraceSegments",
        "xray:PutTelemetryRecords"
      ],
      "Resource": "*"
    }
  ]
}
```

**Role 3: video-downloader-role**

**Permissions Policy**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3Upload",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:PutObjectTagging",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts"
      ],
      "Resource": "arn:aws:s3:::gopro-archive-bucket-${account}/gopro-videos/*"
    },
    {
      "Sid": "DynamoDBWrite",
      "Effect": "Allow",
      "Action": [
        "dynamodb:UpdateItem",
        "dynamodb:PutItem"
      ],
      "Resource": "arn:aws:dynamodb:${region}:${account}:table/gopro-sync-tracker"
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:${region}:${account}:log-group:/aws/lambda/video-downloader:*"
    },
    {
      "Sid": "CloudWatchMetrics",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:PutMetricData"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "cloudwatch:namespace": "GoProSync"
        }
      }
    },
    {
      "Sid": "XRayTracing",
      "Effect": "Allow",
      "Action": [
        "xray:PutTraceSegments",
        "xray:PutTelemetryRecords"
      ],
      "Resource": "*"
    }
  ]
}
```

**Role 4: gopro-sync-orchestrator-role**

**Trust Policy**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "states.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

**Permissions Policy**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "LambdaInvoke",
      "Effect": "Allow",
      "Action": [
        "lambda:InvokeFunction"
      ],
      "Resource": [
        "arn:aws:lambda:${region}:${account}:function:media-authenticator",
        "arn:aws:lambda:${region}:${account}:function:media-lister",
        "arn:aws:lambda:${region}:${account}:function:video-downloader"
      ]
    },
    {
      "Sid": "SNSPublish",
      "Effect": "Allow",
      "Action": [
        "sns:Publish"
      ],
      "Resource": "arn:aws:sns:${region}:${account}:gopro-sync-alerts"
    },
    {
      "Sid": "XRayTracing",
      "Effect": "Allow",
      "Action": [
        "xray:PutTraceSegments",
        "xray:PutTelemetryRecords"
      ],
      "Resource": "*"
    }
  ]
}
```

### Encryption

**Data in Transit**:
- All API calls use HTTPS with TLS 1.2 or higher
- GoPro API connections encrypted
- AWS service-to-service communication encrypted

**Data at Rest**:
- S3 objects encrypted with SSE-KMS using customer-managed key
- DynamoDB encrypted with AWS managed key
- Secrets Manager encrypted with AWS managed key
- CloudWatch Logs encrypted with AWS managed key

**KMS Key Policy** (S3 encryption key):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Enable IAM User Permissions",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::${account}:root"
      },
      "Action": "kms:*",
      "Resource": "*"
    },
    {
      "Sid": "Allow Lambda to use the key",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::${account}:role/video-downloader-role"
      },
      "Action": [
        "kms:Decrypt",
        "kms:GenerateDataKey"
      ],
      "Resource": "*"
    },
    {
      "Sid": "Allow S3 to use the key",
      "Effect": "Allow",
      "Principal": {
        "Service": "s3.amazonaws.com"
      },
      "Action": [
        "kms:Decrypt",
        "kms:GenerateDataKey"
      ],
      "Resource": "*"
    }
  ]
}
```

### Network Security

**VPC Configuration** (Recommended for production):
- Lambda functions deployed in private subnets
- NAT Gateway for outbound internet access (GoPro API)
- VPC Endpoints for AWS services (S3, DynamoDB, Secrets Manager, CloudWatch)
- Security groups restrict traffic to necessary ports

**VPC Architecture**:
```
┌─────────────────────────────────────────────────────────┐
│                         VPC                              │
│                                                          │
│  ┌──────────────────┐         ┌──────────────────┐     │
│  │ Private Subnet A │         │ Private Subnet B │     │
│  │                  │         │                  │     │
│  │  Lambda          │         │  Lambda          │     │
│  │  Functions       │         │  Functions       │     │
│  │                  │         │                  │     │
│  └────────┬─────────┘         └────────┬─────────┘     │
│           │                            │               │
│           └────────────┬───────────────┘               │
│                        │                               │
│  ┌─────────────────────▼──────────────────┐           │
│  │         NAT Gateway                     │           │
│  │  (Outbound to GoPro API)                │           │
│  └─────────────────────┬──────────────────┘           │
│                        │                               │
│  ┌─────────────────────▼──────────────────┐           │
│  │      Internet Gateway                   │           │
│  └─────────────────────────────────────────┘           │
│                                                          │
│  ┌──────────────────────────────────────────┐          │
│  │         VPC Endpoints                     │          │
│  │  - S3 Gateway Endpoint                    │          │
│  │  - DynamoDB Gateway Endpoint              │          │
│  │  - Secrets Manager Interface Endpoint     │          │
│  │  - CloudWatch Logs Interface Endpoint     │          │
│  └──────────────────────────────────────────┘          │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

**Security Group Configuration**:
```python
# Lambda security group
lambda_sg = ec2.SecurityGroup(
    self, "LambdaSecurityGroup",
    vpc=vpc,
    description="Security group for Lambda functions",
    allow_all_outbound=True  # Required for GoPro API access
)

# VPC Endpoint security group
endpoint_sg = ec2.SecurityGroup(
    self, "EndpointSecurityGroup",
    vpc=vpc,
    description="Security group for VPC endpoints"
)

# Allow Lambda to access VPC endpoints
endpoint_sg.add_ingress_rule(
    peer=lambda_sg,
    connection=ec2.Port.tcp(443),
    description="Allow Lambda to access VPC endpoints"
)
```

**Cost Considerations**:
- NAT Gateway: ~$32/month per AZ
- VPC Endpoints: ~$7/month per endpoint
- Data transfer: Reduced costs for S3/DynamoDB via gateway endpoints
- **Recommendation**: Use VPC for production, skip for development to save costs

**S3 Bucket Policy**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyInsecureTransport",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::gopro-archive-bucket-${account}",
        "arn:aws:s3:::gopro-archive-bucket-${account}/*"
      ],
      "Condition": {
        "Bool": {
          "aws:SecureTransport": "false"
        }
      }
    },
    {
      "Sid": "AllowLambdaUpload",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::${account}:role/video-downloader-role"
      },
      "Action": [
        "s3:PutObject",
        "s3:PutObjectTagging"
      ],
      "Resource": "arn:aws:s3:::gopro-archive-bucket-${account}/gopro-videos/*"
    }
  ]
}
```

### Audit and Compliance

**CloudTrail Configuration**:
- Enable CloudTrail for all regions
- Log all management events
- Log S3 data events for archive bucket
- Store logs in separate audit bucket with MFA delete
- Log retention: 7 years

**AWS Config Rules**:
- `s3-bucket-public-read-prohibited`
- `s3-bucket-public-write-prohibited`
- `s3-bucket-server-side-encryption-enabled`
- `lambda-function-public-access-prohibited`
- `dynamodb-table-encrypted-kms`
- `secretsmanager-rotation-enabled-check`

**Compliance Considerations**:
- GDPR: Personal data (credentials) encrypted and access logged
- SOC 2: Audit trail via CloudTrail, access controls via IAM
- HIPAA: Not applicable (no PHI data)


## Deployment and Infrastructure

### Infrastructure as Code

**Tool**: AWS CDK (Cloud Development Kit) with Python

**Rationale**:
- Type-safe infrastructure definitions
- Reusable constructs for common patterns
- Automatic CloudFormation template generation
- Built-in best practices and validations
- Easy to test and version control

**CDK Stack Structure**:
```
cloud-sync-app/
├── app.py                          # CDK app entry point
├── stacks/
│   ├── storage_stack.py            # S3, DynamoDB
│   ├── compute_stack.py            # Lambda functions
│   ├── orchestration_stack.py      # Step Functions
│   ├── monitoring_stack.py         # CloudWatch, SNS
│   └── security_stack.py           # IAM roles, KMS keys
├── lambda/
│   ├── layers/
│   │   └── common/
│   │       └── python/
│   │           ├── providers/
│   │           │   ├── __init__.py
│   │           │   ├── interface.py
│   │           │   ├── gopro.py
│   │           │   └── factory.py
│   │           └── utils/
│   │               ├── __init__.py
│   │               ├── retry.py
│   │               ├── logging.py
│   │               └── metrics.py
│   ├── media_authenticator/
│   │   ├── handler.py
│   │   ├── requirements.txt
│   │   └── tests/
│   ├── media_lister/
│   │   ├── handler.py
│   │   ├── requirements.txt
│   │   └── tests/
│   └── video_downloader/
│       ├── handler.py
│       ├── requirements.txt
│       └── tests/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── chaos/                      # Chaos engineering tests
├── cdk.json
└── requirements.txt
```

**Lambda Layer Benefits**:
- Share common code (provider abstraction, utilities) across functions
- Reduce deployment package size
- Faster deployments (layer cached by Lambda)
- Easier dependency management
- Version control for shared code

**Layer Configuration in CDK**:
```python
from aws_cdk import aws_lambda as lambda_

# Create shared layer
common_layer = lambda_.LayerVersion(
    self, "CommonLayer",
    code=lambda_.Code.from_asset("lambda/layers/common"),
    compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
    description="Shared utilities and provider abstractions"
)

# Attach layer to Lambda functions
media_authenticator = lambda_.Function(
    self, "MediaAuthenticator",
    runtime=lambda_.Runtime.PYTHON_3_12,
    handler="handler.lambda_handler",
    code=lambda_.Code.from_asset("lambda/media_authenticator"),
    layers=[common_layer]  # Attach shared layer
)
```

**Example CDK Stack** (Storage):
```python
from aws_cdk import (
    Stack,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_kms as kms,
    RemovalPolicy,
    Duration
)
from constructs import Construct

class StorageStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # KMS key for S3 encryption
        self.s3_key = kms.Key(
            self, "S3EncryptionKey",
            description="KMS key for GoPro archive bucket encryption",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN
        )
        
        # S3 bucket for video archive
        self.archive_bucket = s3.Bucket(
            self, "ArchiveBucket",
            bucket_name=f"gopro-archive-bucket-{self.account}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.s3_key,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="transition-to-deep-archive",
                    enabled=True,
                    prefix="gopro-videos/",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER_INSTANT_RETRIEVAL,
                            transition_after=Duration.days(7)
                        ),
                        s3.Transition(
                            storage_class=s3.StorageClass.DEEP_ARCHIVE,
                            transition_after=Duration.days(14)
                        )
                    ]
                )
            ],
            removal_policy=RemovalPolicy.RETAIN
        )
        
        # DynamoDB table for sync tracking
        self.sync_tracker = dynamodb.Table(
            self, "SyncTracker",
            table_name="gopro-sync-tracker",
            partition_key=dynamodb.Attribute(
                name="media_id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            removal_policy=RemovalPolicy.RETAIN
        )
        
        # GSI for status queries
        self.sync_tracker.add_global_secondary_index(
            index_name="status-sync_timestamp-index",
            partition_key=dynamodb.Attribute(
                name="status",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="sync_timestamp",
                type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL
        )
```

### Deployment Process

**Environments**:
1. **Development**: For feature development and testing
2. **Staging**: Pre-production environment for integration testing
3. **Production**: Live environment serving real users

**Deployment Pipeline**:
```
1. Code Commit (Git)
   ↓
2. CI/CD Pipeline (GitHub Actions / AWS CodePipeline)
   ├─> Lint code (pylint, black)
   ├─> Run unit tests (pytest)
   ├─> Build Lambda packages
   ├─> CDK synth (generate CloudFormation)
   └─> Security scan (cfn-nag, checkov)
   ↓
3. Deploy to Development
   ├─> CDK deploy --context env=dev
   └─> Run integration tests
   ↓
4. Manual Approval
   ↓
5. Deploy to Staging
   ├─> CDK deploy --context env=staging
   └─> Run smoke tests
   ↓
6. Manual Approval
   ↓
7. Deploy to Production
   ├─> CDK deploy --context env=prod
   └─> Monitor metrics
```

**Deployment Commands**:
```bash
# Install dependencies
pip install -r requirements.txt

# Synthesize CloudFormation template
cdk synth --context env=prod

# Deploy to production
cdk deploy --context env=prod --require-approval never

# Rollback (if needed)
cdk deploy --context env=prod --rollback
```

### Configuration Management

**Environment-Specific Configuration**:
```python
# config.py
ENVIRONMENTS = {
    'dev': {
        'schedule': 'cron(0 3 * * ? *)',  # 3 AM daily
        'max_concurrency': 2,
        'log_level': 'DEBUG',
        'alert_email': 'dev-team@company.com'
    },
    'staging': {
        'schedule': 'cron(0 2 * * ? *)',  # 2 AM daily
        'max_concurrency': 3,
        'log_level': 'INFO',
        'alert_email': 'staging-alerts@company.com'
    },
    'prod': {
        'schedule': 'cron(0 2 * * ? *)',  # 2 AM CET daily
        'max_concurrency': 5,
        'log_level': 'INFO',
        'alert_email': 'ops-team@company.com'
    }
}
```

**Secrets Management**:
- Store GoPro credentials in Secrets Manager per environment
- Secret names: `{env}/gopro/credentials`
- Rotate secrets every 90 days
- Use AWS CLI or Console for initial secret creation

### Cost Estimation

**Monthly Cost Breakdown** (100 GB transfer, 1,000 videos):

| Service | Usage | Unit Cost | Monthly Cost |
|---------|-------|-----------|--------------|
| **Lambda** | | | |
| - Invocations | 3,000 | $0.20/1M | $0.001 |
| - Compute (512MB, 5min avg) | 250 GB-seconds | $0.0000166667/GB-s | $4.17 |
| **Step Functions** | 30 executions | $0.025/1K transitions | $0.15 |
| **S3** | | | |
| - Standard (7 days) | 100 GB × 7/30 | $0.023/GB | $0.54 |
| - Glacier IR (7 days) | 100 GB × 7/30 | $0.004/GB | $0.09 |
| - Deep Archive (16 days) | 100 GB × 16/30 | $0.00099/GB | $0.05 |
| - PUT requests | 1,000 | $0.005/1K | $0.005 |
| **DynamoDB** | | | |
| - Write requests | 3,000 | $1.25/1M | $0.004 |
| - Read requests | 1,000 | $0.25/1M | $0.0003 |
| - Storage | 1 GB | $0.25/GB | $0.25 |
| **Secrets Manager** | 1 secret | $0.40/secret | $0.40 |
| **CloudWatch** | | | |
| - Logs ingestion | 5 GB | $0.50/GB | $2.50 |
| - Metrics | 20 custom | $0.30/metric | $6.00 |
| - Alarms | 7 | $0.10/alarm | $0.70 |
| **SNS** | 10 notifications | $0.50/1M | $0.00001 |
| **Data Transfer** | 100 GB out | $0.09/GB | $9.00 |
| | | **Total** | **$23.87** |

**Cost Optimization Strategies**:
1. Use S3 lifecycle policies to minimize storage costs (95% reduction)
2. Optimize Lambda memory allocation (512 MB balances cost vs performance)
3. Use DynamoDB on-demand billing for unpredictable access patterns
4. Reduce CloudWatch Logs retention to 30 days
5. Minimize custom metrics (only essential metrics)
6. Use S3 Transfer Acceleration only if needed (adds cost)

**Ongoing Monthly Cost** (after initial transfer):
- S3 Deep Archive: $0.10 (100 GB × $0.00099/GB)
- Lambda: $4.17 (daily sync operations)
- Other services: ~$10
- **Total**: ~$14.27/month


## Extensibility for Multiple Providers

### Provider Abstraction Layer

**Design Pattern**: Strategy pattern with provider-specific implementations

**Provider Interface**:
```python
from abc import ABC, abstractmethod
from typing import Dict, List, Any

class CloudProviderInterface(ABC):
    """Abstract base class for cloud provider implementations"""
    
    @abstractmethod
    def authenticate(self, credentials: Dict[str, str]) -> Dict[str, Any]:
        """
        Authenticate with cloud provider API
        
        Args:
            credentials: Provider-specific credentials
            
        Returns:
            Authentication token and metadata
        """
        pass
    
    @abstractmethod
    def list_media(self, auth_token: str, max_items: int = 1000) -> List[Dict[str, Any]]:
        """
        List all media items from cloud provider
        
        Args:
            auth_token: Valid authentication token
            max_items: Maximum number of items to retrieve
            
        Returns:
            List of media items with metadata
        """
        pass
    
    @abstractmethod
    def get_download_url(self, media_id: str, auth_token: str) -> str:
        """
        Get direct download URL for media item
        
        Args:
            media_id: Unique media identifier
            auth_token: Valid authentication token
            
        Returns:
            Direct download URL
        """
        pass
    
    @abstractmethod
    def get_provider_name(self) -> str:
        """Return provider identifier (e.g., 'gopro', 'google', 'dropbox')"""
        pass
```

**GoPro Provider Implementation**:
```python
class GoProProvider(CloudProviderInterface):
    """GoPro Cloud provider implementation"""
    
    BASE_URL = "https://api.gopro.com"
    
    def authenticate(self, credentials: Dict[str, str]) -> Dict[str, Any]:
        # GoPro-specific authentication logic
        response = requests.post(
            f"{self.BASE_URL}/v1/oauth2/token",
            json={
                "username": credentials["username"],
                "password": credentials["password"]
            }
        )
        return {
            "auth_token": response.json()["access_token"],
            "user_id": response.json()["user_id"],
            "expires_at": response.json()["expires_at"]
        }
    
    def list_media(self, auth_token: str, max_items: int = 1000) -> List[Dict[str, Any]]:
        # GoPro-specific media listing logic
        media_items = []
        page = 1
        per_page = 100
        
        while len(media_items) < max_items:
            response = requests.get(
                f"{self.BASE_URL}/media/search",
                headers={"Authorization": f"Bearer {auth_token}"},
                params={"page": page, "per_page": per_page}
            )
            
            items = response.json()["media"]
            if not items:
                break
                
            media_items.extend([
                {
                    "media_id": item["id"],
                    "filename": item["filename"],
                    "file_size": item["file_size"],
                    "upload_date": item["captured_at"],
                    "duration": item["duration"]
                }
                for item in items
            ])
            
            page += 1
        
        return media_items[:max_items]
    
    def get_download_url(self, media_id: str, auth_token: str) -> str:
        # GoPro-specific download URL logic
        response = requests.get(
            f"{self.BASE_URL}/media/{media_id}/download",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        return response.json()["download_url"]
    
    def get_provider_name(self) -> str:
        return "gopro"
```

**Future Provider Example** (Google Drive):
```python
class GoogleDriveProvider(CloudProviderInterface):
    """Google Drive provider implementation"""
    
    BASE_URL = "https://www.googleapis.com/drive/v3"
    
    def authenticate(self, credentials: Dict[str, str]) -> Dict[str, Any]:
        # Google OAuth2 authentication logic
        # Implementation details...
        pass
    
    def list_media(self, auth_token: str, max_items: int = 1000) -> List[Dict[str, Any]]:
        # Google Drive file listing logic
        # Implementation details...
        pass
    
    def get_download_url(self, media_id: str, auth_token: str) -> str:
        # Google Drive download URL logic
        # Implementation details...
        pass
    
    def get_provider_name(self) -> str:
        return "google_drive"
```

### Provider Factory

**Factory Pattern**:
```python
class ProviderFactory:
    """Factory for creating provider instances"""
    
    _providers = {
        "gopro": GoProProvider,
        "google_drive": GoogleDriveProvider,
        # Add more providers here
    }
    
    @classmethod
    def create_provider(cls, provider_name: str) -> CloudProviderInterface:
        """
        Create provider instance by name
        
        Args:
            provider_name: Provider identifier
            
        Returns:
            Provider instance
            
        Raises:
            ValueError: If provider not supported
        """
        provider_class = cls._providers.get(provider_name)
        if not provider_class:
            raise ValueError(f"Unsupported provider: {provider_name}")
        
        return provider_class()
    
    @classmethod
    def register_provider(cls, provider_name: str, provider_class: type):
        """Register new provider implementation"""
        cls._providers[provider_name] = provider_class
    
    @classmethod
    def list_providers(cls) -> List[str]:
        """List all registered providers"""
        return list(cls._providers.keys())
```

### Multi-Provider Lambda Functions

**Updated Media Authenticator**:
```python
def handler(event, context):
    """
    Authenticate with cloud provider
    
    Event structure:
    {
        "provider": "gopro",  # or "google_drive", etc.
        "action": "authenticate"
    }
    """
    provider_name = event["provider"]
    
    # Create provider instance
    provider = ProviderFactory.create_provider(provider_name)
    
    # Retrieve provider-specific credentials
    secret_name = f"{provider_name}/credentials"
    credentials = get_secret(secret_name)
    
    # Authenticate
    auth_result = provider.authenticate(credentials)
    
    return {
        "statusCode": 200,
        "provider": provider_name,
        **auth_result
    }
```

**Updated Media Lister**:
```python
def handler(event, context):
    """
    List media from cloud provider
    
    Event structure:
    {
        "provider": "gopro",
        "auth_token": "...",
        "max_videos": 1000
    }
    """
    provider_name = event["provider"]
    auth_token = event["auth_token"]
    max_videos = event.get("max_videos", 1000)
    
    # Create provider instance
    provider = ProviderFactory.create_provider(provider_name)
    
    # List media
    media_items = provider.list_media(auth_token, max_videos)
    
    # Filter for new videos (check DynamoDB)
    new_videos = filter_new_videos(media_items, provider_name)
    
    return {
        "statusCode": 200,
        "provider": provider_name,
        "new_videos": new_videos,
        "total_found": len(media_items),
        "new_count": len(new_videos)
    }
```

### Multi-Provider Data Model

**Updated DynamoDB Schema**:
```json
{
  "media_id": "abc123def456",
  "provider": "gopro",  // NEW: Provider identifier
  "filename": "GH010456.MP4",
  "s3_key": "gopro-videos/2025/11/GH010456.MP4",  // Provider prefix in key
  "file_size": 524288000,
  "upload_date": "2025-11-10T14:23:45Z",
  "sync_timestamp": "2025-11-11T02:15:32Z",
  "status": "COMPLETED",
  "retry_count": 0,
  "duration_seconds": 180
}
```

**Composite Primary Key** (for multi-provider support):
- **Partition Key**: `provider` (String) - e.g., "gopro", "google_drive"
- **Sort Key**: `media_id` (String) - Provider-specific media ID

**Updated GSI**:
- **Index Name**: `provider-status-sync_timestamp-index`
- **Partition Key**: `provider#status` (String) - e.g., "gopro#FAILED"
- **Sort Key**: `sync_timestamp` (String)

### Multi-Provider S3 Structure

**Updated Folder Structure**:
```
s3://gopro-archive-bucket/
├── gopro-videos/
│   └── 2025/
│       └── 11/
│           └── GH010456.MP4
├── google-drive-videos/
│   └── 2025/
│       └── 11/
│           └── vacation_2025.mp4
└── dropbox-videos/
    └── 2025/
        └── 11/
            └── family_trip.mp4
```

**Provider-Specific Lifecycle Policies**:
```json
{
  "Rules": [
    {
      "Id": "gopro-transition",
      "Status": "Enabled",
      "Filter": {"Prefix": "gopro-videos/"},
      "Transitions": [
        {"Days": 7, "StorageClass": "GLACIER_IR"},
        {"Days": 14, "StorageClass": "DEEP_ARCHIVE"}
      ]
    },
    {
      "Id": "google-drive-transition",
      "Status": "Enabled",
      "Filter": {"Prefix": "google-drive-videos/"},
      "Transitions": [
        {"Days": 30, "StorageClass": "GLACIER_IR"},
        {"Days": 90, "StorageClass": "DEEP_ARCHIVE"}
      ]
    }
  ]
}
```

### Multi-Provider Orchestration

**Updated Step Functions Input**:
```json
{
  "providers": [
    {
      "name": "gopro",
      "enabled": true,
      "max_videos": 1000
    },
    {
      "name": "google_drive",
      "enabled": true,
      "max_videos": 500
    }
  ]
}
```

**Parallel Provider Processing**:
```json
{
  "Comment": "Multi-Provider Cloud Sync",
  "StartAt": "ProcessProviders",
  "States": {
    "ProcessProviders": {
      "Type": "Map",
      "ItemsPath": "$.providers[?(@.enabled==true)]",
      "MaxConcurrency": 3,
      "Iterator": {
        "StartAt": "AuthenticateProvider",
        "States": {
          "AuthenticateProvider": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:${region}:${account}:function:media-authenticator",
            "Parameters": {
              "provider.$": "$$.Map.Item.Value.name",
              "action": "authenticate"
            },
            "Next": "ListMedia"
          },
          "ListMedia": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:${region}:${account}:function:media-lister",
            "Parameters": {
              "provider.$": "$$.Map.Item.Value.name",
              "auth_token.$": "$.auth_token",
              "max_videos.$": "$$.Map.Item.Value.max_videos"
            },
            "Next": "DownloadVideos"
          },
          "DownloadVideos": {
            "Type": "Map",
            "ItemsPath": "$.new_videos",
            "MaxConcurrency": 5,
            "Iterator": {
              "StartAt": "DownloadVideo",
              "States": {
                "DownloadVideo": {
                  "Type": "Task",
                  "Resource": "arn:aws:lambda:${region}:${account}:function:video-downloader",
                  "End": true
                }
              }
            },
            "End": true
          }
        }
      },
      "End": true
    }
  }
}
```

### Adding New Providers

**Steps to Add New Provider**:

1. **Implement Provider Class**:
   - Create new class inheriting from `CloudProviderInterface`
   - Implement all abstract methods
   - Add provider-specific logic

2. **Register Provider**:
   - Add to `ProviderFactory._providers` dictionary
   - Update Lambda function code

3. **Configure Secrets**:
   - Create new secret in Secrets Manager: `{provider}/credentials`
   - Store provider-specific credentials

4. **Update Infrastructure**:
   - Add provider-specific S3 lifecycle rules
   - Update DynamoDB GSI if needed
   - Add provider-specific CloudWatch metrics

5. **Update Orchestration**:
   - Add provider to Step Functions input
   - Configure provider-specific schedule (if different)

6. **Test**:
   - Unit tests for provider implementation
   - Integration tests for end-to-end flow
   - Load tests for performance validation

**Example: Adding Dropbox Provider**:
```python
# 1. Implement provider
class DropboxProvider(CloudProviderInterface):
    # Implementation...
    pass

# 2. Register provider
ProviderFactory.register_provider("dropbox", DropboxProvider)

# 3. Configure secrets (AWS CLI)
aws secretsmanager create-secret \
    --name dropbox/credentials \
    --secret-string '{"access_token": "..."}'

# 4. Update CDK stack
self.archive_bucket.add_lifecycle_rule(
    id="dropbox-transition",
    prefix="dropbox-videos/",
    transitions=[
        s3.Transition(
            storage_class=s3.StorageClass.GLACIER_INSTANT_RETRIEVAL,
            transition_after=Duration.days(7)
        )
    ]
)

# 5. Update Step Functions input
{
  "providers": [
    {"name": "gopro", "enabled": true},
    {"name": "google_drive", "enabled": true},
    {"name": "dropbox", "enabled": true}  # NEW
  ]
}
```

## Future Enhancements

### Phase 2 Features

1. **Incremental Sync**:
   - Track last sync timestamp per provider
   - Only query for videos uploaded after last sync
   - Reduce API calls and processing time

2. **Selective Sync**:
   - User-defined filters (date range, file size, tags)
   - Exclude specific folders or file types
   - Priority-based sync (recent videos first)

3. **Bidirectional Sync**:
   - Upload videos from S3 back to cloud provider
   - Sync metadata changes (tags, descriptions)
   - Conflict resolution for concurrent edits

4. **Advanced Monitoring**:
   - Real-time dashboard with live metrics
   - Predictive alerts based on trends
   - Cost optimization recommendations

5. **Multi-Region Support**:
   - Replicate videos across AWS regions
   - Disaster recovery with cross-region replication
   - Geo-distributed access for global users

6. **Video Processing**:
   - Automatic thumbnail generation
   - Video transcoding for different formats
   - Metadata extraction (GPS, timestamps)
   - Content analysis (object detection, scene recognition)

### Scalability Improvements

1. **Parallel Provider Processing**:
   - Process multiple providers simultaneously
   - Independent failure handling per provider
   - Provider-specific rate limiting

2. **Batch Processing**:
   - Group small files for efficient transfer
   - Optimize S3 multipart upload chunk size
   - Reduce API call overhead

3. **Caching Layer**:
   - Cache provider API responses (media lists)
   - Reduce redundant API calls
   - Improve performance for large libraries

4. **Event-Driven Architecture**:
   - Trigger sync on new video upload (webhook)
   - Real-time sync instead of scheduled
   - Reduce latency for critical videos

