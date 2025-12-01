# GoPro Cloud to S3 Archive System
## Requirements & Design Specification Document

**Version:** 1.0  
**Date:** November 12, 2025  
**Project Code:** GOPRO-S3-SYNC  
**Document Owner:** Engineering Team  

***

## Executive Summary

This document defines the requirements and technical design for an automated, serverless system that synchronizes video content from GoPro Cloud to AWS S3 low-cost storage (Glacier Deep Archive) [1][2]. The solution leverages AWS serverless technologies to provide a cost-effective, scalable, and maintainable architecture aligned with the AWS Well-Architected Framework principles [2][3].

**Key Objectives:**
- Automate video backup from GoPro Cloud to S3 without manual intervention
- Minimize storage costs using S3 Glacier Deep Archive ($0.00099/GB/month)
- Ensure data durability and prevent duplicate transfers
- Provide visibility into sync operations and failures

**Expected Benefits:**
- 95% reduction in storage costs compared to standard cloud storage
- Zero manual effort for ongoing backups
- Automatic handling of failures and retries
- Complete audit trail of all transfers

***

## Business Requirements

### BR-1: Automated Synchronization
**Priority:** P0  
**Description:** System must automatically discover and transfer new videos from GoPro Cloud to S3 on a scheduled basis without manual intervention [4][5].

**Acceptance Criteria:**
- Daily synchronization runs at configurable time (default: 2 AM CET)
- System identifies only new/unsynced videos to avoid duplicate transfers
- Successful transfers are tracked to prevent re-downloading

### BR-2: Cost Optimization
**Priority:** P0  
**Description:** System must minimize storage costs while maintaining data durability and availability [1][2].

**Acceptance Criteria:**
- Videos stored in S3 Glacier Deep Archive after 30-day transition period
- Total monthly cost for 100GB transfer < $6
- No unnecessary data duplication or redundant API calls

### BR-3: Data Integrity
**Priority:** P0  
**Description:** All transferred videos must maintain integrity with source files [6][7].

**Acceptance Criteria:**
- File size matches between source and destination
- No corrupted or incomplete transfers
- Failed transfers automatically retried with exponential backoff

### BR-4: Operational Visibility
**Priority:** P1  
**Description:** Operations team must have visibility into system health and transfer status [8][9].

**Acceptance Criteria:**
- CloudWatch dashboard showing key metrics (success rate, transfer volume, failures)
- Automated alerts for consecutive failures
- Structured logs for troubleshooting

***

## Functional Requirements

### FR-1: GoPro Authentication & Authorization
**Priority:** P0  
**Description:** System must authenticate with GoPro Cloud API using secure credential management [4][10].

**Specifications:**
- Store GoPro credentials (JWT token, user ID) in AWS Secrets Manager
- Implement token refresh logic for expired sessions (24-hour expiry)
- Rotate secrets automatically every 90 days
- Support manual credential update via secure process

**Input:** GoPro username/password or pre-extracted JWT token  
**Output:** Valid authentication headers for API calls  
**Error Handling:** Retry authentication up to 3 times; send SNS alert on persistent failure

### FR-2: Media Discovery & Filtering
**Priority:** P0  
**Description:** System must query GoPro API to list all videos and filter for unsynced content [4][7].

**Specifications:**
- Paginate through GoPro media library (100 items per page)
- Query DynamoDB to identify videos not yet synced
- Support filtering by date range (optional future enhancement)
- Extract metadata: video ID, filename, size, upload date, duration

**Input:** GoPro API credentials, last sync timestamp  
**Output:** List of video objects requiring transfer  
**Performance:** Complete discovery for 10,000 videos in < 2 minutes

### FR-3: Video Download & Upload
**Priority:** P0  
**Description:** System must stream videos from GoPro Cloud directly to S3 without local storage [11][12].

**Specifications:**
- Use streaming transfer to avoid Lambda memory limits (max 10 GB)
- Implement S3 multipart upload for files > 100 MB (50 MB chunk size)
- Set initial storage class to S3 Standard for validation period
- Generate S3 object key: `gopro-videos/{year}/{month}/{filename}`

**Input:** GoPro video download URL, authentication headers  
**Output:** S3 object ARN, transfer duration, bytes transferred  
**Performance:** Transfer throughput > 50 Mbps per Lambda execution

