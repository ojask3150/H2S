# Multi-stage Dockerfile optimized for Render Free Tier deployment (Single Service)

# Stage 1: Build static Next.js frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# Stage 2: Production Python backend serving API & static frontend
FROM python:3.11-slim AS runner
WORKDIR /app

# Prevent python from writing pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY --from=frontend-builder /app/frontend/out /app/static

ENV PYTHONPATH=src \
    GEE_MODE=static \
    MET_SOURCES=static \
    REASONING_ENGINE=deterministic \
    LOG_LEVEL=INFO \
    STATIC_DIR=/app/static \
    PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "uvicorn aap.service:app --host 0.0.0.0 --port ${PORT:-8000}"]
