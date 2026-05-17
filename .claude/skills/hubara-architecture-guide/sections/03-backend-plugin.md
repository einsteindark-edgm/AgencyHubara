# Sección 03 — Cómo crear un plugin Python nuevo (templates A-D)

> **Cuándo leer esto:** vas a crear un plugin nuevo, o agregar capa Python
> (API, worker, agente) a un plugin frontend-only existente.
> **Pre-requisito:** `sections/01-general.md`. Si toca workflows/activities,
> después leé `sections/04-backend-agents.md`.
> **Tamaño:** ~12 KB.

---

## §1. Los 4 templates de plugin

Identificá cuál es el tuyo según qué stacks necesite contribuir.

| Template | Frontend | API | Worker | Ejemplos actuales |
|---|---|---|---|---|
| **A.** Frontend-only | ✅ | ❌ | ❌ | `agents_admin`, `eta`, `orders` |
| **B.** Frontend + API | ✅ | ✅ | ❌ | (no hay aún — `orders` cuando crezca con CRUD propio) |
| **C.** Frontend + Worker (sin API) | ✅ | ❌ | ✅ | `catalog` |
| **D.** Full-stack agéntico | ✅ | ✅ | ✅ | `chats` |

Los templates son orientativos — el manifest acepta cualquier combinación
de `frontend:`, `api:`, `agent:`. Si necesitás worker SIN frontend
(CLI/batch jobs), omití `frontend:`.

---

## §2. Template A — Frontend-only (más simple)

**Archivos a CREAR (4):**

```
frontend_dashboard/src/plugins/<id>/
├── plugin.yaml                              # manifest
└── frontend/
    ├── index.ts                             # barrel: export default Page
    ├── <Id>Section.tsx                      # el componente Page
    └── features/                            # opcional, features internas
        └── <feature>/
            ├── index.ts
            └── ui/<Component>.tsx

hubara_agency/src/plugins/<id>/
└── __init__.py                              # anchor; vacío con docstring
```

**Manifest mínimo:**

```yaml
# frontend_dashboard/src/plugins/<id>/plugin.yaml
id: my_plugin
version: 0.1.0
display_name: My Plugin
description: Descripción breve.

frontend:
  entry: ./frontend
  contributes:
    sections:
      - { key: myplugin, label: My Plugin, order: 6, icon: bolt }
    sidebar:
      - { route: /myplugin, label: My Plugin, icon: bolt }
```

**Verificación:**

```bash
cd frontend_dashboard
npm run plugins:sync    # genera registry; aparece my_plugin
npm run dev             # nueva sección en el Toolbar
```

**Archivos que NO se editan:** `Dashboard.tsx`, `Toolbar.tsx`, ni ningún
otro shell. El sistema lo descubre auto.

---

## §3. Template B — Frontend + API

Sumar al Template A:

**Archivos a CREAR adicionales (2-3):**

```
hubara_agency/src/plugins/<id>/
├── __init__.py
└── api/
    ├── __init__.py                          # opcional: docstring
    └── routes.py                            # define `router = APIRouter()` con endpoints
```

**Editar `plugin.yaml`** agregando:

```yaml
api:
  python_module: src.plugins.my_plugin.api.routes   # módulo que expone `router`
  prefix: /api/myplugin
  tags: [MyPlugin]
```

**Caso especial — múltiples sub-routers con prefijos heterogéneos** (como `chats`):

```yaml
api:
  python_module: src.plugins.chats.api      # ancla simbólica, IGNORADA si hay legacy_routers
  prefix: /api/chats                        # idem ignorado
  tags: [Chats]                             # idem ignorado
  legacy_routers:                           # ← GANA cuando está presente
    - { module: src.plugins.chats.api.sales,     prefix: /api,           tags: [WhatsApp_Sales_Domain] }
    - { module: src.plugins.chats.api.dashboard, prefix: /api/dashboard, tags: [Dashboard] }
    - { module: src.plugins.chats.api.handoff,   prefix: /api/dashboard, tags: [Dashboard_Handoff] }
```

