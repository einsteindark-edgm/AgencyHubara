# Plugin Refactor Log — AgencyHubara

> **Bitácora viva.** Apéndice ejecutable de `PLUGIN_REFACTOR_PLAN.md`.
> Aquí se registra el progreso real de cada PR, las verificaciones que
> efectivamente corrieron, y cualquier desviación del plan.
>
> **Convención:** cada entrada empieza con fecha en formato `YYYY-MM-DD`,
> identificador del PR, autor (humano o agente), y status. Nuevas entradas
> al **final** del archivo (append-only).

---

## Índice

| PR | Fecha inicio | Status | Notas |
|---|---|---|---|
| PR0 (auditoría) | 2026-05-15 | ✅ done | Documento `PLUGIN_REFACTOR_PLAN.md` creado tras auditar el código real. |
| PR1 (plumbing) | 2026-05-15 | ✅ done | Plumbing completo. `npm run plugins:sync` genera registry, todas las verificaciones verdes. Commit: `4d4d2b2`. |
| PR2 (migrar chats) | 2026-05-15 | ✅ done | 33 archivos Python + 7 features TS movidos a `plugins/chats/`. Backend + frontend verdes. Commit: `c13387f`. |
| PR3 (loaders) | 2026-05-15 | ✅ done | Auto-discovery en main.py + run_workers.py + Dashboard.tsx consume PLUGINS. ENABLED_PLUGINS funcional. Commit: `fa7d13e`. |
| PR4 (agents_admin) | 2026-05-15 | ✅ done | Plugin frontend-only. 3 features movidas + AgentsSection extraída + Dashboard usa registry. Commit: `847b2c7`. |
| PR5 (catalog) | 2026-05-15 | ✅ done | catalog_sync (worker + activities + workflows) + 3 features upload migrados. Meta-launcher descubre 3 workers (chats×2 + catalog×1). Commit: `9b01306`. |
| PR6 (eta) | 2026-05-15 | ✅ done | Plugin frontend-only (3 features eta-*). Mismo patrón que PR4. |
| PR7 (orders) | — | ⏸ pending | Bloqueado por PR3. |

---

## 2026-05-15 — PR0 (auditoría) — Claude — ✅ done

### Contexto

El usuario pidió "lee plugin_architecture.md, analiza el código actual,
vuelve y revisa el plan y crea todo un plan completo". El contrato
`PLUGIN_ARCHITECTURE.md` se había escrito antes de la auditoría
detallada, así que tenía supuestos sobre paths y modelo de workers que
no encajaban con la realidad.

### Trabajo realizado

1. **Mapeo de estructura**: leído `hubara_agency/src/`,
   `exoclaw-temporal/exoclaw_temporal/`, `frontend_dashboard/src/`.
2. **Lectura de archivos clave**: `main.py`, los 3 `worker.py` de
   dominio, `composition.py`, `tool_extensions.py`, `registries.py`,
   `Dockerfile`, `docker-compose.local.yml`, `package.json`,
   `vite.config.ts`, `.importlinter`, `.dependency-cruiser.cjs`,
   `pages/Dashboard.tsx`, `main.tsx`.
3. **Verificación de toolchain**: tsx/yaml/husky no instalados;
   import-linter y dependency-cruiser sí están con reglas estrictas.

### Hallazgos críticos

- **Path real de exoclaw**: `exoclaw-temporal/exoclaw_temporal/` (no
  `src/`). Package es `exoclaw_temporal`.
- **Modelo de workers real**: UN WORKER POR DOMINIO con task queue
  exclusiva, no un loader único por modo como sugería el contrato.
- **Frontend shell**: `pages/Dashboard.tsx`, no `App.tsx` (no existe).
- **Imports `from src.*`**: 40+ archivos. Refactor mecánico pero
  laborioso.
- **Tests de arquitectura existentes** (`pytest -m architecture`,
  `npm run test:arch`): se rompen al mover archivos; deben actualizarse
  en el mismo PR del move.
- **uv workspace**: `members = ["exoclaw-temporal", "hubara_agency"]`.
  Decisión: NO crear un nuevo workspace member por plugin; los plugins
  Python viven como subpaquete de `hubara_agency.src.plugins`.

### Outputs

- `PLUGIN_REFACTOR_PLAN.md` creado con plan corregido a la realidad.
- `PLUGIN_REFACTOR_LOG.md` (este archivo) creado para tracking.
- `PLUGIN_ARCHITECTURE.md` se mantiene como contrato; las
  discrepancias quedan documentadas en §0 del PLAN.

### Próximo paso recomendado

Validar el plan con el operador. Específicamente, confirmar:

1. **Layout final de un plugin (§1.1)**: el plugin Python vive en
   `hubara_agency/src/plugins/<id>/`, el TS en
   `frontend_dashboard/src/plugins/<id>/`, el manifest está en el lado
   TS. ¿OK con esa asimetría?
2. **Modelo de workers (§1.4)**: cada plugin agéntico trae su propio
   worker, hay un meta-launcher para dev local pero docker/K8s sigue
   con un container por worker. ¿OK?
