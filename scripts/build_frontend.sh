#!/bin/bash
set -e

echo "Building Clarinet frontend..."

cd clarinet/frontend

# Clean old build artifacts
rm -rf build/

STATIC_DIR="../../clarinet/static"
rm -rf "$STATIC_DIR"
mkdir -p "$STATIC_DIR"/{css,assets}

# Download deps and build minified bundle (lustre_dev_tools + bun).
# outdir is read from gleam.toml [tools.lustre.build]; --minify is prod-only.
gleam deps download
gleam run -m lustre/dev build --minify

# Copy HTML/CSS from public/ (overwrites lustre-generated index.html)
if [ -d "public" ]; then
    cp -r public/* "$STATIC_DIR/"
fi

echo "Frontend build complete! Output in clarinet/static/"
echo "Bundle: $(du -h "$STATIC_DIR/clarinet_frontend.js" | cut -f1)"
