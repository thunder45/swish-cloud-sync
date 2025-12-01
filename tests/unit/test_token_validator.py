"""
Unit tests for Token Validator Lambda function.
"""

import json
import pytest
import requests
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
import sys
import os

# Add lambda function and layer to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda_functions/token_validator'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda_layer/python'))

import handler


@pytest.fixture
def valid_credentials():
    """Fixture for valid credentials."""
    return {
        'gp-access-token': 'eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ.test_token',
        'cookies': 'gp_access_token=eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ.test_token; gp_user_id=test-uuid-1234',
        'user-agent': 'Mozilla/5.0 (Test)',
        'last_updated': datetime.utcnow().isoformat() + 'Z'
    }


@pytest.fixture
def expired_credentials():
    """Fixture for expired credentials."""
    expired_time = (datetime.utcnow() - timedelta(days=90)).isoformat() + 'Z'
    return {
        'gp-access-token': 'eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ.expired_token',
        'cookies': 'gp_access_token=eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ.expired_token; gp_user_id=test-uuid-1234',
        'user-agent': 'Mozilla/5.0 (Test)',
        'last_updated': expired_time
    }


@pytest.fixture
def lambda_context():
    """Fixture for Lambda context."""
    context = Mock()
    context.function_name = 'token-validator'
    context.function_version = '1'
    context.invoked_function_arn = 'arn:aws:lambda:us-east-1:123456789012:function:token-validator'
    context.memory_limit_in_mb = 256
    context.aws_request_id = 'test-request-id'
    return context


class TestTokenValidator:
    """Tests for Token Validator Lambda handler."""
    
    @patch('handler.secrets_client')
    @patch('handler.requests.get')
    @patch('handler.metrics_publisher')
    def test_handler_success_minimal_cookies(self, mock_metrics_publisher, mock_requests_get, mock_secrets_client, valid_credentials, lambda_context):
        """Test successful validation with minimal cookies."""
        # Mock Secrets Manager response
        mock_secrets_client.get_secret_value.return_value = {
            'SecretString': json.dumps(valid_credentials)
        }
        
        # Mock successful API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_requests_get.return_value = mock_response
        
        # Call handler
        event = {'correlation_id': 'test-correlation-id'}
        result = handler.handler(event, lambda_context)
        
        # Verify response
        assert result['statusCode'] == 200
        assert result['valid'] is True
        assert result['validation_method'] == 'minimal_cookies'
        assert 'cookie_age_days' in result
        assert 'correlation_id' in result
        
        # Verify API was called with minimal cookies
        call_args = mock_requests_get.call_args
        headers = call_args[1]['headers']
        assert 'gp_access_token=' in headers['Cookie']
        assert 'gp_user_id=' in headers['Cookie']
        
        # Verify metrics were published
        assert mock_metrics_publisher.put_metric.called or mock_metrics_publisher.put_metrics.called
        
    @patch('handler.secrets_client')
    @patch('handler.requests.get')
    @patch('handler.metrics_publisher')
    def test_handler_success_full_cookies_fallback(self, mock_metrics_publisher, mock_requests_get, mock_secrets_client, valid_credentials, lambda_context):
        """Test successful validation with full cookies after minimal fails."""
        # Mock Secrets Manager response
        mock_secrets_client.get_secret_value.return_value = {
            'SecretString': json.dumps(valid_credentials)
        }
        
        # Mock API responses - first call fails, second succeeds
        mock_response_fail = Mock()
        mock_response_fail.status_code = 400
        
        mock_response_success = Mock()
        mock_response_success.status_code = 200
        
        mock_requests_get.side_effect = [mock_response_fail, mock_response_success]
        
        # Call handler
        event = {}
        result = handler.handler(event, lambda_context)
        
        # Verify response
        assert result['statusCode'] == 200
        assert result['valid'] is True
        assert result['validation_method'] == 'full_cookies'
        
        # Verify two API calls were made
        assert mock_requests_get.call_count == 2
        
    @patch('handler.secrets_client')
    @patch('handler.requests.get')
    @patch('handler.metrics_publisher')
    @patch('handler.publish_expiration_alert')
    def test_handler_token_expired(self, mock_publish_alert, mock_metrics_publisher, mock_requests_get, mock_secrets_client, expired_credentials, lambda_context):
        """Test handling of expired tokens."""
        # Mock Secrets Manager response
        mock_secrets_client.get_secret_value.return_value = {
            'SecretString': json.dumps(expired_credentials)
        }
        
        # Mock 401 response (expired)
        mock_response = Mock()
        mock_response.status_code = 401
        mock_requests_get.return_value = mock_response
        
        # Call handler
        event = {}
        result = handler.handler(event, lambda_context)
        
        # Verify response
        assert result['statusCode'] == 401
        assert result['valid'] is False
        assert result['error'] == 'TokenExpiredError'
        
        # Verify alert was published
        assert mock_publish_alert.called
        
        # Verify failure metric was published
        assert mock_metrics_publisher.put_metric.called
        
    @patch('handler.secrets_client')
    def test_handler_missing_secret(self, mock_secrets_client, lambda_context):
        """Test handling when secret doesn't exist."""
        # Mock secret not found
        mock_secrets_client.get_secret_value.side_effect = \
            mock_secrets_client.exceptions.ResourceNotFoundException(
                {'Error': {'Code': 'ResourceNotFoundException'}}, 'GetSecretValue')
        
        # Call handler
        event = {}
        result = handler.handler(event, lambda_context)
        
        # Verify error response
        assert result['statusCode'] == 500
        assert result['valid'] is False
        
    @patch('handler.secrets_client')
    @patch('handler.requests.get')
    def test_handler_api_timeout(self, mock_requests_get, mock_secrets_client, valid_credentials, lambda_context):
        """Test handling of API timeout."""
        # Mock Secrets Manager response
        mock_secrets_client.get_secret_value.return_value = {
            'SecretString': json.dumps(valid_credentials)
        }
        
        # Mock timeout
        mock_requests_get.side_effect = requests.exceptions.Timeout()
        
        # Call handler
        event = {}
        result = handler.handler(event, lambda_context)
        
        # Verify error response
        assert result['statusCode'] == 500
        assert result['valid'] is False


