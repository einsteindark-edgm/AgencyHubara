"""Procedencia de las dos fixtures CONGELADAS del gate `restart-carries-reads-v1`.

NO es parte del flujo normal: las fixtures ya están commiteadas y NO se
regeneran. Este script queda como registro reproducible de cómo se produjeron.

Turno 1 de …7392 (2026-10-08): el modelo buscó en el catálogo, el cliente
escribió antes de que saliera la respuesta y el reinicio volvió a empezar sin
la búsqueda. El gate hace que el reinicio reciba lo que el intento cortado ya
leyó: cambia lo que cuenta como usado para el contrato del turno y, con eso,
cuántas rondas pide. Las dos historias son sintéticas (sesión `wa_lecturas_v2`,
bot nuevo con un contrato que pide `search_products` para el precio):

  * `history_sales_v2_carry_reads_prepatch_v1.json` — el código anterior: el
    gate forzado a «no» (`workflow.patched` devuelve False sin grabar
    marcador). El reinicio no tiene la búsqueda, el modelo contesta con texto,
    el contrato lo retiene (una ronda más) y el texto sale en la siguiente.
  * `history_sales_v2_carry_reads_v1.json` — control positivo con el código
    actual: marcador grabado, el reinicio trae la búsqueda y el texto sale en
    la primera ronda.

    cd hubara_agency && PYTHONPATH=. uv run python \\
        tests/fixtures/generate_sales_v2_carry_reads_fixtures.py /tmp/out_dir

Escenario: «¿Cuánto vale la Calabaza?»; el modelo busca, pide la lista de
productos y el cliente escribe «y qué viene incluido» mientras piensa; después,
el cierre por inactividad (turno admin) y fin.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import sys
from pathlib import Path

from tests.fixtures.generate_sales_v2_burst_prepatch_fixture import patch_markers

GATE = "restart-carries-reads-v1"
SESSION = "wa_lecturas_v2"
CUSTOMER = "¿Cuánto vale la Calabaza?"
FOLLOW_UP = "y qué viene incluido"
FOUND = json.dumps({"query": "calabaza", "count": 1, "results": [{"handle": "calabaza", "title": "Calabaza", "price": "16000"}]})
ANSWER = "La Calabaza cuesta $16.000 y viene con aroma a frutos rojos 🎃"
CONTRACT = {
    "required": [
        {"topic": "precio", "any_of": ["search_products"], "nudge": "El precio sale del catálogo: consúltalo con search_products."}
    ]
}
PREPATCH = "history_sales_v2_carry_reads_prepatch_v1.json"
CARRY = "history_sales_v2_carry_reads_v1.json"


async def _generate(*, gated_off: bool) -> tuple[str, int, list[str], list[str]]:
    from temporalio import workflow
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from exoclaw_temporal.config import LLMResponseData, ToolCallData
    from src.plugins.chats.agent.sales.contracts import SalesSessionInput
    from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2
    from tests.test_sales_perception_layers import Classifier
    from tests.test_sales_workflow_debounce import SALES_QUEUE, Tracker, _make_fake_activities

    def calls(name: str, args: dict) -> LLMResponseData:
        return LLMResponseData(
            content="", finish_reason="tool_calls", has_tool_calls=True,
            tool_calls=[ToolCallData(id=f"call_{name}", name=name, arguments=args)],
        )

    def final(text: str) -> LLMResponseData:
        return LLMResponseData(content=text, finish_reason="stop", has_tool_calls=False, tool_calls=[])

    classifier = Classifier()
    base_decisions = classifier.decisions
    classifier.decisions = lambda profile: dataclasses.replace(base_decisions(profile), tools=CONTRACT)  # type: ignore[method-assign]

    responses = [
        calls("search_products", {"q": "calabaza"}),
        calls("present_products", {"handles": ["calabaza"], "intro_text": "Mira la Calabaza 🎃"}),
        final(ANSWER),  # el reinicio contesta con texto
    ]
    if gated_off:
        responses.append(final(ANSWER))  # sin la búsqueda, el contrato pidió otra ronda
    responses.append(final("Etiqueta registrada."))  # cierre por inactividad (admin)

    box: dict = {}

    async def customer_writes() -> None:
        meta = {"wamid": "wamid.LECTURA2", "ts_ms": 2_000, "kind": "text", "text": FOLLOW_UP}
        await box["handle"].signal(HubaraSalesSessionWorkflowV2.send_message, args=[FOLLOW_UP, None, None, meta])

    tracker = Tracker()
    acts = _make_fake_activities(
        tracker,
        workspace_path="/fixture/workspace",
        llm_responses=responses,
        tool_results={"search_products": FOUND, "present_products": json.dumps({"queued": True})},
        llm_call_hooks={2: customer_writes},
        prior_history=[
            {"role": "user", "content": "Hola"},
            {"role": "assistant", "content": "¡Buenas! Bienvenido a *Hubara*..."},
        ],
    )

    real_patched = workflow.patched

    def old_code(patch_id: str) -> bool:
        # El código anterior no llevaba lecturas al reinicio: sin marcador.
        return False if patch_id == GATE else real_patched(patch_id)

    if gated_off:
        workflow.patched = old_code  # type: ignore[assignment]
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client, task_queue=SALES_QUEUE, workflows=[HubaraSalesSessionWorkflowV2],
                activities=[*acts, *classifier.activities()],
            ):
                handle = await env.client.start_workflow(
                    HubaraSalesSessionWorkflowV2.run,
                    SalesSessionInput(session_id=SESSION, runtime_workspace_path="/fixture/workspace"),
                    id=f"session-{SESSION}",
                    task_queue=SALES_QUEUE,
                )
                box["handle"] = handle
                meta = {
                    "wamid": "wamid.LECTURA1", "ts_ms": 1_000, "kind": "text", "text": CUSTOMER,
                    "perception_mode": "on", "perception_profile": "jev-v1",
                }
                await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=[CUSTOMER, None, None, meta])
                await handle.result()
                history = await handle.fetch_history()
    finally:
        workflow.patched = real_patched  # type: ignore[assignment]

    # Saneado: la identidad del worker (pid@ip local) → `fixture-worker`.
    raw = re.sub(r'("identity":\s*")[^"]+(")', r"\1fixture-worker\2", history.to_json())
    sent = [m for (_s, m) in tracker.send_whatsapp_calls]
    return raw, tracker.llm_calls, sent, list(tracker.execute_tool_calls)


async def _main(out_dir: Path) -> None:
    prepatch, llm_calls, sent, tools = await _generate(gated_off=True)
    assert GATE not in patch_markers(prepatch), "la history pre-patch trae el marcador"
    assert (llm_calls, sent, tools) == (5, [ANSWER], ["search_products"]), (
        f"forma inesperada pre-patch: llm={llm_calls} envíos={sent} tools={tools}"
    )
    (out_dir / PREPATCH).write_text(prepatch, encoding="utf-8")

    carried, llm_calls, sent, tools = await _generate(gated_off=False)
    assert GATE in patch_markers(carried), "la history con las lecturas no trae el marcador"
    assert (llm_calls, sent, tools) == (4, [ANSWER], ["search_products"]), (
        f"forma inesperada con las lecturas: llm={llm_calls} envíos={sent} tools={tools}"
    )
    (out_dir / CARRY).write_text(carried, encoding="utf-8")
    print(f"escritas {out_dir / PREPATCH} y {out_dir / CARRY}")


if __name__ == "__main__":
    asyncio.run(_main(Path(sys.argv[1])))
