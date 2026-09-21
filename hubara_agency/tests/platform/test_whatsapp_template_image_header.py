"""Plantilla con FOTO en el encabezado — "tu pedido está listo" fuera de 24h.

El pedido suele quedar listo cuando la ventana de servicio ya cerró: solo una
plantilla aprobada puede reabrir la conversación. Meta permite un encabezado
IMAGE en plantillas utility cuya foto se elige AL ENVIAR (media_id), así que
la plantilla lleva la foto real del pedido de ese cliente.

Contrato:
  * El catálogo declara `header_format: image` en la plantilla.
  * El builder emite el componente `header` con la imagen por `id`, y se niega
    a construir el payload si falta la foto (Meta lo rechazaría con 132012) o
    si se pasa una foto a una plantilla sin encabezado.
  * `send_template_to_session` la manda y deja la foto en el historial del chat
    (`image_url`) para que el operador vea lo que recibió el cliente.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.platform.whatsapp.activities import send_template_to_session
from src.platform.whatsapp.dtos import OutboundResult
from src.platform.whatsapp.outbound import build_template_message
from src.platform.whatsapp.templates.registry import (
    _build_registry_from_dict,
    load_template_registry_from_yaml,
)

ORDER_READY = "order_ready_photo_utility_v1"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFINITIONS = _REPO_ROOT / "infra" / "whatsapp-provisioning" / "definitions" / "templates.json"


def _entry(**overrides: Any) -> dict[str, Any]:
    entry = {
        "name": "photo_v1",
        "category": "utility",
        "language": "es_CO",
        "waba_template_name": "photo_v1",
        "semantics": "...",
        "triggers_when_window_expiring": False,
        "body": "Hola, tu pedido {{1}} ya está listo.",
        "header_format": "image",
        "variables": [{"name": "order_reference", "type": "string", "max_length": 60}],
    }
    entry.update(overrides)
    return entry


# ---------- catálogo ----------


def test_catalog_parses_image_header():
    spec = _build_registry_from_dict({"templates": [_entry()]})["photo_v1"]
    assert spec.header_format == "image"


def test_template_without_header_has_no_header_format():
    entry = _entry()
    entry.pop("header_format")
    spec = _build_registry_from_dict({"templates": [entry]})["photo_v1"]
    assert spec.header_format is None


def test_catalog_rejects_unsupported_header_format():
    with pytest.raises(ValueError, match="header_format"):
        _build_registry_from_dict({"templates": [_entry(header_format="gif")]})


def test_order_ready_template_is_operator_only_utility_with_photo_header():
    spec = load_template_registry_from_yaml()[ORDER_READY]
    assert spec.category == "utility"
    assert spec.header_format == "image"
    assert spec.triggers_when_window_expiring is False
    assert spec.requires_episode_stage is None
    assert [v.name for v in spec.variables] == ["order_reference"]


def test_provisioning_definition_declares_image_header_with_example_asset():
    definitions = {d["name"]: d for d in json.loads(_DEFINITIONS.read_text(encoding="utf-8"))}
    header = definitions[ORDER_READY]["header"]
    assert header["format"] == "IMAGE"
    # Meta exige una foto de ejemplo al crear la plantilla (se sube con la
    # Resumable Upload API); el archivo tiene que existir en el repo.
    example = _DEFINITIONS.parent / header["example_file"]
    assert example.is_file()
    assert example.suffix.lower() in {".jpg", ".jpeg", ".png"}


def test_every_catalog_header_matches_its_provisioning_definition():
    definitions = {d["name"]: d for d in json.loads(_DEFINITIONS.read_text(encoding="utf-8"))}
    for spec in load_template_registry_from_yaml().values():
        declared = (definitions[spec.waba_template_name].get("header") or {}).get("format")
        assert (declared or "").lower() == (spec.header_format or ""), spec.name


# ---------- builder ----------


def _photo_spec():
    return _build_registry_from_dict({"templates": [_entry()]})["photo_v1"]


def test_builder_emits_image_header_before_body():
    payload = build_template_message(
        "573001234567",
        _photo_spec(),
        {"order_reference": "#31"},
        header_media_id="MEDIA123",
    )
    assert payload["template"]["components"] == [
        {"type": "header", "parameters": [{"type": "image", "image": {"id": "MEDIA123"}}]},
        {"type": "body", "parameters": [{"type": "text", "text": "#31"}]},
    ]


def test_builder_refuses_photo_template_without_photo():
    with pytest.raises(ValueError, match="foto"):
        build_template_message("573001234567", _photo_spec(), {"order_reference": "#31"})


def test_builder_refuses_photo_on_template_without_header():
    registry = load_template_registry_from_yaml()
    with pytest.raises(ValueError, match="encabezado"):
        build_template_message(
            "573001234567",
            registry["human_followup_utility_v1"],
            {"followup_message": "tu vela"},
            header_media_id="MEDIA123",
        )


# ---------- envío ----------


@pytest.fixture
def isolated_vault(tmp_path, monkeypatch):
    monkeypatch.setattr("src.platform.whatsapp.activities.WORKSPACE_VAULT_DIR", tmp_path)
    folder = tmp_path / "wa_573001234567"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(
        json.dumps({"phone_number_id": "PHONE_TEST"}), encoding="utf-8"
    )
    return tmp_path


def _history(vault: Path) -> list[dict]:
    path = vault / "wa_573001234567" / "sessions" / "wa_573001234567.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_send_passes_photo_and_keeps_it_in_chat_history(isolated_vault, monkeypatch):
    mock_send = AsyncMock(return_value=OutboundResult(wa_message_id="wamid.P", ok=True))
    monkeypatch.setattr(
        "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
    )

    result = await send_template_to_session(
        "wa_573001234567",
        ORDER_READY,
        {"order_reference": "#31"},
        sender="human",
        header_media_id="MEDIA123",
        header_image_url="/api/dashboard/media/wa_573001234567/out-abc.jpg",
    )

    assert result.ok
    assert mock_send.call_args.kwargs["header_media_id"] == "MEDIA123"
    [event] = _history(isolated_vault)
    assert event["image_url"] == "/api/dashboard/media/wa_573001234567/out-abc.jpg"
    assert event["sender"] == "human"
    assert "#31" in event["content"]


@pytest.mark.asyncio
async def test_same_text_with_a_different_photo_is_not_deduped(isolated_vault, monkeypatch):
    """La idempotencia de reintentos no puede tragarse la segunda foto: dos
    pedidos con la misma referencia pero foto distinta son dos envíos."""
    mock_send = AsyncMock(
        side_effect=[
            OutboundResult(wa_message_id="wamid.1", ok=True),
            OutboundResult(wa_message_id="wamid.2", ok=True),
        ]
    )
    monkeypatch.setattr(
        "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
    )

    for media_id in ("MEDIA_A", "MEDIA_B"):
        await send_template_to_session(
            "wa_573001234567", ORDER_READY, {"order_reference": "#31"},
            header_media_id=media_id,
        )

    assert mock_send.await_count == 2
