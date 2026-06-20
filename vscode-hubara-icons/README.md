# Hubara Architecture Icons

File Icon Theme para VS Code que le pone íconos propios a los conceptos de la
arquitectura de AgencyHubara: **workers, plugins, tools, connectors, CLI, tests,
capas DEHA/FSD, manifests y capabilities**. Ayuda visual para ubicarte en el
monorepo de un vistazo.

## Probar en desarrollo (sin instalar nada)

1. Abrí esta carpeta (`vscode-hubara-icons/`) en una ventana de VS Code.
2. `F5` → abre una **Extension Development Host** con la extensión cargada.
3. En esa ventana nueva: `Cmd/Ctrl+Shift+P` → **Preferences: File Icon Theme** →
   elegí **Hubara Architecture Icons**.
4. Abrí el repo y mirá el árbol. Editás un SVG → guardás → `Cmd/Ctrl+Shift+P` →
   **Developer: Reload Window** para ver el cambio.

## Instalar para el equipo (`.vsix`)

No hace falta publicar en el Marketplace. Empaquetás un `.vsix` y se instala local:

```bash
cd vscode-hubara-icons
npx --yes @vscode/vsce package      # genera hubara-architecture-icons-0.1.0.vsix
code --install-extension hubara-architecture-icons-0.1.0.vsix
```

Después: `Cmd/Ctrl+Shift+P` → **Preferences: File Icon Theme** → **Hubara
Architecture Icons**. Podés commitear el `.vsix` al repo o subirlo a un release
interno para que el resto lo instale desde archivo.

> Para sugerirlo automáticamente al abrir el repo, agregá a `.vscode/extensions.json`:
> `{ "recommendations": ["agencyhubara.hubara-architecture-icons"] }`

## Cómo matchea (la regla a tener en cuenta)

VS Code matchea **solo el último segmento del path** — nombre exacto de carpeta,
nombre exacto de archivo, o extensión (todo lo que va después del primer punto).
**No hay globs ni rutas.** Consecuencias prácticas:

- `workers/`, `plugins/`, `tools/`… funcionan perfecto porque son nombres de carpeta.
- `test_*.py` (prefijo de Python) **no** puede tener ícono propio → te quedás con
  la carpeta `tests/` o nombres exactos como `conftest.py`.
- `*.test.ts` / `*.spec.ts` **sí** (registrados como "extensión" `test.ts` / `spec.ts`).
- Para señal a nivel-archivo de workers/tools, adoptá un sufijo: `sales.worker.py`,
  `register_order.tool.py` (ya están mapeados en `fileExtensions`).

Prioridad: `fileNames` > `fileExtensions` > `languageIds`; carpetas igual con `folderNames`.

## Qué mapea hoy (39 íconos, validados contra el árbol real)

Mapeo derivado de la arquitectura viva — `python3 -m sdk.cli` en GraphAgents, el
SDK del monorepo (`src/sdk/`), el host `src/platform/` y el frontend FSD.

### GraphAgents (LangGraph + AgentSpan)

| Concepto | Matchea por |
|---|---|
| Manifest declarativo | `manifests/`, `*.agent.yaml`, `*.taskgraph.yaml`, `tool.yaml` |
| Capability / StateGraph | carpetas `graphs/` `capabilities/` |
| Tool | carpeta `tools/`, ext `*.tool.py`, `tool.yaml` |
| Connector a Meta | `connectorkit/`, `connectors/`, `vendors/`, `port.py`/`ports.py`, `*.connector.py` |
| Explorer / viewer | carpeta `viewer/` |
| Fixtures golden | carpeta `fixtures/` |
| SDK core | carpeta `sdk/`, `foundation.py` |
| TestKit / TCK | `testkit/`, `conformance/` |

### Backend (DEHA + Temporal + plugin system)

| Concepto | Matchea por |
|---|---|
| Platform host | carpeta `platform/` |
| Plugin | carpeta `plugins/` + cada plugin real: `ads/` `orders/` `eta/` `catalog/` `chats/` `agents_admin/` `system_map/` (ver nota de colisión) |
| Agente durable | carpeta `agent/`, ext `*.agent.yaml` |
| Worker | carpeta `workers/`, ext `*.worker.py` |
| Capa DEHA | carpetas `activities/` `domain/` `use_cases/` |
| Workflow | carpeta `workflows/` |
| API | carpeta `api/` |
| Adapter / composición | carpeta `adapters/`, `composition.py` |
| Contract (DTO) | carpeta `contracts/`, `contracts.py` |
| Workspace del agente | carpeta `workspace/` |
| Core / kits / CLI | `foundation.py` (core) · `agentkit/dashboardkit/eventkit.py` (kit) · `cli/`, `cli.py`, `__main__.py` (CLI) |

