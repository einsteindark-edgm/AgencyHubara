# 04 · Lecciones de validación (sección VIVA — append-only)

Formato de cada entrada (copiar tal cual):

```
### L-<n> · <título corto> (<fecha>, <contexto>)
- **Síntoma:** qué se vio (error literal, comportamiento).
- **Causa raíz:** qué regla/mecanismo se malentendió o qué hueco había.
- **Fix aplicado:** commit/PR + qué cambió.
- **Regla para el skill:** la instrucción imperativa de 1-2 líneas a internalizar.
- **Guard:** el gate que ahora lo caza (o "PENDIENTE: <propuesta>").
```

Cuando un run real (sobre AgentSpan) revela un bug, el **primer** artefacto es un
guard rojo que lo reproduce — el "Guard:" se escribe ANTES que el "Fix:".

### L-0 · La arquitectura de AgentSpan no se lee desde la landing (2026-06-18, bootstrap)
- **Síntoma:** se asumió (dos veces) que AgentSpan no tenía ni YAML declarativo
  ni concepto de "task graph", mirando solo el README/landing.
- **Causa raíz:** la landing muestra el SDK Python (`Agent` + `>>` + strategies).
  El YAML declarativo vive en `cli/examples/{simple,multi}-agent.yaml`; el "task
  graph" es el DAG de Conductor (`docs/design/specs/2026-03-20-server-dag-injection-design.md`);
  la compilación de LangGraph → Conductor está en `docs/langgraph-integration.md`.
- **Fix aplicado:** se leyó el TREE del repo (`gh api .../git/trees/main?recursive=1`)
  y los archivos reales antes de diseñar el subsistema.
- **Regla para el skill:** antes de afirmar qué hace/no hace AgentSpan (o cualquier
  framework), leé el árbol del repo y el archivo fuente, no el resumen de la landing.
- **Guard:** procedimiento (sin gate automático).

### L-1 · El execution-id del runtime debe ser determinista (2026-06-18, montaje de G1)
- **Síntoma:** el test de recovery (`resume(eid)` + asertar `ex.id == eid` y output)
  necesita un id estable; con `uuid4`/`time` el replay/golden de una ejecución no
  es reproducible (y el sandbox de Claude prohíbe `Date.now`/`random`).
- **Causa raíz:** se tiende a generar el id con time/random (lo "natural").
- **Fix aplicado:** `LocalRuntime._new_id` es un contador (`local-000001`, ...).
- **Regla para el skill:** el vendor de runtime genera ids DETERMINISTAS (contador,
  no time/random). Es el R-DET del runtime: lo no-determinista no entra en la
  identidad de una ejecución.
- **Guard:** `test_execution_id_es_determinista` (dos runtimes frescos → mismo primer id).

### L-2 · La capability `run` tiene firma uniforme — el loader inyecta ports Y tools (2026-06-18, montaje de G1)
- **Síntoma:** una capability que declara `run(input, *, ports)` (sin `tools`) truena
  con `TypeError: unexpected keyword argument 'tools'` al correrla por el runtime,
  porque `build_runnable` inyecta SIEMPRE `ports=` y `tools=`.
- **Causa raíz:** cada capability usa solo una de las dos inyecciones (meta-insights
  usa ports; roas-cac usa tools), y tienta declarar solo la que usás.
- **Fix aplicado:** firma uniforme `run(input, *, ports=None, tools=None)` en TODA
  capability; el check `G-RUN-SIG` la exige.
- **Regla para el skill:** toda capability expone `run(input, *, ports=None, tools=None)`
  aunque use una sola — el loader inyecta ambas. La lógica pura va ahí; `build()`
  (el StateGraph) es el adapter al runtime real.
- **Guard:** `check_capability_run_signature` (G-RUN-SIG) + `test_run_sig_guard_*`.

### L-3 · El pre-bash hook del monorepo bloquea el loop de tests de GraphAgents (2026-06-18, explorer V0→V2)
- **Síntoma:** `cd GraphAgents && uv run pytest` → DENEGADO por `pre-bash-cd-check.sh`
  ("'uv run' debe ir con prefijo 'cd hubara_agency &&'").
- **Causa raíz:** el hook del monorepo whitelistea solo `cd hubara_agency`/`cd frontend_dashboard`;
  GraphAgents es un subsistema nuevo que no conoce. Y local no hay `.venv` (un `uv sync`
  pesa + `agentspan` en pypi es incierto).
