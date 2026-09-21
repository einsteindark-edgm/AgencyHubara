"""Reactivar conversación con plantilla (chats handoff).

Cuando un chat está en ruta humano y la ventana de servicio 24h cerró, el
operador no puede mandar texto libre (409). Estos endpoints le dan la salida:

  * `GET  /api/dashboard/whatsapp-templates` — catálogo con el copy real para
    previsualizar en el modal; marca la plantilla por defecto del operador.
  * `POST /api/dashboard/sessions/{id}/template-messages` — envía la plantilla
    elegida con las variables que escribió el humano.
  * `GET  /api/dashboard/sessions/{id}` expone `service_window_expires_at_ms`
    para que el composer sepa, antes de escribir, que la ventana cerró.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from temporalio.exceptions import ApplicationError

from src.platform.whatsapp.dtos import OutboundResult

FOLLOWUP = "human_followup_utility_v1"
CLOSED_WINDOW = 1_000  # epoch ms en el pasado remoto → ventana cerrada


@pytest.fixture
def client_and_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "test_phone")
    with (
        patch("src.plugins.chats.api.dashboard.WORKSPACE_VAULT_DIR", tmp_path),
        patch("src.plugins.chats.api.dashboard_composition.WORKSPACE_VAULT_DIR", tmp_path),
        patch(
            "src.platform.session_history.store.FilesystemMessageHistoryStore.__init__",
            lambda self, vault_dir: setattr(self, "_vault_dir", tmp_path),
        ),
    ):
        import src.plugins.chats.api.dashboard_composition as comp

        comp._METADATA_STORE = None
        comp._HISTORY_STORE = None
        from src.main import app

        yield TestClient(app), tmp_path


def _write_metadata(vault, session_id: str, data: dict) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


def _read_metadata(vault, session_id: str) -> dict:
    return json.loads((vault / session_id / "metadata.json").read_text(encoding="utf-8"))


def _humano(**extra) -> dict:
    return {"active_route": "humano", "tag": "HUMANO", **extra}


# ---------- catálogo ----------


def test_lists_templates_with_real_body_and_marks_operator_default(client_and_vault):
    client, _ = client_and_vault
    res = client.get("/api/dashboard/whatsapp-templates")

    assert res.status_code == 200
    templates = {t["name"]: t for t in res.json()["templates"]}
    followup = templates[FOLLOWUP]
    assert followup["category"] == "utility"
    assert "{{1}}" in followup["body"]
    assert followup["variables"] == [
        {
            "name": "followup_message",
            "description": "Mensaje del operador sobre el tema pendiente",
            "max_length": 400,
        }
    ]
    assert followup["is_default"] is True
    # Las demás también se ofrecen, pero ninguna es la default.
    assert "order_status_utility_v2" in templates
    assert [t["name"] for t in templates.values() if t["is_default"]] == [FOLLOWUP]


# ---------- envío ----------


def test_sends_template_as_human_and_returns_rendered_text(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_57300", _humano(service_window_expires_at_ms=CLOSED_WINDOW))

    send_mock = AsyncMock(
        return_value=OutboundResult(wa_message_id="wamid.X", ok=True, error=None)
    )
    with patch("src.plugins.chats.api.handoff.send_template_to_session", new=send_mock):
        res = client.post(
            "/api/dashboard/sessions/wa_57300/template-messages",
            json={
                "template_name": FOLLOWUP,
                "variables": {"followup_message": "Ya tenemos las fotos de tu vela"},
                "client_message_id": "cmid-1",
            },
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["sender"] == "human"
    assert body["content"] == (
        "Hola, te escribe Liliana, asesora de Hubara, para hacer seguimiento "
        "a tu consulta Ya tenemos las fotos de tu vela. Quedo atenta a tu respuesta."
    )
    send_mock.assert_awaited_once_with(
        "wa_57300",
        FOLLOWUP,
        {"followup_message": "Ya tenemos las fotos de tu vela"},
        sender="human",
    )
    assert "cmid-1" in _read_metadata(vault, "wa_57300")["sent_human_message_ids"]


def test_retry_with_same_client_message_id_does_not_resend(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_57301", _humano(sent_human_message_ids=["cmid-1"]))

    send_mock = AsyncMock()
    with patch("src.plugins.chats.api.handoff.send_template_to_session", new=send_mock):
        res = client.post(
            "/api/dashboard/sessions/wa_57301/template-messages",
            json={
                "template_name": FOLLOWUP,
                "variables": {"followup_message": "Hola de nuevo."},
                "client_message_id": "cmid-1",
            },
        )

    assert res.status_code == 200
    send_mock.assert_not_awaited()


def test_rejects_when_route_is_not_humano(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_57302", {"active_route": "ventas"})

    send_mock = AsyncMock()
    with patch("src.plugins.chats.api.handoff.send_template_to_session", new=send_mock):
        res = client.post(
            "/api/dashboard/sessions/wa_57302/template-messages",
            json={"template_name": FOLLOWUP, "variables": {"followup_message": "x"}},
        )

    assert res.status_code == 409
    send_mock.assert_not_awaited()


@pytest.mark.parametrize(
    ("template_name", "variables"),
    [
        ("no_existe_v9", {"followup_message": "hola"}),
        (FOLLOWUP, {}),
        (FOLLOWUP, {"followup_message": "   "}),
        (FOLLOWUP, {"followup_message": "línea uno\nlínea dos"}),
        (FOLLOWUP, {"followup_message": "x" * 401}),
    ],
)
def test_rejects_invalid_template_or_variables_without_sending(
    client_and_vault, template_name, variables
):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_57303", _humano())

    send_mock = AsyncMock()
    with patch("src.plugins.chats.api.handoff.send_template_to_session", new=send_mock):
        res = client.post(
            "/api/dashboard/sessions/wa_57303/template-messages",
            json={"template_name": template_name, "variables": variables},
        )

    assert res.status_code == 422
    send_mock.assert_not_awaited()


def test_meta_rejection_surfaces_as_502_and_does_not_mark_sent(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_57304", _humano())

    send_mock = AsyncMock(
        side_effect=ApplicationError(
            "WhatsApp template send failed (non-retryable, code=132001): template no existe",
            non_retryable=True,
        )
    )
    with patch("src.plugins.chats.api.handoff.send_template_to_session", new=send_mock):
        res = client.post(
            "/api/dashboard/sessions/wa_57304/template-messages",
            json={
                "template_name": FOLLOWUP,
                "variables": {"followup_message": "Hola."},
                "client_message_id": "cmid-9",
            },
        )

    assert res.status_code == 502
    assert "132001" in res.json()["detail"]
    assert "cmid-9" not in _read_metadata(vault, "wa_57304").get("sent_human_message_ids", [])


# ---------- detalle de sesión ----------


def test_session_detail_exposes_service_window_expiry(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_57305", _humano(service_window_expires_at_ms=CLOSED_WINDOW))
    _write_metadata(vault, "wa_57306", _humano())

    closed = client.get("/api/dashboard/sessions/wa_57305").json()
    unknown = client.get("/api/dashboard/sessions/wa_57306").json()

    assert closed["service_window_expires_at_ms"] == CLOSED_WINDOW
    assert unknown["service_window_expires_at_ms"] is None


# ---------- plantilla con foto ("tu pedido está listo") ----------

ORDER_READY = "order_ready_photo_utility_v1"
PHOTO_REF = "/api/dashboard/media/wa_57310/out-abc.jpg"


def _with_uploaded_photo() -> dict:
    """Sesión en ruta humano con una foto y un PDF ya subidos en la fase A
    (`POST .../media`)."""
    return _humano(
        outbound_media={
            "MEDIA_OK": {"media_ref": PHOTO_REF, "filename": "out-abc.jpg", "mime": "image/jpeg"},
            "PDF_1": {
                "media_ref": "/api/dashboard/media/wa_57310/out-x.pdf",
                "filename": "out-x.pdf",
                "mime": "application/pdf",
                "kind": "document",
            },
        },
    )


def test_catalog_marks_which_templates_need_a_photo(client_and_vault):
    client, _ = client_and_vault
    res = client.get("/api/dashboard/whatsapp-templates")
    templates = {t["name"]: t for t in res.json()["templates"]}

    assert templates[ORDER_READY]["header_format"] == "image"
    assert templates[FOLLOWUP]["header_format"] is None


def test_sends_order_ready_template_with_the_uploaded_photo(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_57310", _with_uploaded_photo())

    send_mock = AsyncMock(
        return_value=OutboundResult(wa_message_id="wamid.P", ok=True, error=None)
    )
    with patch("src.plugins.chats.api.handoff.send_template_to_session", new=send_mock):
        res = client.post(
            "/api/dashboard/sessions/wa_57310/template-messages",
            json={
                "template_name": ORDER_READY,
                "variables": {"order_reference": "#31"},
                "header_attachment_id": "MEDIA_OK",
                "client_message_id": "cmid-photo",
            },
        )

    assert res.status_code == 200, res.text
    assert res.json()["image_url"] == PHOTO_REF
    assert "#31" in res.json()["content"]
    send_mock.assert_awaited_once_with(
        "wa_57310",
        ORDER_READY,
        {"order_reference": "#31"},
        sender="human",
        header_media_id="MEDIA_OK",
        header_image_url=PHOTO_REF,
    )


@pytest.mark.parametrize(
    ("template_name", "variables", "attachment", "detail"),
    [
        # Pedido listo sin foto: Meta la rechazaría.
        (ORDER_READY, {"order_reference": "#31"}, None, "foto"),
        # Un media_id que no se subió para ESTA sesión no se reenvía a Meta.
        (ORDER_READY, {"order_reference": "#31"}, "MEDIA_AJENO", "desconocido"),
        # Un PDF no sirve como encabezado de imagen.
        (ORDER_READY, {"order_reference": "#31"}, "PDF_1", "imagen"),
        # Una foto en una plantilla que no tiene encabezado.
        (FOLLOWUP, {"followup_message": "hola"}, "MEDIA_OK", "encabezado"),
    ],
)
def test_rejects_wrong_photo_combinations_without_sending(
    client_and_vault, template_name, variables, attachment, detail
):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_57310", _with_uploaded_photo())

    send_mock = AsyncMock()
    body: dict = {"template_name": template_name, "variables": variables}
    if attachment:
        body["header_attachment_id"] = attachment
    with patch("src.plugins.chats.api.handoff.send_template_to_session", new=send_mock):
        res = client.post("/api/dashboard/sessions/wa_57310/template-messages", json=body)

    assert res.status_code == 422, res.text
    assert detail in res.json()["detail"]
    send_mock.assert_not_awaited()
