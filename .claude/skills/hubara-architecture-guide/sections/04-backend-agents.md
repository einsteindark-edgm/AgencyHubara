# Sección 04 — Backend agents (workflows, activities, tools, Temporal patterns)

> **Cuándo leer esto:** vas a escribir un workflow, activity, o tool
> Temporal dentro de un plugin (template C o D).
> **Pre-requisito:** `sections/01-general.md` + `sections/03-backend-plugin.md`.
> **Tamaño:** ~14 KB.
> **Reference complementario:** `references/temporal-patterns.md`,
> `references/deha-rules.md`.

---

## §1. Anatomía de un plugin agéntico

Un plugin con worker (template C/D) tiene esta estructura:

```
hubara_agency/src/plugins/<id>/
├── agent/
│   ├── __init__.py              # docstring; NO exporta WORKFLOWS (worker registra a mano)
│   ├── contracts.py             # @dataclass frozen — DTOs boundary (R-JSON)
│   ├── composition.py           # @lru_cache(maxsize=1) factories de tools / use cases
│   ├── workflows/
│   │   ├── __init__.py
│   │   └── <name>.py            # @workflow.defn — código DETERMINÍSTICO
│   ├── activities/
│   │   ├── __init__.py
│   │   └── <name>.py            # @activity.defn — I/O permitido
│   ├── tools/
│   │   └── <name>.py            # ToolBase — funciones puras
│   ├── parsers.py               # parsers JSON (puros, sin I/O)
│   └── prompts.py               # constantes / templates de prompt
└── workers/
    └── <worker_name>.py         # async def main() con Worker(...)
```

**Regla mnemotécnica:**

- `workflows/` — **determinístico** (R-DET). No I/O, no datetime.now(), no random.
- `activities/` — **I/O permitido**. Wrappers de calls externos (HTTP, DB, FS).
- `tools/` — **funciones puras**. Devuelven JSON envelope. NO importan `temporalio.client/worker`.
- `parsers/` — **puro**. NO importan httpx/requests/litellm/temporalio.
- `contracts/` — **dataclasses frozen JSON-serializable**. NO importan nada I/O.

Las 5 R-rules de DEHA aplican acá. Detalle exhaustivo en
`references/deha-rules.md`.

---

## §2. Workflows — el código determinístico

### §2.1 Estructura básica

```python
# canonical — src/plugins/<id>/agent/workflows/<name>.py
from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    # Imports que el sandbox de Temporal no debe inspeccionar
    from src.platform.workflow_helpers import PendingMessage, run_agent_turn
    from src.plugins.<id>.agent.contracts import MyInput
    from src.platform.temporal.retry_policies import _CONV_OPTIONS

@workflow.defn(name="MyWorkflow")
class MyWorkflow:
    def __init__(self) -> None:
        self._pending: list[PendingMessage] = []
        self._stop = False

    @workflow.signal
    def send_message(self, msg: PendingMessage) -> None:
        # Signal handler — append-only, NUNCA llama execute_activity
        self._pending.append(msg)

    @workflow.query
    def get_status(self) -> str:
        return "running" if not self._stop else "stopped"

    @workflow.run
    async def run(self, input: MyInput) -> None:
        # bootstrap activity (siempre lo primero)
        session = await workflow.execute_activity(
            bootstrap_my_session_activity, input, **_CONV_OPTIONS,
        )

        while not self._stop:
            # debounce: espera 1.5s de silencio (cap 12s)
            await self._wait_debounced()

            # coalesce los N mensajes pendientes en uno
            msg = coalesce_pending(self._pending)
            self._pending = []

            turn = await run_agent_turn(session, msg)
            # process turn.transfer_decision, turn.schedule_remarketing, etc.

            # Continue-as-new cada N turnos para evitar history grande
            if workflow.info().total_history_length > MAX_HISTORY:
                workflow.continue_as_new(input)
```

### §2.2 Cosas PROHIBIDAS en workflows (R-DET)

| ❌ NO hacer | ✅ Hacer en su lugar |
|---|---|
| `datetime.now()` | `workflow.now()` |
| `uuid.uuid4()` | `workflow.uuid4()` |
| `random.random()` | `workflow.random().random()` |
| `time.sleep(5)` | `await workflow.sleep(5)` |
| `await asyncio.sleep(5)` | `await workflow.sleep(5)` |
| `import httpx; httpx.get(...)` | mover el call HTTP a una activity |
| `os.environ.get("X")` | leer env vars en el worker (`workers/<name>.py`), pasar via `Worker(...).run()` o via composition |
| `open("file.txt").read()` | activity con I/O |
| Mutar `_pending` desde dentro de un loop sin lock | usar `workflow.wait_condition(...)` |

