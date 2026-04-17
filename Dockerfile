FROM python:3.11-slim AS builder

WORKDIR /app
COPY pyproject.toml ./
COPY lib/trading_platform /tmp/trading_platform

# Install runtime deps only (the project itself is copied in, not pip-installed).
# This keeps the base layer cacheable across code-only changes.
RUN pip install --no-cache-dir \
    "requests>=2.31" \
    "python-dotenv>=1.0" \
    "psycopg2-binary>=2.9" \
    "base58>=2.1" \
    "fastapi>=0.109" \
    "uvicorn[standard]>=0.27" \
    "solders>=0.21" \
    /tmp/trading_platform

# -----------------------------------------------------------
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages \
                    /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Application code
COPY src/ src/
COPY lib/ lib/
COPY config/ config/
COPY scripts/ scripts/
COPY .env.example .env.example

ENV PYTHONPATH=/app/src:/app/lib/trading_platform/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"

# Solana scanner with dashboard on port 8000.
# Config path is env-overridable so production can point at prod_scan.json.
CMD ["sh", "-c", "python -m run_event_driven \
    --config ${BOT_CONFIG:-config/example_config.json} \
    --mode ${BOT_MODE:-jupiter} \
    --port 8000"]
