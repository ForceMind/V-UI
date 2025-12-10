#!/bin/bash
set -e

echo "Starting PyInstaller Build..."

# Ensure clean build
rm -rf build dist *.spec

# Run PyInstaller
# -F: One file
# -n: Name
# --add-data: Include web assets
pyinstaller -F main.py \
    -n v-ui \
    --add-data "web:web" \
    --hidden-import=uvicorn.logging \
    --hidden-import=uvicorn.loops \
    --hidden-import=uvicorn.loops.auto \
    --hidden-import=uvicorn.protocols \
    --hidden-import=uvicorn.protocols.http \
    --hidden-import=uvicorn.protocols.http.auto \
    --hidden-import=uvicorn.lifespan \
    --hidden-import=uvicorn.lifespan.on \
    --clean

echo "Build finished successfully!"
echo "Binary is located at: dist/v-ui"