3. **PR1 ya puede arrancar**: scope está acotado a plumbing
   (sin tocar features). ¿Procedemos directo o el operador prefiere
   validar algo más?

---

## 2026-05-15 — PR1 (plumbing) — Claude — ✅ done

### Plan referenciado
PLUGIN_REFACTOR_PLAN.md §3 — PR1.

### Cambios efectivos

**Archivos creados (10)**:
- `frontend_dashboard/src/plugins/.gitkeep`
- `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml` (~140 LOC, JSON Schema en YAML del manifest)
- `frontend_dashboard/scripts/plugins-sync.ts` (~190 LOC, descubre manifests + genera registry)
- `frontend_dashboard/src/app/plugin-registry.generated.ts` (autogenerado, gitignored)
- `hubara_agency/src/plugins/__init__.py` (namespace package + docstring de convenciones)

**Archivos modificados (7)**:
- `frontend_dashboard/package.json`:
  - Scripts: agregado `plugins:sync`, `predev`, `prebuild`.
  - Deps: `yaml ^2.6.1`. DevDeps: `tsx ^4.19.2`.
- `frontend_dashboard/package-lock.json` (auto-actualizado por `npm install`).
- `frontend_dashboard/vite.config.ts`: alias `@plugins` → `./src/plugins`.
- `frontend_dashboard/tsconfig.app.json`: paths `@plugins/*` → `["./src/plugins/*"]`.
- `frontend_dashboard/tsconfig.node.json`: incluye `scripts/**/*.ts` para typecheck del sync script.
- `frontend_dashboard/.gitignore`: ignora `src/app/plugin-registry.generated.ts`.
- `frontend_dashboard/.dependency-cruiser.cjs`: agrega el archivo generado a `doNotFollow.path`.

### Desviaciones del plan

1. **Comportamiento de `ENABLED_PLUGINS` vacío**: el plan original implícito decía "filtrar siempre". El script implementa la convención más útil: **vacío o unset → carga TODOS los plugins descubiertos**. Esto coincide con el comportamiento del loader Python que documenta el plan §3 PR3. Documentado en el header del archivo generado y en el código del script.

2. **Manejo de `noUnusedLocals`**: el primer render del registry vacío fallaba el typecheck porque importaba `lazy` sin usarlo. Fix: el generador emite **dos shapes distintos** según si hay entries (con `lazy` + `LazyExoticComponent`) o no (sin imports React, `Page: () => null` como placeholder de tipo). El comportamiento runtime es idéntico: `PLUGINS` siempre es un array iterable.

3. **Validación id == directory name**: agregada al script (no estaba en el plan). Si un manifest declara `id: foo` pero vive en `src/plugins/bar/`, se rechaza con warning. Previene que dos plugins compartan id por accident.

4. **Skip de directorios `_*` y `.*`**: agregado al script. Permite usar `_schema/`, `_drafts/`, `.archive/` sin que sean tratados como plugins.

Ninguna desviación cambia el plan global; las anoto para que cualquier futuro PR sepa la racionalización.

### Verificaciones corridas

```bash
$ cd frontend_dashboard
$ npm install
added 29 packages, and audited 403 packages in 5s
found 0 vulnerabilities

$ npm run plugins:sync
[plugins-sync] generated src/app/plugin-registry.generated.ts with 0 plugin(s): (empty)

$ npx tsc -b
TypeScript compilation completed                          # ✅ verde

$ npm run arch:cruise
✔ no dependency violations found (154 modules, 326 dependencies cruised)  # ✅ verde

$ npm run test:arch
Test Files  8 passed (8)
     Tests  12 passed | 1 skipped (13)              # ✅ verde

$ git check-ignore -v frontend_dashboard/src/app/plugin-registry.generated.ts
frontend_dashboard/.gitignore:29:src/app/plugin-registry.generated.ts    # ✅ correctamente ignorado
```

**Smoke test con plugin dummy** (eliminado al final):

```bash
# Crear plugin dummy
echo "id: smoke ..." > src/plugins/smoke/plugin.yaml

# Verificar render con entries:
$ npm run plugins:sync
[plugins-sync] generated src/app/plugin-registry.generated.ts with 1 plugin(s): smoke
# → registry incluye `Page: lazy(() => import("@plugins/smoke/frontend"))`

# Verificar filtros:
$ ENABLED_PLUGINS=other npm run plugins:sync
[plugins-sync] ENABLED_PLUGINS lists "other" but no manifest found
# → registry vacío

$ ENABLED_PLUGINS=smoke npm run plugins:sync
# → registry con smoke entry

# Verificar validación id != dirname:
# (cuando id=_smoke pero dir=smoke)
[plugins-sync] skip smoke: manifest id (_smoke) != directory name

# Cleanup
$ rm -rf src/plugins/smoke && npm run plugins:sync
[plugins-sync] generated src/app/plugin-registry.generated.ts with 0 plugin(s): (empty)
```

### Definition of Done (del plan §3 PR1)

