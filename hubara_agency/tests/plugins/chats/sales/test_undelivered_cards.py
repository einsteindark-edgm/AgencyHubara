"""Una tarjeta que no salió: el turno siguiente lo sabe (celda local de Acktos, 2026-10-23).

Las tools de UI (`tools/ui_intents.py`) le responden al LLM al ENCOLAR
(«Producto enviado al cliente: … NO repitas el precio, ya se mostró en la
imagen»); el envío real lo hace el flush después del texto del turno. En la
celda local de Acktos (el mismo motor) la foto de un producto tenía una URL
http: el cliente de WhatsApp la rechazó (`_validate_media_source`), no salió
nada, y en el turno siguiente el bot le dijo al cliente «Por ahora solo tengo
esa foto…» como si la hubiera visto. El fallo quedaba en
`ui_intents_failures` y nadie lo leía.

Contrato: lo que no le llegó al cliente entra al contexto del turno siguiente,
en lenguaje llano y sin el error técnico, una sola vez.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from exoclaw_temporal.config import BuildPromptInput, LLMConfig, WorkspaceConfig
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales.activities.build_prompt_stage import sales_build_prompt
from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
    flush_pending_ui_intents,
    flush_pending_ui_intents_activity,
)
from src.plugins.chats.agent.sales.use_cases.undelivered_cards import card_words

SID = "wa_573000000777"
PRODUCT = "Vela Cruz de Vida"


def _photo_whatsapp_rejects(**params: Any) -> dict[str, Any]:
    """La foto del caso: la celda local sirve las imágenes por http y el
    cliente de WhatsApp la rechaza antes de llamar a Meta."""
    return {
        "id": "ui_foto_cruz",
        "kind": "product_detail",
        "queued_at_ms": int(time.time() * 1000),
        "params": {
            "handle": "vela-cruz-de-vida",
            "image_url": "http://localhost:9000/static/vela-cruz-de-vida.jpg",
            "caption": f"{PRODUCT} · $23.000 COP",
            "title": PRODUCT,
            **params,
        },
        "analytics": {"component_id": "product_detail", "component_kind": "image"},
    }


def _meta_rejects(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """La URL es buena, pero Meta no acepta el mensaje (`ok=False`)."""
    import src.platform.whatsapp.client as wa_client

    monkeypatch.setattr(
        wa_client,
        "send_image",
        AsyncMock(return_value=SimpleNamespace(ok=False, wa_message_id=None, error="(#131047) Re-engagement message")),
    )
    return _photo_whatsapp_rejects(image_url="https://cdn.test/vela-cruz-de-vida.jpg")


#: Las tres formas en que una tarjeta no sale del flush: el envío lanza, Meta
#: la rechaza o no hay nada que mandar (`_dispatch_intent` devuelve None).
_CARD_THAT_DOES_NOT_GO_OUT = {
    "el envio lanza": lambda monkeypatch: _photo_whatsapp_rejects(),
    "meta la rechaza": _meta_rejects,
    "nada que mandar": lambda monkeypatch: _photo_whatsapp_rejects(image_url=None),
}


def _seed(vault: Path, intents: list[dict[str, Any]]) -> None:
    (vault / SID).mkdir(parents=True, exist_ok=True)
    (vault / SID / "metadata.json").write_text(
        json.dumps({"phone_number_id": "pnid-test", "pending_ui_intents": intents}, ensure_ascii=False),
        encoding="utf-8",
    )


async def _turn_prompt(tmp_path: Path, message: str) -> str:
    """El prompt que arma el turno (todo lo que lee el LLM, en un texto)."""
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    messages = await sales_build_prompt(
        BuildPromptInput(
            session_id=SID,
            message=message,
            channel="whatsapp",
            chat_id=SID,
            llm=LLMConfig(model="fake"),
            workspace=WorkspaceConfig(path=str(workspace)),
            media=None,
            plugin_context=None,
        )
    )
    parts: list[str] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return "\n\n".join(parts)


def _block_about(prompt: str, needle: str) -> str:
    """El párrafo del prompt que nombra `needle`."""
    blocks = [b for b in prompt.split("\n\n") if needle in b]
    assert blocks, f"el contexto del turno no nombra {needle!r}"
    return blocks[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("how", list(_CARD_THAT_DOES_NOT_GO_OUT))
async def test_a_card_that_did_not_go_out_reaches_the_next_turn_as_not_received(
    how: str, tmp_path: Path, _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(_isolate_vault_dir, [_CARD_THAT_DOES_NOT_GO_OUT[how](monkeypatch)])

    report = await ActivityEnvironment().run(flush_pending_ui_intents_activity, SID)
    assert report == [{"kind": "product_detail", "wamid": None, "ok": False}]  # la foto no salió

    note = _block_about(await _turn_prompt(tmp_path, "¿y la foto?"), PRODUCT).lower()
    assert "no le llegó" in note  # el cliente NO la recibió
    assert "la foto" in note  # qué no le llegó, en palabras
    assert "https" not in note and "link" not in note  # sin el error técnico


@pytest.mark.asyncio
async def test_the_notice_reaches_one_turn_even_if_that_turn_restarts(
    tmp_path: Path, _isolate_vault_dir: Path
) -> None:
    """Una sola vez: el turno siguiente lo ve, también si una ráfaga del
    cliente lo reinicia y el prompt se vuelve a armar antes de terminar; el
    flush del final de ese turno lo borra y el turno de después ya no lo ve."""
    _seed(_isolate_vault_dir, [_photo_whatsapp_rejects()])
    await ActivityEnvironment().run(flush_pending_ui_intents_activity, SID)  # turno 1: la foto no sale

    assert PRODUCT in await _turn_prompt(tmp_path, "¿y la foto?")  # turno 2
    assert PRODUCT in await _turn_prompt(tmp_path, "¿y la foto? no me llegó")  # turno 2 reiniciado
    await ActivityEnvironment().run(flush_pending_ui_intents_activity, SID)  # fin del turno 2

    assert PRODUCT not in await _turn_prompt(tmp_path, "bueno, gracias")  # turno 3


@pytest.mark.asyncio
async def test_a_page_of_the_product_list_that_did_not_arrive_reaches_the_next_turn(
    tmp_path: Path, _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La lista de respaldo sale en páginas de a 10: si la 2 no llega, el
    intent cuenta como enviado (la 1 llegó), pero el cliente no vio esos
    productos y la tool le dijo al LLM «Catálogo completo enviado»."""
    import src.platform.whatsapp.client as wa_client

    monkeypatch.delenv("META_CATALOG_ID", raising=False)
    monkeypatch.setattr(
        wa_client,
        "send_interactive_list",
        AsyncMock(
            side_effect=[
                SimpleNamespace(ok=True, wa_message_id="wamid.page1", error=None),
                SimpleNamespace(ok=False, wa_message_id=None, error="(#131000) boom"),
                SimpleNamespace(ok=True, wa_message_id="wamid.page3", error=None),
            ]
        ),
    )
    rows = [{"id": f"vela-{i}", "title": f"Vela {i}"} for i in range(25)]
    _seed(
        _isolate_vault_dir,
        [
            {
                "id": "ui_lista",
                "kind": "products_list",
                "queued_at_ms": int(time.time() * 1000),
                "params": {"intro_text": "Nuestro catálogo:", "sections": [{"title": "Velas", "rows": rows}]},
            }
        ],
    )

    report = await ActivityEnvironment().run(flush_pending_ui_intents_activity, SID)
    assert report == [{"kind": "products_list", "wamid": "wamid.page1", "ok": True}]

    note = _block_about(await _turn_prompt(tmp_path, "no veo todas"), "página 2 de 3").lower()
    assert "no le llegó" in note and "la lista de productos" in note


