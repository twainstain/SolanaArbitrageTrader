FROM python:3.11-slim AS builder

WORKDIR /app
COPY pyproject.toml .
# Install only the dependencies (not the project itself) to cache this layer
RUN pip install --no-cache-dir \
    "web3>=6.0" \
    "requests>=2.31" \
    "python-dotenv>=1.0" \
    "fastapi>=0.109" \
    "uvicorn[standard]>=0.27" \
    "psycopg2-binary>=2.9"

# -----------------------------------------------------------
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages \
                    /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY src/ src/
COPY config/ config/
COPY .env.example .env.example

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Default: live scan with dashboard, dry-run (safe).
CMD ["python", "-m", "run_live_with_dashboard", \
     "--config", "config/live_config.json", \
     "--iterations", "999999", \
     "--sleep", "30"]
