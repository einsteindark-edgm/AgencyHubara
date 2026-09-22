"""Pospuesto MANUAL: el operador marca "retomar el <fecha>" desde el dashboard.

Pedido del operador (2026-09-22): poder posponer a mano —también chats que
tomó un humano— para tenerlos en el filtro "Pospuestos" y retomarlos después.
Vencida la fecha la fila se pinta en rojo con aviso (`overdue`).

Diferencias con el aplazamiento que escribe el cliente:
  * lo decide el EQUIPO: ni un mensaje del cliente ni un «les escribo el
    lunes» lo levantan o lo pisan — solo el operador lo quita;
  * NO dispara la cita automática: retoma el humano;
  * vencido sigue en el filtro (en rojo) hasta que el operador actúe, aunque
    el chat esté SIN_RESPUESTA.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.whatsapp.reengagement_deferral import (
    DEFERRAL_KEY,
    DEFERRAL_KIND_MANUAL,
    POSTPONED_OVERDUE,
    POSTPONED_WAITING,
    appointment_pending,
    postponed_view,
    reengagement_deferred_until,
    register_reengagement_deferral,
)
from src.plugins.chats.api import session_actions
from src.plugins.chats.api.session_actions import SessionActionsDeps

BOGOTA = ZoneInfo("America/Bogota")
H = 60 * 60 * 1000
SESSION = "wa_573001234567"


def _ms(y: int, mo: int, d: int, h: int = 0) -> int:
    return int(datetime(y, mo, d, h, tzinfo=BOGOTA).timestamp() * 1000)


# ---------------------------------------------------------------------------
# Endpoint: POST / DELETE /session-actions/{key}/postpone
# ---------------------------------------------------------------------------


@pytest.fixture
def client_vault(tmp_path: Path):
    async def _noop(*_a, **_kw):
        return 0

    deps = SessionActionsDeps(
        vault_dir=tmp_path, catalog=None, order_port=None, flush=_noop,
        notify_episode_closed=_noop,
    )
    app = FastAPI()
    app.include_router(session_actions.router, prefix="/api/chats")
    app.dependency_overrides[session_actions.get_session_actions_deps] = lambda: deps
    return TestClient(app), tmp_path


def _seed(vault: Path, data: dict) -> None:
    d = vault / SESSION
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


def _meta(vault: Path) -> dict:
    return json.loads((vault / SESSION / "metadata.json").read_text(encoding="utf-8"))


_URL = f"/api/chats/session-actions/{SESSION}/postpone"


def test_el_operador_pospone_un_chat_del_humano_hasta_una_fecha(client_vault):
    client, vault = client_vault
    _seed(vault, {"tag": "HUMANO", "active_route": "humano"})

    r = client.post(_URL, json={"date": "2099-01-15", "note": "Llamar para cerrar el pedido"})

    assert r.status_code == 200, r.text
    entry = _meta(vault)[DEFERRAL_KEY]
    assert entry["kind"] == DEFERRAL_KIND_MANUAL
    assert entry["until_ms"] == _ms(2099, 1, 15, 10)
    assert entry["text"] == "Llamar para cerrar el pedido"
    assert entry["resume_label"] == "el jueves 15 de enero"
    # La ruta y la etiqueta del humano NO se tocan.
    assert _meta(vault)["active_route"] == "humano"
    assert _meta(vault)["tag"] == "HUMANO"
    assert r.json()["postponed"]["status"] == POSTPONED_WAITING


def test_no_se_pospone_hacia_el_pasado(client_vault):
    client, vault = client_vault
    _seed(vault, {"tag": "INTERESADO"})
    assert client.post(_URL, json={"date": "2020-01-01"}).status_code == 422


def test_no_se_pospone_un_chat_que_no_existe(client_vault):
    client, _ = client_vault
    assert client.post(_URL, json={"date": "2099-01-15"}).status_code == 404


def test_el_operador_quita_el_pospuesto(client_vault):
    client, vault = client_vault
    _seed(vault, {"tag": "HUMANO", "active_route": "humano"})
    client.post(_URL, json={"date": "2099-01-15"})

    r = client.delete(_URL)

    assert r.status_code == 200, r.text
    assert DEFERRAL_KEY not in _meta(vault)
    assert r.json()["postponed"] is None


# ---------------------------------------------------------------------------
# Semántica del pospuesto manual
# ---------------------------------------------------------------------------


def _manual(until_ms: int, tag: str = "HUMANO") -> dict:
    return {
        "tag": tag,
        DEFERRAL_KEY: {
            "at_ms": until_ms - 5 * 24 * H,
            "until_ms": until_ms,
            "kind": DEFERRAL_KIND_MANUAL,
            "text": "Llamar para cerrar el pedido",
            "resume_label": "el lunes 28 de septiembre",
        },
    }


def test_un_mensaje_del_cliente_no_levanta_el_pospuesto_del_equipo():
    until = _ms(2026, 9, 28, 10)
    meta = _manual(until)
    register_reengagement_deferral(
        meta, "¿Tienen la de calabaza?", now_ms=until - 2 * 24 * H, tz=BOGOTA
    )
    register_reengagement_deferral(
        meta, "mañana les escribo", now_ms=until - 2 * 24 * H, tz=BOGOTA
    )
    assert meta[DEFERRAL_KEY]["kind"] == DEFERRAL_KIND_MANUAL
    assert meta[DEFERRAL_KEY]["until_ms"] == until


def test_el_pospuesto_manual_pausa_el_remarketing_pero_no_agenda_cita():
    until = _ms(2026, 9, 28, 10)
    meta = _manual(until, tag="INTERESADO")
    assert reengagement_deferred_until(meta, until - H) == until
    assert appointment_pending(meta) is False


def test_vencido_sigue_en_el_filtro_en_rojo_aunque_este_sin_respuesta():
    until = _ms(2026, 9, 28, 10)
    view = postponed_view(_manual(until, tag="SIN_RESPUESTA"), until + H)
    assert view is not None
    assert view["status"] == POSTPONED_OVERDUE
    assert view["overdue"] is True
    assert view["kind"] == DEFERRAL_KIND_MANUAL


def test_antes_de_la_fecha_no_esta_vencido():
    until = _ms(2026, 9, 28, 10)
    view = postponed_view(_manual(until), until - H)
    assert view["status"] == POSTPONED_WAITING
    assert view["overdue"] is False


def test_el_aplazamiento_del_cliente_tambien_avisa_cuando_su_fecha_paso():
    until = _ms(2026, 9, 28, 10)
    meta = {
        "tag": "INTERESADO",
        DEFERRAL_KEY: {"at_ms": until - 7 * 24 * H, "until_ms": until, "kind": "fecha",
                       "text": "les escribo la otra semana"},
    }
    assert postponed_view(meta, until + H)["overdue"] is True
    assert postponed_view(meta, until - H)["overdue"] is False