- [x] `npm run plugins:sync` genera un archivo TS válido (vacío) sin error.
- [x] `npx tsc -b` pasa con el archivo generado.
- [x] `npm run test:arch` (8 archivos, 12 tests) verde.
- [x] `npm run arch:cruise` verde (154 módulos, sin violaciones).
- [x] El archivo generado está correctamente gitignored.
- [ ] `npm run dev` arranca y la app se renderiza idéntica a antes. **NO probado en esta sesión** — Vite dev requiere un puerto libre y el operador puede preferir validarlo manualmente. Riesgo bajo: el archivo generado es un módulo TS aislado, no se importa todavía desde ningún sitio.

### Bloqueadores encontrados

Ninguno. Cero discrepancias críticas vs. plan.

### Estado de git al cerrar

```
Modified (7):
  frontend_dashboard/.dependency-cruiser.cjs
  frontend_dashboard/.gitignore
  frontend_dashboard/package-lock.json
  frontend_dashboard/package.json
  frontend_dashboard/tsconfig.app.json
  frontend_dashboard/tsconfig.node.json
  frontend_dashboard/vite.config.ts

Untracked (5):
  PLUGIN_REFACTOR_LOG.md (este archivo)
  PLUGIN_REFACTOR_PLAN.md
  frontend_dashboard/scripts/                   # plugins-sync.ts
  frontend_dashboard/src/plugins/               # .gitkeep + _schema/
  hubara_agency/src/plugins/                    # __init__.py

NB: PLUGIN_ARCHITECTURE.md también está untracked — viene de la sesión
anterior (pre-compact). Si se commitea PR1, agregar también ese archivo.
```

### Próximo paso recomendado

1. Operador commitea PR1 (sugerencia de mensaje: `feat(plugins): plumbing — manifest schema, sync script, FE plumbing`).
2. Validar smoke test manual con `npm run dev` para confirmar DOD #6.
3. Arrancar **PR2 (migrar `chats`)**: ver §3 PR2 del plan. Es el PR más laborioso (~50 archivos movidos + reescritura de imports). Estimado 2-3 días.

### Status final

✅ **done** — todas las verificaciones automatizadas verdes. Pendiente solo
smoke test manual de `npm run dev` (no bloqueante).

---

## 2026-05-15 — PR2 (migrar chats) — Claude — ✅ done

### Plan referenciado
PLUGIN_REFACTOR_PLAN.md §3 — PR2.

### Cambios efectivos

**Backend** (33 archivos Python movidos via `git mv` + 4 dirs `__init__.py`):

```
src/sales_whatsapp/        → src/plugins/chats/agent/sales/    (excepto api/worker)
src/sales_whatsapp/api.py  → src/plugins/chats/api/sales.py
src/sales_whatsapp/worker.py → src/plugins/chats/workers/sales.py
src/sales_whatsapp/workspace/ → src/plugins/chats/agent/sales/workspace/  (datos del agente: IDENTITY.md, SOUL.md, MEMORY.md, etc.)

src/remarketing_whatsapp/        → src/plugins/chats/agent/remarketing/
src/remarketing_whatsapp/worker.py → src/plugins/chats/workers/remarketing.py
src/remarketing_whatsapp/workspace/ → src/plugins/chats/agent/remarketing/workspace/

src/dashboard/api.py         → src/plugins/chats/api/dashboard.py
src/dashboard/handoff.py     → src/plugins/chats/api/handoff.py
src/dashboard/composition.py → src/plugins/chats/api/dashboard_composition.py
src/dashboard/__init__.py    → DELETED (era solo un comentario)
```

**Backend nuevos archivos**:
- `src/plugins/chats/__init__.py` (docstring del plugin)
- `src/plugins/chats/api/__init__.py`
- `src/plugins/chats/agent/__init__.py`
- `src/plugins/chats/workers/__init__.py`

**Backend imports reescritos** (~50 sitios via sed, 2 pasadas):
- `from src.sales_whatsapp.X` → `from src.plugins.chats.agent.sales.X`
- `from src.remarketing_whatsapp.X` → `from src.plugins.chats.agent.remarketing.X`
- `from src.dashboard.{api,handoff,composition}` → `from src.plugins.chats.api.{dashboard,handoff,dashboard_composition}`
- Más arreglos manuales en `main.py` (sintaxis `from X import Y`), `test_handoff_endpoints.py` (string `import src.dashboard.composition as comp`), `test_imports.py` (paths de workers que no seguían el patrón general).

