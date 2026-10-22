"""Procedencia de la fixture CONGELADA del gate `eta-rejected-text-as-template-v1`.

NO es parte del flujo normal: la fixture ya está commiteada y NO se regenera.
Este script queda como registro reproducible de cómo se produjo.

Premortem de la Ola 0 de Temporal (2026-10-16): dentro de la ventana, el aviso
del ETA sale como texto; si WhatsApp no acepta ninguna burbuja, la activity no
lanza (devuelve `[]`) y el aviso quedaba grabado como enviado. Ahora sale por
la plantilla: comando nuevo en un workflow que vive días (L-9). La history es
la de ANTES: el texto rechazado se persiste y se graba. Se genera con el código
actual y el gate forzado a «no» (`workflow.patched` devuelve False sin grabar
marcador: exactamente el camino del código anterior).

    cd hubara_agency && MEDUSA_BASE_URL=http://medusa.invalid MEDUSA_ADMIN_TOKEN=ci-dummy \\
        OTEL_SDK_DISABLED=true uv run python -m tests.fixtures.generate_eta_rejected_text_prepatch_fixture /tmp/out

Escenario (sesión sintética `wa_eta_rechazo`): un aviso de `shipping` dentro de
la ventana, WhatsApp no acepta el texto, una semana sin eventos y fin.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

GATE = "eta-rejected-text-as-template-v1"
SESSION = "wa_eta_rechazo"
FIXTURE = "history_eta_rejected_text_prepatch_v1.json"


async def _generate() -> tuple[str, list[str]]:
    from temporalio import activity, workflow
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from src.plugins.eta.agent.eta.workflows.eta_session import HubaraEtaSessionWorkflow
    from tests.plugins.eta.test_eta_ready_photo_workflow import QUEUE, Tracker, _fakes

    calls: list[str] = []

    @activity.defn(name="claim_eta_notification_activity")
    async def claim_in_window(session_id, order_id, stage, tracking_url=None, shipping_cost=None):
        return {
            "customer_name": "",
            "order_display_id": "#31",
            "total_label": "$ 10.000",
            "pay_type": "confirmed",
            "payment_confirmed": True,
            "delivery_window": None,
            "items_label": "Velón",
            "in_service_window": True,
            "has_ready_photo": False,
        }

    @activity.defn(name="send_whatsapp_message_activity")
    async def rejected_text(session_id: str, message: str, decided_by_engine: bool = False) -> list:
        calls.append("text")
        return []

    @activity.defn(name="send_whatsapp_template_activity")
    async def send_template(session_id: str, template: str, variables: dict) -> None:
        calls.append("template")

    @activity.defn(name="persist_assistant_message_activity")
    async def persist(session_id: str, message: str, tools_used: list | None = None, delivered: list | None = None) -> None:
        calls.append("persist")

    replaced = {
        "claim_eta_notification_activity",
        "send_whatsapp_message_activity",
        "send_whatsapp_template_activity",
        "persist_assistant_message_activity",
    }
    acts = [a for a in _fakes(Tracker(has_photo=False)) if a.__temporal_activity_definition.name not in replaced]
    acts += [claim_in_window, rejected_text, send_template, persist]

    real_patched = workflow.patched
    workflow.patched = lambda patch_id: False if patch_id == GATE else real_patched(patch_id)  # type: ignore[assignment]
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(env.client, task_queue=QUEUE, workflows=[HubaraEtaSessionWorkflow], activities=acts):
                handle = await env.client.start_workflow(
                    "HubaraEtaSessionWorkflow",
                    {"session_id": SESSION, "order_id": "order_A"},
                    id=f"eta-{SESSION}",
                    task_queue=QUEUE,
                )
                await handle.signal(
                    "notify_stage_change", {"session_id": SESSION, "order_id": "order_A", "to_stage": "shipping"}
                )
                await handle.result()
                history = await handle.fetch_history()
    finally:
        workflow.patched = real_patched  # type: ignore[assignment]
    raw = re.sub(r'("identity":\s*")[^"]+(")', r"\1fixture-worker\2", history.to_json())
    return raw, calls


async def _main(out_dir: Path) -> None:
    from tests.fixtures.generate_sales_v2_burst_prepatch_fixture import patch_markers

    raw, calls = await _generate()
    assert GATE not in patch_markers(raw), "la history pre-patch trae el marcador"
    assert calls == ["text", "persist"], f"forma inesperada: {calls}"
    events = json.loads(raw)["events"]
    assert not any("activityTaskFailedEventAttributes" in e for e in events), "una activity falló en la fixture"
    (out_dir / FIXTURE).write_text(raw, encoding="utf-8")
    print(f"escrita {out_dir / FIXTURE}")


if __name__ == "__main__":
    asyncio.run(_main(Path(sys.argv[1])))
