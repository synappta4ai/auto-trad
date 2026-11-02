FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC \
    DATA_DIR=/data \
    API_BIND=0.0.0.0 \
    API_PORT=8080

RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
USER app
VOLUME /data
EXPOSE 8080

# Motor continuo + panel web en /
CMD ["python", "-m", "app.main"]
