"""Logging utilities with structured JSON logging."""

import json
import logging
import sys
from datetime import datetime
from typing import Any, Dict, Optional


class StructuredLogger:
    """Structured JSON logger for Lambda functions."""

    def __init__(self, name: str, correlation_id: Optional[str] = None):
        """Initialize structured logger.
        
        Args:
            name: Logger name (typically function name)
            correlation_id: Optional correlation ID for request tracing
        """
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)
        self.correlation_id = correlation_id
        self.function_name = name

        # Remove existing handlers
        self.logger.handlers = []

        # Add JSON handler
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        self.logger.addHandler(handler)

    def _build_log_entry(
        self,
        level: str,
        message: str,
        extra: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Build structured log entry.
        
        Args:
            level: Log level
            message: Log message
            extra: Additional fields
            
        Returns:
            Structured log entry dictionary
        """
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": level,
            "function_name": self.function_name,
            "message": message
        }

        if self.correlation_id:
            entry["correlation_id"] = self.correlation_id

        if extra:
            entry.update(extra)

        return entry

    def debug(self, message: str, **kwargs):
        """Log debug message."""
        entry = self._build_log_entry("DEBUG", message, kwargs)
        self.logger.debug(json.dumps(entry))

    def info(self, message: str, **kwargs):
        """Log info message."""
        entry = self._build_log_entry("INFO", message, kwargs)
        self.logger.info(json.dumps(entry))

    def warning(self, message: str, **kwargs):
        """Log warning message."""
        entry = self._build_log_entry("WARN", message, kwargs)
        self.logger.warning(json.dumps(entry))

    def error(self, message: str, **kwargs):
        """Log error message."""
        entry = self._build_log_entry("ERROR", message, kwargs)
        self.logger.error(json.dumps(entry))

    def critical(self, message: str, **kwargs):
        """Log critical message."""
        entry = self._build_log_entry("CRITICAL", message, kwargs)
        self.logger.critical(json.dumps(entry))


class JsonFormatter(logging.Formatter):
    """JSON formatter for log records."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON.
        
        Args:
            record: Log record
            
        Returns:
            JSON formatted log string
        """
        # If message is already JSON, return as-is
        try:
            json.loads(record.getMessage())
            return record.getMessage()
        except (json.JSONDecodeError, ValueError):
            # Otherwise, create basic JSON structure
            log_entry = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "level": record.levelname,
                "message": record.getMessage()
            }
            
            if record.exc_info:
                log_entry["exception"] = self.formatException(record.exc_info)
                
            return json.dumps(log_entry)


def get_logger(name: str, correlation_id: Optional[str] = None) -> StructuredLogger:
    """Get a structured logger instance.
    
    Args:
        name: Logger name
        correlation_id: Optional correlation ID
        
    Returns:
        StructuredLogger instance
    """
    return StructuredLogger(name, correlation_id)
