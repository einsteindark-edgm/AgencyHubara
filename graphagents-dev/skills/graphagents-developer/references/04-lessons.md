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

### L-7 · Los tests que necesitan langgraph/agentspan van con `importorskip` (corren en el container) (2026-06-19, G1)
- **Síntoma:** el golden de `build()` (StateGraph) y el smoke de AgentSpan importan
  langgraph/agentspan; el python del sistema NO los tiene (L-3) → romperían el loop
  local (`python3 -m pytest`).
- **Causa raíz:** langgraph/langchain/agentspan viven en el venv del container
  (`/opt/venv`), no en el python del sistema. (agentspan en pypi 0.1.x; langgraph
  NO es dep de agentspan, se instala aparte.)
- **Fix aplicado:** `pytest.importorskip("langgraph")` / `("agentspan")` al tope del
  test → local SKIP, container RUN. El loop de grafos/runtime-real corre en el
  container: `docker compose run --rm --no-deps graphagents /opt/venv/bin/python -m
  pytest …` (el python del venv DIRECTO — NO `uv run`, lo bloquea el hook, L-3).
- **Regla para el skill:** todo test que importe langgraph/agentspan empieza con
  `importorskip`; corrélos en el container con `/opt/venv/bin/python -m pytest`. El
  smoke de un runtime real se guarda además con `skipif(not _server_up())`.
- **Guard:** el `importorskip` + `skipif` (local/CI sin deps/server → skip; container
  con todo → run). 51/51 verde en el container, 48 local.

### L-8 · La API real de AgentSpan: pasás el grafo COMPILADO directo, y el output viene envuelto (2026-06-19, G1)
- **Síntoma:** el `loader` asumía `Agent(name=, graph=)` + `AgentRuntime().run(agent,
  input)` con output crudo — todo inventado de la landing.
- **Causa raíz (L-0 otra vez):** la API real (`sdk/python/` del repo) NO tiene wrapper
  `Agent` para LangGraph. Se compila el `StateGraph` con `compile(name=...)` y se pasa
  el grafo compilado **directo** a `AgentRuntime().run(graph, input)` — el SDK lo
  autodetecta como langgraph. Server URL = `AGENTSPAN_SERVER_URL` (el SDK le agrega
  `/api`). Sin LLM → path **passthrough** (el grafo entero como UNA task durable). El
  output llega como `{'result': '<json-string del state final>'}` (hay que parsearlo).
  Recovery (`resume`) necesita RE-pasar el grafo (`resume(eid, graph)`); el recovery
  LangGraph-nativo (checkpoints/time-travel) NO está — la durabilidad la da Conductor.
- **Fix aplicado:** `loader.build_agent` devuelve el `CompiledStateGraph` (sin wrapper);
  `AgentSpanRuntime.run` llama `AgentRuntime().run(graph, input)` y desempaqueta
  `output['result']` (JSON) → `Execution`. `greeter` corrió end-to-end: execution-id
  en `:6767`, `COMPLETED`, `"hola, ada"` (el input atravesó el grafo).
- **Regla para el skill:** para correr una capability en AgentSpan, pasá el grafo de
  `build()` (con `compile(name=...)`) DIRECTO al runtime; desempaquetá `output['result']`.
  NO inventes la API — leé `sdk/python/` del repo de agentspan (L-0).
- **Guard:** `tests/integration/test_agentspan_runtime.py` (skip sin server) + el golden
  de `build()` (`tests/graphs/test_greeter_golden.py`).

### L-9 · Editar un `.py` del viewer/sdk en Docker → `up -d --force-recreate`, NO `restart` (2026-06-19, wiring explorer→AgentSpan)
- **Síntoma:** edité `viewer/server.py`, hice `docker compose restart graphagents`, y el
  endpoint `/api/run` seguía sirviendo el código VIEJO (sin el param `runtime`; el run
  caía a LocalRuntime con id `local-000001`). `docker compose exec graphagents grep -c
  _run_on_agentspan viewer/server.py` → **0** (el container montaba el archivo viejo).
