# Hubara Studio — Plan: llevar los dos viewers a VS Code como extensión

> Doc de planificación (2026-07-07). Objetivo: que VS Code sea la central de
> desarrollo del proyecto — grafos, ejecuciones, certificaciones y gates
> integrados nativamente, sin ir a producción a probar.
>
> Carpeta de desarrollo: **`vscode-hubara/`** en la raíz del monorepo
> (versionada acá, autocontenida, exportable después como repo propio).

---

## 0. Qué existe hoy (inventario verificado)

Dos viewers, **ambos ya sobre React Flow (`@xyflow/react` v12)** — esto es la
convergencia que hace viable UN solo canvas compartido:

| | GraphAgents viewer | System explorer (principal) |
|---|---|---|
| Frontend | `GraphAgents/viewer/index.html` (SPA single-file, React 18 + htm + dagre + React Flow **por CDN/ESM**, sin build) | `system_explorer/` (React 19 + Vite + React Flow + ELK + TanStack Query + Zod) |
| Backend | `GraphAgents/viewer/server.py` — stdlib `http.server` en :8900. Core portable: `api_route(method, path, params, body, ga_root) -> (status, payload)` (server.py:239), aislado del socket | Plugin `hubara_agency/src/plugins/system_map/` — FastAPI `GET /api/system-map/graph`. Core portable: `build_system_graph()` en `domain/builder.py` |
| Grafo | `GraphAgents/sdk/graph.py:build_graph()` — nodos tool/agent/port, edges uses/agent/consumes, desde `manifests/*.yaml` + `tools/*/tool.yaml` | `builder.py` — nodos plugin/frontend_unit/api_router/worker/task_queue, edges depends_on/invokes_worker/consumes_queue/uses_api/belongs_to, desde `frontend_dashboard/src/plugins/<id>/plugin.yaml` + code-scan `get_task_queue(...)` |
| Certs | `/api/checks` → `sdk/inspect.py:system_checks` (TCK C0–C3 + reglas G-*/T-* por nodo y arista) | `certifications[]` por plugin en vivo (`domain/certification.py` → `src.sdk.testkit.run_conformance`) |
| Ejecución | replay determinista (`/api/replay`, golden vs output), run durable AgentSpan (`/api/run-durable` + `/api/trace` + `/api/flow-trace` con estado por nodo: done/running/failed, ms, retries) | — (read-only) |
| Edición | connect/disconnect agente↔tool con gates G-BIND/G-WIRE/G-CONTRACT + **rollback byte-idéntico** (`sdk/manifest_edit.py`), save/publish (git+gh) | — (V1 read-only) |
| CLI | `python3 -m sdk.cli` — check, certify, certify-tool, graph --format json, cases --check, run/start/status/resume | `uv run python -m src.sdk.cli` — check, certify (reportes JSON a `.hubara/certification/`), explain, graph --format json, create plugin |
| Gates | `tests/{architecture,conformance,graphs,tools,integration}` vía pytest; panel `run-gates.sh` | `pytest -m architecture`, `lint-imports`, `pytest tests/conformance`, `pytest tests/plugins/` |

Detalles que ya juegan a favor del port:
- `GraphAgents/sdk/inspect.py` ya emite `abspath` y el frontend arma links `vscode://file/...` — la intención IDE ya estaba.
- Ambos backends tienen el core **separado del transporte HTTP** — se pueden invocar sin levantar servidor.
- Ambos manifests están modelados en **pydantic** (`GraphAgents/sdk/manifest_model.py`, `hubara_agency/src/sdk/manifest_model.py`) → se puede generar JSON Schema para validación YAML en el editor.

---

## 1. Decisión de arquitectura (la pregunta webview vs "nativo")

### 1.1 Qué significa "nativo" en VS Code — y qué no existe

VS Code no tiene API nativa de canvas/grafo. Las superficies nativas son:
árboles (TreeView), Testing API, Problems/Diagnostics, CodeLens, Tasks,
status bar, decoraciones de archivos, custom editors y webviews. **Un grafo
interactivo solo puede vivir en una webview** — no hay alternativa más
"nativa" para eso. La estrategia correcta no es elegir webview *o* nativo,
sino **repartir**: canvas en webview, TODO lo demás en APIs nativas.

