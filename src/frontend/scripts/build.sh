#!/bin/bash

# Build script for Clarinet frontend

set -e

echo "Building Clarinet frontend..."

# Download dependencies if needed
if [ ! -d "build/(.venv) nest@nest-home:~/clarinet-feature$ cd src/frontend/
(.venv) nest@nest-home:~/clarinet-feature/src/frontend$ gleam build
error: File IO failure

An error occurred while trying to read this file:

    /home/nest/clarinet-feature/frontend/src/api/client.gleam

The error message from the file IO library was:

    No such file or directory (os error 2)

packages" ]; then
    echo "Downloading dependencies..."
    gleam deps download
fi

# Build the project
echo "Compiling Gleam to JavaScript..."
gleam build --target javascript

# Check if build was successful
if [ -f "build/dev/javascript/clarinet.mjs" ]; then
    echo "✓ Frontend built successfully!"
    echo "  Output: build/dev/javascript/clarinet.mjs"
else
    echo "✗ Build failed!"
    exit 1
fi

# Copy static assets if needed
if [ ! -f "static/favicon.ico" ]; then
    echo "Creating default favicon..."
    # Create a simple SVG favicon
    cat > static/favicon.svg << 'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" fill="#3498db"/>
  <text x="50" y="50" font-family="sans-serif" font-size="60" fill="white" text-anchor="middle" dominant-baseline="middle">C</text>
</svg>
EOF
fi

echo "Build complete!"