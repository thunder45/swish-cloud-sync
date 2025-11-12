"""
Media Lister Lambda Function

Queries cloud provider API for video list and filters for unsynced content.
Checks DynamoDB to determine which videos need to be synced.
"""

import os
import boto3
from typing import Dict, Any, List
from aws_xray_sdk.core import xray_recorder
from cloud_sync_common.logging_utils import get_logger
from cloud_sync_common.correlation import get_or_create_correlation_id
from cloud_sync_common.provider_interface import ProviderFactory
from cloud_sync_common.exceptions import ProviderError

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
logger = get_logger(__name__)

# Environment variables
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE', 'gopro-sync-tracker')
PAGE_SIZE = int(os.environ.get('PAGE_SIZE', '100'))
MAX_VIDEOS = int(os.environ.get('MAX_VIDEOS', '1000'))


@xray_recorder.capture('lambda_handler')
def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for media listing.
    
    Args:
        event: Lambda event containing provider, auth_token, and user_id
        context: Lambda context
        
    Returns:
        List of new videos that need to be synced
    """
    # Set up correlation ID
    correlation_id = get_or_create_correlation_id(event)
    xray_recorder.put_annotation('correlation_id', correlation_id)
    
    logger.info('Media Lister invoked', extra={
        'event': event,
        'correlation_id': correlation_id
    })
    
    try:
        provider_name = event.get('provider', 'gopro')
        auth_token = event['auth_token']
        user_id = event.get('user_id', '')
        max_videos = event.get('max_videos', MAX_VIDEOS)
        
        xray_recorder.put_annotation('provider', provider_name)
        xray_recorder.put_annotation('user_id', user_id)
        
        # Get provider instance
        provider = ProviderFactory.get_provider(provider_name)
        
        # List media from provider
        logger.info(f'Listing media from {provider_name}')
        all_videos = list_media_from_provider(
            provider, auth_token, user_id, max_videos
        )
        
        logger.info(f'Found {len(all_videos)} total videos from provider')
        
        # Filter for new videos
        new_videos = filter_new_videos(all_videos, provider_name)
        
        logger.info(f'Found {len(new_videos)} new videos to sync')
        
        # Return response
        response = {
            'statusCode': 200,
            'provider': provider_name,
            'new_videos': new_videos,
            'total_found': len(all_videos),
            'new_count': len(new_videos),
            'already_synced': len(all_videos) - len(new_videos),
            'correlation_id': correlation_id
        }
        
        logger.info('Media listing completed successfully', extra={
            'total_found': len(all_videos),
            'new_count': len(new_videos),
            'correlation_id': correlation_id
        })
        
        return response
        
    except ProviderError as e:
        logger.error(f'Provider error: {str(e)}', extra={
            'error_type': 'ProviderError',
            'correlation_id': correlation_id
        }, exc_info=True)
        
        return {
            'statusCode': 500,
            'error': 'ProviderError',
            'message': str(e),
            'correlation_id': correlation_id
        }
        
    except Exception as e:
        logger.error(f'Unexpected error: {str(e)}', extra={
            'error_type': type(e).__name__,
            'correlation_id': correlation_id
        }, exc_info=True)
        
        return {
            'statusCode': 500,
            'error': type(e).__name__,
            'message': str(e),
            'correlation_id': correlation_id
        }


@xray_recorder.capture('list_media_from_provider')
def list_media_from_provider(
    provider: Any,
    auth_token: str,
    user_id: str,
    max_videos: int
) -> List[Dict[str, Any]]:
    """
    List media from cloud provider with pagination.
    
    Args:
        provider: Provider instance
        auth_token: Authentication token
        user_id: User ID
        max_videos: Maximum number of videos to retrieve
        
    Returns:
        List of video metadata dictionaries
    """
    logger.info(f'Listing media from provider (max_videos={max_videos})')
    
    # Call provider's list_media method
    # Provider handles pagination internally and returns a list
    result = provider.list_media(
        auth_token=auth_token,
        user_id=user_id,
        page_size=PAGE_SIZE,
        max_videos=max_videos
    )
    
    # Handle both list and dict return types for compatibility
    if isinstance(result, list):
        # Provider returns list directly (e.g., GoPro provider)
        all_videos = result
    else:
        # Provider returns dict with 'media' key (future providers)
        all_videos = result.get('media', [])
    
    # Convert VideoMetadata objects to dictionaries if needed
    video_dicts = []
    for video in all_videos:
        if hasattr(video, '__dict__'):
            # Convert VideoMetadata object to dict
            video_dict = {
                'media_id': video.media_id,
                'filename': video.filename,
                'download_url': video.download_url,
                'file_size': video.file_size,
                'upload_date': video.upload_date,
                'duration': video.duration
            }
            video_dicts.append(video_dict)
        else:
            # Already a dict
            video_dicts.append(video)
    
    logger.info(f'Retrieved {len(video_dicts)} videos from provider')
    
    return video_dicts


@xray_recorder.capture('filter_new_videos')
def filter_new_videos(
    videos: List[Dict[str, Any]],
    provider_name: str
) -> List[Dict[str, Any]]:
    """
    Filter videos to find those that need to be synced.
    
    Args:
        videos: List of video metadata
        provider_name: Provider name
        
    Returns:
        List of videos that need to be synced
    """
    if not videos:
        return []
    
    # Get DynamoDB table
    table = dynamodb.Table(DYNAMODB_TABLE)
    
    # Batch get items from DynamoDB
    media_ids = [video['media_id'] for video in videos]
    sync_statuses = batch_get_sync_status(table, media_ids)
    
    # Filter for new videos
    new_videos = []
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
            logger.debug(f'Video {media_id} already synced, skipping')
    
    return new_videos


@xray_recorder.capture('batch_get_sync_status')
def batch_get_sync_status(
    table: Any,
    media_ids: List[str]
) -> Dict[str, str]:
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
    
    return sync_statuses
