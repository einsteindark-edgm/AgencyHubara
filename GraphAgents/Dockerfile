# Imagen de la app GraphAgents (SDK + CLI + capabilities + hola mundo).
# Habla con el server AgentSpan (servicio aparte del compose) por AGENTSPAN_SERVER_URL.
FROM python:3.11-slim

# uv para deps reproducibles.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# git + gh: el botón 'publicar ▸' del explorer despliega el wiring bendecido
# (commit quirúrgico → push → PR). El .git NO vive en la imagen: entra por el
# bind-mount de la RAÍZ del monorepo (ver docker-compose.yml) y el token por
# GH_TOKEN (docker-entrypoint.sh registra a gh como credential helper de git).
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

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

# El entrypoint vive en / (fuera de /app y /workspace: los bind-mounts no lo tapan).
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8900
# Por defecto SIRVE el explorer visual (:8900) — la cara persistente de la app en la suite.
# El hola mundo y la CLI son on-demand (no levantan server):
#   docker compose run --rm graphagents uv run python hello_world.py
#   docker compose run --rm graphagents uv run python -m sdk.cli list-tools
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uv", "run", "python", "-m", "viewer.server"]
