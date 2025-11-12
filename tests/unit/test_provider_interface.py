"""Unit tests for provider interface and factory."""

import pytest
from lambda_layer.python.cloud_sync_common.provider_interface import (
    CloudProviderInterface,
    ProviderFactory,
    VideoMetadata,
    AuthenticationResult
)
from lambda_layer.python.cloud_sync_common.exceptions import AuthenticationError


class MockProvider(CloudProviderInterface):
    """Mock provider for testing."""

    def authenticate(self, credentials):
        """Mock authenticate method."""
        if credentials.get('invalid'):
            raise AuthenticationError("Invalid credentials")
        return AuthenticationResult(
            auth_token="mock_token",
            user_id="mock_user",
            expires_at="2025-12-31T00:00:00Z",
            provider="mock"
        )

    def list_media(self, auth_token, user_id, page_size=100, max_videos=1000):
        """Mock list_media method."""
        return [
            VideoMetadata(
                media_id="mock_123",
                filename="test.mp4",
                download_url="https://example.com/test.mp4",
                file_size=1000000,
                upload_date="2025-11-01T00:00:00Z",
                duration=60,
                provider="mock"
            )
        ]

    def get_download_url(self, media_id, auth_token):
        """Mock get_download_url method."""
        return f"https://example.com/{media_id}"

    def refresh_token(self, refresh_token):
        """Mock refresh_token method."""
        return AuthenticationResult(
            auth_token="new_mock_token",
            user_id="mock_user",
            expires_at="2025-12-31T00:00:00Z",
            provider="mock"
        )


class TestProviderFactory:
    """Test cases for ProviderFactory."""

    def test_provider_registration(self):
        """Test provider registration."""
        ProviderFactory.register_provider('mock', MockProvider)
        assert 'mock' in ProviderFactory.list_providers()

    def test_provider_creation(self):
        """Test provider creation."""
        ProviderFactory.register_provider('mock', MockProvider)
        provider = ProviderFactory.create_provider('mock')
        assert isinstance(provider, MockProvider)

    def test_unknown_provider_raises_error(self):
        """Test that unknown provider raises ValueError."""
        with pytest.raises(ValueError, match="Provider 'unknown' not registered"):
            ProviderFactory.create_provider('unknown')

    def test_list_providers(self):
        """Test listing registered providers."""
        ProviderFactory.register_provider('mock', MockProvider)
        providers = ProviderFactory.list_providers()
        assert isinstance(providers, list)
        assert 'mock' in providers


class TestMockProvider:
    """Test cases for MockProvider implementation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.provider = MockProvider()

    def test_authenticate_success(self):
        """Test successful authentication."""
        result = self.provider.authenticate({'username': 'test'})
        assert isinstance(result, AuthenticationResult)
        assert result.auth_token == "mock_token"
        assert result.user_id == "mock_user"
        assert result.provider == "mock"

    def test_authenticate_failure(self):
        """Test authentication failure."""
        with pytest.raises(AuthenticationError):
            self.provider.authenticate({'invalid': True})

    def test_list_media(self):
        """Test media listing."""
        media = self.provider.list_media("token", "user")
        assert len(media) == 1
        assert isinstance(media[0], VideoMetadata)
        assert media[0].media_id == "mock_123"
        assert media[0].filename == "test.mp4"

    def test_get_download_url(self):
        """Test download URL retrieval."""
        url = self.provider.get_download_url("test_id", "token")
        assert url == "https://example.com/test_id"

    def test_refresh_token(self):
        """Test token refresh."""
        result = self.provider.refresh_token("old_token")
        assert isinstance(result, AuthenticationResult)
        assert result.auth_token == "new_mock_token"


class TestVideoMetadata:
    """Test cases for VideoMetadata dataclass."""

    def test_video_metadata_creation(self):
        """Test VideoMetadata creation."""
        metadata = VideoMetadata(
            media_id="test_123",
            filename="video.mp4",
            download_url="https://example.com/video.mp4",
            file_size=5000000,
            upload_date="2025-11-01T00:00:00Z",
            duration=120,
            provider="test"
        )
        assert metadata.media_id == "test_123"
        assert metadata.filename == "video.mp4"
        assert metadata.file_size == 5000000
        assert metadata.duration == 120

    def test_video_metadata_optional_fields(self):
        """Test VideoMetadata with optional fields."""
        metadata = VideoMetadata(
            media_id="test_123",
            filename="video.mp4",
            download_url="https://example.com/video.mp4",
            file_size=5000000,
            upload_date="2025-11-01T00:00:00Z"
        )
        assert metadata.duration is None
        assert metadata.provider == "unknown"


class TestAuthenticationResult:
    """Test cases for AuthenticationResult dataclass."""

    def test_authentication_result_creation(self):
        """Test AuthenticationResult creation."""
        result = AuthenticationResult(
            auth_token="token_123",
            user_id="user_456",
            expires_at="2025-12-31T00:00:00Z",
            provider="test"
        )
        assert result.auth_token == "token_123"
        assert result.user_id == "user_456"
        assert result.expires_at == "2025-12-31T00:00:00Z"
        assert result.provider == "test"
