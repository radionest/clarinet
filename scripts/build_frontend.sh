#!/bin/bash
set -e

echo "Building Clarinet frontend..."

# Переход в директорию фронтенда
cd clarinet/frontend

# Очистка старых артефактов сборки
rm -rf build/

# Загрузка зависимостей
gleam deps download

# Компиляция Gleam в JavaScript
gleam build --target javascript

# Очистка и создание директории static внутри пакета
STATIC_DIR="../../clarinet/static"
rm -rf "$STATIC_DIR"
mkdir -p "$STATIC_DIR"/{js,css,assets}

# Копирование собранного JavaScript модуля
cp -r build/dev/javascript/* "$STATIC_DIR/js/"

# Удаление кэша компиляции Gleam (не нужен в рантайме)
find "$STATIC_DIR/js" -type d -name "_gleam_artefacts" -exec rm -rf {} + 2>/dev/null || true

# Копирование всех статических файлов из public
if [ -d "public" ]; then
    cp -r public/* "$STATIC_DIR/"
fi

echo "Frontend build complete! Output in clarinet/static/"
