"""Índice incremental de reactivación (Punto 2, escala del Window Strategist).

Con millones de conversaciones el scan O(N) del vault por ciclo no aguanta.
El índice es un shortlist LIVIANO (sesión → ventanas + flags de lead) que el
ingest actualiza en cada inbound; el snapshot builder solo abre el metadata
de los candidatos. El índice stale NUNCA es incorrecto — solo shortlist: la
decisión real corre sobre metadata real (pre-filtro) y el gate re-valida al
ejecutar.
"""
from __future__ import annotations

from pathlib import Path

from src.platform.whatsapp.reengagement_index import (
    index_entry_from_metadata,
    load_index,
    shortlist_session_ids,
    update_index_entry,
)

NOW = 1_716_700_000_000
HOUR = 60 * 60 * 1000
DAY = 24 * HOUR


class TestEntry:
    def test_entry_captura_ventanas_y_lead(self):
        meta = {
            "tag": "INTERESADO",
            "service_window_expires_at_ms": NOW + HOUR,
            "ctwa_window_expires_at_ms": NOW - HOUR,
            "last_inbound_at_ms": NOW - 1000,
            "episodes": [
                {"episode_id": "ep_001", "closed_at_ms": None, "order_id": "o1"}
            ],
        }
        e = index_entry_from_metadata(meta, now_ms=NOW)
        assert e["service_window_expires_at_ms"] == NOW + HOUR
        assert e["ctwa_window_expires_at_ms"] == NOW - HOUR
        assert e["tag"] == "INTERESADO"
        assert e["transactional_hook"] is True
        assert e["updated_at_ms"] == NOW


class TestRoundtrip:
    def test_update_y_load(self, tmp_path: Path):
        meta = {"tag": "INTERESADO", "service_window_expires_at_ms": NOW + HOUR}
        update_index_entry(tmp_path, "wa_1", meta, now_ms=NOW)
        update_index_entry(tmp_path, "wa_2", {"tag": "HUMANO"}, now_ms=NOW)
        idx = load_index(tmp_path)
        assert set(idx) == {"wa_1", "wa_2"}
        assert idx["wa_1"]["service_window_expires_at_ms"] == NOW + HOUR

    def test_load_sin_indice_devuelve_none(self, tmp_path: Path):
        assert load_index(tmp_path) is None


class TestShortlist:
    def _idx(self):
        return {
            # ventana abierta → candidato
            "wa_open": {
                "service_window_expires_at_ms": NOW + HOUR,
                "ctwa_window_expires_at_ms": NOW - DAY,
                "tag": "INTERESADO",
                "transactional_hook": False,
                "updated_at_ms": NOW - 5 * DAY,
            },
            # todo cerrado pero CON gancho → candidato (fase B utility)
            "wa_hook": {
                "service_window_expires_at_ms": NOW - DAY,
                "ctwa_window_expires_at_ms": NOW - DAY,
                "tag": "CONFIRMADO_PAGO_PENDIENTE",
                "transactional_hook": True,
                "updated_at_ms": NOW - 5 * DAY,
            },
            # frío y viejo → FUERA (el caso masivo a escala)
            "wa_cold_old": {
                "service_window_expires_at_ms": NOW - 10 * DAY,
                "ctwa_window_expires_at_ms": NOW - 10 * DAY,
                "tag": "NO_ETIQUETADO",
                "transactional_hook": False,
                "updated_at_ms": NOW - 10 * DAY,
            },
            # frío pero entrada JOVEN (<72h) → candidato (el estado puede
            # estar cambiando rápido; el metadata real decide)
            "wa_cold_young": {
                "service_window_expires_at_ms": NOW - HOUR,
                "ctwa_window_expires_at_ms": None,
                "tag": "NO_ETIQUETADO",
                "transactional_hook": False,
                "updated_at_ms": NOW - HOUR,
            },
        }

    def test_shortlist_reglas(self):
        ids = shortlist_session_ids(self._idx(), now_ms=NOW)
        assert sorted(ids) == ["wa_cold_young", "wa_hook", "wa_open"]
