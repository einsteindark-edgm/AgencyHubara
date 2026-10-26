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
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
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


def test_every_line_of_the_script_of_every_live_campaign_reaches_ventas() -> None:
    """El guion se cambia en el YAML, sin tocar código (operador, 2026-10-22:
    «todo esto solo debería aplicar a files de configuración»): cada línea de
    cada campaña viva llega tal cual a la nota de Ventas, diga lo que diga
    (p. ej. la de las velitas que este año no se personalizan)."""
    for campaign in sc.load_campaigns():
        note = sc.sales_note(campaign, [ONDULADAS, CUADRADAS], cod_min=45_000, boxes=BOXES)

        for step in campaign.guion:
            assert f"- {step}" in note, (campaign.id, step[:60])


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


# Operador, 2026-10-21: «si la conversación termina hablando de productos que
# tienen el gancho o el descuento, ahí debería reconocerse y hacerle el gancho
# sobre ese producto». El producto del que habla el cliente AHORA manda.


def _halloween() -> sc.Campaign:
    (velitas,) = _FIXTURE
    return replace(velitas, id="halloween", nombre="Halloween", productos=("trilogia-del-terror",),
                   anuncios=("noche de brujas",), cupon=None)


def test_naming_a_product_of_the_campaign_makes_it_a_campaign_conversation() -> None:
    found = sc.campaign_for(_episode(), today=TODAY, said="¿Y las velas de deseos cuadradas?", campaigns=_FIXTURE)

    assert found.id == "velitas-7-dic"


def test_a_conversation_of_another_campaign_moves_to_the_product_it_ends_up_talking_about() -> None:
    campaigns = [_halloween(), *_FIXTURE]
    episode = {**_episode(headline="Noche de brujas"), sc.EPISODE_KEY: "halloween"}

    moved = sc.campaign_for(episode, today=TODAY, said="¿Y las velas de deseos onduladas?", campaigns=campaigns)
    stays = sc.campaign_for(episode, today=TODAY, said="ok, ¿y la Trilogía del Terror?", campaigns=campaigns)
    quiet = sc.campaign_for(episode, today=TODAY, said="Luego te aviso", campaigns=campaigns)

    assert (moved.id, stays.id, quiet.id) == ("velitas-7-dic", "halloween", "halloween")


def test_an_episode_with_its_order_registered_gets_no_script() -> None:
    """El guion es para vender: con el pedido registrado (pago pendiente,
    posventa) el bot no le sigue ofreciendo el combo."""
    episode = {**_episode(headline="Velitas de intención"), sc.EPISODE_KEY: "velitas-7-dic", "order_id": "order_01"}

    assert sc.campaign_for(episode, today=TODAY, campaigns=_FIXTURE) is None


def test_a_post_sale_episode_gets_no_script() -> None:
    """Revisión del 2026-10-20: «ya te transferí» abre un episodio de posventa
    (`after_order`) sin `order_id`; ahí tampoco va el guion de venta."""
    episode = {**_episode(headline="Velitas de intención"), sc.EPISODE_KEY: "velitas-7-dic",
               "after_order": {"order_id": "order_01"}}

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
    # Sin el cupón vigente en Medusa, la nota no lo nombra.
    assert "BIENVENIDA" not in note
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
    kwargs = {"cod_min": 45_000, "boxes": BOXES}
    first = sc.remarketing_block(velitas, [ONDULADAS, CUADRADAS], touch_number=1, **kwargs)
    fifth = sc.remarketing_block(velitas, [ONDULADAS, CUADRADAS], touch_number=5, **kwargs)

    assert velitas.toques[0] in first and "$22.500" in first and "$45.000" in first
    assert velitas.toques[-1] in fifth and velitas.toques[0] not in fifth
    assert "BIENVENIDA" not in first


# ── la oferta con fecha límite (operador, 2026-10-20) ───────────────────────
#
# «Que le ofrezca el descuento del 5 % antes del 31 de octubre y después lo
# mandamos al 15 de noviembre, para forzar a la gente a que compre»: cuando
# el cliente aplaza la compra (Ventas) y en un toque para quien tuvo intención
# de compra y no compró (remarketing). La fecha es la de fin del cupón en
# Medusa: cambiarla en el dashboard cambia lo que dice el bot, y el bot nunca
# dice una fecha que el cupón no tiene.

BOGOTA = ZoneInfo("America/Bogota")


def _promo(*, ends: datetime | None, value: int = 5, first_purchase_only: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        code="VELITAS5", discount_type="percentage", value=value, first_purchase_only=first_purchase_only,
        ends_at_ms=int(ends.timestamp() * 1000) if ends else None,
    )


