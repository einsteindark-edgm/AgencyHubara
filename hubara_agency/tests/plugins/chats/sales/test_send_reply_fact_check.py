"""`send_reply` no deja salir, sin avisar, aromas o colores de otro producto ni
presentaciones que el producto no tiene: el texto vuelve UNA vez al modelo con
lo que el producto del que habla sí tiene.

Incidente 2026-10-13 (bot V2, ventas-6): el cliente armó un carrito con 2 Velas
de deseos cuadradas y el bot escribió «vienen en Chanel, Drakar y Ylang
Ylang» dos turnos seguidos. Drakar e Ylang Ylang son de la Plegaria de Luz
(salió en la misma búsqueda); el producto tiene un solo aroma y un colega lo
corrigió en el chat. La decisión «¿le ofrece a ese producto un aroma de
otro?» es la capacidad `aroma_ajeno` (paquete `ventas-7`): la regla la toma
en el bot actual y Jev quita los avisos falsos en el nuevo; `color_ajeno` es la
misma con los colores; `variante_ajena` la lee Jev con la ficha del producto.
Las tres van en UNA retención por mensaje del cliente. Con un paquete anterior
(ventas-6) no se revisa nada: las decisiones todavía no existen.
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
PLEGARIA_AROMAS = [
    "Caballero de la noche", "Limoncillo", "Lavanda", "Café", "Sándalo", "Ylan ylang",
    "Coco cremoso", "Frutos rojos", "Verde menta", "Drakar", "Chanel",
]
COLORS = ["gris", "Amarillo", "Café", "Lila", "Azul"]
INCIDENT = "¿Qué aroma prefieres? Las de deseos cuadradas vienen en *Chanel*, *Drakar* y *Ylang Ylang*."


def _product(title: str, handle: str, aromas: list[str], colors: list[str] = (), options: dict | None = None,
             metadata: dict | None = None, price: int | None = None) -> SimpleNamespace:
    tags = [f"Aroma: {a}" for a in aromas] + [f"Color: {c}" for c in colors]
    prices = [SimpleNamespace(amount=price, currency_code="cop")] if price else []
    return SimpleNamespace(title=title, handle=handle, tags=tags, options=options or {"Unico": ["Unico"]},
                           metadata=metadata or {}, variants=[SimpleNamespace(prices=prices)])


DESEOS = _product(
    "Velas de deseos cuadradas", "velas-de-deseos-cuadradas", ["Noche de Navidad"],
    options={"Color": ["Rojo y verde", "Multicolor"]}, price=25_000,
)
DUO = _product("Duo Zodiacal", "duo-zodiacal", ["Lavanda"], ["Lila"], options={"Signo": ["Aries", "Leo"]})
PLEGARIA = _product("Plegaria de Luz", "plegaria-de-luz", PLEGARIA_AROMAS, COLORS)
SIN_AROMAS = _product("Hellowen", "hellowen", [])


class _Catalog:
    def __init__(self, *products: SimpleNamespace, down: bool = False) -> None:
        self._products = list(products) or [DESEOS, PLEGARIA]
        self._down = down

    async def search(self, q: str = "", limit: int = 30):
        if self._down:
            raise RuntimeError("catálogo caído")
        return SimpleNamespace(results=self._products)


@pytest.fixture(autouse=True)
def _bundle(monkeypatch):
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    registry.reset()
    yield
    registry.reset()


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _tool(vault: Path, *, products: list[str] = ("Velas de deseos cuadradas",), catalog: _Catalog | None = None,
          inbound: str = "wamid.T6", quantity: str = "2", order_id: str | None = None, profile: str | None = None,
          **sources: Any) -> SendReplyTool:
    items = [{"producto": name, "cantidad": quantity} for name in products]
    episode = {"episode_id": "ep_001", "closed_at_ms": None, "order_draft": {"items": items}}
    if order_id:
        episode["order_id"] = order_id
    metadata: dict[str, Any] = {"last_inbound_message_id": inbound, "episodes": [episode]}
    if profile:
        metadata["profile"] = {"name": profile}
    FilesystemMetadataStore(vault).write(KEY, metadata)
    catalog = catalog or _Catalog()
    fact_check = FactCheck(vault, catalog, **sources) if sources else None
    return SendReplyTool(workspace=str(vault), vault_dir=vault, catalog=catalog, fact_check=fact_check)


async def _send(tool: SendReplyTool, ctx: ToolContext, text: str) -> dict:
    return json.loads(await tool.execute_with_context(ctx, text=text))


async def test_the_incident_goes_back_once_with_the_real_aroma(tmp_path: Path, ctx: ToolContext) -> None:
    tool = _tool(tmp_path)

    first = await _send(tool, ctx, INCIDENT)
    again = await _send(tool, ctx, INCIDENT)

    assert first.get("reply") is None and first["error"] == "fact_check"
    assert first["checks"] == ["aroma_ajeno"]  # la traza dice qué revisión lo retuvo
    message = first["message"]
    assert "Velas de deseos cuadradas" in message
    assert "«Chanel», «Drakar» y «Ylan ylang»" in message
    assert "Noche de Navidad" in message and "send_reply" in message
    assert again.get("reply") == {"text": INCIDENT}  # insiste: sale (una retención por mensaje)


async def test_the_real_aroma_goes_as_is(tmp_path: Path, ctx: ToolContext) -> None:
    text = "Las de deseos cuadradas vienen en aroma *Noche de Navidad* 🎄 ¿Te las dejo así?"

    assert (await _send(_tool(tmp_path), ctx, text)).get("reply") == {"text": text}


async def test_an_aroma_of_a_product_the_text_names_is_not_foreign(tmp_path: Path, ctx: ToolContext) -> None:
    """El pedido es de las Velas de deseos, pero el texto habla de la Plegaria
    de Luz por su nombre: sus aromas son de ella."""
    text = "Las de deseos vienen en *Noche de Navidad*. La Plegaria de Luz sí viene en *Drakar* y *Lavanda*."

    assert (await _send(_tool(tmp_path), ctx, text)).get("reply") == {"text": text}


async def test_without_an_order_the_product_the_text_names_is_the_focus(tmp_path: Path, ctx: ToolContext) -> None:
    text = "Las Velas de deseos cuadradas vienen en *Drakar*."

    held = await _send(_tool(tmp_path, products=()), ctx, text)

    assert held["error"] == "fact_check" and "«Drakar»" in held["message"]


async def test_without_a_product_in_focus_nothing_is_judged(tmp_path: Path, ctx: ToolContext) -> None:
    text = "Manejamos Chanel, Drakar y Lavanda en casi todas las velas."

    assert (await _send(_tool(tmp_path, products=()), ctx, text)).get("reply") == {"text": text}


async def test_a_product_without_aromas_in_the_catalog_is_not_judged(tmp_path: Path, ctx: ToolContext) -> None:
    tool = _tool(tmp_path, products=("Hellowen",), catalog=_Catalog(SIN_AROMAS, PLEGARIA))

    assert (await _send(tool, ctx, "La Hellowen viene en Drakar.")).get("reply") == {"text": "La Hellowen viene en Drakar."}


async def test_each_customer_message_gets_its_own_check(tmp_path: Path, ctx: ToolContext) -> None:
    tool = _tool(tmp_path)
    await _send(tool, ctx, INCIDENT)
    store = FilesystemMetadataStore(tmp_path)
    store.write(KEY, {**store.read(KEY), "last_inbound_message_id": "wamid.T7"})

    assert (await _send(tool, ctx, INCIDENT)).get("error") == "fact_check"


async def test_with_a_bundle_older_than_the_decision_nothing_is_checked(tmp_path: Path, ctx: ToolContext,
                                                                        monkeypatch) -> None:
    """Hasta que la tienda promueva `ventas-7` (Terraform), el bot sigue igual."""
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-6")
    registry.reset()

    assert (await _send(_tool(tmp_path), ctx, INCIDENT)).get("reply") == {"text": INCIDENT}


async def test_a_broken_bundle_never_breaks_the_text(tmp_path: Path, ctx: ToolContext, monkeypatch) -> None:
    """Premortem del PR #426: `facts_in_bundle()` iba fuera del `try`; un
    paquete roto tumbaba las tarjetas."""
    from src.plugins.chats.agent.sales.tools import fact_check

    def _broken() -> bool:
        raise RuntimeError("paquete roto")

    monkeypatch.setattr(fact_check, "facts_in_bundle", _broken)
    facts = FactCheck(tmp_path, _Catalog())
    FilesystemMetadataStore(tmp_path).write(KEY, {"last_inbound_message_id": "wamid.T6"})

    assert await facts.held(KEY, INCIDENT, tool="present_products") is None


async def test_without_catalog_the_text_goes_as_is(tmp_path: Path, ctx: ToolContext) -> None:
    tool = _tool(tmp_path, catalog=_Catalog(down=True))

    assert (await _send(tool, ctx, INCIDENT)).get("reply") == {"text": INCIDENT}


async def test_with_jev_the_decision_is_logged_for_the_conversation(tmp_path: Path, ctx: ToolContext,
                                                                       monkeypatch) -> None:
    """Bot nuevo (Jev): Calidad LLM → «Decisiones de Jev» la muestra en el turno
    (con `reglas` no hay fila: decidió la regla de hoy)."""
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    answers = {f"aroma_ajeno.termino_{n}": TypedAnswer(id=f"aroma_ajeno.termino_{n}", kind="noul", p=0.96) for n in (1, 2, 3)}
    fake = FakePerceptionAdapter(answers)

    def _get(_oracle: str):
        return fake

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    monkeypatch.setenv("DECISIONS_BOT", "B")

    held = await _send(_tool(tmp_path), ctx, INCIDENT)

    assert held["error"] == "fact_check"
    rows = [json.loads(line) for line in (tmp_path / KEY / "evals" / "decisions.jsonl").read_text().splitlines()]
    [row] = [r for r in rows if r["capability"] == "aroma_ajeno"]
    assert (row["value"], row["by"], row["bundle"]) == (["Chanel", "Drakar", "Ylan ylang"], "jev", "ventas-7@7")


# ── Con la regla sola (`reglas`/sombra: así nace la decisión) ─────────────


@pytest.mark.parametrize(
    "text",
    [
        "Las de deseos no vienen en Drakar, solo en Noche de Navidad.",
        "Te recomiendo también la plegaria: viene en Drakar y Lavanda.",
    ],
)
async def test_with_the_rule_alone_a_denial_or_another_product_goes_as_is(tmp_path: Path, ctx: ToolContext,
                                                                         text: str) -> None:
    assert (await _send(_tool(tmp_path), ctx, text)).get("reply") == {"text": text}


async def test_with_the_rule_alone_a_false_alarm_costs_one_resend(tmp_path: Path, ctx: ToolContext) -> None:
    """«la noche de Navidad» es también el aroma de las velas de deseos: con
    un pedido de Plegaria y una oración que ofrece, la regla lo marca (Jev lo
    quitaría). El mensaje le dice al modelo que, si no se lo ofrecía, reenvíe
    el mismo texto."""
    text = "Te las dejo listas para la noche de Navidad 🎄"
    tool = _tool(tmp_path, products=("Plegaria de Luz",), catalog=_Catalog(DESEOS, PLEGARIA))

    first = await _send(tool, ctx, text)
    again = await _send(tool, ctx, text)

    assert first["error"] == "fact_check" and "con el mismo texto" in first["message"]
    assert again.get("reply") == {"text": text}


@pytest.mark.parametrize(
    ("text", "products"),
    [
        # Premortem del PR #426: la regla sola marcaba lo que el texto nombra
        # sin ofrecerlo. Ahora solo juzga las oraciones que ofrecen algo.
        ("Tu Plegaria de Luz te llega antes de la noche de Navidad 🎄", ("Plegaria de Luz",)),
        ("El Chanel tiene notas de sándalo, ylang ylang y lavanda 🤍", ("Velas de deseos cuadradas",)),
        ("Te las mandamos en una caja azul con moño amarillo 🎁", ("Velas de deseos cuadradas",)),
        ("¿Te gustaría agregar una vela de lavanda? 🤍", ("Velas de deseos cuadradas",)),
    ],
)
async def test_with_the_rule_alone_what_is_named_but_not_offered_goes_as_is(
    tmp_path: Path, ctx: ToolContext, text: str, products: tuple
) -> None:
    tool = _tool(tmp_path, products=products, catalog=_Catalog(DESEOS, PLEGARIA))

    assert (await _send(tool, ctx, text)).get("reply") == {"text": text}


async def test_the_customer_name_is_not_a_color(tmp_path: Path, ctx: ToolContext) -> None:
    """«Gracias Lila, te las dejo en Noche de Navidad»: Lila es la clienta."""
    text = "Gracias Lila, te las dejo en Noche de Navidad 🎄"

    named = await _send(_tool(tmp_path, profile="Lila Gómez"), ctx, text)
    unknown = await _send(_tool(tmp_path / "otra", inbound="wamid.T9"), ctx, text)

    assert named.get("reply") == {"text": text}
    assert unknown.get("error") == "fact_check" and "«Lila»" in unknown["message"]


async def test_with_jev_everything_the_text_names_is_judged(tmp_path: Path, ctx: ToolContext, monkeypatch) -> None:
    """La regla sola es prudente; Jev ve también lo que no viene con una frase
    de oferta y decide (aquí: sí se la ofrece a ese producto)."""
    _jev(monkeypatch, {"aroma_ajeno.termino_1": 0.9})
    text = "Puedes agregar lavanda a tus velas de deseos 🤍"

    held = await _send(_tool(tmp_path), ctx, text)

    assert held.get("error") == "fact_check" and "«Lavanda»" in held["message"]


# ── Colores y presentaciones (color_ajeno, variante_ajena) ─────────────────


def _jev(monkeypatch, answers: dict[str, float]):
    """El bot nuevo: Jev decide, con estas respuestas fijas (las demás, sí a todo)."""
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    fake = FakePerceptionAdapter({q: TypedAnswer(id=q, kind="noul", p=p) for q, p in answers.items()})

    def _get(_oracle: str):
        return fake

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    monkeypatch.setenv("DECISIONS_BOT", "B")
    return fake


async def test_colors_of_another_product_go_back_with_the_real_ones(tmp_path: Path, ctx: ToolContext) -> None:
    text = "Te las dejo en azul y lila, ¿te sirve?"

    held = await _send(_tool(tmp_path), ctx, text)

    assert held["error"] == "fact_check"
    assert "«Azul» y «Lila» como colores de Velas de deseos cuadradas" in held["message"]
    assert "Sus colores son: Rojo y verde, Multicolor." in held["message"]


async def test_a_foreign_aroma_and_color_go_back_in_one_hold(tmp_path: Path, ctx: ToolContext) -> None:
    text = "Las de deseos te las dejo en Drakar y en azul."
    tool = _tool(tmp_path)

    first = await _send(tool, ctx, text)
    again = await _send(tool, ctx, text)

    assert "«Drakar» como aroma y «Azul» como color" in first["message"]
    assert again.get("reply") == {"text": text}


async def test_the_presentation_the_product_has_goes_as_is(tmp_path: Path, ctx: ToolContext) -> None:
    text = "Ese diseño viene en dos presentaciones: Multicolor o Rojo y verde. ¿Cuál prefieres?"

    assert (await _send(_tool(tmp_path), ctx, text)).get("reply") == {"text": text}


async def test_without_jev_an_invented_sign_goes_as_is(tmp_path: Path, ctx: ToolContext) -> None:
    """`variante_ajena` no tiene regla: sin Jev no revisa (la sombra mide, canary actúa)."""
    tool = _tool(tmp_path, products=("Duo Zodiacal",), catalog=_Catalog(DUO, PLEGARIA))

    assert (await _send(tool, ctx, "Lo tenemos en Ofiuco ✨")).get("reply") == {"text": "Lo tenemos en Ofiuco ✨"}


async def test_with_jev_an_invented_sign_goes_back_with_the_real_ones(tmp_path: Path, ctx: ToolContext,
                                                                    monkeypatch) -> None:
    fake = _jev(monkeypatch, {"variante.ajena": 0.95})
    tool = _tool(tmp_path, products=("Duo Zodiacal",), catalog=_Catalog(DUO, PLEGARIA))

    held = await _send(tool, ctx, "Lo tenemos en Ofiuco ✨")

    assert held["error"] == "fact_check"
    assert "Le ofreciste a Duo Zodiacal una presentación, diseño o tamaño que no tiene" in held["message"]
    assert "Sus presentaciones son: Aries, Leo." in held["message"]
    [state] = [state for state, questions in fake.calls if questions == ("variante.ajena",)]
    assert "Presentaciones que tiene: Aries, Leo." in state


async def test_a_single_presentation_is_asked_only_when_the_text_names_a_presentation(
    tmp_path: Path, ctx: ToolContext, monkeypatch
) -> None:
    """La Plegaria tiene una sola presentación: «viene en Lavanda» no gasta la
    llamada; «viene en tamaño grande» se le pregunta a Jev con su ficha."""
    fake = _jev(monkeypatch, {"variante.ajena": 0.9})
    tool = _tool(tmp_path, products=("Plegaria de Luz",), catalog=_Catalog(DESEOS, PLEGARIA))

    await _send(tool, ctx, "La Plegaria viene en Lavanda.")
    assert all("variante.ajena" not in questions for _state, questions in fake.calls)

    held = await _send(tool, ctx, "La Plegaria también viene en tamaño grande.")
    assert held["error"] == "fact_check" and "Viene en una sola presentación" in held["message"]



async def test_a_text_that_offers_nothing_does_not_ask_about_presentations(
    tmp_path: Path, ctx: ToolContext, monkeypatch
) -> None:
    """Premortem del PR #426: con un producto de presentaciones en foco, cada
    texto («Quedo atenta») era una llamada a Jev (y en sombra, espera)."""
    fake = _jev(monkeypatch, {"variante.ajena": 0.05})
    tool = _tool(tmp_path, products=("Duo Zodiacal",), catalog=_Catalog(DUO, PLEGARIA))

    await _send(tool, ctx, "Con gusto, quedo atenta a lo que necesites 😊")
    assert all("variante.ajena" not in questions for _state, questions in fake.calls)

    await _send(tool, ctx, "Lo tenemos en Ofiuco ✨")
    assert any("variante.ajena" in questions for _state, questions in fake.calls)


# ── precio_ajeno ───────────────────────────────────────────────────────────


async def test_a_price_the_catalog_does_not_have_goes_back_with_the_real_one(tmp_path: Path, ctx: ToolContext) -> None:
    """Turno 3 del incidente: «las cuadradas en $23.000» (catálogo: $25.000)."""
    text = "Las cuadradas te quedan en $23.000 cada una 🤍"

    held = await _send(_tool(tmp_path), ctx, text)

    assert held["error"] == "fact_check"
    assert "«$23.000»" in held["message"] and "Velas de deseos cuadradas cuesta $25.000" in held["message"]


async def test_a_wrong_price_is_corrected_head_on_with_the_order_total(tmp_path: Path, ctx: ToolContext) -> None:
    """Laboratorio del 2026-10-14 (Jev real, prefijo del incidente): el cliente
    preguntó «entonces son $46.000 las dos, ¿cierto?»; con la retención el bot
    corrigió el monto pero empezó con «Así es, $50.000» y nunca dijo que los
    $23.000 de antes estaban mal. El mensaje le da el total del pedido y le
    pide corregirlo de frente."""
    held = await _send(_tool(tmp_path), ctx, "Así es, $46.000 por las dos velas 🤍")

    message = held["message"]
    assert "este pedido (2 × $25.000) suma $50.000 en productos" in message
    assert "no empieces con «así es»" in message and "ese valor estaba mal" in message


async def test_the_catalog_price_by_the_quantity_goes_as_is(tmp_path: Path, ctx: ToolContext) -> None:
    text = "Las dos cuadradas te quedan en $50.000 🤍"

    assert (await _send(_tool(tmp_path), ctx, text)).get("reply") == {"text": text}



# ── pago, estado del pedido, cupones y lo que niega ────────────────────────

NOW_MS = 1_791_900_000_000  # 2026-10-13


class _OrderFacts:
    """OrderFacts falso: el pedido #67 en preparación y sin pago."""

    def __init__(self, stage: str = "preparing", pay_status: str = "pending", stale: bool = False,
                 total_cop: int = 66_940) -> None:
        self.calls: list[list[str]] = []
        self.invalidated: list[str | None] = []
        self._stale = stale
        self._fact = SimpleNamespace(
            order_id="order_67", display_id="67", stage=stage, pay_status=pay_status, total_cop=total_cop
        )

    def invalidate(self, order_id: str | None = None) -> None:
        self.invalidated.append(order_id)

    async def get_facts(self, ids):
        self.calls.append(list(ids))
        return SimpleNamespace(facts={"order_67": self._fact}, unresolved=frozenset(), stale=self._stale)


