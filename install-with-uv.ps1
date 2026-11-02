#!/usr/bin/env powershell
# install-with-uv.ps1 - AutoTrader installation using uv
param(
    [switch]$UseLockfile,
    [switch]$SystemPython,
    [switch]$WithDesktopGui
)

Write-Host "=== AutoTrader Installation with uv ===" -ForegroundColor Cyan

# 1. uv disponible?
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Instalando uv..." -ForegroundColor Yellow
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] uv sigue sin estar disponible. Abre una consola nueva y reintenta." -ForegroundColor Red
    exit 1
}

# 2. Entorno + dependencias (uv crea .venv y resuelve la version de Python)
Write-Host "Sincronizando dependencias..." -ForegroundColor Yellow
uv sync
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] fallo la sincronizacion de dependencias." -ForegroundColor Red
    exit 1
}

# 3. GUI de escritorio opcional
if ($WithDesktopGui) {
    Write-Host "Instalando PyQt6 (GUI de escritorio)..." -ForegroundColor Yellow
    uv pip install -r requirements-gui.txt
}

# 4. Verificacion
Write-Host "Verificando..." -ForegroundColor Yellow
uv run python -c "from app.config import Config; c = Config(); print('Config OK:', c.mode, c.symbols)"

Write-Host "=== Installation Complete ===" -ForegroundColor Green
Write-Host "Run: uv run python -m app.main   ->  http://127.0.0.1:8080/ui" -ForegroundColor White
