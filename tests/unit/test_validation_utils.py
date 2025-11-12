"""Unit tests for validation utilities."""

import pytest
import os
from lambda_layer.python.cloud_sync_common.validation_utils import (
    validate_required_env_vars,
    validate_lambda_event,
    validate_s3_key,
    validate_media_id,
    validate_file_size,
    validate_provider_name,
    validate_sync_status
)


class TestValidateRequiredEnvVars:
    """Test cases for validate_required_env_vars."""

    def test_all_vars_present(self, monkeypatch):
        """Test when all required vars are present."""
        monkeypatch.setenv("VAR1", "value1")
        monkeypatch.setenv("VAR2", "value2")
        
        # Should not raise
        validate_required_env_vars(["VAR1", "VAR2"])

    def test_missing_vars(self, monkeypatch):
        """Test when required vars are missing."""
        monkeypatch.setenv("VAR1", "value1")
        
        with pytest.raises(ValueError, match="Missing required environment variables: VAR2"):
            validate_required_env_vars(["VAR1", "VAR2"])

    def test_empty_list(self):
        """Test with empty list of required vars."""
        # Should not raise
        validate_required_env_vars([])


class TestValidateLambdaEvent:
    """Test cases for validate_lambda_event."""

    def test_all_fields_present(self):
        """Test when all required fields are present."""
        event = {"field1": "value1", "field2": "value2"}
        
        # Should not raise
        validate_lambda_event(event, ["field1", "field2"])

    def test_missing_fields(self):
        """Test when required fields are missing."""
        event = {"field1": "value1"}
        
        with pytest.raises(ValueError, match="Lambda event missing required fields: field2"):
            validate_lambda_event(event, ["field1", "field2"])

    def test_empty_event(self):
        """Test with empty event."""
        with pytest.raises(ValueError):
            validate_lambda_event({}, ["field1"])


class TestValidateS3Key:
    """Test cases for validate_s3_key."""

    def test_valid_s3_key(self):
        """Test valid S3 key."""
        # Should not raise
        validate_s3_key("folder/subfolder/file.txt")

    def test_empty_key(self):
        """Test empty S3 key."""
        with pytest.raises(ValueError, match="S3 key cannot be empty"):
            validate_s3_key("")

    def test_key_starts_with_slash(self):
        """Test S3 key starting with slash."""
        with pytest.raises(ValueError, match="S3 key cannot start with /"):
            validate_s3_key("/folder/file.txt")

    def test_key_with_consecutive_slashes(self):
        """Test S3 key with consecutive slashes."""
        with pytest.raises(ValueError, match="S3 key cannot contain consecutive slashes"):
            validate_s3_key("folder//file.txt")

    def test_key_with_invalid_chars(self):
        """Test S3 key with invalid characters."""
        with pytest.raises(ValueError, match="S3 key contains invalid character"):
            validate_s3_key("folder/file\0.txt")


class TestValidateMediaId:
    """Test cases for validate_media_id."""

    def test_valid_media_id(self):
        """Test valid media ID."""
        # Should not raise
        validate_media_id("abc123-def_456")

    def test_empty_media_id(self):
        """Test empty media ID."""
        with pytest.raises(ValueError, match="Media ID cannot be empty"):
            validate_media_id("")

    def test_media_id_too_long(self):
        """Test media ID exceeding max length."""
        long_id = "a" * 256
        with pytest.raises(ValueError, match="Media ID cannot exceed 255 characters"):
            validate_media_id(long_id)

    def test_media_id_invalid_chars(self):
        """Test media ID with invalid characters."""
        with pytest.raises(ValueError, match="can only contain alphanumeric"):
            validate_media_id("abc@123")


class TestValidateFileSize:
    """Test cases for validate_file_size."""

    def test_valid_file_size(self):
        """Test valid file size."""
        # Should not raise
        validate_file_size(1000)

    def test_negative_file_size(self):
        """Test negative file size."""
        with pytest.raises(ValueError, match="File size cannot be negative"):
            validate_file_size(-1)

    def test_zero_file_size(self):
        """Test zero file size."""
        with pytest.raises(ValueError, match="File size cannot be zero"):
            validate_file_size(0)

    def test_file_size_exceeds_max(self):
        """Test file size exceeding maximum."""
        with pytest.raises(ValueError, match="exceeds maximum"):
            validate_file_size(2000, max_size=1000)

    def test_file_size_within_max(self):
        """Test file size within maximum."""
        # Should not raise
        validate_file_size(500, max_size=1000)


class TestValidateProviderName:
    """Test cases for validate_provider_name."""

    def test_valid_provider(self):
        """Test valid provider name."""
        # Should not raise
        validate_provider_name("gopro", ["gopro", "google_drive"])

    def test_empty_provider(self):
        """Test empty provider name."""
        with pytest.raises(ValueError, match="Provider name cannot be empty"):
            validate_provider_name("", ["gopro"])

    def test_invalid_provider(self):
        """Test invalid provider name."""
        with pytest.raises(ValueError, match="Invalid provider"):
            validate_provider_name("unknown", ["gopro", "google_drive"])


class TestValidateSyncStatus:
    """Test cases for validate_sync_status."""

    def test_valid_status(self):
        """Test valid sync status."""
        for status in ["PENDING", "IN_PROGRESS", "COMPLETED", "FAILED"]:
            # Should not raise
            validate_sync_status(status)

    def test_invalid_status(self):
        """Test invalid sync status."""
        with pytest.raises(ValueError, match="Invalid sync status"):
            validate_sync_status("UNKNOWN")

    def test_lowercase_status(self):
        """Test lowercase status (should fail)."""
        with pytest.raises(ValueError, match="Invalid sync status"):
            validate_sync_status("completed")