class _Promotions:
    def __init__(self, down: bool = False) -> None:
        self.calls = 0
        self._down = down

    async def list_active(self):
        from src.sdk.connectorkit import PromotionDTO

        self.calls += 1
        if self._down:
            raise RuntimeError("Medusa no respondió")
        base = dict(
            id="p", discount_type="percentage", currency_code=None, target_type="items", allocation="across",
            max_quantity=None, product_ids=(), variant_ids=(), collection_ids=(), min_subtotal_cop=None,
            is_automatic=False, status="active", starts_at_ms=None, budget_type=None, budget_limit=None,
            budget_used=None, description=None,
        )
        return [
            PromotionDTO(**{**base, "id": "amor", "code": "AMOR2026", "value": 10, "ends_at_ms": NOW_MS - 86_400_000}),
            PromotionDTO(**{**base, "id": "bienvenida", "code": "BIENVENIDA", "value": 5, "ends_at_ms": None}),
        ]


async def test_cash_on_delivery_for_a_small_order_goes_back(tmp_path: Path, ctx: ToolContext, monkeypatch) -> None:
    _jev(monkeypatch, {"pago_ajeno.termino_1": 0.95})

    held = await _send(_tool(tmp_path, quantity="1"), ctx, "Claro, puedes pagar contra entrega 🤍")

    assert held["error"] == "fact_check"
    assert "este pedido suma $25.000 en productos y contra entrega es desde $45.000" in held["message"]