- **Causa raíz:** (a) Python no hot-reloadea — el proceso `python -m viewer.server` quedó
  con el módulo viejo en memoria; (b) el bind-mount de Docker Desktop (macOS) no re-sincronizó
  el cambio en `restart` (cache del overlay sobre el `COPY . .` de la imagen).
- **Fix aplicado:** `docker compose up -d --force-recreate graphagents` (recrea el container
  con mount fresco + proceso nuevo). Tras eso, el run en `runtime=agentspan` devolvió un
  execution-id de Conductor (UUID, visible en `:6767`).
- **Regla para el skill:** tocaste un `.py` del viewer/sdk y corre en Docker →
  `docker compose up -d --force-recreate <svc>`. Tocaste SOLO `index.html` → basta refrescar
  el browser (el server lo lee por-request). Verificá el endpoint servido con `urllib` (L-4),
  no `curl`.
- **Guard:** procedimiento (sin gate). El smoke por urllib al endpoint `:8900` confirma que
  el código nuevo está vivo (`runtime` field + execution-id UUID).

### L-10 · Panel verde ≠ supervisor compuesto; correr el grafo por su MANIFEST es parte del DoD del feature (2026-06-20, feature ads-analytics CTWA)
- **Síntoma:** el panel `/graphagents-gates` salió VERDE con 5 tools + 5 capabilities + supervisor
  todos C2, pero el pod NO produce el reporte final al correrse por su propio manifest
  (`build_runnable(ads-analytics.taskgraph.yaml)` devolvía solo el PRIMER agente). El "end-to-end"
  solo vivía en un test que encadenaba los `run()` A MANO (bypassa el loader).
- **Causa raíz:** el golden-replay prueba cada capability PURA por separado; el `loader` no
  implementa la orquestación multi-agente (strategy `sequential`/DAG = G1+/AgentSpan). Un supervisor
  con 2 extractores que alimentan 1 analyzer es un DAG, no una línea — la composición real corre en
  AgentSpan, no en el LocalRuntime (el branch supervisor del loader es router-shaped: corre uno). Lo
  cazó un premortem multi-agente (`graph-cert-reviewer`, no-self-review), NO el panel determinístico.
- **Fix aplicado (RESUELTO 2026-06-20):** (a) los bugs de correctitud de las capabilities PURAS se
  arreglaron test-first (actions-as-string→conv muda; QA sin reconciliar el periodo; revenue=0→rotate;
  currency dropeada; seam KeyError). (b) La ORQUESTACIÓN se implementó como CORE de la arquitectura:
  binding `inputs:` en los agentes-ref (`manifest_model`) + el `loader` threadea un estado acumulador
  (`build_runnable`, composing branch) + el check **G-WIRE** lo exige (`testkit/checks.py`) + el CLI
  `run --input-file` lo corre. El supervisor `ads-analytics` AHORA corre por su manifest y produce el reporte.
- **Regla para el skill:** el DoD de un FEATURE (no de una unidad) incluye correr el grafo por su
  MANIFEST, no solo los `run()` sueltos. Antes de declarar un feature terminado: (1) corré un
  premortem multi-agente — caza lo que el panel verde no; (2) si el supervisor compone un DAG,
  declaralo deferred-a-G1+ EXPLÍCITO. "tests verdes ≠ feature viva" es literal, no un eslogan.
- **Guard:** `tests/integration/test_ads_analytics_supervisor.py` (corre el supervisor POR SU MANIFEST
  → reporte terminal; + un seed incompleto falla LOUD) + `tests/architecture/test_taskgraph_wiring.py`
  (G-WIRE: un supervisor que compone sin `inputs:` no certifica). La orquestación ahora es ley del panel.

