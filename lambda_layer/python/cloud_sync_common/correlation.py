"""Correlation ID utilities for request tracing."""

import uuid
from typing import Optional


def generate_correlation_id() -> str:
    """Generate a new correlation ID.
    
    Returns:
        UUID string for correlation ID
    """
    return str(uuid.uuid4())


def extract_correlation_id(event: dict) -> Optional[str]:
    """Extract correlation ID from Lambda event.
    
    Args:
        event: Lambda event dictionary
        
    Returns:
        Correlation ID if present, None otherwise
    """
    # Check top-level event
    if 'correlation_id' in event:
        return event['correlation_id']
    
    # Check Step Functions context
    if 'Execution' in event and 'Id' in event['Execution']:
        return event['Execution']['Id']
    
    return None


def get_or_create_correlation_id(event: dict) -> str:
    """Get existing correlation ID or create new one.
    
    Args:
        event: Lambda event dictionary
        
    Returns:
        Correlation ID string
    """
    correlation_id = extract_correlation_id(event)
    if correlation_id:
        return correlation_id
    return generate_correlation_id()


class CorrelationContext:
    """Context manager for correlation ID."""

    def __init__(self, correlation_id: Optional[str] = None):
        """Initialize correlation context.
        
        Args:
            correlation_id: Optional correlation ID, generates new if None
        """
        self.correlation_id = correlation_id or generate_correlation_id()

    def __enter__(self):
        """Enter context."""
        return self.correlation_id

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context."""
        pass