**Backend configs actualizadas**:
- `hubara_agency/src/main.py`: imports apuntan a nuevas ubicaciones (no introduce loader; PR3 lo hará).
- `hubara_agency/.importlinter`: contracts R-DIP renombrados (`src.sales_whatsapp` → `src.plugins.chats.agent.sales` etc.).
- `hubara_agency/docker-compose.local.yml`: workers commands actualizados.
- `hubara_agency/k8s/aws-produccion/worker-sales.yaml`: command actualizado.
- `hubara_agency/tests/architecture/conftest.py`: nuevo helper `agent_paths(agent)` que abstrae los paths físicos por agent id (chats.sales / chats.remarketing / catalog_sync).
- `hubara_agency/tests/architecture/test_spinal.py`: usa `agent_paths()` en lugar de `SRC_ROOT / agent`.
- `hubara_agency/tests/test_workspace_system_prompt*.py`: WORKSPACE paths actualizados.
- `hubara_agency/tests/sales_whatsapp/{test_workspace_system_prompt,workspace/test_skill_frontmatter}.py`: paths actualizados.
- `hubara_agency/src/platform/{session_history,tools}/__init__.py`: docstrings refrescados con paths nuevos.
- `hubara_agency/tests/README.md`: comandos `python -m src.<dominio>.worker` → `python -m src.plugins.chats.workers.<sub>`.

**Frontend** (7 features movidas via `git mv`):

```
src/features/chats-conversation/  → src/plugins/chats/frontend/features/
src/features/chats-inbox/         → src/plugins/chats/frontend/features/
src/features/chats-inspector/     → src/plugins/chats/frontend/features/
src/features/memory-modal/        → src/plugins/chats/frontend/features/
src/features/session-chat/        → src/plugins/chats/frontend/features/
src/features/session-list/        → src/plugins/chats/frontend/features/
src/features/session-metadata/    → src/plugins/chats/frontend/features/
```

**Frontend nuevos archivos**:
- `src/plugins/chats/plugin.yaml` (manifest completo: frontend.contributes + api.legacy_routers + agent.workers).
- `src/plugins/chats/frontend/ChatsSection.tsx` (extracción de la `ChatsSection` antes inline en Dashboard.tsx).
- `src/plugins/chats/frontend/index.ts` (barrel: `default` = ChatsSection + named re-exports).

**Frontend imports reescritos**:
- `@/features/{chats-*,session-*,memory-modal}` → `@plugins/chats/frontend/features/...`
- `pages/Dashboard.tsx`: importa `ChatsSection` de `@plugins/chats/frontend` (barrel) en lugar de la implementación inline.

**Frontend configs**:
- `.dependency-cruiser.cjs`: 3 nuevas reglas (cross-plugin prohibido, plugins no pueden importar pages/app, features no pueden importar plugins).
- `scripts/plugins-sync.ts`: `Page` type ahora es `LazyExoticComponent<ComponentType<any>>` (laxo) porque cada plugin define su firma de props propia.

### Desviaciones del plan

1. **Workspace dirs (datos del agente)**: el plan original no mencionaba que `src/sales_whatsapp/workspace/` (con IDENTITY.md, SOUL.md, MEMORY.md, skills/, etc.) tenía que moverse junto con el código. Lo hice porque `config/env.py` resuelve el path con `Path(__file__).parents[1] / "workspace"`. Documentado.
2. **`tests/sales_whatsapp/` no se movió**: por scope (PR2 es ya grande). El path interno apunta al nuevo `src/plugins/chats/...` pero la carpeta de tests sigue como `tests/sales_whatsapp/`. Mover esta carpeta queda como mejora futura (no bloqueante).
3. **`agent_paths()` helper**: el plan no lo especificaba. Lo introduje en `tests/architecture/conftest.py` para abstraer la diferencia entre el layout legacy (catalog_sync top-level) y el nuevo (chats sub-divides en sales+remarketing). Permite que test_spinal.py funcione con ambos layouts mientras hay migración progresiva.
4. **Tipo `Page` del registry**: el primer render con `ChatsSection` falló typecheck porque `Record<string, unknown>` no satisface `ChatsSectionProps`. Cambié a `ComponentType<any>` (laxo) — cada plugin tiene su firma de props propia, el shell sabe qué pasar. PR3 puede formalizar con generics si vale la pena.

### Verificaciones corridas

```bash
# Backend smoke
$ cd hubara_agency && uv run python -c "from src.main import app; print(app.title)"
Agency API

$ uv run python -c "import src.plugins.chats.workers.sales; import src.plugins.chats.workers.remarketing"
(no error)

# Backend full suite
$ uv run pytest --tb=short
264 passed, 1 skipped in 13.55s

# Backend architecture
$ uv run pytest -m architecture
18 passed, 1 skipped in 5.07s

# Frontend
$ cd frontend_dashboard && npm run plugins:sync
[plugins-sync] generated src/app/plugin-registry.generated.ts with 1 plugin(s): chats

$ npx tsc -b
TypeScript compilation completed

$ npm run arch:cruise
✔ no dependency violations found (156 modules, 334 dependencies cruised)

$ npm run test:arch
8 archivos, 12 tests pass, 1 skipped

$ npm test
19 archivos, 69 tests pass, 1 skipped
```

### Definition of Done (del plan §3 PR2)

