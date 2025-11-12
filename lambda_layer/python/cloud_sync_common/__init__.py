"""Cloud Sync Common Library - Shared utilities for Lambda functions."""

__version__ = "1.0.0"

# Import provider implementations to trigger registration
from . import gopro_provider  # noqa: F401
