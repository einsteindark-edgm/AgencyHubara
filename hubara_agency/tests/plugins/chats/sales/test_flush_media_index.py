"""El flush etiqueta cada foto enviada y persiste wamid→foto en metadata.

Caso real (sesión wa_573125671604): el bot mandó 4 fotos de la galería del
Duo Zodiacal; el cliente respondió CITANDO una ("Esta me gusta") y el sistema
no tenía forma de saber cuál — el wa_message_id de cada send_image se
descartaba (solo quedaba el de la última). Contrato nuevo:

  * Galería: cada imagen va con caption = su label de diseño (la primera
    lleva "título · label"); el cliente ve qué es qué.
  * Cada send_image exitoso (galería y product_detail) registra
    `outbound_media_index[wamid] = {handle, title, image_url, label}` en
    metadata.json, capped a los últimos 50.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
    flush_pending_ui_intents_activity,
)

_SESSION_ID = "wa_573000000000"

_ACUARIO = "https://assets.hubara.com.co/Acuario-01KW2SQN75E2KRYVDPA2Z42MHM.webp"
_CANCER = "https://assets.hubara.com.co/cancer-01KW2SQP0Q0VFRJK20Y0N89KJ3.webp"
_LEO = "https://assets.hubara.com.co/leo-01KW2SQSD4RP0KSM9HTJ38QPEF.webp"


def _seed_metadata(vault, intents: list[dict], extra: dict | None = None) -> None:
    session_dir = vault / _SESSION_ID
    session_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "phone_number_id": "phone-1",
        "pending_ui_intents": intents,
        **(extra or {}),
    }
    (session_dir / "metadata.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _read_metadata(vault) -> dict:
    return json.loads(
        (vault / _SESSION_ID / "metadata.json").read_text(encoding="utf-8")
    )


def _gallery_intent() -> dict:
    return {
        "id": "i-1",
        "kind": "product_gallery",
        "params": {
            "handle": "duo-zodiacal",
            "title": "Duo Zodiacal",
            "image_urls": [_ACUARIO, _CANCER, _LEO],
            "images": [
                {"url": _ACUARIO, "label": "Acuario"},
                {"url": _CANCER, "label": "Cancer"},
                {"url": _LEO, "label": "Leo"},
            ],
            "lead_caption": "Duo Zodiacal",
        },
    }


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr("src.platform.config.WORKSPACE_VAULT_DIR", tmp_path)
    import src.platform.whatsapp.client as wa_client

    counter = {"n": 0}

    async def _send_image(*args, **kwargs):
        counter["n"] += 1
        return SimpleNamespace(
            ok=True, wa_message_id=f"wamid.img.{counter['n']}", error=None
        )

    monkeypatch.setattr(wa_client, "send_image", AsyncMock(side_effect=_send_image))
    monkeypatch.setattr(
        wa_client,
        "send_text",
        AsyncMock(
            return_value=SimpleNamespace(
                ok=True, wa_message_id="wamid.txt.1", error=None
            )
        ),
    )
    # Sin pausas entre fotos de galería en tests
    import src.plugins.chats.agent.sales.activities.flush_ui_intents as flush_mod

    monkeypatch.setattr(flush_mod, "_GALLERY_INTER_IMAGE_DELAY_S", 0.0)
    return tmp_path


@pytest.mark.asyncio
async def test_gallery_captions_every_photo_with_its_label(vault):
    _seed_metadata(vault, [_gallery_intent()])
    import src.platform.whatsapp.client as wa_client

    env = ActivityEnvironment()
    sent = await env.run(flush_pending_ui_intents_activity, _SESSION_ID)
    assert sent == 1

    calls = wa_client.send_image.await_args_list
    captions = [c.args[2].caption for c in calls]
    # Primera: título · label; siguientes: label solo — el cliente ve qué es qué
    assert captions == ["Duo Zodiacal · Acuario", "Cancer", "Leo"]


@pytest.mark.asyncio
async def test_gallery_indexes_each_wamid_to_its_photo(vault):
    _seed_metadata(vault, [_gallery_intent()])
    env = ActivityEnvironment()
    await env.run(flush_pending_ui_intents_activity, _SESSION_ID)

    index = _read_metadata(vault)["outbound_media_index"]
    assert index["wamid.img.1"]["label"] == "Acuario"
    assert index["wamid.img.1"]["image_url"] == _ACUARIO
    assert index["wamid.img.1"]["handle"] == "duo-zodiacal"
    assert index["wamid.img.2"]["label"] == "Cancer"
    assert index["wamid.img.3"]["label"] == "Leo"


@pytest.mark.asyncio
async def test_product_detail_indexes_wamid_with_design(vault):
    _seed_metadata(
        vault,
        [
            {
                "id": "i-1",
                "kind": "product_detail",
                "params": {
                    "handle": "duo-zodiacal",
                    "title": "Duo Zodiacal",
                    "image_url": _LEO,
                    "caption": "Duo Zodiacal · $35.000 COP · Leo",
                    "design": "Leo",
                },
            }
        ],
    )
    env = ActivityEnvironment()
    await env.run(flush_pending_ui_intents_activity, _SESSION_ID)

    index = _read_metadata(vault)["outbound_media_index"]
    (entry,) = index.values()
    assert entry["label"] == "Leo"
    assert entry["image_url"] == _LEO
    assert entry["handle"] == "duo-zodiacal"


@pytest.mark.asyncio
async def test_media_index_caps_at_50_evicting_oldest(vault):
    old_index = {
        f"wamid.old.{i}": {
            "handle": "x",
            "title": "X",
            "image_url": "u",
            "label": None,
        }
        for i in range(49)
    }
    _seed_metadata(vault, [_gallery_intent()], extra={"outbound_media_index": old_index})
    env = ActivityEnvironment()
    await env.run(flush_pending_ui_intents_activity, _SESSION_ID)

    index = _read_metadata(vault)["outbound_media_index"]
    assert len(index) == 50
    # Los 3 nuevos están; los 2 más viejos fueron evictados
    assert "wamid.img.3" in index
    assert "wamid.old.0" not in index
    assert "wamid.old.1" not in index