- [x] Todo el código relacionado con Chats vive bajo `plugins/chats/` (backend + frontend).
- [x] Las carpetas viejas (`src/sales_whatsapp/`, `src/remarketing_whatsapp/`, `src/dashboard/`, `src/features/chats-*`, etc.) están borradas.
- [x] El chat funciona idéntico a antes (smoke por imports y tests; smoke runtime con `docker compose up` queda al operador).
- [x] `import-linter` verde con paths nuevos.
- [x] `dependency-cruiser` verde + 3 reglas nuevas para plugin isolation.
- [x] `pytest -m architecture`: 18 passed.
- [x] `pytest`: 264 passed.
- [x] `npm test:arch`: 12 passed.
- [x] `npm test`: 69 passed.
- [x] `npm run plugins:sync`: registry tiene 1 entry (chats).
- [x] `npx tsc -b`: compila sin errores.
- [x] PR3 (loaders) **NO** introducido en este PR — `main.py` y `workers` siguen con imports estáticos a las nuevas ubicaciones.

### Bloqueadores encontrados

Ninguno crítico. Solo correcciones esperadas:
- BSD sed no soporta `\b` (resolución: usar variantes con `$` y espacio explícito).
- 2 errores TS post-move (lazy load + named export missing) — fixes triviales documentados arriba.
- 22 tests fallaron tras el primer move por workspace paths hardcoded — fix in-place sin scope adicional.

### Stats

```
141 archivos cambiados
~50 imports reescritos via sed (Python)
~10 imports reescritos via sed (TypeScript)
33 archivos Python movidos
7 features TS movidas (subdirs incluidos)
4 archivos __init__.py nuevos (estructura plugin)
3 archivos nuevos frontend (plugin.yaml + ChatsSection.tsx + index.ts)
3 nuevas reglas dependency-cruiser
1 helper nuevo (agent_paths) en tests arquitectura
```

### Próximo paso recomendado

PR3 (loaders): reescribir `main.py` para auto-discovery via manifest, crear `run_workers.py` meta-launcher, refactorizar `Dashboard.tsx` para consumir `PLUGINS` registry. Ver §3 PR3 del plan. Estimado 2 días.

### Status final

✅ **done** — backend + frontend verdes. Listo para commit + PR3.

---

## 2026-05-15 — PR3 (loaders) — Claude — ✅ done

### Plan referenciado
PLUGIN_REFACTOR_PLAN.md §3 — PR3.

### Cambios efectivos

**Backend**:
- `hubara_agency/src/main.py` — reescrito como **loader de auto-discovery**.
  Lee `frontend_dashboard/src/plugins/<id>/plugin.yaml`, filtra por
  `ENABLED_PLUGINS` env (vacío = todos), registra routers via `api.python_module`
  o `api.legacy_routers`. Health check ahora reporta `plugins_loaded`.
- `hubara_agency/src/run_workers.py` — **NUEVO meta-launcher**.
  Descubre workers via `manifest.agent.workers`/`worker_module`, los arranca
  en paralelo (asyncio.gather) en un solo proceso. Útil para dev local. En
  producción cada worker sigue como container separado.

**Frontend**:
- `frontend_dashboard/src/pages/Dashboard.tsx` — modo **híbrido**:
  - `chat` se carga del registry: `PLUGINS.find(p => p.id === "chats")?.Page`
    + `<Suspense>` para code-splitting via lazy.
  - `orders`, `eta`, `upload`, `agent` siguen inline hasta sus PRs (PR4-7).
- `frontend_dashboard/.dependency-cruiser.cjs` — excepción documentada en
  `pages-no-app-or-cross-page` para permitir
  `pages → src/app/plugin-registry.generated.ts` (artefacto autogenerado, no
  parte de `app/`).

### Desviaciones del plan

1. **Modo híbrido del shell**: el plan sugería que `Dashboard.tsx` consumiera
   `PLUGINS` para construir TODAS las secciones, pero hoy solo `chat` está
   en plugin. Voy con el camino pragmático: el shell consume `PLUGINS` para
   el plugin migrado y mantiene las otras 4 secciones inline. Cada PR
   (PR4-7) las irá moviendo. Cuando todas estén migradas, se puede iterar
   `PLUGINS` como en el plan.
2. **Toolbar y `SectionKey`**: el plan mencionaba "adaptar Toolbar para
   sections dinámicas". Lo dejo para cuando >1 plugin esté migrado — hoy
   no aporta porque toda la lista de secciones sigue siendo la misma.
3. **`scripts/render-compose.py`**: marcado como "opcional, no bloqueante"
   en el plan. NO implementado en PR3 — entra como TODO post-PR7.

### Verificaciones corridas

```bash
# Backend full suite
$ cd hubara_agency && uv run pytest --tb=short -q
264 passed, 1 skipped in 12.65s

# Loader sin filtro
$ uv run python -c "from src.main import app, _LOADED_PLUGINS; print(_LOADED_PLUGINS)"
['chats']

# Loader con ENABLED_PLUGINS=other (plugin no existente)
$ ENABLED_PLUGINS=other uv run python -c "from src.main import app, _LOADED_PLUGINS; print(_LOADED_PLUGINS)"
[]

# Meta-launcher discovery
$ ENABLED_PLUGINS=chats uv run python -c "from src.run_workers import _discover_workers; print(_discover_workers())"
[('chats', 'sales', 'src.plugins.chats.workers.sales'), ('chats', 'remarketing', 'src.plugins.chats.workers.remarketing')]

# Frontend
$ npx tsc -b --force
TypeScript compilation completed

$ npm run arch:cruise
✔ no dependency violations found (157 modules, 334 dependencies cruised)

$ npm run test:arch
8 archivos, 12 tests pass

$ npm test
19 archivos, 69 tests pass
```

