# Plugin Architecture — AgencyHubara

> **Propósito de este documento.** Este es el contrato y el plan de refactor para
> introducir un sistema de plugins composable end-to-end (frontend Tauri +
> FastAPI + worker Temporal). Está escrito para sobrevivir un `/compact` y
> guiar la primera refactorización de las funcionalidades existentes
> (`sales_whatsapp`, `remarketing_whatsapp`, `catalog_sync`, `dashboard`).
>
> **Audiencia primaria:** future-me (Claude post-compact) implementando el
> primer refactor. Si lees esto y tienes dudas sobre el porqué de una decisión,
> NO la cuestiones — están justificadas en la sección §11.

---

## §1. Resumen ejecutivo

AgencyHubara es un **OS de gestión multi-tenant** desplegable en infra propia
por empresa (5 empresas planeadas, sin ambición de escala mundial inmediata).
La mayoría del producto es código deterministico clásico (Órdenes, Ads,
Productos, Datos, Bases). **Una sola sección es agentic** (Chats), corriendo
sobre `exoclaw-temporal` (DEHA architecture).

El objetivo del refactor es habilitar **composabilidad por plugins**:

- Cada feature vertical (frontend + REST + opcional worker Temporal + jobs +
  migrations) vive en su propia carpeta `plugins/<id>/` autocontenida.
- Múltiples agentes/devs pueden trabajar en plugins distintos **en paralelo
  sin conflictos de merge en archivos centrales**.
- Cada empresa habilita un subset de plugins vía variable `ENABLED_PLUGINS`,
  que se inyecta al boot de los 3 stacks.

---

## §2. Decisiones arquitectónicas firmes

Estas decisiones están cerradas. **No reabrir sin explícita instrucción del
usuario.**

| # | Decisión | Razón |
|---|---|---|
| D1 | **Monorepo único** + N stacks Terraform por tenant | Mantenimiento simple; aislamiento de cuotas/costos vía infra separada. |
| D2 | **Temporal sigue siendo el motor de durabilidad** | Maduro, ya en uso, DEHA validado. |
| D3 | **El resto del OS = FastAPI + Postgres + jobs programados** | La mayoría no son workflows durables; son CRUD/cron/state machines. |
| D4 | **NO adoptar AgentSpan** (ni Conductor, ni Java) | v0.1.10, sin respaldo comercial, JVM en plano operacional Python/TS = asimétrico. |
| D5 | **NO adoptar LangGraph como orquestador supremo** | Rompe el modelo session-based de exoclaw. Si se usa, vive DENTRO de actividades Temporal, sin checkpoints propios. |
| D6 | **NO adoptar Prefect/Dagster** ahora | Segundo motor de durabilidad innecesario para 5 empresas. |
| D7 | **Plugin contract = `plugin.yaml`** unificado para los 3 stacks | Una sola fuente de verdad por feature. |
| D8 | **Auto-discovery por filesystem en los 3 loaders** | Cero archivos centrales editados al agregar un plugin (paralelismo libre de conflicts). |
| D9 | **Frontend registry generado** (no commiteado) | `plugin-registry.generated.ts` se reconstruye en build; cero conflicts. |
| D10 | **"Instalar plugin desde la UI" = trigger CI/CD**, NO hot-load | Auditable, reversible, realista para 5 empresas. |
| D11 | **Aislamiento entre plugins es vía REST público**, NO acceso directo a DB de otro plugin | Evita acoplamientos invisibles. |

---

## §3. Las tres reglas no negociables

Si alguien (humano o agente) siente la tentación de violarlas, **el diseño
está mal — no la regla**.

### R1 — Cero registros centrales editados al agregar componentes

Los siguientes archivos **se escriben UNA vez** y nunca más se editan al
agregar plugins:

- `hubara_agency/src/main.py` — un solo loop de auto-discovery.
- `exoclaw-temporal/src/<mode>/worker.py` — un solo loop de auto-discovery.
- `frontend_dashboard/src/app/App.tsx` — consume el registry generado.

### R2 — Generated files no se commitean

`frontend_dashboard/src/app/plugin-registry.generated.ts` vive en
`.gitignore`. Se reconstruye con `pnpm plugins:sync` (pre-commit hook + build
step). Dos branches paralelos que agregan plugins NUNCA tocan el mismo
archivo fuente.