async def test_cash_on_delivery_for_an_order_that_reaches_the_minimum_goes_as_is(tmp_path: Path, ctx: ToolContext) -> None:
    text = "Claro, puedes pagar contra entrega 🤍"

    assert (await _send(_tool(tmp_path, quantity="2"), ctx, text)).get("reply") == {"text": text}


async def test_without_jev_the_keyword_facts_never_hold(tmp_path: Path, ctx: ToolContext) -> None:
    """Pago, estado, cupón y negación no actúan con la regla sola (`reglas`, sombra)."""
    text = "Claro, puedes pagar contra entrega 🤍"

    assert (await _send(_tool(tmp_path, quantity="1"), ctx, text)).get("reply") == {"text": text}


async def test_a_payment_the_order_does_not_have_goes_back(tmp_path: Path, ctx: ToolContext, monkeypatch) -> None:
    _jev(monkeypatch, {"estado_ajeno.termino_1": 0.95})
    facts = _OrderFacts()
    tool = _tool(tmp_path, order_id="order_67", order_facts=facts)

    held = await _send(tool, ctx, "¡Listo! Tu pago quedó confirmado 🤍")

    assert held["error"] == "fact_check"
    assert "el pago del pedido #67 está pendiente" in held["message"]
    assert facts.calls == [["order_67"]]


