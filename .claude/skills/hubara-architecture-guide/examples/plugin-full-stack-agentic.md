# Example — Plugin full-stack agéntico (template D)

> **Plugin real del repo:** `chats` — agente WhatsApp sales + remarketing
> con dashboard SSE + handoff humano.
>
> **Use cuándo:** tu plugin necesita TODO — frontend, API REST,
> múltiples workers Temporal con LLM tool-calling, integración con
> servicios externos.

---

## §1. Layout completo del plugin `chats`

```
frontend_dashboard/src/plugins/chats/
├── plugin.yaml                                # manifest (ver §2)
└── frontend/
    ├── index.ts                               # barrel
    ├── ChatsSection.tsx                       # Page root
    └── features/
        ├── chats-inbox/                       # lista de sesiones
        ├── session-chat/                      # vista de chat individual
        ├── session-metadata/                  # panel inspector con metadata
        ├── memory-modal/                      # modal para ver workspace skills
        └── ... otras features internas

hubara_agency/src/plugins/chats/
├── __init__.py
├── api/
│   ├── __init__.py                            # docstring (no agrupa router)
│   ├── sales.py                               # POST /api/webhook (WhatsApp inbound)
│   ├── dashboard.py                           # GET /api/dashboard/sessions/* (SSE)
│   └── handoff.py                             # POST /api/dashboard/handoff/* (operador → humano)
├── agent/
│   ├── __init__.py
│   ├── sales/
│   │   ├── workflows/sales_session.py         # HubaraSalesSessionWorkflow
│   │   ├── activities/                        # bootstrap, send_typing, ...
│   │   ├── tools/                             # SearchProducts, TransferToSalesAgent, etc.
│   │   ├── composition.py                     # factories @lru_cache
│   │   ├── contracts.py                       # @dataclass frozen DTOs
│   │   ├── parsers.py                         # parsers WhatsApp inbound
│   │   ├── prompts.py                         # templates de prompt
│   │   └── workspace/                         # IDENTITY.md, SOUL.md, TOOLS.md, ...
│   └── remarketing/
│       ├── workflows/remarketing_session.py   # RemarketingSessionWorkflow
│       ├── activities/
│       ├── composition.py
│       ├── contracts.py
│       └── workspace/
└── workers/
    ├── __init__.py
    ├── sales.py                               # async def main() — registra tools de sales
    └── remarketing.py                         # async def main() — registra tools de remarketing

hubara_agency/k8s/aws-produccion/
├── worker-sales.yaml                          # 1 deployment K8s (replicas: 3)
└── worker-remarketing.yaml                    # 1 deployment K8s (replicas: 1)
```

**Tamaño:** ~50-80 archivos Python + ~30-50 archivos TS. Es el plugin
más complejo del repo.

---

## §2. Manifest (`plugin.yaml`) real

```yaml
id: chats
version: 0.1.0
display_name: Chats
description: Conversaciones WhatsApp con agente Temporal (sales + remarketing) + dashboard SSE + handoff humano.

depends_on: []

frontend:
  entry: ./frontend
  contributes:
    sections:
      - { key: chat, label: Chats, order: 1, icon: chat }
    sidebar:
      - { route: /chats, label: Chats, icon: chat }

api:
  python_module: src.plugins.chats.api           # ancla; IGNORADA por legacy_routers
  prefix: /api/chats                              # IGNORADO
  tags: [Chats]                                   # IGNORADO
  legacy_routers:                                 # ← GANA
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
        replicas: 3                       # high-throughput
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
          CATALOG_SNAPSHOT_DIR: /app/hubara_vault/catalog
          CATALOG_MAX_AGE_MINUTES: "30"
          DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY}
          WHATSAPP_PHONE_NUMBER_ID: ${WHATSAPP_PHONE_NUMBER_ID}
          WHATSAPP_ACCESS_TOKEN: ${WHATSAPP_ACCESS_TOKEN}
          WHATSAPP_VERIFY_TOKEN: ${WHATSAPP_VERIFY_TOKEN}
        volumes:
          - hubara-vault-local:/app/hubara_vault
        depends_on:
          - temporal
          - litellm

    - name: remarketing
      module: src.plugins.chats.workers.remarketing
      task_queue: queue-remarketing-agent
      deployment:
        replicas: 1                       # low-throughput, long-lived
        cpu_request: 250m
        memory_request: 384Mi
        env_secrets:
          # (mismas env_secrets que sales)
      compose:
        # (similar a sales)

wiring_intents:
  filesystem_volumes:
    - hubara-vault                   # JSONL message store + workspaces canónicos
  env_vars_required:
    - DEEPSEEK_API_KEY
    - WHATSAPP_PHONE_NUMBER_ID
    - WHATSAPP_ACCESS_TOKEN
    - WHATSAPP_VERIFY_TOKEN
    - TEMPORAL_URL
    - WORKSPACE_VAULT_DIR
```

