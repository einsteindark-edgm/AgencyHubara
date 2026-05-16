# Plugin Refactor Plan — AgencyHubara

> **Documento operativo.** Acompaña a `PLUGIN_ARCHITECTURE.md` (el contrato).
> Este archivo es **ejecutable**: explica qué hacer, en qué orden, con qué
> verificaciones, y registra las **discrepancias entre el contrato y la
> realidad del código** descubiertas en la auditoría inicial (2026-05-15).
>
> **Audiencia primaria:** future-me (Claude post-compact) ejecutando el
> refactor por PRs. Si lees esto y la realidad del código difiere de lo
> aquí descrito, lee el §0 (auditoría) y actualiza el LOG, no improvises.

---

## §0. Auditoría — discrepancias contrato vs. código real

El contrato `PLUGIN_ARCHITECTURE.md` se escribió antes de auditar el código
en detalle. La auditoría del 2026-05-15 encontró las siguientes
discrepancias. **El plan de PRs en §3 está corregido contra la realidad,
no contra el contrato.** Cuando contrato y realidad diverjan, gana este
documento.

### §0.1 — Estructura de carpetas

| Contrato dice | Realidad encontrada | Impacto |
|---|---|---|
| `exoclaw-temporal/src/{session_based,turn_based}/worker.py` | `exoclaw-temporal/exoclaw_temporal/{session_based,turn_based}/worker.py` (no `src/`, package es `exoclaw_temporal`) | El §5.2 del contrato apunta a paths que no existen. **Loaders se reescriben en los paths reales.** |
| Un solo `worker.py` por modo a reescribir | **Hay UN WORKER POR DOMINIO**: `hubara_agency/src/{sales_whatsapp,remarketing_whatsapp,catalog_sync}/worker.py`. Cada uno levanta su propio `Worker(...)` con su task queue exclusiva (`SALES_QUEUE`, `REMARKETING_QUEUE`, `CATALOG_SYNC_QUEUE`). | **El plan original de "un loader unifica todo" no aplica.** El refactor preserva worker-per-dominio; cada plugin agéntico **declara y trae su propio worker**. Hay un meta-launcher opcional que arranca todos los workers habilitados. |
| Frontend: `App.tsx` consume `PLUGINS` | **`App.tsx` no existe.** El shell real es `frontend_dashboard/src/pages/Dashboard.tsx`, montado en `main.tsx` vía `<AppProviders><Dashboard/></AppProviders>`. `app/` solo contiene `index.tsx` (re-export de providers) y `providers/QueryProvider.tsx`. | El registry generado se consume desde `pages/Dashboard.tsx`, NO desde un `App.tsx`. |
| Hay `src/sales_whatsapp/api.py` (router FastAPI) | OK, existe + `dashboard/api.py` y `dashboard/handoff.py`. Confirma el modelo. | El loader FastAPI (§5.1) sí aplica tal cual, pero los routers a importar son los reales. |
| `composition.py`, `contracts.py`, `parsers.py` viven en `exoclaw-temporal/src/session_based/` | **Viven en `hubara_agency/src/<dominio>/`** (cada dominio tiene su composición). `exoclaw_temporal/` es el motor puro (workflows base + activities base). | Confirma que `platform/` es la librería compartida y los dominios son los plugins. |

### §0.2 — Toolchain frontend

| Contrato asume | Realidad | Acción |
|---|---|---|
| Husky pre-commit | NO instalado | PR1 agrega script `plugins:sync` a `prebuild` y al `dev`. Pre-commit queda como TODO post-PR3. |
| `tsx` para correr el script TS | NO instalado en `frontend_dashboard/package.json` | PR1 agrega `tsx` a devDependencies. |
| `yaml` package | NO instalado | PR1 agrega `yaml` a dependencies. |
| `vite.config.ts` ya tiene alias `@plugins` | Solo tiene `@` → `./src` | PR1 agrega `@plugins` → `../plugins`. |
| `dependency-cruiser` reglas FSD | **EXISTE** y es estricto: no cross-feature, no deep-imports, no `pages → app`, etc. (`.dependency-cruiser.cjs`) | PR3 actualiza reglas para reconocer `@plugins/<id>/` como nueva unidad lógica. Hasta entonces, NO mover features (PR1 no toca). |

### §0.3 — Toolchain Python

