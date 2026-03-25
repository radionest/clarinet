#!/bin/bash

# Dev build script for Clarinet frontend (no minification for faster rebuilds)

set -e

echo "Building Clarinet frontend (dev)..."

# Download dependencies if needed
if [ ! -d "build/packages" ]; then
    echo "Downloading dependencies..."
    gleam deps download
fi

# Build bundled JS (without minification for speed)
echo "Bundling JavaScript..."
gleam run -m lustre/dev build --outdir=../../clarinet/static

# Copy static assets from public/
if [ -d "public" ]; then
    cp -r public/* ../../clarinet/static/
fi

echo "Dev build complete!"