### Frontend (Feature-Sliced)

| Concepto | Matchea por |
|---|---|
| App root | carpetas `app/` `frontend/` |
| Pages · Features · Entities | `pages/` · `features/`/`widgets/` · `entities/`/`model/`+`model.ts` |
| Shared · UI | `shared/` · `ui/` |
| API client / Contracts | `api.ts` · `contracts.ts` |
| Plugin manifest + schema | `plugin.yaml`, `_schema/`, `plugin.schema.yaml` |

### Cross-cutting

| Concepto | Matchea por |
|---|---|
| Manifest / spine | `manifests/`, `spinal-files.yaml`, `manifest_model.py`, `*.manifest.yaml` |
| Test | `tests/`, `architecture/`, `integration/`, `evals/`, `conftest.py`, `*.test.ts`/`*.spec.ts`/`*.test.py` |
| Skill | carpeta `skills/`, `SKILL.md` |
| Docker / deploy | `Dockerfile`, `docker-compose*.yml`, `.dockerignore` |

> **Nota — plugins reales reconocidos (+ colisión conocida).** Los 7 plugins
> (`ads`, `orders`, `eta`, `catalog`, `chats`, `agents_admin`, `system_map`) están
> mapeados por nombre → ícono de plugin, en backend y frontend. **Colisión:**
> `catalog` y `orders` también existen como bounded contexts del host
> (`platform/catalog/`, `platform/orders/`); como el theme matchea solo por nombre
> (sin ruta), esas 2 carpetas de `platform/` también muestran el puzzle. Es el
> precio de reconocer los plugins por nombre. **Al agregar un plugin nuevo, sumá su
> id a `folderNames` y `folderNamesExpanded`.**
>
> **Nota — archivos de lenguaje en gris suave.** `.py` `.ts` `.tsx` `.yaml` `.json`
> `.md` tienen ícono propio pero **monocromo gris**, a propósito: los conceptos de
> arquitectura resaltan en color y el código queda quieto. Los mapeos de
> arquitectura siempre ganan por especificidad (`contracts.ts`→contract,
> `*.agent.yaml`→agent, `cli.py`→CLI, `*.test.ts`→test).

## Cómo extender

1. **Agregar/cambiar un ícono**: editá o tirá un SVG nuevo en `icons/`.
2. **Mapearlo**: en `fileicons/hubara-icon-theme.json` agregá una entrada en
   `iconDefinitions` (`"_miconcepto": { "iconPath": "../icons/miconcepto.svg" }`)
   y referenciála en `folderNames` / `folderNamesExpanded` / `fileNames` /
   `fileExtensions`. Recordá que las keys se comparan en **minúsculas**.
3. Reload Window para ver el cambio.

## Sobre los SVGs

Los íconos de este pack son originales (línea, 24×24, estilo trazo, licencia MIT)
para no depender de terceros. Si querés glifos más pulidos o logos de tecnología,
podés reemplazarlos por SVGs de packs gratis y de licencia permisiva:

- [Tabler Icons](https://tabler.io/icons) — MIT, 5800+ outline (la estética base de este pack).
- [Lucide](https://lucide.dev) — ISC.
- [Material Design Icons](https://pictogrammers.com/library/mdi/) — Apache-2.0.
- [Simple Icons](https://simpleicons.org) — CC0, logos de tech (Python, TS, Docker, YAML…).

Bajás el `.svg`, lo ponés en `icons/`, lo apuntás en el theme JSON. Mantené la
atribución/licencia del pack que uses.

### Dark mode

VS Code muestra el SVG tal cual — **no recolorea** según el tema. Por eso los
íconos usan tonos medios que se leen tanto en sidebar claro como oscuro. Si querés
variantes por tema, el theme JSON soporta un override:

```jsonc
"iconDefinitions": {
  "_worker":       { "iconPath": "../icons/worker.svg" },
  "_worker_light": { "iconPath": "../icons/worker-light.svg" }
},
"light": { "folderNames": { "workers": "_worker_light" } }
```

## Estructura

```
vscode-hubara-icons/
├── package.json                     # contribuye el iconTheme
├── fileicons/
│   └── hubara-icon-theme.json       # el mapeo nombre/ext/carpeta → SVG
├── icons/                           # los 19 SVGs
└── README.md
```
