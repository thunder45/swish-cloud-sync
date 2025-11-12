"""Custom exceptions for Cloud Sync Application."""


class CloudSyncError(Exception):
    """Base exception for Cloud Sync Application."""
    pass


class AuthenticationError(CloudSyncError):
    """Raised when authentication fails."""
    pass


class APIError(CloudSyncError):
    """Raised when API call fails."""
    
    def __init__(self, message: str, status_code: int = None):
        super().__init__(message)
        self.status_code = status_code


class NetworkError(CloudSyncError):
    """Raised when network operation fails."""
    pass


class TimeoutError(CloudSyncError):
    """Raised when operation times out."""
    pass


class ValidationError(CloudSyncError):
    """Raised when data validation fails."""
    pass


class StorageError(CloudSyncError):
    """Raised when storage operation fails."""
    pass


class TransferError(CloudSyncError):
    """Raised when video transfer fails."""
    pass


class ProviderError(CloudSyncError):
    """Raised when cloud provider operation fails."""
    pass
