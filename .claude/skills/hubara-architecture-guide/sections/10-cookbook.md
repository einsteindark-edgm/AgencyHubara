# Sección 10 — Cookbook (recetas para tareas recurrentes)

> **Cuándo leer esto:** ya tenés clara la arquitectura (sections 01-09)
> y querés saber "cómo se hace X" en pasos concretos.
> **Pre-requisito:** la sección de tu dominio (02-06 según corresponda).
> **Tamaño:** ~15 KB.

Cada receta tiene: **título imperativo**, **paths a tocar**,
**snippet canónico**, **comando de verificación**.

---

## §1. Agregar tool LLM nueva a un agente Temporal

**Template aplicable:** C o D. **Pre-req:** plugin ya tiene worker.

### Files

| Path | Acción |
|---|---|
| `hubara_agency/src/plugins/<id>/agent/<sub>/tools/<my_tool>.py` | NEW |
| `hubara_agency/src/plugins/<id>/agent/composition.py` | MODIFY (factory) |
| `hubara_agency/src/plugins/<id>/workers/<worker>.py` | MODIFY (register_tool_extension) |
| `hubara_agency/src/plugins/<id>/agent/<sub>/workspace/TOOLS.md` | MODIFY (describir la tool al LLM) |
| `hubara_agency/tests/plugins/<id>/tools/test_<my_tool>.py` | NEW |
| `hubara_agency/tests/functional/test_<my_tool>_e2e.py` | NEW |

### Snippet

```python
# canonical — src/plugins/<id>/agent/<sub>/tools/<my_tool>.py
import json
from exoclaw.agent.tools import ToolBase, ToolContext

class MyTool(ToolBase):
    name = "my_tool"
    description = "Hace X cuando el usuario pide Y."
    parameters = {
        "type": "object",
        "properties": {"foo": {"type": "string"}},
        "required": ["foo"],
    }

    def __init__(self, workspace_path: str):
        self._workspace = workspace_path

    async def execute_with_context(self, ctx: ToolContext, **kwargs) -> str:
        return json.dumps({"status": "ok", "result": "..."})


# canonical — composition.py addition
@lru_cache(maxsize=1)
def get_my_tool(workspace_path: str) -> MyTool:
    return MyTool(workspace_path=workspace_path)


# canonical — workers/sales.py addition (en async def main())
register_tool_extension(
    "<id>.my_tool",
    lambda workspace_path: get_my_tool(str(workspace_path)),
)


# canonical — workspace/TOOLS.md addition
## my_tool

- **Cuándo llamar:** cuando el usuario pide X.
- **Cuándo NO llamar:** durante greetings.
- **Returns:** JSON `{"status": "ok", "result": "..."}`.
```

### Verificación

```bash
cd hubara_agency
uv run pytest tests/plugins/<id>/tools/test_<my_tool>.py -v
uv run pytest tests/functional/test_<my_tool>_e2e.py -m functional -v
uv run pytest -m architecture
uv run lint-imports
```

---

## §2. Agregar activity nueva (no LLM) a un agente

**Template aplicable:** C o D.

### Files

| Path | Acción |
|---|---|
| `hubara_agency/src/plugins/<id>/agent/activities/<my_activity>.py` | NEW |
| `hubara_agency/src/plugins/<id>/agent/contracts.py` | MODIFY (Input/Output DTOs) |
| `hubara_agency/src/plugins/<id>/workers/<worker>.py` | MODIFY (`activities=[...]`) |
| `hubara_agency/tests/plugins/<id>/activities/test_<my_activity>.py` | NEW |

### Snippet

```python
# canonical — agent/contracts.py addition
@dataclass(frozen=True)             # R-JSON
class MyActivityInput:
    session_id: str
    payload: str

@dataclass(frozen=True)
class MyActivityOutput:
    result: str


# canonical — agent/activities/my_activity.py
from temporalio import activity
from src.platform.temporal.heartbeat import with_heartbeat
from src.plugins.<id>.agent.contracts import MyActivityInput, MyActivityOutput

@activity.defn(name="my_activity")
@with_heartbeat(every=10)           # SOLO si worst-case >10s
async def my_activity(input: MyActivityInput) -> MyActivityOutput:
    # I/O permitido acá
    result = await some_io(input.payload)
    return MyActivityOutput(result=result)


# canonical — workers/<worker>.py addition
worker = Worker(
    client,
    task_queue=task_queue,
    workflows=[MyWorkflow],
    activities=[my_activity, *otras_activities],     # ← agregar
)
```

