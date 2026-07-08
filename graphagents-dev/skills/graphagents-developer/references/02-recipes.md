# 02 · Recetas (paso a paso, test-first)

Toda receta se ejecuta **test-first** (`00-tdd-law.md`): el primer archivo que
tocás es el test que falla por la razón correcta. Acá va QUÉ archivos tocar; el
bucle rojo→verde→refactor dice en qué orden.

## §0 · Empezá acá: levantar el stack

```bash
cd GraphAgents
docker compose up --build               # toda la suite; la app SIRVE la API del viewer en :8900
#   solo la API:       docker compose up --build --no-deps graphagents
#   sin Docker:        python3 -m viewer.server      (→ http://localhost:8900/api/*)
#   el hola mundo:     docker compose run --rm graphagents uv run python hello_world.py
```

La UI visual es **Acktos Studio** (extensión de VS Code, `vscode-hubara/` en la
raíz del monorepo — su README lista los backends). El viejo explorer web
(`viewer/index.html`) se eliminó; `viewer/server.py::api_route` es el backend
que ambos transportes comparten (HTTP :8900 y el puente stdio `viewer/bridge.py`
que spawnea la extensión).

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
2. `graphs/<x>.py` — DOS entrypoints (convención de capability):
   - `run(input, *, ports=None, tools=None)` PURO — la lógica (G-RUN-SIG, G-DET).
   - `def build() -> CompiledStateGraph` — el `StateGraph` cuyo(s) nodo(s) **REUSAN la
     lógica pura** (vive UNA sola vez). `compile(name="<x>")` — AgentSpan lee ese nombre. El
     nodo LLM, si hay, marcado, `temperature=0` + structured output.
   - **Single-node** (greeter): el único nodo llama al `run()` puro. **Multi-nodo** (una
     capability que COMPONE tools, p.ej. `graphs/ctwa_campaign_funnel.py`): un nodo por tool
     (`parse-entities → parse-insights → complement`), cada uno reusa la **impl de la tool**
     (la misma que compone el `run()`) — el grafo es la estructura explícita, visible en Studio.
   - **Durabilidad — no la sobre-afirmes (L-11):** sin nodo LLM, AgentSpan corre el grafo por
     passthrough = el grafo ENTERO como UNA task (recovery del run completo, NO por-nodo). El
     multi-nodo se justifica por claridad/Studio/futuro, no por durabilidad por-task (eso es
     G1.x). Un claim de recovery-por-nodo exige un test que mate el proceso mid-graph.
   - Referencias ya verdes: `graphs/greeter.py` (single-node) · `graphs/ctwa_campaign_funnel.py`
     (multi-nodo, su golden-replay del compilado + smoke en AgentSpan → §2.5d).
3. Estado: `State` (TypedDict/Pydantic) con reducers declarados si hay writes concurrentes (G-STATE).
4. Datos externos: SOLO vía un port de `consumes:` (G-PORT) — nunca red cruda.
5. Verde: el golden del `run()` puro corre local (`python3 -m pytest …`); el del `build()`
   (StateGraph) importa langgraph → arrancalo con `pytest.importorskip("langgraph")` y corré
   en el container (`docker compose run --rm --no-deps graphagents /opt/venv/bin/python -m
   pytest tests/graphs/test_<x>_golden.py -q`) — L-7.
6. Declarala en un manifest (2.3), corré en AgentSpan (§2.5d) y certificá (2.6).

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

## 2.5 Componer el supervisor (la orquestación ES el task graph)

La orquestación NO es código imperativo — es el `*.taskgraph.yaml`. Test-first:

1. `manifests/<team>.taskgraph.yaml`: `archetype: supervisor`, `strategy:`
   (sequential/parallel/router/...), `agents: [{uses: agent://<id>@1, inputs: {...}}]`.