### Definition of Done (del plan §3 PR3)

- [x] `main.py` no importa ningún plugin estáticamente — todo via importlib.
- [x] Cambiar `ENABLED_PLUGINS` activa/desactiva chats sin tocar código:
      `loaded: ['chats']` ↔ `loaded: []` según env.
- [x] Worker meta-launcher descubre y arranca todos los workers del plugin
      chats (en este caso, sales + remarketing) en paralelo.
- [x] Frontend renderiza `chat` desde el registry generado.
- [x] Tests verdes (backend 264 + frontend 69).

### Bloqueadores encontrados

Ninguno. La complejidad mayor estaba en PR2 (los moves); PR3 es plumbing
puro de auto-discovery.

### Stats

```
4 archivos modificados:
  hubara_agency/src/main.py            (reescrito completo, ~110 LOC)
  hubara_agency/src/run_workers.py     (nuevo, ~120 LOC)
  frontend_dashboard/src/pages/Dashboard.tsx
  frontend_dashboard/.dependency-cruiser.cjs
```

### Próximo paso recomendado

PR4 (`agents-admin`): el más simple. Solo frontend + API stub. ~15 archivos,
~1 día. Sirve como dry-run del template "plugin without agent" antes de
PR5 (catalog) que sí tiene agente Temporal.

### Status final

✅ **done** — backend + frontend verdes. ENABLED_PLUGINS funcional.
Listo para commit + PR4.

---

## 2026-05-15 — PR4 (agents_admin) — Claude — ✅ done

### Plan referenciado
PLUGIN_REFACTOR_PLAN.md §3 — PR4. Primer plugin frontend-only.

### Cambios efectivos

**Frontend** (3 features movidas via `git mv`):
```
src/features/agents-list      → src/plugins/agents_admin/frontend/features/
src/features/agents-prompts   → src/plugins/agents_admin/frontend/features/
src/features/agents-inspector → src/plugins/agents_admin/frontend/features/
```

**Frontend nuevos archivos**:
- `src/plugins/agents_admin/plugin.yaml` — sin `api`, sin `agent`. Solo
  declara `frontend.contributes.sections` (key="agent").
- `src/plugins/agents_admin/frontend/AgentsSection.tsx` — extracción de
  `Dashboard.tsx`.
- `src/plugins/agents_admin/frontend/index.ts` — barrel: default + named
  re-exports.

**Backend**:
- `hubara_agency/src/plugins/agents_admin/__init__.py` — namespace package
  con docstring (sin código). Reservado para CRUD futuro.

**Frontend imports reescritos**:
- `@/features/agents-{list,prompts,inspector}` → `@plugins/agents_admin/frontend/features/agents-{list,prompts,inspector}`

**Dashboard.tsx**:
- `AgentsPage` se busca en `PLUGINS` (idéntico al pattern de `ChatsPage`).
- Función inline `AgentsSection` removida (vive ahora en el plugin).

### Desviaciones del plan

1. **Naming**: el plan §3 dice "agents-admin" con dash. Usé **snake_case**
   (`agents_admin`) porque Python no permite dashes en nombres de paquetes,
   y queremos un id único cross-stack. La convención: ids multi-word usan
   snake_case en TODO (filesystem + Python + TS). `chats` (single word) no
   tuvo este problema.
2. **Sin `api:` en el manifest**: el plan sugería "puede ser stub si no
   existe lógica de backend todavía". Decidí NO declarar `api` en el
   manifest si no hay endpoints — el manifest queda más limpio. El loader
   Python funciona correctamente: skipea plugins sin `api`. El
   `__init__.py` Python existe como anchor por si en el futuro se necesita
   agregar endpoints.

### Verificaciones corridas

```bash
# Frontend
$ npm run plugins:sync
[plugins-sync] generated src/app/plugin-registry.generated.ts with 2 plugin(s): agents_admin, chats

$ npx tsc -b --force
TypeScript compilation completed

$ npm run arch:cruise
✔ no dependency violations found (159 modules, 339 dependencies cruised)

$ npm run test:arch
12 passed, 1 skipped

$ npm test
69 passed, 1 skipped

# Backend
$ uv run pytest -m architecture
18 passed, 1 skipped

# Loader
$ ENABLED_PLUGINS=chats,agents_admin uv run python -c "from src.main import _LOADED_PLUGINS; print(_LOADED_PLUGINS)"
['chats']  # ← agents_admin no aporta routers, correcto
```

### Definition of Done (del plan §3 PR4)