### Verificación

```bash
cd hubara_agency
uv run pytest tests/plugins/<id>/activities/test_<my_activity>.py -v
uv run pytest -m architecture
```

---

## §3. Agregar workflow nuevo a un plugin

**Template aplicable:** C o D.

### Files

| Path | Acción |
|---|---|
| `hubara_agency/src/plugins/<id>/agent/workflows/<my_workflow>.py` | NEW |
| `hubara_agency/src/plugins/<id>/agent/contracts.py` | MODIFY (Input DTO) |
| `hubara_agency/src/plugins/<id>/workers/<worker>.py` | MODIFY (`workflows=[...]`) |
| `hubara_agency/tests/plugins/<id>/workflows/test_<my_workflow>.py` | NEW |

### Snippet

```python
# canonical — agent/workflows/my_workflow.py
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.plugins.<id>.agent.contracts import MyWorkflowInput
    from src.platform.workflow_helpers import PendingMessage

@workflow.defn(name="MyWorkflow")
class MyWorkflow:
    def __init__(self):
        self._pending: list[PendingMessage] = []
        self._stop = False

    @workflow.signal
    def send_message(self, msg: PendingMessage) -> None:
        self._pending.append(msg)

    @workflow.run
    async def run(self, input: MyWorkflowInput) -> None:
        # ... loop principal ...
        pass
```

### Verificación

```bash
cd hubara_agency
uv run pytest tests/plugins/<id>/workflows/test_<my_workflow>.py -v
# Para replay test:
uv run pytest tests/plugins/<id>/workflows/test_<my_workflow>_replay.py -v
```

---

## §4. Agregar webhook endpoint a un plugin con API

**Template aplicable:** B o D.

### Files

| Path | Acción |
|---|---|
| `hubara_agency/src/plugins/<id>/api/<my_endpoint>.py` (o agregar a `routes.py`) | NEW o MODIFY |
| `hubara_agency/src/plugins/<id>/api/__init__.py` | MODIFY (si usás unified router) |
| `frontend_dashboard/src/plugins/<id>/plugin.yaml` | MODIFY (declarar en `api.legacy_routers` o usar `api.python_module`) |
| `hubara_agency/tests/plugins/<id>/api/test_<my_endpoint>.py` | NEW |
| `hubara_agency/tests/functional/test_<my_endpoint>_e2e.py` | NEW |

### Snippet

```python
# canonical — api/<my_endpoint>.py
from fastapi import APIRouter, BackgroundTasks, HTTPException

router = APIRouter()

@router.post("/my-endpoint")
async def my_endpoint(payload: dict, background: BackgroundTasks) -> dict:
    # ack inmediato + background task (patrón webhook)
    background.add_task(_process_in_background, payload)
    return {"status": "ack"}

async def _process_in_background(payload: dict) -> None:
    # I/O pesado / signal a Temporal / etc.
    pass
```

```yaml
# manifest addition (api.legacy_routers caso múltiples sub-routers):
api:
  legacy_routers:
    # ...routers existentes...
    - { module: src.plugins.<id>.api.<my_endpoint>, prefix: /api/<id>, tags: [MyEndpoint] }
```

### Verificación

```bash
cd hubara_agency
uv run python run_api.py
# En otra terminal:
curl -X POST http://localhost:8000/api/<id>/my-endpoint -d '{"foo": "bar"}'
# El loader debe loguear el registro al arrancar
```

---

## §5. Agregar SSE endpoint al dashboard

**Template aplicable:** B o D (típicamente `chats`).

### Files

| Path | Acción |
|---|---|
| `hubara_agency/src/plugins/<id>/api/<my_stream>.py` | NEW |
| `frontend_dashboard/src/entities/<x>/api.ts` | MODIFY (agregar `use<X>Stream`) |
| `frontend_dashboard/tests/<x>/api.stream.test.tsx` | NEW |
| (test functional + e2e según patrón) | NEW |

### Snippet

```python
# canonical — api/<my_stream>.py
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import asyncio, json

router = APIRouter()

@router.get("/<x>/stream")
async def stream_x():
    async def event_gen():
        while True:
            data = {"foo": "bar", "ts": ...}
            yield f"data: {json.dumps(data)}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(event_gen(), media_type="text/event-stream")
```