### 1.2 Sin servidor — ni Node extra ni HTTP

Preocupación del operador: "evitar cargar un server node". Respuesta:

- **La extensión corre en el extension host de VS Code** (Node que VS Code ya
  tiene levantado). No se agrega ningún proceso servidor.
- **La webview se sirve estática** desde los recursos de la extensión
  (`webview.asWebviewUri`). No hay `http.server` :8900 ni FastAPI :8000 ni
  Vite :5175 para ver los grafos.
- **Los datos vienen por un puente stdio a Python** (JSON-lines sobre
  stdin/stdout): la extensión spawnea el MISMO Python que ya corre los CLIs,
  invocando `api_route(...)` / `build_system_graph()` directo. Sin puertos,
  sin CORS, sin lifecycle de servidor. Si el proceso muere, se relanza.
- Excepción única: **AgentSpan/Conductor (:6767)** sí es un servicio externo
  real (runtime durable) — la extensión le habla por fetch desde el extension
  host, igual que hoy lo hace `server.py`, y degrada limpio si está caído.

**Por qué NO reimplementar la lógica en TypeScript**: `build_graph`,
`system_checks`, `manifest_edit` (con su rollback), el TCK y los gates son la
fuente de verdad en Python y evolucionan con el proyecto. Duplicarlos en TS =
drift garantizado. El puente los reusa tal cual; la extensión es una capa de
presentación + orquestación.

### 1.3 UI toolkit — estado 2026

⚠️ El **Webview UI Toolkit oficial de Microsoft está DEPRECADO** (sunset
1-ene-2025, repo archivado). No usarlo. Stack elegido:

| Pieza | Elección | Por qué |
|---|---|---|
| Canvas | `@xyflow/react` v12 **bundleado local** (esbuild) | Es lo que ya usan ambos viewers; CSP de webviews bloquea CDN → vendorizar |
| Componentes de panel/forms | `@vscode-elements/elements` | El sucesor comunitario recomendado del toolkit deprecado; look&feel nativo, theme-aware |
| Iconos | `@vscode/codicons` | Set oficial, gratis en webviews |
| Theming | Variables CSS `--vscode-*` | La webview hereda el theme del usuario automáticamente |
| Layout de grafos | dagre (GraphAgents) + elkjs (system map) — bundleados | Preservar el layout que cada grafo ya tiene |
| Estado webview↔extensión | `postMessage` + `acquireVsCodeApi().setState` + `workspaceState` | Reemplaza el localStorage de system_explorer para persistir posiciones |

### 1.4 Una extensión, dos proveedores de grafo

**Una sola extensión** (`hubara-studio`) con una webview-app compartida y dos
*data providers* (GraphAgents | System Map). No dos extensiones:
- comparten el 80% (canvas, panels, bridge, testing controller);
- el usuario final es uno (vos) y el destino de export es un solo VSIX;
- si algún día se separa, la frontera ya queda marcada por el provider.

---

## 2. Mapa funcionalidad → superficie de VS Code