#: Como lo guarda el dashboard: «hasta el 31 de octubre» = el 1 de noviembre a las 00:00 en Bogotá.
UNTIL_OCT_31 = datetime(2026, 11, 1, tzinfo=BOGOTA)


def test_the_offer_says_the_last_day_of_the_coupon_in_medusa() -> None:
    offer = sc.coupon_offer(_promo(ends=UNTIL_OCT_31), today=TODAY)

    assert offer is not None and offer.code == "VELITAS5" and offer.discount == "5 %"
    assert offer.last_day == date(2026, 10, 31)
    assert "hasta el 31 de octubre" in sc.offer_phrase(offer)
    assert "solo en su primera compra" in sc.offer_phrase(offer)


def test_moving_the_coupon_to_november_15_moves_the_date_the_bot_says() -> None:
    offer = sc.coupon_offer(_promo(ends=datetime(2026, 11, 16, tzinfo=BOGOTA)), today=date(2026, 11, 1))

    assert "hasta el 15 de noviembre" in sc.offer_phrase(offer)


def test_a_coupon_without_a_near_end_does_not_announce_a_date() -> None:
    """BIENVENIDA vence en 2030: «hasta el 31 de diciembre de 2030» no empuja a
    nadie, y el bot no inventa otra fecha."""
    far = sc.coupon_offer(_promo(ends=datetime(2031, 1, 1, tzinfo=BOGOTA)), today=TODAY)
    endless = sc.coupon_offer(_promo(ends=None), today=TODAY)

    for offer in (far, endless):
        assert offer is not None and offer.last_day is None
        assert "hasta el" not in sc.offer_phrase(offer)


def test_the_sales_note_carries_the_offer_and_says_it_now_when_the_customer_defers(velitas: sc.Campaign) -> None:
    offer = sc.coupon_offer(_promo(ends=UNTIL_OCT_31), today=TODAY)
    kwargs = {"cod_min": 45_000, "boxes": BOXES, "offer": offer}

    calm = sc.sales_note(velitas, [ONDULADAS, CUADRADAS], **kwargs)
    deferring = sc.sales_note(velitas, [ONDULADAS, CUADRADAS], offer_now=True, **kwargs)

    assert "VELITAS5" in calm and "hasta el 31 de octubre" in calm and "list_promotions" in calm
    assert "acaba de aplazar" not in calm
    assert "acaba de aplazar" in deferring and "una sola vez" in deferring


def test_the_offer_touch_goes_only_to_who_showed_purchase_intent(velitas: sc.Campaign) -> None:
    offer = sc.coupon_offer(_promo(ends=UNTIL_OCT_31), today=TODAY)
    kwargs = {"cod_min": 45_000, "boxes": BOXES, "offer": offer}

    with_intent = sc.remarketing_block(velitas, [ONDULADAS, CUADRADAS], touch_number=velitas.toque_oferta, intent=True, **kwargs)
    other_touch = sc.remarketing_block(velitas, [ONDULADAS, CUADRADAS], touch_number=1, intent=True, **kwargs)
    no_intent = sc.remarketing_block(velitas, [ONDULADAS, CUADRADAS], touch_number=velitas.toque_oferta, intent=False, **kwargs)

    assert "VELITAS5" in with_intent and "hasta el 31 de octubre" in with_intent
    assert "VELITAS5" not in other_touch and "VELITAS5" not in no_intent


def _decision(choice: str, *, at_ms: int = 2_000, confidence: float = 0.9, value=None) -> dict:
    return {
        "at_ms": at_ms, "stage": "ingest", "capability": "compra", "by": "jev", "provider": "jev",
        "value": value if value is not None else [None, "text"],
        "answers": [{"q": "compra.que_hace", "choice": choice, "confidence": confidence}],
    }


@pytest.mark.parametrize("choice", ["elige", "aplaza", "da_datos"])
def test_jev_reading_that_the_customer_chose_deferred_or_gave_data_is_purchase_intent(choice: str) -> None:
    episode = {"episode_id": "ep_001", "started_at_ms": 1_000}

    assert sc.purchase_intent(episode, [_decision(choice)]) is True


def test_a_confirmation_counts_when_the_engine_kept_it() -> None:
    """Revisión del 2026-10-20: la respuesta cruda `confirma` no basta (el
    paquete la retira si el asesor no preguntó por la compra); la decisión
    final `affirmation` sí."""
    episode = {"episode_id": "ep_001", "started_at_ms": 1_000}

    assert sc.purchase_intent(episode, [_decision("confirma")]) is False
    assert sc.purchase_intent(episode, [_decision("confirma", value=["affirmation", "text"])]) is True