class TestCalculateCookieAge:
    """Tests for calculate_cookie_age function."""
    
    def test_recent_cookies(self):
        """Test age calculation for recent cookies."""
        credentials = {
            'last_updated': datetime.utcnow().isoformat() + 'Z'
        }
        
        age = handler.calculate_cookie_age(credentials)
        
        assert age >= 0
        assert age < 1  # Less than 1 day old
        
    def test_old_cookies(self):
        """Test age calculation for old cookies."""
        old_time = (datetime.utcnow() - timedelta(days=30)).isoformat() + 'Z'
        credentials = {
            'last_updated': old_time
        }
        
        age = handler.calculate_cookie_age(credentials)
        
        assert age >= 29
        assert age <= 31
        
    def test_missing_timestamp(self):
        """Test handling of missing timestamp."""
        credentials = {}
        
        age = handler.calculate_cookie_age(credentials)
        
        assert age == 0.0


class TestExtractCookieValue:
    """Tests for extract_cookie_value function."""
    
    def test_extract_existing_cookie(self):
        """Test extracting existing cookie."""
        cookie_string = 'cookie1=value1; cookie2=value2; cookie3=value3'
        
        value = handler.extract_cookie_value(cookie_string, 'cookie2')
        
        assert value == 'value2'
        
    def test_extract_nonexistent_cookie(self):
        """Test extracting non-existent cookie."""
        cookie_string = 'cookie1=value1; cookie2=value2'
        
        value = handler.extract_cookie_value(cookie_string, 'cookie3')
        
        assert value == ''
        
    def test_extract_first_cookie(self):
        """Test extracting first cookie in string."""
        cookie_string = 'first=value1; second=value2'
        
        value = handler.extract_cookie_value(cookie_string, 'first')
        
        assert value == 'value1'
        
    def test_extract_last_cookie(self):
        """Test extracting last cookie in string."""
        cookie_string = 'first=value1; second=value2; last=value3'
        
        value = handler.extract_cookie_value(cookie_string, 'last')
        
        assert value == 'value3'
        
    def test_extract_with_spaces(self):
        """Test extracting cookie with spaces."""
        cookie_string = 'cookie1=value1;  cookie2=value2  ; cookie3=value3'
        
        value = handler.extract_cookie_value(cookie_string, 'cookie2')
        
        assert value == 'value2'
        
    def test_extract_gp_access_token(self):
        """Test extracting gp_access_token."""
        token = 'eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkExMjhHQ00ifQ.test'
        cookie_string = f'gp_access_token={token}; gp_user_id=uuid-1234'
        
        value = handler.extract_cookie_value(cookie_string, 'gp_access_token')
        
        assert value == token
        
    def test_extract_gp_user_id(self):
        """Test extracting gp_user_id."""
        cookie_string = 'gp_access_token=token123; gp_user_id=uuid-1234-5678'
        
        value = handler.extract_cookie_value(cookie_string, 'gp_user_id')
        
        assert value == 'uuid-1234-5678'


