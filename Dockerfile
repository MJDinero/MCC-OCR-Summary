FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	PIP_NO_CACHE_DIR=1 \
	PORT=8080

WORKDIR /app

# System deps (build) layer
FROM base AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
	build-essential \
	&& rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --prefix=/install -r requirements.txt

# Final slim image
FROM base AS final
COPY --from=build /install /usr/local

# Create non-root user
RUN addgroup --system app && adduser --system --ingroup app app
USER app

COPY src/ /app/src/
ENV PYTHONPATH=/app/src

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD python -c "import urllib.request,sys;\n\nimport os;\nurl=f'http://127.0.0.1:{os.environ.get(\'PORT\',8080)}/healthz';\n\nurllib.request.urlopen(url) or sys.exit(1)" || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
