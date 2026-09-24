"""Procedencia de `history_sales_perception_v1.json` (fixture CONGELADA).

NO es parte del flujo normal: la fixture ya está commiteada y NO se regenera.
Este script queda como registro reproducible de cómo se produjo.

Congela la forma de una sesión de ventas con el clasificador PRENDIDO
(`workflow.patched("perception-v1")`, capas ①②③ del laboratorio, PR 14): una
vez en producción, las sesiones en vuelo traen esa forma y cualquier cambio de
commands en esos caminos sin su propio `workflow.patched` las rompería al
redeployar (L-9). Mientras no haya historias REALES con el marker (el
clasificador todavía no corrió en producción), esta sintética es la barrera;
cuando corra unos días en sombra, se suman historias reales saneadas (A-PM07).

Generada con el código de la integración de la cadena del laboratorio
(`lab/integracion`, commit 9afa43b4, ANTES de que la verificación leyera todo lo
que el cliente recibe en el turno): ese cambio solo toca el payload de
`verify_coverage`, así que esta history replayea igual con el código nuevo (L-22).

    cd hubara_agency && PYTHONPATH=. uv run python \\
        tests/fixtures/generate_perception_v1_fixture.py /tmp/out.json

Escenario (sesión sintética `wa_perception`, cliente que ya había saludado):
  turno 1, modo `shadow`: percepción en paralelo → LLM → `send_reply` → envío
      → verificación DESPUÉS de enviar (no cambia nada)
  turno 2, modo `on`: percepción → plan como nota del turno → LLM →
      `send_reply` → verificación ANTES de enviar dice `complement` → envío →
      turno de sistema del complemento (una burbuja más)
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

# A nivel de módulo: Temporal resuelve la anotación de la activity con los
# globales del módulo.
from exoclaw_temporal.config import ExecuteToolInput

_MARKER = "perception-v1"
SESSION = "wa_perception"
TURN_1 = "vi que hacen velas con otros diseños, ¿me mandas el catálogo?"
TURN_2 = "y el envío a Bogotá cuánto sale?"
REPLY_1 = "¡Claro! Te dejo nuestro catálogo 👇"
REPLY_2 = "Con gusto te cuento del envío."
COMPLEMENT = "El envío a Bogotá cuesta $12.900 y llega en 2 a 3 días hábiles."


def patch_markers(history_json: str) -> set[str]:
    """Los ids de `workflow.patched` grabados en la history: van en base64
    dentro de los payloads de cada `markerRecordedEvent`."""
    import base64

    found: set[str] = set()
    for event in json.loads(history_json).get("events", []):
        attrs = event.get("markerRecordedEventAttributes") or {}
        for detail in (attrs.get("details") or {}).values():
            for payload in detail.get("payloads") or []:
                try:
                    data = json.loads(base64.b64decode(payload.get("data") or ""))
                except ValueError:
                    continue
                if isinstance(data, dict) and isinstance(data.get("id"), str):
                    found.add(data["id"])
    return found


async def _generate(out: Path) -> None:
    from temporalio import activity
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from src.plugins.chats.agent.sales.contracts import SalesSessionInput
    from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow
    from tests.test_sales_perception_layers import LLM, Classifier, _tool
    from tests.test_sales_workflow_debounce import SALES_QUEUE, Tracker, _make_fake_activities

    tracker = Tracker()
    classifier = Classifier(decision="complement", missing=["envio"])
    llm = LLM([_tool("send_reply", text=REPLY_1), _tool("send_reply", text=REPLY_2), _tool("send_reply", text=COMPLEMENT)])
    base = _make_fake_activities(
        tracker,
        workspace_path="/fixture/workspace",
        tool_results={},
        prior_history=[{"role": "user", "content": "Hola"}, {"role": "assistant", "content": "¡Buenas! Bienvenido a *Hubara*..."}],
    )

    @activity.defn(name="execute_tool")
    async def execute_tool(input: ExecuteToolInput) -> str:
        tracker.execute_tool_calls.append(input.name)
        if input.name == "send_reply":
            return json.dumps({"reply": {"text": input.params.get("text", "")}}, ensure_ascii=False)
        return "ok"

    replaced = {"llm_chat", "execute_tool"}
    acts = [a for a in base if a.__temporal_activity_definition.name not in replaced] + [
        llm.activity(), execute_tool, *classifier.activities()
    ]
    turns = [(TURN_1, "shadow"), (TURN_2, "on")]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=SALES_QUEUE, workflows=[HubaraSalesSessionWorkflow], activities=acts):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id=SESSION, runtime_workspace_path="/fixture/workspace"),
                id=f"session-{SESSION}",
                task_queue=SALES_QUEUE,
            )
            for n, (text, mode) in enumerate(turns, 1):
                meta = {"perception_mode": mode, "perception_profile": "jev-v1", "ts_ms": n * 60_000}
                await handle.signal(HubaraSalesSessionWorkflow.send_message, args=[text, None, None, meta])
                for _ in range(40):
                    if sum(1 for t in tracker.turn_traces if t["trigger"] == "customer") >= n:
                        break
                    await env.sleep(timedelta(seconds=1))
                    await asyncio.sleep(0.05)
            await handle.result()
            history = await handle.fetch_history()

    # Saneado: la identidad del worker (pid@ip local) → `fixture-worker`.
    raw = re.sub(r'("identity":\s*")[^"]+(")', r"\1fixture-worker\2", history.to_json())
    assert _MARKER in patch_markers(raw), f"la history no trae el marker del clasificador: {patch_markers(raw)}"
    modes = [t.get("mode") for t in tracker.turn_traces if t["trigger"] == "customer"]
    assert modes == ["shadow", "on"], f"forma inesperada: modos {modes}"
    assert any(t["trigger"] == "complement" for t in tracker.turn_traces), "falta el turno del complemento"
    assert len(classifier.perceived) == 2 and len(classifier.verified) >= 2
    out.write_text(raw, encoding="utf-8")
    print(f"escrita {out} ({len(raw.encode())} bytes; turnos={[t['trigger'] for t in tracker.turn_traces]})")


if __name__ == "__main__":
    asyncio.run(_generate(Path(sys.argv[1])))
