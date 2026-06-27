# syntax=docker/dockerfile:1.7

# [LEARN] Multi-stage build keeps final image small.
# Pattern: "Builder pattern for Docker images" — compile/install deps in a
# heavy stage, copy only the runtime artifacts to a slim final stage.
# Read more: https://docs.docker.com/build/building/multi-stage/

# --- Stage 1: builder ---
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /install

# System deps required to build wheels (psycopg, bcrypt, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --prefix=/install/deps -r requirements.txt

# --- Stage 2: runtime ---
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install/deps /usr/local

# Copy application source
COPY ./app ./app

# Copy Alembic migration config and scripts
COPY alembic.ini .
COPY ./alembic ./alembic

# Non-root user (defense-in-depth — never run prod containers as root)
RUN useradd --create-home --shell /bin/bash appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# Default command — overridden per service in docker-compose
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
