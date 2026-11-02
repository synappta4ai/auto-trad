# AutoTrader con uv

[uv](https://astral.sh/uv) es el gestor de paquetes/entornos usado en este
proyecto. Resuelve e instala mucho más rápido que pip y mantiene un lockfile
reproducible.

## Requisitos

- [uv](https://astral.sh/uv) instalado.
- Python 3.9–3.12 (uv lo descarga solo si no lo tienes; el proyecto fija
  `requires-python = ">=3.9,<3.13"`).

## Instalación

```powershell
.\install-with-uv.ps1      # crea .venv, instala deps y verifica
# o manualmente:
uv sync                    # lee pyproject.toml + uv.lock y crea/actualiza .venv
```

No hace falta activar el entorno: `uv run` lo usa automáticamente.

## Ejecutar

```bash
uv run python -m app.main
# panel web: http://127.0.0.1:8080/ui
```

En Windows el `python` del sistema puede ser 2.7; por eso todos los comandos
del proyecto usan `uv run python ...`.

## Dependencias

```bash
uv add <paquete>           # añade a pyproject.toml, actualiza uv.lock e instala
uv remove <paquete>
uv sync                    # aplica lo que haya en pyproject.toml/uv.lock
uv lock --upgrade          # actualiza versiones dentro de los rangos permitidos
```

- `requirements.txt` se mantiene por compatibilidad con `pip`/Docker.
- `pytest` vive en `[dependency-groups] dev`: `uv sync --dev` lo instala.

## Docker

La imagen usa `pip` a propósito (build simple y reproducible con
`requirements.txt`). uv es la vía recomendada para desarrollo local.

## Problemas típicos

| Síntoma | Solución |
| --- | --- |
| `uv: command not found` | instala uv y abre una consola nueva: `irm https://astral.sh/uv/install.ps1 \| iex` |
| Resolución falla por versión de Python | `uv python install 3.11` y `uv python pin 3.11` |
| Quieres reinstalar limpio | borra `.venv` y ejecuta `uv sync` |
| `TOML parse error` en `pyproject.toml` | revisa que no queden claves ajenas a `[tool.uv]` (p. ej. `install-dir`) |

## Referencias

- `pyproject.toml` — metadatos, dependencias y entrypoint `autotrader`.
- `uv.lock` — versiones exactas resueltas (se commitea).
- `install-with-uv.ps1` — instalación/verificación en Windows.
- `README.md` — uso general, panel web y operación.
