# Reference — Schema completo del `plugin.yaml`

> **Cuándo leer esto:** necesitás el detalle exacto de un campo del
> manifest, o vas a extender el schema.
> **Source of truth:** `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml`.
> Esta reference es **espejo legible**; si diverge del schema YAML, el
> schema gana.

---

## §1. Top-level fields

```yaml
id: <required>                  # pattern ^[a-z][a-z0-9_]*$, debe matchear el dirname
version: <required>             # SemVer: ^[0-9]+\.[0-9]+\.[0-9]+(-[a-z0-9.]+)?$
display_name: <optional>        # nombre para mostrar en UI
description: <optional>         # descripción breve
depends_on: []                  # array de plugin ids — deps DURAS, enforced AL BOOT (ver abajo)
agentic: false                  # true si el plugin alberga agentes conversacionales (HONRADO — ver abajo)
consumes: []                    # casts declarados de contratos de otros plugins (canal 3 — ver abajo)
```

**Solo `id` y `version` son obligatorios.** Todo lo demás depende de
qué stacks contribuye el plugin.

### §1.1 `depends_on:` — deps DURAS, validadas al boot (post-refactor F1-F8)

Ya NO es un campo reservado: son dependencias **DURAS** (P-DEPS,
PLUGIN_CONTRACT.md §4) — el plugin NO funciona sin ellas (típicamente el
provider de un `consumes:`/cast, o una capability que vive en otro plugin,
ej. `eta → chats` por el ingest de WhatsApp). **ENFORCED AL BOOT** (gate
P-6): los 3 loaders (`main.py`, `run_workers.py`, `plugins-sync.ts`)
llaman `validate_enabled()` — habilitar un plugin sin sus deps mata el
boot con mensaje claro. Las transitions cross-plugin son SOFT y NO van
acá (el dispatcher las skipea si el target está apagado).

### §1.2 `agentic:` — HONRADO (post-refactor F1-F8)

Marca que el plugin alberga agentes conversacionales con workspace de
prompts (IDENTITY.md, SOUL.md, TOOLS.md, AGENTS.md, USER.md). Ya no es
decorativo: `agents_admin` solo expone (`GET /api/agents`) los agentes de
plugins **agentic + habilitados**. El gate P-17 exige coherencia:
`agentic: true` ⟺ ≥1 worker con bloque `dashboard:`.

### §1.3 `consumes:` — casts declarados (canal 3, PLUGIN_CONTRACT.md §5.3)

La ÚNICA forma de consumir DATOS de otro plugin:

```yaml
consumes:
  - provider: orders            # plugin id del dueño del contrato (∈ depends_on)
    contract: order@v1          # contrato versionado que el provider publica
    into: order-ref             # nombre de la entity/vista LOCAL del consumidor
    cast: api/order_actions     # módulo cast, path relativo al plugin
```

El cast corre **SERVER-SIDE** (reenvía al contrato HTTP publicado del
provider o usa un platform port); el frontend del consumidor define una
entity LOCAL que llama SOLO `/api/<este-id>/*`. Gates: P-14 (forma +
existencia del cast), P-9 (cero strings `/api/<otro>/` en frontends,
estricto), P-23 (literales `/api` pertenecen al owner del archivo).
Ejemplos vivos: chats (`order@v1` → `order-ref` vía `api/order_actions`),
agents_admin (`evals@v1` vía `api/evals`).

---

## §2. Bloque `frontend:` (consumido por `plugins-sync.ts`)

```yaml
frontend:
  entry: ./frontend             # default; path relativo al manifest al index.ts del frontend
  contributes:
    sidebar:                    # array de entradas del sidebar (reservado)
      - route: /chats
        label: Chats
        icon: chat              # debe existir en base (Icon.tsx) ∪ contribuciones (frontend/icons.tsx → PLUGIN_ICONS) — gate P-12; fallback: bot
        badge_query: ...        # opcional, query key de TanStack
    sections:                   # array de entradas del segmented control del Toolbar
      - key: chat               # único; usado por el shell para indexar
        label: Chats
        order: 1                # entero; orden en el Toolbar
        icon: chat
    dashboard_widgets:          # array (reservado)
      - id: chats-summary
        position: top-right
```

| Campo | Required | Tipo | Notas |
|---|---|---|---|
| `entry` | ❌ (default `./frontend`) | string | Path relativo al manifest |
| `contributes.sidebar` | ❌ | array | Solo declarativo hoy (no router) |
| `contributes.sections` | ❌ | array | Lo que el Toolbar muestra |
| `contributes.dashboard_widgets` | ❌ | array | Reservado para futuro |

