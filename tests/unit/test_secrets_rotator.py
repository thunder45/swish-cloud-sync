"""Unit tests for secrets rotator Lambda function."""

import sys
import os
from pathlib import Path

# Add lambda_layer/python to path for imports
lambda_layer_path = Path(__file__).parent.parent.parent / 'lambda_layer' / 'python'
sys.path.insert(0, str(lambda_layer_path))

import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime


@pytest.fixture
def mock_env(monkeypatch):
    """Set up environment variables."""
    monkeypatch.setenv('SECRET_NAME', 'test/credentials')
    monkeypatch.setenv('SNS_TOPIC_ARN', 'arn:aws:sns:us-east-1:123456789:test-topic')
    monkeypatch.setenv('PROVIDER_NAME', 'gopro')
    monkeypatch.setenv('GOPRO_CLIENT_ID', 'test_client_id')
    monkeypatch.setenv('GOPRO_CLIENT_SECRET', 'test_client_secret')


@pytest.fixture
def mock_secrets_client():
    """Mock Secrets Manager client."""
    with patch('lambda_functions.secrets_rotator.handler.secrets_client') as mock:
        yield mock


@pytest.fixture
def mock_sns_client():
    """Mock SNS client."""
    with patch('lambda_functions.secrets_rotator.handler.sns_client') as mock:
        yield mock


@pytest.fixture
def mock_provider_factory():
    """Mock ProviderFactory."""
    with patch('lambda_functions.secrets_rotator.handler.ProviderFactory') as mock:
        yield mock


@pytest.fixture
def sample_credentials():
    """Sample credentials for testing."""
    return {
        'refresh_token': 'test_refresh_token',
        'access_token': 'old_access_token',
        'user_id': 'test_user_123',
        'token_timestamp': '2025-10-01T00:00:00Z',
        'rotation_count': 5
    }


@pytest.fixture
def sample_event():
    """Sample Lambda event."""
    return {
        'source': 'aws.events',
        'detail-type': 'Scheduled Event'
    }


class TestRefreshCredentials:
    """Tests for refresh_credentials function."""
    
    def test_refresh_token_success(self, mock_env, mock_provider_factory, sample_credentials):
        """Test successful token refresh."""
        from lambda_functions.secrets_rotator.handler import refresh_credentials
        
        # Mock provider
        mock_provider = Mock()
        mock_provider.authenticate.return_value = {
            'access_token': 'new_access_token',
            'refresh_token': 'new_refresh_token',
            'user_id': 'test_user_123'
        }
        mock_provider_factory.get_provider.return_value = mock_provider
        
        # Execute
        result = refresh_credentials(sample_credentials)
        
        # Verify
        assert result['access_token'] == 'new_access_token'
        assert result['refresh_token'] == 'new_refresh_token'
        assert result['rotation_count'] == 6
        assert 'last_rotated' in result
        mock_provider.authenticate.assert_called_once()
    
    def test_refresh_token_failure(self, mock_env, mock_provider_factory, sample_credentials):
        """Test token refresh failure."""
        from lambda_functions.secrets_rotator.handler import refresh_credentials
        from cloud_sync_common.exceptions import AuthenticationError
        
        # Mock provider to raise error
        mock_provider = Mock()
        mock_provider.authenticate.side_effect = Exception('API error')
        mock_provider_factory.get_provider.return_value = mock_provider
        
        # Execute and verify exception
        with pytest.raises(AuthenticationError):
            refresh_credentials(sample_credentials)


