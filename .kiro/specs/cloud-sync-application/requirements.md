# Requirements Document

## Introduction

The Cloud Sync Application is an automated, serverless system that synchronizes video content from cloud storage providers (starting with GoPro Cloud) to AWS S3 cost-optimized storage tiers. The system operates without manual intervention, automatically discovering new content, transferring it securely, and managing storage lifecycle to minimize costs while ensuring data durability and integrity.

The initial implementation focuses on GoPro Cloud as the source and AWS S3 with Glacier Deep Archive as the destination. The architecture is designed to be extensible for future cloud providers (e.g., Google Drive, Dropbox).

## Glossary

- **Cloud Sync Application**: The complete serverless system that orchestrates video synchronization from cloud providers to AWS S3
- **GoPro Cloud**: GoPro's cloud storage service where users upload videos from their cameras
- **AWS S3**: Amazon Simple Storage Service, object storage service
- **S3 Glacier Deep Archive**: AWS S3 storage class optimized for long-term archival with lowest cost ($0.00099/GB/month)
- **Sync Tracker**: DynamoDB table that maintains the state of each video transfer to prevent duplicates
- **Orchestrator**: AWS Step Functions state machine that coordinates the multi-step sync workflow
- **Media Authenticator**: Lambda function responsible for authenticating with cloud provider APIs
- **Media Lister**: Lambda function that queries cloud provider APIs to discover available videos
- **Video Downloader**: Lambda function that streams videos from cloud provider to S3
- **Sync Status**: Enumerated state of a video transfer (PENDING, IN_PROGRESS, COMPLETED, FAILED)
- **Multipart Upload**: S3 upload method for large files that splits them into chunks for parallel transfer
- **Storage Lifecycle Policy**: S3 configuration that automatically transitions objects between storage classes based on age

## Requirements

### Requirement 1: Automated Video Discovery

**User Story:** As a GoPro user, I want the system to automatically discover new videos in my GoPro Cloud account, so that I don't have to manually identify which videos need to be backed up.

#### Acceptance Criteria

1. WHEN the scheduled sync execution starts, THE Media Lister SHALL query the GoPro Cloud API to retrieve the complete list of available videos.

2. THE Media Lister SHALL paginate through the GoPro Cloud API results with a page size of 100 items until all videos are retrieved.

3. THE Media Lister SHALL extract metadata for each video including media identifier, filename, file size in bytes, upload timestamp, and duration in seconds.

4. THE Media Lister SHALL query the Sync Tracker for each discovered video to determine synchronization status.

5. THE Media Lister SHALL filter the video list to include only videos where the Sync Tracker contains no record OR the Sync Status equals FAILED.

### Requirement 2: Secure Authentication Management

**User Story:** As a system administrator, I want cloud provider credentials to be stored securely and refreshed automatically, so that the system maintains access without exposing sensitive information.

#### Acceptance Criteria

1. THE Media Authenticator SHALL retrieve GoPro Cloud credentials from AWS Secrets Manager using the secret identifier "gopro/credentials".

2. WHEN the stored authentication token has an expiration timestamp less than 24 hours from the current time, THE Media Authenticator SHALL request a new token from the GoPro Cloud API.

3. THE Media Authenticator SHALL store the new authentication token in AWS Secrets Manager with the current timestamp.

4. THE Media Authenticator SHALL return authentication headers containing the valid token for use by downstream components.

5. IF authentication fails after 3 retry attempts with exponential backoff, THEN THE Media Authenticator SHALL publish a failure notification to the alert topic.

### Requirement 3: Reliable Video Transfer

**User Story:** As a GoPro user, I want my videos to be transferred completely and accurately to S3, so that I can trust my backups are identical to the originals.

#### Acceptance Criteria

1. WHEN a video transfer begins, THE Video Downloader SHALL update the Sync Tracker to set the Sync Status to IN_PROGRESS for that video's media identifier.

