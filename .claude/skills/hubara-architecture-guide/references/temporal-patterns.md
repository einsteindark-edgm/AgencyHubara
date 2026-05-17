# Reference — Patrones Temporal en AgencyHubara

> **Cuándo leer esto:** vas a escribir / modificar workflows o activities,
> y necesitás el detalle de patrones específicos (signal, debounce,
> continue-as-new, patched).
> **Pre-requisito:** `sections/04-backend-agents.md` para contexto general.
> **Reference complementario:** `references/deha-rules.md` (R-DET, R-JSON).

---

## §1. Signal-driven workflows

### §1.1 Estructura

```python
@workflow.defn(name="MyWorkflow")
class MyWorkflow:
    def __init__(self):
        self._pending: list[PendingMessage] = []
        self._stop = False

    @workflow.signal
    def send_message(self, msg: PendingMessage) -> None:
        # Signal handler — append-only, NUNCA llama execute_activity
        # Devolve None (es void)
        self._pending.append(msg)

    @workflow.signal
    def stop(self) -> None:
        self._stop = True

    @workflow.query
    def get_status(self) -> dict:
        # Query handler — pure read, NUNCA llama execute_activity, NUNCA muta state
        return {"pending_count": len(self._pending), "stopped": self._stop}

    @workflow.run
    async def run(self, input: MyWorkflowInput) -> None:
        # Loop principal
        while not self._stop:
            await self._wait_debounced()
            msg = coalesce_pending(self._pending)
            self._pending = []
            await self._process_turn(msg)
```

### §1.2 Reglas para signal handlers

| ✅ Permitido | ❌ Prohibido |
|---|---|
| Mutar state interno del workflow (`self._pending.append(...)`) | Llamar `workflow.execute_activity(...)` |
| Validar el payload (`if not msg.message: return`) | Hacer I/O (network, disk) |
| Setear flags (`self._stop = True`) | `await asyncio.sleep(...)` (usar `workflow.sleep` en `run`, no en signals) |
| Return None | Return value (los signals son void) |

### §1.3 Reglas para query handlers

| ✅ Permitido | ❌ Prohibido |
|---|---|
| Leer state interno | Mutar state |
| Hacer cómputo determinístico sobre el state | Llamar `workflow.execute_activity(...)` |
| Devolver un dict / dataclass JSON-serializable | Devolver `None` (queries necesitan respuesta) |

---

## §2. Debounce 1.5s silencio / 12s cap (replay-safe)

El patrón canónico para coalescing de mensajes rápidos del cliente. Vive
en cada workflow que recibe signals de mensajes (`HubaraSalesSessionWorkflow`,
etc.).

```python
from datetime import timedelta
from temporalio import workflow

_DEBOUNCE_SILENCE = timedelta(seconds=1.5)
_DEBOUNCE_CAP = timedelta(seconds=12.0)

async def _wait_debounced(self) -> None:
    """Espera 1.5s de silencio entre mensajes (cap duro a 12s).

    Replay-safe: usa workflow.wait_condition + workflow.sleep (NO asyncio.sleep).
    """
    start = workflow.now()
    last_count = 0
    while True:
        # Esperar al menos un mensaje (cap absoluto al CAP)
        await workflow.wait_condition(
            lambda: len(self._pending) > 0 or self._stop,
            timeout=_DEBOUNCE_CAP,
        )
        if self._stop:
            return

        if len(self._pending) == last_count:
            # No llegaron mensajes nuevos en _DEBOUNCE_SILENCE — procesar
            return

        last_count = len(self._pending)

        # Espera adicional silenciosa
        elapsed = workflow.now() - start
        if elapsed >= _DEBOUNCE_CAP:
            return

        await workflow.sleep(_DEBOUNCE_SILENCE)
```

### §2.1 Por qué replay-safe

`workflow.wait_condition` + `workflow.sleep` son determinísticos — el
sandbox los registra en el history. Replay regenera el mismo flow.

Si usás `asyncio.sleep(1.5)` en su lugar, el replay produce diferente
resultado (sleep real vs sleep mocked) → `NonDeterminismError`.

---

## §3. Coalesce pending messages

Ya descrito en `sections/02-backend-platform.md §5`. Resumen:

```python
from src.platform.workflow_helpers import coalesce_pending

msg = coalesce_pending(self._pending)
self._pending = []
# msg es UN solo PendingMessage que combina los N pending del debounce window
```

---

## §4. Continue-as-new (history pruning)