### §2.3 Debounce pattern (replay-safe)

```python
# canonical — debounce 1.5s silencio, cap 12s
_DEBOUNCE_SILENCE_S = 1.5
_DEBOUNCE_CAP_S = 12.0

async def _wait_debounced(self) -> None:
    start = workflow.now()
    last_count = 0
    while True:
        await workflow.wait_condition(
            lambda: len(self._pending) > 0 or self._stop,
            timeout=timedelta(seconds=_DEBOUNCE_CAP_S),
        )
        if self._stop:
            return
        if len(self._pending) == last_count:
            # No llegaron mensajes en los últimos 1.5s → procesar
            return
        last_count = len(self._pending)
        elapsed = (workflow.now() - start).total_seconds()
        if elapsed >= _DEBOUNCE_CAP_S:
            return
        await workflow.sleep(timedelta(seconds=_DEBOUNCE_SILENCE_S))
```

### §2.4 Continue-as-new (history pruning)

```python
# canonical — continue-as-new cada 50 turnos
_CONTINUE_AS_NEW_AFTER_TURNS = 50

if self._turn_count >= _CONTINUE_AS_NEW_AFTER_TURNS:
    workflow.continue_as_new(input)
```

Temporal limita el history a ~50MB / workflow. Sin CAN, una sesión de
chat larga eventualmente falla con `WorkflowHistoryLimit`.

### §2.5 `workflow.patched()` para features gated

```python
# canonical — feature gated por patched (replay-safe)
if workflow.patched("typing-indicator-v1"):
    # Solo workflows nuevos ejecutan esto.
    # Los in-flight pre-deploy siguen sin typing indicator.
    await workflow.execute_activity(send_typing_indicator_activity, ...)
```

Usar cuando agregás una activity nueva a un workflow existente. Sin
`patched()`, el replay de un workflow viejo falla con
`NonDeterminismError`.

---

## §3. Activities — el código con I/O

### §3.1 Estructura básica

```python
# canonical — src/plugins/<id>/agent/activities/<name>.py
from dataclasses import dataclass
from temporalio import activity

from src.platform.temporal.heartbeat import with_heartbeat
from src.plugins.<id>.agent.contracts import MyInput, MyOutput

@activity.defn(name="my_activity")
@with_heartbeat(every=10)               # SOLO si worst-case >10s
async def my_activity(input: MyInput) -> MyOutput:
    # I/O permitido acá — esto NO es workflow code
    activity.logger.info("processing %s", input.session_id)
    result = await some_io(input.payload)
    return MyOutput(value=result)
```

### §3.2 Heartbeat (R-HEARTBEAT)

Cualquier activity con worst-case >10s debe usar `@with_heartbeat`:

```python
from src.platform.temporal.heartbeat import with_heartbeat

@activity.defn
@with_heartbeat(every=10)
async def long_running_activity(input: ...) -> ...:
    # ... LLM call que puede tardar 30s ...
    pass
```

El decorador hace `activity.heartbeat()` cada 10s en background. Sin
heartbeat, Temporal piensa que el activity está zombie y reintenta (caos).

**Excepciones documentadas** en `R_HEARTBEAT_EXEMPTIONS`
(`tests/architecture/conftest.py`) — solo para activities con worst-case
estrictamente <10s.

### §3.3 Retry policies — `_CONV_OPTIONS`, `_LLM_OPTIONS`, `_TOOL_OPTIONS`

Vienen de `src/platform/temporal/retry_policies.py`. Cada uno tiene
timeouts + retry tunados para su clase de operación:

```python
from src.platform.temporal.retry_policies import _CONV_OPTIONS, _LLM_OPTIONS, _TOOL_OPTIONS

# Conversation I/O (filesystem + WhatsApp send):
await workflow.execute_activity(build_prompt, input, **_CONV_OPTIONS)
# typical: 30s start_to_close, 3 retries, exponential backoff

# LLM calls (pueden tardar):
await workflow.execute_activity(llm_chat, input, **_LLM_OPTIONS)
# typical: 120s start_to_close, 3 retries

# Tool execution (mixed):
await workflow.execute_activity(execute_tool, input, **_TOOL_OPTIONS)
# typical: 60s start_to_close, 2 retries
```

**Nunca hardcodear `start_to_close_timeout=timedelta(seconds=30)`** en
tu workflow. Usá los presets. Si necesitás un valor distinto, agregalo a
`retry_policies.py` con un nombre semántico, no a `timedelta` inline.

### §3.4 Activities sin estado (R-STATELESS)

