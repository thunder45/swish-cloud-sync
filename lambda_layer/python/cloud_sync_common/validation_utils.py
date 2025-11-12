"""Validation utilities for environment variables and configuration."""

import os
from typing import List, Dict, Any, Optional


def validate_required_env_vars(required: List[str]) -> None:
    """Validate that required environment variables are set.
    
    Args:
        required: List of required environment variable names
        
    Raises:
        ValueError: If any required environment variables are missing
    """
    missing = [var for var in required if not os.getenv(var)]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )


def validate_env_var_format(var_name: str, expected_format: str) -> None:
    """Validate environment variable format.
    
    Args:
        var_name: Environment variable name
        expected_format: Expected format description
        
    Raises:
        ValueError: If environment variable format is invalid
    """
    value = os.getenv(var_name)
    if not value:
        raise ValueError(f"Environment variable {var_name} is not set")
    
    # Add format validation logic based on expected_format
    if expected_format == "url":
        if not value.startswith(("http://", "https://")):
            raise ValueError(
                f"Environment variable {var_name} must be a valid URL"
            )
    elif expected_format == "number":
        try:
            int(value)
        except ValueError:
            raise ValueError(
                f"Environment variable {var_name} must be a number"
            )


def validate_lambda_event(
    event: Dict[str, Any],
    required_fields: List[str]
) -> None:
    """Validate Lambda event contains required fields.
    
    Args:
        event: Lambda event dictionary
        required_fields: List of required field names
        
    Raises:
        ValueError: If any required fields are missing
    """
    missing = [field for field in required_fields if field not in event]
    if missing:
        raise ValueError(
            f"Lambda event missing required fields: {', '.join(missing)}"
        )


def validate_s3_key(s3_key: str) -> None:
    """Validate S3 object key format.
    
    Args:
        s3_key: S3 object key
        
    Raises:
        ValueError: If S3 key format is invalid
    """
    if not s3_key:
        raise ValueError("S3 key cannot be empty")
    
    if s3_key.startswith("/"):
        raise ValueError("S3 key cannot start with /")
    
    if "//" in s3_key:
        raise ValueError("S3 key cannot contain consecutive slashes")
    
    # Check for invalid characters
    invalid_chars = ["\0", "\r", "\n"]
    for char in invalid_chars:
        if char in s3_key:
            raise ValueError(f"S3 key contains invalid character: {repr(char)}")


def validate_media_id(media_id: str) -> None:
    """Validate media ID format.
    
    Args:
        media_id: Media identifier
        
    Raises:
        ValueError: If media ID format is invalid
    """
    if not media_id:
        raise ValueError("Media ID cannot be empty")
    
    if len(media_id) > 255:
        raise ValueError("Media ID cannot exceed 255 characters")
    
    # Media ID should be alphanumeric with hyphens and underscores
    if not all(c.isalnum() or c in ("-", "_") for c in media_id):
        raise ValueError(
            "Media ID can only contain alphanumeric characters, hyphens, and underscores"
        )


def validate_file_size(file_size: int, max_size: Optional[int] = None) -> None:
    """Validate file size.
    
    Args:
        file_size: File size in bytes
        max_size: Optional maximum file size in bytes
        
    Raises:
        ValueError: If file size is invalid
    """
    if file_size < 0:
        raise ValueError("File size cannot be negative")
    
    if file_size == 0:
        raise ValueError("File size cannot be zero")
    
    if max_size and file_size > max_size:
        raise ValueError(
            f"File size {file_size} exceeds maximum {max_size} bytes"
        )


def validate_provider_name(provider: str, allowed_providers: List[str]) -> None:
    """Validate provider name.
    
    Args:
        provider: Provider name
        allowed_providers: List of allowed provider names
        
    Raises:
        ValueError: If provider name is invalid
    """
    if not provider:
        raise ValueError("Provider name cannot be empty")
    
    if provider not in allowed_providers:
        raise ValueError(
            f"Invalid provider '{provider}'. "
            f"Allowed providers: {', '.join(allowed_providers)}"
        )


def validate_sync_status(status: str) -> None:
    """Validate sync status value.
    
    Args:
        status: Sync status
        
    Raises:
        ValueError: If status is invalid
    """
    valid_statuses = ["PENDING", "IN_PROGRESS", "COMPLETED", "FAILED"]
    if status not in valid_statuses:
        raise ValueError(
            f"Invalid sync status '{status}'. "
            f"Valid statuses: {', '.join(valid_statuses)}"
        )
