"""Unit tests for GoPro provider implementation."""

import pytest
import os
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
import requests

from lambda_layer.python.cloud_sync_common.gopro_provider import GoProProvider
from lambda_layer.python.cloud_sync_common.provider_interface import (
    AuthenticationResult,
    VideoMetadata,
    ProviderFactory
)
from lambda_layer.python.cloud_sync_common.exceptions import (
    AuthenticationError,
    APIError,
    NetworkError
)


class TestGoProProviderRegistration:
    """Test GoPro provider registration with factory."""

    def test_gopro_provider_registered(self):
        """Test that GoPro provider is registered with factory."""
        providers = ProviderFactory.list_providers()
        assert 'gopro' in providers

    def test_gopro_provider_creation(self):
        """Test creating GoPro provider instance from factory."""
        provider = ProviderFactory.create_provider('gopro')
        assert isinstance(provider, GoProProvider)


class TestGoProProviderInitialization:
    """Test GoPro provider initialization."""

    def test_initialization_with_env_vars(self):
        """Test provider initialization with environment variables."""
        with patch.dict(os.environ, {
            'GOPRO_CLIENT_ID': 'test_client_id',
            'GOPRO_CLIENT_SECRET': 'test_client_secret'
        }):
            provider = GoProProvider()
            assert provider.client_id == 'test_client_id'
            assert provider.client_secret == 'test_client_secret'

    def test_initialization_without_env_vars(self):
        """Test provider initialization without environment variables."""
        with patch.dict(os.environ, {}, clear=True):
            provider = GoProProvider()
            assert provider.client_id == ''
            assert provider.client_secret == ''


class TestGoProAuthentication:
    """Test GoPro authentication methods."""

    def setup_method(self):
        """Set up test fixtures."""
        with patch.dict(os.environ, {
            'GOPRO_CLIENT_ID': 'test_client_id',
            'GOPRO_CLIENT_SECRET': 'test_client_secret'
        }):
            self.provider = GoProProvider()

    def test_authenticate_with_valid_token(self):
        """Test authentication with valid non-expired token."""
        # Token created 1 hour ago
        token_time = (datetime.utcnow() - timedelta(hours=1)).isoformat() + 'Z'
        
        credentials = {
            'access_token': 'valid_token',
            'token_timestamp': token_time,
            'user_id': 'test_user_123',
            'refresh_token': 'refresh_token_123'
        }
        
        result = self.provider.authenticate(credentials)
        
        assert isinstance(result, AuthenticationResult)
        assert result.auth_token == 'valid_token'
        assert result.user_id == 'test_user_123'
        assert result.provider == 'gopro'

    def test_authenticate_with_expired_token(self):
        """Test authentication with expired token triggers refresh."""
        # Token created 25 hours ago (expired)
        token_time = (datetime.utcnow() - timedelta(hours=25)).isoformat() + 'Z'
        
        credentials = {
            'access_token': 'expired_token',
            'token_timestamp': token_time,
            'user_id': 'test_user_123',
            'refresh_token': 'refresh_token_123'
        }
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'access_token': 'new_token',
            'refresh_token': 'new_refresh_token',
            'expires_in': 86400,
            'user_id': 'test_user_123'
        }
        
        with patch('requests.post', return_value=mock_response):
            result = self.provider.authenticate(credentials)
            
            assert result.auth_token == 'new_token'
            assert result.user_id == 'test_user_123'

    def test_authenticate_without_refresh_token(self):
        """Test authentication without refresh token raises error."""
        credentials = {
            'access_token': '',
            'token_timestamp': ''
        }
        
        with pytest.raises(AuthenticationError, match="No refresh token provided"):
            self.provider.authenticate(credentials)

    def test_authenticate_with_invalid_timestamp(self):
        """Test authentication with invalid timestamp triggers refresh."""
        credentials = {
            'access_token': 'token',
            'token_timestamp': 'invalid_timestamp',
            'refresh_token': 'refresh_token_123'
        }
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'access_token': 'new_token',
            'expires_in': 86400,
            'user_id': 'test_user'
        }
        
        with patch('requests.post', return_value=mock_response):
            result = self.provider.authenticate(credentials)
            assert result.auth_token == 'new_token'