```typescript
// canonical — entities/<x>/api.ts addition
import { subscribeSse } from "@/shared/api/sse";
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

export function use<X>Stream() {
  const qc = useQueryClient();
  useEffect(() => {
    const sub = subscribeSse(`/api/<x>/stream`, {
      onMessage(msg) {
        qc.setQueryData(<x>Keys.list(), (prev: <X>[] | undefined) =>
          mergeNewItem(prev, msg)
        );
      },
    });
    return () => sub.unsubscribe();
  }, [qc]);
}
```

---

## §6. Agregar feature frontend dentro de un plugin existente

**Template aplicable:** A/B/C/D.

### Files

| Path | Acción |
|---|---|
| `frontend_dashboard/src/plugins/<id>/frontend/features/<my_feature>/index.ts` | NEW |
| `frontend_dashboard/src/plugins/<id>/frontend/features/<my_feature>/ui/<Component>.tsx` | NEW |
| `frontend_dashboard/src/plugins/<id>/frontend/<Id>Section.tsx` | MODIFY (mount feature) |
| `frontend_dashboard/e2e/<my_feature>/<slice>.spec.ts` | NEW |

### Snippet

```typescript
// canonical — features/<my_feature>/ui/<Component>.tsx
import { useChats } from "@/entities/chat";   // OK: entity shared

export function MyFeature() {
  const { data, isLoading } = useChats();
  if (isLoading) return <div>Loading…</div>;
  return <div>{data.map(c => <div key={c.id}>{c.name}</div>)}</div>;
}

// canonical — features/<my_feature>/index.ts
export { MyFeature } from "./ui/MyFeature";

// canonical — <Id>Section.tsx mount
import { MyFeature } from "./features/my-feature";
// dentro del render:
<MyFeature />
```

---

## §7. Agregar entity shared cross-plugin (NUEVO entity)

**Template aplicable:** cualquiera (es shared, no del plugin).

### Files (todos NEW)

```
frontend_dashboard/src/entities/<x>/
├── model.ts
├── contracts.ts
├── keys.ts
├── api.ts
├── index.ts
└── api.test.tsx
```

### Verificación

```bash
cd frontend_dashboard
npm test -- entities/<x>
npm run test:arch                # asegura barrel-only public API
```

**Importante:** si el entity es consumido por UN solo plugin, NO va en
`entities/` — va dentro del plugin (`plugins/<id>/frontend/features/<x>/`).
La regla es **2+ consumidores → promote**.

---

## §8. Agregar shared/ui/ primitive

**Template aplicable:** cualquiera (es shared, no del plugin).

### Files

| Path | Acción |
|---|---|
| `frontend_dashboard/src/shared/ui/<My>.tsx` | NEW |
| `frontend_dashboard/src/shared/ui/index.ts` | MODIFY (export) — **SPINAL** |

### Spinal warning

`index.ts` es spinal. Si 2+ plugins agregan primitivas en paralelo,
declarar `wiring_intent` `ts_barrel` en task-result.yaml.

```yaml
wiring_intents:
  frontend_dashboard/src/shared/ui/index.ts:
    - kind: ts_barrel
      export_statement: 'export { MyComponent } from "./MyComponent";'
      file_role: "shared_barrel"
      order_hint: append
```

---

## §9. Crear plugin nuevo template A (frontend-only) — receta exprés

```bash
mkdir -p frontend_dashboard/src/plugins/my_plugin/frontend
mkdir -p hubara_agency/src/plugins/my_plugin
touch hubara_agency/src/plugins/my_plugin/__init__.py
```

```yaml
# frontend_dashboard/src/plugins/my_plugin/plugin.yaml
id: my_plugin
version: 0.1.0
display_name: My Plugin
description: ...
frontend:
  entry: ./frontend
  contributes:
    sections: [{ key: myplugin, label: My Plugin, order: 6, icon: bolt }]
    sidebar: [{ route: /myplugin, label: My Plugin, icon: bolt }]
```

```typescript
// frontend_dashboard/src/plugins/my_plugin/frontend/index.ts
export { default, MyPluginSection } from "./MyPluginSection";

// frontend_dashboard/src/plugins/my_plugin/frontend/MyPluginSection.tsx
export interface MyPluginSectionProps {
  showSidebar: boolean;
  showInspector: boolean;
}
export function MyPluginSection({ showSidebar, showInspector }: MyPluginSectionProps) {
  return <main>Hello plugin</main>;
}
export default MyPluginSection;
```

```bash
cd frontend_dashboard
npm run plugins:sync             # genera registry
npm run dev                       # ver el nuevo tab en el Toolbar
```

---

## §10. Crear plugin nuevo template D (full-stack agéntico) — receta exprés