**Snippet canónico de `api/routes.py`:**

```python
# canonical — hubara_agency/src/plugins/my_plugin/api/routes.py
from fastapi import APIRouter

router = APIRouter()

@router.get("/items")
async def list_items() -> list[dict]:
    return [{"id": 1, "name": "foo"}]
```

**Verificación:**

```bash
cd hubara_agency
uv run python run_api.py
# El loader debe loguear:
# [loader] registered src.plugins.my_plugin.api.routes → prefix='/api/myplugin' tags=['MyPlugin']
curl http://localhost:8000/api/myplugin/items
```

---

## §4. Template C — Frontend + Worker Temporal (sin API)

Es el caso de `catalog`. Sumar al **Template A** (NO al B — no hay API):

**Archivos a CREAR adicionales (5-7):**

```
hubara_agency/src/plugins/<id>/
├── agent/
│   ├── __init__.py                          # docstring; NO exporta WORKFLOWS (worker registra a mano)
│   ├── contracts.py                         # @dataclass frozen — DTOs boundary (R-JSON)
│   ├── workflows/
│   │   ├── __init__.py
│   │   └── <name>.py                        # @workflow.defn
│   └── activities/
│       ├── __init__.py
│       └── <name>.py                        # @activity.defn
└── workers/
    ├── __init__.py
    └── <worker_name>.py                     # async def main() con Worker(...)
```

**Editar `plugin.yaml`** agregando (NOTA: `task_queue` y `compose` son
**críticos** post-PR11 — single source of truth):

```yaml
agent:
  python_module: src.plugins.my_plugin.agent
  workers:
    - name: sync
      module: src.plugins.my_plugin.workers.sync
      task_queue: queue-my-plugin-sync       # ← REQUIRED — la queue vive acá, no en constants.py
      deployment:                             # ← hint para K8s manifest
        replicas: 1
        cpu_request: 100m
        memory_request: 256Mi
      compose:                                # ← input de render-compose.py
        env:
          TEMPORAL_URL: temporal:7233
          WORKSPACE_VAULT_DIR: /app/hubara_vault
        volumes:
          - hubara-vault-local:/app/hubara_vault
        depends_on:
          - temporal
```

**NO editar `src/platform/constants.py`** — las queues legacy (`SALES_QUEUE`,
`REMARKETING_QUEUE`, `CATALOG_SYNC_QUEUE`) fueron eliminadas en PR11. Si
necesitás la queue desde código, usá `get_task_queue("my_plugin", "sync")`.

**Snippet canónico del worker:**

```python
# canonical — hubara_agency/src/plugins/my_plugin/workers/sync.py
import asyncio

from loguru import logger
from temporalio.worker import Worker

from src.platform.plugin_manifest import get_task_queue
from src.platform.logging import setup_logging
from src.platform.temporal.client import get_temporal_client
from src.plugins.my_plugin.agent.activities import my_activity
from src.plugins.my_plugin.agent.workflows import MyWorkflow

setup_logging()

async def main() -> None:
    logger.info("Conectando worker MyPlugin a Temporal...")
    client = await get_temporal_client()
    task_queue = get_task_queue("my_plugin", "sync")    # ← post-PR11
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[MyWorkflow],
        activities=[my_activity],
    )
    logger.info("MyPlugin worker up. Queue: '{}'", task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
```

**Crear K8s deployment** (el test invariante
`test_every_worker_in_manifest_has_k8s_deployment` falla si no lo creás):

```bash
cp hubara_agency/k8s/aws-produccion/worker-catalog-sync.yaml \
   hubara_agency/k8s/aws-produccion/worker-my-plugin-sync.yaml
# Editar:
#   - metadata.name → hubara-worker-my-plugin-sync
#   - spec.replicas / resources según deployment del manifest
#   - containers[0].command → ["python", "-m", "hubara_agency.src.plugins.my_plugin.workers.sync"]
#   - env: leer secrets según declarado en deployment.env_secrets del manifest
```