| Funcionalidad de hoy | Superficie VS Code | Cómo |
|---|---|---|
| Canvas de grafos (ambos) | **WebviewPanel** (`hubara.openGraph`, `hubara.openSystemMap`) | React Flow bundleado; datos por bridge; `retainContextWhenHidden` |
| Certificaciones TCK C0–C3 "como tests" | **Testing API (`TestController`)** | Árbol: GraphAgents (por agente/tool/case) + Hubara (por plugin/gate). Run profile → spawns CLI/pytest → resultados verde/rojo en el Test Explorer nativo, con historia y gutter icons |
| "Certificados de compilación" | **Diagnostics (Problems panel)** on-save | Al guardar un manifest: correr `check` → parsear `error[P-x]`/`error[G-x]` → squiggles sobre el YAML + Problems. Es literalmente la experiencia compilador que pediste |
| Validación de manifests mientras escribís | **JSON Schema + extensión YAML de Red Hat** | Generar schemas desde los pydantic (`model_json_schema()`) → `yaml.schemas` → autocomplete + validación inline en `plugin.yaml` / `*.taskgraph.yaml` / `tool.yaml` |
| Descripciones/info de nodos | **Webview side panel** + **hovers/TreeView tooltips** | El mismo `description`/`contract` que hoy muestra el modal; glosario (`/api/legend`) como walkthrough/hover |
| Ejecutar caso golden desde el archivo | **CodeLens** en manifests y en `fixtures/cases/*.case.yaml` | "▶ Replay | ▶ Durable | ✓ Certify | ⌂ Ver en grafo" |
| Runs vivos + trace por nodo | **TreeView "Runs"** + overlay en el canvas | Poll a Conductor desde el extension host; al click en un run, el canvas pinta done/running/failed/ms/retries (mismo `FlowNode` de hoy) |
| Dónde falla la ejecución | Overlay en canvas + **Diagnostics** | Nodo rojo en el grafo + entrada en Problems apuntando a la capability/tool que falló |
| Edit mode (conectar/desconectar) | Webview (drag entre nodos) → bridge → `manifest_edit.py` | El gate G-* y el rollback quedan en Python (fuente única); la webview solo pide `validate-connection` para el feedback inmediato |
| Save/Publish a producción | **Command + SCM** | `hubara.publish` → bridge → `sdk/production.py` (git+gh); estado dirty en status bar |
| Gates (`/graphagents-gates`, `/hubara-gates`) | **Tasks** con problem matchers + Testing API | Cada gate = task detectable (`tasks.json` contribuido); los que son pytest también viven en el Test Explorer |
| Nivel de cert por plugin visible siempre | **FileDecorations + StatusBar** | Badge C0/C1/C2 sobre `src/plugins/<id>/` y sobre los manifests; status bar con el agregado |
| Huérfanos / warnings del system map | **Diagnostics** + sección en TreeView | `orphan_detector` ya clasifica; cada huérfano → warning sobre su plugin.yaml |
| Abrir archivo de un nodo | `vscode.open` vía postMessage | Reemplaza los links `vscode://file/` (dentro de la extensión es directo) |

---

## 2.5 Panel de ejecución estilo Xcode — bundles / test plans

Concepto pedido por el operador: el Test Navigator de Xcode (panel lateral
con las suites, run con ▶, debugger integrado) + los **schemes/test plans**
de Xcode (elegir QUÉ bundle se ejecuta antes de correr). Mapa 1:1 a VS Code:

| Concepto Xcode | Equivalente VS Code (nativo) |
|---|---|
| Test Navigator (panel lateral con jerarquía y ▶ por nodo) | **Test Explorer** — lo puebla nuestro `TestController`; progreso en vivo, duración por item, historia, re-run failed, gutter icons en los archivos |
| Scheme / Test Plan (`.xctestplan` versionado en el repo) | **Archivos de plan propios** `*.hubaraplan.yaml` (versionados, schema-validado con la maquinaria de F2) → cada plan se registra como **`TestRunProfile`** |
| Selector de scheme en la toolbar | **Status bar item** `$(beaker) Plan: <activo>` → QuickPick para cambiar de plan / "Editar plan…" abre el YAML |
| Elegir bundles a probar (checkboxes del test plan) | `include:` del plan por **tags** o suites concretas; además el Test Explorer nativo ya permite correr cualquier selección arbitraria |
| Test con debugger (breakpoints) | **Run profile `Debug`**: pytest bajo `debugpy` (extensión Python) para todo lo backend/GraphAgents; vitest bajo node-debug para frontend. Breakpoints reales en el TCK, en capabilities, en tools |
| Code coverage de Xcode | **Run profile `Coverage`** (Testing API ≥1.88): `pytest --cov --cov-report=json` / vitest v8 → cobertura pintada en el editor |
| Test on save / continuous | **`supportsContinuousRun`**: al guardar, re-corre los items afectados (misma heurística que los hooks `post-edit-affected-tests-*.sh` ya existentes) |

