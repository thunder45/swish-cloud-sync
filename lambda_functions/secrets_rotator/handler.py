"""
Secrets Rotator Lambda Function

Automatically rotates cloud provider credentials stored in AWS Secrets Manager.
Performs token refresh, validates new credentials, and publishes metrics.
"""

import json
import os
import boto3
from datetime import datetime
from typing import Dict, Any
from aws_xray_sdk.core import xray_recorder
from cloud_sync_common.logging_utils import get_logger
from cloud_sync_common.correlation import get_or_create_correlation_id
from cloud_sync_common.exceptions import AuthenticationError
from cloud_sync_common.provider_interface import ProviderFactory
from cloud_sync_common.metrics_utils import MetricsPublisher
from cloud_sync_common.retry_utils import exponential_backoff_retry

# Initialize AWS clients
secrets_client = boto3.client('secretsmanager')
sns_client = boto3.client('sns')
cloudwatch = boto3.client('cloudwatch')
logger = get_logger(__name__)

# Environment variables
SECRET_NAME = os.environ.get('SECRET_NAME', 'gopro/credentials')
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN', '')
PROVIDER_NAME = os.environ.get('PROVIDER_NAME', 'gopro')


@xray_recorder.capture('lambda_handler')
def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for secrets rotation.
    
    Args:
        event: Lambda event (can be from EventBridge or Secrets Manager rotation)
        context: Lambda context
        
    Returns:
        Rotation response with status
    """
    # Set up correlation ID
    correlation_id = get_or_create_correlation_id(event)
    xray_recorder.put_annotation('correlation_id', correlation_id)
    
    logger.info('Secrets Rotator invoked', extra={
        'event': event,
        'correlation_id': correlation_id
    })
    
    rotation_start = datetime.utcnow()
    
    try:
        # Retrieve current credentials
        current_credentials = retrieve_credentials()
        
        # Create backup of current credentials for rollback
        credentials_backup = json.dumps(current_credentials)
        
        # Perform token refresh
        logger.info('Starting token refresh')
        new_credentials = refresh_credentials(current_credentials)
        
        # Test new credentials
        logger.info('Testing new credentials')
        test_credentials(new_credentials)
        
        # Store new credentials with rollback on failure
        logger.info('Storing new credentials')
        try:
            store_credentials(new_credentials)
        except Exception as store_error:
            logger.error(f'Failed to store new credentials, attempting rollback: {str(store_error)}')
            try:
                # Rollback to previous credentials
                store_credentials(current_credentials)
                logger.info('Successfully rolled back to previous credentials')
            except Exception as rollback_error:
                logger.error(f'Rollback failed: {str(rollback_error)}')
            raise AuthenticationError(f'Failed to store new credentials: {str(store_error)}')
        
        # Calculate rotation duration
        rotation_duration = (datetime.utcnow() - rotation_start).total_seconds()
        
        # Publish success metrics
        publish_rotation_metrics(success=True, duration=rotation_duration)
        
        # Send success notification
        send_notification(
            subject='Secrets Rotation Successful',
            message=f'Successfully rotated credentials for {PROVIDER_NAME}',
            correlation_id=correlation_id,
            success=True
        )
        
        logger.info('Secrets rotation completed successfully', extra={
            'duration_seconds': rotation_duration,
            'correlation_id': correlation_id
        })
        
        return {
            'statusCode': 200,
            'message': 'Secrets rotation completed successfully',
            'provider': PROVIDER_NAME,
            'duration_seconds': rotation_duration,
            'correlation_id': correlation_id
        }
        
    except Exception as e:
        # Calculate rotation duration
        rotation_duration = (datetime.utcnow() - rotation_start).total_seconds()
        
        # Publish failure metrics
        publish_rotation_metrics(success=False, duration=rotation_duration)
        
        # Send failure notification
        send_notification(
            subject='Secrets Rotation Failed',
            message=f'Failed to rotate credentials for {PROVIDER_NAME}: {str(e)}',
            correlation_id=correlation_id,
            success=False,
            error=str(e)
        )
        
        logger.error(f'Secrets rotation failed: {str(e)}', extra={
            'error_type': type(e).__name__,
            'duration_seconds': rotation_duration,
            'correlation_id': correlation_id
        }, exc_info=True)
        
        return {
            'statusCode': 500,
            'error': type(e).__name__,
            'message': str(e),
            'provider': PROVIDER_NAME,
            'duration_seconds': rotation_duration,
            'correlation_id': correlation_id
        }


@xray_recorder.capture('retrieve_credentials')
def retrieve_credentials() -> Dict[str, Any]:
    """
    Retrieve credentials from AWS Secrets Manager.
    
    Returns:
        Dictionary containing credentials
        
    Raises:
        AuthenticationError: If credentials cannot be retrieved
    """
    try:
        logger.info(f'Retrieving credentials from Secrets Manager: {SECRET_NAME}')
        
        response = secrets_client.get_secret_value(SecretId=SECRET_NAME)
        credentials = json.loads(response['SecretString'])
        
        logger.info('Credentials retrieved successfully')
        return credentials
        
    except secrets_client.exceptions.ResourceNotFoundException:
        raise AuthenticationError(f'Secret not found: {SECRET_NAME}')
    except secrets_client.exceptions.InvalidRequestException as e:
        raise AuthenticationError(f'Invalid request to Secrets Manager: {str(e)}')
    except Exception as e:
        raise AuthenticationError(f'Failed to retrieve credentials: {str(e)}')


@xray_recorder.capture('refresh_credentials')
@exponential_backoff_retry(
    max_attempts=3,
    initial_delay=2.0,
    backoff_rate=2.0,
    retryable_exceptions=(Exception,)
)
def refresh_credentials(credentials: Dict[str, Any]) -> Dict[str, Any]:
    """
    Refresh authentication credentials using provider's OAuth flow.
    
    Args:
        credentials: Current credentials
        
    Returns:
        Updated credentials with new tokens
        
    Raises:
        AuthenticationError: If refresh fails
    """
    try:
        # Get provider instance
        provider = ProviderFactory.get_provider(PROVIDER_NAME)
        
        # Force token refresh by removing access_token
        refresh_credentials_copy = {
            **credentials,
            'access_token': '',  # Force refresh
            'token_timestamp': ''
        }
        
        # Authenticate (provider will handle token refresh)
        auth_result = provider.authenticate(refresh_credentials_copy)
        
        # Update credentials with new token
        updated_credentials = {
            **credentials,
            'access_token': auth_result['access_token'],
            'refresh_token': auth_result.get('refresh_token', credentials.get('refresh_token')),
            'user_id': auth_result.get('user_id', credentials.get('user_id')),
            'token_timestamp': datetime.utcnow().isoformat() + 'Z',
            'last_rotated': datetime.utcnow().isoformat() + 'Z',
            'rotation_count': credentials.get('rotation_count', 0) + 1
        }
        
        logger.info('Credentials refreshed successfully', extra={
            'rotation_count': updated_credentials['rotation_count']
        })
        
        return updated_credentials
        
    except Exception as e:
        raise AuthenticationError(f'Credential refresh failed: {str(e)}')


@xray_recorder.capture('test_credentials')
def test_credentials(credentials: Dict[str, Any]) -> None:
    """
    Test new credentials by making a test API call.
    
    Args:
        credentials: Credentials to test
        
    Raises:
        AuthenticationError: If credentials are invalid
    """
    try:
        # Get provider instance
        provider = ProviderFactory.get_provider(PROVIDER_NAME)
        
        # Test by listing media (with limit of 1 to minimize API usage)
        auth_token = credentials.get('access_token', '')
        user_id = credentials.get('user_id', '')
        
        if not auth_token or not user_id:
            raise AuthenticationError('Missing access_token or user_id in credentials')
        
        # Make a test API call
        logger.info('Testing credentials with API call')
        media_list = provider.list_media(
            auth_token=auth_token,
            user_id=user_id,
            page_size=1,
            max_videos=1
        )
        
        logger.info(f'Credentials test successful, retrieved {len(media_list)} media items')
        
    except Exception as e:
        raise AuthenticationError(f'Credential test failed: {str(e)}')


@xray_recorder.capture('store_credentials')
def store_credentials(credentials: Dict[str, Any]) -> None:
    """
    Store updated credentials in AWS Secrets Manager.
    
    Args:
        credentials: Updated credentials to store
        
    Raises:
        AuthenticationError: If credentials cannot be stored
    """
    try:
        logger.info(f'Storing updated credentials in Secrets Manager: {SECRET_NAME}')
        
        secrets_client.update_secret(
            SecretId=SECRET_NAME,
            SecretString=json.dumps(credentials)
        )
        
        logger.info('Credentials stored successfully')
        
    except Exception as e:
        raise AuthenticationError(f'Failed to store credentials: {str(e)}')


@xray_recorder.capture('publish_rotation_metrics')
def publish_rotation_metrics(success: bool, duration: float) -> None:
    """
    Publish rotation metrics to CloudWatch.
    
    Args:
        success: Whether rotation was successful
        duration: Rotation duration in seconds
    """
    try:
        metrics_publisher = MetricsPublisher(namespace='CloudSync/SecretsRotation')
        
        # Publish success/failure metric
        metrics_publisher.put_metric(
            metric_name='RotationSuccess' if success else 'RotationFailure',
            value=1,
            unit='Count',
            dimensions={'Provider': PROVIDER_NAME}
        )
        
        # Publish duration metric
        metrics_publisher.put_metric(
            metric_name='RotationDuration',
            value=duration,
            unit='Seconds',
            dimensions={'Provider': PROVIDER_NAME}
        )
        
        logger.info(f'Published rotation metrics: success={success}, duration={duration}s')
        
    except Exception as e:
        logger.error(f'Failed to publish rotation metrics: {str(e)}')


def send_notification(
    subject: str,
    message: str,
    correlation_id: str,
    success: bool,
    error: str = None
) -> None:
    """
    Send notification to SNS topic.
    
    Args:
        subject: Notification subject
        message: Notification message
        correlation_id: Correlation ID for tracking
        success: Whether rotation was successful
        error: Error message if failed
    """
    try:
        if not SNS_TOPIC_ARN:
            logger.warning('SNS_TOPIC_ARN not configured, skipping notification')
            return
        
        notification_body = {
            'message': message,
            'provider': PROVIDER_NAME,
            'success': success,
            'correlation_id': correlation_id,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'function': 'secrets-rotator'
        }
        
        if error:
            notification_body['error'] = error
        
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=json.dumps(notification_body, indent=2)
        )
        
        logger.info('Notification sent to SNS')
        
    except Exception as e:
        logger.error(f'Failed to send notification: {str(e)}')
