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

# Por defecto: el hola mundo sobre el LocalRuntime (no necesita API key ni server).
CMD ["uv", "run", "python", "hello_world.py"]