### El inventario de suites (los "bundles" atómicos)

Un solo `TestController` con items taggeados por dimensión (`lado`:
graphagents/backend/frontend · `tipo`: compiler/cert/arch/golden/tools/
integration/unit). Todos son comandos que YA existen:

**GraphAgents** (cwd `GraphAgents/`, `python3`):
| Suite | Comando | Granularidad en el árbol |
|---|---|---|
| compiler (manifests) | `python3 -m sdk.cli check` | por manifest |
| cert TCK C0–C3 | `python3 -m pytest tests/conformance -q` | por agente |
| tools | `python3 -m sdk.cli certify-tool` + `pytest tests/tools -q` | por tool |
| arquitectura G-* | `pytest tests/architecture -q` | por regla/test |
| golden replays (G-DET) | `pytest tests/graphs -q` + replay por caso vía bridge | **por caso** (`fixtures/cases/*.case.yaml`) |
| integration | `pytest tests/integration -q` | por test |

**Hubara backend** (cwd `hubara_agency/`, `uv run`):
| Suite | Comando | Granularidad |
|---|---|---|
| compiler | `python -m src.sdk.cli check` | por plugin (diagnósticos `error[P-x]`) |
| cert TCK C0–C3 | `python -m src.sdk.cli certify` / `pytest tests/conformance -q` | por plugin |
| arquitectura | `pytest -m architecture` + `lint-imports` | por ratchet/contrato |
| invariantes plugins | `pytest tests/plugins/` | por test |
| unit (opcional en planes) | `pytest` | por test |

**Frontend** (cwd `frontend_dashboard/`, npm):
| Suite | Comando | Granularidad |
|---|---|---|
| arquitectura FSD | `npm run test:arch` | por regla |
| tipos | `npx tsc --noEmit` | diagnostics |
| unit | vitest | por test |
| build | `npm run build` | pass/fail |

### Los planes (bundles compuestos, estilo `.xctestplan`)

Archivos descubiertos por glob `**/*.hubaraplan.yaml` (default en
`.hubara/test-plans/`), con JSON Schema propio (autocomplete + validación
inline, misma maquinaria de F2). Ejemplo:

```yaml
# .hubara/test-plans/pre-pr.hubaraplan.yaml
name: Pre-PR completo
description: Equivalente a /hubara-gates all + /graphagents-gates all
include:
  - tag: compiler
  - tag: cert
  - tag: arch
  - suite: graphagents/graphs        # golden replays completos
  - suite: frontend/build
exclude:
  - suite: backend/unit              # demasiado lento para este plan
options:
  failFast: false
  parallel: true                     # suites de cwd distinto en paralelo
```

Planes default que la extensión trae de fábrica (editables/clonables):
1. **Compilador** — checks de manifests ambos lados (segundos; el smoke).
2. **Certificación C2** — TCK completo GraphAgents + Hubara.
3. **Arquitectura** — G-* + `pytest -m architecture` + lint-imports + FSD.
4. **Golden replays** — `tests/graphs` + todos los casos replayables.
5. **Pre-PR completo** — todo lo anterior + integration + build.

Cada plan = un `TestRunProfile` (kind Run) + variantes Debug/Coverage donde
aplica. El plan activo se muestra en la status bar (como el scheme de Xcode)
y `▶ Run Tests` sin selección ejecuta el plan activo. Los resultados de una
corrida de plan quedan agrupados como una ejecución (progreso en vivo por
suite, duración, output streameado a un canal por run, fallos con peek-view
en la línea exacta vía junitxml).

**Relación con los gates existentes**: los planes NO reemplazan
`/hubara-gates` ni `run-gates.sh` — invocan los mismos comandos. Un plan es
solo la vista IDE del mismo panel determinístico; si un día cambia un gate,
cambia en un solo lugar (el comando) y el plan lo hereda.

---

