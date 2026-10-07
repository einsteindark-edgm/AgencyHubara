"""Procedencia de `history_sales_system_turns_preclock_v1.json` (V1) y
`history_sales_v2_system_turns_preclock_v1.json` (V2) — fixtures CONGELADAS.

NO es parte del flujo normal: la fixture ya está commiteada y NO se regenera.
Este script queda como registro reproducible de cómo se produjo.

Congela la forma de los turnos de SISTEMA del V1 (los que arma el workflow sin
mensaje del cliente) ANTES del gate `system-turn-bogota-clock-v1`: el traspaso
de remarketing al arrancar, el que llega con la sesión dormida (chequeo del
timeout), el que llega a mitad de sesión (refresco por iteración) y el cierre
por abandono. En producción, las sesiones en vuelo al desplegar traen esta
forma: si la activity de la hora (`compute_bogota_context`) se agendara en esos
caminos sin su `workflow.patched`, el replay chocaría (L-9). El complemento de
la capa ③ lo cubre `history_sales_perception_v1.json`.

La del V1 se generó con el código de `lab/todo` en f7e38acf; la del V2, con el
de `main` en f89716ad (el V2 ya tenía sesiones vivas: todas las conversaciones
desde el 2026-10-07). Ambas, anteriores al gate. Corrido contra el código actual
daría la forma POST-patch y dejaría de proteger nada: el script se niega.

    cd hubara_agency && PYTHONPATH=. uv run python \\
        tests/fixtures/generate_system_turns_preclock_fixture.py /tmp/out.json [v1|v2]

Escenario (sesión sintética `wa_systemturns`, cliente que ya había conversado):
  turno 1: traspaso de remarketing leído al arrancar (con la nota del pedido)
  turno 2: traspaso escrito con la sesión dormida (lo lee el chequeo del
      timeout, con la nota del pedido) — no hay abandono ese ciclo
  turno 3: mensaje del cliente + traspaso del refresco por iteración (sin nota)
  turno 4: cierre por abandono (turno admin, no se envía) → fin
"""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import timedelta
from pathlib import Path

_HUB = Path(__file__).resolve().parents[2]
_GATE = "system-turn-bogota-clock-v1"
SESSION = "wa_systemturns"
HANDOFF_AT_START = "Usuario respondió: Hola, ¿todavía tienen la vela de Leo?"
HANDOFF_WHILE_IDLE = "Usuario respondió: Dame 3"
HANDOFF_MID_SESSION = "Usuario respondió: sí, las 3"
CUSTOMER = "¿Y cuánto sale el envío a Bogotá?"
DRAFT_NOTE = "[DATOS DEL PEDIDO YA CONFIRMADOS POR EL CLIENTE, metadata]\nNotas: 3× Leo café"


def _refuse_if_the_gate_already_exists(version: str) -> None:
    workflows = _HUB / "src" / "plugins" / "chats" / "agent" / "sales" / "workflows"
    workflow_src = (workflows / "sales_session.py").read_text(encoding="utf-8")
    v2_src = (workflows / "sales_session_v2.py").read_text(encoding="utf-8")
    if _GATE in workflow_src or (version == "v2" and "_system_turn_clock" in v2_src):
        raise SystemExit(
            f"sales_session.py ya contiene el gate `{_GATE}`: este código produce la forma "
            "POST-patch. La fixture congelada se generó desde f7e38acf (ver el docstring). No se regenera."
        )


async def _until(env, condition, *, seconds: int = 40) -> None:
    """Avanza el reloj del servidor de pruebas de a 1 s hasta que se cumpla."""
    for _ in range(seconds):
        if condition():
            return
        await env.sleep(timedelta(seconds=1))
        await asyncio.sleep(0.05)
    raise AssertionError("el escenario no avanzó")


async def _generate(out: Path, version: str) -> None:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from src.plugins.chats.agent.sales.contracts import SalesSessionInput
    from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow
    from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2

    workflow_cls = HubaraSalesSessionWorkflowV2 if version == "v2" else HubaraSalesSessionWorkflow
    from tests.fixtures.generate_perception_v1_fixture import patch_markers
    from tests.test_sales_workflow_debounce import SALES_QUEUE, Tracker, _make_fake_activities

    tracker = Tracker()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[workflow_cls],
            activities=_make_fake_activities(
                tracker,
                workspace_path="/fixture/workspace",
                # Llamadas a `read_and_clear_pending_handoff`, en orden:
                # arranque, refresco (t1), timeout (t2), refresco (t2),
                # refresco (t3), timeout (abandono), refresco (t4).
                handoff_sequence=[HANDOFF_AT_START, None, HANDOFF_WHILE_IDLE, None, HANDOFF_MID_SESSION],
                order_draft_note=DRAFT_NOTE,
                prior_history=[
                    {"role": "user", "content": "Hola"},
                    {"role": "assistant", "content": "¡Buenas! Bienvenido a *Hubara*..."},
                ],
            ),
        ):
            handle = await env.client.start_workflow(
                workflow_cls.run,
                SalesSessionInput(session_id=SESSION, runtime_workspace_path="/fixture/workspace"),
                id=f"session-{SESSION}",
                task_queue=SALES_QUEUE,
            )
            await _until(env, lambda: len(tracker.turn_traces) >= 1)
            # La sesión se duerme: el timeout de inactividad lee el traspaso.
            await env.sleep(timedelta(seconds=61))
            await _until(env, lambda: len(tracker.turn_traces) >= 2)
            await handle.signal(workflow_cls.send_message, args=[CUSTOMER, None, None])
            await _until(env, lambda: len(tracker.turn_traces) >= 3)
            await handle.result()
            history = await handle.fetch_history()

    # Saneado: la identidad del worker (pid@ip local) → `fixture-worker`.
    raw = re.sub(r'("identity":\s*")[^"]+(")', r"\1fixture-worker\2", history.to_json())
    markers = patch_markers(raw)
    assert _GATE not in markers and "compute_bogota_context" not in raw, "la history ya trae la hora: NO es pre-patch"
    if version == "v1":
        assert {"handoff-draft-note-v1", "ghost-checks-handoff-first-v1", "handoff-refresh-per-iteration-v1"} <= markers
    messages = [c.message for c in tracker.build_prompt_calls]
    assert len(messages) == 4, f"forma inesperada: {messages}"
    assert HANDOFF_AT_START in messages[0] and HANDOFF_WHILE_IDLE in messages[1], messages
    assert messages[2] == CUSTOMER and any(HANDOFF_MID_SESSION in c for c in tracker.build_prompt_calls[2].plugin_context)
    assert tracker.ghosting_calls == 1 and messages[3] == "[GHOST] auto-tagging"
    out.write_text(raw, encoding="utf-8")
    print(f"escrita {out} ({len(raw.encode())} bytes; turnos={len(messages)})")


if __name__ == "__main__":
    version = sys.argv[2] if len(sys.argv) > 2 else "v1"
    _refuse_if_the_gate_already_exists(version)
    asyncio.run(_generate(Path(sys.argv[1]), version))
