# FlameScout API (FastAPI + agents). Built by docker-compose.yml; see docs/deploy-vultr.md.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/packages/schemas

WORKDIR /app

# Dependencies first so code edits don't reinstall them (pyproject installs deps only, packages = []).
COPY pyproject.toml ./
RUN pip install .

COPY packages ./packages
COPY services ./services
COPY config ./config
COPY scripts ./scripts
COPY data/fixtures ./data/fixtures

# Runtime state lives on mounted volumes: workspace/, data/cache/, data/costar/ (licensed, read-only).
RUN mkdir -p workspace data/cache data/costar

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