## 2.6 Scopes de grafo — el concepto project/target de Xcode en el canvas

El mismo concepto de bundles aplica a la VISUALIZACIÓN: antes de mirar,
elegís el alcance, como elegir project/target en Xcode. Jerarquía de scopes:

```
Workspace (todo)                         ← "el project completo"
├── Sistema principal (system map)       ← "un project del workspace"
│   └── Focus: plugin <id>               ← "un target": el plugin + sus conexiones
│       └── Focus: worker/router/unit    ← nodo + vecindad
└── GraphAgents                          ← "el otro project"
    └── Focus: agente <id>               ← el agente + sus tools/ports/sub-agentes
        └── Focus: tool <id>             ← la tool + quién la usa
```

**Comportamiento por scope:**

| Scope | Qué se ve |
|---|---|
| `workspace` | AMBOS grafos completos en un canvas, cada sistema como un "swimlane"/cluster propio, + las **costuras cross-sistema** (ver abajo) como edges entre clusters |
| `system:hubara` | Solo el system map (plugins, workers, routers, queues) — lo que hoy muestra `system_explorer/` |
| `system:graphagents` | Solo el grafo GraphAgents (agentes, tools, ports) — lo que hoy muestra el viewer :8900 |
| `focus:<node>` | **Ego-graph**: el nodo elegido + su vecindad a N saltos (slider de profundidad 1–3, default 1). Ej.: `focus:agent:ctwa-report` = ese agente, sus tools, sus ports y el supervisor que lo invoca — nada más |

**Cómo se elige el scope** (tres vías, consistentes con el selector de plan):
1. **Toolbar del canvas**: breadcrumb clickeable `Workspace ▸ GraphAgents ▸
   agent:ctwa-report` + QuickPick (mismo patrón que el selector de plan).
2. **TreeView lateral**: click en un plugin/agente/tool → "Ver en grafo"
   abre/enfoca el canvas en ese scope.
3. **Doble-click en un nodo del canvas** → baja a su focus (drill-down);
   breadcrumb para volver a subir.

El scope activo se persiste por panel en `workspaceState` (volvés a abrir el
canvas y está donde lo dejaste). Las posiciones guardadas son POR SCOPE (el
layout del workspace no pisa el layout del focus de un agente).

**El grafo unificado del workspace (merge):**
- Los dos providers ya emiten shapes React Flow-compatibles; el merger de la
  extensión namespacea ids (`hub:plugin:chats`, `ga:agent:ctwa-report`) y
  agrupa cada sistema en un cluster/subflow colapsable.
- **Costuras cross-sistema**: hoy ningún builder emite edges entre sistemas,
  así que arrancan DECLARADAS en `vscode-hubara/seams.yaml` (fuente
  explícita, honesta) — las conocidas: plugin `reengagement` → puente
  POLL-based a GraphAgents (graphagentskit), plugin `ads` → pod
  `ads-analytics`, HITL bridge dashboard→GraphAgents. Candidato F7:
  auto-detectarlas escaneando usos de `graphagentskit`/execution-ids, igual
  que el system map ya escanea `get_task_queue(...)`.
- Colapsar un cluster lo reduce a un solo nodo-sistema con las costuras
  visibles — la vista "de pájaro" del negocio entero.

**Sinergia scopes ↔ test plans**: desde un scope enfocado, "▶ Testear este
scope" arma una corrida con las suites del nodo (el TCK del agente enfocado,
los golden cases cuyo `target` es ese nodo, el `check` de ese plugin). Es el
cierre del loop Xcode: elegís el target, lo ves, lo corrés, lo debuggeás.

---

## 3. Estructura de la carpeta `vscode-hubara/`