### R3 — Cada plugin es una carpeta autocontenida

Toda la lógica de un plugin vive bajo `plugins/<id>/`. Si necesita afectar
algo "central" (ej. una migration compartida, un CSS token global), declara
un `wiring_intent` en su `plugin.yaml` que un merger aplica. Esto preserva
el patrón ya existente con `exoclaw-merger-archon`.

---

## §4. El contrato `plugin.yaml`

Schema único leído por los 3 stacks. Ejemplo completo:

```yaml
# plugins/order-tracking/plugin.yaml
id: order-tracking
version: 0.1.0
display_name: Seguimiento de Pedidos
description: Tablero kanban de órdenes con estados y ETAs.

depends_on: []                 # otros plugin ids requeridos

# ── Frontend (consumido por scripts/plugins-sync.ts) ────────────
frontend:
  entry: ./frontend            # carpeta con index.ts que exporta {Page, sidebarItem}
  contributes:
    sidebar:
      - { route: /orders, label: Órdenes, icon: workflow, badge_query: ordersPending }
    dashboard_widgets:
      - { id: orders-summary, position: top-right }

# ── API REST (consumido por main.py auto-discovery) ─────────────
api:
  module: api                  # plugins/order-tracking/api/__init__.py exporta `router`
  prefix: /api/orders
  tags: [Orders]
  migrations: ./api/migrations # alembic env per-plugin (opcional)

# ── Agente Temporal (opcional) ──────────────────────────────────
agent:
  module: agent                # exporta WORKFLOWS, ACTIVITIES, TOOLS
  task_queue: orders-tq        # opcional; default = task queue del modo
  graph_spec: ./agent/graph.json  # opcional; LangGraph spec dentro de actividad

# ── Jobs programados (opcional) ─────────────────────────────────
jobs:
  - { id: refresh-etas, schedule: "*/15 * * * *", handler: jobs.refresh_etas }

# ── Wiring intents (cosas que tocan archivos centrales) ─────────
wiring_intents:
  db_tables: [orders, order_events]
  s3_buckets: [order-docs]
  env_vars_required: [SHIPPING_PROVIDER_API_KEY]

# ── Permisos cross-plugin (opcional) ────────────────────────────
permissions:
  reads: [customers]           # via REST público de otros plugins
  writes: [orders]
```

**Campos mínimos obligatorios**: `id`, `version`. Todo lo demás es opcional
según qué capas el plugin habita.

---

## §5. Los tres loaders

### §5.1 — FastAPI loader (escrito UNA vez)

Reemplaza el `include_router()` estático actual en
`hubara_agency/src/main.py`.

**Estado actual** (a refactorizar):

```python
# hubara_agency/src/main.py — ANTES
from src.sales_whatsapp import api as whatsapp_api
from src.dashboard import api as dashboard_api
from src.dashboard import handoff as dashboard_handoff

app.include_router(whatsapp_api.router, prefix="/api", tags=["WhatsApp_Sales_Domain"])
app.include_router(dashboard_api.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(dashboard_handoff.router, prefix="/api/dashboard", tags=["Dashboard_Handoff"])
```

**Estado objetivo**:

```python
# hubara_agency/src/main.py — DESPUÉS
import importlib, os, yaml
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Agency API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

ENABLED = set(os.environ.get("ENABLED_PLUGINS", "").split(","))
PLUGINS_DIR = Path(__file__).parent.parent.parent / "plugins"

for entry in sorted(PLUGINS_DIR.iterdir()):
    if not entry.is_dir() or entry.name not in ENABLED:
        continue
    manifest_path = entry / "plugin.yaml"
    if not manifest_path.exists():
        continue
    manifest = yaml.safe_load(manifest_path.read_text())
    api_cfg = manifest.get("api")
    if not api_cfg:
        continue
    module_path = f"plugins.{entry.name}.{api_cfg['module']}"
    mod = importlib.import_module(module_path)
    app.include_router(mod.router,
                       prefix=api_cfg.get("prefix", f"/api/{entry.name}"),
                       tags=api_cfg.get("tags", [entry.name]))

@app.get("/")
def health_check():
    return {"status": "ok", "plugins_loaded": sorted(ENABLED)}
```

**Regla**: agregar un plugin NUNCA edita este archivo.

### §5.2 — Temporal worker loader (escrito UNA vez por modo)