**Decisiones clave:**

- **3 routers heterogéneos** (`legacy_routers`) — el patrón del manifest
  para casos donde `api/__init__.py` no expone un router unificado.
- **2 workers en queues distintas** — aislamiento operacional + de
  seguridad (el LLM de sales no puede invocar tools de remarketing).
- **`replicas: 3` para sales / `replicas: 1` para remarketing** —
  sales recibe alto throughput; remarketing son workflows que duermen días.
- **`depends_on: [temporal, litellm]`** — sales necesita LLM proxy.

---

## §3. API — los 3 routers

### §3.1 `api/sales.py` — webhook WhatsApp

```python
# canonical — api/sales.py (estructura)
from fastapi import APIRouter, BackgroundTasks, Request, HTTPException

router = APIRouter()

@router.get("/webhook")
async def verify_webhook(request: Request) -> int:
    """WhatsApp Meta verification challenge."""
    # ... verify hub.mode, hub.verify_token, return challenge

@router.post("/webhook")
async def handle_whatsapp_webhook(
    request: Request,
    background: BackgroundTasks,
) -> dict:
    """Webhook inbound — ack inmediato + processing en background."""
    body = await request.json()
    background.add_task(_ingest_inbound_message, body)
    return {"status": "ack"}


async def _ingest_inbound_message(body: dict) -> None:
    parsed = parse_whatsapp_inbound(body)
    if not parsed:
        return
    use_case = build_ingest_inbound_message_use_case()
    await use_case.execute(parsed)
```

### §3.2 `api/dashboard.py` — SSE del dashboard

```python
# canonical — api/dashboard.py (estructura)
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter()

@router.get("/sessions/stream")
async def stream_sessions():
    async def event_gen():
        async for session_update in iter_session_changes():
            yield f"data: {json.dumps(session_update)}\n\n"
    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    # leer metadata + history del vault
    pass
```

### §3.3 `api/handoff.py` — operador toma control

```python
# canonical — api/handoff.py (estructura)
@router.post("/handoff/{session_id}/take")
async def operator_take(session_id: str, payload: dict) -> dict:
    # Signal al workflow sales para que escale a humano
    pass

@router.post("/handoff/{session_id}/release")
async def operator_release(session_id: str) -> dict:
    # Re-activar al agente
    pass
```

---

## §4. Workflow Sales (`agent/sales/workflows/sales_session.py`)