2. **El wiring (G-WIRE):** para strategies que COMPONEN, cada agente-ref declara
   `inputs: {cap_input: $state.<key>}` — el binding state→input del task graph. El
   seed del supervisor son las claves externas; cada output se mergea al estado y
   alimenta a los de abajo (DAG fan-in). Sin `inputs:` no certifica (G-WIRE). Ver
   `01-graph-rules.md` §"La orquestación ES el task graph".
3. **Rojo de integración — el DoD del FEATURE:** un test que corra el supervisor
   POR SU MANIFEST: `build_runnable(load_manifest(<taskgraph>), ga_root)(seed)` y
   asierte el output TERMINAL (el reporte) + que un seed incompleto falle LOUD. NO
   encadenes los `run()` a mano — eso prueba las unidades, no la orquestación (L-10).
4. Verde + `cli certify <team>` (C2; G-WIRE + G-CERT). Corrélo: `cli run <team> --input-file seed.json`.

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
   `build()` (StateGraph real — §2.1).
2. Compilá el manifest a un callable: `build_runnable(manifest, ga_root, ports={...})`
   — el loader resuelve refs `agent://`, inyecta los ports (vendor `fixture` para
   tests) y las tools del catálogo bindeadas.
3. Corré sobre el runtime: `LocalRuntime().run(runnable, input)` → `Execution`
   (id, status, output).
4. **Recovery:** `eid = rt.start_durable(runnable, input)` (arranca, no completa) →
   `rt.resume(eid)` recupera por `execution-id`. Durabilidad sin servidor.
5. El runtime real es el mismo port: `AgentSpanRuntime` — correrlo sobre el server durable
   es **§2.5d** (G1 hecho: greeter corre en :6767).

## 2.5d Correr una capability en AgentSpan (el runtime durable real) — G1

Cuando la capability tiene `build()` real (§2.1) y la querés correr de verdad sobre el
server durable (no el LocalRuntime in-process):

1. Levantá: `docker compose up -d agentspan` (server + UI en **:6767**), o `docker compose up`
   (toda la suite + el explorer en :8900).
2. `loader.build_agent(node, ga_root)` → el `CompiledStateGraph` de `build()`. AgentSpan lo
   toma **DIRECTO**: NO hay wrapper `Agent` (L-8). Supervisor/tools nativos de AgentSpan: G2+.
3. `AgentSpanRuntime().run(graph, input)` corre `AgentRuntime().run(graph, input)`, mapea el
   `AgentResult` → `Execution` y **desempaqueta** `output['result']` (el json del state del
   passthrough). El `execution-id` (UUID de Conductor) aparece en la UI de **:6767**.
4. Granularidad de tasks (L-14, firsthand): un grafo **single-node** corre como UNA task; un
   grafo **multi-nodo** AgentSpan lo descompone en tasks de Conductor POR-NODO (un worker por
   nodo, con retry). Server-side solo se mapea `operator.add` como reducer — para estado
   compuesto usá canales por-clave LastValue o merge-en-código (NO un reducer custom). El
   server URL sale de `AGENTSPAN_SERVER_URL` (el SDK le agrega `/api`).
5. **Rojo de integración:** `tests/integration/test_agentspan_runtime.py` con
   `importorskip("agentspan")` + `skipif(not _server_up())` → local/CI sin server SKIP,
   container con server RUN: `docker compose run --rm --no-deps graphagents
   /opt/venv/bin/python -m pytest tests/integration/test_agentspan_runtime.py -q` (L-7).
6. **Desde el explorer:** el panel CORRER elige `AgentSpan · durable` → el run cae en :6767
   (link en el inspector). Si editaste `viewer/server.py` (o cualquier `.py` del viewer),
   recreá el container: `docker compose up -d --force-recreate graphagents` (NO `restart`, L-9).

Las **tres lentes** son complementarias: el explorer (**:8900**) = el mapa del sistema ·
AgentSpan (**:6767**) = la ejecución durable · `langgraph dev` (Studio) = el interior de UNA
capability (G1.x). Recovery mid-flight real necesita re-pasar el grafo
(`AgentRuntime().resume(eid, graph)`); el port `resume(id)` devuelve el estado actual.

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

