#!/usr/bin/env powershell
# start-autotrader.ps1 - arranca el motor y abre el panel web en el navegador.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] uv no esta instalado. Instalalo con:" -ForegroundColor Red
    Write-Host '  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"' -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path ".venv")) {
    Write-Host "Instalando dependencias con uv..." -ForegroundColor Yellow
    uv sync
}

$port = 8080
if (Test-Path ".env") {
    $line = Select-String -Path ".env" -Pattern '^\s*API_PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($line) { $port = [int]$line.Matches[0].Groups[1].Value }
}

$url = "http://127.0.0.1:$port/ui"
Write-Host "Iniciando AutoTrader en $url" -ForegroundColor Cyan

Start-Process -FilePath "uv" -ArgumentList @("run", "python", "-m", "app.main") `
    -WorkingDirectory $PSScriptRoot -WindowStyle Normal

Start-Sleep -Seconds 6
Start-Process $url
