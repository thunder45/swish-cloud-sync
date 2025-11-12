"""
Unit tests for Media Authenticator Lambda function
"""

import sys
import os
from pathlib import Path

# Add lambda_layer/python to path for imports
lambda_layer_path = Path(__file__).parent.parent.parent / 'lambda_layer' / 'python'
sys.path.insert(0, str(lambda_layer_path))

import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from botocore.exceptions import ClientError


# Mock the Lambda handler module
@pytest.fixture
def mock_env(monkeypatch):
    """Set up environment variables"""
    monkeypatch.setenv('SECRET_NAME', 'test/credentials')
    monkeypatch.setenv('TOKEN_EXPIRY_HOURS', '24')
    monkeypatch.setenv('SNS_TOPIC_ARN', 'arn:aws:sns:us-east-1:123456789:test-topic')


@pytest.fixture
def mock_secrets_client():
    """Mock Secrets Manager client"""
    with patch('lambda_functions.media_authenticator.handler.secrets_client') as mock:
        yield mock


@pytest.fixture
def mock_provider():
    """Mock provider"""
    with patch('lambda_functions.media_authenticator.handler.ProviderFactory') as mock:
        yield mock


@pytest.fixture
def valid_credentials():
    """Valid credentials fixture"""
    return {
        'provider': 'gopro',
        'username': 'test@example.com',
        'access_token': 'valid_access_token',
        'refresh_token': 'valid_refresh_token',
        'user_id': '12345678',
        'token_timestamp': datetime.utcnow().isoformat() + 'Z',
        'last_updated': datetime.utcnow().isoformat() + 'Z'
    }


@pytest.fixture
def expired_credentials():
    """Expired credentials fixture"""
    expired_time = datetime.utcnow() - timedelta(hours=25)
    return {
        'provider': 'gopro',
        'username': 'test@example.com',
        'access_token': 'expired_access_token',
        'refresh_token': 'valid_refresh_token',
        'user_id': '12345678',
        'token_timestamp': expired_time.isoformat() + 'Z',
        'last_updated': expired_time.isoformat() + 'Z'
    }


