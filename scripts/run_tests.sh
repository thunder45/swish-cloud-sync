#!/bin/bash
# Script to run tests with coverage

set -e

echo "=== Cloud Sync Application - Test Runner ==="
echo ""

# Check if pytest is installed
if ! python3 -m pytest --version &> /dev/null; then
    echo "Installing test dependencies..."
    pip install -r requirements-dev.txt
fi

# Run unit tests
echo "Running unit tests..."
python3 -m pytest tests/unit -v --cov=lambda_layer/python/cloud_sync_common --cov=cloud_sync --cov-report=term-missing --cov-report=html

echo ""
echo "=== Test Results ==="
echo "Coverage report generated in htmlcov/index.html"
echo ""

# Check coverage threshold
COVERAGE=$(python3 -m pytest tests/unit --cov=lambda_layer/python/cloud_sync_common --cov=cloud_sync --cov-report=term | grep "TOTAL" | awk '{print $4}' | sed 's/%//')

if [ -n "$COVERAGE" ]; then
    echo "Total coverage: ${COVERAGE}%"
    
    if (( $(echo "$COVERAGE < 80" | bc -l) )); then
        echo "⚠️  Warning: Coverage is below 80%"
    else
        echo "✓ Coverage meets 80% threshold"
    fi
fi