### §2.1 Contrato del sync: bloque `frontend:` es el gate de inclusión

`scripts/plugins-sync.ts` usa la **presencia del bloque `frontend:`** como
switch para decidir si el plugin entra en `src/app/plugin-registry.generated.ts`
(consumido por `pages/Dashboard.tsx`). Reglas:

1. **Sin bloque `frontend:`** → plugin backend-only. El sync emite
   `[plugins-sync] skip <id>: backend-only` y NO lo agrega al registry.
   Caso canónico: `system_map` expone `/api/system-map/graph` y su UI vive
   en `system_explorer/` (container Vite separado).

2. **Con `frontend:` + entry inexistente en disco** → el sync emite
   `[plugins-sync] skip <id>: frontend.entry "..." does not exist` y aborta
   la inclusión. Defensa contra typos en `entry:`.

3. **Con `frontend:` + entry válido** → emite el entry con
   `Page: lazy(() => import("@plugins/<id>/frontend").then(assertPluginModule))`
   — el entry DEBE default-exportar el componente Page; `assertPluginModule`
   lo verifica EN COMPILACIÓN (`tsc` rompe si falta).

Si un plugin backend-only **declara `frontend:` por error** o un
frontend-only **omite el bloque por error**, Vite rompe con:

```
[plugin:vite:import-analysis] Failed to resolve import "@plugins/<id>/frontend"
```

