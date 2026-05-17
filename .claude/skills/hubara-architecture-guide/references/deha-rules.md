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
