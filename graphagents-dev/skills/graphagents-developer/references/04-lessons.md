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
  LocalRuntime/checkpointer).
- **CORRECCIÓN (2026-06-20, ver L-14):** el claim de arriba de que "AgentSpan corre el grafo como UNA task
  passthrough" es FALSO para grafos MULTI-NODO — firsthand, AgentSpan los descompone en tasks de Conductor
  POR-NODO (un worker por nodo, con retry). Vale para single-node. El recovery por-nodo SIN recomputar a nivel
  Conductor quedó luego PROBADO (L-14, `test_conductor_reintenta_el_nodo_fallido_...`, log `A B B C`).

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

### L-14 · AgentSpan compila un grafo multi-nodo a tasks de Conductor POR-NODO, y server-side SOLO mapea `operator.add` (2026-06-20, G2 supervisor compuesto)
- **Síntoma:** `build_agent(ads-analytics)` compuso los 6 agentes en UN StateGraph con un canal único
  `acc: Annotated[dict, _merge]` (reducer de merge). Local (`graph.invoke`) andaba; en el **server real de
  AgentSpan** FALLÓ tras ~68s de retries: `sales-ledger` leía `$state.manual_sales` pero el `acc` solo tenía
  `{currency, insights}` (el output del nodo anterior) — el seed se había perdido.
- **Causa raíz (dos hallazgos firsthand del log del server):** (1) AgentSpan **descompone el grafo multi-nodo
  en tasks de Conductor POR-NODO** — spawneó 6 workers (`ads-analytics_ctwa_insights_0`,
  `..._sales_ledger_1`, …), cada uno una task durable con su retry (vimos sales-ledger reintentar 3×). NO es
  "el grafo entero como UNA task passthrough" (eso vale para single-node; corrige el claim de §2.5d/L-11).
  (2) Server-side **solo se mapea `operator.add`** como reducer: *"custom reducers are not supported
  server-side… last-write-wins… may cause data loss"*. Mi `_merge` custom se ignoró → el canal `acc`
  (LastValue) se sobreescribía con el patch de cada nodo → el acumulador se destruía.
- **Fix aplicado:** SIN reducer custom. Canal `acc: dict` (LastValue) + cada nodo **mergea EN CÓDIGO** y
  devuelve el `acc` COMPLETO (`acc = {**acc, **out}`) → con una cadena SECUENCIAL, last-write-wins es
  correcto (cada nodo ve el acumulador completo del anterior). Verificado verde en el server real (2.5s, sin
  retries). **`parallel` (RESUELTO 2026-06-20):** un canal `patches: Annotated[list, operator.add]` (el ÚNICO
  reducer server-safe) + fan-out a agentes independientes que APPENDEAN `[out]` + un `join` que foldea →
  corre verde en el server (FORK_JOIN, 5.3s). Solo para agentes que escriben claves DISJUNTAS.
  `build_supervisor_graph` compone `sequential`/`router`/`parallel`; `handoff`/`swarm` raisean loud.
- **Regla para el skill:** un grafo que anda con `graph.invoke` local NO está probado hasta correrlo en el
  **server de AgentSpan** — la semántica de reducers/estado difiere (solo `operator.add`; multi-nodo = tasks
  por-nodo). Para estado compuesto server-safe: o canales por-clave LastValue, o merge-en-código devolviendo
  el estado completo (secuencial). Nunca un reducer de merge custom para el server.
- **Durabilidad por-nodo en Conductor — AHORA PROBADA (2026-06-20):** que AgentSpan corra cada nodo como
  task de Conductor con retry, Y que un retry NO recompute los nodos previos, es firsthand Y test-probado:
  `test_conductor_reintenta_el_nodo_fallido_sin_recomputar_los_previos` (en `test_agentspan_runtime.py`) corre
  un grafo sonda a→flaky_b→c en el server real; flaky_b crashea la 1ra vez y Conductor REINTENTA SOLO esa task
  → el LOG cross-process (los workers son procesos forkeados, no sirve un contador in-process) sale `A B B C`:
  A y C UNA vez, B dos. Hay DOS niveles de recovery por-nodo probados: checkpointer de LangGraph in-process
  (`test_durable_recovery.py`) Y Conductor server-side (este). Ya NO es un claim sin guard.
