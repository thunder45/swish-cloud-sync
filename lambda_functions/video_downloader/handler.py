"""
Video Downloader Lambda Function

Streams video from cloud provider to S3 using multipart upload for large files.
Updates DynamoDB with sync status and publishes CloudWatch metrics.
"""

import json
import os
import boto3
import requests
from datetime import datetime
from typing import Dict, Any
from decimal import Decimal
from aws_xray_sdk.core import xray_recorder
from cloud_sync_common.logging_utils import get_logger
from cloud_sync_common.correlation import get_or_create_correlation_id
from cloud_sync_common.metrics_utils import MetricsPublisher
from cloud_sync_common.gopro_provider import GoProProvider
from cloud_sync_common.exceptions import TransferError, APIError

# Initialize AWS clients
s3_client = boto3.client('s3')
secrets_client = boto3.client('secretsmanager')
dynamodb = boto3.resource('dynamodb')
logger = get_logger(__name__)
metrics_publisher = MetricsPublisher(namespace='GoProSync')

# Environment variables
S3_BUCKET = os.environ.get('S3_BUCKET')
SECRET_NAME = os.environ.get('SECRET_NAME', 'gopro/credentials')
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE', 'gopro-sync-tracker')
MULTIPART_THRESHOLD = int(os.environ.get('MULTIPART_THRESHOLD', '104857600'))  # 100 MB
CHUNK_SIZE = int(os.environ.get('CHUNK_SIZE', '104857600'))  # 100 MB


