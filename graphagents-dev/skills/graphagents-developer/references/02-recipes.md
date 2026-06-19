# 02 · Recetas (paso a paso, test-first)

Toda receta se ejecuta **test-first** (`00-tdd-law.md`): el primer archivo que
tocás es el test que falla por la razón correcta. Acá va QUÉ archivos tocar; el
bucle rojo→verde→refactor dice en qué orden.

## §0 · Empezá acá: levantar el stack + el explorer

```bash
cd GraphAgents
docker compose up --build               # toda la suite; la app SIRVE el explorer en :8900
#   solo el explorer:  docker compose up --build --no-deps graphagents
#   sin Docker:        python3 -m viewer.server      (→ http://localhost:8900)
#   el hola mundo:     docker compose run --rm graphagents uv run python hello_world.py
```

La **plantilla mínima** (patrón tool→agente→runtime, ya verde): copiá
`tools/hello/` para una tool nueva y `manifests/greeter.agent.yaml` +
`graphs/greeter.py` para un agente nuevo. Toda la estructura se construye a partir
de ahí, recetas abajo.

## 2.0 Crear una tool agnóstica de catálogo

La unidad reusable de primera clase. Test-first.

1. **Rojo:** `tests/tools/test_<id>.py` — golden de la `impl` pura (input → output
   exacto) + idempotencia. Velo fallar con assert real.
2. `tools/<id>/tool.yaml` — contrato: `id`, `version` (semver), `tags`,
   `side_effect` (pure/read/outward), `idempotent`, `approval_required` (true si
   outward, T-DUR), `credentials`, `inputs`/`outputs`, `impl: tools.<id>.impl:run`.
3. `tools/<id>/impl.py` — lógica PURA. **NO importes ningún runtime** (G-AGNOSTIC;
   el gate AST te frena).
4. `tools/<id>/adapters/{langgraph,agentspan}.py` — exponé la impl a cada runtime
   (ACÁ sí va el import del runtime). LangGraph = un callable `state→patch`;
   AgentSpan = `@tool` (import del runtime DENTRO de la función).
5. Verde: `uv run python -m sdk.cli certify-tool <id>` (C2) + `uv run pytest tests/tools/test_<id>.py -q`.
6. Aparece en el palette: `uv run python -m sdk.cli list-tools` / `search <tag>`.
7. **Bindearla a un agente:** en su manifest, `tools: [{uses: <id>@1, with: {input: $state.x}}]`
   (G-BIND verifica que existe en el catálogo).

## 2.1 Agregar una capability (un `StateGraph` determinista)

1. **Rojo:** `tests/graphs/test_<x>_golden.py` — fixture en `fixtures/<x>.json`,
   construí el grafo, asertá el output exacto. Velo fallar con assert real.
2. `graphs/<x>.py` — `def build(): -> CompiledStateGraph`. Esqueleto PURO
   (extract → transform → analyze). El nodo LLM, si hay, marcado y con
   `temperature=0` + structured output (G-DET).
3. Estado: `State` Pydantic con reducers declarados (G-STATE).
4. Datos externos: SOLO vía un port de `consumes:` (G-PORT) — nunca red cruda.
5. Verde: `uv run pytest tests/graphs/test_<x>_golden.py -q`.
6. Declarala en un manifest (2.3) y certificá (2.6).

## 2.2 Agregar una tool (con aprobación si es outward)

1. **Rojo:** `tests/.../test_<tool>.py` — asertá el **decision payload** / efecto,
   no la implementación.
2. `tools/<tool>.py` — `@tool`. Si toca algo outward (gasto, cambio en Meta):
   `approval_required=True` (G-DUR) + **idempotencia** (fingerprint + pre-check,
   no solo una marca).
3. Verde + declarala en el manifest del agente (`tools: [{name, type, approval_required}]`).

## 2.3 Agregar / editar un manifest (`taskgraph.yaml` o `agent.yaml`)

1. **Rojo (si agregás un campo):** el caso negativo en `tests/architecture/` —
   fabricá el manifest roto y probá que el check lo caza.
2. Editá `manifests/<id>.yaml`. Campos nativos de AgentSpan (`name`, `model`,
   `instructions`, `strategy`, `agents`, `tools`) + ext nuestros (`archetype`,
   `capability`, `consumes`, `certification`, `approval_required`).
3. **Regla de oro** si el campo es nuevo: `manifest_model` (lo modela) +
   `loader` (lo consume) + `testkit/checks.py` (lo chequea), mismo cambio.
4. `uv run python -m sdk.cli check` → verde.

## 2.4 Agregar un connector a Meta (ConnectorKit)

1. **Rojo:** unit del port con sus 4 paths (éxito · error del vendor · timeout ·
   no-disponible).
2. `sdk/connectorkit/ports.py` — definí el `Port` (protocolo) + el vendor
   (`live` Graph/Marketing API · `fixture` para tests/golden · `warehouse`).
3. Dimensioná el timeout por la cadena real de Meta (no por el hop local).
4. El grafo lo usa por `consumes: [<port>]` — nunca instancia el vendor a mano.

## 2.5 Componer el supervisor (la orquestación en YAML)

1. `manifests/<team>.taskgraph.yaml` con `archetype: supervisor`, `strategy:`
   (handoff/router/parallel/...) y `agents: [<ids o inline>]`.
2. El `loader` mapea `strategy` → estrategia de AgentSpan y `agents` → los
   sub-`Agent` (cada capability via su `capability:`).
