# Reference — Las 5 R-rules de DEHA (detallado con ejemplos)

> **Cuándo leer esto:** entendés el patrón general (sección 04) pero
> necesitás el detalle exacto de qué es válido vs inválido por cada R-rule.
> **Source of truth:** los archivos de tests bajo
> `hubara_agency/tests/architecture/` + `hubara_agency/.importlinter`.

---

## §0. ¿Qué es DEHA?

**Durable Execution Hexagonal Architecture** — el conjunto de reglas
para escribir agentes Python que corren sobre Temporal.io de forma
segura, testeable, y debuggeable.

5 reglas hard. Si las violás:
- R-DET → tu workflow falla con `NonDeterminismError` en replay.
- R-JSON → tu workflow falla al deserializar histórial post-deploy.
- R-STATELESS → tu activity tiene memory leak o produce data corrupto.
- R-HEARTBEAT → Temporal cree que tu activity zombie y reintenta.
- R-DIP → tests no funcionan en isolation; cross-plugin coupling silencioso.

---

## §1. R-DET — Workflows determinísticos

**Regla:** todo lo que NO sea determinístico (i.e. da diferente result
en cada call) DEBE vivir en un activity, NUNCA en código de workflow.

### §1.1 Qué es "código de workflow"

```python
@workflow.defn
class MyWorkflow:
    @workflow.run
    async def run(self, input: MyInput):
        # ESTO es código de workflow (NO determinístico aquí)
        pass

    @workflow.signal
    def send_message(self, msg: PendingMessage):
        # ESTO es código de workflow
        pass
```

Cualquier código que se ejecuta dentro de `@workflow.defn` o handlers
`@workflow.signal` / `@workflow.query` es "código de workflow".

### §1.2 Qué NO es determinístico (prohibido en workflows)

| ❌ Prohibido | ✅ Reemplazo |
|---|---|
| `datetime.now()`, `datetime.utcnow()`, `time.time()` | `workflow.now()` |
| `uuid.uuid4()` | `workflow.uuid4()` |
| `random.random()`, `secrets.token_hex()` | `workflow.random().random()` |
| `time.sleep(N)` | `await workflow.sleep(N)` |
| `await asyncio.sleep(N)` | `await workflow.sleep(N)` |
| `import httpx; httpx.get(...)` (directo) | Mover a activity con `workflow.execute_activity(...)` |
| `open("file.txt").read()` | Activity con I/O |
| `os.environ.get("X")` | Leer en `worker.py`; pasar via composition o input |
| Llamar API LLM directamente | Activity `llm_chat` |
| `asyncio.gather()` (puede tener races) | `workflow.execute_activity` paralelos via `asyncio.gather(*coros)` está OK si las activities son determinísticas — el sandbox lo permite |

### §1.3 Patrón: importar third-party libs en workflow

Si necesitás importar una lib third-party en el workflow file (e.g. para
type hints), envolvé en `workflow.unsafe.imports_passed_through()`:

```python
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from exoclaw_temporal.config import SessionInput, LLMConfig
    from src.platform.workflow_helpers import PendingMessage, run_agent_turn
    from src.plugins.chats.agent.sales.contracts import BootstrapSalesInput
    from src.platform.temporal.retry_policies import _CONV_OPTIONS, _LLM_OPTIONS

@workflow.defn(name="HubaraSalesSessionWorkflow")
class HubaraSalesSessionWorkflow:
    # ...
```

Sin el `with workflow.unsafe.imports_passed_through()`, Temporal sandbox
re-importa el módulo en cada replay y puede romper. Con el wrapper, se
deja pasar al sandbox como-es.

### §1.4 Snippet completo válido vs inválido

```python
# ❌ INVÁLIDO
@workflow.defn
class BadWorkflow:
    @workflow.run
    async def run(self, input):
        now = datetime.now()                  # R-DET violation
        wait_secs = random.randint(1, 5)      # R-DET violation
        await asyncio.sleep(wait_secs)         # R-DET violation
        response = httpx.get("https://api.x") # R-DET violation
        return response.json()


# ✅ VÁLIDO
@workflow.defn
class GoodWorkflow:
    @workflow.run
    async def run(self, input):
        now = workflow.now()                                       # OK
        wait_secs = workflow.random().randint(1, 5)                # OK
        await workflow.sleep(timedelta(seconds=wait_secs))         # OK
        response = await workflow.execute_activity(                # OK
            fetch_x_activity,
            input,
            **_HTTP_OPTIONS,
        )
        return response
```

### §1.5 Enforcement

- **Convention.** No hay AST scan completo (es difícil detectar todos los
  casos). El Temporal sandbox detecta MUCHOS en runtime con
  `WorkflowSandbox`, pero no todos.
- **Code review** es la última defensa.
- **Replay tests** (con fixture JSON del history) son el oráculo final
  — si el replay falla con `NonDeterminismError`, hay R-DET violation.

---

## §2. R-JSON — DTOs frozen JSON-serializable

**Regla:** todo lo que cruza `workflow.execute_activity(...)` o
`client.start_workflow(...)` DEBE ser `@dataclass(frozen=True)` con
solo tipos JSON-compatibles.

