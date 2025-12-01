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
        
        xray_recorder.put_annotation('provider', provider_name)
        xray_recorder.put_annotation('media_id', media_id)
        xray_recorder.put_annotation('file_size', file_size)
        
        # Get DynamoDB table
        table = dynamodb.Table(DYNAMODB_TABLE)
        
        # Retrieve credentials from Secrets Manager
        credentials = retrieve_credentials()
        
        # Generate S3 key
        s3_key = generate_s3_key(provider_name, filename, upload_date)
        
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
        
        # Update status to IN_PROGRESS
        update_sync_status(table, media_id, 'IN_PROGRESS', {
            'provider': provider_name,
            'filename': filename,
            'file_size': file_size,
            'upload_date': upload_date
        })
        
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
        
        # Update status to COMPLETED
        update_sync_status(table, media_id, 'COMPLETED', {
            's3_key': s3_key,
            's3_etag': result.get('etag', ''),
            'sync_timestamp': end_time.isoformat() + 'Z',
            'transfer_duration': transfer_duration,
            'throughput_mbps': throughput_mbps,
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
