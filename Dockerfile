# Build dependencies and local shared packages stay out of the runtime image.
FROM python:3.11-slim AS builder

ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY graph-attention-engine-v50 /src/graph-attention-engine-v50
COPY ci-platform /src/ci-platform
COPY copilot-sdk /src/copilot-sdk

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --no-deps /src/graph-attention-engine-v50 /src/ci-platform /src/copilot-sdk \
    && pip install --no-cache-dir \
        "fastapi>=0.100.0" "uvicorn[standard]>=0.23.0" "neo4j>=5.0.0" \
        "numpy>=1.24.0" "psycopg[binary]>=3.1.0" "httpx>=0.25.0" \
        "PyYAML>=6.0" "python3-saml>=1.16.0"

FROM python:3.11-slim AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app/backend:/opt/ci-platform:/opt/copilot-sdk" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv
COPY s2p-copilot/backend /app/backend
COPY s2p-copilot/data /app/data
COPY ci-platform/domain_config.py /opt/ci-platform/domain_config.py

WORKDIR /app/backend
RUN chown -R appuser:appuser /app /opt/venv /opt/ci-platform
USER appuser

EXPOSE 8002
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
    CMD curl --fail --silent http://localhost:8002/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
