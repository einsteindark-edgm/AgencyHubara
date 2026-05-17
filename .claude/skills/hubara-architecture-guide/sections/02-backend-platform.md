# Sección 02 — Backend platform (la librería compartida cross-plugin)

> **Cuándo leer esto:** vas a editar/extender `hubara_agency/src/platform/`,
> o necesitás entender qué helpers podés reusar desde un plugin.
> **Pre-requisito:** `sections/01-general.md`.
> **Tamaño:** ~10 KB.

---

## §1. ¿Qué es `src/platform/` y qué no?

`src/platform/` es **librería compartida cross-plugin**. NO es un plugin.
NO tiene `plugin.yaml`. Vive permanentemente en `hubara_agency/src/platform/`.

**Regla R-DIP:** `platform/` NO importa `plugins/`. Si necesitás referenciar
algo de un plugin desde acá, **estás del lado equivocado** — la cosa
shared se mueve a `platform/` o se elimina la dependencia con DI.

---

## §2. Mapa del directorio (qué hay en cada subdir)

```
src/platform/
├── config.py                    # env vars globales (WORKSPACE_VAULT_DIR, WHATSAPP_*)
├── constants.py                 # SOLO cross-plugin: ROUTE_VENTAS / ROUTE_REMARKETING /
│                                #                    ROUTE_HUMANO / WHATSAPP_SESSION_PREFIX
│                                # NO agregar queues acá — viven en plugin.yaml
├── contracts.py                 # DTOs boundary cross-plugin (TransferDecision,
│                                #   ScheduleRemarketingDecision, EscalationDecision)
├── logging.py                   # setup_logging() loguru config compartida
├── plugin_manifest.py           # API de lectura de manifests (§3 abajo)
├── registries.py                # base registry pattern (ToolRegistry)
├── state.py                     # FilesystemMetadataStore (canonical impl)
├── tool_extensions.py           # DI invertida — registry global por dominio
├── workflow_helpers.py          # run_agent_turn + PendingMessage + coalesce_pending
├── temporal/
│   ├── client.py                # get_temporal_client() singleton helper
│   ├── dispatcher.py            # start_or_signal pattern
│   ├── activities.py            # execute_tool, claim_routing
│   ├── retry_policies.py        # _CONV_OPTIONS, _LLM_OPTIONS, _TOOL_OPTIONS
│   └── heartbeat.py             # @with_heartbeat decorator
├── whatsapp/
│   ├── client.py                # httpx wrapper (WhatsApp Cloud API)
│   └── activities.py            # send_whatsapp_message_activity, send_typing_indicator
├── session_history/
│   ├── store.py                 # JSONL append-only store
│   └── activities.py            # persist_assistant_message_activity, persist_user_event
├── catalog/
│   ├── port.py                  # CatalogPort protocol
│   ├── snapshot.py              # LocalSnapshotCatalog (reader)
│   └── checkout.py              # MedusaCheckoutVerification (auth)
├── medusa/
│   └── client.py                # MedusaAdminClient (httpx wrapper)
└── tools/
    ├── transfer_to_sales.py     # TransferToSalesAgentTool (cross-plugin)
    └── escalate_to_human.py     # EscalateToHumanTool (cross-plugin)
```

---

## §3. `plugin_manifest.py` — API de lectura del manifest (post-PR11)

Es la API canónica para resolver task queues, workers, y metadata del
manifest desde código. **Usar SIEMPRE esto en vez de hardcodear queues.**

```python
# hubara_agency/src/platform/plugin_manifest.py

from src.platform.plugin_manifest import (
    load_manifest,                 # full dict por plugin_id
    get_worker_spec,               # dict del worker (name, module, task_queue, ...)
    get_task_queue,                # str — usada por Worker(...) y start_workflow(...)
    enumerate_manifest_workers,    # [(plugin_id, worker_name, module_path), ...]
    # Excepciones específicas:
    ManifestNotFoundError,
    WorkerNotDeclaredError,
    TaskQueueMissingError,
)
```

### §3.1 Uso típico

