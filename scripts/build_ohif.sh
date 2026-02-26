#!/usr/bin/env bash
# Download pre-built OHIF Viewer v3 from npm and install into src/ohif/.
#
# The @ohif/app npm package ships with a pre-built production bundle in dist/.
# This script downloads it and copies the files, preserving our app-config.js.
#
# Usage: bash scripts/build_ohif.sh [version]
# Default version: 3.12.0

set -euo pipefail

OHIF_VERSION="${1:-3.12.0}"
OHIF_DIR="src/ohif"
TEMP_DIR=$(mktemp -d)

cleanup() {
    rm -rf "${TEMP_DIR}"
}
trap cleanup EXIT

echo "==> Downloading OHIF Viewer v${OHIF_VERSION} from npm..."
curl -fsSL "https://registry.npmjs.org/@ohif/app/-/app-${OHIF_VERSION}.tgz" \
    | tar xz -C "${TEMP_DIR}"

if [ ! -d "${TEMP_DIR}/package/dist" ]; then
    echo "Error: dist/ directory not found in @ohif/app package"
    exit 1
fi

echo "==> Installing to ${OHIF_DIR}..."
# Preserve our app-config.js
CONFIG_BACKUP=""
if [ -f "${OHIF_DIR}/app-config.js" ]; then
    CONFIG_BACKUP="${TEMP_DIR}/app-config.js.bak"
    cp "${OHIF_DIR}/app-config.js" "${CONFIG_BACKUP}"
fi

# Clean old files (except app-config.js and CLAUDE.md)
find "${OHIF_DIR}" -mindepth 1 \
    -not -name "app-config.js" \
    -not -name "CLAUDE.md" \
    -delete 2>/dev/null || true

# Copy built files
cp -r "${TEMP_DIR}/package/dist/"* "${OHIF_DIR}/"

# Restore our custom config (overwrite the npm default)
if [ -n "${CONFIG_BACKUP}" ]; then
    cp "${CONFIG_BACKUP}" "${OHIF_DIR}/app-config.js"
fi

echo "==> Rewriting asset paths for /ohif/ base path..."

# --- index.html: PUBLIC_URL + root-relative href/src ---
python3 -c "
import re

with open('${OHIF_DIR}/index.html', 'r') as f:
    html = f.read()

# Set PUBLIC_URL to /ohif/
html = html.replace(\"window.PUBLIC_URL = '/';\", \"window.PUBLIC_URL = '/ohif/';\")

# Rewrite root-relative href, src, and content attributes to /ohif/ prefix
html = re.sub(r'href=\"/(?!ohif/)(?!/)', 'href=\"/ohif/', html)
html = re.sub(r'src=\"/(?!ohif/)(?!/)', 'src=\"/ohif/', html)
html = re.sub(r'content=\"/(?!ohif/)(?!/)', 'content=\"/ohif/', html)

with open('${OHIF_DIR}/index.html', 'w') as f:
    f.write(html)
"

# --- JS bundle: webpack public path ---
echo "    Patching webpack public path in all JS bundles..."
for f in "${OHIF_DIR}"/*.bundle.*.js; do
    sed -i 's|__webpack_require__\.p = "/"|__webpack_require__.p = "/ohif/"|g' "$f"
done

# --- CSS bundle: root-relative url() references (fonts, etc.) ---
echo "    Patching CSS asset URLs..."
sed -i 's|url(/\([^o)]\)|url(/ohif/\1|g' "${OHIF_DIR}/app.bundle.css"

# --- manifest.json: icon paths ---
echo "    Patching manifest.json icon paths..."
sed -i 's|"/assets/|"/ohif/assets/|g' "${OHIF_DIR}/manifest.json"

echo "==> OHIF Viewer v${OHIF_VERSION} installed to ${OHIF_DIR}"
echo "    Config: ${OHIF_DIR}/app-config.js"
echo "    Start Clarinet and visit /ohif to use the viewer."
