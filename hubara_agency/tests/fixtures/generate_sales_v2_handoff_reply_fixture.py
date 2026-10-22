"""Procedencia de la fixture CONGELADA del gate `handoff-customer-reply-v1`.

NO es parte del flujo normal: la fixture ya está commiteada y NO se regenera.
Este script queda como registro reproducible de cómo se produjo.

Caso del 2026-10-16: remarketing le pasó el chat a Ventas solo con su resumen;
Ventas no tenía el «Si» del cliente y contestó NO_MESSAGE. El gate hace que el
V2, al leer un traspaso, lea también lo que escribió el cliente
(`read_handoff_reply`) y lo meta al turno. La historia es sintética (sesión
`wa_traspaso_v2`): Ventas arranca por el traspaso, el modelo contesta
NO_MESSAGE, tiene la ronda extra y responde; después, el cierre por
inactividad (turno admin) y fin.

  * `history_sales_v2_handoff_reply_v1.json` — control positivo con el código
    actual: marcador grabado y la activity nueva en la history. El control
    negativo (el código de antes) es `history_sales_v2_system_turns_preclock_v1.json`,
    que trae tres traspasos sin el marcador.

    cd hubara_agency && PYTHONPATH=. uv run python \\
        tests/fixtures/generate_sales_v2_handoff_reply_fixture.py /tmp/out_dir
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from tests.fixtures.generate_sales_v2_burst_prepatch_fixture import patch_markers

GATE = "handoff-customer-reply-v1"
SESSION = "wa_traspaso_v2"
SUMMARY = "Cliente respondió \"Si\" al gancho de reactivación sobre la caja de velas. Siguiente paso: datos de envío."
ANSWER = "¡Listo! Para enviarte tu caja necesito unos datos 🤍"
FIXTURE = "history_sales_v2_handoff_reply_v1.json"


async def _generate() -> tuple[str, list[str], int]:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from exoclaw_temporal.config import LLMResponseData
    from src.plugins.chats.agent.sales.contracts import HandoffReply, SalesSessionInput
    from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2
    from tests.test_sales_workflow_debounce import SALES_QUEUE, Tracker, _make_fake_activities

    def final(text: str) -> LLMResponseData:
        return LLMResponseData(content=text, finish_reason="stop", has_tool_calls=False, tool_calls=[])

    reply = HandoffReply(
        text=(
            "[Después de tu último mensaje, el cliente recibió estos mensajes en este chat (los ve como la "
            "misma conversación, aunque no estén en tu historial):\n"
            "- Mensaje automático: «Hola de nuevo, ¿te ayudo a cerrar el pedido?»\n"
            "Lo que el cliente escribe ahora responde a eso: sigue desde ahí y no contradigas lo que se le dijo.]\n"
            "Si"
        ),
        customer_text="Si",
        wamids=["wamid.TRASPASO1"],
        ts_ms=1_000,
    )
    tracker = Tracker()
    acts = _make_fake_activities(
        tracker,
        workspace_path="/fixture/workspace",
        pending_handoff=SUMMARY,
        handoff_reply=reply,
        llm_responses=[final("NO_MESSAGE"), final(ANSWER), final("Etiqueta registrada.")],
        prior_history=[
            {"role": "user", "content": "Hola"},
            {"role": "assistant", "content": "¡Buenas! Bienvenido a *Hubara*..."},
        ],
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=SALES_QUEUE, workflows=[HubaraSalesSessionWorkflowV2], activities=acts
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflowV2.run,
                SalesSessionInput(session_id=SESSION, runtime_workspace_path="/fixture/workspace"),
                id=f"session-{SESSION}",
                task_queue=SALES_QUEUE,
            )
            await handle.result()
            history = await handle.fetch_history()

    # Saneado: la identidad del worker (pid@ip local) → `fixture-worker`.
    raw = re.sub(r'("identity":\s*")[^"]+(")', r"\1fixture-worker\2", history.to_json())
    return raw, [m for (_s, m) in tracker.send_whatsapp_calls], tracker.llm_calls


async def _main(out_dir: Path) -> None:
    raw, sent, llm_calls = await _generate()
    assert GATE in patch_markers(raw), "la history no trae el marcador"
    assert "read_handoff_reply" in raw, "la history no trae la activity nueva"
    assert (sent, llm_calls) == ([ANSWER], 3), f"forma inesperada: envíos={sent} llm={llm_calls}"
    (out_dir / FIXTURE).write_text(raw, encoding="utf-8")
    print(f"escrita {out_dir / FIXTURE}")


if __name__ == "__main__":
    asyncio.run(_main(Path(sys.argv[1])))
