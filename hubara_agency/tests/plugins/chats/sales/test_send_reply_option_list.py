"""`send_reply` no deja salir, sin avisar, una lista de aromas o colores
escrita como texto: se la devuelve UNA vez al modelo para que la mande con el
selector.

Laboratorio caso-fotos-0930-r10, 4567 t20 (los dos bots): el cliente escogió
«la de los pajaritos en lila»; el bot anotó el producto y el color y respondió
«¿Qué aroma quieres? Maneja Caballero de la noche, Limoncillo, Lavanda…» como
texto. La protección lo cambió por el selector DESPUÉS del turno (el cliente
lo recibió bien, pero el check marca que tuvo que actuar). La decisión «¿es
una lista para escoger?» ya existe (capacidad `enumeracion`: regla en el bot
actual, Jev en el nuevo); ahora se toma ANTES de enviar y el modelo manda el
selector él mismo, con el producto y lo que iba a decir. La protección queda
como red por si insiste.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.tools.reply import SendReplyTool

KEY = "wa_573001234567"
AROMAS = [
    "Caballero de la noche", "Limoncillo", "Lavanda", "Café", "Sándalo", "Ylan ylang",
    "Coco cremoso", "Frutos rojos", "Verde menta", "Drakar", "Chanel",
]
COLORS = ["Lila", "Azul", "gris", "Rosado", "Blanco", "Negro"]
LIST_TEXT = (
    "Listo, el Velón Gorrión en lila 🌿\n\n¿Qué aroma quieres? Maneja Caballero de la noche, Limoncillo, "
    "Lavanda, Café, Sándalo, Ylan ylang, Coco cremoso, Frutos rojos, Verde menta, Drakar y Chanel."
)


class _Catalog:
    def __init__(self, *, down: bool = False) -> None:
        self._down = down

    async def search(self, q: str = "", limit: int = 30):
        if self._down:
            raise RuntimeError("catálogo caído")
        tags = [f"Aroma: {a}" for a in AROMAS] + [f"Color: {c}" for c in COLORS]
        return SimpleNamespace(results=[SimpleNamespace(handle="velon-gorrion", tags=tags)])


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _tool(vault: Path, *, catalog: _Catalog | None = None, inbound: str = "wamid.T20") -> SendReplyTool:
    FilesystemMetadataStore(vault).write(KEY, {"last_inbound_message_id": inbound})
    return SendReplyTool(workspace=str(vault), vault_dir=vault, catalog=catalog or _Catalog())


async def _send(tool: SendReplyTool, ctx: ToolContext, text: str) -> dict:
    return json.loads(await tool.execute_with_context(ctx, text=text))


async def test_a_list_of_aromas_goes_back_once_to_be_sent_with_the_picker(tmp_path: Path, ctx: ToolContext) -> None:
    tool = _tool(tmp_path)

    first = await _send(tool, ctx, LIST_TEXT)
    again = await _send(tool, ctx, LIST_TEXT)

    assert first.get("reply") is None and first["error"] == "option_list"
    message = first["message"]
    assert "present_variant_picker" in message and '"scent"' in message
    assert "11 aromas" in message
    assert "«Listo, el Velón Gorrión en lila 🌿\n\n¿Qué aroma quieres? Maneja»" in message
    assert again.get("reply") == {"text": LIST_TEXT}  # insiste: sale y la protección hace lo suyo


async def test_each_customer_message_gets_its_own_check(tmp_path: Path, ctx: ToolContext) -> None:
    tool = _tool(tmp_path)
    await _send(tool, ctx, LIST_TEXT)
    store = FilesystemMetadataStore(tmp_path)
    store.write(KEY, {**store.read(KEY), "last_inbound_message_id": "wamid.T21"})

    assert (await _send(tool, ctx, LIST_TEXT)).get("error") == "option_list"


@pytest.mark.parametrize(
    "text",
    [
        "¿Lo quieres en lila o en azul?",  # dos opciones: no es una lista
        "El café y la lavanda son los que más rotan. ¿Cuál te llama?",
        "Listo, anoté el Velón Gorrión en lila con aroma Lavanda 🤍",
    ],
)
async def test_what_is_not_a_list_to_choose_goes_as_is(tmp_path: Path, ctx: ToolContext, text: str) -> None:
    assert (await _send(_tool(tmp_path), ctx, text)).get("reply") == {"text": text}


def test_the_sales_worker_gives_send_reply_the_catalog(tmp_path: Path, monkeypatch) -> None:
    """Sin catálogo la revisión no corre: en producción la tool tiene que
    recibirlo (gotcha #6: ejecutar el lambda caza lo que falte)."""
    monkeypatch.setenv("MEDUSA_BASE_URL", "http://medusa.test")
    monkeypatch.setenv("MEDUSA_ADMIN_TOKEN", "dummy")
    import src.plugins.chats.workers.sales  # noqa: F401  (registra las tools)
    from src.platform.tool_extensions import _EXTENSIONS  # type: ignore

    assert dict(_EXTENSIONS)["sales.send_reply"](tmp_path)._catalog is not None


async def test_without_catalog_the_text_goes_as_is(tmp_path: Path, ctx: ToolContext) -> None:
    """El catálogo caído nunca frena la respuesta: la protección sigue detrás."""
    tool = _tool(tmp_path, catalog=_Catalog(down=True))

    assert (await _send(tool, ctx, LIST_TEXT)).get("reply") == {"text": LIST_TEXT}