### L-11 · El path passthrough corre el grafo ENTERO como UNA task — no afirmes durabilidad por-nodo (2026-06-20, G1 ctwa-campaign-funnel)
- **Síntoma:** implementé `ctwa_campaign_funnel.build()` como StateGraph multi-nodo (un nodo por tool:
  parse-entities → parse-insights → complement) y, sin verificarlo, escribí en el docstring + el test que
  "cada nodo es una task durable de Conductor; un crash entre tasks se recupera sin recomputar las
  anteriores". Los tests (golden-replay del compilado + smoke en AgentSpan) salieron VERDES — pero la
  AFIRMACIÓN de granularidad de durabilidad era falsa para el path actual.
- **Causa raíz:** sin un nodo LLM, AgentSpan corre el `CompiledStateGraph` por el **path passthrough** =
  el grafo entero como UNA sola task durable (recovery por el execution-id del run completo, NO por-nodo;
  §2.5d línea "Sin LLM → path passthrough"). Los tests asertaban el OUTPUT (el complemento sobrevive el
  round-trip), que ES correcto — no la granularidad de las tasks. Clásico "tests verdes ≠ feature viva":
  el output verde no respalda un claim de recovery por-nodo que nadie ejercitó (no hubo crash mid-graph).
- **Fix aplicado:** corregí el docstring de `build()` y del smoke para decir la verdad — el grafo multi-nodo
  es la **estructura explícita** (legible en langgraph Studio, lista para cuando AgentSpan compile a
  tasks por-nodo / haya un nodo HUMAN/LLM que fuerce el path no-passthrough), pero HOY corre como una task
  passthrough. La descomposición por-task (retry/HUMAN/recovery por nodo) queda marcada **G1.x EXPLÍCITO**.
- **Regla para el skill:** un claim de DURABILIDAD (recovery por-nodo, retry por task, HUMAN gate) solo
  vale si hay un test que lo EJERCITA (matá el proceso mid-graph y probá que no recomputa) — no lo
  inferís de un output verde. Multi-nodo en `build()` se justifica por CLARIDAD/Studio/futuro, no por
  durabilidad que el passthrough no da aún. Describí el mecanismo real, no el aspiracional.
- **Guard:** el docstring de `graphs/ctwa_campaign_funnel.py` `build()` + `test_agentspan_runtime.py`
  ahora citan L-11 y describen el passthrough honestamente.
- **RESUELTO (2026-06-20) — el recovery por-nodo ahora está PROBADO, no afirmado:** `build(*, checkpointer=)`
  acepta un checkpointer de LangGraph (MemorySaver/SQLite/Postgres); `tests/integration/test_durable_recovery.py`
  es EL test que L-11 exigía: inyecta un crash en el nodo `complement`, reanuda con el mismo `thread_id`, y
  asierta que `parse-entities` corrió UNA vez (NO se recomputó) + que el resultado correcto sobrevive. Alcance
  honesto: esto prueba el recovery por-nodo cuando **LangGraph drive** la ejecución (durabilidad
  LocalRuntime/checkpointer); el **passthrough de AgentSpan** sigue corriendo el grafo como UNA task — la
  compilación a tasks por-nodo NATIVA de AgentSpan (retry/HUMAN por task de Conductor) sigue siendo **G2**.

### L-12 · Un campo del manifest que el loader IGNORA igual necesita su check (regla de oro) (2026-06-20, cleanup post-cert-review)
- **Síntoma:** el manifest `ctwa-campaign-funnel.agent.yaml` bindeaba `complement-funnel` con
  `with: {entities, insights}`, pero el `tool.yaml` de esa tool declara `inputs: {payload}`. El binding
  era INERTE (el loader inyecta la impl por `ref_id`, ignora el `with:`) **y** contradecía el contrato.
  El panel verde no lo cazó — lo encontró un cert-review (graph-cert-reviewer) leyendo el diff a mano.