### FR-4: Transfer State Management
**Priority:** P0  
**Description:** System must track transfer status to prevent duplicates and enable recovery [7][13].

**Specifications:**
- Record sync status in DynamoDB for each video
- Store metadata: media_id (PK), filename, s3_key, sync_timestamp, file_size, status
- Support status values: PENDING, IN_PROGRESS, COMPLETED, FAILED
- Enable idempotent retries (safe to re-run for same video)

**Input:** Video metadata from GoPro API  
**Output:** DynamoDB item with sync status  
**Consistency:** Strong consistency for status reads to prevent race conditions

### FR-5: Storage Lifecycle Management
**Priority:** P1  
**Description:** System must automatically transition videos to low-cost storage classes [1][2].

**Specifications:**
- S3 lifecycle policy: Standard (7 days) → Glacier Instant Retrieval (7 days) → Deep Archive (after 14 days)
- Apply policy to prefix: `gopro-videos/`
- Preserve metadata and tags during transitions
- Support manual override for specific objects

**Input:** S3 object creation event  
**Output:** Automatic transition based on age  
**Cost Impact:** Reduces monthly storage cost by 95% after 14 days

### FR-6: Workflow Orchestration
**Priority:** P0  
**Description:** System must coordinate multi-step sync process with error handling [5][14][15].

**Specifications:**
- Step Functions state machine orchestrates: Authenticate → List Media → Filter New → Download → Update Status
- Support parallel downloads (max 5 concurrent) using Map state
- Implement retry logic with exponential backoff per state
- Catch and handle errors gracefully with appropriate fallback

**Input:** EventBridge scheduled trigger  
**Output:** Execution summary (videos synced, bytes transferred, failures)  
**Performance:** Complete 100 video sync in < 30 minutes

***

## Non-Functional Requirements

### NFR-1: Reliability
**Priority:** P0  
**Description:** System must achieve 99.5% successful sync rate over 30-day period [2][16].

**Specifications:**
- Automatic retry for transient failures (3 attempts with exponential backoff)
- Dead letter queue for persistent failures requiring investigation
- State machine timeout: 2 hours for full sync workflow
- Lambda function timeout: 15 minutes for video download

### NFR-2: Security
**Priority:** P0  
**Description:** System must follow AWS security best practices and least privilege access [17][18][19].

**Specifications:**
- IAM roles with least privilege permissions (no wildcard actions)
- Encrypt data in transit (HTTPS) and at rest (S3 SSE-KMS)
- Store secrets in Secrets Manager with automatic rotation
- Enable CloudTrail logging for all API calls
- S3 bucket: block public access, versioning enabled

### NFR-3: Cost Efficiency
**Priority:** P1  
**Description:** System must operate within defined cost constraints [20][2].

**Specifications:**
- Monthly operating cost (excluding storage): < $5 for 100 GB transfer
- Lambda memory: 512 MB (balance cost vs performance)
- DynamoDB: On-demand billing for unpredictable access patterns
- S3: Use lifecycle policies to minimize storage costs

**Target Costs (100 GB/month):**
- Lambda: $2.50
- Step Functions: $0.10
- DynamoDB: $0.50
- S3 (transition period): $2.30
- S3 Deep Archive (ongoing): $0.10/month

### NFR-4: Observability
**Priority:** P1  
**Description:** System must provide comprehensive monitoring and alerting [8][9].

**Specifications:**
- CloudWatch Logs: Structured JSON logging with correlation IDs
- CloudWatch Metrics: Custom metrics for sync success rate, transfer volume, duration
- CloudWatch Dashboard: Real-time visualization of key metrics
- SNS alerts: Trigger on 3 consecutive failures or auth errors
- Log retention: 30 days for operational logs, 1 year for audit logs

### NFR-5: Maintainability
**Priority:** P1  
**Description:** System must be easy to understand, debug, and modify [2][21].

**Specifications:**
- Infrastructure as Code using AWS CDK or CloudFormation
- Modular Lambda functions (single responsibility principle)
- Comprehensive inline documentation and README
- Version control all code and configurations in Git
- Enable X-Ray tracing for distributed request tracking

### NFR-6: Scalability
**Priority:** P2  
**Description:** System must scale to handle increasing video volume [2][3].