2. THE Video Downloader SHALL stream video data from the GoPro Cloud download URL directly to S3 without writing to local disk storage.

3. WHEN the video file size exceeds 100 megabytes, THE Video Downloader SHALL use S3 multipart upload with a chunk size of 50 megabytes.

4. THE Video Downloader SHALL verify that the total bytes transferred equals the file size reported by the GoPro Cloud API.

5. WHEN the transfer completes successfully and byte count matches, THE Video Downloader SHALL update the Sync Tracker to set the Sync Status to COMPLETED with the S3 object key and transfer timestamp.

6. IF the byte count verification fails, THEN THE Video Downloader SHALL update the Sync Tracker to set the Sync Status to FAILED and SHALL publish an alert notification.

### Requirement 4: Duplicate Prevention

**User Story:** As a system administrator, I want to ensure videos are never downloaded multiple times, so that we avoid unnecessary data transfer costs and API rate limits.

#### Acceptance Criteria

1. THE Sync Tracker SHALL store a unique record for each video using the media identifier as the partition key.

2. THE Media Lister SHALL exclude videos from the transfer list where the Sync Tracker contains a record with Sync Status equal to COMPLETED.

3. THE Video Downloader SHALL implement idempotent transfer logic that safely handles duplicate invocations for the same media identifier.

4. THE Sync Tracker SHALL maintain records indefinitely to provide permanent duplicate prevention.

### Requirement 5: Cost-Optimized Storage

**User Story:** As a system administrator, I want videos to automatically transition to the lowest-cost storage tier, so that long-term storage costs are minimized.

#### Acceptance Criteria

1. THE Cloud Sync Application SHALL configure an S3 lifecycle policy on the archive bucket with the prefix "gopro-videos/".

2. THE S3 lifecycle policy SHALL transition objects from S3 Standard to S3 Glacier Instant Retrieval after 7 days.

3. THE S3 lifecycle policy SHALL transition objects from S3 Glacier Instant Retrieval to S3 Glacier Deep Archive after 14 days from object creation.

4. THE Video Downloader SHALL create S3 objects with the initial storage class set to S3 Standard.

5. THE Video Downloader SHALL apply object tags "Source=GoPro" and "AutoSync=True" to all uploaded videos.

### Requirement 6: Workflow Orchestration

**User Story:** As a system administrator, I want the sync process to run automatically on a schedule and handle errors gracefully, so that backups happen reliably without manual intervention.

#### Acceptance Criteria

1. THE Orchestrator SHALL execute the complete sync workflow daily at 2:00 AM Central European Time.

2. THE Orchestrator SHALL invoke the Media Authenticator, then the Media Lister, then the Video Downloader for each new video in sequence.

3. THE Orchestrator SHALL process up to 5 video downloads concurrently using parallel execution.

4. WHEN a component invocation fails with a transient error, THE Orchestrator SHALL retry the invocation up to 3 times with exponential backoff starting at 2 seconds.

5. IF the Media Authenticator or Media Lister fails after all retries, THEN THE Orchestrator SHALL terminate the workflow and publish a critical failure notification.

6. IF one or more Video Downloader invocations fail while others succeed, THEN THE Orchestrator SHALL complete the workflow and publish a partial failure notification with the failure count.

### Requirement 7: Operational Visibility

**User Story:** As a system administrator, I want to monitor sync operations and receive alerts for failures, so that I can quickly identify and resolve issues.

#### Acceptance Criteria

1. THE Video Downloader SHALL publish a custom CloudWatch metric "VideosSynced" with a value of 1 for each successful transfer.

2. THE Video Downloader SHALL publish a custom CloudWatch metric "SyncFailures" with a value of 1 and dimension "ErrorType" for each failed transfer.

3. THE Video Downloader SHALL publish a custom CloudWatch metric "BytesTransferred" with the file size value for each successful transfer.

4. THE Cloud Sync Application SHALL configure a CloudWatch alarm that triggers when the "SyncFailures" metric exceeds 3 failures within a 5-minute period.

