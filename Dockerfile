# Marketer Agent — Docker image
# Single-stage. Python 3.11 slim. Async I/O handles intra-process concurrency;
# horizontal scaling happens at the orchestrator level (ECS desired count, k8s replicas).

FROM python:3.11-slim

# System deps (minimal). libpq-dev needed if we switch to psycopg2; asyncpg
# ships its own binary. Keeping slim until DATABASE_URL is wired.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd --system marketer \
    && useradd --system --gid marketer --create-home --home-dir /home/marketer marketer

WORKDIR /app

# Python deps first (cache-friendly layer)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Code
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini

# Env defaults (override at run time)
ENV PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO \
    GEMINI_MODEL=gemini-3-flash-preview \
    LLM_TIMEOUT_SECONDS=60 \
    LLM_MAX_OUTPUT_TOKENS=8192 \
    PROMPT_TEXT_TRUNCATION_CHARS=600 \
    CALLBACK_HTTP_TIMEOUT_SECONDS=30 \
    CALLBACK_RETRY_ATTEMPTS=2 \
    EXTRAS_LIST_TRUNCATION=10

USER marketer

EXPOSE 8080

# Health check hits /ready so readiness gates on GEMINI_API_KEY + DB (when wired)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/ready || exit 1

CMD ["uvicorn", "marketer.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