### §2.1 Tipos JSON-compatibles válidos

| ✅ Permitido | ❌ Prohibido |
|---|---|
| `str` | `bytes` (binary) |
| `int`, `float`, `bool` | `Decimal` |
| `None` | `complex` |
| `list[T]` donde T es JSON | `tuple[...]` (use `list` en su lugar) |
| `dict[str, T]` donde T es JSON | `dict[KeyT, V]` con KeyT no-string |
| Otro `@dataclass(frozen=True)` JSON-compatible | Cualquier `@dataclass` sin `frozen=True` |
| `Enum` con string/int values | Custom classes con state |
| `datetime` con `temporalio.api.common.v1.Timestamp` (raro) | `datetime.datetime` directo (use ISO string) |
| Strings con paths | `pathlib.Path` |

### §2.2 Snippet válido vs inválido

```python
# ❌ INVÁLIDO
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime

@dataclass                          # NO frozen
class BadDto:
    session_id: str
    workspace_path: Path            # NO use Path; use str
    created_at: datetime            # NO use datetime; use ISO string
    metadata: dict[int, str]        # NO use int keys


# ✅ VÁLIDO
@dataclass(frozen=True)
class GoodDto:
    session_id: str
    workspace_path: str             # ISO string path
    created_at: str                 # ISO 8601 string
    metadata: dict[str, str]
    optional_field: str | None = None
    nested: GoodNestedDto | None = None

@dataclass(frozen=True)
class GoodNestedDto:
    foo: int
    bar: list[str]
```

### §2.3 Pydantic está PROHIBIDO

Temporal NO sabe serializar Pydantic models por default. **NO uses Pydantic
para DTOs que cruzan boundary.** Pydantic está OK para validar input HTTP
en FastAPI, pero hacer `pydantic.dict()` y pasar el dict al activity (que
recibe `dict[str, Any]` o un frozen dataclass).

### §2.4 Excepciones documentadas (`R_JSON_FROZEN_EXEMPTIONS`)

Vive en `hubara_agency/tests/architecture/conftest.py`. Hoy tiene ~5
entries. Cada una tiene comment de ≥1 línea con la razón.

**Cuándo agregar:**
- El dataclass tiene un motivo legítimo (e.g. inheritance, legacy).
- El motivo está documentado.
- El dataclass NO cruza boundary (sólo se usa internamente).

**NUNCA agregar** para "fix temporal del test" — eso abre la puerta a
violaciones reales.

### §2.5 Enforcement

- `test_r_json.py` — AST scan de cada `@dataclass` que se pasa como arg
  a `workflow.execute_activity` o `client.start_workflow` (resuelto por
  análisis estático). Falla CI si encuentra uno sin `frozen=True` que no
  esté en la exemption list.

---

## §3. R-STATELESS — Activities sin estado

**Regla:** activities NO mantienen state entre llamadas. No cache a nivel
módulo. No singletons. Cada call construye lo que necesita.

### §3.1 Anti-patterns

```python
# ❌ INVÁLIDO — module-level cache (R-STATELESS violation)
_TOOL_REGISTRY: dict[str, ToolBase] = {}

@activity.defn(name="execute_tool")
async def execute_tool(input: ExecuteToolInput) -> str:
    if input.name not in _TOOL_REGISTRY:
        _TOOL_REGISTRY[input.name] = build_tool(input.name)  # STATEFUL
    return await _TOOL_REGISTRY[input.name].execute(input.params)


# ❌ INVÁLIDO — singleton client (R-STATELESS violation)
_HTTP_CLIENT = httpx.AsyncClient()        # creado al import del módulo

@activity.defn
async def fetch_thing(url: str) -> str:
    return (await _HTTP_CLIENT.get(url)).text
```

### §3.2 Patrón correcto: factories cacheadas en composition

```python
# ✅ VÁLIDO — cache vive en composition.py por plugin (no en activities/)

# src/plugins/<id>/agent/composition.py
from functools import lru_cache

@lru_cache(maxsize=1)
def get_tool_registry(workspace_path: str) -> ToolRegistry:
    return build_tool_registry(workspace_path=workspace_path)


# src/platform/temporal/activities.py
@activity.defn(name="execute_tool")
async def execute_tool(input: ExecuteToolInput) -> str:
    registry = get_tool_registry(input.workspace)     # cacheado por workspace
    return await registry.dispatch(input.name, input.params, ctx)
```

**Por qué `composition.py` y no en `activities/`:**

- `composition.py` es plugin-specific (no `platform/`). Cumple R-DIP.
- `@lru_cache(maxsize=1)` por `workspace` es deterministic por session.
- La cache vive en el worker process. Si reiniciás el worker, la cache
  se va — no hay state cross-restart. Cumple R-STATELESS "moralmente".

### §3.3 Anti-pattern: HTTP client global

```python
# ❌ INVÁLIDO
_HTTP = httpx.AsyncClient()

@activity.defn
async def fetch(url: str) -> str:
    return (await _HTTP.get(url)).text


# ✅ VÁLIDO — cliente cacheado por composition o construido per-call
@lru_cache(maxsize=1)
def get_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30.0)

@activity.defn
async def fetch(url: str) -> str:
    client = get_http_client()
    return (await client.get(url)).text
```

