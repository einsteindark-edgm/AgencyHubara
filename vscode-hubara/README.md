# Acktos Studio — extensión de VS Code

Convierte VS Code en la central de desarrollo de AgencyHubara + GraphAgents:
grafos de arquitectura, certificaciones (TCK C0–C3) como tests nativos,
ejecuciones y trace, todo sin levantar servidores ni ir a producción.

Plan completo y decisiones de arquitectura: [`../VSCODE_STUDIO_PLAN_fable.md`](../VSCODE_STUDIO_PLAN_fable.md).

## Estado

**F0 — scaffold + puente (hecho).** Puente stdio a Python (sin servidor HTTP),
bridge TS con relanzamiento/backoff/handshake-timeout.

**F1 — canvas + scopes (hecho).** Canvas React Flow bundleado (dagre para
GraphAgents, ELK para System Map), scopes workspace/system/focus con
breadcrumb + drill-down + persistencia por scope (`workspaceState`), merge del
workspace con costuras declaradas (`seams.yaml`), TreeView de catálogo,
Inspector con click-to-open-file.

**F3 — panel de tests estilo Xcode (hecho).** `TestController` con las suites
de GraphAgents/Backend/Frontend (espejo 1:1 de `/hubara-gates` y
`/graphagents-gates`), granularidad por-test vía JUnit XML, test plans
(`test-plans/*.hubaraplan.yaml` — el equivalente a un `.xctestplan`) con
selector en la status bar.

**F2 — experiencia compilador (hecho).** JSON Schemas generados DESDE los
modelos pydantic reales (`scripts/gen-schemas.mjs`) + validación inline vía
`redhat.vscode-yaml`; Diagnostics on-save (parsea el output real de `check` —
formato rustc en Hubara, un-liner en GraphAgents — a Problems panel, misma
suite que corre el Test Explorer); CodeLens "⌂ Ver en grafo · ✓ Check" sobre
cada manifest; FileDecorations con el nivel de cert (C0–C3) en el Explorer.

**F4 — ejecución y trace en vivo (hecho).** TreeView "Runs" (poll a
`/api/runs`, degrada limpio si AgentSpan `:6767` está caído); "▶ Replay"
(determinista, LocalRuntime — reporta pass/fail al Test Explorer con
historia real) y "▶ Run Durable" por caso desde el CatalogTree; el canvas
overlaya el estado por-nodo en vivo (done/running/failed/awaiting, ms,
retries — poll a `/api/trace`) y un nodo failed deja un Diagnostic sobre su
archivo (vía `/api/inspect`, la misma fuente que el Inspector de F1).

**F5 — edit mode + producción (hecho).** Drag-connect y right-click-disconnect
en el canvas (solo GraphAgents, scope system/focus) + picker "+ Conectar
desde…" en el CatalogTree — misma secuencia validate→confirm→mutate en
`editOps.ts`, gate + rollback en `sdk/manifest_edit.py`, confirmación
SIEMPRE por diálogo nativo (los webviews de VS Code no soportan
`window.confirm`). `acktos.saveProduction`/`publishProduction` + status bar
`dirty`/`saved` (`/api/production-status`); publish pide confirmación
explícita (commit+push+PR).

**F6 — empaquetado y export (hecho).** VSIX real, verificado (`npm run vsix`
→ 21 archivos, ~645KB comprimido); walkthrough de onboarding en 4 pasos
(scopes, test plans, certificaciones, edit mode); `repository` apuntando al
monorepo con `directory: vscode-hubara`.

**F7 — detalle de ejecución + workflows + palette (hecho, post-feedback).**
Del pase visual real salieron 3 fixes y 2 features:
- **Detalle de ejecución completo**: con un trace activo (click en un run de
  Runs, o "▶ Run Durable"), el Inspector gana pestañas **entró/salió** por
  nodo (el `acc` real de Conductor vía `/api/node-state`, lazy), la
  **sub-ejecución por-tool** reconstruida (`/api/flow-trace`, replay
  determinista G-DET) con I/O expandible por llamada, y la pestaña «salió»
  del nodo raíz = **la respuesta final del workflow** (narrativa destacada).
- **Scope `workflow`**: grupo **Workflows** en el catálogo (las raíces de los
  flujos conectados); al elegir uno el canvas dibuja SOLO ese workflow
  (clausura dirigida desde la raíz). Los runs abren directo en este scope.
- **Palette drag-and-drop** (edit mode): catálogo arrastrable — soltar un
  tool/agente SOBRE un agente lo conecta (mismo gate validate→confirm→mutate).
- **Desconectar por click**: seleccionar una arista muestra la barra
  "✕ desconectar" (el context-menu sigue funcionando; hit-area de 24px).
- **Fix del parpadeo**: los nodos viven en estado local del canvas (patrón
  uncontrolled de React Flow) — un drag ya no reconstruye todos los nodos por
  frame ni re-monta los labels de las aristas; las posiciones se persisten
  UNA vez al soltar.

**F8 — ejecución local sin infraestructura (hecho, post-feedback).** El
runtime durable (AgentSpan `:6767`) es **opcional** para probar un flujo:
- **⚡ Ejecutar (local)** — el click default en un case: corre el flow EN
  PROCESO con los fixtures del caso (`POST /api/run-local`, nuevo — mismo
  `run()` por nodo que el durable, determinista G-DET) y muestra el detalle
  completo en el canvas: nodos, entró/salió por nodo (accs pre-calculados),
  sub-ejecución por-tool y output final. ~600ms, cero infraestructura —
  local hoy, AWS mañana.