```python
# canonical — agent/sales/workflows/sales_session.py (estructura)
from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.platform.workflow_helpers import (
        PendingMessage,
        coalesce_pending,
        run_agent_turn,
    )
    from src.plugins.chats.agent.sales.contracts import SalesSessionInput
    from src.platform.contracts import (
        TransferDecision,
        ScheduleRemarketingDecision,
        EscalationDecision,
    )
    from src.platform.temporal.retry_policies import _CONV_OPTIONS

_CONTINUE_AS_NEW_AFTER_TURNS = 50

@workflow.defn(name="HubaraSalesSessionWorkflow")
class HubaraSalesSessionWorkflow:
    def __init__(self):
        self._pending: list[PendingMessage] = []
        self._stop = False
        self._turn_count = 0

    @workflow.signal
    def send_message(self, msg: PendingMessage) -> None:
        self._pending.append(msg)

    @workflow.signal
    def force_shutdown(self) -> None:
        self._stop = True

    @workflow.run
    async def run(self, input: SalesSessionInput) -> None:
        session = await workflow.execute_activity(
            "bootstrap_sales_session", input, **_CONV_OPTIONS,
        )

        while not self._stop:
            await self._wait_debounced()
            if self._stop:
                break

            # Typing indicator gated por patched
            if workflow.patched("typing-indicator-v1"):
                await workflow.execute_activity(
                    "send_typing_indicator", session.session_id,
                    start_to_close_timeout=timedelta(seconds=5),
                    retry_policy=workflow.RetryPolicy(maximum_attempts=1),  # best-effort
                )

            msg = coalesce_pending(self._pending)
            self._pending = []

            turn = await run_agent_turn(session, msg)

            # Process tool decisions ANTES del send (para no enviar respuesta
            # cuando se va a escalar a humano)
            if turn.escalation_decision:
                # Enviar mensaje de despedida + force_shutdown
                if turn.final_content:
                    await workflow.execute_activity(
                        "send_whatsapp_message",
                        session.session_id, turn.final_content,
                        **_CONV_OPTIONS,
                    )
                self._stop = True
                continue

            if turn.transfer_decision:
                # No es nuestro turno; el cliente queda con otro agente
                continue

            if turn.schedule_remarketing:
                await workflow.execute_activity(
                    "schedule_remarketing_workflow",
                    turn.schedule_remarketing, **_CONV_OPTIONS,
                )

            # Send final content al cliente
            await workflow.execute_activity(
                "send_whatsapp_message",
                session.session_id, turn.final_content,
                **_CONV_OPTIONS,
            )

            # Persist DESPUÉS del send (si send falla, no contamina JSONL)
            await workflow.execute_activity(
                "persist_assistant_message",
                session.session_id, turn.final_content,
                **_CONV_OPTIONS,
            )

            self._turn_count += 1
            if self._turn_count >= _CONTINUE_AS_NEW_AFTER_TURNS:
                workflow.continue_as_new(input)

    async def _wait_debounced(self) -> None:
        # Patrón debounce 1.5s silencio / 12s cap (ver references/temporal-patterns.md)
        # ... (omitido por brevedad)
        pass
```

---

## §5. Tools del agente sales (`agent/sales/tools/`)

### §5.1 SearchProducts (tool sin decisión)

```python
# canonical — agent/sales/tools/search_products.py
import json
from pathlib import Path
from exoclaw.agent.tools import ToolBase, ToolContext

class SearchProductsTool(ToolBase):
    name = "search_products"
    description = "Busca productos del catálogo por keyword."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    def __init__(self, workspace_path: str, catalog_dir: str):
        self._workspace = workspace_path
        self._catalog_dir = catalog_dir

    async def execute_with_context(self, ctx: ToolContext, **kwargs) -> str:
        query = kwargs["query"].lower()
        manifest = json.loads((Path(self._catalog_dir) / "manifest.json").read_text())
        # ... query manifest, devolver top-3 products ...
        return json.dumps({"status": "ok", "results": top_3})
```

### §5.2 TransferToSalesAgent (tool que emite decisión)

```python
# canonical — agent/sales/tools/transfer.py (ya existe en platform actually)
class TransferToSalesAgentTool(ToolBase):
    name = "transfer_to_sales_agent"
    description = "Transfiere la conversación a otro agente vendedor."
    parameters = {
        "type": "object",
        "properties": {
            "target_route": {"type": "string", "enum": ["ventas", "remarketing"]},
            "summary": {"type": "string"},
        },
        "required": ["target_route"],
    }

    async def execute_with_context(self, ctx: ToolContext, **kwargs) -> str:
        return json.dumps({
            "status": "ok",
            "transfer_decision": {
                "session_id": ctx.session_id,
                "target_route": kwargs["target_route"],
                "summary": kwargs.get("summary"),
            },
        })
```

---

## §6. Worker Sales (`workers/sales.py`)