### §3.4 Enforcement

- Convention + AST scan parcial (`test_r_stateless.py`).
- Code review.

---

## §4. R-HEARTBEAT — Activities long-running heartbeat

**Regla:** activities con worst-case >10s deben usar
`@with_heartbeat(every=10)`.

### §4.1 Por qué

Temporal asume que un activity con `start_to_close_timeout=30s` que no
hace heartbeat durante 25s está zombie. Hace cancel + retry. Si tu
activity hace un LLM call que tarda 28s, vas a tener cancels falsos.

`@with_heartbeat(every=10)` hace `activity.heartbeat()` cada 10s en
background mientras tu activity corre. Temporal sabe que sigue viva.

### §4.2 Snippet

```python
# canonical — src/platform/temporal/heartbeat.py
from functools import wraps
import asyncio
from temporalio import activity

def with_heartbeat(every: int = 10):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            stop = asyncio.Event()
            async def beat():
                while not stop.is_set():
                    activity.heartbeat()
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=every)
                    except asyncio.TimeoutError:
                        pass
            heartbeat_task = asyncio.create_task(beat())
            try:
                return await fn(*args, **kwargs)
            finally:
                stop.set()
                await heartbeat_task
        return wrapper
    return decorator


# Uso:
@activity.defn
@with_heartbeat(every=10)
async def llm_chat(input: LLMChatInput) -> LLMResponse:
    # ... 30s LLM call ...
    return response
```

### §4.3 Excepciones (`R_HEARTBEAT_EXEMPTIONS`)

`hubara_agency/tests/architecture/conftest.py`. Activities con worst-case
estrictamente <10s pueden omitir. Hoy hay ~3 entries.

### §4.4 Enforcement

- `test_r_heartbeat.py` — AST scan de cada `@activity.defn` cuyo
  `start_to_close_timeout` (declarado en retry policy) sea ≥30s. Si no
  tiene `@with_heartbeat`, falla.

---

## §5. R-DIP — Dependency Inversion + Plugin Isolation

**Regla:** `src/platform/` NO importa `src/plugins/...`. Plugins NO
importan plugins siblings. `tools/` NO importa `temporalio.client/worker`.
`parsers.py` NO importa libs HTTP / litellm / temporalio.

### §5.1 Los 4 contratos `import-linter`

Detalle en `sections/08-tests-and-gates.md §3`. Resumen:

| Contrato | Forbidden source → forbidden module |
|---|---|
| `platform-no-agents` | `src.platform` → `src.plugins.<X>.agent` |
| `agents-independent` | `src.plugins.<A>.agent` → `src.plugins.<B>.agent` (cross-plugin) |
| `tools-no-temporal` | `src.plugins.<X>.agent.<sub>.tools` → `temporalio.client/worker` |
| `parsers-pure` | `src.plugins.<X>.agent.<sub>.parsers` → `httpx/requests/litellm/temporalio` |

### §5.2 Cómo respetar `platform-no-agents` (DI invertida)

`platform/` NO sabe de plugins. Si `platform/` necesita ejecutar tools
del plugin (e.g. `execute_tool` activity en `platform/temporal/`), usa
el **registry pattern**:

```python
# platform/tool_extensions.py
_TOOL_FACTORIES: dict[str, Callable[[str], ToolBase]] = {}

def register_tool_extension(name: str, factory: Callable[[str], ToolBase]) -> None:
    _TOOL_FACTORIES[name] = factory

def build_tool_registry(workspace_path: str) -> ToolRegistry:
    return ToolRegistry({name: f(workspace_path) for name, f in _TOOL_FACTORIES.items()})


# Plugin (chats/workers/sales.py) registra al boot:
from src.platform.tool_extensions import register_tool_extension
from src.plugins.chats.agent.sales.tools.search_products import SearchProductsTool

register_tool_extension(
    "chats.search_products",
    lambda workspace_path: SearchProductsTool(workspace_path=workspace_path),
)
```

`platform/temporal/activities.py` consume el registry sin importar
ninguna tool concreta. R-DIP satisfecho.

### §5.3 Cómo respetar `agents-independent`

Plugins NO importan plugins. Si `chats` necesita catalogar, NO hace
`from src.plugins.catalog.agent.snapshot import ...`. En su lugar:

- Lee directo del filesystem (catalog escribe en `$WORKSPACE_VAULT_DIR/catalog/`).
- O usa la API REST de catalog (si la tuviera).

Lo único cross-plugin permitido es **vía `src/platform/`** — promote la
funcionalidad shared a platform.

### §5.4 Snippets válidos vs inválidos