async def test_the_order_is_not_read_when_the_text_does_not_talk_about_it(tmp_path: Path, ctx: ToolContext) -> None:
    facts = _OrderFacts()
    tool = _tool(tmp_path, order_id="order_67", order_facts=facts)

    await _send(tool, ctx, "Las de deseos vienen en Noche de Navidad 🎄")

    assert facts.calls == []


async def test_an_expired_coupon_goes_back_with_the_valid_ones(tmp_path: Path, ctx: ToolContext, monkeypatch) -> None:
    _jev(monkeypatch, {"cupon_ajeno.termino_1": 0.95})
    promotions = _Promotions()
    tool = _tool(tmp_path, promotions=promotions, now_ms=lambda: NOW_MS)

    held = await _send(tool, ctx, "Te aplico el cupón AMOR2026 🤍")

    assert held["error"] == "fact_check"
    assert "«AMOR2026»" in held["message"] and "BIENVENIDA" in held["message"]


async def test_the_coupons_are_not_read_when_the_text_does_not_talk_about_them(tmp_path: Path, ctx: ToolContext) -> None:
    promotions = _Promotions()

    await _send(_tool(tmp_path, promotions=promotions, now_ms=lambda: NOW_MS), ctx, "¿A qué ciudad te las envío?")

    assert promotions.calls == 0