def test_asking_the_price_is_interest_not_purchase_intent() -> None:
    episode = {"episode_id": "ep_001", "started_at_ms": 1_000}

    assert sc.purchase_intent(episode, [_decision("pregunta"), _decision("elige", confidence=0.5)]) is False


def test_a_reading_of_a_previous_episode_does_not_count() -> None:
    episode = {"episode_id": "ep_002", "started_at_ms": 10_000}

    assert sc.purchase_intent(episode, [_decision("elige", at_ms=2_000)]) is False


def test_a_half_made_order_is_purchase_intent() -> None:
    episode = {**_episode(items=["Caja de Velas de deseos cuadradas"]), "started_at_ms": 1_000}

    assert sc.purchase_intent(episode, []) is True


# ── revisión del 2026-10-20 ──────────────────────────────────────────────────


def test_an_offer_without_a_date_tells_the_bot_not_to_say_any_date(velitas: sc.Campaign) -> None:
    """BIENVENIDA vence en 2030: sin fecha, el texto no puede hablar de «la
    única fecha» (el contexto trae «7 de diciembre» y el bot la tomaba)."""
    offer = sc.coupon_offer(_promo(ends=None), today=TODAY)
    kwargs = {"cod_min": 45_000, "boxes": BOXES, "offer": offer}

    note = sc.sales_note(velitas, [ONDULADAS, CUADRADAS], offer_now=True, **kwargs)
    block = sc.remarketing_block(velitas, [ONDULADAS, CUADRADAS], touch_number=velitas.toque_oferta, intent=True, **kwargs)

    for text in (note, block):
        assert "no tiene una fecha límite que puedas decir" in text
        assert "única fecha" not in text
    assert "fecha límite que te paso" not in "\n".join(velitas.guion)


def test_once_told_the_note_says_so_instead_of_asking_again(velitas: sc.Campaign) -> None:
    offer = sc.coupon_offer(_promo(ends=UNTIL_OCT_31), today=TODAY)

    told = sc.sales_note(velitas, [ONDULADAS], cod_min=45_000, boxes=BOXES, offer=offer, already_offered=True)

    line = next(x for x in told.splitlines() if x.startswith("Cupón de la campaña"))
    assert "Ya se la dijiste en este episodio" in line and "Ofrécelo cuando" not in line


def test_the_now_line_asks_to_confirm_a_first_purchase_coupon_before_saying_it(velitas: sc.Campaign) -> None:
    offer = sc.coupon_offer(_promo(ends=UNTIL_OCT_31), today=TODAY)

    now = sc.sales_note(velitas, [ONDULADAS], cod_min=45_000, boxes=BOXES, offer=offer, offer_now=True)

    line = next(x for x in now.splitlines() if x.startswith("AHORA"))
    assert "list_promotions" in line and "applies_to_customer" in line


@pytest.mark.parametrize("bad", ["0", "true", '"2"'])
def test_toque_oferta_must_be_a_touch_number(tmp_path: Path, bad: str) -> None:
    source = (FIXTURES / "velitas-7-dic.yaml").read_text(encoding="utf-8")
    (tmp_path / "velitas-7-dic.yaml").write_text(source.replace("toque_oferta: 2", f"toque_oferta: {bad}"), encoding="utf-8")

    with pytest.raises(sc.CampaignError, match="toque_oferta"):
        sc.load_campaigns(tmp_path)


@pytest.mark.parametrize(
    "extra",
    [
        {"min_subtotal_cop": 60_000},  # un mínimo que la frase no dice
        {"collection_ids": ("pcol_1",)},
        {"tag_values": ("Color: Rosado",)},
        {"scope_unresolved": True},
        {"variant_ids": ("variant_1",)},
        {"product_ids": ("prod_otro",)},  # no cubre los productos de la campaña
        # Revisión del 2026-10-20: el cupo por unidad (#364) y el monto fijo
        # por ítem tampoco caben en «$X de descuento».
        {"max_quantity": 1},
        {"discount_type": "fixed", "value": 5_000, "allocation": "each"},
    ],
)
def test_a_coupon_with_conditions_the_phrase_does_not_say_is_not_offered(extra: dict) -> None:
    promo = SimpleNamespace(**{**vars(_promo(ends=UNTIL_OCT_31)), **extra})

    assert sc.coupon_offer(promo, today=TODAY, product_ids=("prod_onduladas", "prod_cuadradas")) is None


