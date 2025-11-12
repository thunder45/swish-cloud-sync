"""Cloud provider abstraction interface."""

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional
from dataclasses import dataclass


@dataclass
class VideoMetadata:
    """Video metadata from cloud provider."""
    
    media_id: str
    filename: str
    download_url: str
    file_size: int
    upload_date: str
    duration: Optional[int] = None
    provider: str = "unknown"


@dataclass
class AuthenticationResult:
    """Authentication result from cloud provider."""
    
    auth_token: str
    user_id: str
    expires_at: str
    provider: str


class CloudProviderInterface(ABC):
    """Abstract interface for cloud storage providers."""

    @abstractmethod
    def authenticate(self, credentials: Dict[str, Any]) -> AuthenticationResult:
        """Authenticate with the cloud provider.
        
        Args:
            credentials: Provider-specific credentials
            
        Returns:
            AuthenticationResult with token and user info
            
        Raises:
            AuthenticationError: If authentication fails
        """
        pass

    @abstractmethod
    def list_media(
        self,
        auth_token: str,
        user_id: str,
        page_size: int = 100,
        max_videos: int = 1000
    ) -> List[VideoMetadata]:
        """List all videos from the cloud provider.
        
        Args:
            auth_token: Authentication token
            user_id: User identifier
            page_size: Number of items per page
            max_videos: Maximum number of videos to retrieve
            
        Returns:
            List of VideoMetadata objects
            
        Raises:
            APIError: If API call fails
        """
        pass

    @abstractmethod
    def get_download_url(
        self,
        media_id: str,
        auth_token: str
    ) -> str:
        """Get download URL for a specific video.
        
        Args:
            media_id: Video identifier
            auth_token: Authentication token
            
        Returns:
            Download URL string
            
        Raises:
            APIError: If API call fails
        """
        pass

    @abstractmethod
    def refresh_token(self, refresh_token: str) -> AuthenticationResult:
        """Refresh authentication token.
        
        Args:
            refresh_token: Refresh token from previous authentication
            
        Returns:
            AuthenticationResult with new tokens
            
        Raises:
            AuthenticationError: If refresh fails
        """
        pass


class ProviderFactory:
    """Factory for creating cloud provider instances."""

    _providers: Dict[str, type] = {}

    @classmethod
    def register_provider(cls, name: str, provider_class: type) -> None:
        """Register a provider implementation.
        
        Args:
            name: Provider name (e.g., 'gopro', 'google_drive')
            provider_class: Provider class implementing CloudProviderInterface
        """
        cls._providers[name] = provider_class

    @classmethod
    def create_provider(cls, name: str) -> CloudProviderInterface:
        """Create a provider instance.
        
        Args:
            name: Provider name
            
        Returns:
            Provider instance
            
        Raises:
            ValueError: If provider not registered
        """
        if name not in cls._providers:
            raise ValueError(f"Provider '{name}' not registered")
        return cls._providers[name]()

    @classmethod
    def list_providers(cls) -> List[str]:
        """List all registered providers.
        
        Returns:
            List of provider names
        """
        return list(cls._providers.keys())
