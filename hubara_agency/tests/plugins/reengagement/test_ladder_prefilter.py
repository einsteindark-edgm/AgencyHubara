"""La escalera de reactivación gobierna el pre-filtro del ciclo.

Incidente runs `01a0b0da`…`01a0b586`: tras el primer gancho el ciclo
re-despachaba la sesión cada 45 min (sin rastro determinista del intento) y el
`cadence_cap` de 2 toques/24h habría cortado la escalera en el tercer peldaño.
"""
from __future__ import annotations

from src.plugins.reengagement.agent.cycle.use_cases import (
    build_snapshot_from_sessions,
)

H = 60 * 60 * 1000
NOW = 1_800_000_000_000


def _lead(silence_h: float, touches_h_ago: list[float] | None = None, **extra) -> dict:
    last_inbound = NOW - int(silence_h * H)
    return {
        "tag": "INTERESADO",
        "service_window_expires_at_ms": last_inbound + 24 * H,
        "last_inbound_at_ms": last_inbound,
        "last_outbound": {"sent_at_ms": last_inbound + 10_000},
        "remarketing_touches": [
            {"at_ms": NOW - int(h * H), "kind": "free_form"}
            for h in (touches_h_ago or [])
        ],
        **extra,
    }


def _ids(snapshot: dict) -> list[str]:
    return sorted(c["session_id"] for c in snapshot["conversations"])


def test_segundo_toque_no_entra_hasta_2h_despues_del_primero():
    snapshot = build_snapshot_from_sessions(NOW, [
        # 1er toque hace 1h → el 2º aún no vence
        ("wa_pronto", _lead(3, touches_h_ago=[1])),
        # 1er toque hace 2h → vence el 2º
        ("wa_vencido", _lead(4, touches_h_ago=[2])),
    ])
    assert _ids(snapshot) == ["wa_vencido"]
    assert snapshot["prefiltered"] == {"ladder_not_due": 1}


def test_escalera_agotada_queda_fuera_del_seed():
    snapshot = build_snapshot_from_sessions(NOW, [
        ("wa_agotado", _lead(23, touches_h_ago=[21, 19, 15, 9, 3])),
    ])
    assert _ids(snapshot) == []
    assert snapshot["prefiltered"] == {"ladder_exhausted": 1}


def test_inbound_nuevo_reinicia_la_escalera_aunque_haya_toques_viejos():
    # El caso del incidente: gancho → el cliente respondió → volvió el ghosting.
    # Los toques anteriores al último inbound no cuentan: toca de nuevo a las 2h.
    snapshot = build_snapshot_from_sessions(NOW, [
        ("wa_revivido", _lead(2.5, touches_h_ago=[20])),
    ])
    assert _ids(snapshot) == ["wa_revivido"]


def test_el_seed_lleva_la_politica_para_que_el_agente_no_corte_la_escalera():
    # GraphAgents aplica `max_touches_per_24h` (default 2) sobre los outbounds
    # proactivos: sin override cortaría la escalera en el 3er peldaño. La
    # autoridad de cadencia es la escalera de hubara.
    snapshot = build_snapshot_from_sessions(NOW, [("wa_x", _lead(3))])
    assert snapshot["policy"]["max_touches_per_24h"] >= 5


def test_entry_lleva_el_numero_de_toque():
    snapshot = build_snapshot_from_sessions(NOW, [
        ("wa_vencido", _lead(4, touches_h_ago=[2])),
    ])
    assert snapshot["conversations"][0]["ladder_step"] == 1