```python
# ❌ NO hacer — module-level cache
_REGISTRY: dict[str, ToolBase] = {}

@activity.defn
async def execute_tool(input: ExecuteToolInput) -> str:
    if input.name not in _REGISTRY:
        _REGISTRY[input.name] = build_tool(input.name)        # ❌ STATEFUL
    return await _REGISTRY[input.name].execute(input.params)


# ✅ Hacer — registry construido en cada call, cache en composition (cacheable por workspace)
from src.platform.tool_extensions import build_tool_registry

@activity.defn(name="execute_tool")
async def execute_tool(input: ExecuteToolInput) -> str:
    registry = build_tool_registry(workspace_path=input.workspace)
    return await registry.dispatch(input.name, input.params, ctx)
```

El `build_tool_registry` internamente usa `@lru_cache(maxsize=1)` sobre
`workspace`, así que en práctica es cacheado — pero el cache vive en
`composition.py` (sin clase, sin estado de worker), no en el módulo del
activity. R-STATELESS satisfecho.

---

## §4. Tools — el contrato LLM-facing

### §4.1 Estructura básica

```python
# canonical — src/plugins/<id>/agent/tools/<name>.py
import json
from exoclaw.agent.tools import ToolBase, ToolContext

class MyTool(ToolBase):
    name = "my_tool"                                 # ID que el LLM ve
    description = "Hace X cuando el usuario pide Y."
    parameters = {                                    # JSON Schema
        "type": "object",
        "properties": {
            "foo": {"type": "string", "description": "..."},
            "bar": {"type": "integer", "minimum": 1},
        },
        "required": ["foo"],
    }

    def __init__(self, workspace_path: str):
        self._workspace = workspace_path

    async def execute_with_context(self, ctx: ToolContext, **kwargs) -> str:
        # NO usar Temporal client acá (R-DIP — tools no importan temporalio.client)
        # NO hacer I/O hacia services externos (eso va en activities)
        # SÍ leer del filesystem si necesitás datos del workspace

        foo = kwargs["foo"]
        bar = kwargs.get("bar", 1)

        # Devolver JSON envelope. Si la tool emite una decision:
        return json.dumps({
            "status": "ok",
            "result": "...",
            "transfer_decision": {                    # opcional — el workflow lo parseará
                "session_id": ctx.session_id,
                "target_route": "ventas",
                "summary": "...",
            },
        })
```

### §4.2 Decisión vs acción (ADR-001)

**Las tools NO ejecutan acciones con efectos colaterales sobre Temporal.**
Devuelven una **decision** (DTO en el JSON envelope) que el workflow
parsea y aplica via una activity dedicada.

| Activity tipo "dispatcher" | Cuándo se llama |
|---|---|
| `start_or_signal_sales_workflow_activity(TransferDecision)` | Cuando una tool devuelve `transfer_decision` |
| `schedule_remarketing_workflow_activity(ScheduleRemarketingDecision)` | Cuando una tool devuelve `schedule_remarketing` |
| (la escalation no dispara workflow, sólo termina el actual) | Cuando una tool devuelve `escalation_decision` |

**Por qué este patrón:** tools sin Temporal client son **testables sin
WorkflowEnvironment** (unit test puro). Las activities dispatcher
encapsulan la I/O de Temporal. Cumple R-DET + R-STATELESS.

### §4.3 Registrar tools desde el worker

```python
# canonical — src/plugins/<id>/workers/<name>.py
from src.platform.tool_extensions import register_tool_extension
from src.plugins.<id>.agent.tools.my_tool import MyTool

async def main() -> None:
    # ... setup client + worker ...

    register_tool_extension(
        "<id>.my_tool",                          # namespace: plugin_id.tool_name
        lambda workspace_path: MyTool(workspace_path=str(workspace_path)),
    )

    await Worker(...).run()
```

El `lambda workspace_path: ...` es la factory que `execute_tool` invoca
con el workspace del session actual. NO instances la tool directamente
acá — eso rompería el aislamiento por session.

### §4.4 Documentar la tool en el workspace

Crear/editar `workspace/TOOLS.md` para que el LLM la conozca:

```markdown
## Tools disponibles

### my_tool

- **Cuándo llamarla:** cuando el usuario pide X.
- **Cuándo NO llamarla:** durante greetings o small-talk.
- **Returns:** JSON `{"status": "ok", "result": "..."}`.
```

El `ContextBuilder` lee `workspace/TOOLS.md` y lo inyecta al system prompt.

---

## §5. Composition factories (`composition.py`)

`composition.py` vive en cada plugin agentic. Es el lugar para factories
cacheadas:

```python
# canonical — src/plugins/<id>/agent/composition.py
from functools import lru_cache
from src.plugins.<id>.agent.tools.my_tool import MyTool

@lru_cache(maxsize=1)
def get_my_tool(workspace_path: str) -> MyTool:
    return MyTool(workspace_path=workspace_path)
```