Hay dos workers en exoclaw-temporal: `session_based/worker.py` y
`turn_based/worker.py`. Cada uno aplica el mismo patrón.

**Estado objetivo** (template — ajustar imports al modo):

```python
# exoclaw-temporal/src/session_based/worker.py — DESPUÉS
import importlib, os, yaml
from pathlib import Path
from temporalio.client import Client
from temporalio.worker import Worker

ENABLED = set(os.environ.get("ENABLED_PLUGINS", "").split(","))
PLUGINS_DIR = Path(__file__).parent.parent.parent.parent / "plugins"
MODE = "session_based"  # o "turn_based"

def discover():
    workflows, activities, tool_factories = [], [], []
    for entry in sorted(PLUGINS_DIR.iterdir()):
        if not entry.is_dir() or entry.name not in ENABLED:
            continue
        manifest = yaml.safe_load((entry / "plugin.yaml").read_text())
        agent_cfg = manifest.get("agent")
        if not agent_cfg:
            continue
        # Solo cargar plugins compatibles con este modo
        if agent_cfg.get("mode", MODE) != MODE:
            continue
        try:
            mod = importlib.import_module(f"plugins.{entry.name}.{agent_cfg['module']}")
        except ImportError as e:
            print(f"[worker] skip {entry.name}: {e}")
            continue
        workflows.extend(getattr(mod, "WORKFLOWS", []))
        activities.extend(getattr(mod, "ACTIVITIES", []))
        tool_factories.extend(getattr(mod, "TOOL_FACTORIES", []))
    return workflows, activities, tool_factories

async def main():
    client = await Client.connect(os.environ["TEMPORAL_HOST"])
    workflows, activities, tool_factories = discover()
    # Registrar tools usando el patrón existente register_tool_extension
    from src.platform.tool_extensions import register_tool_extension
    for key, factory in tool_factories:
        register_tool_extension(key, factory)
    worker = Worker(
        client,
        task_queue=f"exoclaw-{MODE.replace('_', '-')}",
        workflows=workflows,
        activities=activities,
    )
    await worker.run()
```

**Cada plugin con agente expone** en `plugins/<id>/agent/__init__.py`:

```python
from .workflows import OrderTrackingWorkflow
from .activities import lookup_order, update_eta
from .tools import order_lookup_tool_factory

WORKFLOWS = [OrderTrackingWorkflow]
ACTIVITIES = [lookup_order, update_eta]
TOOL_FACTORIES = [("orders.lookup", order_lookup_tool_factory)]
```

**Compatibilidad con `tool_extensions.py` existente**: el patrón
`register_tool_extension(key, factory)` ya implementa convention-based
registration. El loader lo invoca en boot iterando sobre `TOOL_FACTORIES`
de cada plugin. **Ese módulo se conserva tal cual.**

### §5.3 — Frontend Tauri sync script (escrito UNA vez)

```typescript
// frontend_dashboard/scripts/plugins-sync.ts
import { readFileSync, writeFileSync, readdirSync, existsSync } from "fs";
import { join } from "path";
import { parse } from "yaml";

const ENABLED = new Set((process.env.ENABLED_PLUGINS || "").split(","));
const PLUGINS_DIR = join(__dirname, "..", "..", "plugins");

const entries: any[] = [];
for (const id of readdirSync(PLUGINS_DIR).sort()) {
  if (!ENABLED.has(id)) continue;
  const manifestPath = join(PLUGINS_DIR, id, "plugin.yaml");
  if (!existsSync(manifestPath)) continue;
  const manifest = parse(readFileSync(manifestPath, "utf-8"));
  if (!manifest.frontend) continue;
  entries.push({ id, ...manifest.frontend });
}

const ts = `// AUTO-GENERATED by scripts/plugins-sync.ts — DO NOT EDIT
import { lazy } from "react";

export type PluginEntry = {
  id: string;
  sidebar: Array<{ route: string; label: string; icon: string }>;
  dashboardWidgets: Array<{ id: string; position: string }>;
  Page: React.LazyExoticComponent<any>;
};

