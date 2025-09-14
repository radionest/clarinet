#!/bin/bash

# Watch script for Clarinet frontend development

set -e

echo "Starting Clarinet frontend in watch mode..."

# Change to frontend directory
cd "$(dirname "$0")/.."

# Check if entr is installed
if ! command -v entr &> /dev/null; then
    echo "Warning: 'entr' is not installed. Install it for file watching:"
    echo "  Ubuntu/Debian: sudo apt-get install entr"
    echo "  macOS: brew install entr"
    echo ""
    echo "Running single build instead..."
    ./scripts/build.sh
    exit 0
fi

# Watch Gleam source files and rebuild on change
echo "Watching for changes in src/ directory..."
echo "Press Ctrl+C to stop"

find src -name "*.gleam" -o -name "*.js" | entr -c ./scripts/build.sh