**Declarar la costura en `vscode-hubara/seams.yaml` (obligatorio al cerrar la
integración).** Las conexiones cross-sistema NO se auto-detectan: Acktos
Studio (la extensión de VS Code) dibuja el workspace con las costuras de ese
archivo — sin la entrada, tu integración es invisible en el mapa (y en la
vista colapsada, donde cada costura aparece como sub-caja dentro del sistema).
Formato — ids NAMESPACED tal cual los produce cada grafo (`hub:` = System Map
del monorepo, `ga:` = GraphAgents):

```yaml
seams:
  - id: ads-analytics-pod
    from: hub:plugin:ads            # nodo del system map (plugin:/api:/worker:…)
    to: ga:agent:ads-analytics      # nodo del catálogo GA (agent:/tool:…)
    label: "pod CTWA (runs/conductor.py)"   # CITÁ el código vivo que la implementa
    kind: launches
```

Regla: cada costura debe ser VERIFICABLE en código vivo, no aspiracional —
el label nombra el archivo que la implementa. Una costura cuyo from/to no
resuelve se reporta como "rota" en el canvas (no rompe nada, pero te delata).

## 2.8 La API del viewer (el backend de Acktos Studio)

La UI visual es **Acktos Studio** (extensión de VS Code, `vscode-hubara/`); acá
vive su backend. El grafo es una **proyección read-only** del catálogo (NO un
editor: los manifests siguen siendo la verdad — git + cert + TDD). Una sola
fuente, tres consumidores, como el TestKit:

```
sdk/graph.py  (build_graph → {nodes,edges} · to_mermaid)   ← ÚNICA fuente
   ├── sdk/cli.py  graph --format mermaid|json             (terminal / GitHub)
   ├── viewer/server.py  api_route()                       (el core ruteable, stdlib http.server en :8900)
   └── viewer/bridge.py  stdio JSON-lines                  (el MISMO api_route — lo spawnea Acktos Studio)
```

- **Levantar standalone:** `python3 -m viewer.server` → http://localhost:8900/api/* (L-3: python3, no `uv run`).
- **Verificar HTTP local:** `urllib`, NUNCA `curl` (L-4 lo trunca).

### Agregar un dato al grafo (un atributo de nodo / un tipo de arista)

1. **Rojo:** en `tests/architecture/test_system_graph.py` asertá el atributo/arista
   nuevo sobre el catálogo real (ej. `_node(g,"agent:x")["nuevo"] == ...`). Velo fallar.
2. `sdk/graph.py` — agregalo en `_tool_node`/`_agent_node`/`_port_node` o en `_edges_of`.
   **Regla de oro:** el dato sale del modelo del manifest (`manifest_model`), no se inventa.
3. Verde. Acktos Studio lo recibe solo por el bridge; si hay que RENDERIZARLO,
   el canvas vive en `vscode-hubara/webview/src/` (FlowNode/Inspector).

### Agregar un endpoint al backend

1. **Rojo:** en `tests/integration/test_viewer_api.py` llamá `api_route(method, path, …)`
   SIN socket y asertá `(status, payload)`. Velo fallar.
2. `viewer/server.py` — sumá la rama en `api_route` (core ruteable, aislado del
   `BaseHTTPRequestHandler`). Si corre un agente, reusá `run_agent` (rechaza los que
   `consumes:` un port — necesitan un Fixture, no la UI).
3. Verde. Ambos transportes (HTTP y el bridge stdio) lo sirven sin tocar nada más.

### Docker

`docker compose up` → toda la suite con la API del viewer en :8900 (la sirve el
servicio `graphagents` como su proceso persistente; el CMD de la imagen es
`python -m viewer.server`). Solo la API: `docker compose up --no-deps graphagents`.
El hola mundo/CLI son on-demand (`docker compose run --rm graphagents uv run …`).
bind-mount → editás un manifest y el catálogo se refleja.
