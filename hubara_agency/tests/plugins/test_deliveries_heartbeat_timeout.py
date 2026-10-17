"""Toda entrega al cliente se agenda con `heartbeat_timeout` (auditoría de
Temporal, 2026-10-15; premortem 2026-10-16).

Los envíos de WhatsApp, el flush de componentes y la foto del pedido laten
(`@with_heartbeat`), pero el latido solo sirve si el workflow lo pide: sin
`heartbeat_timeout`, si el worker muere a mitad de un envío (la caja sin
memoria), Temporal recién lo reintenta al vencer el `start_to_close`, y el
cliente espera de más. La auditoría los puso en ventas V2, ETA y campañas; el
premortem encontró seis que faltaban (V1, remarketing y el watchdog).

Solo entregas: son I/O asíncrono que late seguido. A una activity que bloquea
el event loop (CPU en el cuerpo) un `heartbeat_timeout` le daría timeouts
falsos — esas se dejan con su `start_to_close`.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.plugins.activity_options_ast import SRC, ScheduledActivity, all_scheduled_activities, scheduled_activities

#: Las activities que le entregan algo al cliente (función o nombre registrado).
DELIVERIES = frozenset(
    {
        "send_whatsapp_message_activity",
        "send_whatsapp_template_activity",
        "flush_pending_ui_intents_activity",
        "send_ready_photo_activity",
        "send_remarketing_template_activity",
    }
)


def _without_heartbeat(calls: list[ScheduledActivity]) -> list[str]:
    return [
        c.where
        for c in calls
        if c.target in DELIVERIES and (c.options is None or "heartbeat_timeout" not in c.options)
    ]


def test_the_gate_catches_a_delivery_without_heartbeat_timeout(tmp_path: Path) -> None:
    module = tmp_path / "wf.py"
    module.write_text(
        "from temporalio import workflow\n"
        "_SEND = {'start_to_close_timeout': 1, 'retry_policy': 1, 'heartbeat_timeout': 1}\n"
        "async def run():\n"
        "    await workflow.execute_activity(send_whatsapp_message_activity, retry_policy=1)\n"  # 4
        "    await workflow.execute_activity('send_whatsapp_template_activity', **_SEND)\n"  # 5: ok
        "    await workflow.execute_activity(otra_activity, retry_policy=1)\n",  # 6: no es entrega
        encoding="utf-8",
    )
    assert [w.rsplit(":", 1)[1] for w in _without_heartbeat(scheduled_activities(module))] == ["4"]


def test_every_delivery_to_the_customer_has_a_heartbeat_timeout() -> None:
    assert _without_heartbeat(all_scheduled_activities()) == []


def test_every_delivery_heartbeats_more_often_than_its_timeout() -> None:
    """Con `heartbeat_timeout` de 30 s, cada entrega late cada 10 s o menos
    (el SDK manda al servidor como mucho cada 0,8 × 30 s)."""
    every: dict[str, int] = {}
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) or node.name not in DELIVERIES:
                continue
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and getattr(dec.func, "id", None) == "with_heartbeat":
                    value = next((k.value for k in dec.keywords if k.arg == "every"), None)
                    every[node.name] = value.value if isinstance(value, ast.Constant) else 10
    assert set(every) == DELIVERIES, f"entregas que no laten: {sorted(DELIVERIES - set(every))}"
    assert all(seconds <= 10 for seconds in every.values()), every