| Contrato asume | Realidad | Acción |
|---|---|---|
| `import-linter` no mencionado | **EXISTE** en `hubara_agency/.importlinter` con contratos R-DIP detallados (`platform-no-agents`, `agents-independent`, `tools-no-temporal`, `parsers-pure`) | PR3 actualiza contratos para que `src.plugins.<id>` sustituya a `src.sales_whatsapp` etc. |
| `uv workspace` | Sí: `[tool.uv.workspace] members = ["exoclaw-temporal", "hubara_agency"]` | **Decisión §1.3**: NO crear nuevo workspace member por plugin. Los plugins Python viven como `hubara_agency/src/plugins/<id>/` (subpaquete) hasta que crezcan. |
| Auto-discovery via `plugins/` en root | Conflicto con el patrón uv workspace si los plugins viven fuera de `hubara_agency/` | **Decisión §1.3**: el directorio canónico de plugins es `hubara_agency/src/plugins/<id>/` para el código Python, y `frontend_dashboard/src/plugins/<id>/` para el código TS. El `plugin.yaml` vive en `frontend_dashboard/src/plugins/<id>/plugin.yaml` (raíz lógica del plugin desde el punto de vista del manifest); ambos directorios se vinculan por `id` y por convención de nombre. |

### §0.4 — Modelo de workers (corrección crítica al contrato)

**El contrato §5.2 propone un loader único por modo (`session_based`,
`turn_based`) que descubre WORKFLOWS/ACTIVITIES/TOOLS y levanta UN solo
`Worker`.** Esto **no funciona** sobre el código actual porque:

1. Cada dominio tiene su **task queue exclusiva** (`SALES_QUEUE`,
   `REMARKETING_QUEUE`, `CATALOG_SYNC_QUEUE`). En producción hay
   deployments K8s separados (`hubara-worker-sales`, `hubara-worker-catalog-sync`)
   con escalado independiente.
2. Cada dominio registra **diferentes activities** (Sales tiene
   `bootstrap_sales_session_activity`, Remarketing tiene
   `bootstrap_remarketing_session_activity`, Catalog tiene
   `pull_medusa_catalog_activity`). Unificar es viable pero rompe el
   modelo de aislamiento operacional ya validado.
3. Las tools de cada dominio se registran en boot via
   `register_tool_extension(...)` — consciente de que el worker A no
   debería tener las tools de B (foot-gun: el LLM podría usar tools de
   un dominio que no aplican).

**Decisión adoptada (§1.4)**: cada plugin agéntico **trae su propio worker**.
El manifest declara `agent.worker_module: worker` apuntando a
`hubara_agency/src/plugins/<id>/worker.py`. Existe un **meta-launcher
opcional** (`hubara_agency/src/run_workers.py`) que descubre plugins
habilitados con `agent.worker_module` y los arranca en paralelo. En
producción se sigue usando un container por worker (Docker compose / K8s
deployment), pero la lista de containers se genera desde los manifests
en vez de hardcoded.

---

## §1. Decisiones operativas (cierran ambigüedades del contrato)

### §1.1 — Layout final de un plugin

```
frontend_dashboard/src/plugins/<id>/         ← raíz lógica del plugin
├── plugin.yaml                              ← manifest (única fuente de verdad)
├── frontend/                                ← código React/TS (FSD interno relajado)
│   ├── index.ts                             ← exporta { Page, sidebar, dashboardWidgets }
│   ├── pages/                               ← (opcional) sub-pages del plugin
│   ├── features/                            ← features migradas desde src/features/
│   └── README.md                            ← (opcional) onboarding del plugin
└── (los pointers a backend viven en plugin.yaml; ver §1.3)

hubara_agency/src/plugins/<id>/              ← código Python del plugin (espejo)
├── api/                                     ← (opcional) router FastAPI
│   └── __init__.py                          ← exporta `router`
├── agent/                                   ← (opcional) workflows/activities/tools Temporal
│   ├── __init__.py                          ← exporta WORKFLOWS, ACTIVITIES, TOOL_FACTORIES
│   ├── workflows/
│   ├── activities/
│   └── tools/
├── worker.py                                ← worker propio del plugin (si tiene `agent:`)
├── jobs/                                    ← (opcional) handlers de cron
└── migrations/                              ← (opcional) alembic env per-plugin
```

**Por qué no `plugins/<id>/` en root del repo**: rompería el `uv workspace`
y los imports `from src.platform...`. Mantener los plugins Python como
subpaquete de `hubara_agency.src` evita esa fricción y permite que los
contratos R-DIP existentes se actualicen mecánicamente
(`src.sales_whatsapp` → `src.plugins.chats`).

**Por qué el manifest vive en frontend_dashboard/src/plugins/**: el
`plugins-sync.ts` lo lee desde ahí, y ese es el árbol que el desarrollador
edita más seguido al agregar UI. El `loader` Python lo encuentra por path
relativo.

### §1.2 — Convención de nombres

- `id` del plugin = nombre del directorio (`chats`, `orders`, etc.).
- Los path Python: `src.plugins.<id>.api`, `src.plugins.<id>.agent`,
  `src.plugins.<id>.worker`.
- Los path TS: `@plugins/<id>/frontend`.
- Los manifests se descubren escaneando
  `frontend_dashboard/src/plugins/*/plugin.yaml`.

### §1.3 — `plugin.yaml` con paths cross-stack

```yaml
# frontend_dashboard/src/plugins/chats/plugin.yaml
id: chats
version: 0.1.0
display_name: Chats
description: Conversaciones WhatsApp con agente Temporal.