- **Runs = solo esta sesión**: la vista default lista lo que ejecutaste acá
  (⚡ locales + ▶ durables lanzados); el histórico completo de AgentSpan se
  abre con el toggle ⟲. AgentSpan caído ya no es un error si trabajás local.
- **▶ Iniciar AgentSpan** (ícono en Runs, o desde la paleta): levanta la
  infra durable en una terminal visible (`agentspan server start`,
  configurable vía `acktos.graphagents.agentspanStart`). Si un Run Durable
  falla, el toast ofrece "Iniciar AgentSpan" y "Ejecutar local".
- **Infra remota por settings**: `acktos.graphagents.env` inyecta env al
  puente — `AGENTSPAN_SERVER_URL`/`LITELLM_PROXY_URL` apuntando a AWS sin
  tocar código.

Todas las fases del plan (F0–F8) están implementadas.

## Arquitectura (sin servidor)

- La extensión corre en el extension host de VS Code (Node ya provisto).
- La webview se sirve estática desde `dist/` (`asWebviewUri`) — sin puertos.
- Los datos llegan por un **puente stdio JSON-lines** a Python, que reusa el
  MISMO código que los viewers web:
  - GraphAgents → [`GraphAgents/viewer/bridge.py`](../GraphAgents/viewer/bridge.py) → `api_route()` (todos los endpoints del explorer).
  - System Map → [`hubara_agency/scripts/system_map_bridge.py`](../hubara_agency/scripts/system_map_bridge.py) → `build_system_graph()` + `collect_certifications()`.
- Única dependencia externa dura: AgentSpan/Conductor (`:6767`) para runs
  durables — degrada limpio si está caído.

**Ningún endpoint/gate/regla se reimplementa en TypeScript**: la fuente de
verdad vive en Python y el puente la reusa. La extensión es presentación +
orquestación.

## Desarrollo

```bash
cd vscode-hubara
npm install
npm run build          # o: npm run watch
```

Luego F5 en VS Code (config "Run Acktos Studio (Extension)") — abre un
Extension Development Host con el monorepo como workspace. Corré el comando
**Acktos: Open Graph** desde la paleta.

### Requisitos del runtime

- `python3` disponible para GraphAgents (subsistema aislado, NO usa `uv`).
- `uv` disponible para el backend Hubara (`uv run python`).
- Extensión `redhat.vscode-yaml` instalada (opcional — sin ella, los
  manifests siguen funcionando, solo sin autocomplete/validación inline).
- Ambos configurables en Settings → Acktos Studio si tu setup difiere.

### Config

| Setting | Default | Qué es |
|---|---|---|
| `acktos.graphagents.python` | `python3` | Python del puente GraphAgents |
| `acktos.graphagents.cwd` | `${workspaceFolder}/GraphAgents` | raíz de GraphAgents |
| `acktos.backend.command` | `["uv","run","python"]` | comando Python del backend |
| `acktos.backend.cwd` | `${workspaceFolder}/hubara_agency` | raíz del backend |
| `acktos.backend.env` | `{MEDUSA_BASE_URL,MEDUSA_ADMIN_TOKEN}` dummy | env que necesita `pytest -m architecture` a nivel de import |
| `acktos.frontend.cwd` | `${workspaceFolder}/frontend_dashboard` | raíz del dashboard |
| `acktos.frontend.npm` / `.npx` | `npm` / `npx` | ejecutables del lado frontend |
| `acktos.seamsPath` | `""` | override de `seams.yaml`; vacío = `<repoRoot>/seams.yaml` si existe, o el bundled |

Todas las settings se leen desde un único módulo (`src/config.ts`) — puentes,
Test Explorer y diagnostics no pueden driftear entre sí.

## Schemas (`schemas/*.schema.json`)

Generados DESDE los modelos pydantic reales — nunca a mano:

```bash
node scripts/gen-schemas.mjs   # regenera los 3 *.schema.json desde AgentNode/ToolContract/PluginManifest
```

Corré esto de nuevo si esos modelos cambian (nuevo campo, nueva regla) —
si no, el schema queda desincronizado del validador real.

## Test plans (`test-plans/*.hubaraplan.yaml`)

5 planes de fábrica — Compilador, Certificación C2, Arquitectura, Golden
replays, Pre-PR completo. Cada uno es un `TestRunProfile`; el activo se elige
desde la status bar (`$(beaker) Plan: …`) o desde el picker nativo de
profiles del Test Explorer. Agregar un plan propio = un YAML nuevo en
`test-plans/` con `include`/`exclude` por `tag` (lado o tipo de suite) o
`suite` (id exacto) — ver `schemas/hubaraplan.schema.json` y
`src/testing/suites.ts` para el catálogo.

## Empaquetar (VSIX)

```bash
npm run vsix   # vsce package --no-dependencies → acktos-studio-0.0.1.vsix
```

`--no-dependencies` es correcto acá: esbuild ya bundlea TODO lo runtime
(`dist/extension.js`, `dist/webview.js`) — vsce no necesita reinstalar
`node_modules` en el paquete. El VSIX incluye `dist/`, `schemas/`,
`test-plans/`, `seams.yaml`, `walkthrough/`, `package.json`, `readme.md` —
nada de `src/`/`webview/` fuente ni `scripts/` (dev-only). Instalar:
`code --install-extension acktos-studio-0.0.1.vsix` (o "Install from
VSIX…" en la UI de Extensions).

## Export

La carpeta es autocontenida salvo los dos puentes Python, que viven a
propósito junto a cada subsistema (son la fuente de verdad, no deben
duplicarse). Para exportar como repo propio: llevar `vscode-hubara/` + los dos
`*bridge.py`, y apuntar los `cwd` a los checkouts destino.
