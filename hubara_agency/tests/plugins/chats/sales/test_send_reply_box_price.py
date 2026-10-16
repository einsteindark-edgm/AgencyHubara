"""`send_reply` no deja salir, sin avisar, que el precio de lo que se cobra por
caja es el de cada vela (`caja_por_unidad`, paquete `ventas-8`).

Caso de producción del 2026-10-16 (bot V2, ventas-7, run de Ventas 738c9ca9,
turnos 4 y 5): el cliente armó un carrito con 1 caja de Velas de deseos
cuadradas · Multicolor ($25.000 la caja de 12), preguntó si cada vela valía eso
y el bot contestó «Así es, *$25.000* cada una» y, en el turno siguiente, «el
precio es por unidad, cada velita vale *$25.000*». El cliente se desanimó y un
colega tuvo que tomar el chat para corregirlo. `precio_ajeno` no lo vio: el
monto es el del catálogo; lo falso es ponérselo a cada vela.

La regla (frases del paquete) propone si una cláusula dice un precio por
unidad sin nombrar la caja y hay en foco un producto que se cobra por caja
(`domain.yaml: sold_by_box`); Jev, con lo que dice el sistema de la caja, dice
si el texto le pone a cada vela el precio de la caja. Sin Jev no revisa (como
las demás reglas de palabras de los hechos).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.tools.fact_check import FactCheck
from src.plugins.chats.agent.sales.tools.reply import SendReplyTool

KEY = "wa_573001111111"
#: Los textos del bot en el caso del 2026-10-16 (turnos 4 y 5).
EACH_ONE = "Así es, *$25.000* cada una. La que armaste va en una unidad, ¿la dejamos así o quieres más?"
PER_UNIT = (
    "Claro, te cuento: el precio es por unidad, cada velita vale *$25.000*.\n\n"
    "Si quieres varias, dime cuántas y te las dejo en el pedido 🤍"
)
#: Lo que el preámbulo y la presentación no deben tocar en estas pruebas.
KEEP = {**{f"preambulo.{n}": 0.01 for n in range(1, 6)}, "variante.ajena": 0.05}


def _product(title: str, handle: str, price: int, options: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        title=title, handle=handle, tags=["Aroma: Noche de Navidad"], options=options or {"Unico": ["Unico"]},
        metadata={}, variants=[SimpleNamespace(prices=[SimpleNamespace(amount=price, currency_code="cop")])],
    )


DESEOS = _product(
    "Velas de deseos cuadradas", "velas-de-deseos-cuadradas", 25_000, {"Color": ["Rojo y verde", "Multicolor"]}
)
PLEGARIA = _product("Plegaria de Luz", "plegaria-de-luz", 45_000)


class _Catalog:
    async def search(self, q: str = "", limit: int = 30):
        return SimpleNamespace(results=[DESEOS, PLEGARIA])


@pytest.fixture(autouse=True)
def _bundle(monkeypatch):
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-8")
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    registry.reset()
    yield
    registry.reset()


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _jev(monkeypatch, answers: dict[str, float]):
    """El bot nuevo: Jev decide, con estas respuestas fijas."""
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    fake = FakePerceptionAdapter({q: TypedAnswer(id=q, kind="noul", p=p) for q, p in {**KEEP, **answers}.items()})

    def _get(_oracle: str):
        return fake

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    monkeypatch.setenv("DECISIONS_BOT", "B")
    return fake


def _tool(vault: Path, *, products: tuple[str, ...] = ("Velas de deseos cuadradas",)) -> SendReplyTool:
    items = [{"producto": name, "cantidad": "1", "color": "Multicolor"} for name in products]
    episode: dict[str, Any] = {"episode_id": "ep_001", "closed_at_ms": None, "order_draft": {"items": items}}
    FilesystemMetadataStore(vault).write(KEY, {"last_inbound_message_id": "wamid.T4", "episodes": [episode]})
    catalog = _Catalog()
    fact_check = FactCheck(vault, catalog, history_reader=lambda _key: [])
    return SendReplyTool(workspace=str(vault), vault_dir=vault, catalog=catalog, fact_check=fact_check)


async def _send(tool: SendReplyTool, ctx: ToolContext, text: str) -> dict:
    return json.loads(await tool.execute_with_context(ctx, text=text))


@pytest.mark.parametrize("text", [PER_UNIT, EACH_ONE])
async def test_the_box_price_said_per_candle_goes_back_with_the_box(
    tmp_path: Path, ctx: ToolContext, monkeypatch, text: str
) -> None:
    fake = _jev(monkeypatch, {"caja_por_unidad.termino_1": 0.95})
    tool = _tool(tmp_path)

    first = await _send(tool, ctx, text)
    again = await _send(tool, ctx, text)

    assert first.get("reply") is None and first["error"] == "fact_check"
    assert first["checks"] == ["caja_por_unidad"]
    message = first["message"]
    assert "Velas de deseos cuadradas" in message
    assert "$25.000 es el precio de la caja de 12" in message and "send_reply" in message
    state, _questions = next(c for c in fake.calls if "caja_por_unidad.termino_1" in c[1])
    assert text.strip() in state and "caja de 12" in state
    assert again.get("reply") == {"text": text}  # insiste: sale (una retención por mensaje)


async def test_jev_lets_the_price_of_each_box_go(tmp_path: Path, ctx: ToolContext, monkeypatch) -> None:
    """«Te quedan a $25.000 cada una» hablando de cajas: Jev lee que es el precio de la caja."""
    _jev(monkeypatch, {"caja_por_unidad.termino_1": 0.04})
    text = "Si llevas dos cajas, te quedan a *$25.000* cada una 🤍"

    assert (await _send(_tool(tmp_path), ctx, text)).get("reply") == {"text": text}


async def test_a_clause_that_names_the_box_is_not_asked(tmp_path: Path, ctx: ToolContext, monkeypatch) -> None:
    fake = _jev(monkeypatch, {})
    text = "Cada caja trae 12 velitas y vale *$25.000* 🤍"

    assert (await _send(_tool(tmp_path), ctx, text)).get("reply") == {"text": text}
    assert not [c for c in fake.calls if any(q.startswith("caja_por_unidad.") for q in c[1])]


async def test_a_product_sold_by_unit_is_not_asked(tmp_path: Path, ctx: ToolContext, monkeypatch) -> None:
    fake = _jev(monkeypatch, {})
    text = "La Plegaria de Luz vale *$45.000* cada una 🤍"

    assert (await _send(_tool(tmp_path, products=("Plegaria de Luz",)), ctx, text)).get("reply") == {"text": text}
    assert not [c for c in fake.calls if any(q.startswith("caja_por_unidad.") for q in c[1])]


async def test_without_jev_it_never_holds(tmp_path: Path, ctx: ToolContext) -> None:
    """Una regla de palabras solo propone: en `reglas` y en sombra no retiene nada."""
    assert (await _send(_tool(tmp_path), ctx, PER_UNIT)).get("reply") == {"text": PER_UNIT}


async def test_ventas_7_does_not_check_it(tmp_path: Path, ctx: ToolContext, monkeypatch) -> None:
    """El paquete publicado no se edita: con ventas-7 (el del caso) el texto sale."""
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    registry.reset()
    _jev(monkeypatch, {"caja_por_unidad.termino_1": 0.95})

    assert (await _send(_tool(tmp_path), ctx, PER_UNIT)).get("reply") == {"text": PER_UNIT}