async def test_a_code_written_as_a_code_is_checked_without_the_word_coupon(
    tmp_path: Path, ctx: ToolContext, monkeypatch
) -> None:
    """Premortem 2026-10-13: «Usa AMOR2026 al pagar» no traía «cupón» ni
    «descuento» y los cupones no se consultaban: el vencido salía."""
    _jev(monkeypatch, {"cupon_ajeno.termino_1": 0.95})
    promotions = _Promotions()

    held = await _send(_tool(tmp_path, promotions=promotions, now_ms=lambda: NOW_MS), ctx, "Usa AMOR2026 al pagar 🤍")

    assert promotions.calls == 1
    assert held["error"] == "fact_check" and "«AMOR2026»" in held["message"]


async def test_when_the_coupons_cannot_be_read_no_coupon_is_judged(
    tmp_path: Path, ctx: ToolContext, monkeypatch
) -> None:
    """Premortem del PR #426: sin respuesta de Medusa, los cupones quedaban
    vacíos y «10 % de descuento» volvía con «no hay cupones vigentes»: el
    modelo retiraba un cupón válido. No saber no es «no hay»."""
    _jev(monkeypatch, {"cupon_ajeno.termino_1": 0.95})
    text = "Con tu cupón de bienvenida tienes 10 % de descuento 🤍"
    tool = _tool(tmp_path, promotions=_Promotions(down=True), now_ms=lambda: NOW_MS)

    assert (await _send(tool, ctx, text)).get("reply") == {"text": text}


