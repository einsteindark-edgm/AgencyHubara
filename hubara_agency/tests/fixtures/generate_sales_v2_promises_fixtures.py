"""Procedencia de las dos fixtures CONGELADAS del gate `promised-actions-round-v1`.

NO es parte del flujo normal: las fixtures ya están commiteadas y NO se
regeneran. Este script queda como registro reproducible de cómo se produjeron.

La ronda de lo prometido (incidente del 2026-10-09, «Te paso el formulario»
sin `request_shipping_details`) se decide por una clave NUEVA del resultado de
`send_reply` (`promises`). Una history real de antes del deploy (resultados de
forma vieja) jamás la activa y no protege el gate. Las dos historias son
sintéticas a propósito (sesión `wa_promesas_v2`, bot nuevo):

  * `history_sales_v2_promises_prepatch_v1.json` — la ventana de versiones
    mezcladas de un deploy: la activity ya devuelve `promises` y el workflow
    todavía no tiene la ronda. Se genera con el código actual y el gate
    forzado a «no» (`workflow.patched` devuelve False sin grabar marcador:
    exactamente el camino del código anterior). El turno termina en el
    `send_reply` y el texto sale.
  * `history_sales_v2_promises_round_v1.json` — control positivo, con el
    código actual: marcador grabado, la ronda, el modelo manda el formulario y
    el texto retenido sale.

    cd hubara_agency && PYTHONPATH=. uv run python \\
        tests/fixtures/generate_sales_v2_promises_fixtures.py /tmp/out_dir

Escenario: el cliente «El contra entrega, y cuánto se demora en llegar?»; el
modelo responde con `send_reply` prometiendo el formulario; después, el cierre
por inactividad (turno admin) y fin.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

# A nivel de módulo: Temporal resuelve la anotación de la activity con los
# globales del módulo.
from src.plugins.chats.agent.sales.decisions.contracts import EgressInput, EgressOutput
from tests.fixtures.generate_sales_v2_burst_prepatch_fixture import patch_markers

GATE = "promised-actions-round-v1"
SESSION = "wa_promesas_v2"
CUSTOMER = "El contra entrega, y cuánto se demora en llegar?"
PROMISE = "Perfecto, contra entrega 🤍 En Bogotá llega en 1 a 2 días hábiles.\n\nTe paso el formulario para los datos de envío."
NUDGE = "Le dijiste que le pasas el formulario de envío y no lo mandaste: llama request_shipping_details."
PREPATCH = "history_sales_v2_promises_prepatch_v1.json"
ROUND = "history_sales_v2_promises_round_v1.json"


async def _generate(*, gated_off: bool) -> tuple[str, int, list[str]]:
    from temporalio import activity, workflow
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from exoclaw_temporal.config import LLMResponseData, ToolCallData
    from src.plugins.chats.agent.sales.contracts import SalesSessionInput
    from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2
    from tests.test_sales_workflow_debounce import SALES_QUEUE, Tracker, _make_fake_activities

    def calls(*names_args: tuple[str, dict]) -> LLMResponseData:
        return LLMResponseData(
            content="", finish_reason="tool_calls", has_tool_calls=True,
            tool_calls=[ToolCallData(id=f"call{n}", name=name, arguments=args) for n, (name, args) in enumerate(names_args)],
        )

    def final(text: str) -> LLMResponseData:
        return LLMResponseData(content=text, finish_reason="stop", has_tool_calls=False, tool_calls=[])

    @activity.defn(name="decide_egress")
    async def decide_egress(inp: EgressInput) -> EgressOutput:
        return EgressOutput(text=inp.final_text, llm_text=inp.final_text, final_text=inp.final_text)

    reply = {"text": PROMISE}
    responses = [calls(("send_reply", reply))]
    if not gated_off:
        responses.append(calls(("request_shipping_details", {"items": [{"handle": "calabaza", "quantity": 1}]})))
    responses.append(final("Etiqueta registrada."))  # cierre por inactividad (admin)

    tracker = Tracker()
    base = _make_fake_activities(
        tracker,
        workspace_path="/fixture/workspace",
        llm_responses=responses,
        tool_results={
            "send_reply": json.dumps({
                "reply": {"text": PROMISE}, "summary": "Mensaje listo para el cliente.",
                "promises": [{"kind": "formulario", "tools": ["request_shipping_details"], "nudge": NUDGE}],
            }, ensure_ascii=False),
            "request_shipping_details": json.dumps({"queued": True, "kind": "shipping_flow"}),
        },
        prior_history=[
            {"role": "user", "content": "Hola"},
            {"role": "assistant", "content": "¡Buenas! Bienvenido a *Hubara*..."},
        ],
    )
    acts = [a for a in base if a.__temporal_activity_definition.name != "decide_egress"] + [decide_egress]

    real_patched = workflow.patched

    def old_code(patch_id: str) -> bool:
        # El código anterior no tenía la ronda: sin marcador, camino viejo.
        return False if patch_id == GATE else real_patched(patch_id)

    if gated_off:
        workflow.patched = old_code  # type: ignore[assignment]
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(env.client, task_queue=SALES_QUEUE, workflows=[HubaraSalesSessionWorkflowV2], activities=acts):
                handle = await env.client.start_workflow(
                    HubaraSalesSessionWorkflowV2.run,
                    SalesSessionInput(session_id=SESSION, runtime_workspace_path="/fixture/workspace"),
                    id=f"session-{SESSION}",
                    task_queue=SALES_QUEUE,
                )
                meta = {"wamid": "wamid.PROMESA1", "ts_ms": 1_000, "kind": "text", "text": CUSTOMER}
                await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=[CUSTOMER, None, None, meta])
                await handle.result()
                history = await handle.fetch_history()
    finally:
        workflow.patched = real_patched  # type: ignore[assignment]

    # Saneado: la identidad del worker (pid@ip local) → `fixture-worker`.
    raw = re.sub(r'("identity":\s*")[^"]+(")', r"\1fixture-worker\2", history.to_json())
    sent = [m for (_s, m) in tracker.send_whatsapp_calls]
    return raw, tracker.llm_calls, sent


async def _main(out_dir: Path) -> None:
    prepatch, llm_calls, sent = await _generate(gated_off=True)
    assert GATE not in patch_markers(prepatch), "la history pre-patch trae el marcador"
    assert llm_calls == 2 and sent == [PROMISE], f"forma inesperada pre-patch: llm={llm_calls} envíos={sent}"
    (out_dir / PREPATCH).write_text(prepatch, encoding="utf-8")

    with_round, llm_calls, sent = await _generate(gated_off=False)
    assert GATE in patch_markers(with_round), "la history con la ronda no trae el marcador"
    assert llm_calls == 3 and sent == [PROMISE], f"forma inesperada con la ronda: llm={llm_calls} envíos={sent}"
    (out_dir / ROUND).write_text(with_round, encoding="utf-8")
    print(f"escritas {out_dir / PREPATCH} y {out_dir / ROUND}")


if __name__ == "__main__":
    asyncio.run(_main(Path(sys.argv[1])))
