FROM python:3.11-slim AS base

# commit: use python:3.11-slim base and minimize layers for faster builds

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	PIP_NO_CACHE_DIR=1 \
	PORT=8080

WORKDIR /app

# System deps (build) layer
FROM base AS build
# commit: install only wheel-friendly dependencies; no compiler toolchain needed
COPY requirements.txt ./
RUN pip install --upgrade pip && \
    pip install --prefix=/install -r requirements.txt && \
    find /install -type d -name "__pycache__" -prune -exec rm -rf {} +

# Final slim image
FROM base AS final
COPY --from=build /install /usr/local

# Install curl while still root (needed for HEALTHCHECK)
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Create non-root user and drop privileges
RUN addgroup --system app && adduser --system --ingroup app app
USER app

# commit: copy source after dependency layer for better cache utilisation
COPY src/ /app/src/
ENV PYTHONPATH=/app/src

EXPOSE 8080

# HEALTHCHECK uses curl installed above
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
