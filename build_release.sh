#!/bin/bash
set -e

echo "Building V-UI Linux Binary..."

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed or not in PATH"
    exit 1
fi

# Build Builder Image
echo "Step 1: Creating Build Environment..."
docker build -t v-ui-builder -f Dockerfile.build .

# Run Build
echo "Step 2: Compiling Binary..."
docker run --rm -v "$(pwd):/app" v-ui-builder

echo "Done! Binary is in dist/v-ui"
