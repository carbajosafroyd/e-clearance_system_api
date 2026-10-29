# Production Multi-Stage Dockerfile for E-Clearance System API
FROM python:3.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install system dependencies required for mysqlclient, cryptography, and xhtml2pdf
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    default-libmysqlclient-dev \
    pkg-config \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Create non-privileged user and setup directories
RUN useradd -m -u 1000 eclearance && \
    mkdir -p /app/staticfiles && \
    chown -R eclearance:eclearance /app

USER eclearance

EXPOSE 8000 10000

# Health check using the live health endpoint (works for both local and Render)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health/live/ || exit 1

# Launch production WSGI application with Gunicorn on dynamic $PORT
CMD ["sh", "-c", "gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 3 --threads 2 --timeout 120"]