```python
# canonical — workers/sales.py
import asyncio
import os
from pathlib import Path

from loguru import logger
from temporalio.worker import Worker

from src.platform.plugin_manifest import get_task_queue
from src.platform.logging import setup_logging
from src.platform.temporal.client import get_temporal_client
from src.platform.tool_extensions import register_tool_extension
from src.platform.tools.transfer_to_sales import TransferToSalesAgentTool
from src.platform.tools.escalate_to_human import EscalateToHumanTool

# Sales-specific tools
from src.plugins.chats.agent.sales.tools.search_products import SearchProductsTool
from src.plugins.chats.agent.sales.tools.checkout import VerifyCheckoutTool
from src.plugins.chats.agent.sales.tools.tag import ManageConversationTagTool

# Workflows + activities
from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow
from src.plugins.chats.agent.sales.activities import (
    bootstrap_sales_session,
    send_typing_indicator,
    send_whatsapp_message,
    persist_assistant_message,
    start_or_signal_sales_workflow,
    schedule_remarketing_workflow,
)
from src.platform.temporal.activities import execute_tool
from src.platform.whatsapp.activities import (...)
from src.platform.session_history.activities import (...)


setup_logging()


async def main() -> None:
    logger.info("Conectando worker sales a Temporal...")
    client = await get_temporal_client()
    task_queue = get_task_queue("chats", "sales")

    # Registrar tools del agente sales (incluye las shared de platform/tools/)
    catalog_dir = os.environ.get("CATALOG_SNAPSHOT_DIR", "/app/hubara_vault/catalog")

    register_tool_extension(
        "chats.search_products",
        lambda workspace_path: SearchProductsTool(
            workspace_path=str(workspace_path),
            catalog_dir=catalog_dir,
        ),
    )
    register_tool_extension(
        "chats.verify_checkout",
        lambda workspace_path: VerifyCheckoutTool(workspace_path=str(workspace_path)),
    )
    register_tool_extension(
        "chats.manage_conversation_tag",
        lambda workspace_path: ManageConversationTagTool(workspace_path=str(workspace_path)),
    )
    register_tool_extension(
        "chats.transfer_to_sales_agent",
        lambda workspace_path: TransferToSalesAgentTool(workspace_path=str(workspace_path)),
    )
    register_tool_extension(
        "chats.escalate_to_human",
        lambda workspace_path: EscalateToHumanTool(workspace_path=str(workspace_path)),
    )

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[HubaraSalesSessionWorkflow],
        activities=[
            bootstrap_sales_session,
            send_typing_indicator,
            send_whatsapp_message,
            persist_assistant_message,
            start_or_signal_sales_workflow,
            schedule_remarketing_workflow,
            execute_tool,
            # ... otras activities de WhatsApp + session_history ...
        ],
    )
    logger.info("Sales worker up. Queue: '{}'", task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
```

**Notar:**

- Importa de `src.platform.tools.*` (cross-plugin shared OK via platform).
- Registra **solo** las tools del agente sales (no las de remarketing).
- Mismo `execute_tool` activity para todas las tools — el registry pattern
  lo resuelve sin acoplamiento.

---

## §7. Workspace del agente sales (`agent/sales/workspace/`)

```
agent/sales/workspace/
├── IDENTITY.md          # quién es el agente sales
├── SOUL.md              # tono, actitud
├── USER.md              # perfil del usuario esperado
├── TOOLS.md             # cuándo llamar cada tool (lee el LLM)
├── AGENTS.md            # otros agentes que existen (handoff context)
└── skills/
    └── greeting/
        ├── SKILL.md
        ├── bootstrap.md
        └── agent_end.md
```

Estos archivos se inyectan al system prompt via `ContextBuilder` dentro
de `build_prompt` activity. Editar los `.md` cambia el comportamiento
del agente sin tocar código.

---

## §8. Frontend del plugin (`frontend/`)

