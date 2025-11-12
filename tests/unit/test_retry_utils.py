"""Unit tests for retry utilities."""

import pytest
import time
from lambda_layer.python.cloud_sync_common.retry_utils import (
    exponential_backoff_retry,
    retry_on_api_error
)
from lambda_layer.python.cloud_sync_common.exceptions import (
    NetworkError,
    TimeoutError,
    APIError
)


class TestExponentialBackoffRetry:
    """Test cases for exponential_backoff_retry decorator."""

    def test_successful_execution_no_retry(self):
        """Test successful execution without retry."""
        call_count = 0

        @exponential_backoff_retry(max_attempts=3)
        def successful_function():
            nonlocal call_count
            call_count += 1
            return "success"

        result = successful_function()
        assert result == "success"
        assert call_count == 1

    def test_retry_on_network_error(self):
        """Test retry on NetworkError."""
        call_count = 0

        @exponential_backoff_retry(max_attempts=3, initial_delay=0.1)
        def failing_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise NetworkError("Network error")
            return "success"

        result = failing_function()
        assert result == "success"
        assert call_count == 3

    def test_max_attempts_exceeded(self):
        """Test that exception is raised after max attempts."""
        call_count = 0

        @exponential_backoff_retry(max_attempts=3, initial_delay=0.1)
        def always_failing_function():
            nonlocal call_count
            call_count += 1
            raise NetworkError("Network error")

        with pytest.raises(NetworkError):
            always_failing_function()
        assert call_count == 3

    def test_non_retryable_exception(self):
        """Test that non-retryable exceptions are not retried."""
        call_count = 0

        @exponential_backoff_retry(max_attempts=3, retryable_exceptions=(NetworkError,))
        def function_with_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("Not retryable")

        with pytest.raises(ValueError):
            function_with_value_error()
        assert call_count == 1

    def test_exponential_backoff_timing(self):
        """Test that backoff timing increases exponentially."""
        call_times = []

        @exponential_backoff_retry(max_attempts=3, initial_delay=0.1, backoff_rate=2.0)
        def timed_function():
            call_times.append(time.time())
            if len(call_times) < 3:
                raise NetworkError("Network error")
            return "success"

        timed_function()
        
        # Check that delays increase (with some tolerance for timing)
        assert len(call_times) == 3
        delay1 = call_times[1] - call_times[0]
        delay2 = call_times[2] - call_times[1]
        assert delay1 >= 0.09  # ~0.1s with tolerance
        assert delay2 >= 0.18  # ~0.2s with tolerance


class TestRetryOnAPIError:
    """Test cases for retry_on_api_error decorator."""

    def test_retry_on_429_status(self):
        """Test retry on 429 (rate limit) status."""
        call_count = 0

        @retry_on_api_error(max_attempts=3, initial_delay=0.1)
        def api_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise APIError("Rate limited", status_code=429)
            return "success"

        result = api_function()
        assert result == "success"
        assert call_count == 3

    def test_retry_on_500_status(self):
        """Test retry on 500 (server error) status."""
        call_count = 0

        @retry_on_api_error(max_attempts=3, initial_delay=0.1)
        def api_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise APIError("Server error", status_code=500)
            return "success"

        result = api_function()
        assert result == "success"
        assert call_count == 2

    def test_no_retry_on_400_status(self):
        """Test no retry on 400 (client error) status."""
        call_count = 0

        @retry_on_api_error(max_attempts=3, status_codes=(429, 500, 502, 503, 504))
        def api_function():
            nonlocal call_count
            call_count += 1
            raise APIError("Bad request", status_code=400)

        with pytest.raises(APIError):
            api_function()
        assert call_count == 1

    def test_max_attempts_exceeded_api_error(self):
        """Test that APIError is raised after max attempts."""
        call_count = 0

        @retry_on_api_error(max_attempts=3, initial_delay=0.1)
        def api_function():
            nonlocal call_count
            call_count += 1
            raise APIError("Rate limited", status_code=429)

        with pytest.raises(APIError):
            api_function()
        assert call_count == 3

    def test_custom_status_codes(self):
        """Test retry with custom status codes."""
        call_count = 0

        @retry_on_api_error(max_attempts=3, initial_delay=0.1, status_codes=(503,))
        def api_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise APIError("Service unavailable", status_code=503)
            return "success"

        result = api_function()
        assert result == "success"
        assert call_count == 2

    def test_no_retry_on_non_matching_status(self):
        """Test no retry when status code doesn't match."""
        call_count = 0

        @retry_on_api_error(max_attempts=3, status_codes=(503,))
        def api_function():
            nonlocal call_count
            call_count += 1
            raise APIError("Rate limited", status_code=429)

        with pytest.raises(APIError):
            api_function()
        assert call_count == 1