```python
_CONTINUE_AS_NEW_AFTER_TURNS = 50

@workflow.run
async def run(self, input: MyWorkflowInput) -> None:
    while not self._stop:
        await self._wait_debounced()
        msg = coalesce_pending(self._pending)
        self._pending = []
        await self._process_turn(msg)
        self._turn_count += 1

        if self._turn_count >= _CONTINUE_AS_NEW_AFTER_TURNS:
            # Transfiere state a nuevo workflow run con history limpia
            workflow.continue_as_new(input)
            # NUNCA llega acá — continue_as_new es terminal
```

### §4.1 Por qué

Temporal limita history a ~50MB / workflow run. Una sesión de chat larga
(>50 turnos) puede chocar el límite con `WorkflowHistoryLimit`. CAN reseta
el run con el state nuevo (input passes through).

### §4.2 Caveat — state mutable se pierde

`continue_as_new(input)` solo pasa el `input` original al nuevo run. **No
pasa `self._pending` ni `self._turn_count` ni nada más.** Si necesitás
preservar state, inclúyelo en el `input`:

```python
@dataclass(frozen=True)
class MyWorkflowInput:
    session_id: str
    # Persistencia trans-CAN:
    resume_from_turn: int = 0
    resume_history_count: int = 0
```

---

## §5. `workflow.patched()` para features gated

Cuando agregás una activity nueva o cambiás el flow de un workflow que
tiene runs in-flight, usá `patched()` para evitar `NonDeterminismError`
en replay.

```python
@workflow.run
async def run(self, input):
    # ... resto del workflow ...

    if workflow.patched("typing-indicator-v1"):
        # SOLO los workflows nuevos (creados post-deploy) ejecutan esto.
        # Los workflows que ya estaban running antes del deploy NO ejecutan.
        await workflow.execute_activity(
            send_typing_indicator_activity,
            input,
            **_TYPING_OPTIONS,
        )

    # ... más código ...
```

### §5.1 Reglas

- El `patch_id` (e.g. `"typing-indicator-v1"`) **debe ser único** y
  documentar QUÉ feature gated.
- Después de unos meses cuando todos los workflows in-flight pre-deploy
  hayan terminado, podés **deprecar** el patch — pero NO removerlo del
  código sin un PR explícito de cleanup.
- Si removés el patch y un workflow viejo todavía está running, replay
  falla.

### §5.2 Patches activos en el repo

Pueden cambiar; verificá con `grep -rEn 'workflow\.patched' src/`:

- `"typing-indicator-v1"` — agregado en PR de typing indicator
- (otros según evolución)

---

## §6. Retry policies — usar los presets (`_CONV_OPTIONS`, `_LLM_OPTIONS`, `_TOOL_OPTIONS`)

Vienen de `src/platform/temporal/retry_policies.py`:

```python
from src.platform.temporal.retry_policies import (
    _CONV_OPTIONS,    # Conversation I/O (build_prompt, record_turn, FS, send_whatsapp)
    _LLM_OPTIONS,     # LLM calls (llm_chat)
    _TOOL_OPTIONS,    # Tool execution (execute_tool)
)

await workflow.execute_activity(build_prompt, input, **_CONV_OPTIONS)
await workflow.execute_activity(llm_chat, input, **_LLM_OPTIONS)
await workflow.execute_activity(execute_tool, input, **_TOOL_OPTIONS)
```

### §6.1 NO hardcodear timeouts

```python
# ❌ INVÁLIDO — hardcoded inline
await workflow.execute_activity(
    my_activity,
    input,
    start_to_close_timeout=timedelta(seconds=30),
    retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3),
)


# ✅ VÁLIDO — usar preset semántico
await workflow.execute_activity(my_activity, input, **_TOOL_OPTIONS)


# ✅ TAMBIÉN VÁLIDO — agregar preset nuevo a retry_policies.py si necesitás otro tuning
# (en PR separado, no en feature task)
_DISPATCHER_OPTIONS = {
    "start_to_close_timeout": timedelta(seconds=10),
    "retry_policy": RetryPolicy(maximum_attempts=2),
}
```

---

## §7. Tool decisions vs actions (ADR-001)

### §7.1 El problema

Una tool que necesita **disparar otro workflow** (e.g. transfer a otro
agente) NO puede importar `temporal_client` y hacer `start_workflow`:
- Viola R-DIP (tools no importan `temporalio.client`).
- No es testable sin Temporal real.

### §7.2 La solución: decisión + activity dispatcher

**Tool emite decision DTO** en el JSON envelope:

```python
class TransferToSalesAgentTool(ToolBase):
    async def execute_with_context(self, ctx, **kwargs):
        return json.dumps({
            "status": "ok",
            "transfer_decision": {
                "session_id": ctx.session_id,
                "target_route": "ventas",
                "summary": kwargs.get("summary"),
            },
        })
```

**Workflow parsea decision** (vía `run_agent_turn` que ya lo hace
automáticamente):

```python
turn = await run_agent_turn(session, msg)
if turn.transfer_decision:
    await workflow.execute_activity(
        start_or_signal_sales_workflow_activity,
        turn.transfer_decision,
        **_DISPATCHER_OPTIONS,
    )
```

**Activity dispatcher** (única con I/O de Temporal client):

```python
@activity.defn(name="start_or_signal_sales_workflow")
async def start_or_signal_sales_workflow_activity(decision: TransferDecision) -> None:
    client = await get_temporal_client()
    workflow_id = f"hubara-sales-{decision.session_id}"
    try:
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(HubaraSalesSessionWorkflow.send_message, ...)
    except WorkflowNotFoundError:
        await client.start_workflow(
            HubaraSalesSessionWorkflow.run,
            SalesSessionInput(session_id=decision.session_id, ...),
            id=workflow_id,
            task_queue=get_task_queue("chats", "sales"),
        )
```

### §7.3 Decisiones actuales

| DTO | Tool que la emite | Workflow consume → activity dispatcher |
|---|---|---|
| `TransferDecision` | `TransferToSalesAgentTool` | `start_or_signal_sales_workflow_activity` |
| `ScheduleRemarketingDecision` | `ManageConversationTagTool` (cuando tag = INTERESADO) | `schedule_remarketing_workflow_activity` |
| `EscalationDecision` | `EscalateToHumanTool` | Workflow termina (sin dispatcher) |

---

## §8. Activity timing-out: cuándo extender timeout vs heartbeat

| Problema | Solución |
|---|---|
| Activity tarda 5s típico, 30s peor caso | `_LLM_OPTIONS` (start_to_close=120s) + `@with_heartbeat(every=10)` |
| Activity tarda 2s típico, raro tarda más | `_TOOL_OPTIONS` (start_to_close=60s) — sin heartbeat OK |
| Activity tarda hours (e.g. esperar webhook externo) | Considerar **child workflow** con `await workflow.execute_child_workflow(...)` en lugar de activity bloqueante |
| Activity tarda days/weeks (e.g. esperar a que el cliente conteste) | Patrón **signal-driven workflow**: el workflow espera signal con `workflow.wait_condition(...)` |

---

## §9. Replay tests

Vive en `tests/plugins/<id>/workflows/test_<name>_replay.py`. Carga un
JSON history fixture y verifica que el workflow code actual produce el
mismo resultado.

### §9.1 Cuándo bumpear la fixture version

Si cambiás el workflow signature (signal arg, init field, run input):

1. Run el workflow contra una sesión real, capturá el history JSON.
2. Salvalo como `tests/plugins/<id>/fixtures/<workflow>_v<N+1>.json`.
3. Update el replay test para usar `v<N+1>`.
4. **Mantené `v<N>`** funcional — debe seguir replayendo OK (el workflow
   code debe ser backward-compatible para los runs in-flight).

### §9.2 Si el replay falla con `NonDeterminismError`

1. Diagnosticá QUÉ cambió: nueva activity sin patched, signal signature cambió, etc.
2. Si fue intencional: agregá `workflow.patched("feature-vN")` gating.
3. Si fue accidental: revertí el cambio o ajustá para preservar
   compatibilidad.

---

## §10. Cheat sheet — qué activity timeout usar

| Tipo de activity | Preset | Heartbeat? |
|---|---|---|
| `build_prompt`, `record_turn`, FS reads/writes | `_CONV_OPTIONS` | NO |
| `llm_chat` | `_LLM_OPTIONS` | SÍ |
| `execute_tool` (mixed I/O) | `_TOOL_OPTIONS` | Depende — si la tool hace I/O pesado, SÍ |
| `send_whatsapp_message` | `_CONV_OPTIONS` | NO |
| `send_typing_indicator` | `_CONV_OPTIONS` (con cap más bajo) | NO |
| `bootstrap_sales_session` | `_CONV_OPTIONS` | NO |
| Dispatcher (`start_or_signal_*`) | `_DISPATCHER_OPTIONS` (o `_CONV_OPTIONS`) | NO |
| Catalog sync (Medusa pull) | Custom timeout (~5min) + heartbeat | SÍ |

---

**Fin reference.**