async def test_denying_a_product_the_catalog_has_goes_back(tmp_path: Path, ctx: ToolContext, monkeypatch) -> None:
    _jev(monkeypatch, {"negacion_falsa.termino_1": 0.95})

    held = await _send(_tool(tmp_path), ctx, "Lo siento, no tenemos la Plegaria de Luz por ahora.")

    assert held["error"] == "fact_check"
    assert "«Plegaria de Luz» está en el catálogo" in held["message"]



async def test_the_order_is_read_fresh(tmp_path: Path, ctx: ToolContext, monkeypatch) -> None:
    """Revisión 2026-10-13: el caché de OrderFacts del worker vive 60 s y solo
    lo invalida el bus de la API; tras «Confirmar pago» decía «pendiente» y
    contradecía lo que el bot acababa de leer en vivo."""
    _jev(monkeypatch, {"estado_ajeno.termino_1": 0.95})
    facts = _OrderFacts()

    await _send(_tool(tmp_path, order_id="order_67", order_facts=facts), ctx, "¡Listo! Tu pago quedó confirmado 🤍")

    assert facts.invalidated == ["order_67"]


async def test_an_order_medusa_could_not_confirm_is_not_judged(tmp_path: Path, ctx: ToolContext, monkeypatch) -> None:
    """Medusa no respondió y OrderFacts sirvió el último valor (`stale`): no se juzga."""
    _jev(monkeypatch, {"estado_ajeno.termino_1": 0.95})
    tool = _tool(tmp_path, order_id="order_67", order_facts=_OrderFacts(stale=True))

    out = await _send(tool, ctx, "¡Listo! Tu pago quedó confirmado 🤍")

    assert out.get("error") is None and "reply" in out  # (con Jev, `preambulo` ya le quita el «¡Listo!»)


