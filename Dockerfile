FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

# Configuraciones óptimas de despliegue para Python y UV
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 1. Copiamos ESQUELETOS (Workspace config) para aprovechar la caché agresiva de Docker
COPY pyproject.toml uv.lock ./
COPY exoclaw-temporal/pyproject.toml ./exoclaw-temporal/
COPY hubara_agency/pyproject.toml ./hubara_agency/

# 2. Descargamos las librerías base sin código vivo
RUN uv sync --frozen --no-install-project --no-dev

# 3. Copiamos y montamos la sangre viva del framework y la agencia
COPY exoclaw-temporal/ ./exoclaw-temporal/
COPY hubara_agency/ ./hubara_agency/

# 4. Instalamos el compilado final del proyecto propio
RUN uv sync --frozen --no-dev

# Insertamos el ambiente virtual al Path del sistema
ENV PATH="/app/.venv/bin:$PATH"

# El comando por defecto (Útil por si corres la imagen manual), 
# pero Kubernetes lo sobreescribirá dependiendo del deployment (API o Worker)
CMD ["python", "hubara_agency/run_api.py"]
