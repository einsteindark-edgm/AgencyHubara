"""Procedencia de `history_sales_tag_closure_prepatch_v1.json` (fixture CONGELADA).

NO es parte del flujo normal: la fixture ya está commiteada y NO se regenera.
Este script queda como registro reproducible de cómo se produjo.

REQUIERE el código del workflow ANTERIOR al gate `tag-ends-turn-v1`:

    git checkout 4052c29 -- hubara_agency/src/platform/workflow_helpers.py \\
                            hubara_agency/src/plugins/chats/agent/sales/workflows/sales_session.py
    cd hubara_agency && PYTHONPATH=. uv run python \\
        tests/fixtures/generate_tag_closure_prepatch_fixture.py /tmp/out.json

Ahí el tool-loop todavía pide un `llm_chat` después de `manage_conversation_tag`.
Los tool results que se graban YA traen el envelope nuevo (`tag_closure`): es la
ventana de versiones mezcladas de un deploy (activity con la tool nueva + workflow
task con el loop viejo), el único caso en que una history SIN el marker contiene
un envelope que el código nuevo cortaría. Por eso es sintética: una history real
de prod (tool results de forma vieja) jamás activa el corte y no protege el gate.

Corrido contra el código ACTUAL produciría la forma POST-patch (con marker y sin
el `llm_chat` extra) y la fixture dejaría de proteger nada: el script se niega.

Escenario (sesión sintética `wa_tagclosure`):
  turno 1 (cliente "No gracias…"): tag RECHAZO con `customer_message`
      → llm_chat FORZADO → texto final → send
  turno 2 (ghost, admin): tag RECHAZO sin `customer_message`
      → llm_chat FORZADO → "Etiqueta registrada." (no se envía) → shutdown
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

_HUB = Path(__file__).resolve().parents[2]
_GATE = "tag-ends-turn-v1"


def _refuse_if_the_gate_already_exists() -> None:
    helper = (_HUB / "src" / "platform" / "workflow_helpers.py").read_text(encoding="utf-8")
    if _GATE in helper:
        raise SystemExit(
            f"workflow_helpers.py ya contiene el gate `{_GATE}`: este código "
            "produce la forma POST-patch. La fixture congelada se generó desde "
            "el commit 4052c29 (ver el docstring). No se regenera."
        )


async def _generate(out: Path) -> None:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from exoclaw_temporal.config import LLMResponseData, ToolCallData

    from src.plugins.chats.agent.sales.contracts import SalesSessionInput
    from src.plugins.chats.agent.sales.workflows.sales_session import (
        HubaraSalesSessionWorkflow,
    )
    from tests.test_sales_workflow_debounce import (
        SALES_QUEUE,
        Tracker,
        _make_fake_activities,
    )

    session = "wa_tagclosure"
    farewell = "Con gusto, aquí quedo atenta por si más adelante te animas 🤍"

    def tag_call(call_id: str, *, customer_message: str | None) -> LLMResponseData:
        args = {"tag": "RECHAZO", "motivo": "El cliente dijo que ya no le interesa."}
        if customer_message is not None:
            args["customer_message"] = customer_message
        return LLMResponseData(
            # La despedida JUNTO a la tool call: la forma real del fallo (el
            # default-deny la descarta).
            content=farewell if customer_message is not None else "",
            finish_reason="tool_calls",
            has_tool_calls=True,
            tool_calls=[
                ToolCallData(id=call_id, name="manage_conversation_tag", arguments=args)
            ],
        )

    def final(text: str) -> LLMResponseData:
        return LLMResponseData(
            content=text, finish_reason="stop", has_tool_calls=False, tool_calls=[]
        )

    def envelope(*, customer_message: str | None) -> str:
        closure: dict = {"tag": "RECHAZO", "ends_turn": True}
        if customer_message is not None:
            closure["customer_message"] = customer_message
        return json.dumps({"message": "Hecho.", "tag_closure": closure}, ensure_ascii=False)

    class Sequenced(dict):
        def __getitem__(self, key: str) -> str:
            return super().__getitem__(key).pop(0)

    tracker = Tracker()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path="/fixture/workspace",
                llm_responses=[
                    tag_call("tag1", customer_message=farewell),
                    final(farewell),
                    tag_call("tag2", customer_message=None),
                    final("Etiqueta registrada."),
                ],
                tool_results=Sequenced(
                    {
                        "manage_conversation_tag": [
                            envelope(customer_message=farewell),
                            envelope(customer_message=None),
                        ]
                    }
                ),
                prior_history=[
                    {"role": "user", "content": "Hola"},
                    {"role": "assistant", "content": "¡Buenas! Bienvenido a *Hubara*..."},
                ],
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(
                    session_id=session, runtime_workspace_path="/fixture/workspace"
                ),
                id=f"session-{session}",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message,
                args=["No gracias, ya no me interesa", None, None],
            )
            await handle.result()
            history = await handle.fetch_history()

    # Saneado: la identidad del worker (pid@ip local) → `fixture-worker`.
    raw = re.sub(r'("identity":\s*")[^"]+(")', r"\1fixture-worker\2", history.to_json())
    assert _GATE not in raw, "la history trae el marker: NO es pre-patch"
    assert tracker.llm_calls == 4, f"forma inesperada: {tracker.llm_calls} llm_chat (esperaba 4)"
    out.write_text(raw, encoding="utf-8")
    print(f"escrita {out} ({len(raw.encode())} bytes; llm_calls={tracker.llm_calls})")


if __name__ == "__main__":
    _refuse_if_the_gate_already_exists()
    asyncio.run(_generate(Path(sys.argv[1])))
