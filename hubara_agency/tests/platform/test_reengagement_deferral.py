"""Un "les escribo la otra semana" PAUSA la escalera de reactivación.

Incidente (session wa_5730…, runs 337efe8c / ee3cec91, 2026-09-21): el cliente
respondió «Sí, pero les escribo la otra semana» y el bot contestó «aquí estaré».
Aun así la escalera le mandó 4 toques en 24h (3 free-form + la plantilla) y el
cliente respondió «No más» — baja definitiva de un lead que iba a volver.

El aplazamiento se lee determinista (sin LLM) y deja una fecha de retoma en el
metadata: hasta esa fecha ningún toque proactivo (pre-filtro del ciclo + gate).
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from temporalio.testing import ActivityEnvironment

from src.platform.whatsapp.activities import check_reengagement_policy_activity
from src.platform.whatsapp.reengagement_deferral import (
    DEFERRAL_KEY,
    DEFERRAL_KIND_DATED,
    DEFERRAL_KIND_OPEN,
    OPEN_DEFERRAL_MS,
    parse_reengagement_deferral,
    reengagement_deferred_until,
    register_reengagement_deferral,
)
from src.plugins.reengagement.agent.cycle.use_cases import (
    build_snapshot_from_sessions,
)

BOGOTA = ZoneInfo("America/Bogota")
H = 60 * 60 * 1000
DAY = 24 * H


def _ms(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=BOGOTA).timestamp() * 1000)


#: Lunes 21-sep-2026 15:03 hora Colombia — el momento del «les escribo la otra semana».
NOW = _ms(2026, 9, 21, 15, 3)


def _until(text: str, now_ms: int = NOW) -> int | None:
    parsed = parse_reengagement_deferral(text, now_ms, BOGOTA)
    return None if parsed is None else parsed.until_ms


# ---------------------------------------------------------------------------
# Lectura del aplazamiento → fecha de retoma (10:00 hora local del cliente)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Si, pero les escribo la otra semana", _ms(2026, 9, 28, 10)),
        ("La próxima semana lo compro", _ms(2026, 9, 28, 10)),
        ("la otra semana", _ms(2026, 9, 28, 10)),
        ("Mañana lo pido", _ms(2026, 9, 22, 10)),
        ("pasado mañana te escribo", _ms(2026, 9, 23, 10)),
        ("El jueves te confirmo", _ms(2026, 9, 24, 10)),
        # Hoy es lunes: "el lunes" es el de la semana que viene.
        ("el lunes les escribo", _ms(2026, 9, 28, 10)),
        ("en 15 días les escribo", _ms(2026, 10, 6, 10)),
        ("en una semana te aviso", _ms(2026, 9, 28, 10)),
        ("el otro mes lo compro", _ms(2026, 10, 1, 10)),
        # Quincena colombiana: 15 y fin de mes — la próxima después de hoy.
        ("cuando me paguen la quincena te escribo", _ms(2026, 9, 30, 10)),
        ("este fin de semana les escribo", _ms(2026, 9, 26, 10)),
    ],
)
def test_aplazamiento_con_fecha_da_la_fecha_de_retoma(text: str, expected: int):
    parsed = parse_reengagement_deferral(text, NOW, BOGOTA)
    assert parsed is not None
    assert parsed.kind == DEFERRAL_KIND_DATED
    assert parsed.until_ms == expected


def test_el_cliente_que_dice_que_el_escribe_pausa_una_semana():
    parsed = parse_reengagement_deferral(
        "Yo les escribo cuando vaya a comprar", NOW, BOGOTA
    )
    assert parsed is not None
    assert parsed.kind == DEFERRAL_KIND_OPEN
    assert parsed.until_ms == NOW + OPEN_DEFERRAL_MS


@pytest.mark.parametrize(
    "text",
    [
        "¿Me llega mañana?",  # pregunta de envío, no aplazamiento
        "Cuánto vale y cuánto tarda el envío a Bogotá?",
        "más tarde",  # aplazamiento del MISMO día: la escalera ya espera 2h
        "voy en camino",
        "la quiero en tamaño grande",
        "",
    ],
)
def test_lo_que_no_es_aplazamiento_con_fecha_no_pausa(text: str):
    assert parse_reengagement_deferral(text, NOW, BOGOTA) is None


# ---------------------------------------------------------------------------
# Registro en el metadata (lo llama el ingest en cada inbound de texto)
# ---------------------------------------------------------------------------


def test_registrar_estampa_la_fecha_y_el_texto():
    meta: dict = {}
    register_reengagement_deferral(
        meta, "Si, pero les escribo la otra semana", now_ms=NOW, tz=BOGOTA
    )
    assert meta[DEFERRAL_KEY]["until_ms"] == _ms(2026, 9, 28, 10)
    assert meta[DEFERRAL_KEY]["at_ms"] == NOW
    assert meta[DEFERRAL_KEY]["kind"] == DEFERRAL_KIND_DATED
    assert "otra semana" in meta[DEFERRAL_KEY]["text"]
    assert reengagement_deferred_until(meta, NOW + H) == _ms(2026, 9, 28, 10)


def test_un_aplazamiento_abierto_no_pisa_la_fecha_que_ya_dio():
    # Secuencia real del incidente: primero la fecha, después «yo les escribo…».
    meta: dict = {}
    register_reengagement_deferral(
        meta, "Si, pero les escribo la otra semana", now_ms=NOW, tz=BOGOTA
    )
    register_reengagement_deferral(
        meta, "Yo les escribo cuando vaya a comprar", now_ms=NOW + 60_000, tz=BOGOTA
    )
    assert meta[DEFERRAL_KEY]["until_ms"] == _ms(2026, 9, 28, 10)


def test_una_cortesia_despues_no_levanta_la_pausa():
    meta: dict = {}
    register_reengagement_deferral(meta, "el jueves te confirmo", now_ms=NOW, tz=BOGOTA)
    register_reengagement_deferral(meta, "Gracias 🙏", now_ms=NOW + 60_000, tz=BOGOTA)
    assert reengagement_deferred_until(meta, NOW + H) == _ms(2026, 9, 24, 10)


def test_si_retoma_la_charla_la_pausa_se_levanta():
    meta: dict = {}
    register_reengagement_deferral(meta, "el jueves te confirmo", now_ms=NOW, tz=BOGOTA)
    register_reengagement_deferral(
        meta, "¿Tienen la de calabaza en tamaño grande?", now_ms=NOW + H, tz=BOGOTA
    )
    assert DEFERRAL_KEY not in meta
    assert reengagement_deferred_until(meta, NOW + 2 * H) is None


def test_la_pausa_vence_en_su_fecha():
    meta: dict = {}
    register_reengagement_deferral(meta, "Mañana lo pido", now_ms=NOW, tz=BOGOTA)
    assert reengagement_deferred_until(meta, _ms(2026, 9, 22, 9, 59)) is not None
    assert reengagement_deferred_until(meta, _ms(2026, 9, 22, 10, 0)) is None


# ---------------------------------------------------------------------------
# Consumidores: pre-filtro del ciclo y gate de la central
# ---------------------------------------------------------------------------


def _deferred_meta(now_ms: int, until_ms: int) -> dict:
    return {
        "tag": "INTERESADO",
        "last_inbound_at_ms": now_ms - 3 * H,
        "service_window_expires_at_ms": now_ms + 21 * H,
        DEFERRAL_KEY: {
            "at_ms": now_ms - 3 * H,
            "until_ms": until_ms,
            "kind": DEFERRAL_KIND_DATED,
            "text": "les escribo la otra semana",
        },
    }


def test_el_prefiltro_del_ciclo_excluye_al_que_aplazo():
    meta = _deferred_meta(NOW, NOW + 5 * DAY)
    snapshot = build_snapshot_from_sessions(NOW, [("wa_aplazo", meta)])
    assert snapshot["conversations"] == []
    assert snapshot["prefiltered"] == {"customer_deferred": 1}


def test_vencida_la_fecha_el_ciclo_lo_vuelve_a_considerar():
    meta = _deferred_meta(NOW, NOW - H)
    snapshot = build_snapshot_from_sessions(NOW, [("wa_aplazo", meta)])
    assert "customer_deferred" not in snapshot["prefiltered"]


@pytest.mark.asyncio
async def test_el_gate_suprime_al_que_aplazo(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_START", "0")
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_END", "24")
    now_ms = int(time.time() * 1000)
    d = _isolate_vault_dir / "wa_573001234567"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(
        json.dumps(_deferred_meta(now_ms, now_ms + 5 * DAY)), encoding="utf-8"
    )
    decision = await ActivityEnvironment().run(
        check_reengagement_policy_activity, "wa_573001234567"
    )
    assert decision.allowed is False
    assert decision.suppress_reason == "customer_deferred"
