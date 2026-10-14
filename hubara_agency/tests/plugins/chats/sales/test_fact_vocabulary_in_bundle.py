"""El vocabulario de las reglas de palabras vive en el paquete (2026-10-13).

Las reglas de pago, estado del pedido, cupón y negación (`pago_ajeno`,
`estado_ajeno`, `cupon_ajeno`, `negacion_falsa`) buscan frases en el texto del
bot. Esas frases son de la tienda, como las palabras de presentación de
`variante_ajena`: van en el `with:` de los ítems de cada capacidad. Un falso
positivo (o algo que se escapa) se corrige con otra frase en una versión nueva
del paquete, sin tocar código (gotcha 14). El mismo vocabulario decide si vale
la pena consultar el pedido o los cupones en Medusa.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.decisions.capabilities.texto import (
    CuponDelTexto,
    EstadoDelTexto,
    NegacionDelTexto,
)
from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.tools.fact_check import FactCheck

KEY = "wa_573001111111"
WORD_RULES = ("pago_ajeno", "estado_ajeno", "cupon_ajeno", "negacion_falsa")
PENDING = EstadoDelTexto(text="", order="#67", stage="preparing", pay_status="pending")


@pytest.fixture
def bundles(tmp_path: Path, monkeypatch) -> Path:
    """Una copia de los paquetes para editar ventas-7 como lo haría el operador."""
    root = tmp_path / "bundles"
    shutil.copytree(registry.BUNDLES_DIR, root)
    monkeypatch.setattr(registry, "BUNDLES_DIR", root)
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    registry.reset()
    yield root
    registry.reset()


def _add_phrase(bundles: Path, capability: str, param: str, phrase: str) -> None:
    path = bundles / "ventas-7" / "capabilities" / f"{capability}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["items"]["with"][param] += f", {phrase}"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    registry.reset()


def _terms(capability: str, inp) -> list[str]:
    return [item["term"] for item in registry.capability(capability).items(inp)]


def test_each_word_rule_takes_its_vocabulary_from_the_bundle(bundles: Path) -> None:
    for name in WORD_RULES:
        words = registry.capability(name).items_params
        assert words and all(isinstance(v, str) and v.strip() for v in words.values()), name


def test_a_false_positive_is_fixed_with_a_phrase_in_the_bundle(bundles: Path) -> None:
    """«Mañana… va en camino» es algo por venir: hoy se le propone a Jev."""
    inp = EstadoDelTexto(**{**PENDING.__dict__, "text": "Mañana a primera hora tu pedido va en camino 🤍"})
    assert _terms("estado_ajeno", inp)

    _add_phrase(bundles, "estado_ajeno", "todavia_no", "mañana")

    assert _terms("estado_ajeno", inp) == []


def test_a_claim_that_slipped_is_caught_with_a_phrase_in_the_bundle(bundles: Path) -> None:
    inp = EstadoDelTexto(**{**PENDING.__dict__, "text": "Tu pago quedó acreditado 🤍"})
    assert _terms("estado_ajeno", inp) == []

    _add_phrase(bundles, "estado_ajeno", "pago_confirmado", "tu/su pago … acreditad*")

    assert _terms("estado_ajeno", inp) == ["el pago de su pedido quedó confirmado"]


def test_an_idiom_that_is_not_a_denial_is_fixed_in_the_bundle(bundles: Path) -> None:
    inp = NegacionDelTexto(
        text="No hay pierde con la Plegaria de Luz 🤍", titles=("Plegaria de Luz",), own=(),
        products=(),
    )
    assert _terms("negacion_falsa", inp) == ["Plegaria de Luz"]

    _add_phrase(bundles, "negacion_falsa", "no_es_negacion", "no hay pierde")

    assert _terms("negacion_falsa", inp) == []


def test_a_discount_word_of_the_store_is_added_in_the_bundle(bundles: Path) -> None:
    inp = CuponDelTexto(text="Tienes un bono del 20 % 🤍", known=(), valid=(), valid_lines=(), valid_percents=(10,))
    assert _terms("cupon_ajeno", inp) == []

    _add_phrase(bundles, "cupon_ajeno", "habla_de_descuento", "bono*")

    assert _terms("cupon_ajeno", inp) == ["un descuento del 20 %"]


# ── Lo que se consulta en Medusa sale del mismo vocabulario ─────────────────


class _Catalog:
    async def search(self, q: str = "", limit: int = 30):
        return SimpleNamespace(results=[])


class _OrderFacts:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def get_facts(self, ids):
        self.calls.append(list(ids))
        fact = SimpleNamespace(order_id="order_67", display_id="67", stage="preparing", pay_status="pending")
        return SimpleNamespace(facts={"order_67": fact}, unresolved=frozenset(), stale=False)


class _Promotions:
    def __init__(self) -> None:
        self.calls = 0

    async def list_active(self):
        self.calls += 1
        return []


def _with_order(vault: Path) -> None:
    episode = {"episode_id": "ep_001", "closed_at_ms": None, "order_id": "order_67", "order_draft": {"items": []}}
    FilesystemMetadataStore(vault).write(KEY, {"last_inbound_message_id": "wamid.T6", "episodes": [episode]})


async def test_the_order_is_read_when_the_bundle_vocabulary_says_the_text_talks_about_it(
    bundles: Path, tmp_path: Path
) -> None:
    _with_order(tmp_path)
    orders = _OrderFacts()
    check = FactCheck(tmp_path, _Catalog(), order_facts=orders)

    await check.facts(KEY, "Tu pago quedó acreditado 🤍")
    assert orders.calls == []

    _add_phrase(bundles, "estado_ajeno", "pago_confirmado", "tu/su pago … acreditad*")
    facts = await check.facts(KEY, "Tu pago quedó acreditado 🤍")

    assert orders.calls == [["order_67"]] and facts.pay_status == "pending"


async def test_the_coupons_are_read_when_the_bundle_vocabulary_says_the_text_talks_about_them(
    bundles: Path, tmp_path: Path
) -> None:
    _with_order(tmp_path)
    promotions = _Promotions()
    check = FactCheck(tmp_path, _Catalog(), promotions=promotions)

    await check.facts(KEY, "Tienes un bono para tu próxima compra 🤍")
    assert promotions.calls == 0

    _add_phrase(bundles, "cupon_ajeno", "habla_de_descuento", "bono*")
    await check.facts(KEY, "Tienes un bono para tu próxima compra 🤍")

    assert promotions.calls == 1


def test_every_phrase_of_every_bundle_can_be_read() -> None:
    """Una frase mal escrita («(contra|entrega)», «… pago») apagaría esa
    revisión en producción sin avisar más que en el log: falla aquí, en CI."""
    from src.plugins.chats.agent.sales.use_cases.fact_rules import phrases

    builtins = {"payment_claim_items", "status_claim_items", "coupon_claim_items", "denial_claim_items"}
    seen = 0
    for path in sorted(registry.BUNDLES_DIR.glob("*/capabilities/*.yaml")):
        items = yaml.safe_load(path.read_text(encoding="utf-8")).get("items") or {}
        if items.get("builtin") in builtins:
            for name, spec in items["with"].items():
                phrases(spec)  # ValueError = la frase no se puede leer
                seen += 1
    assert seen >= 16  # las cuatro reglas de ventas-7
