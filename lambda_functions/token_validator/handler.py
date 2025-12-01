"""
Token Validator Lambda Function

Validates GoPro Cloud authentication cookies before sync operations.
Makes test API call to verify cookies are still valid and alerts if expired.
"""

import json
import os
import boto3
import requests
from datetime import datetime
from typing import Dict, Any
from aws_xray_sdk.core import xray_recorder
from cloud_sync_common.logging_utils import get_logger
from cloud_sync_common.metrics_utils import MetricsPublisher
from cloud_sync_common.correlation import get_or_create_correlation_id
from cloud_sync_common.exceptions import AuthenticationError, TokenExpiredError

# Initialize AWS clients
secrets_client = boto3.client('secretsmanager')
sns_client = boto3.client('sns')
logger = get_logger(__name__)
metrics_publisher = MetricsPublisher(namespace='CloudSync/TokenValidation')

# Environment variables
SECRET_NAME = os.environ.get('SECRET_NAME', 'gopro/credentials')
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN')
GOPRO_API_URL = 'https://api.gopro.com/media/search'


@xray_recorder.capture('lambda_handler')
def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for token validation.
    
    Args:
        event: Lambda event (can be empty)
        context: Lambda context
        
    Returns:
        Validation response with status
    """
    # Set up correlation ID
    correlation_id = get_or_create_correlation_id(event)
    xray_recorder.put_annotation('correlation_id', correlation_id)
    
    logger.info('Token Validator invoked', extra={
        'correlation_id': correlation_id
    })
    
    start_time = datetime.utcnow()
    
    try:
        # Retrieve credentials from Secrets Manager
        credentials = retrieve_credentials()
        
        # Calculate cookie age
        cookie_age_days = calculate_cookie_age(credentials)
        
        # Publish cookie age metric
        metrics_publisher.put_metric(
            metric_name='CookieAgeDays',
            value=cookie_age_days,
            unit='None'
        )
        
        # Validate cookies with test API call
        validation_result = validate_cookies(credentials, correlation_id)
        
        # Calculate validation duration
        duration = (datetime.utcnow() - start_time).total_seconds()
        
        # Publish success metrics
        metrics_publisher.put_metrics([
            {
                'metric_name': 'ValidationSuccess',
                'value': 1,
                'unit': 'Count'
            },
            {
                'metric_name': 'ValidationDuration',
                'value': duration,
                'unit': 'Seconds'
            }
        ])
        
        logger.info('Token validation successful', extra={
            'cookie_age_days': cookie_age_days,
            'duration_seconds': duration,
            'correlation_id': correlation_id
        })
        
        return {
            'statusCode': 200,
            'valid': True,
            'cookie_age_days': cookie_age_days,
            'validation_method': validation_result['method'],
            'duration_seconds': duration,
            'correlation_id': correlation_id
        }
        
    except TokenExpiredError as e:
        # Calculate cookie age even on failure
        try:
            credentials = retrieve_credentials()
            cookie_age_days = calculate_cookie_age(credentials)
        except:
            cookie_age_days = 999.0  # Unknown age
        
        logger.error(f'Tokens expired: {str(e)}', extra={
            'error_type': 'TokenExpiredError',
            'correlation_id': correlation_id
        })
        
        # Publish failure metric
        metrics_publisher.put_metric(
            metric_name='ValidationFailure',
            value=1,
            unit='Count'
        )
        
        # Publish expiration alert
        publish_expiration_alert(str(e), correlation_id)
        
        return {
            'statusCode': 401,
            'valid': False,
            'cookie_age_days': cookie_age_days,
            'error': 'TokenExpiredError',
            'message': str(e),
            'correlation_id': correlation_id
        }
        
    except Exception as e:
        # Calculate cookie age even on failure
        try:
            credentials = retrieve_credentials()
            cookie_age_days = calculate_cookie_age(credentials)
        except:
            cookie_age_days = 999.0  # Unknown age
        
        logger.error(f'Validation error: {str(e)}', extra={
            'error_type': type(e).__name__,
            'correlation_id': correlation_id
        }, exc_info=True)
        
        # Publish failure metric
        metrics_publisher.put_metric(
            metric_name='ValidationFailure',
            value=1,
            unit='Count'
        )
        
        return {
            'statusCode': 500,
            'valid': False,
            'cookie_age_days': cookie_age_days,
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


@xray_recorder.capture('calculate_cookie_age')
def calculate_cookie_age(credentials: Dict[str, Any]) -> float:
    """
    Calculate age of cookies in days.
    
    Args:
        credentials: Credentials dictionary
        
    Returns:
        Age in days
    """
    try:
        last_updated = credentials.get('last_updated')
        if not last_updated:
            logger.warning('No last_updated timestamp found')
            return 0.0
        
        # Parse timestamp
        updated_time = datetime.fromisoformat(last_updated.replace('Z', '+00:00'))
        current_time = datetime.now(updated_time.tzinfo)
        
        # Calculate age in days
        age = (current_time - updated_time).total_seconds() / 86400
        
        logger.info(f'Cookie age: {age:.2f} days')
        return age
        
    except Exception as e:
        logger.warning(f'Error calculating cookie age: {str(e)}')
        return 0.0


@xray_recorder.capture('validate_cookies')
def validate_cookies(credentials: Dict[str, Any], correlation_id: str) -> Dict[str, Any]:
    """
    Validate cookies by making test API call to GoPro.
    
    Implements fallback strategy from COOKIE_TESTING_STRATEGY.md:
    1. Try minimal cookies (gp_access_token + gp_user_id)
    2. Fall back to full cookie header if minimal fails
    
    Args:
        credentials: Credentials dictionary
        correlation_id: Correlation ID for tracking
        
    Returns:
        Validation result with method used
        
    Raises:
        TokenExpiredError: If tokens are expired (401/403)
        AuthenticationError: If validation fails for other reasons
    """
    cookies = credentials.get('cookies', '')
    user_agent = credentials.get('user-agent', 
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36')
    
    if not cookies:
        raise AuthenticationError('No cookies found in credentials')
    
    # Phase 1: Try minimal cookies approach
    gp_access_token = extract_cookie_value(cookies, 'gp_access_token')
    gp_user_id = extract_cookie_value(cookies, 'gp_user_id')
    
    if gp_access_token and gp_user_id:
        logger.info('Trying validation with minimal cookies')
        minimal_cookies = f'gp_access_token={gp_access_token}; gp_user_id={gp_user_id}'
        
        result = test_api_call(minimal_cookies, user_agent, correlation_id)
        if result['success']:
            logger.info('Validation successful with minimal cookies')
            return {'method': 'minimal_cookies', 'http_code': result['http_code']}
    
    # Phase 2: Fall back to full cookie header
    logger.info('Trying validation with full cookie header')
    result = test_api_call(cookies, user_agent, correlation_id)
    
    if result['success']:
        logger.info('Validation successful with full cookies')
        return {'method': 'full_cookies', 'http_code': result['http_code']}
    
    # If we get here, validation failed
    if result['http_code'] in [401, 403]:
        raise TokenExpiredError(
            'Cookies are expired or invalid. Manual refresh required. '
            'Follow docs/TOKEN_EXTRACTION_GUIDE.md to extract fresh cookies.'
        )
    else:
        raise AuthenticationError(
            f'Cookie validation failed with HTTP {result["http_code"]}: {result.get("error", "Unknown error")}'
        )


@xray_recorder.capture('test_api_call')
def test_api_call(cookies: str, user_agent: str, correlation_id: str) -> Dict[str, Any]:
    """
    Make test API call to GoPro.
    
    Args:
        cookies: Cookie string to test
        user_agent: User agent string
        correlation_id: Correlation ID for tracking
        
    Returns:
        Dictionary with success status and HTTP code
    """
    headers = {
        'Cookie': cookies,
        'User-Agent': user_agent,
        'Accept': 'application/vnd.gopro.jk.media+json; version=2.0.0',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://gopro.com/',
        'X-Correlation-ID': correlation_id
    }
    
    try:
        response = requests.get(
            GOPRO_API_URL,
            headers=headers,
            params={'per_page': 1},
            timeout=10
        )
        
        logger.info(f'API test call returned HTTP {response.status_code}')
        
        return {
            'success': response.status_code == 200,
            'http_code': response.status_code
        }
        
    except requests.exceptions.Timeout:
        logger.error('API test call timed out')
        return {
            'success': False,
            'http_code': 0,
            'error': 'Request timeout'
        }
    except Exception as e:
        logger.error(f'API test call failed: {str(e)}')
        return {
            'success': False,
            'http_code': 0,
            'error': str(e)
        }


def extract_cookie_value(cookie_string: str, cookie_name: str) -> str:
    """
    Extract value of a specific cookie from cookie string.
    
    Args:
        cookie_string: Full cookie header value
        cookie_name: Name of cookie to extract
        
    Returns:
        Cookie value or empty string if not found
    """
    try:
        # Split by semicolon and look for the cookie
        for part in cookie_string.split(';'):
            part = part.strip()
            if part.startswith(f'{cookie_name}='):
                return part[len(cookie_name)+1:]
        return ''
    except Exception as e:
        logger.warning(f'Error extracting cookie {cookie_name}: {str(e)}')
        return ''


def publish_expiration_alert(message: str, correlation_id: str) -> None:
    """
    Publish token expiration alert to SNS topic.
    
    Args:
        message: Alert message
        correlation_id: Correlation ID for tracking
    """
    if not SNS_TOPIC_ARN:
        logger.warning('SNS_TOPIC_ARN not configured, skipping alert')
        return
    
    try:
        alert_message = {
            'alert_type': 'TOKEN_EXPIRATION',
            'severity': 'HIGH',
            'message': message,
            'correlation_id': correlation_id,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'function': 'token-validator',
            'action_required': 'Manual cookie refresh required',
            'documentation': 'See docs/TOKEN_EXTRACTION_GUIDE.md for instructions'
        }
        
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject='🔴 GoPro Sync: Cookies Expired - Manual Refresh Required',
            Message=json.dumps(alert_message, indent=2)
        )
        
        logger.info('Expiration alert published to SNS', extra={
            'correlation_id': correlation_id
        })
        
    except Exception as e:
        logger.error(f'Failed to publish alert: {str(e)}', exc_info=True)