class TestGoProTokenRefresh:
    """Test GoPro token refresh functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        with patch.dict(os.environ, {
            'GOPRO_CLIENT_ID': 'test_client_id',
            'GOPRO_CLIENT_SECRET': 'test_client_secret'
        }):
            self.provider = GoProProvider()

    def test_refresh_token_success(self):
        """Test successful token refresh."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'access_token': 'new_access_token',
            'refresh_token': 'new_refresh_token',
            'expires_in': 86400,
            'user_id': 'test_user_123'
        }
        
        with patch('requests.post', return_value=mock_response) as mock_post:
            result = self.provider.refresh_token('old_refresh_token')
            
            # Verify API call
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[0][0] == self.provider.TOKEN_URL
            assert call_args[1]['json']['grant_type'] == 'refresh_token'
            assert call_args[1]['json']['refresh_token'] == 'old_refresh_token'
            
            # Verify result
            assert isinstance(result, AuthenticationResult)
            assert result.auth_token == 'new_access_token'
            assert result.user_id == 'test_user_123'
            assert result.provider == 'gopro'

    def test_refresh_token_without_credentials(self):
        """Test token refresh without client credentials raises error."""
        provider = GoProProvider()  # No env vars set
        
        with pytest.raises(AuthenticationError, match="environment variables must be set"):
            provider.refresh_token('refresh_token')

    def test_refresh_token_unauthorized(self):
        """Test token refresh with invalid credentials."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = 'Unauthorized'
        
        with patch('requests.post', return_value=mock_response):
            with pytest.raises(AuthenticationError, match="Invalid credentials"):
                self.provider.refresh_token('invalid_refresh_token')

    def test_refresh_token_api_error(self):
        """Test token refresh with API error."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = 'Internal Server Error'
        
        with patch('requests.post', return_value=mock_response):
            with pytest.raises(AuthenticationError, match="Token refresh failed"):
                self.provider.refresh_token('refresh_token')

    def test_refresh_token_network_timeout(self):
        """Test token refresh with network timeout."""
        with patch('requests.post', side_effect=requests.exceptions.Timeout):
            with pytest.raises(NetworkError, match="Token refresh timeout"):
                self.provider.refresh_token('refresh_token')

    def test_refresh_token_network_error(self):
        """Test token refresh with network error."""
        with patch('requests.post', side_effect=requests.exceptions.ConnectionError):
            with pytest.raises(NetworkError, match="Token refresh failed"):
                self.provider.refresh_token('refresh_token')

    def test_refresh_token_invalid_response(self):
        """Test token refresh with invalid response format."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}  # Missing access_token
        
        with patch('requests.post', return_value=mock_response):
            with pytest.raises(AuthenticationError, match="Invalid token response"):
                self.provider.refresh_token('refresh_token')


class TestGoProMediaListing:
    """Test GoPro media listing functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.provider = GoProProvider()

    def test_list_media_single_page(self):
        """Test listing media with single page of results."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'media': [
                {
                    'id': 'video_123',
                    'filename': 'GH010456.MP4',
                    'file_size': 524288000,
                    'created_at': '2025-11-10T14:23:45Z',
                    'duration': 180
                },
                {
                    'id': 'video_456',
                    'filename': 'GH010457.MP4',
                    'file_size': 1048576000,
                    'created_at': '2025-11-11T10:15:30Z',
                    'duration': 240
                }
            ],
            'total_pages': 1
        }
        
        with patch('requests.get', return_value=mock_response):
            videos = self.provider.list_media('auth_token', 'user_123')
            
            assert len(videos) == 2
            assert all(isinstance(v, VideoMetadata) for v in videos)
            assert videos[0].media_id == 'video_123'
            assert videos[0].filename == 'GH010456.MP4'
            assert videos[0].file_size == 524288000
            assert videos[0].provider == 'gopro'

    def test_list_media_multiple_pages(self):
        """Test listing media with pagination."""
        # First page
        mock_response_1 = Mock()
        mock_response_1.status_code = 200
        mock_response_1.json.return_value = {
            'media': [{'id': f'video_{i}', 'filename': f'video_{i}.mp4', 
                      'file_size': 1000000, 'created_at': '2025-11-10T00:00:00Z'}
                     for i in range(100)],
            'total_pages': 2
        }
        
        # Second page
        mock_response_2 = Mock()
        mock_response_2.status_code = 200
        mock_response_2.json.return_value = {
            'media': [{'id': f'video_{i}', 'filename': f'video_{i}.mp4',
                      'file_size': 1000000, 'created_at': '2025-11-10T00:00:00Z'}
                     for i in range(100, 150)],
            'total_pages': 2
        }
        
        with patch('requests.get', side_effect=[mock_response_1, mock_response_2]):
            videos = self.provider.list_media('auth_token', 'user_123')
            
            assert len(videos) == 150

    def test_list_media_with_max_videos_limit(self):
        """Test listing media respects max_videos limit."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'media': [{'id': f'video_{i}', 'filename': f'video_{i}.mp4',
                      'file_size': 1000000, 'created_at': '2025-11-10T00:00:00Z'}
                     for i in range(100)],
            'total_pages': 10
        }
        
        with patch('requests.get', return_value=mock_response):
            videos = self.provider.list_media('auth_token', 'user_123', max_videos=50)
            
            assert len(videos) == 50

    def test_list_media_rate_limit(self):
        """Test media listing handles rate limiting."""
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.headers = {'Retry-After': '60'}
        mock_response.text = 'Rate limited'
        
        with patch('requests.get', return_value=mock_response):
            with pytest.raises(APIError, match="Rate limited"):
                self.provider.list_media('auth_token', 'user_123')

    def test_list_media_api_error(self):
        """Test media listing with API error."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = 'Internal Server Error'
        
        with patch('requests.get', return_value=mock_response):
            with pytest.raises(APIError, match="Media listing failed"):
                self.provider.list_media('auth_token', 'user_123')

    def test_list_media_timeout(self):
        """Test media listing with timeout."""
        with patch('requests.get', side_effect=requests.exceptions.Timeout):
            with pytest.raises(APIError, match="Media listing timeout"):
                self.provider.list_media('auth_token', 'user_123')

    def test_list_media_network_error(self):
        """Test media listing with network error."""
        with patch('requests.get', side_effect=requests.exceptions.ConnectionError):
            with pytest.raises(APIError, match="Media listing failed"):
                self.provider.list_media('auth_token', 'user_123')

    def test_list_media_empty_results(self):
        """Test media listing with no results."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'media': [],
            'total_pages': 0
        }
        
        with patch('requests.get', return_value=mock_response):
            videos = self.provider.list_media('auth_token', 'user_123')
            
            assert len(videos) == 0

    def test_list_media_invalid_item_skipped(self):
        """Test that invalid media items are skipped."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'media': [
                {
                    'id': 'video_123',
                    'filename': 'valid.mp4',
                    'file_size': 1000000,
                    'created_at': '2025-11-10T00:00:00Z'
                },
                {
                    # Missing 'id' field - should be skipped
                    'filename': 'invalid.mp4'
                },
                {
                    'id': 'video_456',
                    'filename': 'valid2.mp4',
                    'file_size': 2000000,
                    'created_at': '2025-11-11T00:00:00Z'
                }
            ],
            'total_pages': 1
        }
        
        with patch('requests.get', return_value=mock_response):
            videos = self.provider.list_media('auth_token', 'user_123')
            
            # Should have 2 valid videos, 1 skipped
            assert len(videos) == 2
            assert videos[0].media_id == 'video_123'
            assert videos[1].media_id == 'video_456'


class TestGoProMediaParsing:
    """Test GoPro media item parsing."""

    def setup_method(self):
        """Set up test fixtures."""
        self.provider = GoProProvider()

    def test_parse_media_item_complete(self):
        """Test parsing complete media item."""
        item = {
            'id': 'video_123',
            'filename': 'GH010456.MP4',
            'file_size': 524288000,
            'created_at': '2025-11-10T14:23:45Z',
            'duration': 180
        }
        
        video = self.provider._parse_media_item(item)
        
        assert video.media_id == 'video_123'
        assert video.filename == 'GH010456.MP4'
        assert video.file_size == 524288000
        assert video.upload_date == '2025-11-10T14:23:45Z'
        assert video.duration == 180
        assert video.provider == 'gopro'

    def test_parse_media_item_minimal(self):
        """Test parsing minimal media item."""
        item = {
            'id': 'video_123'
        }
        
        video = self.provider._parse_media_item(item)
        
        assert video.media_id == 'video_123'
        assert video.filename == 'video_123.MP4'
        assert video.file_size == 0
        assert video.duration is None

    def test_parse_media_item_string_file_size(self):
        """Test parsing media item with string file size."""
        item = {
            'id': 'video_123',
            'file_size': '524288000',
            'created_at': '2025-11-10T00:00:00Z'
        }
        
        video = self.provider._parse_media_item(item)
        
        assert video.file_size == 524288000

    def test_parse_media_item_string_duration(self):
        """Test parsing media item with string duration."""
        item = {
            'id': 'video_123',
            'duration': '180.5',
            'created_at': '2025-11-10T00:00:00Z'
        }
        
        video = self.provider._parse_media_item(item)
        
        assert video.duration == 180

    def test_parse_media_item_missing_id(self):
        """Test parsing media item without ID raises error."""
        item = {
            'filename': 'video.mp4'
        }
        
        with pytest.raises(KeyError):
            self.provider._parse_media_item(item)


class TestGoProDownloadURL:
    """Test GoPro download URL generation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.provider = GoProProvider()

    def test_get_download_url(self):
        """Test download URL generation."""
        url = self.provider.get_download_url('video_123', 'auth_token')
        
        assert url == 'https://api.gopro.com/v1/media/video_123/download'

    def test_get_download_url_format(self):
        """Test download URL has correct format."""
        media_id = 'test_media_456'
        url = self.provider.get_download_url(media_id, 'token')
        
        assert url.startswith('https://api.gopro.com/v1/media/')
        assert url.endswith('/download')
        assert media_id in url