5. WHEN a CloudWatch alarm enters the ALARM state, THE Cloud Sync Application SHALL publish a notification to the SNS topic "gopro-sync-alerts".

6. THE Media Authenticator, Media Lister, and Video Downloader SHALL write structured JSON logs to CloudWatch Logs with fields: timestamp, log level, correlation identifier, function name, event type, and relevant metadata.

### Requirement 8: Error Recovery

**User Story:** As a system administrator, I want failed transfers to be retried automatically with appropriate delays, so that transient issues don't result in permanent failures.

#### Acceptance Criteria

1. WHEN the Video Downloader encounters a network timeout or HTTP 5xx error, THE Video Downloader SHALL retry the transfer up to 3 times with exponential backoff starting at 30 seconds.

2. THE Video Downloader SHALL increment the retry count in the Sync Tracker for each retry attempt.

3. IF the Video Downloader exhausts all retry attempts, THEN THE Video Downloader SHALL update the Sync Tracker to set the Sync Status to FAILED with the error message.

4. THE Orchestrator SHALL continue processing remaining videos when individual Video Downloader invocations fail.

5. WHEN the next scheduled sync execution runs, THE Media Lister SHALL include videos with Sync Status equal to FAILED in the transfer list for automatic retry.

### Requirement 9: Data Security

**User Story:** As a system administrator, I want all data to be encrypted and access to be restricted, so that video content and credentials are protected from unauthorized access.

#### Acceptance Criteria

1. THE Cloud Sync Application SHALL encrypt all data in transit using HTTPS with TLS 1.2 or higher.

2. THE Cloud Sync Application SHALL configure the S3 archive bucket with server-side encryption using AWS KMS with a customer-managed key.

3. THE Cloud Sync Application SHALL configure the S3 archive bucket to block all public access.

4. THE Cloud Sync Application SHALL enable S3 bucket versioning to protect against accidental deletion.

5. THE Media Authenticator SHALL be granted IAM permissions only for "secretsmanager:GetSecretValue" and "secretsmanager:UpdateSecretValue" actions on the "gopro/credentials" secret resource.

6. THE Video Downloader SHALL be granted IAM permissions only for "s3:PutObject", "s3:PutObjectTagging", and "s3:AbortMultipartUpload" actions on the "gopro-videos/*" prefix resource.

### Requirement 10: Scalability

**User Story:** As a GoPro user with a large video library, I want the system to handle hundreds of videos efficiently, so that my entire library can be backed up in a reasonable time.

#### Acceptance Criteria

1. THE Media Lister SHALL complete discovery and filtering of 1,000 videos within 2 minutes.

2. THE Orchestrator SHALL support processing up to 1,000 videos per sync execution without modification.

3. THE Video Downloader SHALL achieve a minimum transfer throughput of 50 megabits per second per concurrent execution.

4. THE Cloud Sync Application SHALL configure the Orchestrator with a maximum concurrency of 5 parallel Video Downloader invocations.

5. THE Cloud Sync Application SHALL configure the Video Downloader with a memory allocation of 512 megabytes and timeout of 15 minutes.

### Requirement 11: Extensibility for Multiple Cloud Providers

**User Story:** As a system administrator, I want the architecture to support adding new cloud providers in the future, so that users can sync from multiple sources without redesigning the system.

#### Acceptance Criteria

1. THE Cloud Sync Application SHALL organize S3 object keys with a provider prefix in the format "{provider}-videos/{year}/{month}/{filename}".

2. THE Sync Tracker SHALL include a provider identifier field to distinguish videos from different cloud sources.

3. THE Media Authenticator SHALL accept a provider type parameter to determine which authentication logic to execute.

4. THE Media Lister SHALL accept a provider type parameter to determine which API to query.

5. THE Orchestrator SHALL accept a provider type input parameter to route requests to the appropriate provider-specific components.