**Regenerar docker-compose** (NO editarlo a mano):

```bash
cd hubara_agency
uv run python scripts/render-compose.py
# Output: [render-compose] wrote hubara_agency/docker-compose.local.yml (~5500 bytes)
git add docker-compose.local.yml
```

**Si el plugin define nuevas tools**, registrarlas en `workers/<name>.py`:

```python
from src.platform.tool_extensions import register_tool_extension
from src.plugins.my_plugin.agent.tools.my_tool import MyTool

register_tool_extension(
    "my_plugin.my_tool",
    lambda workspace: MyTool(workspace=str(workspace)),
)
```

**Si el plugin define un nuevo dataclass que cruza el workflow/activity boundary**,
agregarlo a `src.platform.contracts.R_JSON_FROZEN_EXEMPTIONS` **SOLO** si
tiene un motivo legítimo (e.g. inheritance). El test R-JSON rompe si no
lo declarás como `@dataclass(frozen=True)`.

**Verificación:**

```bash
# Smoke imports
uv run python -c "import src.plugins.my_plugin.workers.sync"

# Discovery
uv run python -c "from src.run_workers import _discover_workers; print(_discover_workers())"
# Debe aparecer ('my_plugin', 'sync', 'src.plugins.my_plugin.workers.sync')

# Boot del worker
uv run python -m src.plugins.my_plugin.workers.sync

# Resolver la queue desde código
uv run python -c "from src.platform.plugin_manifest import get_task_queue; print(get_task_queue('my_plugin', 'sync'))"

# Tests architecture + plugins
uv run pytest -m architecture
uv run pytest tests/plugins/
```

---

## §5. Template D — Full-stack agéntico

Combinar A + B + C. Es básicamente el plugin `chats`. Ver el ejemplo
trabajado completo en `examples/plugin-full-stack-agentic.md`.

**Particularidades cuando el plugin tiene 2+ workers:**

```yaml
agent:
  workers:
    - name: sales
      module: src.plugins.chats.workers.sales
      task_queue: queue-sales-agent
      # ...
    - name: remarketing
      module: src.plugins.chats.workers.remarketing
      task_queue: queue-remarketing-agent
      # ...
```

- Cada worker tiene su queue **exclusiva** (no compartir — falla
  `test_task_queues_are_unique_across_workers`).
- Cada worker tiene su **K8s manifest** propio
  (`worker-sales.yaml`, `worker-remarketing.yaml`).
- Cada worker registra **solo las tools que necesita** (aislamiento de
  seguridad — el LLM de sales no puede invocar tools de remarketing por
  accidente).

---

## §6. Checklist completo post-PR11 (para CUALQUIER plugin nuevo)

```
[ ] Crear directorio frontend_dashboard/src/plugins/<id>/
[ ] Crear plugin.yaml con `id` matching el dirname y pattern ^[a-z][a-z0-9_]*$
[ ] Crear directorio hubara_agency/src/plugins/<id>/ con __init__.py
[ ] Si tiene frontend:
    [ ] frontend/index.ts con `export default <Page>`
    [ ] <Id>Section.tsx con props { showSidebar: boolean, showInspector: boolean }
[ ] Si tiene API:
    [ ] Módulo Python que expone `router = APIRouter()`
    [ ] Manifest declara `api.python_module` o `api.legacy_routers`
[ ] Si tiene workers:
    [ ] Declarar en agent.workers[] del manifest con task_queue, deployment, compose
    [ ] Crear workers/<name>.py con async def main() que usa get_task_queue(...)
    [ ] Crear manifest K8s k8s/aws-produccion/worker-<name>.yaml
[ ] Si tiene DTOs cross-boundary: @dataclass(frozen=True) en contracts.py
[ ] Si tiene tools: registrar en workers/<name>.py con register_tool_extension(...)
[ ] cd hubara_agency && uv run python scripts/render-compose.py  (regen docker-compose)
[ ] cd frontend_dashboard && npm run plugins:sync  (regen registry frontend)
[ ] uv run pytest tests/plugins/  — todos los invariantes verdes
[ ] uv run lint-imports  — R-DIP verde
[ ] cd frontend_dashboard && npm run test:arch  — FSD verde
```