def test_a_fixed_amount_for_the_whole_order_is_offered() -> None:
    promo = SimpleNamespace(**{**vars(_promo(ends=UNTIL_OCT_31)), "discount_type": "fixed", "value": 5_000,
                               "allocation": "across"})

    offer = sc.coupon_offer(promo, today=TODAY)

    assert offer is not None and offer.discount == "$5.000"


def test_a_coupon_for_the_campaign_products_is_offered() -> None:
    promo = SimpleNamespace(**{**vars(_promo(ends=UNTIL_OCT_31)), "product_ids": ("prod_onduladas", "prod_cuadradas", "prod_x")})

    assert sc.coupon_offer(promo, today=TODAY, product_ids=("prod_onduladas", "prod_cuadradas")) is not None


def _live_bundles() -> list[str]:
    """Los paquetes que corren las tiendas: el que nombra cada tenant en
    Terraform (`lab.decisions_bundle`) y el default de la variable (un clon de
    forge, sin bloque `lab`). Promover un paquete nuevo lo mete aquí solo."""
    import re

    from tests.infra.test_lab_settings_terraform import _TERRAFORM, _tenant_bundles

    variables = (_TERRAFORM / "platform" / "variables.tf").read_text(encoding="utf-8")
    default = re.search(r'decisions_bundle\s*=\s*optional\(string,\s*"([^"]+)"\)', variables)
    tfvars = _TERRAFORM / "platform" / "tenants.auto.tfvars"
    named = _tenant_bundles(tfvars.read_text(encoding="utf-8")) if tfvars.is_file() else {}
    return sorted({*named.values(), *([default.group(1)] if default else [])})


@pytest.mark.parametrize("bundle", _live_bundles())
def test_the_intent_reading_matches_the_live_bundle(bundle: str) -> None:
    """La intención lee la pregunta `compra.que_hace` del paquete vivo: si
    otra versión renombra una opción o cambia el umbral, esta prueba avisa (la
    oferta y el perfil se apagarían en silencio)."""
    import yaml

    from src.plugins.chats.shared.store_pack import BUNDLES_DIR

    compra = yaml.safe_load((Path(BUNDLES_DIR) / bundle / "capabilities" / "compra.yaml").read_text(encoding="utf-8"))
    (question,) = [q for q in compra["questions"] if q["id"] == "compra.que_hace"]

    assert sc.INTENT_CHOICES <= set(question["criteria"])
    assert compra["thresholds"]["choice"] == sc.INTENT_CONFIDENCE


def test_the_live_bundles_come_from_terraform() -> None:
    # Sin paquetes, la paridad de arriba no correría ningún caso y pasaría en
    # silencio. Cuál corre cada tienda es dato del tfvars: no se escribe aquí.
    assert _live_bundles()


def test_a_reading_logged_by_the_engine_round_trips_into_intent(tmp_path: Path) -> None:
    from src.plugins.chats.agent.sales.decisions.decision_log import SessionDecisionLog, read_decisions

    verdict = {"capability": "compra", "value": [None, "text"], "by": "jev", "provider": "jev",
               "answers": [{"q": "compra.que_hace", "choice": "elige", "confidence": 0.81, "p": 0.81}]}
    SessionDecisionLog(tmp_path).record("wa_10000000001", verdict, stage="ingest", message_id="wamid.X", at_ms=2_000)

    episode = {"episode_id": "ep_001", "started_at_ms": 1_000}
    assert sc.purchase_intent(episode, read_decisions(tmp_path, "wa_10000000001")) is True


# ── envío gratis desde 6 cajas (operador, 2026-10-20) ───────────────────────
#
# «Otro gancho de venta: a partir de 6 cajas envío gratis, para que Ventas y
# remarketing lo sepan y para que se haga bien la cuenta si hacen el pedido por
# 6 cajas o más.»


@pytest.mark.parametrize("bad", ["1", "0", "true", '"6"'])
def test_free_shipping_from_must_be_a_number_of_boxes(tmp_path: Path, bad: str) -> None:
    source = (FIXTURES / "velitas-7-dic.yaml").read_text(encoding="utf-8")
    assert "envio_gratis_desde_cajas: 6" in source
    (tmp_path / "velitas-7-dic.yaml").write_text(
        source.replace("envio_gratis_desde_cajas: 6", f"envio_gratis_desde_cajas: {bad}"), encoding="utf-8"
    )

    with pytest.raises(sc.CampaignError, match="envio_gratis_desde_cajas"):
        sc.load_campaigns(tmp_path)


