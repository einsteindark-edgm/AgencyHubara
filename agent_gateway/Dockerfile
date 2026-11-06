# Hubara Agent Gateway — MCP server de solo lectura sobre /api/ads/*.
# Imagen mínima: deps resueltas con uv en el builder; el runtime solo lleva el venv + código.
# Sin secretos: toda la config llega por variables de entorno (ver .env.example).

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim-bookworm
RUN groupadd --system --gid 10001 gateway \
    && useradd --system --uid 10001 --gid gateway --no-create-home --shell /usr/sbin/nologin gateway
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY hubara_gateway ./hubara_gateway
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 GATEWAY_PORT=8765
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"GATEWAY_PORT\",\"8765\")}/healthz', timeout=2)"]
ENTRYPOINT ["python", "-m", "hubara_gateway"]
