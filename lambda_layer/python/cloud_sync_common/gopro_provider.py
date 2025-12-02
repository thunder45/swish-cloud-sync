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
        cookies: str = None,
        user_agent: str = None,
        auth_token: str = None,
        user_id: str = None,
        page_size: int = 100,
        max_videos: int = None,
        max_results: int = 1000
    ) -> List[VideoMetadata]:
        """List all videos from GoPro Cloud with pagination.
        
        Supports both cookie-based and OAuth authentication.
        
        Args:
            cookies: Cookie header string (for unofficial API)
            user_agent: User agent string (for unofficial API)
            auth_token: Authentication token (for OAuth, deprecated)
            user_id: User identifier (for OAuth, deprecated)
            page_size: Number of items per page (max 100)
            max_videos: Maximum number of videos (deprecated, use max_results)
            max_results: Maximum number of videos to retrieve
            
        Returns:
            List of VideoMetadata objects
            
        Raises:
            APIError: If API call fails
        """
        # Handle legacy parameter names
        if max_videos and not max_results:
            max_results = max_videos
        elif not max_results:
            max_results = 1000
            
        logger.info(
            f"Listing GoPro media",
            extra={
                "page_size": page_size,
                "max_results": max_results,
                "auth_method": "cookies" if cookies else "oauth"
            }
        )
        
        videos = []
        page = 1
        
        while len(videos) < max_results:
            try:
                # Build headers based on auth method
                if cookies:
                    # Cookie-based authentication (unofficial API)
                    headers = {
                        'Cookie': cookies,
                        'User-Agent': user_agent or 'Mozilla/5.0',
                        'Accept': 'application/vnd.gopro.jk.media+json; version=2.0.0',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Referer': 'https://gopro.com/'
                    }
                    # Use unofficial API endpoint
                    api_url = "https://api.gopro.com/media/search"
                else:
                    # OAuth authentication (official API)
                    headers = {
                        'Authorization': f'Bearer {auth_token}',
                        'Accept': 'application/json'
                    }
                    api_url = self.MEDIA_SEARCH_URL
                
                # Build params
                params = {
                    'page': page,
                    'per_page': min(page_size, 100),  # GoPro API max is 100
                }
                
                response = requests.get(
                    api_url,
                    headers=headers,
                    params=params,
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
                
                # Handle both response structures
                # Official API: data['media']
                # Unofficial API: data['_embedded']['media']
                if '_embedded' in data:
                    media_items = data['_embedded'].get('media', [])
                else:
                    media_items = data.get('media', [])
                
                if not media_items:
                    logger.info(f"No more media items found on page {page}")
                    break
                
                # Parse media items
                for item in media_items:
                    try:
                        # Strict filtering for GoPro camera files only
                        filename = item.get('filename', '')
                        
                        # Skip if no filename
                        if not filename:
                            logger.debug(f"Skipping item with no filename: {item.get('id')}")
                            continue
                        
                        # Only include files starting with GH or GO (GoPro camera naming)
                        # GH = GoPro HERO series, GO = older GoPro models
                        if not (filename.startswith('GH') or filename.startswith('GO')):
                            logger.debug(f"Skipping non-GoPro filename: {filename}")
                            continue
                        
                        video = self._parse_media_item(item)
                        videos.append(video)
                        
                        if len(videos) >= max_results:
                            logger.info(f"Reached max_results limit: {max_results}")
                            break
                    except (KeyError, ValueError) as e:
                        logger.warning(
                            f"Failed to parse media item: {e}",
                            extra={"media_id": item.get('id', 'unknown')}
                        )
                        continue
                
                # Check if there are more pages
                # Unofficial API uses _pages with different field names
                if '_pages' in data:
                    total_pages = data['_pages'].get('total_pages', data['_pages'].get('total', 1))
                    current_page = data['_pages'].get('current_page', data['_pages'].get('page', page))
                    total_items = data['_pages'].get('total_items', 0)
                    
                    logger.info(f"Page {current_page}/{total_pages}, {len(media_items)} items on this page, {total_items} total items")
                    
                    if current_page >= total_pages:
                        logger.info(f"Reached last page: {current_page}/{total_pages}")
                        break
                else:
                    # Official API
                    total_pages = data.get('total_pages', page)
                    if page >= total_pages:
                        logger.info(f"Reached last page: {page}/{total_pages}")
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
        # Unofficial API provides a 'token' field for downloading
        token = item.get('token', '')
        if token:
            # Use token-based URL for unofficial API
            # URL format: https://api.gopro.com/media/{media_id}/download?t={token}
            download_url = f"https://api.gopro.com/media/{media_id}/download?t={token}"
        else:
            # Official API format
            download_url = self.get_download_url(media_id, '')
        
        # Parse file size (may be null in unofficial API)
        file_size = item.get('file_size')
        if file_size is None:
            # Estimate based on resolution and duration
            # For now, use 0 - will be updated during download
            file_size = 0
        elif isinstance(file_size, str):
            file_size = int(file_size)
        
        # Parse upload date
        upload_date = item.get('created_at') or item.get('captured_at') or item.get('client_updated_at')
        if not upload_date:
            upload_date = datetime.utcnow().isoformat() + 'Z'
        
        # Parse duration (may be null or in source_duration in milliseconds)
        duration = item.get('duration') or item.get('source_duration')
        if duration and isinstance(duration, str):
            try:
                # source_duration is in milliseconds, convert to seconds
                duration = int(float(duration)) // 1000
            except ValueError:
                duration = 0
        elif isinstance(duration, int):
            # Already a number, convert from milliseconds to seconds
            duration = duration // 1000
        elif duration is None:
            duration = 0
        
        return VideoMetadata(
            media_id=media_id,
            filename=filename,
            download_url=download_url,
            file_size=file_size,
            upload_date=upload_date,
            duration=duration,
            provider='gopro'
        )
    
    def get_download_url(
        self,
        media_id: str,
        cookies: str = None,
        user_agent: str = None,
        auth_token: str = None,
        quality: str = 'source'
    ) -> str:
        """Get download URL for a specific video.
        
        For unofficial API, this is a 2-step process:
        1. Call /media/{media_id}/download to get file variations
        2. Extract the pre-signed CloudFront URL for the desired quality
        
        Args:
            media_id: Video identifier
            cookies: Cookie string (for unofficial API)
            user_agent: User agent (for unofficial API)
            auth_token: Authentication token (for OAuth, deprecated)
            quality: Desired quality ('source', 'high_res_proxy_mp4', 'edit_proxy')
            
        Returns:
            Pre-signed CloudFront download URL
            
        Raises:
            APIError: If API call fails or quality not available
        """
        if cookies:
            # Unofficial API: 2-step download process
            headers = {
                'Cookie': cookies,
                'User-Agent': user_agent or 'Mozilla/5.0',
                'Accept': 'application/vnd.gopro.jk.media+json; version=2.0.0',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://gopro.com/'
            }
            
            try:
                response = requests.get(
                    f"https://api.gopro.com/media/{media_id}/download",
                    headers=headers,
                    timeout=30
                )
                
                if response.status_code != 200:
                    raise APIError(
                        f"Failed to get download URL: HTTP {response.status_code}",
                        status_code=response.status_code
                    )
                
                data = response.json()
                
                # Find the requested quality in files or variations
                files = data.get('_embedded', {}).get('files', [])
                variations = data.get('_embedded', {}).get('variations', [])
                
                # Try files first (primary quality)
                for file in files:
                    if file.get('label') == quality and file.get('available'):
                        return file['url']
                
                # Try variations (alternative qualities)
                for var in variations:
                    if var.get('label') == quality and var.get('available'):
                        return var['url']
                
                # If source not found, try high_res_proxy_mp4
                if quality == 'source':
                    logger.warning(f"Source quality not available for {media_id}, trying high_res_proxy_mp4")
                    for file in files + variations:
                        if file.get('label') == 'high_res_proxy_mp4' and file.get('available'):
                            return file['url']
                
                raise APIError(
                    f"Quality '{quality}' not available for media {media_id}",
                    status_code=404
                )
                
            except requests.exceptions.Timeout as e:
                raise APIError(f"Timeout getting download URL: {e}", status_code=408)
            except requests.exceptions.RequestException as e:
                raise APIError(f"Network error getting download URL: {e}", status_code=500)
        else:
            # Official API format
            return f"{self.BASE_URL}/media/{media_id}/download"


    def list_media_with_start_page(
        self,
        cookies: str,
        user_agent: str,
        start_page: int,
        page_size: int = 30,
        max_results: int = 50
    ) -> tuple[List[VideoMetadata], Dict[str, Any]]:
        """List media starting from specific API page.
        
        GoPro API uses page-based pagination with 30 items per page.
        This method allows starting from any page to implement pagination across executions.
        
        Args:
            cookies: Cookie header string
            user_agent: User agent string
            start_page: API page number to start from (1-indexed)
            page_size: Items per page (API default is 30)
            max_results: Maximum videos to return
            
        Returns:
            Tuple of (List of VideoMetadata objects, pagination metadata dict)
            
        Raises:
            APIError: If API call fails
        """
        logger.info(f"Listing media from page {start_page} (max_results={max_results})")
        
        videos = []
        page = start_page
        pages_needed = (max_results + page_size - 1) // page_size
        pagination_metadata = {}
        
        for _ in range(pages_needed):
            try:
                headers = {
                    'Cookie': cookies,
                    'User-Agent': user_agent,
                    'Accept': 'application/vnd.gopro.jk.media+json; version=2.0.0',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Referer': 'https://gopro.com/'
                }
                
                params = {
                    'page': page,
                    'per_page': page_size,
                }
                
                response = requests.get(
                    "https://api.gopro.com/media/search",
                    headers=headers,
                    params=params,
                    timeout=60
                )
                
                if response.status_code == 429:
                    raise APIError("Rate limited", status_code=429)
                
                if response.status_code != 200:
                    raise APIError(
                        f"Media listing failed: HTTP {response.status_code}",
                        status_code=response.status_code
                    )
                
                data = response.json()
                media_items = data.get('_embedded', {}).get('media', [])
                
                if not media_items:
                    logger.info(f"No more items on page {page}")
                    break
                
                # Parse and filter media items
                for item in media_items:
                    filename = item.get('filename', '')
                    
                    if not filename:
                        continue
                    
                    # Only GoPro camera files (GH*/GO*)
                    if not (filename.startswith('GH') or filename.startswith('GO')):
                        continue
                    
                    try:
                        video = self._parse_media_item(item)
                        videos.append(video)
                        
                        if len(videos) >= max_results:
                            logger.info(f"Reached max_results: {max_results}")
                            return videos, pagination_metadata
                            
                    except (KeyError, ValueError) as e:
                        logger.warning(f"Failed to parse item: {e}")
                        continue
                
                # Check pagination info
                pages_info = data.get('_pages', {})
                current_page = pages_info.get('current_page', page)
                total_pages = pages_info.get('total_pages', page)
                total_items = pages_info.get('total_items', 0)
                per_page = pages_info.get('per_page', page_size)
                
                # Store pagination metadata from API response
                pagination_metadata = {
                    'current_page': current_page,
                    'total_pages': total_pages,
                    'total_items': total_items,
                    'per_page': per_page
                }
                
                logger.info(f"Page {current_page}/{total_pages}, got {len(media_items)} items, {total_items} total items")
                
                if current_page >= total_pages:
                    logger.info("Reached last page")
                    break
                
                page += 1
                
            except requests.exceptions.Timeout:
                raise APIError(f"Timeout on page {page}", status_code=408)
            except requests.exceptions.RequestException as e:
                raise APIError(f"Network error on page {page}: {e}", status_code=500)
        
        logger.info(f"Retrieved {len(videos)} videos from page {start_page}")
        logger.info(f"Pagination: {pagination_metadata}")
        return videos, pagination_metadata


# Register GoPro provider with factory
ProviderFactory.register_provider('gopro', GoProProvider)