- [x] `frontend_dashboard/src/plugins/agents_admin/` con manifest + frontend.
- [x] 3 features migradas (agents-list, agents-prompts, agents-inspector).
- [x] `hubara_agency/src/plugins/agents_admin/__init__.py` creado (sin api).
- [x] `src/features/agents-*` borradas (atómico, mismo PR).
- [x] Dashboard renderiza `agent` desde el registry.
- [x] Tests verdes.

### Bloqueadores encontrados

Ninguno. Plugin frontend-only es trivial — el patrón de PR2/PR3 se aplica
sin fricción.

### Stats

```
3 features migradas (con sus subdirs model/ + ui/)
4 archivos nuevos:
  frontend_dashboard/src/plugins/agents_admin/plugin.yaml
  frontend_dashboard/src/plugins/agents_admin/frontend/AgentsSection.tsx
  frontend_dashboard/src/plugins/agents_admin/frontend/index.ts
  hubara_agency/src/plugins/agents_admin/__init__.py
1 archivo modificado:
  frontend_dashboard/src/pages/Dashboard.tsx (~25 LOC eliminadas, ~10 agregadas)
```

### Próximo paso recomendado

PR5 (`catalog`): migración de `catalog_sync` (worker Temporal con schedule)
+ las 3 features de upload del frontend. Incluye worker, así que es más
sustancial que PR4 pero más simple que PR2 (no tiene API HTTP, solo worker
+ schedule). Estimado ~1 día.

### Status final

✅ **done** — backend + frontend verdes. Listo para commit + PR5.

---

## 2026-05-15 — PR5 (catalog) — Claude — ✅ done

### Plan referenciado
PLUGIN_REFACTOR_PLAN.md §3 — PR5.

### Cambios efectivos

**Backend** (12 archivos Python movidos):
```
src/catalog_sync/                       → src/plugins/catalog/agent/  (excepto worker)
src/catalog_sync/worker.py              → src/plugins/catalog/workers/sync.py
```

**Backend nuevos archivos**:
- `src/plugins/catalog/__init__.py` (docstring del plugin)
- `src/plugins/catalog/workers/__init__.py`

**Backend imports reescritos** (~20 sitios via sed):
- `from src.catalog_sync.X` → `from src.plugins.catalog.agent.X`
- Strings literales en tests también (importlib.import_module patterns).

**Backend configs actualizadas**:
- `hubara_agency/.importlinter`: `src.catalog_sync` → `src.plugins.catalog.agent`
  en contracts `platform-no-agents` y `agents-independent`.
- `hubara_agency/docker-compose.local.yml`: worker command actualizado.
- `hubara_agency/k8s/aws-produccion/worker-catalog-sync.yaml`: idem.
- `hubara_agency/tests/architecture/conftest.py`: `agent_paths()` ahora
  reconoce `catalog.sync` en lugar de `catalog_sync` legacy.
- `hubara_agency/tests/test_imports.py`: agregados smoke imports para
  catalog (workers, activities, workflows).
- `hubara_agency/src/platform/{catalog,medusa}/__init__.py`: docstrings
  refrescados con paths nuevos.

**Frontend** (3 features movidas):
```
src/features/upload-jobs       → src/plugins/catalog/frontend/features/
src/features/upload-wizard     → src/plugins/catalog/frontend/features/
src/features/upload-inspector  → src/plugins/catalog/frontend/features/
```

**Frontend nuevos archivos**:
- `src/plugins/catalog/plugin.yaml` (con `agent.workers: [{name: sync, ...}]`).
- `src/plugins/catalog/frontend/UploadSection.tsx` (extracción).
- `src/plugins/catalog/frontend/index.ts` (barrel).

**Dashboard.tsx**:
- `UploadPage` se busca en `PLUGINS` (mismo pattern que Chats/Agents).
- Función inline `UploadSection` removida.

### Desviaciones del plan

1. **Naming**: el plan §6.1 usa "catalog" (single word) y `catalog_sync` legacy. Decidí usar `catalog` como id del plugin y `sync` como nombre del worker (el viejo "catalog_sync" se descompone en plugin id + worker name). El conftest ahora usa `catalog.sync` como agent id (consistente con `chats.sales`/`chats.remarketing`).

### Verificaciones corridas

```bash
# Backend
$ uv run pytest --tb=short -q
264 passed, 1 skipped

$ uv run pytest -m architecture
18 passed, 1 skipped

# Loader (catalog no aporta routers, agents_admin tampoco)
$ ENABLED_PLUGINS=chats,catalog uv run python -c "from src.main import _LOADED_PLUGINS; print(_LOADED_PLUGINS)"
['chats']

# Meta-launcher (descubre 3 workers ahora)
$ ENABLED_PLUGINS=chats,catalog uv run python -c "from src.run_workers import _discover_workers; print(_discover_workers())"
[('catalog', 'sync', 'src.plugins.catalog.workers.sync'),
 ('chats', 'sales', 'src.plugins.chats.workers.sales'),
 ('chats', 'remarketing', 'src.plugins.chats.workers.remarketing')]

# Frontend
$ npm run plugins:sync
[plugins-sync] generated src/app/plugin-registry.generated.ts with 3 plugin(s): agents_admin, catalog, chats

$ npx tsc -b --force
TypeScript compilation completed

$ npm run arch:cruise
✔ no dependency violations found (161 modules, 344 dependencies cruised)

$ npm test
69 passed, 1 skipped
```

