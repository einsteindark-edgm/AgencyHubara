"""Agent eval — maneja al agente de ventas REAL a través de un escenario y califica
SU respuesta (no transcripciones escritas a mano).

Esta es la pieza #4 del loop LLMOps: el offline eval (online) descubre una mala
conversación → se cura como un escenario (los turnos del CLIENTE) → este script
re-corre ese escenario contra el agente REAL → califica la respuesta del agente.
Un dev arregla el prompt/workspace, vuelve a correr esto, y verifica que los
scores subieron antes de ir a producción.

Faithfulness: usa el `SessionInput` que arma el bootstrap canónico
(`bootstrap_sales_session_activity`) — mismo workspace (.md reales), mismo modelo
(`build_default_llm_config`), mismas tools. El system prompt sale de `build_prompt`
(el real). El agente "habla" vía `llm_chat` (el real). Maneja el diálogo en memoria.

NO es el unit test bloqueante del PR (el agente es no-determinista + necesita el
stack litellm). Es un eval de integración / pre-deploy.

Uso:  cd hubara_agency && OTEL_SDK_DISABLED=true \\
        uv run --extra evals python scripts/agent_eval.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# hubara_agency en sys.path (el script corre desde scripts/, `src` no resuelve solo).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("WORKSPACE_VAULT_DIR", "/tmp/agent_eval_vault")

import asyncio  # noqa: E402

from temporalio.testing import ActivityEnvironment  # noqa: E402

# Escenario de demo = los turnos del CLIENTE de la conversación real que el offline
# eval marcó mal (wa_573125671604). Re-corremos esto contra el agente real.
_SCENARIO = ["Hola", "Catálogo", "Me interesa la primera"]


async def drive_agent(customer_turns: list[str]) -> list[str]:
    """Maneja al agente real a través de los turnos del cliente. Devuelve las
    respuestas (texto) del agente, una por turno."""
    from exoclaw_temporal.activities.conversation import BuildPromptInput, build_prompt
    from exoclaw_temporal.activities.llm import LLMChatInput, llm_chat

    from src.plugins.chats.agent.sales.activities.bootstrap_session import (
        bootstrap_sales_session_activity,
    )
    from src.plugins.chats.agent.sales.context import build_bogota_context_string
    from src.plugins.chats.agent.sales.contracts import SalesSessionInput

    env = ActivityEnvironment()
    sid = "wa_agent_eval_demo"
    session = await env.run(
        bootstrap_sales_session_activity,
        SalesSessionInput(session_id=sid, runtime_workspace_path=None),
    )
    bogota = build_bogota_context_string()

    # Turno 1: build_prompt arma el SYSTEM PROMPT REAL (workspace .md) + el 1er msg.
    messages = await env.run(
        build_prompt,
        BuildPromptInput(
            session_id=sid, message=customer_turns[0], channel="whatsapp",
            chat_id=sid, llm=session.llm, workspace=session.workspace,
            media=None, plugin_context=[bogota],
        ),
    )

    responses: list[str] = []
    for i, _cust in enumerate(customer_turns):
        if i > 0:
            messages.append({"role": "user", "content": customer_turns[i]})
        resp = await env.run(
            llm_chat,
            LLMChatInput(
                messages=messages, llm=session.llm,
                tool_definitions_json=session.tool_definitions_json, baggage=None,
            ),
        )
        content = (resp.content or "").strip() or "(turno solo-tool, sin texto al cliente)"
        responses.append(content)
        messages.append({"role": "assistant", "content": content})
    return responses


async def main() -> None:
    from src.plugins.chats.agent.sales_eval.evals import composition, metrics, reconstruct

    print(f"Manejando al agente REAL por {len(_SCENARIO)} turnos…\n")
    responses = await drive_agent(_SCENARIO)

    turns = []
    print("=== CONVERSACIÓN GENERADA POR EL AGENTE REAL ===")
    for cust, ag in zip(_SCENARIO, responses):
        print(f"  CLIENTE: {cust}")
        print(f"  ASESOR : {ag[:160]}")
        turns.append({"role": "user", "content": cust, "tools": []})
        turns.append({"role": "assistant", "content": ag, "tools": []})

    tc = reconstruct.build_conversational_test_case(turns, name="agent-eval-demo")
    judge = composition.get_judge()
    mlist = (metrics.deterministic_metrics()
             + [metrics.script_adherence_metric(judge), metrics.correct_handoff_metric(judge)])

    print("\n=== SCORES DEL AGENTE REAL ===")
    for m in mlist:
        await m.a_measure(tc)
        key = metrics.metric_key(m)
        flag = "✅" if m.is_successful() else "❌"
        print(f"  {flag} {key:22s} {float(m.score or 0):.2f}  — {(m.reason or '')[:90]}")


if __name__ == "__main__":
    asyncio.run(main())
