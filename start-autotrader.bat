@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv no esta instalado.
    echo Instalalo con:  powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Instalando dependencias con uv...
    uv sync
    if errorlevel 1 (
        echo [ERROR] fallo la instalacion de dependencias.
        pause
        exit /b 1
    )
)

rem Puerto del panel (API_PORT de .env, por defecto 8080)
set "APIPORT=8080"
for /f "usebackq tokens=1,2 delims==" %%A in (`findstr /b /c:"API_PORT=" .env 2^>nul`) do set "APIPORT=%%B"

echo Iniciando AutoTrader en http://127.0.0.1:%APIPORT%/ui
start "AutoTrader" cmd /k "uv run python -m app.main"

rem Esperar a que el servidor levante y abrir el navegador
timeout /t 6 /nobreak >nul
start "" "http://127.0.0.1:%APIPORT%/ui"
endlocal