---

## §7. Lo que NO hay que hacer (los detecta el sistema)

Estos errores son automáticamente bloqueados por tests / linters /
gates. No los intentes:

| ❌ NO hacer | Detección |
|---|---|
| Editar `src/platform/constants.py` para agregar queue del plugin | `test_every_manifest_worker_declares_task_queue` falla si la queue vive ahí |
| Editar `tests/plugins/test_premortem_invariants.py:_EXPECTED_K8S_DEPLOYMENTS` | Es auto-discover ahora — no existe ese dict |
| Editar `tests/conftest.py:_VAULT_CAPTURING_MODULES` | AST scan auto-discover |
| Editar `docker-compose.local.yml` a mano | `test_docker_compose_local_is_up_to_date_with_manifests` falla |
| Editar `Dashboard.tsx` o `Toolbar.tsx` | Son 100% data-driven; no hay nada que editar para tu plugin |
| `from src.plugins.<other_id>.X import Y` | `import-linter` falla (R-DIP cross-plugin) |
| `from src.plugins.X import Y` desde `src/platform/` | Mismo |

---

## §8. Cómo activar / desactivar plugins sin tocar código

```bash
# Activar solo chats (filtra todos los demás):
ENABLED_PLUGINS=chats uv run python run_api.py
ENABLED_PLUGINS=chats uv run python -m src.run_workers

# Activar varios:
ENABLED_PLUGINS=chats,catalog,orders uv run python run_api.py

# Sin la env var: carga todos los descubiertos en frontend_dashboard/src/plugins/
uv run python run_api.py
```

Funciona también en frontend:

```bash
ENABLED_PLUGINS=chats,catalog npm run plugins:sync
# Genera registry solo con esos 2
```

---

## §9. Tabla de archivos típicos por template

| Archivo | Template A | Template B | Template C | Template D |
|---|---|---|---|---|
| `plugin.yaml` | ✅ | ✅ | ✅ | ✅ |
| `frontend/index.ts` | ✅ | ✅ | ✅ | ✅ |
| `frontend/<Id>Section.tsx` | ✅ | ✅ | ✅ | ✅ |
| `hubara_agency/src/plugins/<id>/__init__.py` | ✅ | ✅ | ✅ | ✅ |
| `hubara_agency/src/plugins/<id>/api/routes.py` | ❌ | ✅ | ❌ | ✅ (puede ser legacy_routers) |
| `hubara_agency/src/plugins/<id>/agent/__init__.py` | ❌ | ❌ | ✅ | ✅ |
| `hubara_agency/src/plugins/<id>/agent/contracts.py` | ❌ | ❌ | ✅ | ✅ |
| `hubara_agency/src/plugins/<id>/agent/workflows/*.py` | ❌ | ❌ | ✅ | ✅ |
| `hubara_agency/src/plugins/<id>/agent/activities/*.py` | ❌ | ❌ | ✅ | ✅ |
| `hubara_agency/src/plugins/<id>/workers/<name>.py` | ❌ | ❌ | ✅ | ✅ |
| `hubara_agency/k8s/aws-produccion/worker-<name>.yaml` | ❌ | ❌ | ✅ | ✅ |

---

## §10. Próximo paso según template

| Si vas a hacer template… | Leé después |
|---|---|
| A | `sections/06-frontend-plugin.md` (props bandejón + sections vs sidebar) |
| B | `sections/06-frontend-plugin.md` + `sections/10-cookbook.md` "agregar webhook" |
| C | `sections/04-backend-agents.md` (workflows + activities + tools + Temporal patterns) |
| D | `sections/04-backend-agents.md` + `sections/06-frontend-plugin.md` |

---

**Fin sección 03.** El template C/D requiere casi siempre `sections/04`
para entender DEHA + tool-loop + register_tool_extension.