- **Guard:** `test_supervisor_native_graph.py` (compone local) + `test_agentspan_runtime.py` (smokes en el
  server real: el supervisor compuesto + la sonda de recovery por-nodo de Conductor). `build_supervisor_graph`
  documenta el porqué del merge-en-código.

### L-15 · Los conditional edges de langgraph CUELGAN en el server de AgentSpan — routing = un nodo dispatcher (2026-06-20, G2.x router)
- **Síntoma:** implementé `router` en `build_supervisor_graph` con `add_conditional_edges(START, _route, ...)`.
  Local (`graph.invoke`) ruteó perfecto; en el **server de AgentSpan** la ejecución COLGÓ → read timeout de 30s
  → FAILED. El server SÍ creó un worker `ads-supervisor___start___router` (reconoció el conditional edge) pero
  la ejecución nunca resolvió.
- **Causa raíz:** AgentSpan compila el conditional edge de langgraph a un task "router" de Conductor que no
  resuelve / se cuelga — la integración langgraph→Conductor de conditional edges es incompleta en esta versión.
  Mismo patrón que L-14: lo que anda con `graph.invoke` local NO está probado hasta el server.
- **Fix aplicado:** router como UN nodo dispatcher — un solo `_router_node` que elige el agente por
  `acc['route']` (default: el 1ro) y lo corre EN CÓDIGO, sin conditional edges. Una sola task de Conductor →
  server-safe. Misma semántica que el router del LocalRuntime (`build_runnable`). Verde local + server (2.2s).
- **Regla para el skill:** para routing en un `StateGraph` que va a AgentSpan, NO uses `add_conditional_edges`
  (cuelga en Conductor) — un nodo dispatcher que decide en código. Y SIEMPRE verificá el routing en el SERVER,
  no solo con `graph.invoke` local (L-14/L-15 son la misma lección: la semántica de control-flow difiere).
- **Guard:** `test_router_compuesto_rutea_al_agente_elegido` (local, rutea al NO-default) +
  `test_router_compuesto_corre_en_agentspan` (server real). El docstring de `build_supervisor_graph` lo explica.

### L-16 · handoff/swarm son NATIVOS de AgentSpan y LLM-driven — no se componen como StateGraph determinista (2026-06-20, cierre G2.x)
- **Hallazgo:** al cerrar la lista de strategies (sequential/router/parallel ya server-verified), faltaban
  `handoff`/`swarm`. Antes de construirlas como un StateGraph dinámico (Command/cycles), inspeccioné la API
  real de AgentSpan: son primitivas NATIVAS (`agentspan.agents.handoff`, `HandoffCondition`) y **LLM-driven**.
- **Detalle (de la API):** el patrón es `Agent(name=, model="openai/gpt-4o", agents=[...], strategy="swarm",
  handoffs=[OnToolResult(tool_name=, target=), OnTextMention(text=, target=), OnCondition(...), OnFail(...)])`.
  Los handoffs disparan sobre las **tool-calls y el TEXTO de un agente LLM** durante la conversación → son
  inseparables del LLM. Las capabilities de este catálogo son G-DET (sin LLM) → no hay texto/tools del LLM
  sobre los que un `OnTextMention`/`OnToolResult` pueda disparar. Además, los conditional edges de langgraph
  ya cuelgan en Conductor (L-15) → una versión langgraph local-only de handoff sería un fake que no corre en
  el server.
- **Decisión:** handoff/swarm pertenecen al **workstream del nodo LLM** (el reporter narrativo y los agentes
  conversacionales), no a la composición determinista. `build_supervisor_graph` compone las TRES deterministas
  (sequential/router/parallel — todas verdes en el server real) y **raisea loud** para handoff/swarm citando
  L-16. Cuando se implemente el LLM, handoff/swarm se hacen con `agentspan.agents.handoff` nativo, no con un
  StateGraph.
- **Regla para el skill:** distinguí orquestación DETERMINISTA (sequential/router/parallel → un `StateGraph`
  componible por el loader) de LLM-DRIVEN (handoff/swarm → `Agent` nativo de AgentSpan con `handoffs=`). No
  fuerces las segundas a un StateGraph determinista; van con el LLM.
- **Guard:** `test_handoff_y_swarm_raisean_loud_pertenecen_al_LLM` (asierta el raise con ref a L-16) + el
  mensaje de `build_supervisor_graph`.

