"""`send_reply` no deja salir, sin avisar, un «no lo tenemos» cuando las fotos
del cliente ya se verificaron como productos nuestros.

Laboratorio caso-fotos-0930-r8, 4567 t13, los dos bots: con las cuatro fotos
reconocidas y la nota «FOTOS DEL CLIENTE YA RECONOCIDAS» en el turno, a «me
gustaría esas, pero no están todas» el bot contestó «De las cuatro fotos, la
única que manejamos es el Velón Gorrión. Las otras tres no están en nuestro
catálogo». La nota perdió contra lo que el historial ya decía (el bot lo había
negado antes). Ahora, si la respuesta niega disponibilidad y el episodio
tiene fotos verificadas, la tool la retiene UNA vez por mensaje del cliente y
le recuerda al modelo qué está verificado: la corrige o, si habla de otro
producto, la reenvía igual y sale.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.tools.reply import SendReplyTool

KEY = "wa_573001234567"
DENIAL = (
    "Entiendo. De las cuatro fotos, la única que manejamos es el *Velón Gorrión*, en lila y azul.\n\n"
    "Las otras tres no están en nuestro catálogo, así que no te las puedo ofrecer."
)


def _metadata(*, inbound: str = "wamid.T13", verified: bool = True) -> dict:
    photos = [
        {"media_id": "m1", "description": "dos velas lila y azul con pájaros", "episode_id": "ep_001",
         "product": {"handle": "velon-gorrion", "how": "imagen", "title": "Velón Gorrión"}},
        {"media_id": "m2", "description": "familia con rosas", "episode_id": "ep_001",
         "product": {"handle": "luz-de-belen", "how": "enlace", "title": "Luz de Belén"}},
    ]
    return {
        "episodes": [{"episode_id": "ep_001", "closed_at_ms": None}],
        "last_inbound_message_id": inbound,
        "recent_image_descriptions": photos if verified else [{"media_id": "m9", "description": "una vela"}],
    }


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _tool(vault: Path, metadata: dict) -> SendReplyTool:
    FilesystemMetadataStore(vault).write(KEY, metadata)
    return SendReplyTool(workspace=str(vault), vault_dir=vault)


async def _send(tool: SendReplyTool, ctx: ToolContext, text: str) -> dict:
    return json.loads(await tool.execute_with_context(ctx, text=text))


async def test_a_denial_is_held_once_with_what_is_verified(tmp_path: Path, ctx: ToolContext) -> None:
    tool = _tool(tmp_path, _metadata())

    first = await _send(tool, ctx, DENIAL)
    again = await _send(tool, ctx, DENIAL)

    assert "reply" not in first and first["error"] == "verified_photos"
    assert "«Velón Gorrión»" in first["message"] and "«Luz de Belén»" in first["message"]
    assert again["reply"] == {"text": DENIAL}  # habla de otro producto: sale


async def test_each_customer_message_gets_its_own_check(tmp_path: Path, ctx: ToolContext) -> None:
    tool = _tool(tmp_path, _metadata())
    await _send(tool, ctx, DENIAL)
    store = FilesystemMetadataStore(tmp_path)
    store.write(KEY, {**store.read(KEY), "last_inbound_message_id": "wamid.T14"})

    assert "reply" not in await _send(tool, ctx, "No, esa no la tenemos.")


@pytest.mark.parametrize(
    ("verified", "text"),
    [
        (False, DENIAL),  # sin fotos verificadas no hay nada que recordar
        (True, "Sí, las cuatro son nuestras 🤍"),  # no niega nada
        (True, "El jengibre no está entre los aromas de la Luz Serena."),  # una variante, no un producto
        # r9 4567 t21: un cupón no es un producto (falsa alarma que costó una ronda).
        (True, "Anotado, Lavanda 🤍\n\nSobre el cupón HALLOWEEN50: no existe, no lo tenemos vigente."),
        (True, "Ese color no lo manejamos, pero sí el lila."),
    ],
)
async def test_what_does_not_deny_a_verified_photo_goes_as_is(
    tmp_path: Path, ctx: ToolContext, verified: bool, text: str
) -> None:
    tool = _tool(tmp_path, _metadata(verified=verified))

    assert (await _send(tool, ctx, text)).get("reply") == {"text": text}


# ── Prometer revisar después (r9, 4567 t13, bot nuevo) ─────────────────────
# Sin negar ya nada, el bot contestó «Déjame revisar bien las cuatro que me
# enviaste… Dame un momento y te confirmo» y el turno terminó: después no
# puede escribirle (AGENTS.md ya lo prohibía; nadie lo hacía cumplir).

PROMISE = (
    "Entiendo. Déjame revisar bien las cuatro que me enviaste, porque puede que sí las tengamos con otro "
    "nombre.\n\nDame un momento y te confirmo."
)


async def test_a_promise_to_check_later_is_held_once(tmp_path: Path, ctx: ToolContext) -> None:
    tool = _tool(tmp_path, _metadata(verified=False))

    first = await _send(tool, ctx, PROMISE)
    again = await _send(tool, ctx, PROMISE)

    assert "reply" not in first and first["error"] == "promise_later"
    assert "AHORA" in first["message"]
    assert again["reply"] == {"text": PROMISE}


@pytest.mark.parametrize(
    "text",
    [
        "Te confirmo que sí la tenemos 🤍",
        "Ya te muestro las opciones de aroma.",
        "¿Me confirmas la dirección?",
    ],
)
async def test_what_is_not_a_promise_goes_as_is(tmp_path: Path, ctx: ToolContext, text: str) -> None:
    tool = _tool(tmp_path, _metadata(verified=False))

    assert (await _send(tool, ctx, text)).get("reply") == {"text": text}