**Specifications:**
- Support up to 1,000 videos per sync run without modification
- Lambda concurrent execution limit: 10 (adjustable)
- DynamoDB autoscaling enabled for read/write capacity
- S3 unlimited storage capacity
- Step Functions max 5 concurrent downloads (configurable)

***

## System Architecture

### Architecture Diagram Overview

```
┌─────────────────┐
│ EventBridge     │ (Daily 2 AM CET)
│ Scheduler       │
└────────┬────────┘
         │ triggers
         ▼
┌─────────────────────────────────────┐
│  Step Functions State Machine       │
│  (gopro-sync-orchestrator)          │
└─────────────────────────────────────┘
         │
         ├─► Lambda: gopro-authenticator
         │   └─► Secrets Manager (credentials)
         │
         ├─► Lambda: gopro-media-lister
         │   └─► GoPro API
         │   └─► DynamoDB: gopro-sync-tracker (read)
         │
         ├─► Step Functions Map State (parallel)
         │   │
         │   ├─► Lambda: gopro-video-downloader (x5)
         │   │   ├─► GoPro API (stream video)
         │   │   └─► S3: gopro-archive-bucket
         │   │
         │   └─► DynamoDB: gopro-sync-tracker (write)
         │
         └─► SNS: gopro-sync-alerts (on failure)

┌─────────────────┐
│ S3 Lifecycle    │ (Automatic transitions)
│ Policy          │
└────────┬────────┘
         │
         ▼
Standard (7d) → Glacier IR (7d) → Deep Archive
```

### AWS Services Used

| Service | Purpose | Justification |
|---------|---------|---------------|
| **EventBridge** | Schedule daily sync execution | Serverless, reliable cron-like scheduling [5] |
| **Step Functions** | Orchestrate multi-step workflow | Visual workflow, built-in error handling, retry logic [14][15] |
| **Lambda** | Execute sync logic (auth, list, download) | Serverless compute, automatic scaling, pay-per-use [16][22] |
| **S3** | Store videos in cost-optimized tiers | Durable, scalable, lifecycle management [1][12] |
| **DynamoDB** | Track sync state and prevent duplicates | Single-digit ms latency, flexible schema [7][13] |
| **Secrets Manager** | Store GoPro credentials securely | Automatic rotation, encryption at rest [17][18] |
| **CloudWatch** | Logging, metrics, and alerting | Centralized observability [8][9] |
| **SNS** | Send failure notifications | Simple pub/sub for alerts [16] |
| **IAM** | Fine-grained access control | Least privilege security [17][19] |

***

## Component Design

### Component 1: gopro-authenticator (Lambda)

**Responsibility:** Authenticate with GoPro Cloud API and return valid session tokens [4][10].

**Runtime:** Python 3.12  
**Memory:** 256 MB  
**Timeout:** 30 seconds  
**Concurrency:** 1 (serial authentication)

**Input (Event):**
```json
{
  "action": "authenticate"
}
```

**Output:**
```json
{
  "statusCode": 200,
  "auth_token": "eyJhbGc...",
  "user_id": "12345678",
  "expires_at": "2025-11-13T02:00:00Z"
}
```

**Logic:**
1. Retrieve credentials from Secrets Manager: `gopro/credentials`
2. If token exists and not expired (< 24 hours old), return cached token
3. If expired or missing, authenticate using login API or manual token
4. Store new token in Secrets Manager with timestamp
5. Return authentication headers for downstream functions

**Error Scenarios:**
- Invalid credentials → Return 401, trigger SNS alert
- Network timeout → Retry 3 times with exponential backoff
- Secrets Manager unavailable → Return 500, fail execution

