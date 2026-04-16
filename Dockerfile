FROM python:3.11-slim AS builder

WORKDIR /app
COPY pyproject.toml .
# Install only the dependencies (not the project itself) to cache this layer
COPY lib/trading_platform /tmp/trading_platform
RUN pip install --no-cache-dir \
    "web3>=6.0" \
    "requests>=2.31" \
    "python-dotenv>=1.0" \
    "fastapi>=0.109" \
    "uvicorn[standard]>=0.27" \
    "psycopg2-binary>=2.9" \
    /tmp/trading_platform

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
    CMD python -c "import urllib.request,base64,os; u=os.environ.get('DASHBOARD_USER','admin'); p=os.environ.get('DASHBOARD_PASS','admin'); r=urllib.request.Request('http://localhost:8000/health'); r.add_header('Authorization','Basic '+base64.b64encode(f'{u}:{p}'.encode()).decode()); urllib.request.urlopen(r)"

# Default: on-chain event-driven scanner with dashboard.
# Uses per-DEX RPC quotes for same-chain arbitrage detection.
CMD ["python", "-m", "run_event_driven", \
     "--config", "config/multichain_onchain_config.json", \
     "--port", "8000"]