```
vscode-hubara/
├── README.md                    # cómo desarrollar, F5-debug, empaquetar
├── package.json                 # manifest de la extensión (contributes.*)
├── tsconfig.json
├── esbuild.mjs                  # bundle extension (node) + webview (browser)
├── .vscodeignore
├── src/                         # extension host (TypeScript)
│   ├── extension.ts             # activate(): registra todo
│   ├── bridge/
│   │   ├── pythonBridge.ts      # spawn + JSON-lines RPC, relaunch, timeout
│   │   └── endpoints.ts         # tipos de los payloads (espejo de contracts)
│   ├── graph/
│   │   ├── graphPanel.ts        # WebviewPanel manager (provider param)
│   │   ├── scopes.ts            # modelo de scopes + merger workspace + ego-graph
│   │   └── messages.ts          # protocolo postMessage tipado
│   ├── testing/
│   │   ├── controller.ts        # TestController "Hubara Certifications"
│   │   ├── graphagentsTests.ts  # descubre agentes/tools/cases → test items
│   │   ├── hubaraTests.ts       # descubre plugins/gates → test items
│   │   ├── plans.ts             # descubre *.hubaraplan.yaml → TestRunProfiles
│   │   └── planStatusBar.ts     # selector de plan activo (estilo scheme Xcode)
│   ├── diagnostics/
│   │   └── manifestDiagnostics.ts  # on-save → check → Problems
│   ├── trees/
│   │   ├── catalogTree.ts       # Plugins | Agentes | Tools | Ports | Cases
│   │   └── runsTree.ts          # ejecuciones AgentSpan vivas/históricas
│   ├── codelens/manifestCodeLens.ts
│   ├── decorations/certDecorations.ts
│   └── tasks/gateTasks.ts       # TaskProvider de los gates
├── webview/                     # app del canvas (React + React Flow)
│   ├── src/
│   │   ├── App.tsx              # switch por provider (graphagents|system)
│   │   ├── canvas/              # FlowNode, edges, overlays de trace/certs
│   │   ├── panels/              # inspector de nodo, marketplace, connect-modal
│   │   └── vscodeApi.ts         # postMessage wrapper
│   └── index.css                # variables --vscode-* + estilos RF
├── seams.yaml                   # costuras cross-sistema declaradas (§2.6)
├── schemas/                     # JSON Schemas GENERADOS (no editar a mano)
│   ├── graphagents.taskgraph.schema.json
│   ├── graphagents.tool.schema.json
│   ├── hubara.plugin.schema.json
│   └── hubaraplan.schema.json   # schema de los test plans (§2.5)
├── scripts/
│   └── gen-schemas.py           # pydantic → JSON Schema (ambos SDKs)
└── test/                        # tests de la extensión (@vscode/test-cli)
    ├── bridge.test.ts
    └── testing.test.ts
```

**Puentes Python (mínimos, viven junto a cada subsistema, ~50-80 líneas c/u):**

- `GraphAgents/viewer/bridge.py` — loop stdio JSON-lines que delega en el
  `api_route()` existente. Cero lógica nueva; reusa TODOS los endpoints de la
  tabla de §0 (graph, checks, inspect, cases, replay, run-durable, trace,
  flow-trace, validate-connection, connect, disconnect, save, publish...).
- `hubara_agency/scripts/system_map_bridge.py` — stdio que llama
  `build_system_graph()` + `collect_certifications()` + `orphan_detector`.
  (No toca spinal files; es un script nuevo. Alternativa equivalente:
  agregar `--rich --json` al `graph` de `src.sdk.cli`.)

Protocolo del bridge (una línea por request/response):
```json
→ {"id": 1, "method": "GET", "path": "/api/graph", "params": {}}
← {"id": 1, "status": 200, "payload": {"nodes": [...], "edges": [...]}}
```

**Resolución de Python** (setting con defaults):
- `hubara.graphagents.python`: `python3` con cwd `GraphAgents/`
- `hubara.backend.python`: `uv run python` con cwd `hubara_agency/`
  (el hook pre-bash del repo solo aplica a sesiones Claude, no a la extensión)

---

## 4. Fases de desarrollo

Cada fase termina con un criterio verificable. TDD donde hay lógica
(bridge, parsers, controller); la webview se verifica con el Extension
Development Host (F5).

