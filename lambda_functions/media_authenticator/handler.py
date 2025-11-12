"""
Media Authenticator Lambda Function

Authenticates with cloud provider APIs and manages credential lifecycle.
Retrieves credentials from Secrets Manager, checks token expiration,
and refreshes tokens when necessary.
"""

import json
import os
import boto3
from datetime import datetime, timedelta
from typing import Dict, Any
from aws_xray_sdk.core import xray_recorder
from cloud_sync_common.logging_utils import get_logger
from cloud_sync_common.correlation import get_or_create_correlation_id
from cloud_sync_common.exceptions import AuthenticationError
from cloud_sync_common.provider_interface import ProviderFactory

# Initialize AWS clients
secrets_client = boto3.client('secretsmanager')
logger = get_logger(__name__)

# Environment variables
SECRET_NAME = os.environ.get('SECRET_NAME', 'gopro/credentials')
TOKEN_EXPIRY_HOURS = int(os.environ.get('TOKEN_EXPIRY_HOURS', '24'))


@xray_recorder.capture('lambda_handler')
def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for media authentication.
    
    Args:
        event: Lambda event containing provider and action
        context: Lambda context
        
    Returns:
        Authentication response with token and user info
    """
    # Set up correlation ID
    correlation_id = get_or_create_correlation_id(event)
    xray_recorder.put_annotation('correlation_id', correlation_id)
    
    logger.info('Media Authenticator invoked', extra={
        'event': event,
        'correlation_id': correlation_id
    })
    
    try:
        provider_name = event.get('provider', 'gopro')
        action = event.get('action', 'authenticate')
        
        xray_recorder.put_annotation('provider', provider_name)
        xray_recorder.put_annotation('action', action)
        
        if action != 'authenticate':
            raise ValueError(f"Unsupported action: {action}")
        
        # Retrieve credentials from Secrets Manager
        credentials = retrieve_credentials()
        
        # Check if token needs refresh
        if needs_token_refresh(credentials):
            logger.info('Token expired or expiring soon, refreshing')
            credentials = refresh_token(provider_name, credentials)
        else:
            logger.info('Token still valid, using existing token')
        
        # Return authentication response
        response = {
            'statusCode': 200,
            'provider': provider_name,
            'auth_token': credentials['access_token'],
            'user_id': credentials.get('user_id', ''),
            'expires_at': credentials.get('token_timestamp', ''),
            'correlation_id': correlation_id
        }
        
        logger.info('Authentication successful', extra={
            'provider': provider_name,
            'user_id': credentials.get('user_id', ''),
            'correlation_id': correlation_id
        })
        
        return response
        
    except AuthenticationError as e:
        logger.error(f'Authentication failed: {str(e)}', extra={
            'error_type': 'AuthenticationError',
            'correlation_id': correlation_id
        }, exc_info=True)
        
        # Publish SNS alert for authentication failures
        publish_alert(f'Authentication failed: {str(e)}', correlation_id)
        
        return {
            'statusCode': 401,
            'error': 'AuthenticationError',
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


@xray_recorder.capture('needs_token_refresh')
def needs_token_refresh(credentials: Dict[str, Any]) -> bool:
    """
    Check if authentication token needs to be refreshed.
    
    Args:
        credentials: Credentials dictionary
        
    Returns:
        True if token needs refresh, False otherwise
    """
    # Check if access_token exists
    if not credentials.get('access_token'):
        logger.info('No access token found, refresh needed')
        return True
    
    # Check token timestamp
    token_timestamp = credentials.get('token_timestamp')
    if not token_timestamp:
        logger.info('No token timestamp found, refresh needed')
        return True
    
    try:
        # Parse timestamp
        token_time = datetime.fromisoformat(token_timestamp.replace('Z', '+00:00'))
        current_time = datetime.now(token_time.tzinfo)
        
        # Calculate time until expiration
        time_since_refresh = current_time - token_time
        hours_since_refresh = time_since_refresh.total_seconds() / 3600
        
        # Refresh if token is older than (24 - TOKEN_EXPIRY_HOURS) hours
        # This ensures we refresh before expiration
        refresh_threshold = 24 - TOKEN_EXPIRY_HOURS
        needs_refresh = hours_since_refresh >= refresh_threshold
        
        logger.info(f'Token age: {hours_since_refresh:.2f} hours, threshold: {refresh_threshold} hours, needs_refresh: {needs_refresh}')
        
        return needs_refresh
        
    except Exception as e:
        logger.warning(f'Error parsing token timestamp: {str(e)}, will refresh token')
        return True


@xray_recorder.capture('refresh_token')
def refresh_token(provider_name: str, credentials: Dict[str, Any]) -> Dict[str, Any]:
    """
    Refresh authentication token using provider's OAuth flow.
    
    Args:
        provider_name: Name of the provider
        credentials: Current credentials
        
    Returns:
        Updated credentials with new token
        
    Raises:
        AuthenticationError: If token refresh fails
    """
    try:
        # Get provider instance
        provider = ProviderFactory.get_provider(provider_name)
        
        # Authenticate (provider will handle token refresh)
        auth_result = provider.authenticate(credentials)
        
        # Update credentials with new token
        updated_credentials = {
            **credentials,
            'access_token': auth_result['access_token'],
            'refresh_token': auth_result.get('refresh_token', credentials.get('refresh_token')),
            'token_timestamp': datetime.utcnow().isoformat() + 'Z',
            'last_updated': datetime.utcnow().isoformat() + 'Z'
        }
        
        # Store updated credentials in Secrets Manager
        store_credentials(updated_credentials)
        
        logger.info('Token refreshed and stored successfully')
        
        return updated_credentials
        
    except Exception as e:
        raise AuthenticationError(f'Token refresh failed: {str(e)}')


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


def publish_alert(message: str, correlation_id: str) -> None:
    """
    Publish alert to SNS topic.
    
    Args:
        message: Alert message
        correlation_id: Correlation ID for tracking
    """
    try:
        sns_topic_arn = os.environ.get('SNS_TOPIC_ARN')
        if not sns_topic_arn:
            logger.warning('SNS_TOPIC_ARN not configured, skipping alert')
            return
        
        sns_client = boto3.client('sns')
        sns_client.publish(
            TopicArn=sns_topic_arn,
            Subject='GoPro Sync Authentication Failure',
            Message=json.dumps({
                'message': message,
                'correlation_id': correlation_id,
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'function': 'media-authenticator'
            }, indent=2)
        )
        
        logger.info('Alert published to SNS')
        
    except Exception as e:
        logger.error(f'Failed to publish alert: {str(e)}')
