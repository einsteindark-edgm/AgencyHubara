"""Filtro "Pospuestos" de la bandeja: quién está y cuándo sale.

Pedido del operador (2026-09-22): ver solo a los clientes que dijeron cuándo
retoman («les escribo la otra semana») para que un humano esté pendiente de
qué pasa con ellos. Sale del filtro cuando queda `SIN_RESPUESTA`.

Regla (la decide el backend, el front solo la pinta):
  * aplazó con fecha → está: esperando la fecha, con la cita pendiente y con
    la cita ya enviada (mientras espera respuesta);
  * aplazó sin fecha («yo les escribo…») → está mientras dura la pausa;
  * retomó la charla (el ingest borró el aplazamiento) → sale;
  * quedó `SIN_RESPUESTA` → sale.
"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.plugins.chats.api.dashboard as dash_mod
from src.platform.orders.facts import OrderFactsSnapshot
from src.platform.whatsapp.reengagement_deferral import (
    DEFERRAL_KEY,
    DEFERRAL_KIND_DATED,
    DEFERRAL_KIND_OPEN,
    POSTPONED_APPOINTMENT_DUE,
    POSTPONED_APPOINTMENT_SENT,
    POSTPONED_WAITING,
    postponed_view,
)

BOGOTA = ZoneInfo("America/Bogota")
H = 60 * 60 * 1000


def _ms(y: int, mo: int, d: int, h: int = 0) -> int:
    return int(datetime(y, mo, d, h, tzinfo=BOGOTA).timestamp() * 1000)


SAID = _ms(2026, 9, 21, 15)
UNTIL = _ms(2026, 9, 28, 10)


def _meta(*, kind: str = DEFERRAL_KIND_DATED, tag: str = "INTERESADO", **extra) -> dict:
    entry = {
        "at_ms": SAID,
        "until_ms": UNTIL,
        "kind": kind,
        "text": "Si, pero les escribo la otra semana",
    }
    if kind == DEFERRAL_KIND_DATED:
        entry["resume_label"] = "el lunes 28 de septiembre"
    entry.update(extra)
    return {"tag": tag, DEFERRAL_KEY: entry}


def test_esperando_la_fecha_esta_en_el_filtro():
    view = postponed_view(_meta(), SAID + H)
    assert view == {
        "status": POSTPONED_WAITING,
        "kind": DEFERRAL_KIND_DATED,
        "until_ms": UNTIL,
        "resume_label": "el lunes 28 de septiembre",
        "text": "Si, pero les escribo la otra semana",
        "overdue": False,
    }


def test_llegada_la_fecha_la_cita_esta_pendiente():
    assert postponed_view(_meta(), UNTIL + H)["status"] == POSTPONED_APPOINTMENT_DUE


def test_enviada_la_cita_sigue_en_el_filtro_esperando_respuesta():
    meta = _meta(appointment_touched_at_ms=UNTIL + H)
    assert postponed_view(meta, UNTIL + 2 * H)["status"] == POSTPONED_APPOINTMENT_SENT


def test_sin_respuesta_sale_del_filtro():
    meta = _meta(tag="SIN_RESPUESTA", appointment_touched_at_ms=UNTIL + H)
    assert postponed_view(meta, UNTIL + 8 * H) is None


def test_sin_aplazamiento_no_esta():
    assert postponed_view({"tag": "INTERESADO"}, SAID) is None


def test_sin_fecha_esta_mientras_dura_la_pausa_y_despues_sale():
    meta = _meta(kind=DEFERRAL_KIND_OPEN)
    assert postponed_view(meta, SAID + H)["status"] == POSTPONED_WAITING
    assert postponed_view(meta, UNTIL + H) is None


# --- wiring: el listado de la bandeja lo EMITE --------------------------------


class _NoFacts:
    async def get_facts(self, order_ids):
        return OrderFactsSnapshot(facts={}, unresolved=frozenset(order_ids))


@pytest.fixture
def client_vault(tmp_path):
    app = FastAPI()
    app.include_router(dash_mod.router, prefix="/api/dashboard")
    with patch.object(dash_mod, "WORKSPACE_VAULT_DIR", tmp_path), patch(
        "src.sdk.connectorkit.get_order_facts_port", return_value=_NoFacts()
    ):
        yield TestClient(app), tmp_path


def _seed(vault, session_id: str, metadata: dict) -> None:
    path = vault / session_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
    )


def test_el_listado_emite_el_pospuesto(client_vault):
    client, vault = client_vault
    meta = _meta()
    # Fecha lejana: el test no depende del reloj real.
    meta[DEFERRAL_KEY]["until_ms"] = 4_000_000_000_000
    _seed(vault, "wa_573001112233", meta)
    _seed(vault, "wa_573007654321", {"tag": "INTERESADO"})

    resp = client.get("/api/dashboard/sessions")

    assert resp.status_code == 200
    by_id = {s["session_id"]: s for s in resp.json()["sessions"]}
    assert by_id["wa_573001112233"]["postponed"]["status"] == POSTPONED_WAITING
    assert by_id["wa_573001112233"]["postponed"]["until_ms"] == 4_000_000_000_000
    assert by_id["wa_573007654321"]["postponed"] is None