@pytest.mark.asyncio
async def test_a_vault_that_cannot_be_written_does_not_take_the_turn_down(
    tmp_path: Path, _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Marcar el aviso como dicho es de mejor esfuerzo: si el vault no deja
    escribir, el turno se arma igual y con el aviso (que se repetirá en el
    turno siguiente: mejor eso que un turno caído)."""
    from src.sdk.runtime import FilesystemMetadataStore

    _seed(_isolate_vault_dir, [_photo_whatsapp_rejects()])
    await ActivityEnvironment().run(flush_pending_ui_intents_activity, SID)

    def _disk_full(self: Any, *args: Any, **kwargs: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(FilesystemMetadataStore, "update", _disk_full)

    assert PRODUCT in await _turn_prompt(tmp_path, "¿y la foto?")


@pytest.mark.asyncio
async def test_a_notice_no_turn_closed_does_not_come_back_a_day_later(
    tmp_path: Path, _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un aviso que ningún turno alcanzó a cerrar (el turno de abandono lo ve
    pero no envía ni hace flush; o el LLM se rindió) no vuelve al otro día,
    fuera de contexto: vence con la ventana de 24 h de WhatsApp."""
    from src.plugins.chats.agent.sales.activities import build_prompt_stage

    _seed(_isolate_vault_dir, [_photo_whatsapp_rejects()])
    await ActivityEnvironment().run(flush_pending_ui_intents_activity, SID)
    assert PRODUCT in await _turn_prompt(tmp_path, "[turno de abandono]")  # lo ve; no hay flush

    a_day_later = time.time() + 25 * 3600
    monkeypatch.setattr(build_prompt_stage, "time", SimpleNamespace(time=lambda: a_day_later))
    assert PRODUCT not in await _turn_prompt(tmp_path, "hola, sigo interesada")


@pytest.mark.parametrize(
    ("kind", "params", "words"),
    [
        ("product_detail", {"title": PRODUCT}, f"la foto del producto «{PRODUCT}»"),
        ("product_gallery", {"title": PRODUCT}, f"las fotos del producto «{PRODUCT}»"),
        ("products_list", {}, "la lista de productos"),
        ("categories", {}, "el menú de categorías del catálogo"),
        ("variant_picker", {}, "el selector de opciones"),
        ("quick_replies", {}, "los botones de respuesta"),
        ("shipping_flow", {}, "el formulario de datos de envío"),
        ("shipping_rates", {}, "el mensaje con las tarifas de envío"),
        ("order_confirmation", {}, "el resumen del pedido para confirmar"),
        ("payment_instructions", {}, "los datos de pago"),
        ("contact_card", {}, "la tarjeta de contacto del asesor"),
        ("cta_url", {}, "el botón con el enlace"),
        ("reaction", {}, "tu reacción a su mensaje"),
        # Un tipo que el aviso no conoce igual se avisa.
        ("una_tarjeta_nueva", {}, "un mensaje con botones o tarjeta"),
    ],
)
def test_every_card_the_flush_sends_is_named_in_plain_words(kind: str, params: dict[str, Any], words: str) -> None:
    assert card_words(kind, params) == words


@pytest.mark.asyncio
async def test_a_card_the_operator_sent_from_the_app_is_not_a_notice_for_the_bot(
    tmp_path: Path, _isolate_vault_dir: Path
) -> None:
    """Lo que el operador manda desde la app y no sale, la app ya se lo dice
    (502 `send_failed`): el bot no lo mandó ni se lo prometió al cliente."""
    _seed(_isolate_vault_dir, [_photo_whatsapp_rejects()])

    sent = await flush_pending_ui_intents(SID, only_ids={"ui_foto_cruz"}, operator_tool="present_product_detail")

    assert sent == 0
    assert PRODUCT not in await _turn_prompt(tmp_path, "¿y la foto?")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("how", "event", "why"),
    [
        ("el envio lanza", "flush_ui_intents.dispatch_failed", "link debe ser HTTPS público"),
        ("meta la rechaza", "flush_ui_intents.send_failed", "(#131047) Re-engagement message"),
    ],
)
async def test_the_failure_log_says_why_and_for_whom(
    how: str,
    event: str,
    why: str,
    _isolate_vault_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """El error va en el MENSAJE del aviso: el puente a loguru
    (`platform/logging.InterceptHandler`) solo reenvía `record.getMessage()` y
    los `extra` se pierden. En la celda local el log decía
    `flush_ui_intents.dispatch_failed` sin el porqué."""
    _seed(_isolate_vault_dir, [_CARD_THAT_DOES_NOT_GO_OUT[how](monkeypatch)])
    caplog.set_level(logging.WARNING)

    await ActivityEnvironment().run(flush_pending_ui_intents_activity, SID)

    [message] = [r.getMessage() for r in caplog.records if event in r.getMessage()]
    assert why in message and SID in message
