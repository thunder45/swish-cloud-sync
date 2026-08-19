"""
Media Lister Lambda Function

Queries GoPro Cloud API for video list and filters for unsynced content.
Checks DynamoDB to determine which videos need to be synced.
"""

import json
import os
import boto3
from typing import Dict, Any, List
from datetime import datetime
from aws_xray_sdk.core import xray_recorder
from cloud_sync_common.logging_utils import get_logger
from cloud_sync_common.metrics_utils import MetricsPublisher
from cloud_sync_common.correlation import get_or_create_correlation_id
from cloud_sync_common.gopro_provider import GoProProvider
from cloud_sync_common.exceptions import ProviderError, APIError

# Initialize AWS clients
secrets_client = boto3.client('secretsmanager')
dynamodb = boto3.resource('dynamodb')
sns_client = boto3.client('sns')
logger = get_logger(__name__)
metrics_publisher = MetricsPublisher(namespace='CloudSync/MediaListing')

# Environment variables
SECRET_NAME = os.environ.get('SECRET_NAME', 'gopro/credentials')
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE', 'gopro-sync-tracker')
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN')
PAGE_SIZE = int(os.environ.get('PAGE_SIZE', '100'))
MAX_VIDEOS = int(os.environ.get('MAX_VIDEOS', '1000'))


