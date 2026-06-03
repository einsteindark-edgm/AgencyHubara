"""Generador de fixtures sinteticas de history para replay tests (F6.2).

Levanta `WorkflowEnvironment.start_time_skipping`, registra los workflows reales
con activities **mockeadas** que comparten los mismos `@activity.defn(name=...)`
que las productivas, ejecuta un happy-path corto y persiste
`await handle.fetch_history()` (serializado a JSON via `to_json()`) en
`tests/fixtures/history_*_v1.json`.

Convenciones (ver `tests/fixtures/README.md`):
- Activity mocks: deterministicos (sin `time.now`, sin `uuid`, sin `random`).
- Naming: `history_<workflow>_v<N>.json`. Bumpear `N` cuando cambie la shape
  (orden de activities, signal/query signature, args serializables).

Como invocar:
    cd hubara_agency
    uv run python tests/fixtures/generate_fixtures.py
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any

# Garantizamos que `src/` sea importable cuando este script se invoca como
# `python tests/fixtures/generate_fixtures.py` (pytest ya lo hace via rootdir,
# pero la invocacion directa no).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from temporalio import activity  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

# Importar SOLO contracts y workflows productivos. Las activities productivas
# NO se importan para evitar I/O / RPC reales: las reemplazamos por mocks con
# el mismo `@activity.defn(name=...)`.
from exoclaw_temporal.config import (  # noqa: E402
    LLMChatInput,
    LLMConfig,
    LLMResponseData,
    SessionInput,
    WorkspaceConfig,
)
from src.plugins.chats.agent.sales.workflows.sales_session import (  # noqa: E402
    HubaraSalesSessionWorkflow,
)
from src.plugins.chats.agent.sales.contracts import (  # noqa: E402
    SalesSessionInput,
)
from src.plugins.chats.agent.remarketing.workflows.remarketing import (  # noqa: E402
    RemarketingSessionWorkflow,
)
from src.plugins.chats.agent.remarketing.contracts import (  # noqa: E402
    RemarketingSessionInput,
)

FIXTURES_DIR = Path(__file__).parent
# v2: PR-A del refactor DEHA workspace cambia la signature de
# `bootstrap_sales_session_activity(session_id: str)` ->
# `bootstrap_sales_session_activity(input: SalesSessionInput)` para hacer la
# activity extensible (futuros campos del DTO no rompen el shape).
SALES_FIXTURE = FIXTURES_DIR / "history_sales_session_v2.json"
# v3: PR-B del refactor DEHA workspace de remarketing elimina la activity
# `load_remarketing_brain_activity` del workflow body. La identidad / tono /
# catalogo del agente entran al system prompt via `ContextBuilder` desde
# `workspace/*.md` durante `build_prompt`. Como cambio la sequence de
# activities en la history, se bumpea fixture v2 -> v3 (history shape changed).
REMARKETING_FIXTURE = FIXTURES_DIR / "history_remarketing_session_v3.json"


# ---------------------------------------------------------------------------
# Activity mocks. Comparten `name=` con las productivas para que el Worker las
# matchee. Los nombres NO deben cambiar (cambiar = invalidar las fixtures).
# ---------------------------------------------------------------------------


@activity.defn(name="build_prompt")
async def mock_build_prompt(input: Any) -> list[dict[str, Any]]:
    """Mock determinista: retorna una lista de mensajes minima."""
    return [
        {"role": "system", "content": "fixture-system-prompt"},
        {"role": "user", "content": "fixture-user-message"},
    ]


@activity.defn(name="llm_chat")
async def mock_llm_chat(input: LLMChatInput) -> LLMResponseData:
    """Mock determinista: retorna respuesta de texto plano sin tool calls."""
    return LLMResponseData(
        content="fixture-llm-reply",
        finish_reason="stop",
        has_tool_calls=False,
        tool_calls=[],
        reasoning_content=None,
        thinking_blocks=None,
    )


@activity.defn(name="execute_tool")
async def mock_execute_tool(input: Any) -> str:
    """Mock determinista: nunca se llama en happy-path (no tool calls)."""
    return "fixture-tool-noop"


@activity.defn(name="record_turn")
async def mock_record_turn(input: Any) -> None:
    return None


@activity.defn(name="send_whatsapp_message_activity")
async def mock_send_whatsapp_message_activity(session_id: str, message: str) -> None:
    return None


@activity.defn(name="decide_ghosting_action")
async def mock_decide_ghosting_action() -> str:
    """Mock del prompt de ghosting (sales)."""
    return "fixture-ghost-trigger"


@activity.defn(name="start_or_signal_sales_workflow")
async def mock_start_or_signal_sales_workflow(decision: Any) -> None:
    return None


@activity.defn(name="schedule_remarketing_workflow")
async def mock_schedule_remarketing_workflow(decision: Any) -> None:
    return None


@activity.defn(name="claim_conversation_routing")
async def mock_claim_conversation_routing(workspace_path: str, new_route: str) -> None:
    return None


@activity.defn(name="read_workspace_memory_activity")
async def mock_read_workspace_memory_activity(session_id: str) -> str:
    return "fixture-memory-context"


@activity.defn(name="build_remarketing_trigger_activity")
async def mock_build_remarketing_trigger_activity(motivo: str, memory_context: str) -> str:
    return "fixture-remarketing-trigger"


@activity.defn(name="bootstrap_remarketing_session_activity")
async def mock_bootstrap_remarketing_session_activity(
    input: RemarketingSessionInput,
) -> SessionInput:
    """Mock determinista del bootstrap de Remarketing (PR-A workspace refactor).

    Recibe el `RemarketingSessionInput` completo (en lugar de
    `(session_id: str, motivo: str)`) para alinear con la signature productiva
    post-PR-A. PR-B sera el switchover en el cuerpo de la activity (consumir
    `input.runtime_workspace_path`).
    """
    return SessionInput(
        session_id=input.session_id,
        channel="whatsapp",
        chat_id=input.session_id,
        llm=LLMConfig(
            model="fixture-model",
            api_key="fixture-key",
            api_base="http://fixture",
            temperature=0.0,
            max_tokens=512,
            max_iterations=2,
        ),
        workspace=WorkspaceConfig(path="/tmp/fixture-workspace-remarketing"),
        tool_definitions_json="[]",
        turn_count=0,
    )


@activity.defn(name="bootstrap_sales_session_activity")
async def mock_bootstrap_sales_session_activity(input: SalesSessionInput) -> SessionInput:
    """Mock determinista del bootstrap de Sales (PR-A workspace refactor).

    Recibe el `SalesSessionInput` completo (en lugar de `session_id: str`) para
    alinear con la signature productiva post-PR-A.
    """
    return SessionInput(
        session_id=input.session_id,
        channel="whatsapp",
        chat_id=input.session_id,
        llm=LLMConfig(
            model="fixture-model",
            api_key="fixture-key",
            api_base="http://fixture",
            temperature=0.0,
            max_tokens=512,
            max_iterations=2,
        ),
        workspace=WorkspaceConfig(path="/tmp/fixture-workspace-sales"),
        tool_definitions_json="[]",
        turn_count=0,
    )


# HU-003 A7: run_agent_turn resuelve el episodio activo (detrás de
# workflow.patched("cost-attribution-episode-v1")) para atribuir el costo del
# LLM. Los fresh runs lo invocan, así que el worker de fixtures debe registrarlo.
@activity.defn(name="get_active_episode_id")
async def mock_get_active_episode_id(session_id: str) -> str:
    return "ep_001"


SALES_ACTIVITIES = [
    mock_build_prompt,
    mock_llm_chat,
    mock_execute_tool,
    mock_record_turn,
    mock_send_whatsapp_message_activity,
    mock_decide_ghosting_action,
    mock_bootstrap_sales_session_activity,
    mock_start_or_signal_sales_workflow,
    mock_schedule_remarketing_workflow,
    mock_get_active_episode_id,
]


REMARKETING_ACTIVITIES = [
    mock_build_prompt,
    mock_llm_chat,
    mock_execute_tool,
    mock_record_turn,
    mock_send_whatsapp_message_activity,
    mock_claim_conversation_routing,
    mock_read_workspace_memory_activity,
    mock_build_remarketing_trigger_activity,
    mock_bootstrap_remarketing_session_activity,
    # PR-B: `mock_load_remarketing_brain_activity` ya no se necesita — el
    # workflow no invoca la activity (la identidad vive en `workspace/*.md`).
    mock_start_or_signal_sales_workflow,
    mock_schedule_remarketing_workflow,
    mock_get_active_episode_id,
]


# ---------------------------------------------------------------------------
# Generadores
# ---------------------------------------------------------------------------


async def generate_sales_fixture(env: WorkflowEnvironment) -> None:
    """Sales happy-path corto (post-F7):
      1) El workflow se autobootstrapea (`bootstrap_sales_session_activity`).
         Es la PRIMERA activity de la history.
      2) Manda 1 signal `send_message`.
      3) El workflow procesa el turno (mock LLM responde texto plano).
      4) Espera `_IDLE_TIMEOUT` (1 min) -> ghost trigger -> turno final ->
         `_force_shutdown=True` -> return.
    """
    task_queue = f"fixture-sales-{uuid.uuid4()}"
    workflow_id = f"fixture-sales-wf-{uuid.uuid4()}"

    sales_input = SalesSessionInput(session_id="wa_fixture_sales")

    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[HubaraSalesSessionWorkflow],
        activities=SALES_ACTIVITIES,
    ):
        handle = await env.client.start_workflow(
            HubaraSalesSessionWorkflow.run,
            sales_input,
            id=workflow_id,
            task_queue=task_queue,
        )

        # Primer signal: el workflow procesa un turno.
        await handle.signal(
            HubaraSalesSessionWorkflow.send_message,
            args=["fixture-incoming-msg", None, None],
        )

        # `start_time_skipping` avanza el reloj automaticamente cuando el
        # workflow esta esperando. La idle timeout (1 min) disparara, el
        # workflow inyectara el ghost trigger y terminara con _force_shutdown.
        await handle.result()

        history = await handle.fetch_history()
        history_json = history.to_json()

    SALES_FIXTURE.write_text(history_json, encoding="utf-8")
    print(f"[OK] Sales fixture written: {SALES_FIXTURE} ({len(history_json)} bytes)")


async def generate_remarketing_fixture(env: WorkflowEnvironment) -> None:
    """Remarketing happy-path:
      1) El workflow se autoboostrappea (bootstrap_remarketing_session_activity).
      2) Llama claim_conversation_routing, read_workspace_memory_activity,
         build_remarketing_trigger_activity. PR-B: ya NO llama
         `load_remarketing_brain_activity` — la identidad / tono / catalogo
         viven en `workspace/*.md` y los lee `ContextBuilder` en `build_prompt`.
      3) Procesa el system trigger (1 turno LLM mockeado, sin tool calls).
      4) Espera idle (24h) -> claim_conversation_routing(VENTAS) -> return.
    """
    task_queue = f"fixture-remarketing-{uuid.uuid4()}"
    workflow_id = f"fixture-remarketing-wf-{uuid.uuid4()}"

    # PR-B: `runtime_workspace_path` debe estar presente. La produccion failfast
    # con RuntimeError si falta. El mock de `bootstrap_remarketing_session_activity`
    # no la consume (devuelve un SessionInput fijo), pero el DTO viaja con un
    # valor real para shape simetrico con la produccion.
    rem_input = RemarketingSessionInput(
        session_id="wa_fixture_remarketing",
        motivo="fixture-motivo",
        runtime_workspace_path="/tmp/fixture-workspace-remarketing",
    )

    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[RemarketingSessionWorkflow],
        activities=REMARKETING_ACTIVITIES,
    ):
        handle = await env.client.start_workflow(
            RemarketingSessionWorkflow.run,
            rem_input,
            id=workflow_id,
            task_queue=task_queue,
        )

        # No mandamos ningun signal: el workflow corre solo, procesa el trigger
        # interno y luego se apaga al disparar el idle timeout (time-skipping).
        await handle.result()

        history = await handle.fetch_history()
        history_json = history.to_json()

    REMARKETING_FIXTURE.write_text(history_json, encoding="utf-8")
    print(
        f"[OK] Remarketing fixture written: {REMARKETING_FIXTURE} ({len(history_json)} bytes)"
    )


async def main() -> None:
    print("Booting WorkflowEnvironment.start_time_skipping ...")
    env = await WorkflowEnvironment.start_time_skipping()
    try:
        await generate_sales_fixture(env)
        await generate_remarketing_fixture(env)
    finally:
        await env.shutdown()
    print("Done. Run pytest to verify replay.")


if __name__ == "__main__":
    asyncio.run(main())