export const PLUGINS: PluginEntry[] = [
${entries.map(e => `  {
    id: ${JSON.stringify(e.id)},
    sidebar: ${JSON.stringify(e.contributes?.sidebar || [])},
    dashboardWidgets: ${JSON.stringify(e.contributes?.dashboard_widgets || [])},
    Page: lazy(() => import("@plugins/${e.id}/frontend")),
  },`).join("\n")}
];
`;

writeFileSync(join(__dirname, "..", "src", "app", "plugin-registry.generated.ts"), ts);
console.log(`[plugins-sync] generated registry with ${entries.length} plugins`);
```

**Integración**:

- `package.json`: `"plugins:sync": "tsx scripts/plugins-sync.ts"`.
- `vite.config.ts`: alias `@plugins` → `../plugins/`.
- Pre-commit hook (husky o equivalente): correr `plugins:sync` antes de
  commit si cambió algún `plugin.yaml`.
- `tauri build`: corre `plugins:sync` antes del build.
- `.gitignore`: `frontend_dashboard/src/app/plugin-registry.generated.ts`.

**App.tsx consume el registry**:

```tsx
// frontend_dashboard/src/app/App.tsx — DESPUÉS
import { PLUGINS } from "./plugin-registry.generated";

// El sidebar se construye desde PLUGINS:
const sidebarItems = PLUGINS.flatMap(p => p.sidebar);
// Las rutas también:
const routes = PLUGINS.map(p => ({ path: p.sidebar[0]?.route, element: <p.Page /> }));
```

---

## §6. Inventario del estado actual

### §6.1 — `hubara_agency/src/`

```
catalog_sync/         ← dominio: sincronización de productos (refactor → plugin)
dashboard/            ← API SSE + handoff humano (refactor → plugin "core")
platform/             ← infra compartida; SE MANTIENE (no es plugin)
  ├── catalog/
  ├── medusa/
  ├── session_history/
  ├── temporal/
  ├── tools/
  ├── whatsapp/
  ├── tool_extensions.py  ← patrón ya alineado con plugin model
  ├── registries.py
  ├── workflow_helpers.py
  └── ...
remarketing_whatsapp/ ← dominio agéntico (refactor → plugin)
sales_whatsapp/       ← dominio agéntico (refactor → plugin)
tests/                ← SE MANTIENE
main.py               ← SE REESCRIBE (loader)
```

### §6.2 — `exoclaw-temporal/src/`

```
session_based/   ← modo (chat largo con signals)
  ├── activities/
  ├── config/
  ├── tools/
  ├── use_cases/
  ├── workflows/
  ├── workspace/
  ├── api.py
  ├── composition.py
  ├── contracts.py
  ├── parsers.py
  ├── prompts.py
  ├── state.py
  └── worker.py    ← SE REESCRIBE (loader)
turn_based/      ← modo (one workflow per turn)
  ├── ...
  └── worker.py    ← SE REESCRIBE (loader)
```

**Decisión**: los modos `session_based` y `turn_based` se mantienen como
runtimes separados. Los plugins agénticos declaran a qué modo pertenecen
en `agent.mode` del manifest.

### §6.3 — `frontend_dashboard/src/`

```
app/                  ← SE MANTIENE; aquí vivirá plugin-registry.generated.ts
entities/             ← entidades cross-plugin (chat, message, session, agent, order, ...)
                       SE MANTIENEN como librería compartida
features/             ← 19 features actuales; se agrupan por plugin destino (ver §7)
pages/Dashboard.tsx   ← SE REESCRIBE para consumir PLUGINS registry
shared/               ← primitivas UI; SE MANTIENE
```

**19 features actuales agrupadas por plugin de destino**:

| Plugin destino | Features que migran |
|---|---|
| `chats` | chats-conversation, chats-inbox, chats-inspector, memory-modal, session-chat, session-list, session-metadata |
| `agents` | agents-inspector, agents-list, agents-prompts |
| `orders` | orders-board, orders-filters, orders-inspector |
| `eta` | eta-cards, eta-chat, eta-list |
| `catalog` | upload-inspector, upload-jobs, upload-wizard |

---

## §7. Estado objetivo — layout

