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

**F9 — visualización del orden de ejecución (hecho, post-feedback).**
- **Badges de orden en el canvas**: con un trace activo, cada cajita muestra
  su número — agentes `1, 2, 3…` (posición en el plan) y tools `2.1 · 3.2`
  (orden real dentro de cada agente que la invocó, del ledger reconstruido).
- **Runs en cascada** (estilo test navigator de Xcode): cada run se despliega
  en sus agentes en orden de ejecución (con estado/ms/retries) y cada agente
  en sus tools en orden — locales al instante (la cascada viaja con el
  resultado), durables lazy al expandir (`/api/trace` + `/api/flow-trace`).
  Click en cualquier nivel enfoca ese nodo en el grafo.
- **Entró/salió confrontado**: una sola vista con el diff estructural del
  acc — verde = agregado/cambiado por este nodo, rojo = removido; claves
  idénticas en gris. Si los dos lados no comparten estructura, se muestran
  planos (el diff no aporta). En el nodo raíz confronta seed → respuesta
  final del workflow.

**F10 — panel nativo "Ejecución" + canvas a pantalla completa (hecho,
post-feedback).**
- **Panel "Ejecución"** (abajo, junto a Terminal — arrastrable al sidebar
  derecho, VS Code lo recuerda): la CASCADA del run (agentes en orden →
  tools en orden, colapsables) + el detalle del nodo seleccionado (resumen,
  files, entró/salió confrontado). Se puebla al ejecutar un case (⚡/▶) y al
  tocar cualquier nodo del canvas; click en la cascada selecciona el nodo;
  «⌂ enfocar» lo abre en el grafo.
- **El canvas usa toda la pantalla**: el Inspector embebido se mudó al panel
  y la palette arranca colapsada (➕ la abre, solo en edit mode).
- Restricción honesta: la palette NO puede vivir en un panel nativo — el
  drag HTML5 no cruza webviews (iframes aislados). La alternativa nativa
  sigue siendo "+ Conectar desde…" en el Catálogo.

Todas las fases del plan (F0–F10) están implementadas.

## Backends que usa la extensión (qué necesita correr y dónde vive)

Acktos Studio es **la única UI** de los dos sistemas (los viewers web
`system_explorer/` y `GraphAgents/viewer/index.html` se eliminaron el
2026-07-08 — esta extensión los reemplaza). No levanta servidores HTTP para
funcionar: spawnea dos procesos Python por **stdio JSON-lines** y solo habla
HTTP con AgentSpan cuando ejecutás durable.

| # | Proceso | Quién lo levanta | Archivo exacto | Qué reusa | Para qué |
|---|---|---|---|---|---|
| 1 | **Puente GraphAgents** | la extensión, automático (`python3 -m viewer.bridge`, cwd `GraphAgents/`) | [`GraphAgents/viewer/bridge.py`](../GraphAgents/viewer/bridge.py) | `viewer/server.py::api_route()` — grafo, cases, ⚡ run-local, replay, trace, flow-trace, connect/disconnect, delete-node, save/publish, publish-plan | TODO lo de GraphAgents en la extensión |
| 2 | **Puente System Map** | la extensión, automático (`uv run python scripts/system_map_bridge.py`, cwd `hubara_agency/`) | [`hubara_agency/scripts/system_map_bridge.py`](../hubara_agency/scripts/system_map_bridge.py) | `src/plugins/system_map/domain/` (`builder.py` + `serialize.py`) — directo al dominio, SIN pasar por FastAPI | el grafo del System Map + certificaciones |
| 3 | **AgentSpan/Conductor** `:6767` | **vos, solo si ejecutás durable** — botón "▶ Iniciar AgentSpan" (terminal con `agentspan server start`) o `cd GraphAgents && docker compose up` | imagen oficial (servicio `agentspan` de [`GraphAgents/docker-compose.yml`](../GraphAgents/docker-compose.yml)) | — | ▶ Run Durable, trace en vivo, histórico ⟲ de Runs. **Opcional**: ⚡ Ejecutar (local) no lo necesita; todo degrada limpio si está caído |

Lo que **NO** necesita: el `hubara-api` (:8000), el dashboard (:5174), ni
ningún contenedor del stack — los puentes leen manifests y código del disco.
El server HTTP `python3 -m viewer.server` (:8900) expone el mismo `api_route`
del puente #1 para uso standalone/Docker, pero la extensión no lo usa.

**Ningún endpoint/gate/regla se reimplementa en TypeScript**: la fuente de
verdad vive en Python y los puentes la reusan. La extensión es presentación +
orquestación. La webview se sirve estática desde `dist/` (`asWebviewUri`) —
sin puertos.

**Guardar & certificar (F14) y publicar nativo (F15)**: el botón "⤴ Guardar &
certificar" corre TODAS las suites de GraphAgents de `src/testing/suites.ts`
salvo las excluidas con motivo (hoy solo `integration`, que exige `:6767`) —
streameadas en vivo al panel Ejecución — y, solo si todo pasa, bendice
(`/api/save`) y publica. La DECISIÓN de qué publicar (rama, base, paths,
título, body del PR) viene de `GET /api/publish-plan`
(`sdk/production.py::plan_publication`); la EJECUCIÓN usa las APIs nativas de
VS Code (`vscode.git` + `vscode.authentication` → PR por REST, sin `gh`), con
fallback a `POST /api/publish` (gh, headless) si el git nativo no está. El
click derecho sobre un agente/tool borra el nodo vía `POST /api/delete-node`
(siempre con confirmación; cascade con blast-radius).

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
`code --install-extension acktos-studio-<versión>.vsix` (o "Install from
VSIX…" en la UI de Extensions).

## Self-update (releases)

La extensión es sideloaded — el Auto Update de VS Code solo cubre el
Marketplace. En su lugar, **se actualiza sola desde el repo**
(`src/selfUpdate.ts`): al activar compara su versión instalada contra la de
`vscode-hubara/package.json` del workspace; si el repo trae una más nueva
(llegó por `git pull`), re-empaqueta el VSIX in-process (esbuild + vsce con
el node del propio VS Code) y lo instala — el operador solo ve el toast
"Recargar ahora". Chequeo manual: paleta → "Acktos: Buscar actualización de
la extensión".

**Protocolo de release**: todo PR que cambie la extensión DEBE bumpear
`version` en `package.json` — sin bump, las instancias instaladas no se
enteran del cambio. Requisito en la máquina del operador: `node_modules`
presente en `vscode-hubara/` (si falta, la extensión lo avisa con un toast).

## Export

La carpeta es autocontenida salvo los dos puentes Python, que viven a
propósito junto a cada subsistema (son la fuente de verdad, no deben
duplicarse). Para exportar como repo propio: llevar `vscode-hubara/` + los dos
`*bridge.py`, y apuntar los `cwd` a los checkouts destino.