class TestMediaAuthenticator:
    """Test Media Authenticator Lambda function"""
    
    def test_handler_with_valid_token(self, mock_env, mock_secrets_client, valid_credentials):
        """Test handler with valid token that doesn't need refresh"""
        # Import after mocking
        from lambda_functions.media_authenticator.handler import handler
        
        # Setup mock
        mock_secrets_client.get_secret_value.return_value = {
            'SecretString': json.dumps(valid_credentials)
        }
        
        # Create event
        event = {
            'provider': 'gopro',
            'action': 'authenticate'
        }
        
        # Execute
        result = handler(event, Mock())
        
        # Assertions
        assert result['statusCode'] == 200
        assert result['provider'] == 'gopro'
        assert result['auth_token'] == 'valid_access_token'
        assert result['user_id'] == '12345678'
        
        # Verify Secrets Manager was called
        mock_secrets_client.get_secret_value.assert_called_once_with(
            SecretId='test/credentials'
        )
        
        # Verify token was not updated (still valid)
        mock_secrets_client.update_secret.assert_not_called()
    
    def test_handler_with_expired_token(
        self, mock_env, mock_secrets_client, mock_provider, expired_credentials
    ):
        """Test handler with expired token that needs refresh"""
        # Import after mocking
        from lambda_functions.media_authenticator.handler import handler
        
        # Setup mocks
        mock_secrets_client.get_secret_value.return_value = {
            'SecretString': json.dumps(expired_credentials)
        }
        
        # Mock provider authenticate
        mock_provider_instance = Mock()
        mock_provider_instance.authenticate.return_value = {
            'access_token': 'new_access_token',
            'refresh_token': 'new_refresh_token'
        }
        mock_provider.get_provider.return_value = mock_provider_instance
        
        # Create event
        event = {
            'provider': 'gopro',
            'action': 'authenticate'
        }
        
        # Execute
        result = handler(event, Mock())
        
        # Assertions
        assert result['statusCode'] == 200
        assert result['provider'] == 'gopro'
        assert result['auth_token'] == 'new_access_token'
        
        # Verify provider was called
        mock_provider.get_provider.assert_called_once_with('gopro')
        mock_provider_instance.authenticate.assert_called_once()
        
        # Verify token was updated
        mock_secrets_client.update_secret.assert_called_once()
        update_call = mock_secrets_client.update_secret.call_args
        updated_creds = json.loads(update_call[1]['SecretString'])
        assert updated_creds['access_token'] == 'new_access_token'
    
    def test_handler_with_missing_token(
        self, mock_env, mock_secrets_client, mock_provider
    ):
        """Test handler with missing access token"""
        # Import after mocking
        from lambda_functions.media_authenticator.handler import handler
        
        # Setup mocks - credentials without access_token
        credentials = {
            'provider': 'gopro',
            'refresh_token': 'valid_refresh_token',
            'user_id': '12345678'
        }
        mock_secrets_client.get_secret_value.return_value = {
            'SecretString': json.dumps(credentials)
        }
        
        # Mock provider authenticate
        mock_provider_instance = Mock()
        mock_provider_instance.authenticate.return_value = {
            'access_token': 'new_access_token',
            'refresh_token': 'new_refresh_token'
        }
        mock_provider.get_provider.return_value = mock_provider_instance
        
        # Create event
        event = {
            'provider': 'gopro',
            'action': 'authenticate'
        }
        
        # Execute
        result = handler(event, Mock())
        
        # Assertions
        assert result['statusCode'] == 200
        assert result['auth_token'] == 'new_access_token'
        
        # Verify token was refreshed
        mock_provider_instance.authenticate.assert_called_once()
    
    def test_handler_secrets_manager_not_found(self, mock_env, mock_secrets_client):
        """Test handler when secret is not found"""
        # Import after mocking
        from lambda_functions.media_authenticator.handler import handler
        
        # Setup mock to raise ResourceNotFoundException
        mock_secrets_client.get_secret_value.side_effect = \
            mock_secrets_client.exceptions.ResourceNotFoundException(
                {'Error': {'Code': 'ResourceNotFoundException'}},
                'GetSecretValue'
            )
        
        # Create event
        event = {
            'provider': 'gopro',
            'action': 'authenticate'
        }
        
        # Execute
        result = handler(event, Mock())
        
        # Assertions
        assert result['statusCode'] == 401
        assert 'error' in result
        assert result['error'] == 'AuthenticationError'
    
    def test_handler_authentication_failure(
        self, mock_env, mock_secrets_client, mock_provider, expired_credentials
    ):
        """Test handler when authentication fails"""
        # Import after mocking
        from lambda_functions.media_authenticator.handler import handler
        from cloud_sync_common.exceptions import AuthenticationError
        
        # Setup mocks
        mock_secrets_client.get_secret_value.return_value = {
            'SecretString': json.dumps(expired_credentials)
        }
        
        # Mock provider to raise authentication error
        mock_provider_instance = Mock()
        mock_provider_instance.authenticate.side_effect = \
            AuthenticationError('Invalid credentials')
        mock_provider.get_provider.return_value = mock_provider_instance
        
        # Create event
        event = {
            'provider': 'gopro',
            'action': 'authenticate'
        }
        
        # Execute
        result = handler(event, Mock())
        
        # Assertions
        assert result['statusCode'] == 401
        assert result['error'] == 'AuthenticationError'
        assert 'Invalid credentials' in result['message']
    
    def test_needs_token_refresh_with_old_token(self):
        """Test token refresh logic with old token"""
        from lambda_functions.media_authenticator.handler import needs_token_refresh
        
        # Create credentials with old token (25 hours ago)
        old_time = datetime.utcnow() - timedelta(hours=25)
        credentials = {
            'access_token': 'old_token',
            'token_timestamp': old_time.isoformat() + 'Z'
        }
        
        # Test
        result = needs_token_refresh(credentials)
        
        # Should need refresh
        assert result is True
    
    def test_needs_token_refresh_with_recent_token(self):
        """Test token refresh logic with recent token"""
        from lambda_functions.media_authenticator.handler import needs_token_refresh
        
        # Create credentials with recent token (1 hour ago)
        recent_time = datetime.utcnow() - timedelta(hours=1)
        credentials = {
            'access_token': 'recent_token',
            'token_timestamp': recent_time.isoformat() + 'Z'
        }
        
        # Test
        result = needs_token_refresh(credentials)
        
        # Should not need refresh
        assert result is False
    
    def test_needs_token_refresh_with_missing_token(self):
        """Test token refresh logic with missing token"""
        from lambda_functions.media_authenticator.handler import needs_token_refresh
        
        # Create credentials without access_token
        credentials = {
            'refresh_token': 'refresh_token'
        }
        
        # Test
        result = needs_token_refresh(credentials)
        
        # Should need refresh
        assert result is True
    
    def test_needs_token_refresh_with_missing_timestamp(self):
        """Test token refresh logic with missing timestamp"""
        from lambda_functions.media_authenticator.handler import needs_token_refresh
        
        # Create credentials without timestamp
        credentials = {
            'access_token': 'token'
        }
        
        # Test
        result = needs_token_refresh(credentials)
        
        # Should need refresh
        assert result is True
