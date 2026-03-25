#!/bin/bash

# Dev build script for Clarinet frontend (no minification for faster rebuilds).
# outdir is read from gleam.toml [tools.lustre.build]; --minify is intentionally
# omitted here so dev builds stay fast and readable.

set -e

echo "Building Clarinet frontend (dev)..."

# Download dependencies if needed
if [ ! -d "build/packages" ]; then
    echo "Downloading dependencies..."
    gleam deps download
fi

# Build bundled JS without minification
echo "Bundling JavaScript..."
gleam run -m lustre/dev build

# Copy static assets from public/
if [ -d "public" ]; then
    cp -r public/* ../../clarinet/static/
fi

echo "Dev build complete!"