```typescript
// canonical — plugins/chats/frontend/ChatsSection.tsx
import { useState } from "react";
import { ChatsInbox } from "./features/chats-inbox";
import { SessionChat } from "./features/session-chat";
import { SessionMetadata } from "./features/session-metadata";

export interface ChatsSectionProps {
  showSidebar: boolean;
  showInspector: boolean;
}

export function ChatsSection({ showSidebar, showInspector }: ChatsSectionProps) {
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

  return (
    <>
      {showSidebar && (
        <aside className="sidebar">
          <ChatsInbox
            selectedSessionId={selectedSessionId}
            onSelect={setSelectedSessionId}
          />
        </aside>
      )}
      <main>
        {selectedSessionId ? (
          <SessionChat sessionId={selectedSessionId} />
        ) : (
          <p>Selecciona una conversación</p>
        )}
      </main>
      {showInspector && (
        <aside className="inspector">
          {selectedSessionId && <SessionMetadata sessionId={selectedSessionId} />}
        </aside>
      )}
    </>
  );
}

export default ChatsSection;
```

**Notar:**

- 3-panel layout (sidebar inbox + chat principal + inspector metadata).
- Estado local: `selectedSessionId` lifted hasta acá.
- Cross-feature dentro del plugin OK (`ChatsInbox`, `SessionChat`, `SessionMetadata`).

---

## §9. Tests (overview)

```
hubara_agency/tests/plugins/chats/
├── api/
│   ├── test_sales_webhook.py
│   ├── test_dashboard_sse.py
│   └── test_handoff.py
├── agent/
│   ├── sales/
│   │   ├── workflows/
│   │   │   ├── test_sales_session.py
│   │   │   ├── test_sales_session_replay.py
│   │   │   └── fixtures/sales_session_v3.json
│   │   ├── activities/
│   │   │   ├── test_bootstrap_sales_session.py
│   │   │   └── ...
│   │   ├── tools/
│   │   │   ├── test_search_products.py
│   │   │   ├── test_transfer_to_sales.py
│   │   │   └── ...
│   │   └── workspace/
│   │       └── test_workspace_assembled.py
│   └── remarketing/
│       └── ...
├── functional/
│   ├── test_sales_e2e.py             # user msg → LLM tool call → reply
│   └── test_remarketing_e2e.py
└── workers/
    ├── test_sales_worker_smoke.py
    └── test_remarketing_worker_smoke.py
```

---

## §10. Verificación E2E manual

```bash
# Stack completo
docker compose -f hubara_agency/docker-compose.local.yml up -d
# → db + temporal + litellm + hubara-api + 2 workers + frontend

# Simular webhook WhatsApp
curl -X POST http://localhost:8000/api/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "object": "whatsapp_business_account",
    "entry": [{
      "changes": [{
        "value": {
          "messages": [{
            "from": "+5491150000000",
            "text": {"body": "Hola, quiero productos"}
          }]
        }
      }]
    }]
  }'

# Ver workflow en Temporal UI
open http://localhost:8088

# Ver dashboard
open http://localhost:5173
# → tab "Chats" → ver inbox con la sesión nueva
```

---

## §11. Por qué este plugin es el más complejo

| Dimensión | `chats` | Promedio otros plugins |
|---|---|---|
| Archivos Python | ~80 | ~5-15 |
| Archivos TS | ~40 | ~5-10 |
| Workers | 2 | 0-1 |
| Routers FastAPI | 3 | 0-1 |
| Tools LLM | ~8 | 0 |
| Tests | ~50 | ~5-15 |
| K8s manifests | 2 | 0-1 |
| Env vars / secrets | 5+ | 0-2 |
| Integraciones externas | WhatsApp + DeepSeek + Medusa (snapshot) | 0-1 |

`chats` es el caso real que valida que el plugin system aguanta
complejidad. Si tu plugin es más simple, prefiere templates A/B/C.

---

## §12. Pros y limitaciones del template D

| Pro | Limitación |
|---|---|
| Soporta TODO (frontend + API + workers + LLM + integraciones) | Setup largo (~1-2 días iniciales) |
| Aislamiento por queue: deploys independientes | Tests E2E lentos |
| Manifest expresivo (env_secrets, deployment hints, compose env) | Manifests largos (>100 líneas) |
| Tools modulares con DI invertida | Workspace files (`.md`) requieren mantenimiento separado |

---

**Fin example. Este es el caso canónico real más completo del repo.**