```python
# En un worker (chats/workers/sales.py):
from src.platform.plugin_manifest import get_task_queue
task_queue = get_task_queue("chats", "sales")   # → "queue-sales-agent"
worker = Worker(client, task_queue=task_queue, ...)

# En un dispatcher que arranca un workflow:
from src.platform.plugin_manifest import get_task_queue
await client.start_workflow(
    MyWorkflow.run,
    args,
    id=f"hubara-{session_id}",
    task_queue=get_task_queue("chats", "sales"),
)

# Para enumerar TODOS los workers del repo (tests + meta-launcher):
from src.platform.plugin_manifest import enumerate_manifest_workers
for plugin_id, worker_name, module_path in enumerate_manifest_workers():
    print(f"{plugin_id}/{worker_name} → {module_path}")
```

### §3.2 Caching

`load_manifest` está `@cache`-ed por proceso (`functools.cache`). El
filesystem se toca una vez al startup; subsecuentes calls devuelven el
mismo dict. **No mutar el resultado** — comparte el cache entre callers.

### §3.3 Comportamiento de errores

| Excepción | Cuándo | Qué hacer |
|---|---|---|
| `ManifestNotFoundError` | Plugin no existe o manifest malformed | Fail-fast — error de configuración |
| `WorkerNotDeclaredError` | Plugin existe pero worker_name no listado en `agent.workers[]` | Fail-fast — error de configuración |
| `TaskQueueMissingError` | Worker existe pero no declara `task_queue` (debería ser imposible post-PR11 + test invariante) | Fail-fast con mensaje sugiriendo qué agregar al manifest |

Todas son fail-fast al startup. Mejor caer rápido que servir un endpoint
con queue mal resuelta.

---

## §4. `contracts.py` — DTOs boundary cross-plugin

DTOs **frozen** que cruzan `workflow.execute_activity` o `start_workflow`
entre dominios. R-JSON obliga `frozen=True` o **excepción declarada en
`R_JSON_FROZEN_EXEMPTIONS`** (ver §6 abajo).

### DTOs actuales

```python
from src.platform.contracts import (
    TransferDecision,           # emitida por TransferToSalesAgentTool
    ScheduleRemarketingDecision, # emitida por ManageConversationTagTool (INTERESADO)
    EscalationDecision,         # emitida por EscalateToHumanTool (frozen=True)
)
```

### Patrón de uso (ADR-001 — decisión vs acción)

Las **tools son funciones puras** que devuelven un decision DTO
serializado en el JSON envelope. El workflow lo parsea via
`_try_parse_decision_payload` (en `workflow_helpers.py`) y **el workflow**
ejecuta la activity dispatcher correspondiente:

```python
# Tool (pure, no I/O):
class TransferToSalesAgentTool(ToolBase):
    async def execute_with_context(self, ctx, **kwargs) -> str:
        return json.dumps({
            "status": "ok",
            "transfer_decision": {
                "session_id": ctx.session_id,
                "target_route": "ventas",
                "summary": kwargs.get("summary"),
            },
        })

# Workflow (parses decision, calls dispatcher activity):
turn_result = await run_agent_turn(session, msg)
if turn_result.transfer_decision:
    await workflow.execute_activity(
        start_or_signal_sales_workflow_activity,
        turn_result.transfer_decision,
        **_DISPATCHER_OPTIONS,
    )
```

**Por qué este patrón:** tools sin Temporal client son testables sin
`WorkflowEnvironment`. Las activities dispatcher son las únicas con I/O
de Temporal. Cumple R-DET + R-STATELESS.

---

## §5. `workflow_helpers.py` — `run_agent_turn` (el tool-loop)

El helper compartido que encapsula el loop `build_prompt → llm_chat →
execute_tool? → loop`. Vive en `platform/` porque sales y remarketing lo
comparten.

### Firma

```python
async def run_agent_turn(
    session: SessionInput,                          # del exoclaw_temporal.config
    msg: PendingMessage,                            # con .message, .media, .plugin_context
    fallback_plugin_context: list[str] | None = None,
) -> TurnResult:                                    # con .final_content + decision DTOs
```

### Estructura interna (resumida)