Test que enforza el contrato:
`frontend_dashboard/src/test/architecture/test_plugin_registry.arch.test.ts`
(#19a + #19b). Ver `fsd-rules.md §2.15`.

---

## §3. Bloque `api:` (consumido por `src.main`)

```yaml
api:
  python_module: src.plugins.<id>.api      # ancla, módulo con `router` (APIRouter)
  prefix: /api/<id>                        # default `/api/<id>`, ignorado si hay legacy_routers
  tags: [<id>]                             # default [<id>], ignorado si hay legacy_routers
  legacy_routers:                          # array — GANA sobre python_module si presente
    - module: src.plugins.<id>.api.<sub>   # required
      prefix: /api                         # required
      tags: [<Tag>]                        # required
  migrations: ./api/migrations             # path al dir de alembic env (opcional, no usado hoy)
```

### §3.1 Política del loader (de `src.main`)

```
1. Si `legacy_routers` está y no vacío:
   - cada entry se registra con su prefix/tags propio.
   - `python_module` se IGNORA aunque exponga `router`.
2. Si solo `python_module` está y expone `router`:
   - se registra con `prefix` / `tags` del manifest (defaults `/api/<id>`, `[<id>]`).
3. Si `python_module` no expone `router` y no hay `legacy_routers`:
   - el plugin no contribuye HTTP — log debug + sigue arrancando.
4. Si hay error de import:
   - fail-fast (el boot rompe).
```

---

## §4. Bloque `agent:` (consumido por `src.run_workers` + `plugin_manifest.py` + `render-compose.py`)

```yaml
agent:
  python_module: src.plugins.<id>.agent    # ancla simbólica; reservado para introspección futura
  worker_module: src.plugins.<id>.workers.default   # ALTERNATIVA a `workers` (single-worker)
  workers:                                 # array — PREFERIDO post-PR11
    - name: <required>                     # nombre interno del worker
      module: <required>                   # python module con `async def main()` cuya PRIMERA
                                           # línea es ensure_plugin_enabled("<id>") (gate P-21)
      task_queue: <required>               # ← SSoT post-PR11; pattern ^[a-z][a-z0-9-]*$
      owns_route: <opcional>               # ruta de conversación que este worker POSEE (ver §4.3)
      route_workflow_id_template: "<prefijo>-{session_id}"   # requerido si hay owns_route
      deployment:                          # opcional — hint para K8s
        replicas: 1
        cpu_request: 100m
        cpu_limit: 500m
        memory_request: 256Mi
        memory_limit: 512Mi
        strategy: RollingUpdate            # o Recreate (single-writer)
        env_secrets:                       # array — env vars desde K8s Secrets
          - var: <required>                # nombre env var en el container
            secret: <name>                 # K8s Secret resource
            key: <name>                    # key dentro del Secret data
      compose:                             # opcional — input de render-compose.py
        env:                               # dict env var → valor (literal o ${HOST_VAR})
          TEMPORAL_URL: temporal:7233
          DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY}
        volumes:                           # array — volumes formato compose
          - hubara-vault-local:/app/hubara_vault
        depends_on:                        # array — service names del compose
          - temporal
          - litellm
        service_name: my-custom-name       # opcional — override del default `hubara-worker-<plugin>-<name>`
  task_queue: <queue>                      # opcional, default queue del modo (legacy)
  mode: session_based                      # opcional, alternativo: turn_based
  graph_spec: ./agent/graph.json           # opcional, LangGraph spec (deferred)
```

### §4.1 Required vs optional dentro de `agent.workers[]`

| Campo | Required | Notas |
|---|---|---|
| `name` | ✅ | snake_case, único por plugin |
| `module` | ✅ | módulo Python con `async def main()`; primera línea = `ensure_plugin_enabled("<id>")` (P-21, verificado por AST) |
| `task_queue` | ✅ (post-PR11) | sin esto, `TaskQueueMissingError` al boot |
| `owns_route` | ❌ (solo dueños de ruta conversacional) | ver §4.3; requiere `route_workflow_id_template` |
| `route_workflow_id_template` | ❌ (✅ si hay `owns_route`) | DEBE contener `{session_id}` |
| `deployment` | ❌ (recomendado) | hint K8s; sin esto, K8s manifest se mantiene a mano |
| `deployment.replicas` | ❌ (default 1 en K8s) | int ≥ 1 |
| `deployment.env_secrets` | ❌ | array; cada entry necesita `var` |
| `compose` | ❌ (recomendado para dev local) | sin esto, `render-compose.py` skipea ese worker |
| `compose.env` | ❌ | dict |
| `compose.volumes` | ❌ | array |
| `compose.depends_on` | ❌ | array |

### §4.2 `worker_module` (single-worker shortcut, legacy)

```yaml
agent:
  worker_module: src.plugins.<id>.workers.default
```

Equivalente a:

```yaml
agent:
  workers:
    - { name: default, module: src.plugins.<id>.workers.default, task_queue: queue-<id>-default }
```

**Recomendado:** usar `workers:` siempre, aunque sea un solo worker. Da
explícito el `task_queue` y permite extender después.

### §4.3 `owns_route` + `route_workflow_id_template` (route registry, post-refactor F1-F8)

Declaran la ruta de conversación (`active_route` en metadata) que el
worker **POSEE**. El ruteo de inbounds la resuelve genéricamente del
**route registry** (`src/platform/routing.py`) — ningún agente nombra a
otro. `ROUTE_ETA` **ya no existe** en `platform/constants.py`: solo
quedan las rutas core (`ventas`/`remarketing`/`humano`), que NO son
registrables. `route_workflow_id_template` (ej. `"eta-{session_id}"`) es
la ÚNICA copia del template en el sistema: el registry la usa para rutear
y el gate **P-18** ata a su prefijo los `workflow_id_template` de las
transitions de OTROS manifests que targetean a este worker.

```yaml
# real — plugins/eta/plugin.yaml (extracto)
agent:
  workers:
    - name: eta
      module: src.plugins.eta.workers.eta
      task_queue: queue-eta-agent
      owns_route: eta
      route_workflow_id_template: "eta-{session_id}"
```

---

## §5. Bloque `jobs:` (reservado, deferred)

```yaml
jobs:
  - id: refresh-etas              # required
    schedule: "*/15 * * * *"      # required, crontab string
    handler: jobs.refresh_etas    # required, Python module:function
```

**Hoy no se aplica** (no hay cron runner formal). Declarativo solo.

---

## §6. Bloque `wiring_intents:` (declarativo)

```yaml
wiring_intents:
  db_tables: []                   # array de strings (reservado, no usado)
  s3_buckets: []                  # array (reservado)
  filesystem_volumes:             # array — para docs / setup multi-tenant futuro
    - hubara-vault
  env_vars_required:              # array — para checklist setup del operador
    - DEEPSEEK_API_KEY
    - WHATSAPP_PHONE_NUMBER_ID
```

**No se aplica automáticamente**, pero ya NO es solo documental: el gate
**P-25** verifica `wiring_intents.env_vars_required` ⊆ env del compose
renderizado (un var declarado que ningún worker recibe = drift). Sirve
además para docs + setup multi-tenant futuro.

---

## §7. Bloque `permissions:` (reservado, deferred)

```yaml
permissions:
  reads: [customers]              # array de plugin ids que leemos via REST público
  writes: [orders]                # array de plugin ids que mutamos via REST público
```

**Hoy no se aplica.** Reservado para formalizar inter-plugin access
control cuando aparezca el caso real.

---

## §8. Ejemplos por template

### §8.1 Template A — frontend-only (manifest mínimo)

```yaml
# frontend_dashboard/src/plugins/orders/plugin.yaml
id: orders
version: 0.1.0
display_name: Orders
description: Tablero kanban de órdenes.

frontend:
  entry: ./frontend
  contributes:
    sections:
      - { key: orders, label: Orders, order: 2, icon: workflow }
    sidebar:
      - { route: /orders, label: Orders, icon: workflow }

wiring_intents:
  env_vars_required: []
```

### §8.2 Template B — frontend + API

```yaml
id: reports
version: 0.1.0
display_name: Reports
description: Reportes financieros con export CSV.

frontend:
  entry: ./frontend
  contributes:
    sections:
      - { key: reports, label: Reports, order: 7, icon: file }
    sidebar:
      - { route: /reports, label: Reports, icon: file }

api:
  python_module: src.plugins.reports.api.routes
  prefix: /api/reports
  tags: [Reports]

wiring_intents:
  env_vars_required: []
```

### §8.3 Template C — frontend + worker

```yaml
# frontend_dashboard/src/plugins/catalog/plugin.yaml (real)
id: catalog
version: 0.1.0
display_name: Catalog Sync
description: Sincronización del catálogo de productos (Medusa → snapshot filesystem).

frontend:
  entry: ./frontend
  contributes:
    sections:
      - { key: upload, label: Catalog, order: 4, icon: pkg }
    sidebar:
      - { route: /catalog, label: Catalog, icon: pkg }

agent:
  python_module: src.plugins.catalog.agent
  workers:
    - name: sync
      module: src.plugins.catalog.workers.sync
      task_queue: queue-catalog-sync
      deployment:
        replicas: 1                       # single writer (race en os.replace)
        strategy: Recreate                # evita dos pods escribiendo
        cpu_request: 100m
        memory_request: 256Mi
        env_secrets:
          - { var: MEDUSA_BASE_URL,    secret: hubara-medusa-secret, key: MEDUSA_BASE_URL }
          - { var: MEDUSA_ADMIN_TOKEN, secret: hubara-medusa-secret, key: MEDUSA_ADMIN_TOKEN }
      compose:
        env:
          TEMPORAL_URL: temporal:7233
          WORKSPACE_VAULT_DIR: /app/hubara_vault
          CATALOG_SNAPSHOT_DIR: /app/hubara_vault/catalog
          CATALOG_MAX_AGE_MINUTES: "30"
          MEDUSA_BASE_URL: ${MEDUSA_BASE_URL}
          MEDUSA_ADMIN_TOKEN: ${MEDUSA_ADMIN_TOKEN}
        volumes:
          - hubara-vault-local:/app/hubara_vault
        depends_on: [temporal]

wiring_intents:
  filesystem_volumes: [hubara-vault]
  env_vars_required:
    - TEMPORAL_URL
    - CATALOG_SNAPSHOT_DIR
    - MEDUSA_BASE_URL
    - MEDUSA_ADMIN_TOKEN
```

### §8.4 Template D — full-stack agéntico

```yaml
# frontend_dashboard/src/plugins/chats/plugin.yaml (real, abreviado)
id: chats
version: 0.1.0
display_name: Chats
description: Conversaciones WhatsApp con agente Temporal (sales + remarketing) + dashboard SSE + handoff humano.
agentic: true                    # workers conversacionales con workspace (P-17)

# Dep DURA (P-6): el canvas de pago consume order@v1 de `orders` vía cast.
depends_on: [orders]

# Canal 3 (PLUGIN_CONTRACT.md §5.3) — cast declarado (P-14). El frontend
# de chats SOLO ve su entity local `order-ref` y llama /api/chats/*.
consumes:
  - provider: orders
    contract: order@v1
    into: order-ref
    cast: api/order_actions

frontend:
  entry: ./frontend
  contributes:
    sections:
      - { key: chat, label: Chats, order: 1, icon: chat }
    sidebar:
      - { route: /chats, label: Chats, icon: chat }

api:
  python_module: src.plugins.chats.api      # ignorado por legacy_routers
  prefix: /api/chats                        # ignorado
  tags: [Chats]                             # ignorado
  legacy_routers:
    - { module: src.plugins.chats.api.sales,     prefix: /api,           tags: [WhatsApp_Sales_Domain] }
    - { module: src.plugins.chats.api.dashboard, prefix: /api/dashboard, tags: [Dashboard] }
    - { module: src.plugins.chats.api.handoff,   prefix: /api/dashboard, tags: [Dashboard_Handoff] }

agent:
  python_module: src.plugins.chats.agent
  workers:
    - name: sales
      module: src.plugins.chats.workers.sales
      task_queue: queue-sales-agent
      deployment:
        replicas: 3
        cpu_request: 500m
        memory_request: 512Mi
        env_secrets:
          - { var: DEEPSEEK_API_KEY,        secret: hubara-llm-secret,      key: DEEPSEEK_API_KEY }
          - { var: WHATSAPP_PHONE_NUMBER_ID, secret: hubara-whatsapp-secret, key: WHATSAPP_PHONE_NUMBER_ID }
          - { var: WHATSAPP_ACCESS_TOKEN,   secret: hubara-whatsapp-secret, key: WHATSAPP_ACCESS_TOKEN }
          - { var: WHATSAPP_VERIFY_TOKEN,   secret: hubara-whatsapp-secret, key: WHATSAPP_VERIFY_TOKEN }
      compose:
        env:
          TEMPORAL_URL: temporal:7233
          API_BASE_LLMLITE: http://litellm:4000
          WORKSPACE_VAULT_DIR: /app/hubara_vault
          DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY}
          WHATSAPP_PHONE_NUMBER_ID: ${WHATSAPP_PHONE_NUMBER_ID}
          WHATSAPP_ACCESS_TOKEN: ${WHATSAPP_ACCESS_TOKEN}
          WHATSAPP_VERIFY_TOKEN: ${WHATSAPP_VERIFY_TOKEN}
        volumes:
          - hubara-vault-local:/app/hubara_vault
        depends_on: [temporal, litellm]

    - name: remarketing
      module: src.plugins.chats.workers.remarketing
      task_queue: queue-remarketing-agent
      deployment:
        replicas: 1
        cpu_request: 250m
        memory_request: 384Mi
        env_secrets:
          # (mismas env_secrets que sales)
      compose:
        # (similar al sales, sin API_BASE_LLMLITE+catalog_snapshot)

wiring_intents:
  filesystem_volumes: [hubara-vault]
  env_vars_required:
    - DEEPSEEK_API_KEY
    - WHATSAPP_PHONE_NUMBER_ID
    - WHATSAPP_ACCESS_TOKEN
    - WHATSAPP_VERIFY_TOKEN
    - TEMPORAL_URL
    - WORKSPACE_VAULT_DIR
```

---

## §9. Validation flow

```
[plugin.yaml]
     │
     ▼
[plugins-sync.ts] ──── valida `id`, `version`, `frontend.contributes` +
     │                 validate_enabled (deps duras, P-6). Si rompe, EXIT 1.
     ▼
[src.main loader] ──── validate_enabled + valida `api`, importa
     │                 `python_module`/`legacy_routers`. Si rompe, fail-fast.
     ▼
[src.run_workers] ──── validate_enabled + valida `agent.workers[]`. Si rompe,
     │                 RuntimeError. (Cada worker además se auto-gatea con
     │                 ensure_plugin_enabled() — P-21.)
     ▼
[tests/plugins/test_premortem_invariants.py +
 tests/architecture/test_plugin_conformance.py] ──── cross-check con K8s,
     │                 schema regex, compose drift (P-20), routes (P-18), etc.
     ▼
[plugin operacional]
```

Cualquier rompe en este flow es **fail-fast intencional**. Mejor caer
rápido al boot que servir un endpoint silenciosamente ausente.

---

## §10. Extender el schema

**Solo en PR explícito de architecture-change** (no feature task; el PR
lleva el label `architecture-change` y localmente los tests corren con
`ARCH_CHANGE_APPROVED=1`):

1. ADR documentando el campo nuevo + por qué.
2. Editar `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml`.
3. Editar `frontend_dashboard/scripts/plugins-sync.ts` si frontend lo
   consume.
4. Editar `hubara_agency/src/main.py`, `src/run_workers.py`,
   `src/platform/plugin_manifest.py` o `scripts/render-compose.py`
   según qué loader lo consume.
5. **REGLA DE ORO: ningún campo nuevo sin su check de conformidad en el
   MISMO PR** (`tests/architecture/test_plugin_conformance.py`); sumar
   invariante en `tests/plugins/test_premortem_invariants.py` si aplica.
6. Update reference `references/manifest-schema.md` (este archivo).
   Referencia canónica del protocolo: `PLUGIN_PROTOCOL_fable.md` +
   `PLUGIN_CONTRACT.md` (repo root).

**Feature task NUNCA agrega campo al schema** — bloquea con
`requires_planner_update`.

---

**Fin reference.**