class _CountingCatalog(_Catalog):
    def __init__(self, *products: SimpleNamespace) -> None:
        super().__init__(*products)
        self.searches = 0

    async def search(self, q: str = "", limit: int = 30):
        self.searches += 1
        return await super().search(q=q, limit=limit)


async def test_send_reply_reads_the_catalog_once_per_text(tmp_path: Path, ctx: ToolContext) -> None:
    """Los hechos y la lista de opciones revisan el mismo texto contra el mismo
    catálogo: una sola lectura (seguimiento del PR #426)."""
    catalog = _CountingCatalog()
    text = "Las de deseos vienen en Noche de Navidad 🎄"

    out = await _send(_tool(tmp_path, catalog=catalog), ctx, text)

    assert out.get("reply") == {"text": text}
    assert catalog.searches == 1


# ── Precios que el sistema sí explica (premortem del PR #426) ─────────────


async def test_a_price_with_a_valid_coupon_not_applied_yet_goes_as_is(tmp_path: Path, ctx: ToolContext) -> None:
    text = "Con el cupón BIENVENIDA te quedan en $23.750 cada una 🤍"  # 5 % sobre $25.000
    tool = _tool(tmp_path, promotions=_Promotions(), now_ms=lambda: NOW_MS)

    assert (await _send(tool, ctx, text)).get("reply") == {"text": text}