**Por qué `@lru_cache(maxsize=1)`:** las activities llaman estos factories
cada turno. Sin cache, se reconstruye la tool en cada call (R-STATELESS
cumplido pero ineficiente). Con cache `maxsize=1` clave por workspace,
se cumple R-STATELESS Y reusamos la instance.

**NO usar `maxsize=None` (unbounded)** — leak de memoria si los workspaces
varían (debug agents, multi-tenant futuro).

---

## §6. Worker registration (`workers/<name>.py`)

El template canónico:

```python
# canonical — src/plugins/<id>/workers/<name>.py
import asyncio

from loguru import logger
from temporalio.worker import Worker

from src.platform.plugin_manifest import get_task_queue
from src.platform.logging import setup_logging
from src.platform.temporal.client import get_temporal_client
from src.platform.tool_extensions import register_tool_extension

# Plugin-local imports
from src.plugins.<id>.agent.activities.my_activity import my_activity
from src.plugins.<id>.agent.workflows.my_workflow import MyWorkflow
from src.plugins.<id>.agent.tools.my_tool import MyTool

setup_logging()

async def main() -> None:
    logger.info("Conectando worker <id>/<name> a Temporal...")
    client = await get_temporal_client()
    task_queue = get_task_queue("<id>", "<name>")     # post-PR11

    # Registrar tools del plugin
    register_tool_extension(
        "<id>.my_tool",
        lambda workspace_path: MyTool(workspace_path=str(workspace_path)),
    )

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[MyWorkflow],
        activities=[my_activity],
    )
    logger.info("Worker up. Queue: '{}'", task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## §7. Tests del plugin agéntico

### §7.1 Unit test de tool (sin Temporal)

```python
# tests/plugins/<id>/tools/test_my_tool.py
import pytest
from src.plugins.<id>.agent.tools.my_tool import MyTool

async def test_my_tool_returns_ok_envelope(tmp_path):
    tool = MyTool(workspace_path=str(tmp_path))
    ctx = type("Ctx", (), {"session_id": "test", "workspace": tmp_path})()
    result = await tool.execute_with_context(ctx, foo="bar")
    parsed = json.loads(result)
    assert parsed["status"] == "ok"
```

### §7.2 Activity test (con `ActivityEnvironment`)

```python
# tests/plugins/<id>/activities/test_my_activity.py
from temporalio.testing import ActivityEnvironment
from src.plugins.<id>.agent.activities.my_activity import my_activity

async def test_my_activity_happy_path():
    env = ActivityEnvironment()
    result = await env.run(my_activity, MyInput(...))
    assert result.value == "expected"
```

### §7.3 Workflow test (con `WorkflowEnvironment.start_time_skipping`)

```python
# tests/plugins/<id>/workflows/test_my_workflow.py
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from src.plugins.<id>.agent.workflows.my_workflow import MyWorkflow

async def test_my_workflow_runs_to_completion():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue="test-queue",
            workflows=[MyWorkflow], activities=[my_activity_fake],
        ):
            result = await env.client.execute_workflow(
                MyWorkflow.run, MyInput(...),
                id="test-id", task_queue="test-queue",
            )
            assert result.final_content == "..."
```

---

## §8. Workspace deltas (qué editar en `workspace/`)

Cada agente tiene un `workspace/` con archivos que el `ContextBuilder`
inyecta al system prompt. Cuando agregás una tool / cambias el tono /
agregás una skill, editás:

| Archivo | Cuándo editar |
|---|---|
| `workspace/TOOLS.md` | Agregaste tool nueva — describí cuándo llamarla y qué devuelve |
| `workspace/IDENTITY.md` | Cambio fundamental de quién es el agente |
| `workspace/SOUL.md` | Cambio de tone / actitud |
| `workspace/USER.md` | Cambio en el perfil del usuario esperado |
| `workspace/AGENTS.md` | Lista de otros agentes que existen (handoff context) |
| `workspace/skills/<name>/SKILL.md` | Nueva "skill" inline (procedimiento que el agente sigue) |
| `workspace/skills/<name>/bootstrap.md` | Hook que se carga al startup de la session |
| `workspace/skills/<name>/agent_end.md` | Hook que se carga al fin de la session |

---

## §9. Próximo paso

| Si vas a… | Leé después |
|---|---|
| Diagnosticar fallas R-rules | `references/deha-rules.md` |
| Entender patrones Temporal específicos (signal, debounce, CAN) | `references/temporal-patterns.md` |
| Ver el plugin chats completo como ejemplo | `examples/plugin-full-stack-agentic.md` |
| Diagnosticar fallas del architecture gate | `sections/08-tests-and-gates.md` |

---

**Fin sección 04.**
