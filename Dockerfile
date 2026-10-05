# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# curl is used by the container HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

# Chromium plus its OS dependencies, installed into the image.
RUN python -m playwright install --with-deps chromium

COPY app ./app

# Run as a non-root user; Chromium needs a writable home directory.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data /app/artifacts \
    && chown -R appuser:appuser /app /ms-playwright
USER appuser

ENV HOST=0.0.0.0 \
    PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST} --port ${PORT}"]