**IAM Permissions:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:UpdateSecretValue"
      ],
      "Resource": "arn:aws:secretsmanager:region:account:secret:gopro/credentials-*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:region:account:log-group:/aws/lambda/gopro-authenticator:*"
    }
  ]
}
```

### Component 2: gopro-media-lister (Lambda)

**Responsibility:** Query GoPro API for video list and filter for unsynced content [4][7].

**Runtime:** Python 3.12  
**Memory:** 512 MB  
**Timeout:** 5 minutes  
**Concurrency:** 1

**Input (Event):**
```json
{
  "auth_token": "eyJhbGc...",
  "user_id": "12345678",
  "max_videos": 1000
}
```

**Output:**
```json
{
  "statusCode": 200,
  "new_videos": [
    {
      "media_id": "abc123",
      "filename": "GH010456.MP4",
      "download_url": "https://...",
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

**Logic:**
1. Call GoPro API: `GET /media/search` with pagination (100 per page)
2. Extract video metadata: ID, filename, size, upload date
3. For each video, query DynamoDB table `gopro-sync-tracker` with PK `media_id`
4. Filter videos where DynamoDB item doesn't exist or status != COMPLETED
5. Return list of new videos requiring sync
6. Log summary: total videos found, new videos, already synced

**Error Scenarios:**
- GoPro API rate limit → Exponential backoff, retry after delay
- DynamoDB throttling → Enable autoscaling, retry with jitter
- Large result set (>1000 videos) → Process first 1000, log warning

**IAM Permissions:**
```json
{
  "Effect": "Allow",
  "Action": [
    "dynamodb:GetItem",
    "dynamodb:BatchGetItem"
  ],
  "Resource": "arn:aws:dynamodb:region:account:table/gopro-sync-tracker"
}
```

### Component 3: gopro-video-downloader (Lambda)

**Responsibility:** Stream video from GoPro Cloud to S3 using multipart upload [11][12].

**Runtime:** Python 3.12  
**Memory:** 512 MB  
**Timeout:** 15 minutes  
**Concurrency:** 5 (parallel downloads)

**Input (Event):**
```json
{
  "media_id": "abc123",
  "filename": "GH010456.MP4",
  "download_url": "https://...",
  "file_size": 524288000,
  "auth_token": "eyJhbGc...",
  "s3_bucket": "gopro-archive-bucket",
  "s3_prefix": "gopro-videos/2025/11/"
}
```

**Output:**
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

**Logic:**
1. Mark video status as IN_PROGRESS in DynamoDB
2. Open HTTP stream to GoPro download URL with auth headers
3. If file_size > 100 MB, initiate S3 multipart upload; else direct upload
4. Stream chunks (50 MB) from GoPro directly to S3 (no disk buffering)
5. Verify bytes transferred matches file_size
6. Update DynamoDB status to COMPLETED with S3 key and metadata
7. Return transfer statistics

**Multipart Upload Logic:**
```python
def multipart_upload_stream(s3_client, response, bucket, key):
    multipart = s3_client.create_multipart_upload(
        Bucket=bucket,
        Key=key,
        StorageClass='STANDARD',
        Tagging='Source=GoPro&AutoSync=True'
    )
    
    upload_id = multipart['UploadId']
    parts = []
    part_number = 1
    chunk_size = 50 * 1024 * 1024  # 50 MB
    
    try:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                part = s3_client.upload_part(
                    Bucket=bucket,
                    Key=key,
                    PartNumber=part_number,
                    UploadId=upload_id,
                    Body=chunk
                )
                parts.append({
                    'PartNumber': part_number,
                    'ETag': part['ETag']
                })
                part_number += 1
                logger.info(f"Uploaded part {part_number}, ETag: {part['ETag']}")
        
        # Complete multipart upload
        s3_client.complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={'Parts': parts}
        )
        
        return {'status': 'success', 'parts': len(parts)}
        
    except Exception as e:
        logger.error(f"Multipart upload failed: {str(e)}")
        s3_client.abort_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id
        )
        raise e
```

**Error Scenarios:**
- Network interruption during transfer → Abort multipart upload, mark FAILED, retry entire video
- S3 API error → Retry 3 times with exponential backoff
- File size mismatch → Mark FAILED, send alert, manual investigation required
- Lambda timeout (15 min) → Large files may timeout, consider increasing memory for faster network

**IAM Permissions:**
```json
{
  "Effect": "Allow",
  "Action": [
    "s3:PutObject",
    "s3:PutObjectTagging",
    "s3:AbortMultipartUpload"
  ],
  "Resource": "arn:aws:s3:::gopro-archive-bucket/gopro-videos/*"
},
{
  "Effect": "Allow",
  "Action": [
    "dynamodb:UpdateItem"
  ],
  "Resource": "arn:aws:dynamodb:region:account:table/gopro-sync-tracker"
}
```

***

## Data Models

### DynamoDB Table: gopro-sync-tracker

**Purpose:** Track sync status for each video to prevent duplicates and enable recovery [7][13].

**Table Configuration:**
- **Table Name:** `gopro-sync-tracker`
- **Billing Mode:** On-Demand (unpredictable access patterns)
- **Partition Key:** `media_id` (String) - GoPro video unique identifier
- **Sort Key:** None (single item per video)
- **Attributes:**

| Attribute | Type | Description | Example |
|-----------|------|-------------|---------|
| `media_id` | String (PK) | GoPro video unique ID | `"abc123def456"` |
| `filename` | String | Original GoPro filename | `"GH010456.MP4"` |
| `s3_key` | String | S3 object key after upload | `"gopro-videos/2025/11/GH010456.MP4"` |
| `file_size` | Number | Video file size in bytes | `524288000` |
| `upload_date` | String (ISO8601) | Date video uploaded to GoPro Cloud | `"2025-11-10T14:23:45Z"` |
| `sync_timestamp` | String (ISO8601) | Date/time video synced to S3 | `"2025-11-11T02:15:32Z"` |
| `status` | String | Sync status enum | `"COMPLETED"` |
| `retry_count` | Number | Number of retry attempts | `0` |
| `error_message` | String | Last error message if failed | `"Network timeout"` |
| `duration_seconds` | Number | Video duration | `180` |
| `ttl` | Number | Item expiration timestamp (optional) | `1736636400` |

**Status Values:**
- `PENDING`: Video identified, not yet started
- `IN_PROGRESS`: Download/upload in progress
- `COMPLETED`: Successfully synced to S3
- `FAILED`: Transfer failed after retries

**Indexes:**
- **GSI-1:** `status-sync_timestamp-index`
  - Partition Key: `status`
  - Sort Key: `sync_timestamp`
  - Purpose: Query videos by status, sorted by sync time (e.g., recent failures)

**Access Patterns:**
1. Check if video already synced: `GetItem(media_id)`
2. Mark video as IN_PROGRESS: `UpdateItem(media_id, status=IN_PROGRESS)`
3. Complete sync: `UpdateItem(media_id, status=COMPLETED, s3_key, sync_timestamp)`
4. Query recent failures: `Query(GSI-1, status=FAILED, sort by sync_timestamp DESC)`

**DynamoDB Example Item:**
```json
{
  "media_id": "abc123def456",
  "filename": "GH010456.MP4",
  "s3_key": "gopro-videos/2025/11/GH010456.MP4",
  "file_size": 524288000,
  "upload_date": "2025-11-10T14:23:45Z",
  "sync_timestamp": "2025-11-11T02:15:32Z",
  "status": "COMPLETED",
  "retry_count": 0,
  "duration_seconds": 180
}
```

### S3 Bucket: gopro-archive-bucket

**Configuration:**
- **Bucket Name:** `gopro-archive-bucket-{account-id}`
- **Region:** Same as Lambda functions (minimize data transfer costs)
- **Versioning:** Enabled (protect against accidental deletion)
- **Encryption:** SSE-KMS with customer-managed key
- **Block Public Access:** All settings enabled
- **Object Lock:** Disabled (not required for this use case)

**Folder Structure:**
```
s3://gopro-archive-bucket/
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

**S3 Lifecycle Policy:**
```json
{
  "Rules": [
    {
      "Id": "transition-to-glacier",
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

**Object Tagging:**
- `Source=GoPro`: Identifies source system
- `AutoSync=True`: Indicates automatic sync
- `UploadDate=YYYY-MM-DD`: Original upload date to GoPro Cloud

***

## Step Functions State Machine

**State Machine Name:** `gopro-sync-orchestrator`

**Definition (ASL):**
```json
{
  "Comment": "GoPro Cloud to S3 Sync Orchestration",
  "StartAt": "AuthenticateGoPro",
  "TimeoutSeconds": 7200,
  "States": {
    "AuthenticateGoPro": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:region:account:function:gopro-authenticator",
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
          "Next": "NotifyFailure"
        }
      ],
      "Next": "ListMedia"
    },
    "ListMedia": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:region:account:function:gopro-media-lister",
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
          "Next": "NotifyFailure"
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
      "Iterator": {
        "StartAt": "DownloadVideo",
        "States": {
          "DownloadVideo": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:region:account:function:gopro-video-downloader",
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
            "Next": "MarkVideoComplete"
          },
          "MarkVideoComplete": {
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
        "total_videos": "$.media.new_count",
        "successful_downloads": "$.download_results[?(@.statusCode==200)]",
        "failed_downloads": "$.download_results[?(@.statusCode!=200)]",
        "execution_time_seconds.$": "$$.State.EnteredTime"
      },
      "ResultPath": "$.summary",
      "Next": "CheckForFailures"
    },
    "CheckForFailures": {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.summary.failed_downloads",
          "IsPresent": true,
          "Next": "NotifyPartialFailure"
        }
      ],
      "Default": "SyncComplete"
    },
    "NotifyPartialFailure": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sns:publish",
      "Parameters": {
        "TopicArn": "arn:aws:sns:region:account:gopro-sync-alerts",
        "Subject": "GoPro Sync Partial Failure",
        "Message.$": "$.summary"
      },
      "Next": "SyncComplete"
    },
    "NoNewVideos": {
      "Type": "Succeed",
      "Comment": "No new videos to sync"
    },
    "NotifyFailure": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sns:publish",
      "Parameters": {
        "TopicArn": "arn:aws:sns:region:account:gopro-sync-alerts",
        "Subject": "GoPro Sync Critical Failure",
        "Message.$": "$.error"
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

**Retry Strategy per State [15][23]:**

| State | Error Type | Interval | Max Attempts | Backoff Rate |
|-------|------------|----------|--------------|--------------|
| AuthenticateGoPro | Lambda.ServiceException | 2s | 3 | 2.0 |
| ListMedia | Lambda.ServiceException | 2s | 3 | 2.0 |
| DownloadVideo | NetworkError, TimeoutError | 30s | 3 | 2.0 |

**Backoff Calculation Example:**
- Attempt 1: Wait 30 seconds
- Attempt 2: Wait 60 seconds (30 × 2.0)
- Attempt 3: Wait 120 seconds (60 × 2.0)

***

## Error Handling & Monitoring

### Error Handling Strategy

**Error Categories [16][15]:**

1. **Transient Errors** (retryable):
   - Network timeouts
   - HTTP 5xx from GoPro API
   - Lambda throttling
   - DynamoDB throttling
   - **Action:** Retry with exponential backoff (max 3 attempts)

2. **Permanent Errors** (non-retryable):
   - Authentication failure (401)
   - Invalid credentials
   - File not found (404)
   - **Action:** Mark as FAILED, send alert, no retry

3. **Partial Failures**:
   - Some videos succeed, some fail in Map state
   - **Action:** Log failures, continue processing remaining, send summary alert

**Dead Letter Queue:**
- SQS DLQ attached to each Lambda function
- Failed invocations sent to DLQ after exhausting retries
- DLQ retention: 14 days
- Manual investigation required for DLQ messages

### CloudWatch Logging [8][9]

**Log Format (Structured JSON):**
```json
{
  "timestamp": "2025-11-11T02:15:32.123Z",
  "level": "INFO",
  "correlation_id": "abc-123-def",
  "function_name": "gopro-video-downloader",
  "event_type": "video_download_start",
  "media_id": "abc123",
  "filename": "GH010456.MP4",
  "file_size_bytes": 524288000,
  "s3_bucket": "gopro-archive-bucket",
  "s3_key": "gopro-videos/2025/11/GH010456.MP4"
}
```

**Log Levels:**
- **DEBUG**: Detailed diagnostic information (disabled in production)
- **INFO**: General informational messages (function entry/exit, success events)
- **WARN**: Warning conditions (retry attempts, degraded performance)
- **ERROR**: Error events (failures requiring attention)

**CloudWatch Logs Insights Queries:**

**Query 1: Find all failed downloads in last 24 hours**
```
fields @timestamp, media_id, filename, error_message
| filter level = "ERROR" and event_type = "video_download_failed"
| sort @timestamp desc
| limit 100
```

**Query 2: Calculate average transfer throughput**
```
fields media_id, bytes_transferred, transfer_duration_seconds, (bytes_transferred / transfer_duration_seconds / 1048576) as throughput_mbps
| filter event_type = "video_download_complete"
| stats avg(throughput_mbps) as avg_throughput, max(throughput_mbps) as max_throughput, min(throughput_mbps) as min_throughput
```

**Query 3: Identify slow transfers (>2 minutes for <500MB)**
```
fields @timestamp, media_id, filename, file_size_bytes, transfer_duration_seconds
| filter event_type = "video_download_complete" and file_size_bytes < 524288000 and transfer_duration_seconds > 120
| sort transfer_duration_seconds desc
```

### CloudWatch Metrics

**Custom Metrics (published from Lambda):**

| Metric Name | Unit | Description | Dimensions |
|-------------|------|-------------|------------|
| `VideosSynced` | Count | Number of videos successfully synced | Environment |
| `SyncFailures` | Count | Number of failed sync attempts | Environment, ErrorType |
| `BytesTransferred` | Bytes | Total bytes transferred to S3 | Environment |
| `TransferDuration` | Seconds | Time to transfer single video | Environment |
| `TransferThroughput` | Megabits/sec | Network throughput | Environment |
| `AuthenticationSuccess` | Count | Successful auth attempts | Environment |
| `AuthenticationFailure` | Count | Failed auth attempts | Environment |

**Built-in Lambda Metrics:**
- `Invocations`: Total function invocations
- `Errors`: Number of errors
- `Duration`: Execution time
- `Throttles`: Number of throttled invocations
- `ConcurrentExecutions`: Concurrent executions

### CloudWatch Alarms

**Alarm Configurations:**

| Alarm Name | Metric | Threshold | Period | Action |
|------------|--------|-----------|--------|--------|
| `GoPro-Sync-HighFailureRate` | SyncFailures | > 3 failures | 5 minutes | SNS alert |
| `GoPro-Auth-Failure` | AuthenticationFailure | > 1 failure | 5 minutes | SNS alert |
| `GoPro-Lambda-Errors` | Errors | > 5 errors | 5 minutes | SNS alert |
| `GoPro-Lambda-Throttles` | Throttles | > 1 throttle | 5 minutes | SNS alert |
| `GoPro-StepFunction-Failed` | ExecutionsFailed | > 1 failure | 5 minutes | SNS alert |

### SNS Notification Topics

**Topic:** `gopro-sync-alerts`  
**Subscribers:**
- Email: `ops-team@company.com`
- Slack webhook (optional): Post to #gopro-sync-alerts channel

**Notification Format:**
```json
{
  "AlarmName": "GoPro-Sync-HighFailureRate",
  "NewStateValue": "ALARM",
  "Timestamp": "2025-11-11T02:30:00Z",
  "AlarmDescription": "More than 3 sync failures detected",
  "StateChangeReason": "Threshold Crossed: 5 failures in 5 minutes",
  "Region": "us-east-1",
  "ActionDetails": "Investigate recent executions in Step Functions console"
}
```

***

## Security & IAM Policies

### Security Requirements [17][18][19]

1. **Least Privilege Access**: Each component granted only minimum required permissions
2. **Encryption**: Data encrypted in transit (HTTPS/TLS) and at rest (KMS)
3. **Secrets Management**: No hardcoded credentials; use Secrets Manager
4. **Network Security**: Lambda functions in VPC (optional for enhanced security)
5. **Audit Logging**: CloudTrail enabled for all API calls

### IAM Role: gopro-authenticator-role

**Trust Policy:**
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

**Permissions Policy:**
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
      "Resource": "arn:aws:secretsmanager:region:account:secret:gopro/credentials-*"
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:region:account:log-group:/aws/lambda/gopro-authenticator:*"
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

### IAM Role: gopro-media-lister-role

**Permissions Policy:**
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
        "arn:aws:dynamodb:region:account:table/gopro-sync-tracker",
        "arn:aws:dynamodb:region:account:table/gopro-sync-tracker/index/*"
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
      "Resource": "arn:aws:logs:region:account:log-group:/aws/lambda/gopro-media-lister:*"
    }
  ]
}
```

### IAM Role: gopro-video-downloader-role

**Permissions Policy:**
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
      "Resource": "arn:aws:s3:::gopro-archive-bucket/gopro-videos/*"
    },
    {
      "Sid": "DynamoDBWrite",
      "Effect": "Allow",
      "Action": [
        "dynamodb:UpdateItem",
        "dynamodb:PutItem"
      ],
      "Resource": "arn:aws:dynamodb:region:account:table/gopro-sync-tracker"
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:region:account:log-group:/aws/lambda/gopro-video-downloader:*"
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
    }
  ]
}
```

### IAM Role: gopro-stepfunctions-role

**Permissions Policy:**
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
        "arn:aws:

Sources
[1] Amazon S3 Glacier storage classes https://aws.amazon.com/s3/storage-classes/glacier/
[2] AWS Well-Architected Framework https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html
[3] The Definitive Guide to the AWS Well-Architected Framework https://www.hyperglance.com/blog/aws-well-architected/
[4] itsankoff/gopro-plus - GitHub https://github.com/itsankoff/gopro-plus
[5] Ingest Step Functions - Video on Demand on AWS https://docs.aws.amazon.com/solutions/latest/video-on-demand-on-aws/ingest-step-functions.html
[6] Configuring AWS DataSync transfers with Amazon S3 https://docs.aws.amazon.com/datasync/latest/userguide/create-s3-location.html
[7] Amazon DynamoDB use cases for media and entertainment ... https://aws.amazon.com/blogs/database/amazon-dynamodb-use-cases-for-media-and-entertainment-customers/
[8] Searching across logs with CloudWatch Logs Insights in AWS lambda https://www.getorchestra.io/guides/searching-across-logs-with-cloudwatch-logs-insights-in-aws-lambda-useful-insights-queries
[9] Enhanced Observability With Cloudwatch Insights and Lambda ... https://www.linkedin.com/pulse/enhanced-observability-cloudwatch-insights-lambda-advanced-dzsec
[10] Gopro Cloud API : r/gopro - Reddit https://www.reddit.com/r/gopro/comments/12cjv5x/gopro_cloud_api/
[11] How to download a large file stored in aws s3 from ... https://stackoverflow.com/questions/75512338/how-to-download-a-large-file-stored-in-aws-s3-from-a-lambda-function-in-a-stream
[12] Uploading large objects to Amazon S3 using multipart ... https://aws.amazon.com/blogs/compute/uploading-large-objects-to-amazon-s3-using-multipart-upload-and-transfer-acceleration/
[13] The Ten Rules for Data Modeling with DynamoDB - Trek10 https://www.trek10.com/blog/the-ten-rules-for-data-modeling-with-dynamodb
[14] How the solution works - Video on Demand on AWS https://docs.aws.amazon.com/solutions/latest/video-on-demand-on-aws/how-the-solution-works.html
[15] Handling Errors and Retries in StepFunctions https://www.tecracer.com/blog/2023/08/handling-errors-and-retries-in-stepfunctions.html
[16] Understanding retry behavior in Lambda https://docs.aws.amazon.com/lambda/latest/dg/invocation-retries.html
[17] Techniques for writing least privilege IAM policies | AWS Security Blog https://aws.amazon.com/blogs/security/techniques-for-writing-least-privilege-iam-policies/
[18] Managing permissions in AWS Lambda https://docs.aws.amazon.com/lambda/latest/dg/lambda-permissions.html
[19] Applying the principles of least privilege in AWS lambda - Orchestra https://www.getorchestra.io/guides/applying-the-principles-of-least-privilege-in-aws-lambda-avoiding-granting-wildcard-permissions-in-iam-policies
[20] AWS DataSync pricing https://aws.amazon.com/datasync/pricing/
[21] AWS Well-Architected Framework - Design Principles https://tutorialsdojo.com/aws-well-architected-framework-design-principles/
[22] The Guide to a Complete, Minimal‑Error Lambda Architecture https://aws.plainenglish.io/the-guide-to-a-complete-minimal-error-lambda-architecture-5a119edc41be
[23] Handling errors in Step Functions workflows https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html
[24] Miro for AWS Well-Architected Framework Reviews https://miro.com/templates/aws-well-architected-reviews/
[25] Design principles - AWS Well-Architected Framework ... https://docs.aws.amazon.com/wellarchitected/2023-04-10/framework/sec-design.html
[26] AWS Well-Architected Labs https://www.wellarchitectedlabs.com
[27] AWS Lambda Operator Guide | Developing least privilege IAM roles https://serverlessland.com/content/service/lambda/guides/aws-lambda-operator-guide/least-privilege-iam
[28] Best practices for creating least-privilege AWS IAM policies - Datadog https://www.datadoghq.com/blog/iam-least-privilege/
[29] Review and Secure a Lambda Function with an IAM Least Privilege ... https://dev.to/aws-builders/review-and-secure-a-lambda-function-with-an-iam-least-privilege-based-security-policy-cloudtrail-adm