```
build_prompt(session, msg)               → messages: list[dict]
loop while iteration < session.llm.max_iterations:
    response = llm_chat(messages, llm, tool_definitions_json)
    if response.has_tool_calls:
        for tc in response.tool_calls:
            result = execute_tool(name=tc.name, params=tc.arguments, ctx=...)
            # parse decision_payload del JSON envelope:
            #   transfer_decision / schedule_remarketing / escalation_decision
            messages = [..., tool_message]
    else:
        final_content = response.content
        break
record_turn(session, new_messages)
return TurnResult(final_content, tools_used, transfer_decision, schedule_remarketing, escalation_decision)
```

### `PendingMessage` (signal payload)

```python
@dataclass
class PendingMessage:
    message: str
    media: list[str] | None = None
    plugin_context: list[str] | None = None         # datos volátiles del turno (A-MEM, snippets)
    is_handoff: bool = False                        # marker de Remarketing → Sales (no entra como user)
```

### `coalesce_pending(pending)` — debounce coalescing

Combina N mensajes pendientes en uno solo cuando el debounce termina.
Reglas en docstring de `coalesce_pending`:

- Mensajes `is_handoff=True` NO entran al rol "user" — su contenido se
  mueve a `plugin_context` como `[HANDOFF_REMARKETING]: ...`.
- Mensajes reales se concatenan con `\n` preservando orden.
- Si no hay mensajes reales pero sí handoff, usa el último handoff como
  mensaje principal (caso bootstrap).

---

## §6. `R_JSON_FROZEN_EXEMPTIONS` y `R_HEARTBEAT_EXEMPTIONS` — allow-lists

Viven en `hubara_agency/tests/architecture/conftest.py`. Son
**deliberadamente pequeñas** (~5 entries). Cada entry tiene un comment con
la razón.

**Cuándo agregar a `R_JSON_FROZEN_EXEMPTIONS`:**

- El dataclass tiene un motivo legítimo para no ser frozen (e.g.
  inheritance que requiere mutación interna; integración con código legacy).
- El motivo está documentado con un comment de ≥1 línea.
- El dataclass NO cruza boundary (sólo se usa internamente).

**NUNCA agregar para "fix temporal del test":** una excepción mal puesta
abre la puerta a violaciones reales. Si no podés justificarla, el código
está mal.

**Misma regla** para `R_HEARTBEAT_EXEMPTIONS` (activities con worst-case
<10s que el linter cree mayor).

---

## §7. `tool_extensions.py` — DI invertida (cómo platform "descubre" tools)

Pattern: `platform/` declara una `ToolRegistry` base. Cada plugin
**registra** sus tools al boot del worker. `platform/` consume el
registry sin importar plugins.

### Registrar desde un worker

```python
# src/plugins/chats/workers/sales.py

from src.platform.tool_extensions import register_tool_extension
from src.plugins.chats.agent.sales.tools.search_products import SearchProductsTool

async def main():
    # ... setup Temporal client ...
    register_tool_extension(
        "chats.search_products",
        lambda workspace_path: SearchProductsTool(workspace_path=str(workspace_path)),
    )
    # más tools del agente sales...
    await Worker(...).run()
```

### Consumir desde activities

```python
# src/platform/temporal/activities.py
from src.platform.tool_extensions import build_tool_registry

@activity.defn(name="execute_tool")
async def execute_tool(input: ExecuteToolInput) -> str:
    registry = build_tool_registry(workspace_path=input.workspace)
    return await registry.dispatch(input.name, input.params, ctx)
```

**Clave del patrón:** `platform/temporal/activities.py` NO importa
ninguna tool concreta. El registry se construye al primer call y vive
por el ciclo de vida del worker (rebuilt cada activity call para
cumplir R-STATELESS — el cache vive en `composition.py` del plugin,
no en `platform/`).

---

## §8. Cuándo escribir en `platform/` vs en un plugin

**Regla mnemotécnica:** si **2+ plugins** lo van a usar, va en
`platform/`. Si solo uno, va en el plugin.