```
AgencyHubara/
├── plugins/                              ← NUEVO
│   ├── chats/                            ← migración de sales_whatsapp + remarketing_whatsapp + dashboard chat
│   │   ├── plugin.yaml
│   │   ├── frontend/
│   │   │   ├── index.ts                  ← exporta { Page, sidebar, dashboardWidgets }
│   │   │   └── (slices migradas de features/chats-*)
│   │   ├── api/
│   │   │   ├── __init__.py               ← exporta `router`
│   │   │   └── (handlers de webhook + SSE)
│   │   └── agent/
│   │       ├── __init__.py               ← exporta WORKFLOWS, ACTIVITIES, TOOL_FACTORIES
│   │       └── (workflows session_based actuales)
│   ├── agents-admin/                     ← UI de gestión de agentes (no agéntico)
│   ├── orders/                           ← nuevo, ejemplo del POC
│   ├── eta/                              ← nuevo
│   └── catalog/                          ← migración de catalog_sync
│
├── hubara_agency/
│   └── src/
│       ├── platform/                     ← compartido, NO plugin
│       └── main.py                       ← loader (§5.1)
│
├── exoclaw-temporal/
│   └── src/
│       ├── session_based/worker.py       ← loader (§5.2)
│       ├── turn_based/worker.py          ← loader (§5.2)
│       └── (resto se mantiene)
│
├── frontend_dashboard/
│   ├── scripts/plugins-sync.ts           ← NUEVO (§5.3)
│   └── src/
│       ├── app/
│       │   ├── App.tsx                   ← consume PLUGINS
│       │   └── plugin-registry.generated.ts  ← .gitignored
│       ├── entities/                     ← compartido cross-plugin
│       ├── shared/                       ← primitivas UI
│       └── pages/Dashboard.tsx           ← shell que renderiza PLUGINS
│
└── PLUGIN_ARCHITECTURE.md                ← este archivo
```

---

## §8. Plan de migración — orden estricto

**Razón del orden**: cada paso deja el sistema funcionando; ningún paso
introduce regresión simultáneamente en los 3 stacks.

### Fase 0 — Plumbing (no toca features)

1. Crear `plugins/` directory vacío en la raíz.
2. Crear `PLUGIN_ARCHITECTURE.md` (este archivo, ya existe).
3. Definir el JSON Schema de `plugin.yaml` en `plugins/_schema/plugin.schema.yaml`.
4. Implementar `frontend_dashboard/scripts/plugins-sync.ts` (§5.3).
5. Agregar `.gitignore` entry para `plugin-registry.generated.ts`.
6. Agregar `vite.config.ts` alias `@plugins`.

**Verificación Fase 0**:
```bash
cd frontend_dashboard && pnpm plugins:sync
# → genera registry vacío sin error
```

### Fase 1 — Migrar `chats` como primer plugin (vertical completo)

Es el más complejo (toca los 3 stacks) y el más crítico (es el agente
existente). Hacerlo primero valida el patrón end-to-end.

1. Crear `plugins/chats/plugin.yaml` con todo lo de §4.
2. Mover `hubara_agency/src/sales_whatsapp/` → `plugins/chats/api/sales/`.
3. Mover `hubara_agency/src/remarketing_whatsapp/` → `plugins/chats/api/remarketing/`.
4. Mover `hubara_agency/src/dashboard/api.py` y `handoff.py` → `plugins/chats/api/dashboard/`.
5. Crear `plugins/chats/api/__init__.py` que ensambla un `router` único con todos los sub-routers.
6. Mover `exoclaw-temporal/src/session_based/workflows/`, `activities/`, `tools/` específicos del chat → `plugins/chats/agent/`.
7. Crear `plugins/chats/agent/__init__.py` con `WORKFLOWS`, `ACTIVITIES`, `TOOL_FACTORIES`.
8. Mover `frontend_dashboard/src/features/chats-*`, `session-*`, `memory-modal` → `plugins/chats/frontend/`.
9. Crear `plugins/chats/frontend/index.ts` que exporte `{ Page, sidebar, dashboardWidgets }`.

**Verificación Fase 1**:
```bash
ENABLED_PLUGINS=chats uvicorn hubara_agency.src.main:app
ENABLED_PLUGINS=chats python -m exoclaw_temporal.src.session_based.worker
cd frontend_dashboard && ENABLED_PLUGINS=chats pnpm plugins:sync && pnpm tauri dev
# → la sección Chats funciona end-to-end como antes
```

### Fase 2 — Implementar los loaders (§5.1 y §5.2)

Solo después de que `chats` ya esté como plugin, reemplazar los registros
estáticos por loaders.

