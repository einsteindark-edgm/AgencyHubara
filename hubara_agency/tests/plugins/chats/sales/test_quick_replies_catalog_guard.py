"""Guard determinista: `send_quick_replies` NUNCA es un selector de catálogo.

Caso real (run 943e6bff, wa_573229041190): el cliente eligió aroma sin haber
elegido producto entre 4 mostrados. El LLM re-preguntó el producto con
`send_quick_replies` (tope Meta = 3 botones) y recortó la lista a 3 —
"Velón Amor Eterno" desapareció. Los reply buttons no sirven para elegir
productos, aromas, colores ni diseños: eso va SIEMPRE por `present_products`
o `present_variant_picker`. La tool lo rechaza antes de encolar nada.
"""
from __future__ import annotations

import json

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import ProductNotFoundError
from src.plugins.chats.agent.sales.tools.ui_intents import SendQuickRepliesTool


class _Product:
    def __init__(self, handle: str, title: str, tags: list[str]) -> None:
        self.id = f"prod_{handle}"
        self.handle = handle
        self.title = title
        self.tags = tags
        self.categories = ["Decorativas"]
        self.images = []
        self.variants = []
        self.options = None


class _SearchResult:
    def __init__(self, products) -> None:
        self.results = products


_TAGS = ["Aroma: Lavanda", "Aroma: Café", "Aroma: Verde menta", "Color: Rosado", "Color: gris"]
_PRODUCTS = [
    _Product("cubo-love", "Cubo Love", _TAGS),
    _Product("cilindro-love", "Cilindro Love", _TAGS),
    _Product("cubo-de-corazon", "Cubo de corazón", _TAGS),
    _Product("velones", "Velón Amor Eterno", _TAGS),
]


class _FakeCatalog:
    def __init__(self, products=_PRODUCTS, *, broken: bool = False) -> None:
        self._products = products
        self._broken = broken

    async def search(self, q: str = "", limit: int = 30, **_):
        if self._broken:
            raise RuntimeError("catalog down")
        return _SearchResult(self._products)

    async def get_by_handle(self, handle: str):
        for p in self._products:
            if p.handle == handle:
                return p
        raise ProductNotFoundError(handle)


@pytest.fixture
def ctx():
    return ToolContext(
        session_key="wa_test_qr_guard",
        channel="whatsapp",
        chat_id="wa_test_qr_guard",
    )


@pytest.fixture
def vault(tmp_path, ctx):
    v = tmp_path / "isolated_vault"
    (v / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (v / ctx.session_key / "metadata.json").write_text("{}", encoding="utf-8")
    return v


def _intents(vault, ctx) -> list[dict]:
    data = json.loads((vault / ctx.session_key / "metadata.json").read_text(encoding="utf-8"))
    return data.get("pending_ui_intents", [])


async def _call(tool, ctx, body, buttons) -> dict:
    return json.loads(await tool.execute_with_context(ctx, body=body, buttons=buttons))


@pytest.mark.asyncio
async def test_rejects_product_titles_as_buttons_run_943e6bff(ctx, vault):
    """El caso del run: 4 productos mostrados, el LLM manda 3 como botones."""
    tool = SendQuickRepliesTool(workspace=str(vault), catalog=_FakeCatalog())
    result = await _call(tool, ctx, "¿Cuál de los productos te gusta?", [
        {"id": "product.cubo_love", "title": "Cubo Love"},
        {"id": "product.cilindro_love", "title": "Cilindro Love"},
        {"id": "product.cubo_corazon", "title": "Cubo de corazón"},
    ])
    assert result["queued"] is False
    assert result["error"] == "catalog_choice_not_allowed"
    assert "present_products" in result["message"]
    assert set(result["rejected_buttons"]) == {"Cubo Love", "Cilindro Love", "Cubo de corazón"}
    assert _intents(vault, ctx) == [], "no se encola nada — el cliente no ve la lista recortada"


@pytest.mark.asyncio
async def test_rejects_aromas_and_colors_as_buttons(ctx, vault):
    tool = SendQuickRepliesTool(workspace=str(vault), catalog=_FakeCatalog())
    result = await _call(tool, ctx, "¿Qué aroma prefieres?", [
        {"id": "opt.1", "title": "Lavanda"},
        {"id": "opt.2", "title": "café"},          # case/acentos-insensible
        {"id": "opt.3", "title": "Verde Menta"},
    ])
    assert result["queued"] is False
    assert result["error"] == "catalog_choice_not_allowed"
    assert "present_variant_picker" in result["message"]

    result = await _call(tool, ctx, "¿Qué color?", [
        {"id": "a", "title": "Rosado"},
        {"id": "b", "title": "Gris"},
    ])
    assert result["queued"] is False
    assert _intents(vault, ctx) == []


@pytest.mark.asyncio
async def test_rejects_by_id_namespace_even_if_catalog_is_down(ctx, vault):
    """Sin catálogo (o caído) el namespace del id delata el uso indebido."""
    tool = SendQuickRepliesTool(workspace=str(vault), catalog=_FakeCatalog(broken=True))
    result = await _call(tool, ctx, "¿Cuál?", [
        {"id": "product.velones", "title": "Velón Amor Eterno"},
        {"id": "aroma.lavanda", "title": "Lavanda"},
    ])
    assert result["queued"] is False
    assert result["error"] == "catalog_choice_not_allowed"

    tool = SendQuickRepliesTool(workspace=str(vault))  # sin catálogo
    result = await _call(tool, ctx, "¿Cuál?", [
        {"id": "color.rosado", "title": "Rosado"},
        {"id": "color.gris", "title": "Gris"},
    ])
    assert result["queued"] is False


@pytest.mark.asyncio
async def test_binary_decisions_still_pass(ctx, vault):
    """Lo que SÍ es un quick reply: saludo, decisiones binarias, seguir/cambiar."""
    tool = SendQuickRepliesTool(workspace=str(vault), catalog=_FakeCatalog())
    result = await _call(tool, ctx, "¿Por dónde te ayudo?", [
        {"id": "catalog.browse", "title": "Ver catálogo"},
        {"id": "catalog.by_scent", "title": "Por aroma 🌿"},
        {"id": "help.advice", "title": "Asesoría"},
    ])
    assert result["queued"] is True

    # Mencionar una variante en una decisión binaria no es elegir del catálogo.
    result = await _call(tool, ctx, "¿Sigues con lavanda o cambias?", [
        {"id": "order.keep_scent", "title": "Sí, sigo con lavanda"},
        {"id": "order.change_scent", "title": "Cambiar aroma"},
    ])
    assert result["queued"] is True
    assert len(_intents(vault, ctx)) == 2