| Caso | Va en | Por qué |
|---|---|---|
| Nueva tool LLM que SOLO usa el agente sales | `src/plugins/chats/agent/sales/tools/` | Específica del dominio chats |
| Nueva tool LLM compartida (e.g. "buscar producto" cross-agentes) | `src/platform/tools/` | Cross-plugin |
| Nuevo helper para llamar la WhatsApp API | `src/platform/whatsapp/` | Ya hay otros plugins potenciales |
| Nuevo DTO frozen que cruza un boundary específico de chats | `src/plugins/chats/agent/sales/contracts.py` | Específico |
| Nuevo DTO frozen cross-plugin | `src/platform/contracts.py` | Shared |
| Nuevo activity wrapper sobre `aiohttp` (genérico) | `src/platform/temporal/activities.py` o subdir nuevo | Genérico |
| Nueva env var que necesita 1 plugin | NO va en `platform/config.py` | Plugin-specific config va al worker module o composition |
| Nueva env var cross-plugin | `src/platform/config.py` | Shared |

**Anti-pattern:** "agregar a `platform/` por las dudas si después alguien
más lo necesita". NO. Va en el plugin. Si después otro plugin lo necesita,
**ahí** lo promovés a `platform/` con un PR específico (refactor explícito,
no especulación).

---

## §9. Templates de archivo (snippets canónicos para extender `platform/`)

### §9.1 Agregar activity nueva a `platform/temporal/`

```python
# canonical — src/platform/temporal/activities.py
from temporalio import activity
from src.platform.temporal.heartbeat import with_heartbeat

@activity.defn(name="my_new_activity")
@with_heartbeat(every=10)               # solo si worst-case >10s
async def my_new_activity(input: MyInput) -> MyOutput:
    # I/O OK acá — esto NO es workflow code
    result = await some_io(input.foo)
    return MyOutput(value=result)
```

### §9.2 Agregar DTO cross-plugin a `platform/contracts.py`

```python
# canonical — src/platform/contracts.py
from dataclasses import dataclass

@dataclass(frozen=True)                 # R-JSON obliga frozen
class MyCrossPluginDecision:
    session_id: str
    payload_key: str
    delay_seconds: int = 0              # default OK
```

### §9.3 Agregar tool shared a `platform/tools/`

```python
# canonical — src/platform/tools/my_shared_tool.py
from exoclaw.agent.tools import ToolBase, ToolContext

class MySharedTool(ToolBase):
    name = "my_shared_tool"
    description = "..."
    parameters = {"type": "object", "properties": {...}, "required": [...]}

    def __init__(self, workspace_path: str): ...

    async def execute_with_context(self, ctx: ToolContext, **kwargs) -> str:
        # devolver JSON envelope
        return json.dumps({"status": "ok", "result": ...})
```

---

## §10. Anti-patterns típicos en `platform/`

| # | Anti-pattern | Por qué mal | Qué hacer |
|---|---|---|---|
| 1 | `from src.plugins.chats.tools import X` | Viola R-DIP | Usar `tool_extensions` registry, o mover a `platform/tools/` |
| 2 | Hardcodear queue en `constants.py` | Conflict cross-plugin | Agregar al manifest, leer con `get_task_queue` |
| 3 | Module-level cache `_REGISTRY = {}` en activity | Viola R-STATELESS | Mover a `composition.py` del plugin (factory cacheada por workspace) |
| 4 | DTO sin `frozen=True` cruzando workflow↔activity | Viola R-JSON | `@dataclass(frozen=True)` |
| 5 | `import litellm` en `workflow_helpers.py` | Viola R-DET | El call HTTP vive en activity (`activities/llm.py`) |
| 6 | Activity sin `@with_heartbeat` que dura >10s | Viola R-HEARTBEAT | Decorar + ajustar timeout en retry policy |
| 7 | Agregar entry a `R_JSON_FROZEN_EXEMPTIONS` para silenciar test | Boquete arquitectural | Frozzear el dataclass o explicar justificadamente |

---

**Fin sección 02.** Para detalles de workflows / activities / tools
dentro de un plugin → `sections/04-backend-agents.md`.