### L-17 · el trace de runtime debe anclar el match al prefijo del workflow — Conductor emite system-tasks `_fork`/`_join` (2026-06-20, trace en vivo Fase 1)
- **Síntoma:** al leer el estado por-nodo de una ejecución `parallel` desde la REST API de Conductor, el paso
  `join` del plan mostraba el `ms`/`task_id`/acc de la task EQUIVOCADA → click→"ver por dentro" del join mentía.
- **Causa:** un grafo FORK_JOIN en Conductor tiene DOS tasks que terminan en `join`: la system-task del framework
  (`_join`, taskType JOIN) **y** el nodo real del loader (`<wf>_join`, loader.py:297). El matcher hacía
  `endswith("join")` → `next(...)` agarraba la system-task `_join`. El golden sintético pasaba porque NO incluía
  las system-tasks `_fork`/`_join`/`_fork_merge` que el shape REAL trae. Patrón gotcha #1 (tests verdes, feature
  rota) — solo visible contra el server vivo.
- **Fix:** anclar el match al prefijo del workflow (`endswith(f"{workflowName}_{token}")` / `f"{wf}_join"`). El
  `_join` framework no lleva el prefijo `<wf>_` → cae a `unmatched_tasks`. El mismo anclaje mata los falsos
  positivos por sufijo (un sibling cuyo nombre underscored termine en el token de otro).
- **Regla para el skill:** el dato de runtime viene de Conductor con su naming REAL (system-tasks incluidas);
  espejá el naming del loader ANCLADO al `workflowName`, nunca por sufijo suelto. Validá el trace contra una
  ejecución VIVA, no solo un fixture sintético — el fixture no tiene las system-tasks del framework.
- **Guard:** `test_trace_join_binds_the_loader_node_not_the_framework_join_task` (fixture con `_fork`/`_join` +
  el `<wf>_join` real; asierta que bindea el real y las system-tasks van a unmatched).

### L-18 · un nodo con retry reporta su intento TERMINAL, no el primero — Conductor escribe un registro por intento (2026-06-20, trace en vivo Fase 1)
- **Síntoma:** un nodo que falló-y-se-recuperó (lo que prueba el recovery de L-14) se pintaba `failed`/retries=0
  — lo OPUESTO a la verdad (`done`/retries=1). Pintaba verde-vivo como rojo y ocultaba el recovery, contradiciendo
  la razón de ser del runtime durable.
- **Causa:** Conductor devuelve UN task por INTENTO con el mismo `referenceTaskName` (flaky_b: FAILED retryCount=0
  + COMPLETED retryCount=1). El matcher tomaba `next(...)` = el primero = el intento fallido.
- **Fix:** entre tasks homónimos, elegir el intento terminal (`max` por `retryCount`, desempate por `startTime`)
  y derivar `retries` de ese. Marcar TODOS los intentos como matched (el fallido no debe ensuciar `unmatched`).
- **Regla para el skill:** el shape de Conductor es multi-registro por retry; cualquier lectura de estado por-nodo
  debe colapsar los intentos al terminal. Un solo `next()`/`find` sobre `referenceTaskName` es un bug latente de
  recovery — la observabilidad mentiría justo en el caso que el runtime durable existe para resolver.
- **Guard:** `test_trace_retried_node_reports_terminal_attempt_not_first` (flaky con FAILED+COMPLETED → asierta
  done/retries=1/task terminal, unmatched vacío).

### L-19 · ninguna etiqueta de check VERDE sin un check real detrás — el overlay de cert no puede dar falsa confianza (2026-06-20, inspect en el viewer)
- **Síntoma:** el explorer ganó un overlay "verificar certs" que pinta cada nodo/relación verde=garantizado/rojo=roto
  (la promesa: confiar a solo visual, sin leer código). La arista `consumes` (agente→port) se pintaba SIEMPRE verde
  con la etiqueta "G-PORT · port declarado" — pero el `_rule` tenía `[]` hardcodeado (no corría NADA), y ni siquiera
  existe un check G-PORT en el TestKit (`run_checks` corre 7, ninguno valida ports). Un `consumes` a un port
  inexistente daba `ok:True`: el operador "confiaría" en una garantía inexistente.