```python
# ❌ INVÁLIDO — cross-plugin import
# src/plugins/chats/agent/sales/tools/search_products.py
from src.plugins.catalog.agent.snapshot import read_snapshot      # ❌


# ✅ VÁLIDO — leer del filesystem directo
from pathlib import Path
import json

class SearchProductsTool(ToolBase):
    async def execute_with_context(self, ctx, **kwargs):
        snapshot_dir = Path(os.environ["CATALOG_SNAPSHOT_DIR"])     # del env, no del plugin
        manifest = json.loads((snapshot_dir / "manifest.json").read_text())
        # ... query manifest ...


# ✅ ALTERNATIVO — promote a platform si querés código compartido
# src/platform/catalog/snapshot.py
def read_catalog_snapshot(snapshot_dir: str) -> dict:
    return json.loads((Path(snapshot_dir) / "manifest.json").read_text())

# Plugin chats lo importa:
from src.platform.catalog.snapshot import read_catalog_snapshot   # ✅ via platform
```

### §5.5 Enforcement

- `lint-imports` (import-linter via `uv run lint-imports`).
- Falla en CI si rompés algún contrato.

### §5.6 Cross-worker flow: declarative orchestration (ADR-2026-05-20)

**Regla de oro:** un workflow NUNCA importa el class del workflow de otro
worker hermano (R-DIP #10). Tampoco importa sus `contracts.py` o
`activities/`. Si necesitás "arrancar el otro workflow cuando termine el mío":

#### Patrón canónico

1. **Define un completion event** en `src/plugins/<plugin>/shared/contracts/events.py`:

```python
# shared/contracts/events.py — accesible para AMBOS siblings (no viola R-DIP).
@dataclass(frozen=True)
class SalesSessionCompletionEvent:
    session_id: str
    tag: str              # "INTERESADO" | "HUMANO" | etc.
    motivo: str = ""
    delay_seconds: int = 60
```

2. **Declara el flujo en el manifest** del worker source:

```yaml
# frontend_dashboard/src/plugins/<plugin>/plugin.yaml
agent:
  workers:
    - name: sales
      workflow_classes: [HubaraSalesSessionWorkflow]
      emits: [SalesSessionCompletionEvent]
      transitions:
        - id: sales_to_remarketing_on_interested
          on_event: SalesSessionCompletionEvent
          when: { tag: INTERESADO }
          action:
            via: start_workflow_with_replace
            target_plugin: <plugin>           # opcional — default = same plugin
            target_worker: remarketing
            target_workflow: RemarketingWorkflow   # el @workflow.defn name
            workflow_id_template: "remarketing-{event.session_id}"
            input_mapping:
              session_id: "$.session_id"
              motivo: "$.motivo"
            start_delay_field: delay_seconds
```

3. **El workflow source emite el evento** via `dispatch_event_activity`:

```python
from src.platform.orchestration import dispatch_event_activity, envelope_for
from src.plugins.chats.shared.contracts.events import SalesSessionCompletionEvent

# Dentro de @workflow.run, cuando llegue el momento de transition:
await workflow.execute_activity(
    dispatch_event_activity,
    envelope_for(
        SalesSessionCompletionEvent(
            session_id=session.session_id,
            tag="INTERESADO",
            motivo="cliente dudó del precio",
            delay_seconds=60,
        ),
        source_plugin="<plugin>",
        source_worker="sales",
    ),
    start_to_close_timeout=timedelta(seconds=30),
    retry_policy=RetryPolicy(maximum_attempts=3),
)
```

4. **El dispatcher hace todo lo demás**: lee el manifest, encuentra la
   transition matchea por `on_event` + `when`, arranca el target workflow
   por NOMBRE (string) en el task_queue correcto, con el input_mapping
   aplicado.

#### Verbos disponibles (`action.via`)

| Verbo | Comportamiento |
|---|---|
| `start_workflow` | Falla si el workflow_id ya existe |
| `start_workflow_with_replace` | Termina el existente RUNNING y arranca fresco |
| `ensure_running` | Noop si RUNNING, arranca si no existe |
| `signal` | Envía signal al workflow existente (requiere `signal_name:`) |

#### Side-effects (write metadata, etc.)

Si necesitás escribir metadata ANTES del dispatch (e.g.
`pending_handoff_summary`), invocá una activity genérica de platform/ por
separado. NO embedas side-effects en el dispatcher. Patrón:

```python
# 1. Side-effect (generic activity, no agent imports)
await workflow.execute_activity(
    write_pending_handoff_activity,
    args=[session_id, summary],
    start_to_close_timeout=timedelta(seconds=15),
)

# 2. Emit event → dispatcher routes via manifest
await workflow.execute_activity(
    dispatch_event_activity,
    envelope_for(MyEvent(...), source_plugin=..., source_worker=...),
    ...
)
```

#### Workflow.patched() durante migración

Si refactorizás un workflow existente de invocación directa a Nivel 3, **DEBÉS
usar `workflow.patched(...)`** para preservar replay-safety:

```python
if workflow.patched("declarative-orchestration-v1"):
    # Level 3 path
    await workflow.execute_activity(dispatch_event_activity, envelope_for(...))
else:
    # Legacy path — preserved hasta drain
    await workflow.execute_activity(legacy_activity, decision)
```

Tras `idle_timeout` cumplido en producción (drain), `workflow.deprecate_patch("declarative-orchestration-v1")` y eliminar el legacy branch.

#### Pre-requisitos antes de declarar una transition

1. El **target worker** debe declarar `workflow_classes: [<class_name>]` con
   el nombre del `@workflow.defn(name="...")` (no el Python class name).
2. El **target workflow** debe aceptar un dict como input (o tener type hints
   en `run()` para que Temporal deserialice automaticamente al dataclass).
3. El **target bootstrap activity** debe ser robusto a campos opcionales del
   input (fallback a config local con `get_workspace_path()` si necesario).

#### Anti-patterns a evitar

```python
# ❌ INVÁLIDO — viola R-DIP #10
from src.plugins.<plugin>.agent.<other_agent>.workflows.X import OtherWorkflow
await client.start_workflow(OtherWorkflow, ...)

# ❌ INVÁLIDO — workflow class import + .run reference
from src.plugins.<plugin>.agent.<other_agent>.workflows.X import OtherWorkflow
await client.start_workflow(OtherWorkflow.run, ...)

# ❌ INVÁLIDO — importing DTO from sibling
from src.plugins.<plugin>.agent.<other_agent>.contracts import OtherInput

# ✅ VÁLIDO — declarative dispatch via manifest
await workflow.execute_activity(
    dispatch_event_activity,
    envelope_for(MyCompletionEvent(...), source_plugin="<plugin>", source_worker="me"),
)
```

#### Enforcement

- `tests/architecture/test_r_dip_workflow_class_imports.py` falla CI si
  detecta el patrón inválido.
- `tests/architecture/test_manifest_orchestration_consistency.py` detecta
  drift (transitions apuntan a eventos no declarados en `emits[]`, o a
  workflows no declarados en `workflow_classes[]` del target).
- `lint-imports` falla si un agent importa otro sibling.

Ver ADR-2026-05-20-declarative-orchestration para el rationale completo.

### §5.7 Footguns del declarative orchestration (post-premortem)

Estos 4 patrones SÍ rompen producción aunque no violen R-DIP. Aprendidos del
premortem de commit `eb27473`. **Pruebá explícitamente cada uno cuando toques
orchestration:**

#### F1. Dict → dataclass contract en el boundary del workflow target

**Cómo funciona:** el dispatcher pasa un **dict** a
`client.start_workflow(name, dict, ...)`. Temporal lo deserializa al type
hint de `@workflow.run(self, input: <TargetInput>)` reconstruyendo
`TargetInput(**dict)`. Esto solo funciona si:

- **TODO campo no provisto por `input_mapping` tiene default en el dataclass**, O
- **El bootstrap activity tolera su ausencia** (ej: fallback a config local
  con `get_workspace_path()` si `runtime_workspace_path` viene `None`).

**Footgun concreto:** alguien agrega un campo NUEVO required (sin default)
a `RemarketingSessionInput`. El dispatcher sigue pasando
`{"session_id": "x", "motivo": "y"}` — Temporal lanza `TypeError:
__init__() missing 1 required positional argument`. **En producción, no en
CI** (porque los tests architecturales no escanean signatures de input).

```python
# ❌ MALO — agregás campo required sin actualizar input_mapping
@dataclass
class RemarketingSessionInput:
    session_id: str
    motivo: str
    new_field: str          # ← NEW: no default, no en input_mapping → BOOM en runtime

# ✅ BUENO — opción A: default
@dataclass
class RemarketingSessionInput:
    session_id: str
    motivo: str
    new_field: str = ""    # ← default

# ✅ BUENO — opción B: agregar al input_mapping del manifest
# plugin.yaml:
transitions:
  - id: ...
    action:
      input_mapping:
        session_id: "$.session_id"
        motivo: "$.motivo"
        new_field: "$.something"   # ← derivado del evento

# ✅ BUENO — opción C: bootstrap fallback
# bootstrap_activity:
new_field = input.new_field or compute_default()
```

**Test enforcer:** `tests/platform/orchestration/test_dict_to_dataclass_contract.py`
(marked `functional`) verifica:
1. dict → dataclass materialization works
2. Missing required field fails LOUD (TypeError surfaced, no silencio)

#### F2. `workflow.patched()` debe preservar paridad de activities

Cuando refactorizás un workflow existente a Level 3, el `workflow.patched()`
gate protege replay. **Pero ambas ramas DEBEN ejecutar el mismo número de
activities** (Temporal valida count of activity completions vs history).

```python
# ❌ MALO — ramas con conteo desigual de activities
if workflow.patched("v1"):
    await workflow.execute_activity(dispatch_event_activity, ...)  # 1 activity
    await workflow.execute_activity(write_log, ...)                 # ← 2 total
else:
    await workflow.execute_activity(legacy_activity, ...)           # 1 total → NondeterminismError

# ✅ BUENO — paridad
if workflow.patched("v1"):
    await workflow.execute_activity(dispatch_event_activity, ...)  # 1 activity
else:
    await workflow.execute_activity(legacy_activity, ...)           # 1 activity
```

Si necesitás side-effects extra en la rama nueva (ej: `write_pending_handoff`
antes de `dispatch_event`), ponelos en un helper method que SÓLO existe en
la rama nueva — el legacy mantiene su shape original. Mirá
`RemarketingSessionWorkflow._handoff_to_sales` como referencia: la rama
patched llama 2 activities, la legacy 1 — pero **eso solo es safe porque
los workflows con histories pre-patch ya consumieron la legacy y no se
re-replay-an con el patch encima** (el `patched()` bool resuelve True para
nuevos histories, False para viejos, y queda inmutable en history).

**Regla práctica:** si tu refactor cambia el conteo de activities entre
ramas, leé el doc de `workflow.patched()` antes de mergear. Si dudás,
agregá un test de replay con histories pre-patch (ver `test_replay_remarketing.py`).

#### F3. Path comparisons en tests sin `Path.resolve()`

```python
# ❌ FRAGILE — falla en CI con symlinks o relative paths
assert str(get_workspace_path()) in str(result.workspace.path)

# ✅ ROBUSTO — normaliza ambos lados
from pathlib import Path
expected = Path(get_workspace_path()).resolve()
actual = Path(result.workspace.path).resolve()
assert actual == expected
```

#### F4. Eventos opcionales que nadie consume → no_matches silencioso

El dispatcher devuelve `DispatchResult(no_matches=True)` cuando ningún
transition matchea el evento. Esto **NO es error** (workflows pueden emitir
eventos terminales para observability). Pero si te equivocaste con `when:`
o `tag:`, queda silencioso.

**Mitigación:** loguear claramente cuando no hay matches. El dispatcher ya
lo hace (`structlog.info("no matching transition")`). En tests
funcionales, asertá explícitamente `result.no_matches is False` cuando
esperás que la transition dispare.

#### F5. Nested dataclass + PEP 563 en boundary del workflow target

**Confirmado en producción** (workflow `df5a8fe2-bb7c-4627-b861-dc19643467be`,
2026-05-20). Un activity con tipo de retorno como:

```python
from __future__ import annotations  # ← PEP 563
from dataclasses import dataclass, field

@dataclass(frozen=True)
class DispatchedTransition:
    workflow_id: str
    outcome: str

@dataclass(frozen=True)
class DispatchResult:
    matches: list[DispatchedTransition] = field(default_factory=list)

@activity.defn(name="orchestration.dispatch_event")
async def dispatch_event_activity(envelope: EventEnvelope) -> DispatchResult:
    ...
```

…**revienta** cuando el workflow caller intenta deserializar el resultado:

```
NameError: name 'DispatchedTransition' is not defined
RuntimeError: Failed decoding arguments
```

**Mecánica del fallo:** Temporal's default DataConverter llama
`get_type_hints(DispatchResult)` para reconstruir la dataclass. Con
`from __future__ import annotations`, los hints son strings (`"list[DispatchedTransition]"`)
y `get_type_hints` los evalúa via `eval(...)` en el namespace del módulo.
**En el sandbox del workflow**, ese namespace está restringido — la inner
class no está disponible. NameError → infinite retry loop del workflow task
→ el workflow queda colgado.

**Fix:** remove `from __future__ import annotations` del módulo del activity.
Sin PEP 563, los hints se evalúan at-class-definition-time y se guardan como
`types.GenericAlias` reales — `get_type_hints` los devuelve directamente
sin `eval()`. **Orden de definición importa**: la inner dataclass debe estar
ANTES que la outer en el archivo.

**Fallback si no podés remover future annotations:** cambiar el tipo de
retorno a tipos planos (`list[dict]` en lugar de `list[Inner]`) — pierde
type safety pero garantiza serialización.

**Test enforcer:** `tests/architecture/test_r_json_nested_dataclass.py`
(`test_no_future_annotations_with_nested_dataclass_boundary`) escanea
módulos que combinan `@activity.defn` + dataclasses anidadas + future
annotations y falla CI antes del merge.

### §5.8 Checklist obligatorio post-cambio en orchestration

Si tu PR toca CUALQUIERA de:
- `src/platform/orchestration/`
- `src/platform/temporal/dispatcher.py`
- `frontend_dashboard/src/plugins/<plugin>/plugin.yaml` (campos
  `workflow_classes`, `emits`, `transitions`)
- `src/plugins/<plugin>/shared/contracts/events.py`
- Cualquier `Input` dataclass que es target de un transition

**ANTES de emitir `status: passed`:**

1. `uv run lint-imports` → 4 contracts kept, 0 broken
2. `uv run pytest tests/architecture/test_r_dip_workflow_class_imports.py -v`
3. `uv run pytest tests/architecture/test_manifest_orchestration_consistency.py -v`
4. `uv run pytest tests/architecture/test_r_json_nested_dataclass.py -v` (F5 footgun)
5. `uv run pytest tests/platform/orchestration/ -v` (orchestration unit tests)
6. Si modificaste un dataclass target (campos): correr el contract test:
   `uv run pytest tests/platform/orchestration/test_dict_to_dataclass_contract.py -m functional -v`
   (cold start ~3min, paciencia)
7. Si refactorizaste con `workflow.patched()`: agregar test de replay con
   history fixture pre-patch (mirá `tests/test_replay_remarketing.py` como
   plantilla).
8. Si tocaste el manifest: smoke test al system_map:
   ```bash
   uv run python -c "
   from src.plugins.system_map.domain.builder import build_system_graph
   g = build_system_graph()
   assert not g.warnings, g.warnings
   print('edges:', [(e.source, e.target, e.label) for e in g.edges if e.kind=='invokes_worker'])
   "
   ```
   No debe haber warnings y los edges esperados deben aparecer con labels.

Ver ADR-2026-05-20-declarative-orchestration §10 (trade-offs) +
ADR-2026-05-20-declarative-orchestration §11 (riesgos).

### §5.9 LLM response handling — content vs reasoning_content (post-mortem df5a8fe2)

**Caso confirmado en producción** (workflow `df5a8fe2-bb7c-4627-b861-dc19643467be`,
2026-05-20). DeepSeek-v4-flash (y modelos thinking-mode en general — o1, R1,
Claude extended-thinking) ocasionalmente devuelven:

```json
{
  "content": "",
  "reasoning_content": "¡Claro! Tenemos 9 productos en nuestro catálogo. Te los...",
  "finish_reason": "stop",
  "has_tool_calls": false
}
```

El LLM puso la respuesta destinada al cliente en el canal de razonamiento.
El guard del workflow `if result.final_content:` (string vacío es falsy)
suprime el envío → el cliente nunca recibe respuesta → 60s después dispara
el ghosting timer → el bot termina ejecutando `manage_conversation_tag` y
programando remarketing sin que el cliente haya abandonado realmente.

**Regla obligatoria en `run_agent_turn` (workflow_helpers.py):**

```python
# Si terminó sin tool calls Y content vacío Y hay reasoning_content,
# inyectar un system reminder y reintentar UN turn de LLM.
if (
    not response.content
    and response.reasoning_content
    and iteration < session.llm.max_iterations
):
    workflow.logger.warning("LLM emitted empty content with reasoning present — nudging")
    messages = [
        *messages,
        {
            "role": "system",
            "content": (
                "Tu mensaje al cliente debe ir en el campo `content`, "
                "no en el canal de razonamiento. Responde ahora al "
                "cliente directamente, en español natural y cálido."
            ),
        },
    ]
    continue  # retry llm_chat

# Después del retry, si content sigue vacío → fallback NATURAL HUMANO
# (no genérico de bot, no menciona "AI", "error", "sistema"):
if not final_content:
    final_content = "¡Perdón! Justo se me cortó un segundito. ¿Me repetís lo que necesitabas?"
```

**Reglas críticas del fallback:**

- **NO promover `reasoning_content` a `content` directamente.** El reasoning
  puede incluir meta-comentarios tipo "Debo llamar la tool X..." que romperían
  la persona humana del agente. Sólo retry con nudge.
- **El fallback debe sonar humano.** Frases prohibidas: "soy un bot",
  "sistema", "error", "asistente AI", "modelo de lenguaje". Frases válidas:
  "Justo se me cortó", "Dame un segundito", "Perdón, se me trabó".
- **Ghosting prompt debe ser consciente del fallback.** El prompt de
  `decide_ghosting_action` debe instruir al modelo a detectar "el último
  mensaje del agente fue una disculpa por interrupción técnica" y NO
  tratarlo como ghosting real — usar `INTERESADO` con motivo de interrupción
  técnica en su lugar.

Aplica a TODOS los agentes que ejecuten LLM con tool-loop (sales, remarketing,
futuros agentes). Centralizado en `src/platform/workflow_helpers.py:run_agent_turn`
— no duplicar la lógica en cada workflow.

Test sugerido (functional, opcional): mock `llm_chat` para devolver
`(content="", reasoning_content="real answer")` y verificar que el workflow
hace el retry y termina con un mensaje no vacío.

### §5.10 Eligibility gates para workflows programados con `start_delay`

**Caso confirmado en producción** (workflow `remarketing-wa_573125671604` run
`e688685d-c676-4e61-a152-b22ff49788db`, 2026-05-21). Cuando un workflow se
programa con `start_delay=N seconds` (ej. el dispatcher hace
`client.start_workflow(..., start_delay=timedelta(seconds=60))`), el estado
del sistema puede cambiar entre el momento del programa y el arranque del
workflow. Si el workflow toca state compartido sin chequear el estado
actual, puede pisar decisiones humanas.

**Caso concreto observado:**

1. Sales workflow programa `RemarketingWorkflow` con start_delay=60s vía
   `dispatch_event_activity` (tag=INTERESADO en `manage_conversation_tag`).
2. Antes de que pasen los 60s, el cliente vuelve a hablar.
3. Sales workflow procesa los nuevos mensajes y decide ESCALATE_TO_HUMAN
   (escala a humano: `active_route=humano`, `tag=HUMANO`).
4. Pasan los 60s → arranca el remarketing workflow programado.
5. Sin gate: el remarketing llama `claim_conversation_routing(session_id,
   ROUTE_REMARKETING)` **sobrescribiendo el `active_route=humano`**, y
   envía un mensaje al cliente reactivando la conversación. **Violación
   directa de la regla de negocio**: cuando hay humano en el caso, ningún
   bot interviene hasta que el humano devuelva el control.

**Regla obligatoria:** todo workflow que se programe con `start_delay > 0`
debe tener una **eligibility gate** como su primera activity. Esta activity:

1. Lee el estado actual del sistema (metadata.json, DB, etc.).
2. Devuelve un dataclass plano (sin nested dataclasses por F5) con
   `eligible: bool` + razón del bloqueo.
3. Si `eligible=False`, el workflow returna early **SIN side-effects** (no
   pisar routing, no enviar mensajes, no escribir state).

```python
# ✅ Patrón canónico
@activity.defn(name="check_remarketing_eligibility")
async def check_remarketing_eligibility(session_id: str) -> RemarketingEligibility:
    metadata = read_metadata(session_id)
    if metadata.active_route == ROUTE_HUMANO:
        return RemarketingEligibility(
            eligible=False,
            current_route=metadata.active_route,
            current_tag=metadata.tag,
            blocked_reason="active_route=humano — human handling the case",
        )
    if metadata.tag in TERMINAL_TAGS:
        return RemarketingEligibility(
            eligible=False,
            ...,
            blocked_reason=f"tag={metadata.tag} (terminal)",
        )
    return RemarketingEligibility(eligible=True, ...)

# En el workflow (gate envuelta en workflow.patched para replay-safety):
@workflow.run
async def run(self, input: RemarketingSessionInput) -> None:
    if workflow.patched("remarketing-eligibility-gate-v1"):
        eligibility = await workflow.execute_activity(
            check_remarketing_eligibility,
            args=[input.session_id],
            start_to_close_timeout=timedelta(seconds=15),
        )
        if not eligibility.eligible:
            workflow.logger.warning("Aborted by gate: %s", eligibility.blocked_reason)
            return  # SIN side-effects
    # ... resto del workflow
```

**Reglas clave de la gate:**

- **Antes de cualquier side-effect.** No `claim_conversation_routing`, no
  `send_whatsapp_message`, no escribir state. Sólo leer.
- **Fail-safe = NO eligible.** Si el state es corrupto/ilegible, bloquear
  el workflow. Mejor un remarketing perdido que pisar un caso humano.
- **`workflow.patched` gate.** Workflows pre-deploy ya en vuelo NO ejecutan
  la gate (su history no tiene el activity_scheduled correspondiente). Tras
  drain del sistema, `workflow.deprecate_patch(...)`.
- **Sin pisar metadata, no escribir history falso.** El workflow debe poder
  ser re-arrancado limpiamente si el humano devuelve el control via
  dashboard. Si la gate escribió "intentó arrancar" en algún log, OK; si
  escribió "active_route=remarketing", está roto.

**Aplica a todo nuevo workflow programado con `start_delay`** que toque
state compartido (active_route, tags, conversación visible al cliente).
Patrón implementado en `RemarketingWorkflow.run` (commit del fix
post-mortem run e688685d).

Test enforcer (functional): mock metadata con `active_route=humano` o
`tag=HUMANO`/`COMPRA_EXITOSA` y verificar que la activity devuelve
`eligible=False` con `blocked_reason` no vacío. Ver
`tests/test_remarketing_eligibility.py` como plantilla.

---

## §6. Cómo bloquearse correctamente cuando R-rule te lo pide

Si tu task NO se puede implementar sin violar una R-rule, **NO la violes**.
En su lugar:

```yaml
# task-result.yaml
status: blocked
blocked_reason: requires_planner_update
notes: |
  La tarea pide ejecutar un LLM call directamente desde un workflow file
  (workflows/sales.py). Eso viola R-DET — los LLM calls deben vivir en
  activities (llm_chat). El planner debería:
    1. Mover la llamada al activity llm_chat (ya existe).
    2. O proponer ADR si genuinamente necesita LLM síncrono en workflow
       (improbable; sería el primer caso del repo).
```

El planner re-decompone o escala a ADR.

---

## §7. Cómo agregar excepción a la regla (proceso ADR)

**Solo el operador inicia esto, NO el implementer.**

1. **ADR** documentando:
   - Qué R-rule querés relajar / extender.
   - Por qué el caso justifica.
   - Migración plan (si rompe code existente).
2. **PR separado** etiquetado `architecture-change`:
   - Edita el test bajo `tests/architecture/`.
   - Si aplica, edita `.importlinter` o las exemption dicts.
   - **Human review obligatorio.**
3. **Después de mergear el architecture-change PR**, el feature task
   bloqueado se puede re-correr.

---

## §8. Cheat sheet final

| R-rule | Test | Quick check |
|---|---|---|
| R-DET | `pytest -m architecture` (replay tests) | `grep -E '(datetime\.now\|random\.\|os\.environ\|asyncio\.sleep)' src/plugins/*/agent/workflows/` debe ser vacío |
| R-JSON | `test_r_json.py` | Cada DTO de boundary tiene `@dataclass(frozen=True)` |
| R-STATELESS | `test_r_stateless.py` | No `_REGISTRY = {}` ni similar en `activities/` |
| R-HEARTBEAT | `test_r_heartbeat.py` | Activities >10s tienen `@with_heartbeat` |
| R-DIP | `lint-imports` | 4 contratos verdes |

---

**Fin reference.**
