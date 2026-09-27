# ---- Stage 1: build the React dashboard ----
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY dashboard/web/package.json dashboard/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/web/ ./
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY orchestrator ./orchestrator
COPY dashboard/__init__.py ./dashboard/__init__.py
COPY dashboard/api ./dashboard/api
COPY --from=web /web/dist ./dashboard/web/dist

RUN useradd --uid 1000 --create-home alfred && mkdir -p /data && chown alfred:alfred /data
USER alfred

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# No --proxy-headers: the client IP is derived in dashboard/api/security.py, trusting only TRUSTED_PROXY_IPS.
CMD ["uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-server-header"]