@xray_recorder.capture('lambda_handler')
def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for video download.
    
    Args:
        event: Lambda event containing video metadata and auth token
        context: Lambda context
        
    Returns:
        Download result with S3 key and transfer metrics
    """
    # Set up correlation ID
    correlation_id = get_or_create_correlation_id(event)
    xray_recorder.put_annotation('correlation_id', correlation_id)
    
    logger.info('Video Downloader invoked', extra={
        'event': event,
        'correlation_id': correlation_id
    })
    
    start_time = datetime.utcnow()
    
    try:
        provider_name = event.get('provider', 'gopro')
        media_id = event['media_id']
        filename = event['filename']
        file_size = event.get('file_size', 0)
        upload_date = event.get('upload_date', '')
        
        # Extract extended metadata for duplicate detection
        metadata = {
            'media_id': media_id,
            'filename': filename,
            'file_size': file_size,
            'upload_date': upload_date,
            'duration': event.get('duration'),
            'media_type': event.get('media_type'),
            'resolution': event.get('resolution'),
            'camera_model': event.get('camera_model'),
            'captured_at': event.get('captured_at'),
            'created_at': event.get('created_at'),
            'file_extension': event.get('file_extension'),
            'height': event.get('height'),
            'width': event.get('width'),
            'source_duration': event.get('source_duration')
        }
        
        xray_recorder.put_annotation('provider', provider_name)
        xray_recorder.put_annotation('media_id', media_id)
        xray_recorder.put_annotation('file_size', file_size)
        
        # Get DynamoDB table
        table = dynamodb.Table(DYNAMODB_TABLE)
        
        # Retrieve credentials from Secrets Manager
        credentials = retrieve_credentials()
        
        # Generate unique S3 key using full metadata fingerprint
        s3_key = generate_unique_s3_key(table, provider_name, metadata, upload_date)
        
        # Check idempotency
        if check_already_uploaded(s3_key, media_id):
            logger.info(f'Video {media_id} already uploaded, skipping')
            return {
                'statusCode': 200,
                'media_id': media_id,
                's3_key': s3_key,
                'message': 'Already uploaded',
                'correlation_id': correlation_id
            }
        
        # Update status to IN_PROGRESS with full metadata
        update_attributes = {
            'provider': provider_name,
            'filename': filename,
            'file_size': file_size,
            'upload_date': upload_date,
        }
        
        # Add extended metadata fields (only if not None)
        for key in ['duration', 'media_type', 'resolution', 'camera_model', 
                    'captured_at', 'created_at', 'file_extension', 'height', 
                    'width', 'source_duration']:
            value = metadata.get(key)
            if value is not None:
                update_attributes[key] = value
        
        update_sync_status(table, media_id, 'IN_PROGRESS', update_attributes)
        
        # Get actual download URL using 2-step process
        logger.info(f'Resolving download URL for {media_id}')
        provider = GoProProvider()
        
        try:
            actual_download_url = provider.get_download_url(
                media_id=media_id,
                cookies=credentials.get('cookies'),
                user_agent=credentials.get('user-agent'),
                quality='source'
            )
            logger.info(f'Resolved download URL (CloudFront pre-signed)')
        except APIError as e:
            logger.error(f'Failed to resolve download URL: {e}')
            raise TransferError(f'Failed to get download URL: {e}')
        
        # Download and upload video
        logger.info(f'Starting download for {media_id} ({file_size} bytes)')
        
        result = download_and_upload_video(
            download_url=actual_download_url,
            s3_bucket=S3_BUCKET,
            s3_key=s3_key,
            file_size=file_size,
            media_id=media_id,
            provider=provider_name
        )
        
        # Calculate transfer metrics
        end_time = datetime.utcnow()
        transfer_duration = (end_time - start_time).total_seconds()
        throughput_mbps = (result['bytes_transferred'] * 8) / (transfer_duration * 1_000_000)
        
        # Update status to COMPLETED (convert floats to Decimal for DynamoDB)
        update_sync_status(table, media_id, 'COMPLETED', {
            's3_key': s3_key,
            's3_etag': result.get('etag', ''),
            'sync_timestamp': end_time.isoformat() + 'Z',
            'transfer_duration': Decimal(str(round(transfer_duration, 2))),
            'throughput_mbps': Decimal(str(round(throughput_mbps, 2))),
            'bytes_transferred': result['bytes_transferred']
        })
        
        # Publish metrics
        metrics_publisher.record_video_synced(
            provider=provider_name,
            environment=os.environ.get('ENVIRONMENT', 'dev'),
            bytes_transferred=result['bytes_transferred'],
            duration_seconds=transfer_duration
        )
        
        # Publish TTFB metric separately
        metrics_publisher.put_metric(
            metric_name='TimeToFirstByte',
            value=result.get('ttfb', 0),
            unit='Seconds'
        )
        
        logger.info('Video download completed successfully', extra={
            'media_id': media_id,
            's3_key': s3_key,
            'bytes_transferred': result['bytes_transferred'],
            'transfer_duration': transfer_duration,
            'throughput_mbps': throughput_mbps,
            'correlation_id': correlation_id
        })
        
        return {
            'statusCode': 200,
            'media_id': media_id,
            's3_key': s3_key,
            's3_etag': result.get('etag', ''),
            'bytes_transferred': result['bytes_transferred'],
            'transfer_duration_seconds': transfer_duration,
            'throughput_mbps': throughput_mbps,
            'correlation_id': correlation_id
        }
        
    except requests.exceptions.HTTPError as e:
        # Handle 404 (video deleted from source)
        if e.response.status_code == 404:
            logger.warning(f'Video {media_id} not found (404), marking as completed with note')
            
            table = dynamodb.Table(DYNAMODB_TABLE)
            update_sync_status(table, media_id, 'COMPLETED', {
                'note': 'source_deleted',
                'sync_timestamp': datetime.utcnow().isoformat() + 'Z'
            })
            
            return {
                'statusCode': 200,
                'media_id': media_id,
                'message': 'Video deleted from source',
                'correlation_id': correlation_id
            }
        
        # Other HTTP errors
        logger.error(f'HTTP error: {str(e)}', extra={
            'error_type': 'HTTPError',
            'status_code': e.response.status_code,
            'correlation_id': correlation_id
        }, exc_info=True)
        
        # Update status to FAILED
        table = dynamodb.Table(DYNAMODB_TABLE)
        update_sync_status(table, media_id, 'FAILED', {
            'error_message': f'HTTP {e.response.status_code}: {str(e)}',
            'retry_count': event.get('retry_count', 0) + 1
        })
        
        # Publish failure metric
        metrics_publisher.record_sync_failure(
            provider=provider_name,
            environment=os.environ.get('ENVIRONMENT', 'dev'),
            error_type='HTTPError'
        )
        
        raise
        
    except TransferError as e:
        logger.error(f'Transfer error: {str(e)}', extra={
            'error_type': 'TransferError',
            'correlation_id': correlation_id
        }, exc_info=True)
        
        # Update status to FAILED
        table = dynamodb.Table(DYNAMODB_TABLE)
        update_sync_status(table, media_id, 'FAILED', {
            'error_message': str(e),
            'retry_count': event.get('retry_count', 0) + 1
        })
        
        # Publish failure metric
        metrics_publisher.record_sync_failure(
            provider=provider_name,
            environment=os.environ.get('ENVIRONMENT', 'dev'),
            error_type='TransferError'
        )
        
        raise
        
    except Exception as e:
        logger.error(f'Unexpected error: {str(e)}', extra={
            'error_type': type(e).__name__,
            'correlation_id': correlation_id
        }, exc_info=True)
        
        # Update status to FAILED
        try:
            table = dynamodb.Table(DYNAMODB_TABLE)
            update_sync_status(table, media_id, 'FAILED', {
                'error_message': str(e),
                'retry_count': event.get('retry_count', 0) + 1
            })
        except:
            pass
        
        # Publish failure metric
        try:
            metrics_publisher.record_sync_failure(
                provider=event.get('provider', 'gopro'),
                environment=os.environ.get('ENVIRONMENT', 'dev'),
                error_type=type(e).__name__
            )
        except:
            pass
        
        raise


@xray_recorder.capture('retrieve_credentials')
def retrieve_credentials() -> Dict[str, Any]:
    """
    Retrieve credentials from AWS Secrets Manager.
    
    Returns:
        Dictionary containing credentials
        
    Raises:
        TransferError: If credentials cannot be retrieved
    """
    try:
        logger.info(f'Retrieving credentials from Secrets Manager: {SECRET_NAME}')
        
        response = secrets_client.get_secret_value(SecretId=SECRET_NAME)
        credentials = json.loads(response['SecretString'])
        
        logger.info('Credentials retrieved successfully')
        return credentials
        
    except Exception as e:
        raise TransferError(f'Failed to retrieve credentials: {str(e)}')


def generate_s3_key(provider: str, filename: str, upload_date: str) -> str:
    """
    Generate S3 object key for video.
    
    Args:
        provider: Provider name
        filename: Original filename
        upload_date: Upload date (ISO format)
        
    Returns:
        S3 object key
    """
    try:
        # Parse upload date
        date = datetime.fromisoformat(upload_date.replace('Z', '+00:00'))
        year = date.year
        month = f'{date.month:02d}'
    except:
        # Fallback to current date if parsing fails
        now = datetime.utcnow()
        year = now.year
        month = f'{now.month:02d}'
    
    return f'{provider}-videos/{year}/{month}/{filename}'


@xray_recorder.capture('query_existing_filenames')
def query_existing_filenames(table: Any, filename_pattern: str) -> list:
    """
    Query DynamoDB for all items with s3_key containing the filename pattern.
    
    Args:
        table: DynamoDB table resource
        filename_pattern: Base filename to search for (e.g., 'GH011189.MP4')
        
    Returns:
        List of dictionaries with s3_key and file_size for each match
    """
    try:
        # Scan table for items where s3_key contains the filename
        # Note: This is a filter, not a query, since s3_key is not a key
        response = table.scan(
            FilterExpression='contains(s3_key, :filename)',
            ExpressionAttributeValues={':filename': filename_pattern},
            ProjectionExpression='s3_key, file_size, media_id'
        )
        
        existing_items = []
        for item in response.get('Items', []):
            if 's3_key' in item:
                existing_items.append({
                    's3_key': item['s3_key'],
                    'file_size': int(item.get('file_size', 0)),
                    'media_id': item.get('media_id', '')
                })
        
        logger.info(f'Found {len(existing_items)} existing items matching pattern: {filename_pattern}')
        return existing_items
        
    except Exception as e:
        logger.warning(f'Error querying existing filenames: {str(e)}')
        return []


def is_exact_duplicate(new_metadata: Dict[str, Any], existing_metadata: Dict[str, Any]) -> bool:
    """
    Check if metadata represents the exact same file using metadata fingerprint.
    
    Compares immutable characteristics to identify true duplicates.
    
    Args:
        new_metadata: New file metadata
        existing_metadata: Existing file metadata from DynamoDB
        
    Returns:
        True if metadata fingerprints match (same file), False otherwise
    """
    # Compare critical immutable fields
    filename_match = new_metadata.get('filename') == existing_metadata.get('filename')
    size_match = new_metadata.get('file_size') == existing_metadata.get('file_size')
    
    # Compare captured_at if available (most definitive)
    captured_at_match = True
    if new_metadata.get('captured_at') and existing_metadata.get('captured_at'):
        captured_at_match = new_metadata['captured_at'] == existing_metadata['captured_at']
    
    # Compare resolution if available
    resolution_match = True
    if new_metadata.get('resolution') and existing_metadata.get('resolution'):
        resolution_match = new_metadata['resolution'] == existing_metadata['resolution']
    
    # Compare source_duration if available (milliseconds precision)
    duration_match = True
    if new_metadata.get('source_duration') and existing_metadata.get('source_duration'):
        duration_match = new_metadata['source_duration'] == existing_metadata['source_duration']
    
    # All critical fields must match for exact duplicate
    return filename_match and size_match and captured_at_match and resolution_match and duration_match


@xray_recorder.capture('query_existing_files_metadata')
def query_existing_files_metadata(table: Any, filename: str) -> list:
    """
    Query DynamoDB for all items with matching filename, including full metadata.
    
    Args:
        table: DynamoDB table resource
        filename: Filename to search for
        
    Returns:
        List of dictionaries with full metadata for each match
    """
    try:
        # Scan table for items where s3_key contains the filename
        response = table.scan(
            FilterExpression='contains(s3_key, :filename)',
            ExpressionAttributeValues={':filename': filename},
            ProjectionExpression='media_id, s3_key, filename, file_size, captured_at, resolution, source_duration, camera_model, height, width'
        )
        
        existing_items = []
        for item in response.get('Items', []):
            if 's3_key' in item:
                existing_items.append({
                    'media_id': item.get('media_id', ''),
                    's3_key': item['s3_key'],
                    'filename': item.get('filename', ''),
                    'file_size': int(item.get('file_size', 0)),
                    'captured_at': item.get('captured_at'),
                    'resolution': item.get('resolution'),
                    'source_duration': item.get('source_duration'),
                    'camera_model': item.get('camera_model'),
                    'height': item.get('height'),
                    'width': item.get('width')
                })
        
        logger.info(f'Found {len(existing_items)} existing items matching filename: {filename}')
        return existing_items
        
    except Exception as e:
        logger.warning(f'Error querying existing files: {str(e)}')
        return []


@xray_recorder.capture('generate_unique_s3_key')
def generate_unique_s3_key(
    table: Any,
    provider: str,
    metadata: Dict[str, Any],
    upload_date: str
) -> str:
    """
    Generate unique S3 key using full metadata fingerprint for duplicate detection.
    
    Uses comprehensive metadata comparison to identify true duplicates:
    - filename, file_size, captured_at, resolution, source_duration
    
    Args:
        table: DynamoDB table resource
        provider: Provider name
        metadata: Complete file metadata dict
        upload_date: Upload date (ISO format)
        
    Returns:
        S3 object key (existing if exact duplicate, new with counter otherwise)
    """
    filename = metadata.get('filename', 'unknown.mp4')
    file_size = metadata.get('file_size', 0)
    
    # Generate base S3 key
    base_key = generate_s3_key(provider, filename, upload_date)
    
    # Query DynamoDB for existing files with this filename
    existing_items = query_existing_files_metadata(table, filename)
    
    # If no duplicates exist, use the base key
    if not existing_items:
        logger.info(f'No duplicates found for {filename}, using base key')
        return base_key
    
    # Check for exact duplicate using metadata fingerprint
    for item in existing_items:
        if is_exact_duplicate(metadata, item):
            logger.info(
                f'Exact duplicate found via metadata fingerprint: {filename} - '
                f'Metadata match (size={file_size}, captured_at={metadata.get("captured_at")}, '
                f'resolution={metadata.get("resolution")}) - '
                f'Reusing existing key: {item["s3_key"]}'
            )
            return item['s3_key']
    
    # No exact match - this is a true duplicate with different metadata
    logger.info(
        f'Different file detected: {filename} - '
        f'New metadata (size={file_size}, captured_at={metadata.get("captured_at")}, '
        f'resolution={metadata.get("resolution")}) does not match existing files'
    )
    
    # Extract base filename and extension
    filename_parts = filename.rsplit('.', 1)
    if len(filename_parts) == 2:
        base_filename, extension = filename_parts
    else:
        base_filename = filename
        extension = ''
    
    # Find the highest counter used
    max_counter = 1
    for item in existing_items:
        # Extract just the filename from the S3 key path
        existing_filename = item['s3_key'].split('/')[-1]
        
        # Check if this is the base filename (without counter)
        if existing_filename == filename:
            max_counter = max(max_counter, 1)
            continue
        
        # Check for numbered versions (e.g., GH011189_2.MP4)
        if extension:
            pattern = f'{base_filename}_'
            if pattern in existing_filename and existing_filename.endswith(f'.{extension}'):
                try:
                    # Extract counter between base_filename_ and .extension
                    counter_part = existing_filename.replace(pattern, '').replace(f'.{extension}', '')
                    counter = int(counter_part)
                    max_counter = max(max_counter, counter)
                except ValueError:
                    # Not a valid counter, skip
                    pass
    
    # Generate new filename with next counter
    next_counter = max_counter + 1
    if extension:
        unique_filename = f'{base_filename}_{next_counter}.{extension}'
    else:
        unique_filename = f'{base_filename}_{next_counter}'
    
    # Generate unique S3 key with the numbered filename
    unique_key = generate_s3_key(provider, unique_filename, upload_date)
    
    logger.info(
        f'Creating numbered version: {unique_filename} (version {next_counter})'
    )
    
    # Publish duplicate metric
    try:
        metrics_publisher.put_metric(
            metric_name='DuplicateFilename',
            value=1,
            unit='Count',
            dimensions={'Provider': provider, 'Filename': base_filename}
        )
    except Exception as e:
        logger.warning(f'Error publishing duplicate metric: {str(e)}')
    
    return unique_key


@xray_recorder.capture('check_already_uploaded')
def check_already_uploaded(s3_key: str, media_id: str) -> bool:
    """
    Check if video is already uploaded to S3 (idempotency check).
    
    Args:
        s3_key: S3 object key
        media_id: Media ID
        
    Returns:
        True if already uploaded, False otherwise
    """
    try:
        response = s3_client.head_object(
            Bucket=S3_BUCKET,
            Key=s3_key
        )
        
        # Check idempotency token in metadata
        metadata = response.get('Metadata', {})
        if metadata.get('sourcemediaid') == media_id:
            logger.info(f'Video {media_id} already uploaded (idempotency check)')
            return True
        
    except s3_client.exceptions.NoSuchKey:
        # Object doesn't exist, proceed with upload
        pass
    except Exception as e:
        logger.warning(f'Error checking idempotency: {str(e)}')
    
    return False


@xray_recorder.capture('update_sync_status')
def update_sync_status(
    table: Any,
    media_id: str,
    status: str,
    attributes: Dict[str, Any]
) -> None:
    """
    Update sync status in DynamoDB.
    
    Args:
        table: DynamoDB table resource
        media_id: Media ID
        status: Sync status
        attributes: Additional attributes to update
    """
    # DynamoDB reserved keywords that need attribute name substitution
    RESERVED_KEYWORDS = {
        'duration', 'status', 'timestamp', 'type', 'name', 'size',
        'date', 'format', 'order', 'value', 'data', 'time'
    }
    
    try:
        # Build update expression
        update_expr = 'SET #status = :status, update_timestamp = :timestamp'
        expr_attr_names = {'#status': 'status'}
        expr_attr_values = {
            ':status': status,
            ':timestamp': datetime.utcnow().isoformat() + 'Z'
        }
        
        # Add additional attributes
        for key, value in attributes.items():
            safe_key = key.replace('-', '_')
            
            # Check if key is a reserved keyword
            if safe_key.lower() in RESERVED_KEYWORDS:
                # Use attribute name substitution
                attr_name = f'#{safe_key}'
                update_expr += f', {attr_name} = :{safe_key}'
                expr_attr_names[attr_name] = safe_key
            else:
                # Use key directly
                update_expr += f', {safe_key} = :{safe_key}'
            
            expr_attr_values[f':{safe_key}'] = value
        
        table.update_item(
            Key={'media_id': media_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values
        )
        
        logger.info(f'Updated sync status to {status} for {media_id}')
        
    except Exception as e:
        logger.error(f'Error updating sync status: {str(e)}', exc_info=True)
        raise


@xray_recorder.capture('download_and_upload_video')
def download_and_upload_video(
    download_url: str,
    s3_bucket: str,
    s3_key: str,
    file_size: int,
    media_id: str,
    provider: str
) -> Dict[str, Any]:
    """
    Download video from provider and upload to S3.
    
    Args:
        download_url: Pre-signed CloudFront URL (no auth needed)
        s3_bucket: S3 bucket name
        s3_key: S3 object key
        file_size: Expected file size (may be 0 if unknown)
        media_id: Media ID
        provider: Provider name
        
    Returns:
        Dictionary with transfer results
    """
    # Decide upload method based on file size
    # If file_size is 0 (unknown), use multipart to be safe
    if file_size == 0 or file_size > MULTIPART_THRESHOLD:
        logger.info(f'Using multipart upload (file size: {file_size} bytes)')
        return multipart_upload_stream(
            download_url, s3_bucket, s3_key, file_size,
            media_id, provider
        )
    else:
        logger.info(f'Using direct upload (file size: {file_size} bytes)')
        return direct_upload_stream(
            download_url, s3_bucket, s3_key, file_size,
            media_id, provider
        )


@xray_recorder.capture('direct_upload_stream')
def direct_upload_stream(
    download_url: str,
    s3_bucket: str,
    s3_key: str,
    file_size: int,
    media_id: str,
    provider: str
) -> Dict[str, Any]:
    """
    Direct upload for small files.
    
    Args:
        download_url: Pre-signed CloudFront URL (no auth needed)
        s3_bucket: S3 bucket name
        s3_key: S3 object key
        file_size: Expected file size (may be 0 if unknown)
        media_id: Media ID
        provider: Provider name
        
    Returns:
        Dictionary with transfer results
    """
    # Start download - CloudFront URLs are pre-signed, no auth needed
    with xray_recorder.capture('cloudfront_download'):
        response = requests.get(download_url, stream=True, timeout=300)
        response.raise_for_status()
        
        # Track TTFB
        ttfb = response.elapsed.total_seconds()
        xray_recorder.put_metadata('ttfb_seconds', ttfb)
    
    # Read content
    content = response.content
    bytes_transferred = len(content)
    
    # Verify size if provided
    if file_size > 0 and bytes_transferred != file_size:
        logger.warning(
            f'Size mismatch: expected {file_size}, got {bytes_transferred} - using actual size'
        )
    
    # Upload to S3
    with xray_recorder.capture('s3_put_object'):
        put_response = s3_client.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=content,
            StorageClass='STANDARD',
            ServerSideEncryption='aws:kms',
            Tagging='Source=' + provider + '&AutoSync=True',
            Metadata={
                'IdempotencyToken': f'{media_id}-{datetime.utcnow().isoformat()}',
                'SourceMediaId': media_id,
                'SourceProvider': provider
            }
        )
    
    return {
        'bytes_transferred': bytes_transferred,
        'etag': put_response.get('ETag', ''),
        'ttfb': ttfb
    }


@xray_recorder.capture('multipart_upload_stream')
def multipart_upload_stream(
    download_url: str,
    s3_bucket: str,
    s3_key: str,
    file_size: int,
    media_id: str,
    provider: str
) -> Dict[str, Any]:
    """
    Multipart upload for large files.
    
    Args:
        download_url: Pre-signed CloudFront URL (no auth needed)
        s3_bucket: S3 bucket name
        s3_key: S3 object key
        file_size: Expected file size (may be 0 if unknown)
        media_id: Media ID
        provider: Provider name
        
    Returns:
        Dictionary with transfer results
    """
    # Initiate multipart upload
    with xray_recorder.capture('s3_create_multipart_upload'):
        multipart = s3_client.create_multipart_upload(
            Bucket=s3_bucket,
            Key=s3_key,
            StorageClass='STANDARD',
            ServerSideEncryption='aws:kms',
            Tagging='Source=' + provider + '&AutoSync=True',
            Metadata={
                'IdempotencyToken': f'{media_id}-{datetime.utcnow().isoformat()}',
                'SourceMediaId': media_id,
                'SourceProvider': provider
            }
        )
    
    upload_id = multipart['UploadId']
    parts = []
    part_number = 1
    bytes_transferred = 0
    ttfb = 0
    
    try:
        # Start streaming download - CloudFront URLs are pre-signed, no auth needed
        with xray_recorder.capture('cloudfront_download'):
            response = requests.get(download_url, stream=True, timeout=300)
            response.raise_for_status()
            
            # Track TTFB
            ttfb = response.elapsed.total_seconds()
            xray_recorder.put_metadata('ttfb_seconds', ttfb)
        
        # Stream chunks to S3
        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            if chunk:
                with xray_recorder.capture(f's3_upload_part_{part_number}'):
                    part = s3_client.upload_part(
                        Bucket=s3_bucket,
                        Key=s3_key,
                        PartNumber=part_number,
                        UploadId=upload_id,
                        Body=chunk
                    )
                
                parts.append({
                    'PartNumber': part_number,
                    'ETag': part['ETag']
                })
                
                bytes_transferred += len(chunk)
                part_number += 1
                
                logger.info(f'Uploaded part {part_number - 1}, bytes: {bytes_transferred}')
        
        # Verify size if provided
        if file_size > 0 and bytes_transferred != file_size:
            logger.warning(
                f'Size mismatch: expected {file_size}, got {bytes_transferred} - using actual size'
            )
        
        # Complete multipart upload
        with xray_recorder.capture('s3_complete_multipart_upload'):
            complete_response = s3_client.complete_multipart_upload(
                Bucket=s3_bucket,
                Key=s3_key,
                UploadId=upload_id,
                MultipartUpload={'Parts': parts}
            )
        
        logger.info(f'Multipart upload completed: {len(parts)} parts')
        
        return {
            'bytes_transferred': bytes_transferred,
            'etag': complete_response.get('ETag', ''),
            'parts': len(parts),
            'ttfb': ttfb
        }
        
    except Exception as e:
        logger.error(f'Multipart upload failed: {str(e)}', exc_info=True)
        
        # Abort multipart upload
        try:
            s3_client.abort_multipart_upload(
                Bucket=s3_bucket,
                Key=s3_key,
                UploadId=upload_id
            )
            logger.info('Multipart upload aborted')
        except Exception as abort_error:
            logger.error(f'Error aborting multipart upload: {str(abort_error)}')
        
        raise