@xray_recorder.capture('lambda_handler')
def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for media listing.
    
    Args:
        event: Lambda event (can be empty, uses Secrets Manager)
        context: Lambda context
        
    Returns:
        List of new videos that need to be synced
    """
    # Set up correlation ID
    correlation_id = get_or_create_correlation_id(event)
    xray_recorder.put_annotation('correlation_id', correlation_id)
    
    logger.info('Media Lister invoked', extra={
        'correlation_id': correlation_id
    })
    
    start_time = datetime.utcnow()
    
    try:
        # Retrieve credentials from Secrets Manager
        credentials = retrieve_credentials()
        
        # Get page number from event (passed by state machine loop)
        page_number = event.get('page_number', 1)
        
        # Create GoPro provider instance
        provider = GoProProvider()
        
        # List media from provider for this specific page
        logger.info(f'Listing media from GoPro Cloud (page {page_number})')
        all_videos, pagination = list_media_from_provider(provider, credentials, MAX_VIDEOS, correlation_id, page_number)
        
        logger.info(f'Found {len(all_videos)} total videos from provider (page {page_number})')
        logger.info(f'Pagination metadata from GoPro API: {json.dumps(pagination)}')
        
        # Publish metric
        metrics_publisher.put_metric(
            metric_name='MediaListedFromProvider',
            value=len(all_videos),
            unit='Count'
        )
        
        # Filter for new videos
        new_videos = filter_new_videos(all_videos)
        
        logger.info(f'Found {len(new_videos)} new videos to sync')
        
        # Calculate duration
        duration = (datetime.utcnow() - start_time).total_seconds()
        
        # Publish metrics
        metrics_publisher.put_metrics([
            {
                'metric_name': 'NewVideosFound',
                'value': len(new_videos),
                'unit': 'Count'
            },
            {
                'metric_name': 'ListingDuration',
                'value': duration,
                'unit': 'Seconds'
            },
            {
                'metric_name': 'ListingSuccess',
                'value': 1,
                'unit': 'Count'
            }
        ])
        
        # Return response with pagination metadata
        response = {
            'statusCode': 200,
            'provider': 'gopro',
            'new_videos': new_videos,
            'total_found': len(all_videos),
            'new_count': len(new_videos),
            'already_synced': len(all_videos) - len(new_videos),
            'duration_seconds': duration,
            'correlation_id': correlation_id,
            'pagination': pagination
        }
        
        logger.info(f'Media listing completed successfully - Page {pagination.get("current_page", page_number)}/{pagination.get("total_pages", "?")}', extra={
            'total_found': len(all_videos),
            'new_count': len(new_videos),
            'already_synced': len(all_videos) - len(new_videos),
            'pagination': pagination,
            'duration_seconds': duration,
            'correlation_id': correlation_id
        })
        
        return response
        
    except APIError as e:
        logger.error(f'API error: {str(e)}', extra={
            'error_type': 'APIError',
            'status_code': e.status_code,
            'correlation_id': correlation_id
        }, exc_info=True)
        
        # Publish failure metric
        metrics_publisher.put_metric(
            metric_name='ListingFailure',
            value=1,
            unit='Count'
        )
        
        # Check if this is an API structure change
        if e.status_code == 200:
            publish_api_structure_alert(str(e), correlation_id)
        
        return {
            'statusCode': 500,
            'error': 'APIError',
            'message': str(e),
            'correlation_id': correlation_id
        }
        
    except Exception as e:
        logger.error(f'Unexpected error: {str(e)}', extra={
            'error_type': type(e).__name__,
            'correlation_id': correlation_id
        }, exc_info=True)
        
        # Publish failure metric
        metrics_publisher.put_metric(
            metric_name='ListingFailure',
            value=1,
            unit='Count'
        )
        
        return {
            'statusCode': 500,
            'error': type(e).__name__,
            'message': str(e),
            'correlation_id': correlation_id
        }


@xray_recorder.capture('retrieve_credentials')
def retrieve_credentials() -> Dict[str, Any]:
    """
    Retrieve credentials from AWS Secrets Manager.
    
    Returns:
        Dictionary containing credentials
        
    Raises:
        ProviderError: If credentials cannot be retrieved
    """
    try:
        logger.info(f'Retrieving credentials from Secrets Manager: {SECRET_NAME}')
        
        response = secrets_client.get_secret_value(SecretId=SECRET_NAME)
        credentials = json.loads(response['SecretString'])
        
        logger.info('Credentials retrieved successfully')
        return credentials
        
    except secrets_client.exceptions.ResourceNotFoundException:
        raise ProviderError(f'Secret not found: {SECRET_NAME}')
    except secrets_client.exceptions.InvalidRequestException as e:
        raise ProviderError(f'Invalid request to Secrets Manager: {str(e)}')
    except Exception as e:
        raise ProviderError(f'Failed to retrieve credentials: {str(e)}')


@xray_recorder.capture('list_media_from_provider')
def list_media_from_provider(
    provider: GoProProvider,
    credentials: Dict[str, Any],
    max_videos: int,
    correlation_id: str,
    page_number: int = 1
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    List media from GoPro Cloud for a single API page.
    
    Args:
        provider: GoPro provider instance
        credentials: Credentials from Secrets Manager
        max_videos: Maximum number of videos to retrieve (per page)
        correlation_id: Correlation ID for tracking
        page_number: API page number (1-indexed, 30 items per page)
        
    Returns:
        Tuple of (List of video metadata dictionaries for this page, pagination metadata)
        
    Raises:
        APIError: If API response structure is unexpected
    """
    logger.info(f'Listing media from provider (page={page_number})')
    
    try:
        # Extract authentication headers from credentials
        cookies = credentials.get('cookies', '')
        user_agent = credentials.get('user-agent', 
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36')
        
        # Call provider to get a single page (30 items)
        # We get 30 from API but return up to MAX_VIDEOS
        videos, pagination = provider.list_media_with_start_page(
            cookies=cookies,
            user_agent=user_agent,
            start_page=page_number,
            page_size=PAGE_SIZE,
            max_results=MAX_VIDEOS
        )
        
        # Convert VideoMetadata objects to dictionaries
        video_dicts = []
        for video in videos:
            try:
                video_dict = {
                    'media_id': video.media_id,
                    'filename': video.filename,
                    'download_url': video.download_url,
                    'file_size': video.file_size,
                    'upload_date': video.upload_date,
                    'duration': video.duration,
                    'media_type': getattr(video, 'media_type', 'video'),
                    'resolution': getattr(video, 'resolution', 'unknown'),
                    # Extended GoPro metadata for duplicate detection
                    'camera_model': getattr(video, 'camera_model', None),
                    'captured_at': getattr(video, 'captured_at', None),
                    'created_at': getattr(video, 'created_at', None),
                    'file_extension': getattr(video, 'file_extension', None),
                    'height': getattr(video, 'height', None),
                    'width': getattr(video, 'width', None),
                    'source_duration': getattr(video, 'source_duration', None)
                }
                
                # Validate required fields
                validate_video_metadata(video_dict)
                
                video_dicts.append(video_dict)
                
            except Exception as e:
                logger.warning(f'Failed to process video {video.media_id}: {str(e)}')
                continue
        
        logger.info(f'Retrieved and validated {len(video_dicts)} videos from provider with pagination info {pagination}')
        
        return video_dicts, pagination
        
    except APIError:
        # Re-raise API errors (already handled by provider)
        raise
    except Exception as e:
        logger.error(f'Error listing media: {str(e)}', exc_info=True)
        raise ProviderError(f'Failed to list media: {str(e)}')


def validate_video_metadata(video: Dict[str, Any]) -> None:
    """
    Validate video metadata has required fields.
    
    Args:
        video: Video metadata dictionary
        
    Raises:
        APIError: If required fields are missing
    """
    required_fields = ['media_id', 'filename', 'download_url', 'file_size']
    missing_fields = [field for field in required_fields if not video.get(field)]
    
    if missing_fields:
        raise APIError(
            f'API response missing required fields: {", ".join(missing_fields)}. '
            f'API structure may have changed.',
            status_code=200  # Status 200 but invalid structure
        )


@xray_recorder.capture('filter_new_videos')
def filter_new_videos(videos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filter videos to find those that need to be synced.
    For videos already COMPLETED, updates metadata from GoPro API.
    
    Args:
        videos: List of video metadata with full GoPro fields
        
    Returns:
        List of videos that need to be downloaded
    """
    if not videos:
        return []
    
    # Get DynamoDB table
    table = dynamodb.Table(DYNAMODB_TABLE)
    
    # Batch get items from DynamoDB
    media_ids = [video['media_id'] for video in videos]
    sync_statuses = batch_get_sync_status(table, media_ids)
    
    # Filter for new videos and update metadata for existing
    new_videos = []
    metadata_updated = 0
    
    for video in videos:
        media_id = video['media_id']
        status = sync_statuses.get(media_id)
        
        # Include video if:
        # 1. No record exists in DynamoDB
        # 2. Status is not COMPLETED
        if status is None or status != 'COMPLETED':
            new_videos.append(video)
            logger.debug(f'Video {media_id} needs sync (status: {status})')
        else:
            # Video already synced - update metadata from GoPro API
            logger.debug(f'Video {media_id} already synced, updating metadata')
            
            if update_video_metadata(table, media_id, video):
                metadata_updated += 1
    
    logger.info(f'Filtered {len(new_videos)} new videos from {len(videos)} total')
    logger.info(f'Updated metadata for {metadata_updated} existing videos')
    
    return new_videos


@xray_recorder.capture('batch_get_sync_status')
def batch_get_sync_status(table: Any, media_ids: List[str]) -> Dict[str, str]:
    """
    Batch get sync status for multiple media IDs.
    
    Args:
        table: DynamoDB table resource
        media_ids: List of media IDs
        
    Returns:
        Dictionary mapping media_id to status
    """
    sync_statuses = {}
    
    # Process in batches of 100 (DynamoDB limit)
    batch_size = 100
    for i in range(0, len(media_ids), batch_size):
        batch = media_ids[i:i + batch_size]
        
        logger.info(f'Batch querying DynamoDB for {len(batch)} items')
        
        # Build batch get request
        keys = [{'media_id': media_id} for media_id in batch]
        request_items = {
            table.name: {
                'Keys': keys,
                'ProjectionExpression': 'media_id, #status',
                'ExpressionAttributeNames': {
                    '#status': 'status'
                }
            }
        }
        
        try:
            # Process with exponential backoff for unprocessed keys
            max_retries = 3
            retry_delay = 1.0
            
            for attempt in range(max_retries + 1):
                response = dynamodb.batch_get_item(RequestItems=request_items)
                
                # Extract statuses
                items = response.get('Responses', {}).get(table.name, [])
                for item in items:
                    media_id = item['media_id']
                    status = item.get('status')
                    sync_statuses[media_id] = status
                
                logger.info(f'Retrieved {len(items)} sync statuses from DynamoDB (attempt {attempt + 1})')
                
                # Check for unprocessed keys (throttling)
                unprocessed = response.get('UnprocessedKeys', {})
                if not unprocessed:
                    # All items processed successfully
                    break
                
                if attempt < max_retries:
                    # Exponential backoff
                    logger.warning(
                        f'Unprocessed keys detected ({len(unprocessed.get(table.name, {}).get("Keys", []))} items), '
                        f'retrying in {retry_delay}s...'
                    )
                    import time
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    
                    # Use unprocessed keys for next attempt
                    request_items = unprocessed
                else:
                    logger.error(
                        f'Failed to process {len(unprocessed.get(table.name, {}).get("Keys", []))} items '
                        f'after {max_retries} retries'
                    )
        
        except Exception as e:
            logger.error(f'Error batch getting items: {str(e)}', exc_info=True)
            # Continue with partial results
    
    logger.info(f'Retrieved {len(sync_statuses)} sync statuses from DynamoDB')
    
    return sync_statuses


@xray_recorder.capture('update_video_metadata')
def update_video_metadata(table: Any, media_id: str, video_metadata: Dict[str, Any]) -> bool:
    """
    Update DynamoDB record with fresh metadata from GoPro API.
    
    This keeps DynamoDB in sync with GoPro for already-downloaded files.
    
    Args:
        table: DynamoDB table resource
        media_id: Media ID to update
        video_metadata: Complete metadata dict from GoPro API
        
    Returns:
        True if update successful, False otherwise
    """
    # DynamoDB reserved keywords that need attribute name substitution
    RESERVED_KEYWORDS = {
        'duration', 'status', 'timestamp', 'type', 'name', 'size',
        'date', 'format', 'order', 'value', 'data', 'time'
    }
    
    try:
        # Build update expression for metadata fields
        update_parts = []
        expr_values = {':timestamp': datetime.utcnow().isoformat() + 'Z'}
        expr_attr_names = {}
        
        # Add all metadata fields (only non-None values)
        metadata_fields = ['camera_model', 'captured_at', 'created_at', 'file_extension',
                          'height', 'width', 'resolution', 'source_duration', 'media_type',
                          'duration', 'upload_date', 'filename', 'file_size']
        
        for field in metadata_fields:
            value = video_metadata.get(field)
            if value is not None:
                safe_field = field.replace('-', '_')
                
                # Check if field is a reserved keyword
                if safe_field.lower() in RESERVED_KEYWORDS:
                    # Use attribute name substitution
                    attr_name = f'#{safe_field}'
                    update_parts.append(f'{attr_name} = :{safe_field}')
                    expr_attr_names[attr_name] = safe_field
                else:
                    # Use field directly
                    update_parts.append(f'{safe_field} = :{safe_field}')
                
                expr_values[f':{safe_field}'] = value
        
        if not update_parts:
            return True  # No metadata to update
        
        # Add update timestamp
        update_expr = 'SET ' + ', '.join(update_parts) + ', metadata_updated = :timestamp'
        
        # Build update_item kwargs
        update_kwargs = {
            'Key': {'media_id': media_id},
            'UpdateExpression': update_expr,
            'ExpressionAttributeValues': expr_values
        }
        
        # Only add ExpressionAttributeNames if we have reserved keywords
        if expr_attr_names:
            update_kwargs['ExpressionAttributeNames'] = expr_attr_names
        
        table.update_item(**update_kwargs)
        
        logger.debug(f'Updated metadata for {media_id}')
        return True
        
    except Exception as e:
        logger.warning(f'Failed to update metadata for {media_id}: {str(e)}')
        return False


# OBSOLETE: Pagination state now managed by Step Functions context
# These functions are kept for backwards compatibility but not used
#
# @xray_recorder.capture('get_pagination_state')
# def get_pagination_state() -> int:
#     """Get current pagination state from DynamoDB (OBSOLETE - Step Functions tracks this)"""
#     pass
#
# @xray_recorder.capture('update_pagination_state')
# def update_pagination_state(page: int) -> None:
#     """Update pagination state in DynamoDB (OBSOLETE - Step Functions tracks this)"""
#     pass


def publish_api_structure_alert(message: str, correlation_id: str, response_sample: str = None) -> None:
    """
    Publish alert when API response structure differs from expected.
    
    Args:
        message: Alert message
        correlation_id: Correlation ID for tracking
        response_sample: Sample of unexpected response (optional)
    """
    if not SNS_TOPIC_ARN:
        logger.warning('SNS_TOPIC_ARN not configured, skipping alert')
        return
    
    try:
        alert_message = {
            'alert_type': 'API_STRUCTURE_CHANGE',
            'severity': 'MEDIUM',
            'message': message,
            'correlation_id': correlation_id,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'function': 'media-lister',
            'action_required': 'Verify API structure and update code if needed',
            'response_sample': response_sample[:500] if response_sample else None  # Truncate to 500 chars
        }
        
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject='⚠️ GoPro Sync: API Structure Change Detected',
            Message=json.dumps(alert_message, indent=2)
        )
        
        logger.info('API structure alert published to SNS', extra={
            'correlation_id': correlation_id
        })
        
        # Publish metric
        metrics_publisher.put_metric(
            metric_name='APIStructureChangeDetected',
            value=1,
            unit='Count'
        )
        
    except Exception as e:
        logger.error(f'Failed to publish alert: {str(e)}', exc_info=True)
