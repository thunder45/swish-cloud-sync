"""Retry utilities with exponential backoff."""

import time
import logging
from functools import wraps
from typing import Callable, Type, Tuple
from .exceptions import NetworkError, TimeoutError, APIError

logger = logging.getLogger(__name__)


def exponential_backoff_retry(
    max_attempts: int = 3,
    initial_delay: float = 2.0,
    backoff_rate: float = 2.0,
    max_delay: float = 60.0,
    retryable_exceptions: Tuple[Type[Exception], ...] = (NetworkError, TimeoutError)
):
    """Decorator for retrying functions with exponential backoff.
    
    Args:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        backoff_rate: Multiplier for delay after each attempt
        max_delay: Maximum delay in seconds
        retryable_exceptions: Tuple of exception types to retry
        
    Returns:
        Decorated function with retry logic
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            last_exception = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt == max_attempts:
                        logger.error(
                            f"Function {func.__name__} failed after {max_attempts} attempts",
                            extra={
                                "function": func.__name__,
                                "attempts": max_attempts,
                                "error": str(e)
                            }
                        )
                        raise
                    
                    logger.warning(
                        f"Attempt {attempt}/{max_attempts} failed for {func.__name__}, "
                        f"retrying in {delay}s",
                        extra={
                            "function": func.__name__,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "delay": delay,
                            "error": str(e)
                        }
                    )
                    
                    time.sleep(delay)
                    delay = min(delay * backoff_rate, max_delay)
            
            # Should never reach here, but just in case
            if last_exception:
                raise last_exception
                
        return wrapper
    return decorator


def retry_on_api_error(
    max_attempts: int = 3,
    initial_delay: float = 2.0,
    status_codes: Tuple[int, ...] = (429, 500, 502, 503, 504)
):
    """Decorator for retrying API calls on specific status codes.
    
    Args:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        status_codes: HTTP status codes to retry on
        
    Returns:
        Decorated function with retry logic
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except APIError as e:
                    if e.status_code not in status_codes or attempt == max_attempts:
                        raise
                    
                    logger.warning(
                        f"API call {func.__name__} failed with status {e.status_code}, "
                        f"retrying in {delay}s",
                        extra={
                            "function": func.__name__,
                            "attempt": attempt,
                            "status_code": e.status_code,
                            "delay": delay
                        }
                    )
                    
                    time.sleep(delay)
                    delay *= 2.0
                    
        return wrapper
    return decorator