class TestTestCredentials:
    """Tests for test_credentials function."""
    
    def test_credentials_valid(self, mock_env, mock_provider_factory):
        """Test credential validation with valid credentials."""
        from lambda_functions.secrets_rotator.handler import test_credentials
        
        # Mock provider
        mock_provider = Mock()
        mock_provider.list_media.return_value = [{'media_id': 'test123'}]
        mock_provider_factory.get_provider.return_value = mock_provider
        
        credentials = {
            'access_token': 'valid_token',
            'user_id': 'test_user'
        }
        
        # Execute - should not raise exception
        test_credentials(credentials)
        
        # Verify API call was made
        mock_provider.list_media.assert_called_once_with(
            auth_token='valid_token',
            user_id='test_user',
            page_size=1,
            max_videos=1
        )
    
    def test_credentials_invalid(self, mock_env, mock_provider_factory):
        """Test credential validation with invalid credentials."""
        from lambda_functions.secrets_rotator.handler import test_credentials
        from cloud_sync_common.exceptions import AuthenticationError
        
        # Mock provider to raise error
        mock_provider = Mock()
        mock_provider.list_media.side_effect = Exception('Invalid token')
        mock_provider_factory.get_provider.return_value = mock_provider
        
        credentials = {
            'access_token': 'invalid_token',
            'user_id': 'test_user'
        }
        
        # Execute and verify exception
        with pytest.raises(AuthenticationError):
            test_credentials(credentials)
    
    def test_credentials_missing_fields(self, mock_env):
        """Test credential validation with missing required fields."""
        from lambda_functions.secrets_rotator.handler import test_credentials
        from cloud_sync_common.exceptions import AuthenticationError
        
        credentials = {'access_token': 'token'}  # Missing user_id
        
        # Execute and verify exception
        with pytest.raises(AuthenticationError):
            test_credentials(credentials)


class TestRotationHandler:
    """Tests for main Lambda handler."""
    
    @patch('lambda_functions.secrets_rotator.handler.retrieve_credentials')
    @patch('lambda_functions.secrets_rotator.handler.refresh_credentials')
    @patch('lambda_functions.secrets_rotator.handler.test_credentials')
    @patch('lambda_functions.secrets_rotator.handler.store_credentials')
    @patch('lambda_functions.secrets_rotator.handler.publish_rotation_metrics')
    @patch('lambda_functions.secrets_rotator.handler.send_notification')
    def test_handler_success(
        self,
        mock_send_notification,
        mock_publish_metrics,
        mock_store,
        mock_test,
        mock_refresh,
        mock_retrieve,
        mock_env,
        sample_event,
        sample_credentials
    ):
        """Test successful rotation handler execution."""
        from lambda_functions.secrets_rotator.handler import handler
        
        # Setup mocks
        mock_retrieve.return_value = sample_credentials
        new_credentials = {**sample_credentials, 'access_token': 'new_token'}
        mock_refresh.return_value = new_credentials
        
        # Execute
        result = handler(sample_event, None)
        
        # Verify
        assert result['statusCode'] == 200
        assert 'duration_seconds' in result
        mock_retrieve.assert_called_once()
        mock_refresh.assert_called_once()
        mock_test.assert_called_once()
        mock_store.assert_called_once()
        mock_publish_metrics.assert_called_once_with(success=True, duration=pytest.approx(0, abs=5))
        mock_send_notification.assert_called_once()
    
    @patch('lambda_functions.secrets_rotator.handler.retrieve_credentials')
    @patch('lambda_functions.secrets_rotator.handler.publish_rotation_metrics')
    @patch('lambda_functions.secrets_rotator.handler.send_notification')
    def test_handler_failure(
        self,
        mock_send_notification,
        mock_publish_metrics,
        mock_retrieve,
        mock_env,
        sample_event
    ):
        """Test rotation handler with failure."""
        from lambda_functions.secrets_rotator.handler import handler
        
        # Setup mock to raise error
        mock_retrieve.side_effect = Exception('Secrets Manager error')
        
        # Execute
        result = handler(sample_event, None)
        
        # Verify
        assert result['statusCode'] == 500
        assert 'error' in result
        mock_publish_metrics.assert_called_once_with(success=False, duration=pytest.approx(0, abs=5))
        mock_send_notification.assert_called_once()