def test_the_sales_note_says_free_shipping_from_six_boxes(velitas: sc.Campaign) -> None:
    note = sc.sales_note(velitas, [ONDULADAS, CUADRADAS], cod_min=45_000, boxes=BOXES)

    line = next(x for x in note.splitlines() if "Envío gratis" in x)
    assert "desde 6 cajas" in line and "menos de 6" in line
    assert "aunque lleve otros productos" in line and "otros productos no suman" in line


def test_the_remarketing_block_knows_free_shipping_from_six_boxes(velitas: sc.Campaign) -> None:
    block = sc.remarketing_block(velitas, [ONDULADAS, CUADRADAS], cod_min=45_000, boxes=BOXES, touch_number=1)

    line = next(x for x in block.splitlines() if "Envío gratis" in x)
    assert "desde 6 cajas" in line and "menos de 6" in line
    assert "aunque lleve otros productos" in line and "otros productos no suman" in line


def test_without_the_rule_nothing_promises_free_shipping(velitas: sc.Campaign) -> None:
    from dataclasses import replace

    campaign = replace(velitas, envio_gratis_desde_cajas=None)

    assert "gratis" not in sc.sales_note(campaign, [ONDULADAS, CUADRADAS], cod_min=45_000, boxes=BOXES).lower()
    assert "gratis" not in sc.remarketing_block(
        campaign, [ONDULADAS, CUADRADAS], cod_min=45_000, boxes=BOXES, touch_number=1
    ).lower()


# La regla de la cuenta: pura, por los productos y cantidades del pedido (no
# por cómo se marcó la conversación: también vale en «Crear pedido» y en un
# reintento del registro, cuando el episodio ya tiene su pedido).

ONDULADA, CUADRADA = "velas-de-deseos-onduladas", "velas-de-deseos-cuadradas"


def test_six_boxes_of_the_campaign_ship_free_even_mixed(velitas: sc.Campaign) -> None:
    free = sc.free_shipping_for([(ONDULADA, 3), (CUADRADA, 3)], today=TODAY, campaigns=[velitas])

    assert free == sc.FreeShipping(campaign="velitas-7-dic", min_boxes=6, boxes=6)


def test_five_boxes_pay_shipping(velitas: sc.Campaign) -> None:
    assert sc.free_shipping_for([(ONDULADA, 2), (CUADRADA, 3)], today=TODAY, campaigns=[velitas]) is None


def test_other_products_do_not_count_toward_the_boxes(velitas: sc.Campaign) -> None:
    """Operador, 2026-10-20: «lo que no puede ser gratis es 6 productos de otros
    sin que lleven las 6 cajas de velitas cuadradas u onduladas»."""
    assert sc.free_shipping_for([(CUADRADA, 5), ("trilogia-del-terror", 3)], today=TODAY, campaigns=[velitas]) is None
    assert sc.free_shipping_for([("trilogia-del-terror", 6)], today=TODAY, campaigns=[velitas]) is None
    assert sc.free_shipping_for([(CUADRADA, 2), ("trilogia-del-terror", 4)], today=TODAY, campaigns=[velitas]) is None


def test_with_six_boxes_the_whole_order_ships_free_even_with_other_products(velitas: sc.Campaign) -> None:
    """Operador, 2026-10-20: «después de 6 cajas el envío es gratis, ya sean las
    6 cajas más otras velitas»."""
    free = sc.free_shipping_for(
        [(ONDULADA, 3), (CUADRADA, 3), ("trilogia-del-terror", 2)], today=TODAY, campaigns=[velitas]
    )

    assert free == sc.FreeShipping(campaign="velitas-7-dic", min_boxes=6, boxes=6)


@pytest.mark.parametrize("today", [date(2026, 9, 30), date(2026, 12, 8)])
def test_out_of_the_campaign_dates_shipping_is_charged(velitas: sc.Campaign, today: date) -> None:
    assert sc.free_shipping_for([(ONDULADA, 6)], today=today, campaigns=[velitas]) is None


def test_a_campaign_without_the_rule_never_ships_free(velitas: sc.Campaign) -> None:
    from dataclasses import replace

    campaign = replace(velitas, envio_gratis_desde_cajas=None)

    assert sc.free_shipping_for([(ONDULADA, 20)], today=TODAY, campaigns=[campaign]) is None


def test_the_free_shipping_mark_survives_its_json(velitas: sc.Campaign) -> None:
    free = sc.free_shipping_for([(ONDULADA, 7)], today=TODAY, campaigns=[velitas])

    assert sc.FreeShipping.from_json(free.to_json()) == free
    assert sc.FreeShipping.from_json(None) is None and sc.FreeShipping.from_json({"campaign": "x"}) is None