3. **Rojo de integración:** un test que arme el team desde el manifest y asierte
   el ruteo esperado para un input dado (con vendors `fixture`).
4. Verde + `cli certify <team>` (no compongas algo `< C2`, G-CERT).

## 2.5b Agente reusable: referenciar por id, agent-as-tool, publish

1. **Extraé el agente a su propio manifest** `manifests/<id>.agent.yaml` (archetype,
   capability/tools, `certification`). Ya NO lo definas inline en el supervisor.
2. **Referencialo** desde el/los task graph(s): `agents: [{uses: agent://<id>@1}]`
   (G-BIND-AGENT verifica que exista). El mismo agente se reusa desde varios.
3. **Agent-as-tool** (que otro agente lo invoque como tool): `exposes_as_tool: true`
   en su manifest; el loader lo envuelve con `agent_as_tool(<id>)` (G1).
4. **Publicar hacia afuera** (otro sistema lo consume): `publish: {as: mcp}` (o
   `http`) — AgentSpan lo expone por `execution-id`/MCP. Verificá: `cli list-agents`.
5. Certificá: `uv run python -m sdk.cli certify <id>` (C2 antes de publicar/componer).

## 2.5c Ejecutar un task graph (runtime port) + probar recovery

1. La capability expone `run(input, *, ports=None, tools=None)` puro (G-RUN-SIG) +
   `build()` (StateGraph, G1+).
2. Compilá el manifest a un callable: `build_runnable(manifest, ga_root, ports={...})`
   — el loader resuelve refs `agent://`, inyecta los ports (vendor `fixture` para
   tests) y las tools del catálogo bindeadas.
3. Corré sobre el runtime: `LocalRuntime().run(runnable, input)` → `Execution`
   (id, status, output).
4. **Recovery:** `eid = rt.start_durable(runnable, input)` (arranca, no completa) →
   `rt.resume(eid)` recupera por `execution-id`. Durabilidad sin servidor.
5. El runtime real es el mismo port: `AgentSpanRuntime` (G1+, `agentspan server start`).

## 2.6 Certificar

```bash
cd GraphAgents
uv run python -m sdk.cli check            # compilador rápido (schema + refs), segundos
uv run python -m sdk.cli certify <id>     # check + nivel C0–C3 (exit 1 si < C2)
```

La certificación gobierna catálogo/merge, **nunca el runtime**. Un reporte stale
se degrada a "sin certificar" — jamás inventa verde.

## 2.7 El puente a la fase B (integración al monorepo)

Cuando una capability está probada y certificada y la querés viva en el
dashboard: NO importes nada cruzado. Construí en el monorepo un cast del plugin
`ads` que llame al runtime de AgentSpan por `execution-id` (HTTP) y sirva el
resultado bajo `/api/ads/*`. Ese adaptador es el ÚNICO punto de contacto entre
las dos arquitecturas (ver `01-graph-rules.md` §borde).

## 2.8 El explorer visual (catálogo + grafo + marketplace, estilo n8n)

El explorer es una **proyección read-only** del catálogo (NO un editor: los
manifests siguen siendo la verdad — git + cert + TDD). Una sola fuente, tres
frontends, como el TestKit:

```
sdk/graph.py  (build_graph → {nodes,edges} · to_mermaid)   ← ÚNICA fuente
   ├── sdk/cli.py  graph --format mermaid|json             (terminal / GitHub)
   ├── viewer/server.py  GET /api/graph                    (backend vivo, stdlib http.server)
   └── viewer/index.html  Cytoscape vía CDN                (catálogo + canvas + inspector)
```

- **Levantar:** `python3 -m viewer.server` → http://localhost:8900 (L-3: python3, no `uv run`).
- **Verificar HTTP local:** `urllib`/el browser del preview, NUNCA `curl` (L-4 lo trunca).

### Agregar un dato al grafo (un atributo de nodo / un tipo de arista)

1. **Rojo:** en `tests/architecture/test_system_graph.py` asertá el atributo/arista
   nuevo sobre el catálogo real (ej. `_node(g,"agent:x")["nuevo"] == ...`). Velo fallar.
2. `sdk/graph.py` — agregalo en `_tool_node`/`_agent_node`/`_port_node` o en `_edges_of`.
   **Regla de oro:** el dato sale del modelo del manifest (`manifest_model`), no se inventa.
3. Verde. La UI lo lee solo (es genérica sobre `n.*`); si querés mostrarlo, tocá
   `renderInspector`/`catalogCard` en `viewer/index.html` (zero-build, recargás y listo).

### Agregar un endpoint al backend

1. **Rojo:** en `tests/integration/test_viewer_api.py` llamá `api_route(method, path, …)`
   SIN socket y asertá `(status, payload)`. Velo fallar.
2. `viewer/server.py` — sumá la rama en `api_route` (core ruteable, aislado del
   `BaseHTTPRequestHandler`). Si corre un agente, reusá `run_agent` (rechaza los que
   `consumes:` un port — necesitan un Fixture, no la UI).
3. Verde. El handler es glue fino: no metas lógica ahí.

### Docker

`docker compose up` → toda la suite con el explorer en :8900 (lo sirve el servicio
`graphagents` como su proceso persistente; el CMD de la imagen es `python -m
viewer.server`). Solo el explorer: `docker compose up --no-deps graphagents`. El
hola mundo/CLI son on-demand (`docker compose run --rm graphagents uv run …`).
bind-mount → editás `index.html`/un manifest y refrescás.