### F0 — Scaffold + puente (la fundación)
- `vscode-hubara/` con `package.json`, esbuild dual (host+webview), F5 debug.
- `pythonBridge.ts` + `GraphAgents/viewer/bridge.py` + `system_map_bridge.py`.
- Test: bridge responde `/api/health` y `/api/graph` de ambos providers.
- **Criterio: comando "Hubara: Open Graph" abre una webview que lista los
  nodos (aunque sea como texto) de ambos grafos.**

### F1 — Canvas read-only + scopes (paridad visual y modelo project/target)
- Port del canvas: React Flow bundleado, `FlowNode` custom (badges de cert,
  side_effect, archetype), dagre para GraphAgents / ELK para system map,
  minimap, fit, persistencia de posiciones en `workspaceState` (por scope).
- **Scopes de grafo (§2.6)**: breadcrumb + QuickPick (workspace / sistema /
  focus), ego-graph con slider de profundidad, doble-click drill-down,
  merge del workspace con clusters colapsables y `seams.yaml` declarado.
- Inspector de nodo (panel derecho): description, contract, files → click
  abre el archivo en el editor.
- TreeView "Hubara Studio" en la Activity Bar: Plugins / Agentes / Tools /
  Ports / Cases; click → "Ver en grafo" abre el canvas en ese scope.
- **Criterio: elegir workspace y ver AMBOS sistemas con sus costuras;
  elegir un agente y ver SOLO ese agente con sus conexiones — sin ningún
  servidor levantado.**

### F2 — Experiencia compilador (manifests como código)
- `gen-schemas.py` + registro `yaml.schemas` → autocomplete/validación
  inline en `plugin.yaml`, `*.taskgraph.yaml`, `*.agent.yaml`, `tool.yaml`.
- Diagnostics on-save: `sdk.cli check` (ambos lados) → parsear diagnósticos
  (`error[P-27]`, reglas G-*) → Problems + squiggles sobre el YAML.
- CodeLens sobre manifests: "✓ Certify · ⌂ Ver en grafo · ▶ Casos".
- FileDecorations: badge de nivel C0/C1/C2 por plugin/agente.
- Huérfanos del system map → warnings en Problems.
- **Criterio: guardar un manifest roto pinta el error en el editor en <3s,
  sin correr nada a mano. Es el "certificado de compilación".**

### F3 — Panel de ejecución estilo Xcode (diseño completo en §2.5)
- `TestController` con las tres raíces (GraphAgents / Hubara backend /
  Frontend) y el inventario de suites de §2.5, taggeadas por lado y tipo.
- Run profiles que spawnean los comandos existentes; resultados parseados
  (pytest `--junitxml` a archivo temp; CLI por exit code + stdout).
- Fallos navegan al archivo/regla exacta (usar `explain <code>` como detail).
- **Test plans (bundles)**: descubrimiento de `*.hubaraplan.yaml` + schema +
  los 5 planes de fábrica + selector de plan activo en status bar.
- **Debug profile**: pytest bajo `debugpy` (requiere extensión ms-python),
  vitest bajo node-debug — breakpoints en TCK/capabilities/tools.
- **Coverage profile**: `pytest --cov --cov-report=json` / vitest v8 →
  cobertura en editor.
- **Continuous run**: on-save re-corre items afectados (hereda la heurística
  de `post-edit-affected-tests-*.sh`).
- **Criterio: elegir el plan "Certificación C2" en la status bar, apretar ▶
  y ver la corrida suite por suite en el panel lateral con verde/rojo,
  duración e historia — y poder debuggear un TCK con breakpoints.**

### F4 — Ejecución y trace en vivo
- TreeView "Runs": poll a Conductor (`AGENTSPAN_SERVER_URL`, degrade limpio).
- Acciones por caso: **Replay** (determinista, matches vs golden → pass/fail
  también reportado al TestController) y **Run durable** (AgentSpan).
- Overlay de trace en el canvas: estado por nodo (done/running/failed,
  pulso si running, ms, retries), click → estado del acumulador
  (`/api/node-state`) e I/O por tool reconstruido (`/api/flow-trace`).