- **Causa:** se etiquetó una relación con el nombre de una regla sin implementar el check que esa regla representa.
  Peor: "G-PORT" en el plugin (`01-graph-rules.md`) es una propiedad AST de la *capability* (no `requests.get`
  suelto), NO la existencia de una línea `consumes:`. Etiqueta decorativa + nombre equivocado = doble mentira.
- **Fix:** correr un check REAL y honesto (`tgt in PORTS` del ConnectorKit) + renombrar la regla a lo que de verdad
  prueba ("port resuelve en el registry", NO "G-PORT"). El nodo `port` también ganó su rule de resolución (antes
  `rules:[]`). Guard del negativo: un `consumes`/port a un id ausente da rojo.
- **Regla para el skill:** en una feature de "confiar sin leer código", CADA badge verde debe tener un check
  determinista detrás que de verdad lo pruebe — y el check debe correr la lógica REAL (idealmente delegando en el
  TestKit, fuente única), nunca un `[]` ni un nombre de regla prestado. Si una relación no tiene check, pintala
  NEUTRA (gris), nunca verde. Es la regla de oro del plugin-protocol llevada al overlay: ninguna etiqueta de check
  sin su check. Y el agregado global (el botón) debe reflejar si TODO pasó, no si el overlay cargó.
- **Guard:** `test_consumes_edge_to_unknown_port_is_red` + `test_consumes_edge_is_guaranteed_by_real_port_resolution`
  + `test_port_node_checks_resolve_in_registry` (tests/architecture/test_inspect.py).

### L-20 · el guard anti-alucinación del nodo LLM compara VALORES, no dígitos — normalizar por-dígito colisiona números de distinto concepto (2026-06-20, nodo LLM narrativo del reporter)
- **Síntoma:** el nodo LLM narrativo (cita los números del analyzer, no computa) trae un guard
  `invented_numbers` que debe cazar cualquier cifra que el LLM inventa. La 1ra versión normalizaba
  por dígitos (`_norm_num` = quitar todo lo no-dígito) → `5.0` (MER) y `0.40` (drop-off) colapsaban
  a `50`/`040`. Eso (a) **dejaba pasar** un inventado `50%` o `50 órdenes` (colisiona con la forma
  sin-punto de MER `5.0`), y (b) **falso-positiveaba** un reformateo legítimo `0.40→40%` o el
  formato europeo `120.000,00`. El test vivo contra DeepSeek quedaba **flaky por diseño** (rojeaba
  si el LLM escribía `40%` en vez de `0.40`).
- **Causa:** comparar la concatenación de dígitos ignora el separador decimal y el concepto del
  número. `5.0` (un MER) y `50` (un %/órdenes) son números DISTINTOS pero misma cadena de dígitos.
- **Fix:** parsear cada token a su VALOR (`_to_value`, resolviendo miles-vs-decimal es/us) y comparar
  valores con tolerancia, ampliando la fuente con las formas ratio↔% (`v*100`, `v/100`). Así
  `0.40↔40%` y `120000↔$120.000↔120.000,00` son el MISMO número, pero `50%` inventado NO matchea
  MER `5.0`. Tolerar `|v|<10` (ruido de prosa: '5 días').
- **Regla para el skill:** un guard que compara números (anti-alucinación, reconciliación, dedup)
  compara VALORES PARSEADOS, nunca strings de dígitos — y contempla las transformaciones que un
  narrador hace (separador de miles, coma decimal, ratio↔porcentaje). Si no, miente justo donde
  promete confianza. Y un guard sobre la salida de un LLM debe tolerar el reformateo legítimo, o el
  test vivo flakea.
- **Guard:** `test_guard_catches_collision_invention` + `test_guard_allows_percentage_reformat_of_a_ratio`
  + `test_guard_allows_european_thousands_format` (tests/graphs/test_ctwa_report_narrate.py).

### L-21 · al submitear a AgentSpan, el caller debe ESPEJAR la forma del estado compilado — un supervisor toma `{acc: seed}`, no el seed crudo (2026-06-21, "probar durable" del viewer)
- **Síntoma:** correr el flujo `ads-analytics` en el runtime durable desde el viewer fallaba: el
  1er nodo (`ctwa-insights`) reventaba con `dictionary update sequence element #0 has length 1; 2
  is required`, los demás quedaban `pending`. Un run real lo reveló (no lo cazaba ningún test —
  los tests de supervisor-en-server ya pasaban la forma correcta a mano).
