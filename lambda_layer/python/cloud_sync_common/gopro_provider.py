"""GoPro Cloud provider implementation."""

import os
import logging
import requests
from typing import Dict, List, Any
from datetime import datetime, timedelta
from .provider_interface import (
    CloudProviderInterface,
    VideoMetadata,
    AuthenticationResult,
    ProviderFactory
)
from .exceptions import AuthenticationError, APIError, NetworkError
from .retry_utils import exponential_backoff_retry, retry_on_api_error

logger = logging.getLogger(__name__)


class GoProProvider(CloudProviderInterface):
    """GoPro Cloud provider implementation."""

    # GoPro API endpoints
    BASE_URL = "https://api.gopro.com/v1"
    TOKEN_URL = f"{BASE_URL}/oauth2/token"
    MEDIA_SEARCH_URL = f"{BASE_URL}/media/search"
    
    def __init__(self):
        """Initialize GoPro provider."""
        self.client_id = os.environ.get('GOPRO_CLIENT_ID', '')
        self.client_secret = os.environ.get('GOPRO_CLIENT_SECRET', '')
        
    def authenticate(self, credentials: Dict[str, Any]) -> AuthenticationResult:
        """Authenticate with GoPro Cloud.
        
        Args:
            credentials: Dictionary containing:
                - refresh_token: OAuth refresh token
                - client_id: OAuth client ID (optional, uses env var if not provided)
                - client_secret: OAuth client secret (optional, uses env var if not provided)
                - access_token: Current access token (optional)
                - token_timestamp: Timestamp of last token refresh (optional)
                
        Returns:
            AuthenticationResult with token and user info
            
        Raises:
            AuthenticationError: If authentication fails
        """
        # Check if we need to refresh the token
        access_token = credentials.get('access_token', '')
        token_timestamp = credentials.get('token_timestamp', '')
        
        # If we have a valid token (less than 24 hours old), return it
        if access_token and token_timestamp:
            try:
                token_time = datetime.fromisoformat(token_timestamp.replace('Z', '+00:00'))
                current_time = datetime.now(token_time.tzinfo) if token_time.tzinfo else datetime.utcnow()
                if current_time - token_time < timedelta(hours=24):
                    logger.info("Using existing access token (not expired)")
                    return AuthenticationResult(
                        auth_token=access_token,
                        user_id=credentials.get('user_id', ''),
                        expires_at=(token_time + timedelta(hours=24)).isoformat() + 'Z',
                        provider='gopro'
                    )
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid token timestamp, refreshing token: {e}")
        
        # Token expired or missing, refresh it
        refresh_token = credentials.get('refresh_token', '')
        if not refresh_token:
            raise AuthenticationError("No refresh token provided")
        
        return self.refresh_token(refresh_token)
    
    @exponential_backoff_retry(
        max_attempts=3,
        initial_delay=2.0,
        backoff_rate=2.0,
        retryable_exceptions=(NetworkError,)
    )
    def refresh_token(self, refresh_token: str) -> AuthenticationResult:
        """Refresh authentication token using OAuth 2.0 refresh token flow.
        
        Args:
            refresh_token: Refresh token from previous authentication
            
        Returns:
            AuthenticationResult with new tokens
            
        Raises:
            AuthenticationError: If refresh fails
        """
        if not self.client_id or not self.client_secret:
            raise AuthenticationError(
                "GOPRO_CLIENT_ID and GOPRO_CLIENT_SECRET environment variables must be set"
            )
        
        logger.info("Refreshing GoPro access token")
        
        try:
            response = requests.post(
                self.TOKEN_URL,
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret
                },
                timeout=30
            )
            
            if response.status_code == 401:
                raise AuthenticationError(
                    "Invalid credentials or expired refresh token. "
                    "Manual re-authentication required."
                )
            
            if response.status_code != 200:
                raise AuthenticationError(
                    f"Token refresh failed with status {response.status_code}: {response.text}"
                )
            
            data = response.json()
            
            # Calculate expiration time
            expires_in = data.get('expires_in', 86400)  # Default 24 hours
            expires_at = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat() + 'Z'
            
            logger.info(
                "Successfully refreshed GoPro access token",
                extra={
                    "expires_in": expires_in,
                    "user_id": data.get('user_id', 'unknown')
                }
            )
            
            return AuthenticationResult(
                auth_token=data['access_token'],
                user_id=data.get('user_id', ''),
                expires_at=expires_at,
                provider='gopro'
            )
            
        except requests.exceptions.Timeout as e:
            logger.error(f"Token refresh timeout: {e}")
            raise NetworkError(f"Token refresh timeout: {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Token refresh network error: {e}")
            raise NetworkError(f"Token refresh failed: {e}")
        except KeyError as e:
            raise AuthenticationError(f"Invalid token response format: missing {e}")
    
    @retry_on_api_error(
        max_attempts=3,
        initial_delay=2.0,
        status_codes=(429, 500, 502, 503, 504)
    )
    def list_media(
        self,
        auth_token: str,
        user_id: str,
        page_size: int = 100,
        max_videos: int = 1000
    ) -> List[VideoMetadata]:
        """List all videos from GoPro Cloud with pagination.
        
        Args:
            auth_token: Authentication token
            user_id: User identifier
            page_size: Number of items per page (max 100)
            max_videos: Maximum number of videos to retrieve
            
        Returns:
            List of VideoMetadata objects
            
        Raises:
            APIError: If API call fails
        """
        logger.info(
            f"Listing GoPro media for user {user_id}",
            extra={
                "user_id": user_id,
                "page_size": page_size,
                "max_videos": max_videos
            }
        )
        
        videos = []
        page = 1
        
        while len(videos) < max_videos:
            try:
                response = requests.get(
                    self.MEDIA_SEARCH_URL,
                    headers={
                        'Authorization': f'Bearer {auth_token}',
                        'Accept': 'application/json'
                    },
                    params={
                        'page': page,
                        'per_page': min(page_size, 100),  # GoPro API max is 100
                        'media_type': 'video'
                    },
                    timeout=60
                )
                
                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 60))
                    logger.warning(
                        f"Rate limited by GoPro API, retry after {retry_after}s",
                        extra={"retry_after": retry_after}
                    )
                    raise APIError(
                        f"Rate limited, retry after {retry_after}s",
                        status_code=429
                    )
                
                if response.status_code != 200:
                    raise APIError(
                        f"Media listing failed with status {response.status_code}: {response.text}",
                        status_code=response.status_code
                    )
                
                data = response.json()
                media_items = data.get('media', [])
                
                if not media_items:
                    logger.info(f"No more media items found on page {page}")
                    break
                
                # Parse media items
                for item in media_items:
                    try:
                        video = self._parse_media_item(item)
                        videos.append(video)
                        
                        if len(videos) >= max_videos:
                            logger.info(f"Reached max_videos limit: {max_videos}")
                            break
                    except (KeyError, ValueError) as e:
                        logger.warning(
                            f"Failed to parse media item: {e}",
                            extra={"media_id": item.get('id', 'unknown')}
                        )
                        continue
                
                # Check if there are more pages
                total_pages = data.get('total_pages', page)
                if page >= total_pages:
                    logger.info(f"Reached last page: {page}")
                    break
                
                page += 1
                
            except requests.exceptions.Timeout as e:
                logger.error(f"Media listing timeout on page {page}: {e}")
                raise APIError(f"Media listing timeout: {e}", status_code=408)
            except requests.exceptions.RequestException as e:
                logger.error(f"Media listing network error on page {page}: {e}")
                raise APIError(f"Media listing failed: {e}", status_code=500)
        
        logger.info(
            f"Successfully listed {len(videos)} videos from GoPro Cloud",
            extra={"video_count": len(videos), "pages_fetched": page}
        )
        
        return videos
    
    def _parse_media_item(self, item: Dict[str, Any]) -> VideoMetadata:
        """Parse a media item from GoPro API response.
        
        Args:
            item: Media item dictionary from API
            
        Returns:
            VideoMetadata object
            
        Raises:
            KeyError: If required fields are missing
            ValueError: If field values are invalid
        """
        media_id = item['id']
        filename = item.get('filename', f"{media_id}.MP4")
        
        # Get download URL
        download_url = self.get_download_url(media_id, '')  # Will be called with auth token later
        
        # Parse file size
        file_size = item.get('file_size', 0)
        if isinstance(file_size, str):
            file_size = int(file_size)
        
        # Parse upload date
        upload_date = item.get('created_at', item.get('captured_at', ''))
        if not upload_date:
            upload_date = datetime.utcnow().isoformat() + 'Z'
        
        # Parse duration (in seconds)
        duration = item.get('duration')
        if duration and isinstance(duration, str):
            try:
                duration = int(float(duration))
            except ValueError:
                duration = None
        
        return VideoMetadata(
            media_id=media_id,
            filename=filename,
            download_url=download_url,
            file_size=file_size,
            upload_date=upload_date,
            duration=duration,
            provider='gopro'
        )
    
    def get_download_url(self, media_id: str, auth_token: str) -> str:
        """Get download URL for a specific video.
        
        Args:
            media_id: Video identifier
            auth_token: Authentication token (not used for GoPro, URL is direct)
            
        Returns:
            Download URL string
            
        Raises:
            APIError: If API call fails
        """
        # GoPro API provides direct download URLs
        # Format: https://api.gopro.com/media/{media_id}/download
        return f"{self.BASE_URL}/media/{media_id}/download"


# Register GoPro provider with factory
ProviderFactory.register_provider('gopro', GoProProvider)