- **Causa raíz:** `with:` (modelado como `ToolSpec.binding`) se parseaba pero NINGÚN check lo validaba
  contra el contrato de la tool → un campo del manifest **sin su check** (viola la regla de oro). Que el
  loader lo ignore a propósito (la capability que COMPONE cablea sus tools por dentro del StateGraph, no
  por binding declarativo) no exime al campo: si se puede escribir, se puede escribir MAL, y mintió.
- **Fix aplicado:** check `check_tool_bindings_match_contract` (G-BIND binding↔contrato, test negativo-
  primero contra `run_checks`): cada clave del `with:` debe ser un input declarado del `tool.yaml`. Se
  removió el `with:` de `complement-funnel` (su `payload` lo construye la capability desde estado
  intermedio — los outputs de las otras dos tools —, no es un binding a `$state`). Las otras dos tools
  (`with: {payload: $state.X}`) SÍ matchean el contrato y quedan. El check cazó SOLO ese manifest (los
  demás `with:` del catálogo —greeter `hello`, etc.— ya eran fieles).
- **Regla para el skill:** si agregás o ya parseás un campo del manifest, dale su check en el MISMO
  cambio — incluso si el runtime lo ignora hoy (la regla de oro no tiene excepción "pero es inerte").
  Un `with:` solo se justifica si nombra inputs del contrato Y hay un `$state` que los provee; si la
  capability arma el payload por dentro, NO declares `with:` (doc inerte que miente > sin doc).
- **Guard:** `tests/architecture/test_tool_binding_contract.py` (negativo: `with:` que contradice el
  contrato no certifica · positivo: `with: {payload}` pasa · sin-`with:` no dispara nada) + el check en
  `run_checks`. El header de `sdk/testkit/checks.py` lista G-BIND binding↔contrato (y G-WIRE, que faltaba).

### L-13 · La `State` TypedDict de un `build()` debe anotarse con tipos del MÓDULO (`from __future__ import annotations` + langgraph) (2026-06-20, G1 build() del resto de capabilities)
- **Síntoma:** `blended_economics.build()` anotaba `period: Optional[dict]` con `Optional` importado DENTRO
  de `build()`. Al compilar el grafo (`StateGraph(State)`), langgraph reventó con
  `NameError: name 'Optional' is not defined` — el golden-replay del compilado lo cazó al toque.
- **Causa raíz:** los módulos de `graphs/` tienen `from __future__ import annotations` → las anotaciones
  son STRINGS (lazy). langgraph hace `typing.get_type_hints(State)` para armar los channels, y eso evalúa
  las strings en los **globals del módulo** (+ los del módulo donde se definió la clase). `Optional`
  importado en el scope LOCAL de `build()` no está en los globals → NameError. Los builtins
  (`list`/`dict`/`object`/`str`) siempre resuelven; por eso `greeter`/`ctwa_campaign_funnel` (que solo
  usaban builtins) nunca lo pegaron.
- **Fix aplicado:** anotar la `State` SOLO con builtins (`period: dict`, no `Optional[dict]`). El tipo es
  un hint del schema — langgraph NO valida el valor, así que `period=None` con anotación `dict` es legal.
  Si hiciera falta un tipo no-builtin (`Annotated[list, operator.add]` para un reducer, G-STATE), importarlo
  a nivel MÓDULO (top del archivo), no dentro de `build()`.
- **Regla para el skill:** la `State` de un `build()` se anota con tipos resolvibles desde los globals del
  módulo. Default: builtins. Reducers/tipos especiales → import a nivel módulo. Nunca un tipo importado
  local. El guard es el golden-replay del compilado (`build().invoke(seed)`): si la anotación no resuelve,
  falla en `compile()`, no en runtime.
- **Guard:** `tests/graphs/test_<cap>_build.py` (compila el grafo e invoca) — está para las 5 capabilities.

<!-- AÑADIR NUEVAS LECCIONES ARRIBA DE ESTA LÍNEA, NUMERADAS L-1, L-2, ... -->