depends_on: []

frontend:
  entry: ./frontend                          # relativo al plugin.yaml
  contributes:
    sidebar:
      - { route: /chats, label: Chats, icon: chat }
    sections:
      - { key: chat, label: Chats, order: 1 }

api:
  python_module: src.plugins.chats.api       # el `loader` hace importlib
  prefix: /api/chats
  tags: [Chats]
  legacy_routers:                            # mientras coexistan los routers viejos
    - { module: src.plugins.chats.api.sales,     prefix: /api,           tags: [WhatsApp_Sales_Domain] }
    - { module: src.plugins.chats.api.dashboard, prefix: /api/dashboard, tags: [Dashboard] }
    - { module: src.plugins.chats.api.handoff,   prefix: /api/dashboard, tags: [Dashboard_Handoff] }

agent:
  python_module: src.plugins.chats.agent     # exporta WORKFLOWS, ACTIVITIES, TOOL_FACTORIES
  worker_module: src.plugins.chats.worker    # módulo que el meta-launcher arranca
  workers:                                   # múltiples workers por plugin (sales + remarketing)
    - { name: sales,       module: src.plugins.chats.workers.sales }
    - { name: remarketing, module: src.plugins.chats.workers.remarketing }

# wiring_intents documenta recursos compartidos (no se aplican automáticamente).
wiring_intents:
  filesystem_volumes: [hubara-vault]
  env_vars_required:
    - DEEPSEEK_API_KEY
    - WHATSAPP_PHONE_NUMBER_ID
    - WHATSAPP_ACCESS_TOKEN
