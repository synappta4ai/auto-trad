# AutoTrader — integración con uv

Resumen de cómo está montado uv en el proyecto y por qué.

## Archivos

| Archivo | Rol |
| --- | --- |
| `pyproject.toml` | metadatos, dependencias, `requires-python = ">=3.9,<3.13"`, entrypoint `autotrader = "app.main:main"` |
| `uv.lock` | versiones exactas resueltas (reproducibilidad) |
| `.venv/` | entorno gestionado por uv (ignorado por git) |
| `install-with-uv.ps1` | instalación y verificación en Windows |
| `requirements.txt` | compatibilidad con pip y con la imagen Docker |
| `requirements-gui.txt` | PyQt6 opcional para `UI=desktop` |

## Flujo

```powershell
uv sync                     # crea/actualiza .venv desde pyproject + uv.lock
uv run python -m app.main   # ejecuta sin activar el entorno
uv add <paquete>            # nueva dependencia (actualiza pyproject y uv.lock)
```

`uv run` garantiza el intérprete del `.venv`; en esta máquina el `python` del
sistema es 2.7 y no sirve para el proyecto.

## Decisiones

- **Un solo lockfile**: uv resuelve y fija versiones; no se usan constraints
  manuales.
- **Sin `[tool.uv] install-dir`**: provocaba un `TOML parse error` con la
  versión actual de uv y no hacía falta (uv usa `.venv` por defecto).
- **PyQt6 fuera de las dependencias base**: la GUI de escritorio es opcional y
  el panel web (`/ui`) no la necesita.
- **Docker con pip**: build más simple y sin depender de uv dentro de la imagen.

## Verificación

```powershell
uv run python -c "from app.config import Config; print(Config().public())"
uv run python -m app.main
curl http://127.0.0.1:8080/health
```
