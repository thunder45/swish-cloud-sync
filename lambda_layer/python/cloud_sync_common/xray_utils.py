"""AWS X-Ray tracing utilities."""

from typing import Optional, Callable, Any
from functools import wraps
from aws_xray_sdk.core import xray_recorder


def trace_subsegment(name: str):
    """Decorator to create X-Ray subsegment for function.
    
    Args:
        name: Subsegment name
        
    Returns:
        Decorated function with X-Ray tracing
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            subsegment = xray_recorder.begin_subsegment(name)
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                subsegment.put_metadata('error', str(e))
                raise
            finally:
                xray_recorder.end_subsegment()
        return wrapper
    return decorator


def add_annotation(key: str, value: Any) -> None:
    """Add annotation to current X-Ray segment.
    
    Annotations are indexed and searchable.
    
    Args:
        key: Annotation key
        value: Annotation value (string, number, or boolean)
    """
    try:
        xray_recorder.put_annotation(key, value)
    except Exception:
        # Silently fail if X-Ray is not available
        pass


def add_metadata(key: str, value: Any, namespace: str = 'default') -> None:
    """Add metadata to current X-Ray segment.
    
    Metadata is not indexed but can contain complex objects.
    
    Args:
        key: Metadata key
        value: Metadata value (any JSON-serializable object)
        namespace: Metadata namespace
    """
    try:
        xray_recorder.put_metadata(key, value, namespace)
    except Exception:
        # Silently fail if X-Ray is not available
        pass


def trace_provider_api_call(provider: str, operation: str):
    """Decorator to trace cloud provider API calls.
    
    Args:
        provider: Provider name (e.g., 'gopro')
        operation: Operation name (e.g., 'list_media', 'download')
        
    Returns:
        Decorated function with X-Ray tracing
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            subsegment_name = f"{provider}_api_{operation}"
            subsegment = xray_recorder.begin_subsegment(subsegment_name)
            subsegment.put_annotation('provider', provider)
            subsegment.put_annotation('operation', operation)
            
            try:
                result = func(*args, **kwargs)
                subsegment.put_metadata('success', True)
                return result
            except Exception as e:
                subsegment.put_metadata('error', str(e))
                subsegment.put_metadata('success', False)
                raise
            finally:
                xray_recorder.end_subsegment()
        return wrapper
    return decorator


def trace_s3_operation(operation: str):
    """Decorator to trace S3 operations.
    
    Args:
        operation: S3 operation name (e.g., 'upload', 'multipart_upload')
        
    Returns:
        Decorated function with X-Ray tracing
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            subsegment_name = f"s3_{operation}"
            subsegment = xray_recorder.begin_subsegment(subsegment_name)
            subsegment.put_annotation('service', 's3')
            subsegment.put_annotation('operation', operation)
            
            try:
                result = func(*args, **kwargs)
                subsegment.put_metadata('success', True)
                return result
            except Exception as e:
                subsegment.put_metadata('error', str(e))
                subsegment.put_metadata('success', False)
                raise
            finally:
                xray_recorder.end_subsegment()
        return wrapper
    return decorator
