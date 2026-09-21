"""Etiqueta `SIN_RESPUESTA`: la escalera se agotó y el cliente nunca contestó.

Decisión del operador (2026-09-18): tras los 5 toques no se envía más (no se
gasta más) y el cliente queda ETIQUETADO para poder filtrarlo después y hacer
algo con esos números. El tag se pone tras una gracia igual al último hueco
(6h) — el quinto toque también merece su tiempo de respuesta.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.reengagement.agent.cycle.activities import (
    build_reengagement_snapshot_activity,
)
from src.plugins.reengagement.agent.cycle.use_cases import (
    TAG_UNRESPONSIVE,
    unresponsive_session_ids,
)

H = 60 * 60 * 1000
NOW = 1_800_000_000_000


def _exhausted(last_touch_h_ago: float, *, now: int = NOW, tag: str = "INTERESADO") -> dict:
    hours = [last_touch_h_ago + d for d in (18, 16, 12, 6, 0)]
    return {
        "tag": tag,
        "last_inbound_at_ms": now - int((last_touch_h_ago + 20) * H),
        "service_window_expires_at_ms": now - H,
        "remarketing_touches": [
            {"at_ms": now - int(h * H), "kind": "free_form"} for h in hours
        ],
    }


def test_agotada_con_gracia_cumplida_se_etiqueta():
    assert unresponsive_session_ids(NOW, [("wa_a", _exhausted(6.5))]) == ["wa_a"]


def test_agotada_sin_cumplir_la_gracia_todavia_no():
    assert unresponsive_session_ids(NOW, [("wa_a", _exhausted(2))]) == []


def test_no_pisa_tags_que_no_son_del_embudo_abierto():
    sessions = [
        ("wa_humano", _exhausted(7, tag="HUMANO")),
        ("wa_compro", _exhausted(7, tag="COMPRA_EXITOSA")),
        ("wa_rechazo", _exhausted(7, tag="RECHAZO")),
        ("wa_pago", _exhausted(7, tag="CONFIRMADO_PAGO_PENDIENTE")),
        ("wa_ya", _exhausted(7, tag=TAG_UNRESPONSIVE)),
        # REMARKETING = decisión humana del dashboard: no se pisa (L-5).
        ("wa_operador", _exhausted(7, tag="REMARKETING")),
    ]
    assert unresponsive_session_ids(NOW, sessions) == []


def test_escalera_sin_agotar_con_ventana_abierta_no_se_etiqueta():
    meta = _exhausted(7)
    meta["remarketing_touches"] = meta["remarketing_touches"][:3]
    meta["service_window_expires_at_ms"] = NOW + H  # todavía puede seguir
    assert unresponsive_session_ids(NOW, [("wa_a", meta)]) == []


def _stuck(step: int, *, ctwa_open: bool = False, draft: bool = False) -> dict:
    """Escalera a medias con la CSW ya cerrada (p.ej. quiet hours empujaron el
    siguiente toque fuera de las 24h)."""
    meta = {
        "tag": "INTERESADO",
        "last_inbound_at_ms": NOW - 30 * H,
        "service_window_expires_at_ms": NOW - 6 * H,
        "ctwa_window_expires_at_ms": NOW + (H if ctwa_open else -H),
        "remarketing_touches": [
            {"at_ms": NOW - (28 - 2 * i) * H, "kind": "free_form"} for i in range(step)
        ],
    }
    if draft:
        meta["episodes"] = [{"episode_id": "ep_001", "order_draft": {"slots": {"producto": "vela"}}}]
    return meta


def test_escalera_a_medias_sin_ventanas_gratis_tambien_se_etiqueta():
    # Sin ventana de 72h y con la CSW cerrada la central suprime para siempre
    # (no se paga marketing por un lead frío): la escalera no puede continuar,
    # así que el cliente queda igual de "sin respuesta".
    assert unresponsive_session_ids(NOW, [("wa_a", _stuck(4))]) == ["wa_a"]


def test_con_ventana_de_72h_abierta_la_escalera_sigue_por_plantilla():
    assert unresponsive_session_ids(NOW, [("wa_a", _stuck(4, ctwa_open=True))]) == []


def test_con_gancho_transaccional_sigue_por_utility():
    assert unresponsive_session_ids(NOW, [("wa_a", _stuck(4, draft=True))]) == []


def test_sin_ningun_toque_no_es_sin_respuesta():
    # Nunca lo tocamos: no es "no respondió a la escalera", es solo frío.
    assert unresponsive_session_ids(NOW, [("wa_a", _stuck(0))]) == []


@pytest.mark.asyncio
async def test_el_ciclo_escribe_el_tag_en_el_vault(_isolate_vault_dir: Path):
    now = int(time.time() * 1000)
    d = _isolate_vault_dir / "wa_573001234567"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(json.dumps(_exhausted(7, now=now)), encoding="utf-8")

    snapshot = await ActivityEnvironment().run(build_reengagement_snapshot_activity)

    data = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
    assert data["tag"] == "SIN_RESPUESTA"
    assert data["status_history"][-1]["tag"] == "SIN_RESPUESTA"
    assert data["status_history"][-1]["source"] == "reengagement:ladder"
    assert snapshot["marked_unresponsive"] == 1
    assert snapshot["conversations"] == []
