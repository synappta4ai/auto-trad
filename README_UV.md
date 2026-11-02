# AutoTrader with uv

## What is uv?

[uv](https://astral.sh/uv) is a fast Python package installer and resolver written in Rust, by the creators of [Poetry](https://python-poetry.org/) and [Ruff](https://beta.ruff.lol/). It's designed to replace `pip` and `pip-tools` for faster, more reliable installs.

**Key benefits for this project:**
- 10-100x faster than pip
- deterministic installs with lockfile
- zero-step setup (no `requirements.txt` maintenance hassle)
- compatible with existing `requirements.txt`

## Installation with uv

### Option 1: Using the existing `requirements.txt` (recommended for compatibility)

```bash
# Create virtual environment with uv
uv venv              # creates .venv/ with Python 3.7+ (default)
uv sync              # installs from requirements.txt + pyproject.toml

# Or manually:
uv pip install -r requirements.txt
```

### Option 2: Full uv workflow

```bash
# 1. Create venv
uv venv

# 2. Install dependencies (uv will also read requirements.txt)
uv pip install -r requirements.txt

# 3. Or install directly from pyproject.toml
uv pip install .

# 4. Run the app
uv run python -m app.main
```

## Development Workflow

### Adding a new dependency

```bash
# Using uv (updates both requirements.txt and uv.lock)
uv add new-package

# Or manually edit pyproject.toml + uv sync
uv sync
```

### Running scripts

```bash
uv run python -m app.main
uv run python -c "from app.config import Config; c = Config(); print(c.public())"
uv run pytest  # if you add tests
```

## Comparison: uv vs pip

| Feature | uv | pip |
|---------|-----|-----|
| Speed | ✅ Very fast (Rust-based) | ✗ Slower |
| Lockfile | ✅ `uv.lock` generated | ✗ Manual |
| Python reqs | ✅ `requires-python` in pyproject.toml | ✗ Manual |
| Cross-platform | ✅ Windows, macOS, Linux | ✅ All |
| Drop-in replacement | ✅ `uv pip install` | ✅ `pip install` |

## Docker with uv

The Dockerfile can be optimized to use uv for faster builds:

```dockerfile
# Before: RUN pip install -r requirements.txt
# After: Use uv in a temp container

FROM python:3.12-slim AS builder

# Install uv
COPY . /tmp/src
RUN pip install uv  # or use official uv image

# Copy only what's needed
WORKDIR /srv
COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

COPY app ./app
```

For this project, the existing Dockerfile works fine with `pip`. uv is mainly for local development.

## Updating Dependencies

```bash
# Update to latest compatible versions
uv pip sync

# Or force upgrade
uv pip install --upgrade -r requirements.txt
```

## Troubleshooting

### "Python 3.7+ required"
This project requires Python 3.7+. uv will use the system Python. Ensure:
```bash
python3 --version  # Should be 3.7+
# or
uv python pin 3.12  # Set a specific Python version
```

### Version conflicts
uv's resolver is stricter than pip's. If you encounter conflicts:
```bash
# Allow newer versions
uv pip install "package>=version"

# Or ignore specific constraints
uv pip install --extra --no-deps package
```

### Windows-specific
On Windows, ensure you're using PowerShell or cmd with proper activation:
```powershell
# Create venv
uv venv

# Activate
.venv\Scripts\Activate.ps1

# Install
uv pip install -r requirements.txt
```