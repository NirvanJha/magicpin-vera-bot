FROM python:3.11-slim

# State is in-memory by default (brief: "must not persist context data after the test ends").
# Opt in to restart resilience with -e VERA_STATE_FILE=/data/vera_state.json; POST /v1/teardown wipes it.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /data && useradd --create-home vera && chown -R vera /app /data
USER vera

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/v1/healthz', timeout=4)"

# Exactly one worker: all state lives in this process.
CMD ["sh", "-c", "uvicorn bot:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
