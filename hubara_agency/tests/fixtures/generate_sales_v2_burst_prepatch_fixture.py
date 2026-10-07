"""Procedencia de `history_sales_v2_burst_prepatch_v1.json` (fixture CONGELADA).

NO es parte del flujo normal: la fixture ya está commiteada y NO se regenera.
Este script queda como registro reproducible de cómo se produjo.

Congela una sesión del bot nuevo (`HubaraSalesSessionWorkflowV2`) con el código
ANTERIOR a las ráfagas sin cortes (incidente 2026-10-06: el cliente mandó la
dirección en 6 mensajes y el bot contestó a mitad). Hay sesiones vivas del V2
(los números de prueba): lo que cambie en los comandos de su turno tiene que ir
detrás de `workflow.patched`, y esta historia es la barrera. Cubre los tres
gates nuevos (su control negativo está automatizado en
`tests/test_replay_sales.py`):

  * `burst-time-budget-v1`: el turno 1 llega al tope viejo de 2 reinicios y
    el 3.er intento responde aunque el cliente siga escribiendo.
  * `turn-interrupt-before-record-v1`: en el turno 2 llega un mensaje mientras
    corre el egreso y la respuesta se graba y se envía igual.
  * `turn-interrupt-cost-v1`: los intentos cortados reportan tokens y no se
    registra su costo.

Generada con el código de `main` en f83d51b4 (antes de este cambio):

    cd hubara_agency && PYTHONPATH=. uv run python \\
        tests/fixtures/generate_sales_v2_burst_prepatch_fixture.py /tmp/out.json

Corrido contra el código nuevo produciría la forma POST-patch y la fixture
dejaría de proteger nada: el script se niega.

Escenario (sesión sintética `wa_rafaga_v2`, cliente que ya había saludado; la
dirección es inventada):
  turno 1: «Te paso la dirección» → el modelo piensa y llega «Carrera 7 # 12-34»
      (reinicio 1) → llega «Barrio Centro» (reinicio 2) → llega «Torre 2» y,
      con el tope, el turno responde con lo que tenía
  turno 2: «Torre 2» → llega «Apto 201» (reinicio 1) → responde, y mientras
      corre el egreso llega «Frente al parque»: igual se graba y se envía
  turno 3: «Frente al parque» → responde
  cierre por inactividad (turno admin) → fin
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
from pathlib import Path

# A nivel de módulo: Temporal resuelve la anotación de la activity con los
# globales del módulo.
from src.plugins.chats.agent.sales.decisions.contracts import EgressInput, EgressOutput

_HUB = Path(__file__).resolve().parents[2]
#: Los gates de este cambio: si el código ya los trae, produce la forma nueva.
GATES = ("burst-time-budget-v1", "turn-interrupt-before-record-v1", "turn-interrupt-cost-v1")
SESSION = "wa_rafaga_v2"
MESSAGES = (
    "Te paso la dirección",
    "Carrera 7 # 12-34",
    "Barrio Centro",
    "Torre 2",
    "Apto 201",
    "Frente al parque",
)
REPLIES = (
    "¿Me confirmas el teléfono?",
    "¡Gracias! Ya casi lo tengo.",
    "Listo, quedó la dirección completa.",
)
#: Tokens que reporta cada llamada al modelo: sin ellos el costo del turno no
#: se registra y el gate del costo de los intentos cortados no se consulta.
USAGE = {"prompt_tokens": 1200, "completion_tokens": 30}


def patch_markers(history_json: str) -> set[str]:
    """Los ids de `workflow.patched` grabados en la history (base64 en los
    payloads de cada `markerRecordedEvent`)."""
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


def _refuse_if_the_gates_already_exist() -> None:
    sources = [
        _HUB / "src" / "platform" / "workflow_helpers.py",
        _HUB / "src" / "plugins" / "chats" / "agent" / "sales" / "workflows" / "sales_session.py",
        _HUB / "src" / "plugins" / "chats" / "agent" / "sales" / "workflows" / "sales_session_v2.py",
    ]
    code = "\n".join(p.read_text(encoding="utf-8") for p in sources)
    present = [gate for gate in GATES if gate in code]
    if present:
        raise SystemExit(
            f"el código ya contiene {present}: produce la forma POST-patch. La "
            "fixture congelada se generó desde f83d51b4 (ver el docstring). No se regenera."
        )


def _meta(n: int) -> dict:
    return {"wamid": f"wamid.RAFAGA{n}", "ts_ms": 1_000 * n, "kind": "text", "text": MESSAGES[n - 1]}


async def _generate(out: Path) -> None:
    from temporalio import activity
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from exoclaw_temporal.config import LLMResponseData
    from src.plugins.chats.agent.sales.contracts import SalesSessionInput
    from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2
    from tests.test_sales_workflow_debounce import SALES_QUEUE, Tracker, _make_fake_activities

    box: dict = {}

    def says(n: int):
        async def hook() -> None:
            await box["handle"].signal(
                HubaraSalesSessionWorkflowV2.send_message, args=[MESSAGES[n - 1], None, None, _meta(n)]
            )

        return hook

    def final(text: str) -> LLMResponseData:
        return LLMResponseData(
            content=text, finish_reason="stop", has_tool_calls=False, tool_calls=[], usage=dict(USAGE)
        )

    egress_calls: list[str] = []

    # El egreso guionado: devuelve el texto tal cual (sin leer el vault) y, en
    # su 2.ª llamada (el turno 2), el cliente escribe mientras corre.
    @activity.defn(name="decide_egress")
    async def decide_egress(inp: EgressInput) -> EgressOutput:
        egress_calls.append(inp.final_text)
        if len(egress_calls) == 2:
            await says(6)()
        return EgressOutput(text=inp.final_text, llm_text=inp.final_text, final_text=inp.final_text)

    tracker = Tracker()
    base = _make_fake_activities(
        tracker,
        workspace_path="/fixture/workspace",
        llm_responses=[
            final("Respuesta con un solo mensaje"),  # intento 1, cortado
            final("Respuesta con dos mensajes"),  # intento 2, cortado
            final(REPLIES[0]),  # intento 3: el tope viejo, responde
            final("Respuesta con un solo mensaje del turno 2"),  # turno 2, cortado
            final(REPLIES[1]),  # turno 2: responde (llega uno en el egreso)
            final(REPLIES[2]),  # turno 3
            final("Etiqueta registrada."),  # cierre por inactividad (admin)
        ],
        llm_call_hooks={1: says(2), 2: says(3), 3: says(4), 4: says(5)},
        prior_history=[
            {"role": "user", "content": "Hola"},
            {"role": "assistant", "content": "¡Buenas! Bienvenido a *Hubara*..."},
        ],
    )
    acts = [a for a in base if a.__temporal_activity_definition.name != "decide_egress"] + [decide_egress]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=SALES_QUEUE, workflows=[HubaraSalesSessionWorkflowV2], activities=acts):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflowV2.run,
                SalesSessionInput(session_id=SESSION, runtime_workspace_path="/fixture/workspace"),
                id=f"session-{SESSION}",
                task_queue=SALES_QUEUE,
            )
            box["handle"] = handle
            await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=[MESSAGES[0], None, None, _meta(1)])
            await handle.result()
            history = await handle.fetch_history()

    # Saneado: la identidad del worker (pid@ip local) → `fixture-worker`.
    raw = re.sub(r'("identity":\s*")[^"]+(")', r"\1fixture-worker\2", history.to_json())
    markers = patch_markers(raw)
    assert not markers & set(GATES), f"la history trae un marker nuevo: {markers & set(GATES)}"
    sent = [m for (_s, m) in tracker.send_whatsapp_calls]
    assert sent == list(REPLIES), f"forma inesperada: envíos {sent}"
    restarts = [
        [s.get("attempt") for s in t.get("steps") or [] if s.get("kind") == "restart"]
        for t in tracker.turn_traces
        if t["trigger"] == "customer"
    ]
    assert restarts == [[1, 2], [1], []], f"forma inesperada: reinicios {restarts}"
    assert tracker.llm_calls == 7, f"forma inesperada: {tracker.llm_calls} llm_chat (esperaba 7)"
    assert len(egress_calls) == 4, f"forma inesperada: {len(egress_calls)} egresos (esperaba 4)"
    out.write_text(raw, encoding="utf-8")
    print(
        f"escrita {out} ({len(raw.encode())} bytes; llm_calls={tracker.llm_calls}; "
        f"reinicios={restarts}; markers={sorted(markers)})"
    )


if __name__ == "__main__":
    _refuse_if_the_gates_already_exist()
    asyncio.run(_generate(Path(sys.argv[1])))
