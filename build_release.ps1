Write-Host "Building V-UI Linux Binary..." -ForegroundColor Green

# Check if Docker is running
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker is not running. Please start Docker Desktop first."
    exit 1
}

# Build Builder Image
Write-Host "Step 1: Creating Build Environment..."
docker build -t v-ui-builder -f Dockerfile.build .
if ($LASTEXITCODE -ne 0) { 
    Write-Error "Build failed at Step 1"
    exit 1 
}

# Run Build
Write-Host "Step 2: Compiling Binary..."
# Mount current directory to /app
docker run --rm -v "${PWD}:/app" v-ui-builder
if ($LASTEXITCODE -ne 0) { 
    Write-Error "Build failed at Step 2"
    exit 1 
}

Write-Host "Done! Binary is in dist/v-ui" -ForegroundColor Green