- Nodo fallado → Diagnostic sobre su capability/tool.
- **Criterio: disparar un caso desde VS Code y VER en el canvas por dónde va
  y dónde falla, sin abrir el navegador.**

### F5 — Edit mode + producción
- Drag agente↔tool en el canvas → modal de binding (validate-connection
  informativo para sugerir el mapeo) → `connect` (gates + rollback en
  Python). Disconnect por menú contextual del edge.
- Marketplace: nodos del catálogo stageados (dashed) hasta conectar.
- `hubara.save` (422 si checks rotos) y `hubara.publish` (branch+commit+PR
  vía `sdk/production.py`); estado dirty en status bar.
- **Criterio: componer un supervisor completo desde VS Code y salir con un
  PR, con los mismos gates que hoy.**

### F6 — Empaquetado y export
- `vsce package` → VSIX instalable; walkthrough de onboarding (glosario).
- README con el corte de export: la carpeta es autocontenida SALVO los dos
  bridges Python (documentar que viven con cada subsistema a propósito —
  son la fuente de verdad) y un setting `hubara.roots` para apuntar la
  extensión a cualquier checkout.
- Opcional: CI job que empaqueta el VSIX en cada release.
- **Criterio: instalar el VSIX en un VS Code limpio y que funcione contra
  el monorepo clonado.**

**Orden y tamaño**: F0+F1 son el grueso del riesgo técnico (bridge + canvas)
— empezar por ahí. F2 y F3 son casi independientes entre sí (paralelizables).
F4 depende de F1; F5 de F1+F2. Estimación gruesa: F0-F1 ~3-4 sesiones,
F2 ~1-2, F3 ~2, F4 ~2, F5 ~2-3, F6 ~1.

---

## 5. Riesgos y decisiones ya tomadas

| Riesgo | Mitigación / decisión |
|---|---|
| CSP de webviews bloquea CDN (el viewer GraphAgents hoy carga React por ESM remoto) | Bundlear TODO local con esbuild (`localResourceRoots` + nonce). Es el cambio más grande del port del canvas |
| Toolkit oficial deprecado | `@vscode-elements/elements` + codicons + variables `--vscode-*` (decidido, §1.3) |
| Duplicar lógica Python en TS → drift | Prohibido: toda regla/gate/edición pasa por el bridge. La extensión no sabe qué es G-BIND, solo muestra el resultado |
| TCK del system map es caro (corre `run_conformance` por plugin por request) | El bridge cachea por mtime de manifests; refresh explícito + on-save selectivo |
| Dos Pythons distintos (GraphAgents `python3` puro vs hubara `uv run`) | Settings separados por provider con defaults correctos (§3) |
| AgentSpan caído | Igual que hoy: 502 → la UI degrada (runs tree vacío con hint), nunca crashea |
| Webview pierde estado al ocultarse | `retainContextWhenHidden: true` en los paneles de canvas + `setState` como backup |
| Manifests editados fuera de la extensión | FileSystemWatcher sobre `manifests/**`, `tools/**`, `frontend_dashboard/src/plugins/**/plugin.yaml` → invalida cache + re-check |
| Gap ya conocido del system map (no grafica `owns_route` ni casts HTTP) | Fuera de alcance del port (es feature del builder, no de la extensión). Anotado como F7 candidato: extender `builder.py` y la extensión lo hereda gratis |

## 6. Qué NO hacemos (alcance negativo)

- No reescribir `build_graph`/`system_checks`/`manifest_edit` en TypeScript.
- No mantener los viewers web viejos en paralelo indefinidamente: quedan como
  fallback hasta F4; después, el de GraphAgents puede quedar solo para Docker
  (demo sin VS Code) y `system_explorer/` se congela.
- No custom editor como superficie principal (abrir `.taskgraph.yaml` "como
  grafo" por default) — evaluar recién post-F5; el WebviewPanel + CodeLens
  cubre el flujo sin secuestrar el editor de texto.
- No publicar al Marketplace en F6 — VSIX privado primero.
