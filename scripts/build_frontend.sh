#!/bin/bash
set -e

echo "Building Clarinet frontend..."

# Переход в директорию фронтенда
cd src/frontend

# Очистка старых артефактов сборки
rm -rf build/

# Загрузка зависимостей
gleam deps download

# Компиляция Gleam в JavaScript
gleam build --target javascript

# Очистка и создание директории dist
rm -rf ../../dist
mkdir -p ../../dist/{js,css,assets}

# Копирование собранного JavaScript модуля
cp -r build/dev/javascript/* ../../dist/js/

# Копирование всех статических файлов из public
if [ -d "public" ]; then
    cp -r public/* ../../dist/
fi

echo "Frontend build complete! Output in dist/"