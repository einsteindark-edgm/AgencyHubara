# GraphAgents

Agentes de **análisis de datos de Meta Ads**. Arquitectura **propia y separada**
del monorepo AgencyHubara: se apoya en sus *conceptos* (manifest-driven, SDK +
kits, TCK + certificación, archetypes, gates, TDD) **sin importar su código**. Se
desarrolla con el plugin de harness `graphagents-dev` (skill
`graphagents-developer`, hooks de TDD/cert/arquitectura, comando
`/graphagents-gates`).

## La idea en una página

- **Runtime durable:** AgentSpan/Conductor — retry por task, replay, estado
  durable, HUMAN tasks, `execution-id` cross-process. No lo construimos: lo usamos.
- **El "task graph":** el DAG de Conductor en que TODO compila.
- **Dos superficies de autoría → el mismo task graph:**
  1. **YAML declarativo** (`manifests/*.yaml`) — agentes/subagentes + `strategy`
     (`handoff|router|parallel|swarm|…`). Es el `plugin.yaml` de este mundo;
     AgentSpan lo trae nativo (`cli/examples/multi-agent.yaml`) y nosotros lo
     extendemos.
  2. **LangGraph `StateGraph`** (`graphs/*.py`) — la capability determinista de
     un agente. AgentSpan la compila a tasks tipadas (preserva nodos/edges/
     reducers/retries/HUMAN tasks).
- **Determinismo (G-DET):** el esqueleto del grafo es PURO; el LLM/IO va en nodos
  marcados; el rojo de TDD es un **golden-replay** (fixture → output exacto).
- **Manifest superset:** YAML nativo de AgentSpan + nuestras llaves de gobernanza
  (`archetype`, `capability`, `consumes`, `certification`, `approval_required`).
  El `loader` valida, certifica y despacha cada nodo a la API correcta de
  AgentSpan; las llaves ext se quitan antes de `agentspan deploy`.

## Layout

```
GraphAgents/
├── manifests/            # la espina declarativa (taskgraph/agent .yaml)
├── sdk/                  # SDK propio (NO importa el monorepo)
│   ├── manifest_model.py # el modelo Pydantic del manifest (superset de AgentSpan)
│   ├── loader.py         # manifest → grafo de Agent de AgentSpan (G1)
│   ├── cli.py            # `python -m sdk.cli check|certify|graph` (compilador rápido)
│   ├── graph.py          # serializa el sistema a {nodes,edges} (la fuente del explorer)
│   ├── testkit/checks.py # los checks G-* (única fuente; 3 frontends)
│   └── connectorkit/     # ports a Meta (vendor: live|fixture|warehouse)
├── graphs/               # capabilities LangGraph (StateGraphs deterministas)
├── tools/                # @tool (idempotentes; approval_required para outward)
├── viewer/               # el EXPLORER visual (backend stdlib http.server + visor Cytoscape)
├── tests/{architecture,conformance,graphs,integration}/  # G-rules · TCK · golden · runtime+backend
└── fixtures/             # datasets golden (snapshots de insights de Meta)
```

## Cómo se desarrolla

TDD obligatorio (rojo → verde → refactor): el rojo de una capability es un
golden-replay. Reglas duras `G-*` y método: las `references/` del skill
`graphagents-developer`. Verificación: `/graphagents-gates [arch|cert|graphs|manifests|all]`.

## Levantar con Docker (hola mundo)

```bash
cd GraphAgents
cp .env.example .env                  # opcional — el hola mundo no necesita keys

# 1) El hola mundo, sin nada más (corre sobre el LocalRuntime in-process):
docker compose up --build graphagents
#    → lista el catálogo de tools/agentes y corre `greeter` (usa la tool `hello`)
#      + demo de recovery por execution-id. No requiere API key ni el server.

# 2) El runtime durable REAL (server AgentSpan + postgres):
docker compose up -d agentspan        # :6767 — imagen oficial agentspan/server
docker compose run --rm graphagents uv run python -m sdk.cli list-tools
docker compose run --rm graphagents uv run python -m sdk.cli run greeter --input '{"name":"mundo"}'
```

El contenedor `graphagents` monta el código (bind-mount; el venv vive en
`/opt/venv`), así que editás y re-corrés sin rebuild. El hola mundo es la
**plantilla mínima**: copiá `tools/hello/` para una tool nueva, y
`manifests/greeter.agent.yaml` + `graphs/greeter.py` para un agente nuevo.

## El explorer visual (catálogo + grafo + marketplace)

Una interfaz estilo n8n para VER el sistema: el palette de tools y agentes (con su
nivel de certificación), el grafo de conexiones (`uses` / `agent://` / `consumes`)
y un inspector por nodo. Es una **proyección read-only** del catálogo — lee los
manifests, no los muta. El serializador `sdk/graph.py` es la única fuente; la
alimenta a tres frontends:

```bash
cd GraphAgents
python3 -m sdk.cli graph                 # el grafo en mermaid (se ve directo en GitHub)
python3 -m sdk.cli graph --format json   # el mismo grafo como JSON (lo come cualquier UI)

# el explorer VIVO (backend stdlib + visor Cytoscape, cero build):
docker compose up viewer                 # → http://localhost:8900
#   o, sin Docker:  python3 -m viewer.server
```

Desde la UI podés correr un agente tool-only (ej. `greeter`) por el LocalRuntime y
ver el resultado. El backend (`viewer/server.py`) es stdlib `http.server` (cero
deps nuevas): `GET /api/graph`, `POST /api/run`.

## Levantar (G0)

```bash
cd GraphAgents
uv sync                         # instala pydantic, pyyaml, langgraph, langchain, agentspan + pytest
uv run python -m sdk.cli check  # valida los manifests
uv run pytest -q                # arquitectura + conformance + golden (los golden reales se implementan en G0)
agentspan server start          # runtime durable en :6767 (la primera vez baja el server ~50MB)
```

## El borde con el monorepo (fase B)

GraphAgents NO importa `hubara_agency`/`frontend_dashboard` ni al revés. La
integración es un paso posterior y explícito: un cast del plugin `ads` del
monorepo llama al runtime de AgentSpan por `execution-id` (HTTP) y sirve los
resultados bajo `/api/ads/*`. Único punto de contacto, cero fusión de código.