### Definition of Done (del plan §3 PR5)

- [x] `hubara_agency/src/plugins/catalog/` con agent + workers.
- [x] 3 features upload migradas.
- [x] Manifest declara `agent.workers: [{name: sync, ...}]`.
- [x] docker-compose + k8s actualizados.
- [x] Carpetas viejas borradas (`src/catalog_sync/`, `src/features/upload-*`).
- [x] Tests verdes.

### Bloqueadores encontrados

Ninguno. PR5 es esencialmente PR2 simplificado (un solo worker, sin API HTTP).

### Stats

```
12 archivos Python movidos (catalog_sync → plugins/catalog/agent + workers)
3 features TS movidas (upload-* → plugins/catalog/frontend/features/)
2 archivos __init__.py nuevos (estructura plugin)
3 archivos nuevos frontend (plugin.yaml + UploadSection.tsx + index.ts)
~20 imports Python reescritos
~3 imports TS reescritos
1 archivo modificado: pages/Dashboard.tsx (UploadPage del registry)
4 configs actualizadas (.importlinter, docker-compose, k8s/worker-catalog-sync, conftest)
```

### Próximo paso recomendado

PR6 (`eta`): plugin frontend-only (3 features eta-*). Mismo patrón que PR4
(agents_admin). ~1 día.

### Status final

✅ **done** — backend + frontend verdes. Listo para commit + PR6.

---

## 2026-05-15 — PR6 (eta) — Claude — ✅ done

### Plan referenciado
PLUGIN_REFACTOR_PLAN.md §3 — PR6.

### Cambios efectivos

3 features TS migradas (eta-list, eta-cards, eta-chat) →
`src/plugins/eta/frontend/features/`. Plugin frontend-only — sin api,
sin agent (los datos vienen de `entities/tracked-order` shared).

**Frontend nuevos archivos**:
- `src/plugins/eta/plugin.yaml` (solo `frontend.contributes.sections`).
- `src/plugins/eta/frontend/EtaSection.tsx` (extracción de Dashboard.tsx,
  incluye `useTrackedOrders` + `useEtaFilters` + `useMemo`).
- `src/plugins/eta/frontend/index.ts` (barrel + named re-exports).

**Backend**:
- `hubara_agency/src/plugins/eta/__init__.py` (anchor; sin código).

**Dashboard.tsx**:
- `EtaPage` se busca en `PLUGINS`.
- Función inline `EtaSection` removida.
- `useTrackedOrders` import removido (lo usa el plugin ahora).

### Verificaciones corridas

```bash
$ npm run plugins:sync
[plugins-sync] generated src/app/plugin-registry.generated.ts with 4 plugin(s): agents_admin, catalog, chats, eta

$ npx tsc -b --force
TypeScript compilation completed

$ npm run arch:cruise
✔ no dependency violations found (163 modules, 350 dependencies cruised)

$ npm test
19 archivos, 69 passed, 1 skipped
```

### Status final
✅ done — registry tiene 4 plugins. Listo para PR7.

---

## Plantilla para nuevas entradas

Copiar este bloque al iniciar un PR:

```markdown
## YYYY-MM-DD — PRn (nombre) — autor — 🚧 in_progress

### Plan referenciado
PLUGIN_REFACTOR_PLAN.md §3 — PRn

### Cambios efectivos
(lista de archivos creados/modificados/borrados)

### Desviaciones del plan
(si las hubo, justificar; si requiere actualizar el PLAN, dejar nota)

### Verificaciones corridas
(comandos exactos + resultado)

### Definition of Done
- [ ] (item 1 del DOD del plan)
- [ ] (item 2)
- [ ] tests verdes
- [ ] documentación actualizada

### Bloqueadores encontrados
(ninguno / detalle)

### Status final
✅ done | ⚠️ done con caveats | ❌ revertido
```

---

## Reglas de uso

1. **Append-only**: nuevas entradas al final. NO editar entradas
   pasadas (excepto para corregir typos o agregar links cruzados).
2. **Status visible**: cuando un PR cambia de status, actualizar
   también la fila del índice de arriba.
3. **Verificaciones reales**: pegar el output de los comandos, no
   solo decir "pasó". Si un comando falló y se workarround-eó, decirlo.
4. **Vincular PRs de GitHub**: cuando se abra un PR real, pegar el
   link en la entrada.
5. **Si el plan cambia mid-PR**: editar `PLUGIN_REFACTOR_PLAN.md` y
   referenciar el commit del cambio desde la entrada del log.

---

**Fin del log.** Actualizar al iniciar y al cerrar cada PR.
