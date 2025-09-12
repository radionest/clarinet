#!/bin/bash
# Script to run Clarinet integration tests

echo "Running Clarinet Integration Tests"
echo "=================================="

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install development dependencies
echo "Installing test dependencies..."
pip install -q pytest pytest-asyncio pytest-cov

# Install project in development mode
echo "Installing project..."
pip install -q -e .

# Run tests
echo "Running tests..."
python -m pytest tests/integration/ -v --tb=short

# Deactivate virtual environment
deactivate

echo "Tests completed!"