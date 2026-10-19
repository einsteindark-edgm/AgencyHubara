"""El guion de venta por campaña (pedido del operador, 2026-10-19).

Fin de semana 17-18 oct: 36 conversaciones de los anuncios de las velitas del
7 de diciembre y una sola venta. El operador pidió «cambiar el guion de venta
por producto o campaña, para luego cuando salga otra campaña poder modificarla
sin que dañe lo otro que está productivo».

Cada campaña es un archivo (`chats/shared/campaigns/<id>.yaml`). Una
conversación es de la campaña si viene de uno de sus anuncios o si su pedido
tiene uno de sus productos; solo entonces el bot recibe su guion, con los
precios del catálogo. Las demás conversaciones no cambian.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.plugins.chats.shared import sales_campaigns as sc

TODAY = date(2026, 10, 19)
#: Una copia congelada de la campaña de las velitas: el archivo vivo se puede
#: editar (o no existir en un clon de forge) sin romper estas pruebas.
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sales_campaigns"
BOXES = [
    {"producto": "velas-de-deseos-cuadradas", "por_caja": 12},
    {"producto": "velas-de-deseos-onduladas", "por_caja": 10},
]


def _product(handle: str, title: str, price: int) -> SimpleNamespace:
    return SimpleNamespace(
        handle=handle, title=title, description="",
        variants=[SimpleNamespace(prices=[SimpleNamespace(amount=price, currency_code="cop")])],
    )


ONDULADAS = _product("velas-de-deseos-onduladas", "Caja de Velas de deseos onduladas", 22_500)
CUADRADAS = _product("velas-de-deseos-cuadradas", "Caja de Velas de deseos cuadradas", 25_000)
TRILOGIA = _product("trilogia-del-terror", "Trilogía del Terror", 45_000)


def _episode(*, headline: str = "", body: str = "", items: list[str] = ()) -> dict:
    episode: dict = {"episode_id": "ep_001", "closed_at_ms": None}
    if headline or body:
        episode["referral_snapshot"] = {"source_id": "123", "headline": headline, "body": body}
    if items:
        episode["order_draft"] = {"items": [{"producto": i, "cantidad": "1"} for i in items]}
    return episode


_FIXTURE = sc.load_campaigns(FIXTURES)


@pytest.fixture
def velitas() -> sc.Campaign:
    (campaign,) = sc.load_campaigns(FIXTURES)
    return campaign


# ── los archivos de campaña se certifican ───────────────────────────────────


def test_every_campaign_file_loads_and_certifies() -> None:
    files = sorted(sc.CAMPAIGNS_DIR.glob("*.yaml"))
    campaigns = sc.load_campaigns()
    assert [c.id for c in campaigns] == [f.stem for f in files]
    # Cada una se puede evaluar contra un episodio (sus fechas son fechas).
    for campaign in campaigns:
        sc.campaign_for(_episode(headline="x"), today=TODAY, campaigns=[campaign])


def test_a_date_with_a_time_is_an_error(tmp_path: Path) -> None:
    """Revisión del 2026-10-19: «desde: 2026-10-01 08:00:00» llegaba como
    datetime, pasaba la carga y tumbaba `campaign_for` en el ingest de todos."""
    source = (FIXTURES / "velitas-7-dic.yaml").read_text(encoding="utf-8")
    (tmp_path / "velitas-7-dic.yaml").write_text(
        source.replace("desde: 2026-10-01", "desde: 2026-10-01 08:00:00"), encoding="utf-8"
    )

    with pytest.raises(sc.CampaignError, match="desde"):
        sc.load_campaigns(tmp_path)


def test_a_campaign_file_does_not_write_prices() -> None:
    """Los precios salen del catálogo; uno escrito en el YAML se desfasa de Medusa."""
    for path in [*sc.CAMPAIGNS_DIR.glob("*.yaml"), *FIXTURES.glob("*.yaml")]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\$\s?\d|\d{1,3}\.\d{3}", text), path.name


def test_a_broken_campaign_file_is_an_error_with_its_name(tmp_path: Path) -> None:
    (tmp_path / "mala.yaml").write_text("id: mala\nnombre: Mala\n", encoding="utf-8")

    with pytest.raises(sc.CampaignError, match="mala.yaml"):
        sc.load_campaigns(tmp_path)


def test_the_id_is_the_file_name(tmp_path: Path, velitas: sc.Campaign) -> None:
    source = (FIXTURES / "velitas-7-dic.yaml").read_text(encoding="utf-8")
    (tmp_path / "otra.yaml").write_text(source, encoding="utf-8")

    with pytest.raises(sc.CampaignError, match="otra.yaml"):
        sc.load_campaigns(tmp_path)


# ── qué conversación es de la campaña ────────────────────────────────────────


@pytest.mark.parametrize(
    "episode",
    [
        _episode(headline="🕯️ Velitas de intención", body="¿Te quedaste sin velitas el año pasado?"),
        _episode(headline="¡Llegó el Kit de Velitas 2026! 🕯️🚚"),
        _episode(items=["Caja de Velas de deseos cuadradas"]),
    ],
)
def test_an_episode_of_the_campaign_gets_it(episode: dict) -> None:
    assert sc.campaign_for(episode, today=TODAY, campaigns=_FIXTURE).id == "velitas-7-dic"


def test_the_prefilled_message_of_the_ad_also_tells_the_campaign() -> None:
    """C07 y C28 del fin de semana llegaron sin referral, con el prellenado."""
    episode = _episode()

    found = sc.campaign_for(
        episode, today=TODAY, text="¡Hola! Quiero más información sobre las velitas del 7 de diciembre.", campaigns=_FIXTURE
    )

    assert found.id == "velitas-7-dic"


@pytest.mark.parametrize(
    "episode",
    [
        _episode(headline="Velas Aromáticas", body="Decora tus espacios con encanto este Halloween! 🎃"),
        _episode(items=["Trilogía del Terror"]),
        _episode(),
    ],
)
def test_another_conversation_gets_no_campaign(episode: dict) -> None:
    assert sc.campaign_for(episode, today=TODAY, text="Hola, ¿qué precio tiene?", campaigns=_FIXTURE) is None


def test_the_stamp_of_the_episode_keeps_the_campaign() -> None:
    """Una vez marcado, el episodio sigue en la campaña aunque el mensaje de hoy no la nombre."""
    episode = {**_episode(), sc.EPISODE_KEY: "velitas-7-dic"}

    assert sc.campaign_for(episode, today=TODAY, text="ok gracias", campaigns=_FIXTURE).id == "velitas-7-dic"


def test_an_episode_with_its_order_registered_gets_no_script() -> None:
    """El guion es para vender: con el pedido registrado (pago pendiente,
    posventa) el bot no le sigue ofreciendo el combo."""
    episode = {**_episode(headline="Velitas de intención"), sc.EPISODE_KEY: "velitas-7-dic", "order_id": "order_01"}

    assert sc.campaign_for(episode, today=TODAY, campaigns=_FIXTURE) is None


@pytest.mark.parametrize("today", [date(2026, 9, 30), date(2026, 12, 8)])
def test_a_campaign_out_of_its_dates_does_not_apply(today: date) -> None:
    assert sc.campaign_for(_episode(headline="Velitas de intención"), today=today, campaigns=_FIXTURE) is None


def test_a_paused_campaign_does_not_apply(tmp_path: Path) -> None:
    source = (FIXTURES / "velitas-7-dic.yaml").read_text(encoding="utf-8")
    (tmp_path / "velitas-7-dic.yaml").write_text(source.replace("activa: true", "activa: false"), encoding="utf-8")

    found = sc.campaign_for(_episode(headline="Velitas de intención"), today=TODAY, campaigns=sc.load_campaigns(tmp_path))

    assert found is None


# ── lo que el bot recibe ─────────────────────────────────────────────────────


def test_the_sales_note_brings_the_catalog_prices_and_the_combo(velitas: sc.Campaign) -> None:
    note = sc.sales_note(velitas, [ONDULADAS, CUADRADAS, TRILOGIA], cod_min=45_000, boxes=BOXES)

    assert "Velitas del 7 de diciembre" in note
    assert "Caja de Velas de deseos onduladas: $22.500 la caja de 10" in note
    assert "Caja de Velas de deseos cuadradas: $25.000 la caja de 12" in note
    assert "Trilogía" not in note
    # Las tres combinaciones de 2 cajas, todas con contra entrega.
    assert "2 × Caja de Velas de deseos onduladas: $45.000" in note
    assert "1 × Caja de Velas de deseos onduladas + 1 × Caja de Velas de deseos cuadradas: $47.500" in note
    assert "2 × Caja de Velas de deseos cuadradas: $50.000" in note
    combos = [line for line in note.splitlines() if line.startswith("• ") and " × " in line]
    assert len(combos) == 3 and all("paga contra entrega" in line for line in combos)
    # El cupón, solo si aplica a este cliente.
    assert "BIENVENIDA" in note and "list_promotions" in note and "applies_to_customer" in note
    # El guion del archivo.
    assert velitas.guion[0] in note


def test_a_combo_under_the_minimum_says_it_is_paid_in_advance(velitas: sc.Campaign) -> None:
    note = sc.sales_note(velitas, [ONDULADAS, CUADRADAS], cod_min=48_000, boxes=BOXES)

    assert "2 × Caja de Velas de deseos onduladas: $45.000 (pago anticipado" in note


def test_without_catalog_the_note_sends_the_bot_to_look_up_the_prices(velitas: sc.Campaign) -> None:
    note = sc.sales_note(velitas, [], cod_min=45_000, boxes=BOXES)

    assert "search_products" in note and "$" not in note
    assert velitas.guion[0] in note


def test_the_remarketing_block_brings_the_touch_and_its_numbers(velitas: sc.Campaign) -> None:
    kwargs = {"cod_min": 45_000, "boxes": BOXES, "coupon_discount": "5 %"}
    first = sc.remarketing_block(velitas, [ONDULADAS, CUADRADAS], touch_number=1, **kwargs)
    fifth = sc.remarketing_block(velitas, [ONDULADAS, CUADRADAS], touch_number=5, **kwargs)
    unknown = sc.remarketing_block(velitas, [ONDULADAS, CUADRADAS], touch_number=1, cod_min=45_000, boxes=BOXES)

    assert velitas.toques[0] in first and "$22.500" in first and "$45.000" in first
    assert velitas.toques[-1] in fifth and velitas.toques[0] not in fifth
    assert "BIENVENIDA" in first and "5 %" in first
    # Sin el cupón vigente en Medusa, el toque no lo nombra.
    assert "BIENVENIDA" not in unknown