async def test_a_total_with_shipping_goes_as_is(tmp_path: Path, ctx: ToolContext) -> None:
    text = "Total: $66.940 🤍"  # 2 × $25.000 + envío nacional $16.940

    assert (await _send(_tool(tmp_path), ctx, text)).get("reply") == {"text": text}


async def test_a_total_with_the_link_surcharge_goes_as_is(tmp_path: Path, ctx: ToolContext) -> None:
    text = "Con el link te queda en $50.750 🤍"  # $50.000 + 1,5 %

    assert (await _send(_tool(tmp_path), ctx, text)).get("reply") == {"text": text}


async def test_the_total_of_the_registered_order_goes_as_is(tmp_path: Path, ctx: ToolContext) -> None:
    """El envío real (o un ajuste en Medusa) da otro total: el de OrderFacts manda."""
    text = "El total de tu pedido es $61.300 🤍"
    tool = _tool(tmp_path, order_id="order_67", order_facts=_OrderFacts(total_cop=61_300))

    assert (await _send(tool, ctx, text)).get("reply") == {"text": text}


async def test_with_a_bundle_without_the_decisions_no_source_is_read(tmp_path: Path, ctx: ToolContext, monkeypatch) -> None:
    """Revisión 2026-10-13: con ventas-6 (prod hoy) se leían el catálogo, el
    pedido y los cupones para nada, hasta 6 s por texto."""
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-6")
    registry.reset()
    catalog, facts, promotions = _CountingCatalog(), _OrderFacts(), _Promotions()
    check = FactCheck(tmp_path, catalog, promotions=promotions, order_facts=facts)
    FilesystemMetadataStore(tmp_path).write(KEY, {"last_inbound_message_id": "wamid.T6"})

    held = await check.held(KEY, "¡Listo! Tu pago quedó confirmado con el cupón AMOR2026 🤍")

    assert held is None and (catalog.searches, facts.calls, promotions.calls) == (0, [], 0)
