#!/bin/bash
set -e

echo "Building Clarinet frontend..."

# Переход в директорию фронтенда
cd clarinet/frontend

# Очистка старых артефактов сборки
rm -rf build/

STATIC_DIR="../../clarinet/static"
rm -rf "$STATIC_DIR"
mkdir -p "$STATIC_DIR"/{css,assets}

# Загрузка зависимостей и сборка бандла (lustre_dev_tools + bun)
gleam deps download
gleam run -m lustre/dev build --minify --outdir="$STATIC_DIR"

# Копирование HTML/CSS из public/ (перезаписывает сгенерированный index.html)
if [ -d "public" ]; then
    cp -r public/* "$STATIC_DIR/"
fi

echo "Frontend build complete! Output in clarinet/static/"
echo "Bundle: $(du -h "$STATIC_DIR/clarinet_frontend.js" | cut -f1)"
