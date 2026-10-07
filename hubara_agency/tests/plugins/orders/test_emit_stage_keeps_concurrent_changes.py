"""El evento CAPI de la etapa del pedido no pisa el chat (incidente 2026-10-06).

`_emit_stage_capi` leía el `metadata.json` del chat, consultaba a Medusa por
el total (OrderFacts) y escribía la copia ENTERA: lo que el chat escribió en
esa espera (la cola de tarjetas, una toma del operador) se perdía. Ahora solo
encola su evento, sobre la lectura fresca.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.platform.state import FilesystemMetadataStore
from src.plugins.orders.agent.activities import emit_stage

SESSION = "wa_573001234567"
ORDER = "order_01SHIP"
NOW_MS = 1_758_200_000_000


@pytest.mark.asyncio
async def test_stage_event_keeps_what_the_chat_wrote_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.sdk.connectorkit as connectorkit

    async def _no_flush(session_id: str, **_: Any) -> None:
        return None

    monkeypatch.setattr(emit_stage, "WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.setattr(connectorkit, "flush_capi_outbox", _no_flush)
    path = tmp_path / SESSION / "metadata.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "ctwa_referrals": [{"ctwa_clid": "CLID_1", "captured_at_ms": NOW_MS - 1000}],
                "registered_order": {"success": True, "order_id": ORDER, "total_cop": 120000, "currency": "COP"},
                "active_route": "ventas",
            }
        ),
        encoding="utf-8",
    )

    async def order_payload(metadata: dict[str, Any], order_id: str) -> tuple[int, str, None]:
        # Mientras Medusa responde, el operador toma la conversación.
        FilesystemMetadataStore(tmp_path).update(SESSION, lambda d: {**d, "active_route": "humano"})
        return 120000, "COP", None

    monkeypatch.setattr(emit_stage, "_order_payload", order_payload)

    await emit_stage._emit_stage_capi(SESSION, ORDER, "shipping")

    metadata = json.loads(path.read_text(encoding="utf-8"))
    assert metadata["active_route"] == "humano", "el evento de la etapa pisó la toma del operador"
    assert [e["event_name"] for e in metadata["capi_outbox"]] == ["OrderShipped"]
