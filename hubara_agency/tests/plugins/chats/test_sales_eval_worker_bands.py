"""El worker `sales_eval` registra la activity de las bandas por regla (Ciclo
Colossus F4). Sin registrarla, el barrido diario la agenda, falla en silencio
(el paso no bloquea) y nunca abre una alerta."""
from __future__ import annotations

import os

# init_otel() corre al importar el worker (ver test_decisions_probe_schedule.py).
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from src.plugins.chats.workers import sales_eval as worker  # noqa: E402
from tests.plugins.chats.test_decisions_probe_schedule import QUEUE, _Temporal  # noqa: E402


async def test_the_worker_registers_the_bands_activity(monkeypatch) -> None:
    from src.plugins.chats.agent.sales_eval.activities.bands_activities import (
        compute_scorecard_bands_activity,
    )

    client = _Temporal()
    seen: dict = {}

    class _Worker:
        def __init__(self, _client, *, task_queue, workflows, activities, **_kw) -> None:
            seen.update(activities=list(activities))

        async def run(self) -> None:
            return None

    async def _client() -> _Temporal:
        return client

    monkeypatch.setattr(worker, "ensure_plugin_enabled", lambda _plugin: None)
    monkeypatch.setattr(worker, "get_temporal_client", _client)
    monkeypatch.setattr(worker, "get_task_queue", lambda _plugin, _worker: QUEUE)
    monkeypatch.setattr(worker, "Worker", _Worker)

    await worker.main()

    assert compute_scorecard_bands_activity in seen["activities"]