class TestValidateCookies:
    """Tests for validate_cookies function."""
    
    @patch('handler.test_api_call')
    def test_validate_with_minimal_cookies_success(self, mock_test_api_call):
        """Test validation succeeds with minimal cookies."""
        credentials = {
            'cookies': 'gp_access_token=token123; gp_user_id=uuid-1234; session=abc',
            'user-agent': 'Mozilla/5.0'
        }
        
        # Mock successful API call
        mock_test_api_call.return_value = {
            'success': True,
            'http_code': 200
        }
        
        result = handler.validate_cookies(credentials, 'test-correlation-id')
        
        assert result['method'] == 'minimal_cookies'
        assert result['http_code'] == 200
        
        # Verify minimal cookies were used
        call_args = mock_test_api_call.call_args
        cookies_used = call_args[0][0]
        assert cookies_used == 'gp_access_token=token123; gp_user_id=uuid-1234'
        
    @patch('handler.test_api_call')
    def test_validate_with_full_cookies_fallback(self, mock_test_api_call):
        """Test validation falls back to full cookies."""
        credentials = {
            'cookies': 'gp_access_token=token123; gp_user_id=uuid-1234; session=abc',
            'user-agent': 'Mozilla/5.0'
        }
        
        # Mock: minimal fails, full succeeds
        mock_test_api_call.side_effect = [
            {'success': False, 'http_code': 400},
            {'success': True, 'http_code': 200}
        ]
        
        result = handler.validate_cookies(credentials, 'test-correlation-id')
        
        assert result['method'] == 'full_cookies'
        assert result['http_code'] == 200
        
        # Verify both calls were made
        assert mock_test_api_call.call_count == 2
        
    @patch('handler.test_api_call')
    def test_validate_raises_token_expired_on_401(self, mock_test_api_call):
        """Test validation raises TokenExpiredError on 401."""
        credentials = {
            'cookies': 'gp_access_token=token123; gp_user_id=uuid-1234',
            'user-agent': 'Mozilla/5.0'
        }
        
        # Mock 401 response
        mock_test_api_call.return_value = {
            'success': False,
            'http_code': 401
        }
        
        from cloud_sync_common.exceptions import TokenExpiredError
        
        with pytest.raises(TokenExpiredError) as exc_info:
            handler.validate_cookies(credentials, 'test-correlation-id')
        
        assert 'expired or invalid' in str(exc_info.value).lower()
        
    @patch('handler.test_api_call')
    def test_validate_raises_token_expired_on_403(self, mock_test_api_call):
        """Test validation raises TokenExpiredError on 403."""
        credentials = {
            'cookies': 'gp_access_token=token123; gp_user_id=uuid-1234',
            'user-agent': 'Mozilla/5.0'
        }
        
        # Mock 403 response
        mock_test_api_call.return_value = {
            'success': False,
            'http_code': 403
        }
        
        from cloud_sync_common.exceptions import TokenExpiredError
        
        with pytest.raises(TokenExpiredError) as exc_info:
            handler.validate_cookies(credentials, 'test-correlation-id')
        
        assert 'expired or invalid' in str(exc_info.value).lower()
        
    @patch('handler.test_api_call')
    def test_validate_raises_auth_error_on_unexpected_code(self, mock_test_api_call):
        """Test validation raises AuthenticationError on unexpected HTTP code."""
        credentials = {
            'cookies': 'gp_access_token=token123; gp_user_id=uuid-1234',
            'user-agent': 'Mozilla/5.0'
        }
        
        # Mock unexpected response
        mock_test_api_call.return_value = {
            'success': False,
            'http_code': 500,
            'error': 'Internal server error'
        }
        
        from cloud_sync_common.exceptions import AuthenticationError
        
        with pytest.raises(AuthenticationError) as exc_info:
            handler.validate_cookies(credentials, 'test-correlation-id')
        
        assert '500' in str(exc_info.value)
        
    def test_validate_missing_cookies(self):
        """Test validation fails when cookies missing."""
        credentials = {
            'user-agent': 'Mozilla/5.0'
        }
        
        from cloud_sync_common.exceptions import AuthenticationError
        
        with pytest.raises(AuthenticationError) as exc_info:
            handler.validate_cookies(credentials, 'test-correlation-id')
        
        assert 'No cookies found' in str(exc_info.value)