1. Reescribir `hubara_agency/src/main.py` con el loader (§5.1).
2. Reescribir `exoclaw-temporal/src/session_based/worker.py` con el loader (§5.2).
3. Reescribir `exoclaw-temporal/src/turn_based/worker.py` con el loader (§5.2).
4. Reescribir `frontend_dashboard/src/pages/Dashboard.tsx` para consumir `PLUGINS`.
5. Reescribir `frontend_dashboard/src/app/App.tsx` para construir sidebar/rutas desde `PLUGINS`.

**Verificación Fase 2**: idéntica a Fase 1 — debe seguir funcionando solo con `ENABLED_PLUGINS=chats`.

### Fase 3 — Migrar el resto

En este orden (de menor a mayor riesgo):

1. `agents-admin` (solo frontend + API simple, sin agente Temporal).
2. `catalog` (migración de `catalog_sync` + features de upload).
3. `eta` (frontend + API).
4. `orders` (NUEVO — primer plugin construido enteramente bajo el contrato; sirve como referencia canónica para futuros plugins).

Cada migración:
- Crea `plugins/<id>/` con manifest + slices.
- Borra el código de la ubicación vieja (atomico, mismo PR).
- Verifica con `ENABLED_PLUGINS=chats,<id>`.

---

## §9. Primer refactor — scope explícito (POST-COMPACT START HERE)

**Cuando reanudes este trabajo después del compact, comienza por estos
pasos. NO trates de hacerlo todo en un solo PR.**

### PR1 — Plumbing only (Fase 0)

Sin tocar ninguna feature existente. Solo:

- Crear `plugins/` vacío.
- Implementar `frontend_dashboard/scripts/plugins-sync.ts`.
- Configurar `.gitignore`, `vite.config.ts`, `package.json`.
- Verificar que `pnpm plugins:sync` genera un registry vacío sin error.

**Definition of done**: `pnpm tauri dev` corre sin cambios funcionales.

### PR2 — Migrar `chats` como plugin (Fase 1)

Mover (no copiar) el dominio chat completo a `plugins/chats/`. Conservar
`main.py` y `worker.py` con imports estáticos apuntando a las nuevas
ubicaciones (este PR NO introduce loaders todavía).

**Definition of done**: el chat funciona idéntico a antes, todo el código
chat-related vive bajo `plugins/chats/`.

### PR3 — Loaders (Fase 2)

Reescribir `main.py`, ambos `worker.py`, `App.tsx` para usar
auto-discovery y `PLUGINS` registry.

**Definition of done**: el chat funciona idéntico, agregar/quitar
`chats` de `ENABLED_PLUGINS` lo activa/desactiva sin tocar código.

### PR4+ — Resto de plugins (Fase 3)

Uno por PR.

---

## §10. Lo que NO estamos haciendo (deferred / rejected)

| Item | Estado | Razón |
|---|---|---|
| Adoptar AgentSpan | ❌ Rechazado (D4) | v0.1.10, JVM, riesgo asimétrico para 5 empresas. |
| LangGraph como orquestador supremo | ❌ Rechazado (D5) | Rompe modelo session-based exoclaw. |
| LangGraph DENTRO de actividades Temporal | ⏸ Diferido | Útil cuando se necesite tool-loop complejo en un turn. Plugin spec ya soporta `agent.graph_spec`. |
| Hot-load de plugins en runtime sin restart | ❌ Rechazado (D10) | Para 5 empresas es over-engineering. |
| Marketplace UI de plugins | ⏸ Diferido | Construir después de tener 3+ plugins funcionando. |
| Terraform multi-tenant | ⏸ Diferido | Construir después de Fase 3 completa. La estructura `infra/tenants/<x>/` ya está pensada (ver conversación previa). |
| DB migration tooling cross-plugin | ⏸ Diferido | Cada plugin trae sus migrations independientes; el orquestador de migrations se diseña cuando aparezca el primer conflict. |
| Sales Flow DSL configurable por tenant | ⏸ Diferido | Requerimiento futuro real. Se construye encima del plugin system una vez sólido. |
| Frontend bundle por tenant | ⏸ Diferido | Hoy: 1 bundle, gated por `ENABLED_PLUGINS` env var. Migrar a bundle-per-tenant cuando tenant count > 5 o vendor lock requiera branding diferenciado. |

---

## §11. Justificación corta de las decisiones rechazadas

(Para cuando future-me se sienta tentado a reabrir.)