Combinar §9 + §1 + §3 + §4. Más detalle en
`examples/plugin-full-stack-agentic.md`.

**Resumen:** ~15-25 archivos nuevos + plugin.yaml + K8s manifest +
`render-compose.py` re-run + 5-10 tests.

---

## §11. Promover plugin de A a D (sumarle worker)

Empezar desde un plugin template A (e.g. `orders`). Agregar:

1. Crear estructura backend Python:
```bash
mkdir -p hubara_agency/src/plugins/orders/{agent,workers}
mkdir -p hubara_agency/src/plugins/orders/agent/{workflows,activities,tools}
touch hubara_agency/src/plugins/orders/agent/contracts.py
touch hubara_agency/src/plugins/orders/agent/composition.py
touch hubara_agency/src/plugins/orders/workers/sync.py
```

2. Editar `plugin.yaml` agregando bloque `agent:` (ver §3 sección 03).

3. Crear K8s manifest `worker-orders-sync.yaml`.

4. Regenerar docker-compose: `uv run python scripts/render-compose.py`.

5. Tests:
```bash
uv run pytest tests/plugins/                # premortem invariants verdes
uv run python -m src.plugins.orders.workers.sync   # smoke boot
```

---

## §12. Agregar nueva queue Temporal (vía manifest — post-PR11)

```yaml
# plugin.yaml — agregar worker nuevo (cada worker = queue dedicada):
agent:
  workers:
    - name: my_new_worker
      module: src.plugins.<id>.workers.my_new_worker
      task_queue: queue-<id>-<purpose>          # SSoT
      deployment: { replicas: 1, ... }
      compose: { env: {...}, depends_on: [temporal] }
```

```python
# src/plugins/<id>/workers/my_new_worker.py
from src.platform.plugin_manifest import get_task_queue
# ...
task_queue = get_task_queue("<id>", "my_new_worker")
worker = Worker(client, task_queue=task_queue, ...)
```

```bash
# Validar + regenerar:
uv run pytest tests/plugins/                # invariants
uv run python scripts/render-compose.py     # autogen compose
# Crear K8s manifest:
cp k8s/aws-produccion/worker-catalog-sync.yaml \
   k8s/aws-produccion/worker-<id>-my-new-worker.yaml
# editar metadata.name, command, env
```

---

## §13. Manejar fallo del architecture gate

Pasos del diagnose:

1. **¿`pytest -m architecture` falla?** Ver `sections/08-tests-and-gates.md §9.1`.
2. **¿`lint-imports` falla?** Ver `§9.2`.
3. **¿`npm run test:arch` falla?** Ver `§9.3`.
4. **¿META-GATE falla?** `status: blocked, blocked_reason: requires_planner_update`. STOP.

NUNCA editar `tests/architecture/`, `.importlinter`, `R_*_EXEMPTIONS` para
silenciar. Eso es cardinal sin.

---

## §14. Manejar fallo de `render-compose-check`

```bash
cd hubara_agency
uv run python scripts/render-compose.py
git add docker-compose.local.yml
git commit -m "regenerate docker-compose.local.yml"
git push
```

Si el script falla con error de YAML, abrí `docker-compose.local.yml` y
verificá que no haya null literals (`volname: null`). El script tiene
un helper `_yaml_dump` que reemplaza `": null\n"` por `":\n"`.

---

## §15. Bloquearse correctamente (`status: blocked`)

Cuando NO podés implementar y necesitás escalar al planner:

| `blocked_reason` | Cuándo |
|---|---|
| `depends_on_missing` | Una tarea upstream del DAG no fue mergeada al branch |
| `missing_dependency` | El snippet importa una lib que no está en `pyproject.toml` / `package.json` |
| `requires_planner_update` | El task pide algo fuera de scope, o requiere editar protected files, o el flow cambió |
| `regression` | Tu task introduce regresión en tests fuera de tu §3 |
| `command_timeout` | Un comando de §10 timeoutea (>5 min) repetidas veces |
| `requires_merger` | (V2) — tu task toca shared file y otro plugin paralelo también |

En `task-result.yaml`:

```yaml
status: blocked
blocked_reason: requires_planner_update
notes: |
  Task pide modificar @lru_cache decorator a un dataclass existente en
  composition.py. La spec de wiring_intents solo describe APPENDS, no
  mutations. Planner: rebundle esta tarea con la que owns el dataclass
  original, o sequence en su propio batch.
```

---

**Fin sección 10.** Estos son los patrones recurrentes. Si tu task no
encaja en ninguno, revisá si está sobre-engineered.