class TestTestApiCall:
    """Tests for test_api_call function."""
    
    @patch('handler.requests.get')
    def test_api_call_success(self, mock_requests_get):
        """Test successful API call."""
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_requests_get.return_value = mock_response
        
        result = handler.test_api_call(
            'gp_access_token=token; gp_user_id=uuid',
            'Mozilla/5.0',
            'correlation-id'
        )
        
        assert result['success'] is True
        assert result['http_code'] == 200
        
        # Verify headers were set correctly
        call_args = mock_requests_get.call_args
        headers = call_args[1]['headers']
        assert headers['Cookie'] == 'gp_access_token=token; gp_user_id=uuid'
        assert headers['User-Agent'] == 'Mozilla/5.0'
        assert headers['Accept'] == 'application/vnd.gopro.jk.media+json; version=2.0.0'
        assert headers['X-Correlation-ID'] == 'correlation-id'
        
    @patch('handler.requests.get')
    def test_api_call_timeout(self, mock_requests_get):
        """Test API call timeout handling."""
        # Mock timeout
        mock_requests_get.side_effect = requests.exceptions.Timeout()
        
        result = handler.test_api_call(
            'cookies',
            'Mozilla/5.0',
            'correlation-id'
        )
        
        assert result['success'] is False
        assert result['http_code'] == 0
        assert 'timeout' in result['error'].lower()
        
    @patch('handler.requests.get')
    def test_api_call_connection_error(self, mock_requests_get):
        """Test API call connection error handling."""
        # Mock connection error
        mock_requests_get.side_effect = requests.exceptions.ConnectionError('Connection failed')
        
        result = handler.test_api_call(
            'cookies',
            'Mozilla/5.0',
            'correlation-id'
        )
        
        assert result['success'] is False
        assert result['http_code'] == 0
        assert 'error' in result


class TestRetrieveCredentials:
    """Tests for retrieve_credentials function."""
    
    @patch('handler.secrets_client')
    def test_retrieve_success(self, mock_secrets_client, valid_credentials):
        """Test successful credential retrieval."""
        mock_secrets_client.get_secret_value.return_value = {
            'SecretString': json.dumps(valid_credentials)
        }
        
        result = handler.retrieve_credentials()
        
        assert result == valid_credentials
        
    @patch('handler.secrets_client')
    def test_retrieve_not_found(self, mock_secrets_client):
        """Test handling when secret not found."""
        # Create a proper exception class
        class ResourceNotFoundException(Exception):
            pass
        
        mock_secrets_client.exceptions.ResourceNotFoundException = ResourceNotFoundException
        mock_secrets_client.get_secret_value.side_effect = ResourceNotFoundException('Secret not found')
        
        from cloud_sync_common.exceptions import AuthenticationError
        
        with pytest.raises(AuthenticationError) as exc_info:
            handler.retrieve_credentials()
        
        assert 'Secret not found' in str(exc_info.value)


class TestPublishExpirationAlert:
    """Tests for publish_expiration_alert function."""
    
    @patch('handler.sns_client')
    @patch('handler.SNS_TOPIC_ARN', 'arn:aws:sns:us-east-1:123456789012:test-topic')
    def test_publish_alert_success(self, mock_sns_client):
        """Test successful alert publishing."""
        handler.publish_expiration_alert('Test message', 'test-correlation-id')
        
        # Verify SNS publish was called
        assert mock_sns_client.publish.called
        call_args = mock_sns_client.publish.call_args
        
        # Verify message structure
        message = json.loads(call_args[1]['Message'])
        assert message['alert_type'] == 'TOKEN_EXPIRATION'
        assert message['severity'] == 'HIGH'
        assert message['correlation_id'] == 'test-correlation-id'
        assert 'action_required' in message
        
    @patch('handler.SNS_TOPIC_ARN', None)
    def test_publish_alert_no_topic_configured(self):
        """Test handling when SNS topic not configured."""
        # Should not raise error
        handler.publish_expiration_alert('Test message', 'test-correlation-id')
        
    @patch('handler.sns_client')
    @patch('handler.SNS_TOPIC_ARN', 'arn:aws:sns:us-east-1:123456789012:test-topic')
    def test_publish_alert_sns_error(self, mock_sns_client):
        """Test handling of SNS publish error."""
        mock_sns_client.publish.side_effect = Exception('SNS error')
        
        # Should not raise error (logs and continues)
        handler.publish_expiration_alert('Test message', 'test-correlation-id')