**Por qué NO AgentSpan**: usuario lo evaluó genuinamente; le atraía la
declaratividad JSON + componibilidad. Pero:
- v0.1.10, 138 stars, sin respaldo comercial conocido.
- Trae JVM + Spring Boot + Conductor a un plano Python/TS.
- Introduce un segundo motor de durabilidad junto a Temporal.
- El patrón que le atraía (composabilidad declarativa + auto-discovery) es
  replicable en ~1-2 semanas con los 3 loaders de §5, sin agregar servicios.
- Para 5 empresas, el riesgo de adoptar v0.1.x como pieza central es
  asimétrico: downside enorme (migración forzada en producción), upside
  modesto (ahorro de plumbing).

**Por qué NO LangGraph como orquestador supremo**: rompería el modelo
session-based de exoclaw (signals, queries, conversaciones de días). El
patrón consolidado en 2026 es **Temporal arriba, LangGraph dentro de
actividades** — y solo si se necesita tool-loop estructurado. El manifest
soporta `agent.graph_spec` para cuando llegue ese momento.

**Por qué NO Prefect/Dagster**: segundo motor de durabilidad innecesario.
La mayoría del OS no son workflows durables; son CRUD + cron.

**Por qué SÍ monorepo + N infras Terraform**: mantenimiento simple
(un PR, todos los tenants), aislamiento real de cuotas/costos vía cuentas
separadas. Mono-repo y multi-infra son ortogonales.

---

## §12. Decisiones abiertas (para discutir cuando llegue su momento)

1. **AWS Organizations vs cuentas independientes**: depende de si algún
   cliente exige facturación a su nombre. Postergar hasta primer cliente
   real.
2. **Cron runner**: APScheduler in-process, Celery beat, o un container
   separado por tenant. Decidir cuando se construya el primer plugin con
   `jobs:`.
3. **Permisos cross-plugin**: por ahora REST público + auth básica.
   Formalizar (scopes, roles) cuando exista un caso real.
4. **Versionado de plugins**: hoy semver simple en `plugin.yaml`. La
   gestión de upgrades cross-tenant se diseña cuando el segundo tenant
   esté en producción.
5. **Plugin SDK / typing**: hoy convención + duck typing. Considerar tipos
   estrictos (Pydantic models para el manifest, Protocol classes para
   `WORKFLOWS`/`ACTIVITIES`) cuando hayan 3+ plugins.

---

## §13. Verificación cruzada — comandos canónicos

Después de cualquier cambio en el plumbing:

```bash
# Frontend regenera registry sin error
cd frontend_dashboard && pnpm plugins:sync

# FastAPI arranca con plugins enabled
cd hubara_agency && ENABLED_PLUGINS=chats uvicorn src.main:app --reload

# Worker session_based arranca y descubre plugins
cd exoclaw-temporal && ENABLED_PLUGINS=chats python -m src.session_based.worker

# Frontend Tauri compila
cd frontend_dashboard && ENABLED_PLUGINS=chats pnpm tauri dev

# Health check muestra plugins cargados
curl http://localhost:8000/ | jq .
# → { "status": "ok", "plugins_loaded": ["chats"] }
```

---

## §14. Glosario (para post-compact)

- **Plugin** — carpeta autocontenida `plugins/<id>/` con su `plugin.yaml`
  que aporta una vertical slice (frontend + API + opcional agente + jobs).
- **Loader** — código en cada uno de los 3 stacks que escanea `plugins/`
  al boot y registra todo lo declarado en los manifests habilitados.
- **Wiring intent** — declaración en el manifest de algo que toca
  recursos compartidos (DB tables, S3 buckets, env vars). Lo aplica un
  merger; no se commitea como edición directa.
- **Contribution point** — lugar conocido donde un plugin puede aportar UI
  (sidebar, dashboard widgets, settings panels). El registry los expone
  al `App.tsx` shell.
- **DEHA** — Durable Execution Hexagonal Architecture. Las 5 reglas
  (R-DET / R-JSON / R-STATELESS / R-HEARTBEAT / R-DIP) siguen aplicando
  dentro de cada plugin que tenga `agent:`.
- **Auto-discovery** — los loaders descubren componentes escaneando
  filesystem; ningún archivo central se edita al agregar plugins.

---

**Fin del documento.** Este es el contrato. Cualquier desviación durante la
implementación se discute con el usuario antes de aplicarla.