```

**Nota crítica**: el campo `workers` es una lista (no singular) para
soportar el caso real del plugin `chats` que necesita DOS workers (sales
+ remarketing). El meta-launcher itera y arranca todos.

### §1.4 — Estrategia de ejecución de workers

**Hoy (antes del refactor)**: docker-compose levanta 3 services hardcoded
(`hubara-worker`, `hubara-worker-remarketing`, `hubara-worker-catalog-sync`).

**Después de PR3**:

- Opción dev local: `python -m hubara_agency.src.run_workers` arranca
  todos los workers de plugins habilitados en paralelo (asyncio.gather)
  en un solo proceso — útil para `uv run` sin docker.
- Opción docker-compose / K8s: se mantiene un container por worker,
  pero el `docker-compose.local.yml` y los manifests K8s se **generan**
  desde un script `scripts/render-compose.py` que lee los manifests.
  (Generación opcional; PR3 se puede limitar a documentar la regla y
  actualizar manualmente.)

### §1.5 — Coexistencia durante la migración

PR2 mueve archivos pero **no** introduce el sistema de plugins en runtime.
Por compatibilidad, `main.py` y los workers siguen importando desde la
nueva ubicación con paths estáticos. PR3 reemplaza los imports estáticos
por auto-discovery. **En ningún momento el sistema deja de funcionar.**

### §1.6 — Reglas de arquitectura — momento de actualización

| Archivo | PR donde se actualiza |
|---|---|
| `hubara_agency/.importlinter` | PR2 (renombrar contratos `src.sales_whatsapp` → `src.plugins.chats`) |
| `frontend_dashboard/.dependency-cruiser.cjs` | PR3 (relajar reglas para permitir `@plugins/<id>/frontend/features/<x>` como nueva unidad) |
| `hubara_agency/tests/architecture/*` | PR2 (mismo renombrado) |
| `frontend_dashboard/src/test/architecture/*` | PR3 (idem dependency-cruiser) |

---

## §2. Inventario operativo previo al refactor

### §2.1 — Backend (`hubara_agency/src/`)

```
sales_whatsapp/         11 archivos .py + activities/ + tools/ + use_cases/ + workflows/
remarketing_whatsapp/    6 archivos .py + activities/ + workflows/
catalog_sync/            6 archivos .py + activities/ + workflows/
dashboard/               5 archivos .py (api.py, handoff.py, composition.py)
platform/               librería compartida — NO se mueve (10+ archivos)
main.py                 1 archivo — SE REESCRIBE en PR3
```

**Imports `from src.*`** (encontrados con grep):
- 40+ archivos importan `from src.platform.*` (se preservan tal cual).
- 12+ archivos importan `from src.sales_whatsapp.*` (se reescriben mecánicamente a `from src.plugins.chats.*`).
- Cross-domain (a actualizar también): `dashboard/composition.py`,
  `platform/temporal/dispatcher.py` (con excepciones documentadas en
  `.importlinter`).

### §2.2 — Frontend (`frontend_dashboard/src/`)

```
features/   19 features → migran a plugins (mapeo §2.4)
entities/   8 entidades cross-plugin (agent, chat, handoff, import-job, message, order, session, tracked-order) — NO se mueven, son librería compartida
shared/     api, config, lib, ui — NO se mueven
pages/Dashboard.tsx   shell — SE REESCRIBE en PR3 para consumir PLUGINS
app/        index.tsx + providers/ — index.tsx queda; aquí vive plugin-registry.generated.ts
main.tsx    no se toca
```

### §2.3 — Worker runtime (`exoclaw-temporal/exoclaw_temporal/`)

**No se toca.** Es la librería que `hubara_agency` importa. Los workers
genéricos `session_based/worker.py` y `turn_based/worker.py` quedan
como ejemplos de framework pero **no se usan en producción** (los workers
reales viven en cada dominio).

### §2.4 — Mapeo de 19 features a plugins

| Plugin destino | Features migradas | Comentario |
|---|---|---|
| `chats` | chats-conversation, chats-inbox, chats-inspector, memory-modal, session-chat, session-list, session-metadata | 7 features. El más complejo (toca los 3 stacks). |
| `agents` | agents-inspector, agents-list, agents-prompts | 3 features. Solo frontend + API simple. |
| `orders` | orders-board, orders-filters, orders-inspector | 3 features. POC del plugin nuevo (sin código existente backend). |
| `eta` | eta-cards, eta-chat, eta-list | 3 features. |
| `catalog` | upload-inspector, upload-jobs, upload-wizard | 3 features. Migración de `catalog_sync`. |

**Total: 19 features → 5 plugins. Suma exacta: 7+3+3+3+3 = 19. ✅**

---

## §3. Plan de PRs — corregido contra la realidad

Los PRs preservan el contrato de `PLUGIN_ARCHITECTURE.md` §9 pero
ajustan los paths y el modelo de worker. Cada PR tiene **scope cerrado**,
**verificación ejecutable** y **definition of done**.

### PR1 — Plumbing (Fase 0)

**Scope**: introducir el contrato de plugins sin tocar features.

**Cambios**:

1. Crear directorios:
   - `frontend_dashboard/src/plugins/` (con `.gitkeep`)
   - `hubara_agency/src/plugins/__init__.py`
2. Crear schema del manifest: `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml`.
3. Implementar `frontend_dashboard/scripts/plugins-sync.ts` que:
   - Escanea `src/plugins/*/plugin.yaml`.
   - Filtra por `process.env.ENABLED_PLUGINS` (csv).
   - Genera `frontend_dashboard/src/app/plugin-registry.generated.ts`.
4. Editar `frontend_dashboard/package.json`:
   - `"plugins:sync": "tsx scripts/plugins-sync.ts"` en `scripts`.
   - `"prebuild": "npm run plugins:sync"` (corre antes de `vite build`).
   - `"predev": "npm run plugins:sync"` (corre antes de `vite`).
   - Agregar `tsx` a devDependencies.
   - Agregar `yaml` a dependencies.
5. Editar `frontend_dashboard/vite.config.ts`:
   - Agregar alias `@plugins` → `../src/plugins` (relativo a vite root).
6. Editar `frontend_dashboard/.gitignore`:
   - Agregar línea `src/app/plugin-registry.generated.ts`.
7. Editar `frontend_dashboard/.dependency-cruiser.cjs`:
   - Agregar `doNotFollow.path` para `src/app/plugin-registry.generated.ts` (es un archivo generado que importa de muchos plugins).
8. Editar `frontend_dashboard/tsconfig.app.json`:
   - Agregar `"@plugins/*": ["../src/plugins/*"]` a `paths`.

**Verificación**:

```bash
cd frontend_dashboard
npm install                              # instala tsx + yaml
npm run plugins:sync                     # genera registry vacío sin error
test -f src/app/plugin-registry.generated.ts && echo OK
npm run dev                              # arranca como antes (registry vacío no rompe nada)
npm run test:arch                        # las reglas pasan (registry está en doNotFollow)
```

**Definition of Done**:
- `pnpm plugins:sync` (o `npm run plugins:sync`) genera un archivo TS válido (vacío) sin error.
- `npm run dev` arranca y la app se renderiza idéntica a antes.
- `npm run test:arch` pasa.
- CI pipeline existente sigue verde.

**Tamaño estimado**: 8-10 archivos nuevos/modificados, ~150 LOC. **1 día**.

---

### PR2 — Migrar `chats` (Fase 1)

**Scope**: mover el dominio Chats completo (sales_whatsapp +
remarketing_whatsapp + dashboard) bajo `plugins/chats/`. **NO** introducir
loaders todavía (PR3). Los imports estáticos siguen funcionando porque
se reescriben mecánicamente.

**Cambios backend**:

1. Crear `hubara_agency/src/plugins/chats/`:
   ```
   plugins/chats/
   ├── __init__.py
   ├── api/
   │   ├── __init__.py                       ← reúne sub-routers en un solo `router`
   │   ├── sales.py                          ← (ex hubara_agency/src/sales_whatsapp/api.py)
   │   ├── dashboard.py                      ← (ex hubara_agency/src/dashboard/api.py)
   │   └── handoff.py                        ← (ex hubara_agency/src/dashboard/handoff.py)
   ├── agent/
   │   ├── __init__.py                       ← exporta WORKFLOWS, ACTIVITIES, TOOL_FACTORIES (vacío en PR2; lo llena PR3)
   │   ├── sales/                            ← (ex hubara_agency/src/sales_whatsapp/{workflows,activities,tools,use_cases,state,config,parsers,prompts,contracts,composition,api del worker})
   │   └── remarketing/                      ← (ex hubara_agency/src/remarketing_whatsapp/*)
   ├── workers/
   │   ├── __init__.py
   │   ├── sales.py                          ← (ex hubara_agency/src/sales_whatsapp/worker.py)
   │   └── remarketing.py                    ← (ex hubara_agency/src/remarketing_whatsapp/worker.py)
   └── dashboard_composition.py              ← (ex hubara_agency/src/dashboard/composition.py)
   ```

2. Reescribir imports mecánicamente:
   - `from src.sales_whatsapp.X` → `from src.plugins.chats.agent.sales.X`
   - `from src.remarketing_whatsapp.X` → `from src.plugins.chats.agent.remarketing.X`
   - `from src.dashboard.api` → `from src.plugins.chats.api.dashboard`
   - (similar para handoff, composition)
   - `from src.platform.X` → SE PRESERVA (es librería compartida)

3. **Mantener `hubara_agency/src/main.py` con imports estáticos
   apuntando a las nuevas ubicaciones** — sin loader todavía:
   ```python
   from src.plugins.chats.api import router as chats_router
   app.include_router(chats_router)
   ```

4. Actualizar `hubara_agency/.importlinter`:
   - Reemplazar `src.sales_whatsapp`, `src.remarketing_whatsapp` por
     `src.plugins.chats.agent.sales`, `src.plugins.chats.agent.remarketing`.
   - Mantener `src.platform` y los `ignore_imports` documentados (con paths nuevos).

5. Actualizar `hubara_agency/docker-compose.local.yml`:
   - `command: ["python", "-m", "src.sales_whatsapp.worker"]` →
     `command: ["python", "-m", "src.plugins.chats.workers.sales"]`
   - Idem para remarketing.

6. Borrar las carpetas viejas:
   - `hubara_agency/src/sales_whatsapp/` (atómico, mismo PR).
   - `hubara_agency/src/remarketing_whatsapp/`
   - `hubara_agency/src/dashboard/` (api.py, handoff.py, composition.py)

7. Actualizar tests que referencian los paths viejos
   (`hubara_agency/tests/architecture/*` principalmente).

**Cambios frontend**:

1. Crear `frontend_dashboard/src/plugins/chats/`:
   ```
   plugins/chats/
   ├── plugin.yaml                           ← manifest (ver §1.3)
   ├── frontend/
   │   ├── index.ts                          ← export { Page (=ChatsSection), sidebar, dashboardWidgets }
   │   └── features/
   │       ├── chats-conversation/           ← (ex src/features/chats-conversation/*)
   │       ├── chats-inbox/
   │       ├── chats-inspector/
   │       ├── memory-modal/
   │       ├── session-chat/
   │       ├── session-list/
   │       └── session-metadata/
   ```

2. Reescribir imports mecánicamente:
   - `import X from "@/features/chats-conversation"` →
     `import X from "@plugins/chats/frontend/features/chats-conversation"`
   - Las features dentro del mismo plugin pueden importarse entre sí
     (relajar dependency-cruiser para `@plugins/<id>/frontend/features/`).

3. Actualizar `frontend_dashboard/src/pages/Dashboard.tsx` para que la
   `ChatsSection` se importe desde `@plugins/chats/frontend` (o se
   mantenga inline pero usando los paths nuevos).

4. Borrar `src/features/chats-*`, `src/features/memory-modal`,
   `src/features/session-*` (atómico).

5. Actualizar `.dependency-cruiser.cjs`:
   - Agregar regla que permita `@plugins/<id>/frontend/features/<a>` →
     `@plugins/<id>/frontend/features/<b>` (cross-feature dentro del
     mismo plugin OK).

**Verificación**:

```bash
# Backend
cd hubara_agency
uv sync
uv run pytest -m architecture                # import-linter pasa con paths nuevos
uv run python run_api.py                     # FastAPI arranca, /api/chats responde
uv run python -m src.plugins.chats.workers.sales       # worker arranca
uv run python -m src.plugins.chats.workers.remarketing # worker arranca

# Frontend
cd frontend_dashboard
npm run plugins:sync                         # detecta el manifest (registry tendrá 1 entry)
npm run dev                                  # la sección Chats funciona idéntico a antes
npm run test:arch                            # dependency-cruiser pasa

# E2E
docker compose -f hubara_agency/docker-compose.local.yml up -d
# Probar enviar un WhatsApp end-to-end
```

**Definition of Done**:
- Todo el código relacionado con Chats vive bajo `plugins/chats/`.
- `git diff --stat HEAD~1` muestra deletes en `src/sales_whatsapp/` etc.
- El chat funciona idéntico a antes (smoke test manual + tests existentes verdes).
- `import-linter` y `dependency-cruiser` verdes con reglas actualizadas.

**Tamaño estimado**: ~50 archivos movidos + reescritura de imports +
manifest. **2-3 días**.

**Riesgos**:
- Imports cruzados que no se detecten en grep (ej: en strings de log).
  Mitigar con `grep -rn "sales_whatsapp\|remarketing_whatsapp" hubara_agency/` post-move.
- Tests que importan paths viejos. Mitigar corriendo `pytest` antes de
  abrir PR.

---

### PR3 — Loaders (Fase 2)

**Scope**: reemplazar imports estáticos por auto-discovery en los 3
stacks. No agrega features; solo cambia el mecanismo de registro.

**Cambios backend**:

1. Reescribir `hubara_agency/src/main.py` (el loader §5.1 del contrato,
   adaptado a paths reales):

   ```python
   import importlib, os
   from pathlib import Path
   import yaml
   from fastapi import FastAPI
   from fastapi.middleware.cors import CORSMiddleware

   app = FastAPI(title="Agency API", version="2.0.0")
   app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                      allow_methods=["*"], allow_headers=["*"])

   ENABLED = set(filter(None, os.environ.get("ENABLED_PLUGINS", "").split(",")))
   # Manifests viven en frontend_dashboard/src/plugins/<id>/plugin.yaml
   PLUGINS_MANIFEST_DIR = Path(__file__).parents[2] / "frontend_dashboard" / "src" / "plugins"

   for plugin_dir in sorted(PLUGINS_MANIFEST_DIR.iterdir()):
       if not plugin_dir.is_dir() or plugin_dir.name == "_schema":
           continue
       if ENABLED and plugin_dir.name not in ENABLED:
           continue
       manifest_path = plugin_dir / "plugin.yaml"
       if not manifest_path.exists():
           continue
       manifest = yaml.safe_load(manifest_path.read_text())
       api_cfg = manifest.get("api")
       if not api_cfg:
           continue
       # Plain mode: importar el módulo único
       if "python_module" in api_cfg:
           mod = importlib.import_module(api_cfg["python_module"])
           app.include_router(
               mod.router,
               prefix=api_cfg.get("prefix", f"/api/{plugin_dir.name}"),
               tags=api_cfg.get("tags", [plugin_dir.name]),
           )
       # Legacy mode: lista de routers (compat para chats que tiene 3 routers viejos)
       for legacy in api_cfg.get("legacy_routers", []):
           legacy_mod = importlib.import_module(legacy["module"])
           app.include_router(
               legacy_mod.router,
               prefix=legacy.get("prefix", "/api"),
               tags=legacy.get("tags", [plugin_dir.name]),
           )

   @app.get("/")
   def health_check():
       return {"status": "ok", "plugins_loaded": sorted(ENABLED) if ENABLED else "all"}
   ```

2. Crear `hubara_agency/src/run_workers.py` (meta-launcher):

   ```python
   """Meta-launcher: arranca los workers de plugins habilitados en paralelo.

   Útil para dev local. En producción, docker-compose / K8s sigue manejando
   un container por worker (lista generada desde manifests).
   """
   import asyncio, importlib, os
   from pathlib import Path
   import yaml

   ENABLED = set(filter(None, os.environ.get("ENABLED_PLUGINS", "").split(",")))
   PLUGINS_MANIFEST_DIR = Path(__file__).parents[2] / "frontend_dashboard" / "src" / "plugins"

   async def main():
       tasks = []
       for plugin_dir in sorted(PLUGINS_MANIFEST_DIR.iterdir()):
           if not plugin_dir.is_dir() or plugin_dir.name == "_schema":
               continue
           if ENABLED and plugin_dir.name not in ENABLED:
               continue
           manifest = yaml.safe_load((plugin_dir / "plugin.yaml").read_text())
           agent_cfg = manifest.get("agent", {})
           workers = agent_cfg.get("workers")
           if not workers:
               # singular fallback
               if "worker_module" in agent_cfg:
                   workers = [{"name": "default", "module": agent_cfg["worker_module"]}]
               else:
                   continue
           for w in workers:
               mod = importlib.import_module(w["module"])
               tasks.append(asyncio.create_task(mod.main(), name=f"{plugin_dir.name}/{w['name']}"))
       await asyncio.gather(*tasks)

   if __name__ == "__main__":
       asyncio.run(main())
   ```

3. **Tools registration**: el patrón `register_tool_extension` actual
   funciona porque cada `worker.py` lo invoca al importarse. Cuando el
   meta-launcher importa `mod = importlib.import_module(w["module"])`,
   el side-effect de registración se ejecuta. **Cero cambios al
   `tool_extensions.py`.**

4. Opcional: script `scripts/render-compose.py` que regenera
   `docker-compose.local.yml` y los K8s manifests desde los manifests.
   (Recomendado, pero no bloqueante para el DOD.)

**Cambios frontend**:

1. Reescribir `frontend_dashboard/src/pages/Dashboard.tsx` para que el
   shell construya `SectionKey` y secciones desde `PLUGINS`:

   ```tsx
   import { PLUGINS } from "@/app/plugin-registry.generated";
   import { useState, Suspense } from "react";
   // ...
   const sections = PLUGINS.flatMap(p => p.sections || []);
   const [section, setSection] = useState(sections[0]?.key);
   // ...
   <Toolbar sections={sections} section={section} setSection={setSection} ... />
   {PLUGINS.map(p =>
     section === p.sections?.[0]?.key && (
       <Suspense key={p.id}><p.Page showSidebar={...} showInspector={...} /></Suspense>
     )
   )}
   ```

2. Adaptar `frontend_dashboard/src/shared/ui/Toolbar.tsx` para recibir
   `sections` como prop en vez de hardcoded enum (hoy usa `SectionKey`).

3. Actualizar `frontend_dashboard/.dependency-cruiser.cjs` para reglas
   nuevas:
   - Agregar `pages → @plugins/<id>/frontend` como permitido.
   - Mantener prohibido cross-plugin imports (ej:
     `@plugins/chats/frontend → @plugins/orders/frontend`).

**Verificación**:

```bash
# Backend
cd hubara_agency
ENABLED_PLUGINS=chats uv run python run_api.py
# → API arranca, /api/chats responde, /api/dashboard también (legacy_routers)
ENABLED_PLUGINS=chats uv run python -m src.run_workers
# → ambos workers (sales + remarketing) arrancan en paralelo

curl http://localhost:8000/ | jq .
# → {"status":"ok","plugins_loaded":["chats"]}

ENABLED_PLUGINS= uv run python run_api.py     # sin var: carga todo
ENABLED_PLUGINS=other uv run python run_api.py  # carga ninguno

# Frontend
cd frontend_dashboard
ENABLED_PLUGINS=chats npm run plugins:sync
npm run dev
# → solo se ve la sección Chats; agregar/quitar plugins del env la activa/desactiva
```

**Definition of Done**:
- `main.py` no importa ningún plugin estáticamente.
- Cambiar `ENABLED_PLUGINS` activa/desactiva chats sin tocar código.
- Worker meta-launcher arranca todos los workers del plugin chats.
- Frontend renderiza secciones dinámicamente desde el registry.
- Tests existentes verdes.

**Tamaño estimado**: ~10 archivos modificados, ~300 LOC nuevas. **2 días**.

---

### PR4 — Migrar `agents-admin` (Fase 3.1)

**Scope**: el más simple. Solo frontend (3 features) + un router FastAPI
trivial. Sin agente Temporal.

**Cambios**:

1. Crear `frontend_dashboard/src/plugins/agents-admin/`:
   - `plugin.yaml` (sin `agent`, sin `workers`).
   - `frontend/index.ts` exportando `AgentsSection`.
   - `frontend/features/{agents-list,agents-prompts,agents-inspector}/` migradas.
2. Crear `hubara_agency/src/plugins/agents_admin/api/__init__.py` con un
   router FastAPI (puede ser stub si no existe lógica de backend
   todavía).
3. Borrar `frontend_dashboard/src/features/agents-*`.

**Verificación**:

```bash
ENABLED_PLUGINS=chats,agents-admin uv run python run_api.py
ENABLED_PLUGINS=chats,agents-admin npm run dev
# → ambas secciones funcionan
```

**Tamaño estimado**: ~15 archivos, ~1 día.

---

### PR5 — Migrar `catalog` (Fase 3.2)

**Scope**: migración del dominio `catalog_sync` (worker Temporal con
schedule) + las features de upload del frontend.

**Cambios**:

1. Mover `hubara_agency/src/catalog_sync/` → `hubara_agency/src/plugins/catalog/`.
2. Crear `frontend_dashboard/src/plugins/catalog/frontend/features/{upload-inspector,upload-jobs,upload-wizard}/`.
3. Manifest declara `agent.workers: [{name: sync, module: src.plugins.catalog.worker}]`.
4. Actualizar docker-compose para el worker nuevo.
5. Borrar carpetas viejas.

**Verificación**: idéntica a PR4 pero con `chats,agents-admin,catalog`.

**Tamaño estimado**: ~20 archivos, ~1 día.

---

### PR6 — Migrar `eta` (Fase 3.3)

**Scope**: solo frontend (3 features) + opcional API stub. Sin worker.

**Tamaño estimado**: ~15 archivos, ~1 día.

---

### PR7 — Crear `orders` from scratch (Fase 3.4)

**Scope**: PRIMER plugin construido bajo el contrato desde cero. Sirve
como referencia canónica.

**Cambios**:

1. `frontend_dashboard/src/plugins/orders/plugin.yaml` completo.
2. `frontend_dashboard/src/plugins/orders/frontend/` con las 3 features.
3. `hubara_agency/src/plugins/orders/api/__init__.py` con CRUD básico.
4. (Opcional) `hubara_agency/src/plugins/orders/agent/` si necesita workflows.

**Tamaño estimado**: variable según scope de negocio. **2-3 días**.

---

## §4. Verificación cruzada — comandos canónicos

Estos comandos deben funcionar después de PR3 en adelante:

```bash
# Frontend regenera registry sin error
cd frontend_dashboard && npm run plugins:sync

# FastAPI arranca con plugins enabled
cd hubara_agency && ENABLED_PLUGINS=chats uv run python run_api.py

# Meta-launcher arranca todos los workers del plugin
cd hubara_agency && ENABLED_PLUGINS=chats uv run python -m src.run_workers

# Frontend Tauri compila (requiere rust toolchain en host)
cd frontend_dashboard && ENABLED_PLUGINS=chats npm run build

# Health check muestra plugins cargados
curl http://localhost:8000/ | jq .
# → { "status": "ok", "plugins_loaded": ["chats"] }

# Stack completo dockerizado
docker compose -f hubara_agency/docker-compose.local.yml up -d
# → todos los workers + API + frontend up

# Tests de arquitectura
cd hubara_agency && uv run pytest -m architecture
cd frontend_dashboard && npm run test:arch
```

---

## §5. Items diferidos (post-PR7)

Estos items aparecen en el contrato como "diferidos" o salen naturalmente
del refactor. **NO se hacen ahora**. Re-evaluar cuando haya 3+ plugins
funcionando.

| Item | Cuándo |
|---|---|
| Pre-commit hook (husky / pre-commit nativo) que corra `plugins:sync` | Cuando un PR olvide regenerar el registry y rompa el build de otro. |
| `scripts/render-compose.py` que genere `docker-compose.local.yml` desde manifests | Cuando agregar un plugin agéntico nuevo requiera editar 3 archivos. |
| LangGraph dentro de actividades Temporal | Cuando un plugin necesite tool-loop estructurado (`agent.graph_spec`). |
| Marketplace UI para activar/desactivar plugins desde la app | Cuando el operador deje de querer editar `ENABLED_PLUGINS` a mano. |
| Terraform multi-tenant (`infra/tenants/<x>/`) | Cuando llegue el segundo cliente con infra dedicada. |
| Plugin SDK con tipos estrictos (Pydantic models para manifest, Protocol classes para WORKFLOWS/ACTIVITIES) | Cuando haya 3+ plugins agénticos y sea fácil olvidar una convención. |
| Cron runner formal (APScheduler / Celery beat / container per cron) | Cuando un plugin necesite `jobs:` y no se pueda usar Temporal Schedule. |
| Wiring intent merger (aplicar `wiring_intents` automáticamente) | Cuando un plugin necesite tocar `vite.config.ts`/`tsconfig.json` global. |
| Sales Flow DSL configurable por tenant | Después de PR7 (plugin system estable). |

---

## §6. Cómo usar este documento

1. **Antes de empezar un PR**, lee la sección §3 correspondiente. Si la
   realidad difiere, actualiza este archivo y `PLUGIN_REFACTOR_LOG.md`
   antes de codear.
2. **Durante el PR**, marca progreso en `PLUGIN_REFACTOR_LOG.md`.
3. **Al cerrar un PR**, copia los comandos de verificación a la
   descripción del PR en GitHub y check list los DOD.
4. **Si descubres un bloqueador** que cambia el plan: actualiza este
   archivo (no improvises), commitea el cambio en el mismo PR, y
   menciona la modificación en el commit message.

---

**Fin del plan ejecutable.** El contrato vive en `PLUGIN_ARCHITECTURE.md`;
el progreso vive en `PLUGIN_REFACTOR_LOG.md`; este archivo es el puente.
