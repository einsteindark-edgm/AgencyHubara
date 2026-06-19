# Imagen de la app GraphAgents (SDK + CLI + capabilities + hola mundo).
# Habla con el server AgentSpan (servicio aparte del compose) por AGENTSPAN_SERVER_URL.
FROM python:3.11-slim

# uv para deps reproducibles.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# El venv vive FUERA de /app para que el bind-mount de dev no lo tape.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Deps primero (capa cacheada): solo se reinstala si cambia pyproject.
COPY pyproject.toml ./
RUN uv sync --no-install-project

# El código (layout plano: sdk/ graphs/ tools/ manifests/ ...).
COPY . .
RUN uv sync

EXPOSE 8900
# Por defecto SIRVE el explorer visual (:8900) — la cara persistente de la app en la suite.
# El hola mundo y la CLI son on-demand (no levantan server):
#   docker compose run --rm graphagents uv run python hello_world.py
#   docker compose run --rm graphagents uv run python -m sdk.cli list-tools
CMD ["uv", "run", "python", "-m", "viewer.server"]
