FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Configuración para GUI sin display (headless)
    QT_QPA_PLATFORM=offscreen \
    # Timezone
    TZ=UTC

# Sistemas necesarios para PyQt6 y xvfb (opcional)
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    libxcb-xinerama0 \
    libxcb-cursor0 \
    && rm -rf /var/lib/apt/lists/*

# Crear usuario
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

# Instalar PyQt6 para la GUI
RUN pip install PyQt6

COPY app ./app
USER app
VOLUME /data
EXPOSE 8000

# Comando de inicio: ejecuta ambos (server + GUI)
CMD ["python", "-m", "app.main"]
