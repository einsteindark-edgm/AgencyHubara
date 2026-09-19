"""Las burbujas de UN mismo mensaje son UN toque.

Desde 2026-09-18 el log de outbounds guarda una entrada POR BURBUJA (cada una
con su wamid — sin eso el webhook de Meta no podía ponerle precio al texto).
Un gancho de remarketing partido en 2 burbujas ("Hola de nuevo 🌿\\n\\n¿Te
ayudo a elegir?") NO son 2 toques: el `cadence_cap` del Window Strategist
(`max_touches_per_24h`) se agotaría con un solo mensaje. Todas las burbujas de
un envío comparten `sent_at_ms` + `kind` → se colapsan.
"""
from __future__ import annotations

from src.plugins.reengagement.agent.cycle.use_cases.build_snapshot import (
    _recent_touches,
)

H = 60 * 60 * 1000
T0 = 1_800_000_000_000


def _meta(outbounds: list[dict]) -> dict:
    return {
        "last_inbound_at_ms": T0,
        "episodes": [{"episode_id": "ep_001", "outbound_messages": outbounds}],
    }


def test_dos_burbujas_del_mismo_envio_son_un_toque():
    touches = _recent_touches(_meta([
        {"sent_at_ms": T0 + 2 * H, "kind": "text", "wa_message_id": "wamid.A1"},
        {"sent_at_ms": T0 + 2 * H, "kind": "text", "wa_message_id": "wamid.A2"},
    ]))
    assert touches == [{"at_ms": T0 + 2 * H, "kind": "text"}]


def test_dos_envios_distintos_siguen_siendo_dos_toques():
    touches = _recent_touches(_meta([
        {"sent_at_ms": T0 + 2 * H, "kind": "text", "wa_message_id": "wamid.A1"},
        {"sent_at_ms": T0 + 4 * H, "kind": "text", "wa_message_id": "wamid.B1"},
    ]))
    assert [t["at_ms"] for t in touches] == [T0 + 2 * H, T0 + 4 * H]
