#!/usr/bin/env powershell
# install-with-uv.ps1 - AutoTrader installation using uv
param(
    [switch]$UseLockfile,
    [switch]$SystemPython
)

Write-Host "=== AutoTrader Installation with uv ===" -ForegroundColor Cyan

# Check if uv is available
$uvCheck = & "C:\\Users\\Drako\\AppData\\Local\\Programs\\Python\\Python311\\python.exe" -c "import uv; print('uv available')" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing uv..." -ForegroundColor Yellow
    # Download and install uv
    $url = "https://astral.sh/uv/install-sh"
    $installScript = "$env:TEMP\\install-uv.ps1"
    Invoke-WebRequest -Uri $url -OutFile $installScript
    & $installScript --without-self-update
}

# Create virtual environment
Write-Host "Creating virtual environment..." -ForegroundColor Yellow
& "C:\\Users\\Drako\\AppData\\Local\\Programs\\Python\\Python311\\python.exe" -m venv .venv

# Activate
. .venv\Scripts\Activate.ps1

# Install with uv
Write-Host "Installing dependencies with uv..." -ForegroundColor Yellow
if ($UseLockfile) {
    uv sync
} else {
    uv pip install -r requirements.txt
}

# Verify installation
Write-Host "Verifying..." -ForegroundColor Yellow
python -c "from app.config import Config; c = Config(); print('Config OK:', c.mode)"

Write-Host "=== Installation Complete ===" -ForegroundColor Green
Write-Host "Run: uv run python -m app.main" -ForegroundColor White