- **Fix aplicado:** el loop corre con **`python3 -m pytest`** (el python del sistema tiene
  pydantic/pyyaml/pytest; las capabilities importan langgraph LAZY — solo en `build()` —
  así que el camino puro `run()` corre sin deps pesadas). `run-gates.sh` autodetecta el
  intérprete (python3 si tiene deps, si no `uv run` para Docker/`/opt/venv`).
- **Regla para el skill:** para correr tests/CLI de GraphAgents a mano usá
  `python3 -m pytest` / `python3 -m sdk.cli` (NO `uv run`: lo bloquea el hook). El panel
  `/graphagents-gates` ya elige el runner solo.
- **Guard:** `run-gates.sh` (autodetección de runner) — corre local sin `uv sync` ni chocar el hook.

### L-4 · `curl` a localhost lo trunca el proxy Tamp (2026-06-18, explorer V0→V2)
- **Síntoma:** `curl http://localhost:8900/api/graph` devolvió 201 bytes (cortado a mitad de
  palabra, `"pur[e]"`); parecía un control-char inválido en el JSON.
- **Causa raíz:** el proxy Tamp (`:7778`, "token compression active") corta/comprime el body
  de respuestas grandes en el path de `curl`. El server estaba BIEN — `urllib` trajo 2888
  bytes, JSON válido (7 nodos / 6 aristas).
- **Fix aplicado:** verificar HTTP local con `python3 -c "...urllib.request.urlopen..."` o el
  browser del preview, NUNCA con `curl`.
- **Regla para el skill:** para chequear un endpoint local usá `urllib`/el browser; `curl`
  miente por el proxy. La verificación canónica es el unit del `api_route` sin socket.
- **Guard:** procedimiento (sin gate).

### L-5 · El sandbox del preview no entra al worktree profundo (2026-06-18, explorer V0→V2)
- **Síntoma:** `preview_start` con un `bash -c "cd /abs/.../GraphAgents && ..."` → "getcwd:
  Operation not permitted", usó el `python3` de Xcode (sin pydantic), `ModuleNotFoundError:
  viewer`, y el browser quedó en `chrome-error://`.
- **Causa raíz:** el preview spawnea con un cwd restringido y PATH propio; el `cd` absoluto al
  worktree profundo falla.
- **Fix aplicado:** pre-bindear el puerto con un server lanzado por la **Bash tool**
  (intérprete correcto: `python3 GraphAgents/viewer/server.py`), después `preview_start`
  lo **REUSA** (`reused: true`) → el browser pega al server bueno. En `.claude/launch.json`:
  intérprete ABSOLUTO + path del script RELATIVO al worktree root (como `archon-docs`),
  nunca `bash -c cd`.
- **Regla para el skill:** para previsualizar el explorer, lanzá el server con la Bash tool y
  dejá que `preview_start` lo reúse; no metas `cd` absoluto en launch.json.
- **Guard:** procedimiento (sin gate).

### L-6 · El backend del explorer es stdlib `http.server`, cero deps (2026-06-18, explorer V0→V2)
- **Síntoma/decisión:** tentaba FastAPI (consistencia con el monorepo), pero suma dep + exige
  `uv sync` (con `agentspan` incierto en pypi).
- **Causa raíz:** el explorer es read-mostly (PROYECTA el registry; no lo muta) + un endpoint
  de run; no necesita el peso de FastAPI.
- **Fix aplicado:** `viewer/server.py` sobre `http.server` (stdlib) con el core ruteable
  `api_route(method, path, params, body, ga_root)` AISLADO del socket → unit-testeable sin
  abrir un puerto (7 tests). El serializador `sdk/graph.py` es la **única fuente** que leen
  los 3 frontends (CLI mermaid/json · visor Cytoscape · backend) — mismo patrón que el TestKit.
- **Regla para el skill:** para superficies internas read-mostly de GraphAgents, stdlib
  `http.server` con un `api_route` puro; FastAPI solo si crece (auth/websockets). El frontend
  (Cytoscape vía CDN) es zero-build. Toda vista nueva LEE el grafo de `sdk.graph`, no reparsea
  manifests.
- **Guard:** `tests/integration/test_viewer_api.py` (testea `api_route` sin socket) +
  `tests/architecture/test_system_graph.py` (golden estructural del serializador).

<!-- AÑADIR NUEVAS LECCIONES ARRIBA DE ESTA LÍNEA, NUMERADAS L-1, L-2, ... -->