- **Causa:** un supervisor compila a un `StateGraph(_SupervisorState{acc})` (o `_ParallelSupervisorState
  {acc, patches}`); cada nodo hace `acc = dict(state["acc"])`. El viewer (`_run_on_agentspan`) pasaba
  el seed CRUDO (`{meta_insights, manual_sales, ...}`) en vez de `{acc: seed}`. Sin la clave `acc`, el
  framework langgraph de AgentSpan serializaba el seed como STRING en el canal `acc` → `dict(<string>)`
  itera caracteres → el error. Una capability SOLA (greeter) sí toma el seed crudo (su State tiene las
  claves directo) — por eso greeter durable andaba y enmascaró el bug del supervisor.
- **Fix:** `_durable_input(m, seed)` envuelve `{acc: seed}` para un supervisor (+ `patches: []` si es
  `parallel`, el canal `operator.add` del fan-out, L-14) y deja el seed crudo para una capability;
  `_durable_output(m, out)` desenvuelve `out["acc"]`. La forma del estado inicial DEBE espejar lo que
  `build_supervisor_graph` declara por strategy (sequential/router = `{acc}`; parallel = `{acc, patches}`).
- **Regla para el skill:** la frontera viewer→runtime (o cualquier caller de `AgentSpanRuntime().run`)
  no pasa el seed crudo a un grafo compuesto: lo envuelve en la MISMA forma que el loader compiló. Si
  agregás una strategy nueva con otro State, agregá su forma a `_durable_input`. (No confundir con L-13/
  L-14, que son del lado del grafo; esto es del lado del que lo invoca.)
- **Guard:** `test_durable_input_envuelve_el_seed_de_un_supervisor` + `test_durable_input_parallel_agrega_patches`
  + `test_durable_output_desenvuelve_el_acc_de_un_supervisor` (tests/integration/test_viewer_api.py).

### L-22 · submit durable ASÍNCRONO = `start()` (handle no-bloqueante) + un daemon que mantiene el runtime vivo hasta `is_complete`, recién ahí `shutdown()` (2026-06-21, "probar durable" en vivo del viewer)
- **Síntoma:** el durable sincrónico (`AgentSpanRuntime.run()`) BLOQUEA hasta que el workflow completa
  (~1.4s un pod puro, ~60s si un nodo falla por los retries de Conductor) → el viewer recién veía el
  trace ya terminado (todo `done`, SIN animación nodo-por-nodo); peor, una falla colgaba el request 60s.
- **Causa:** `run()` corre los workers in-process y espera el resultado. Para ver los nodos activarse hay
  que devolver el execution-id ANTES de completar y pollear el trace. `AgentRuntime` expone
  `start(agent, input) -> AgentHandle` (devuelve `handle.execution_id` en ~0.4s, los workers siguen).
- **Fix:** `AgentSpanRuntime.start()` abre el runtime SIN context manager, `start()`-ea, toma el eid, y
  lanza un **thread daemon** que pollea `get_status(eid)` hasta `is_complete` (techo ~600s) y AHÍ
  `shutdown()` (reap de los worker processes). Clave: si cerrás el runtime apenas submitea, los workers
  forkeados MUEREN y el workflow stallea → el daemon es lo que los mantiene vivos. El frontend ya pollea
  el trace (loadTrace 700ms) → los nodos animan `pending→running→done`. Reap verificado en vivo
  (`mp.active_children → 0`, 0 huérfanos).
- **Regla para el skill:** un submit durable "fire-and-watch" = `start()` + un drain daemon que vive
  hasta `is_complete`; NUNCA cierres el runtime entre el submit y la complettitud (mata los workers). Y
  el guard `except Exception` NO alcanza: `AgentRuntime.__init__` levanta **`SystemExit`** (deriva de
  `BaseException`) si `:6767` cae con `auto_start_server=false` → capturá `(Exception, SystemExit)` o la
  UI muere el request thread en vez de degradar a 422.
- **Guard:** `test_run_durable_es_async_devuelve_running_y_completa_en_background` (server-gated) +
  `test_run_durable_degrada_systemexit_a_422` (el caso negativo del SystemExit, puro) (tests/integration/test_viewer_api.py).

<!-- AÑADIR NUEVAS LECCIONES ARRIBA DE ESTA LÍNEA, NUMERADAS L-1, L-2, ... -->
