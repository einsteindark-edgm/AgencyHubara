"""El cliente que pidió, recibió el catálogo y se calló (análisis 2026-10-13).

Desde el 15-sep: 51 % de los leads de anuncio no volvió a escribir tras
nuestra primera respuesta; 82 de ellos habían visto la tarjeta del catálogo
(76 de Halloween). El 1er toque les decía «Hola de nuevo 🌿 quedó pendiente lo
de la colección… ¿alguna te llamó la atención?» (64 de 77): nada que no
supieran. Respondía el 25 %; el 2º, a +2 h del 1º, el 4 %.

Decisión del operador: a ese cliente el 1er toque le explica lo que vio (con
la ficha real) y el 2º, a la mañana siguiente, le resuelve envío, tiempos y
pago con los datos de la tienda.
"""
from __future__ import annotations

from src.plugins.chats.agent.remarketing.prompts import build_remarketing_trigger
from src.plugins.chats.agent.remarketing.use_cases.context import (
    context_from_metadata,
    shown_product_titles,
)
from src.plugins.chats.shared.product_truth import invented_numbers, numbers_in

HALLOWEEN = "[el cliente vino desde un anuncio de Facebook/Instagram, titulado 'Velas Aromáticas']\n¡Hola! Quiero más información sobre la colección de Halloween"


def _ev(role: str, content: str, **extra) -> dict:
    return {"role": role, "content": content, "timestamp": "2026-10-13T20:00:00+00:00", **extra}


def _catalog(*titles: str) -> dict:
    return _ev(
        "assistant",
        f"🛍️ El bot envió el catálogo con {len(titles)} productos",
        kind="ui_component",
        component_kind="products_list",
        # La tarjeta del historial trae lo que leyó el cliente (`card.items`).
        card={"items": [{"title": t, "detail": "$45.000 COP"} for t in titles]},
    )


def _meta(**extra) -> dict:
    return {"tag": "INTERESADO", "episodes": [{"episode_id": "ep_1", "msgs_count_at_start": 0}], **extra}


class TestSilentAfterCatalog:
    def test_pidio_vio_el_catalogo_y_no_volvio_a_escribir(self) -> None:
        events = [
            _ev("user", HALLOWEEN),
            _ev("assistant", "¡Buenas! Bienvenido a *Hubara*, velas artesanales hechas a mano."),
            _catalog("Calabaza", "Momia", "Fantasma", "Trilogía del Terror"),
        ]
        assert context_from_metadata(_meta(), events).silent_after_catalog is True

    def test_varios_mensajes_antes_de_nuestra_respuesta_siguen_siendo_el_caso(self) -> None:
        events = [
            _ev("user", "Hola"),
            _ev("user", HALLOWEEN),
            _ev("assistant", "¡Buenas! Bienvenido a *Hubara*."),
            _catalog("Calabaza"),
        ]
        assert context_from_metadata(_meta(), events).silent_after_catalog is True

    def test_si_respondio_despues_del_catalogo_no_es_el_caso(self) -> None:
        events = [
            _ev("user", HALLOWEEN),
            _ev("assistant", "¡Buenas! Bienvenido a *Hubara*."),
            _catalog("Calabaza", "Momia"),
            _ev("user", "¿La calabaza de qué aroma es?"),
            _ev("assistant", "Es de frutos rojos."),
        ]
        assert context_from_metadata(_meta(), events).silent_after_catalog is False

    def test_sin_tarjeta_de_catalogo_no_es_el_caso(self) -> None:
        events = [
            _ev("user", "¡Hola! Quiero más información sobre las velas aromáticas"),
            _ev("assistant", "¡Buenas! ¿Las buscas para ti o para regalar?"),
        ]
        assert context_from_metadata(_meta(), events).silent_after_catalog is False

    def test_lo_de_episodios_anteriores_no_cuenta(self) -> None:
        # Conversó en un episodio viejo; en el nuevo solo pidió y vio el catálogo.
        events = [
            _ev("user", "Quiero la Cruz de Vida"),
            _ev("assistant", "¡Listo!"),
            _ev("user", HALLOWEEN),
            _ev("assistant", "¡Buenas de nuevo!"),
            _catalog("Calabaza"),
        ]
        meta = _meta(episodes=[
            {"episode_id": "ep_1", "msgs_count_at_start": 0, "closed_at_ms": 1},
            {"episode_id": "ep_2", "msgs_count_at_start": 2},
        ])
        assert context_from_metadata(meta, events).silent_after_catalog is True


    def test_con_pedido_registrado_o_pago_pendiente_no_es_el_caso(self) -> None:
        """Revisión de producción del PR #426 (2026-10-14): un pedido que el
        operador creó («Crear pedido») después del catálogo dejaba al cliente
        en el caso, y el 2º toque le explicaba cómo pagar como si no hubiera
        comprado. El gancho es sobre el pedido (`transactional_hook`)."""
        events = [_ev("user", HALLOWEEN), _ev("assistant", "¡Buenas!"), _catalog("Calabaza")]
        registered = _meta()
        registered["episodes"][0]["order_id"] = "order_67"
        pending = _meta(tag="CONFIRMADO_PAGO_PENDIENTE")

        assert context_from_metadata(registered, events).silent_after_catalog is False
        assert context_from_metadata(pending, events).silent_after_catalog is False

    def test_con_pedido_a_medias_no_es_el_caso(self) -> None:
        # Un carrito de la web / pedido en curso: el gancho es sobre el pedido.
        events = [_ev("user", HALLOWEEN), _ev("assistant", "¡Buenas!"), _catalog("Calabaza")]
        meta = _meta()
        meta["episodes"][0]["order_draft"] = {"slots": {"producto": "Calabaza"}}
        assert context_from_metadata(meta, events).silent_after_catalog is False


class TestShownProductTitles:
    def test_los_productos_que_vio_en_las_tarjetas_del_episodio(self) -> None:
        events = [
            _ev("user", HALLOWEEN),
            _catalog("Calabaza", "Momia"),
            _catalog("Momia", "Trilogía del Terror"),
        ]
        assert shown_product_titles(events) == ["Calabaza", "Momia", "Trilogía del Terror"]

    def test_tarjetas_viejas_sin_la_lista_no_aportan_nada(self) -> None:
        events = [_ev("assistant", "🛍️ El bot envió el catálogo con 4 productos", kind="ui_component", component_kind="products_list")]
        assert shown_product_titles(events) == []


class TestInventedNumbers:
    """Explicar más es justo lo que inventó «el Cubo Love también viene en
    vaso» (2026-09-25). Un número (horas, tamaño, precio, días) que no está en
    la ficha, la charla ni los datos de la tienda no sale al cliente."""

    def test_un_numero_que_esta_en_la_ficha_pasa(self) -> None:
        sources = numbers_in("- Calabaza: vela de cera de soya, 40 horas de duración")
        assert invented_numbers("Dura unas 40 horas encendida 🎃", sources) == []

    def test_un_numero_que_no_esta_en_ninguna_fuente_se_caza(self) -> None:
        sources = numbers_in("- Calabaza: vela de cera de soya, 40 horas de duración")
        assert invented_numbers("Dura 60 horas y mide 12 cm", sources) == ["12", "60"]

    def test_los_separadores_de_miles_no_cambian_el_numero(self) -> None:
        sources = numbers_in("Nivel Nacional: $16.940")
        assert invented_numbers("El envío nacional desde $16940", sources) == []

    def test_sin_numeros_no_hay_nada_que_cazar(self) -> None:
        assert invented_numbers("¿Es para ti o para regalar? 🕯️", []) == []

    # Revisión de producción del PR #426 (2026-10-14): lo que la guarda leía mal.

    def test_mil_y_los_centavos_son_el_mismo_monto(self) -> None:
        sources = numbers_in("Contra entrega desde $45.000")
        assert invented_numbers("Desde 45 mil pagas al recibir", sources) == []
        assert invented_numbers("Desde $45.000,00 pagas al recibir", sources) == []

    def test_una_hora_no_es_un_dato_del_producto(self) -> None:
        assert invented_numbers("Te escribo mañana a las 9:00 🌿", []) == []

    def test_un_decimal_no_es_un_entero(self) -> None:
        assert invented_numbers("Mide 2,5 cm", numbers_in("25 velas")) == ["2.5"]
        assert invented_numbers("El recargo es 1,5 %", numbers_in("recargo del 1,5 % con Nequi")) == []

    def test_un_numero_escrito_en_palabras_tambien_se_caza(self) -> None:
        sources = numbers_in("- Calabaza: 40 horas de duración")
        assert invented_numbers("Dura sesenta horas encendida", sources) == ["60"]
        assert invented_numbers("Dura cuarenta horas encendida", sources) == []


def test_los_datos_de_la_tienda_solo_valen_en_el_toque_que_los_trae() -> None:
    """Revisión de producción del PR #426: las cifras de envío y pago se
    permitían en TODOS los toques; el 1er toque, que no las ve, podía
    escribir «$16.940» o «3 tamaños» y pasar."""
    from src.plugins.chats.agent.remarketing.contracts import RemarketingContext
    from src.plugins.chats.agent.remarketing.use_cases.context import hook_number_sources

    facts = "Bogotá: $7.900, 1 a 2 días hábiles. Nacional: $16.940, 3 a 5 días."
    first = RemarketingContext(touch_number=1, silent_after_catalog=True, catalog_facts="- Calabaza: 40 horas.")
    second = RemarketingContext(touch_number=2, silent_after_catalog=True, catalog_facts="- Calabaza: 40 horas.")

    assert "16940" not in hook_number_sources(first, practical_facts=facts)
    assert "40" in hook_number_sources(first, practical_facts=facts)
    assert {"16940", "7900", "5"} <= set(hook_number_sources(second, practical_facts=facts))


# ── El texto del gancho ────────────────────────────────────────────────────────

FICHA = (
    "Productos que existen (4): Calabaza, Momia, Fantasma, Trilogía del Terror. "
    "Cualquier otro producto, forma, envase o presentación NO existe.\n"
    "Ficha de los productos de esta charla:\n"
    "- Calabaza: Vela artesanal en forma de calabaza, cera de palma, aroma frutos rojos. "
    "| Presentación única (no viene en otras formas, envases, tamaños ni versiones)"
)
DATOS_TIENDA = (
    "Tiempos: Bogotá 1 a 2 días hábiles; resto del país 2 a 3 días hábiles. "
    "Envío: tarifas mínimas Bogotá $7.900, nacional $16.940. "
    "Contra entrega: compras desde $45.000 en productos."
)
_SILENT = dict(
    has_order_draft=False,
    transcript=(
        "Cliente: ¡Hola! Quiero más información sobre la colección de Halloween\n"
        "Asesor: 🛍️ El bot envió el catálogo con 4 productos"
    ),
    catalog_facts=FICHA,
    total_touches=3,
    silence_minutes=130,
    silent_after_catalog=True,
    catalog_described=True,
)


def _store(monkeypatch, **extra) -> None:
    from src.plugins.chats.agent.remarketing import prompts

    base = prompts.vocabulary()
    monkeypatch.setattr(prompts, "vocabulary", lambda: {**base, **extra})


class TestPrimerToqueExplica:
    def test_explica_lo_que_vio_en_vez_de_retomar(self, monkeypatch) -> None:
        _store(monkeypatch, hook_examples_explain=["La *Calabaza* huele a frutos rojos 🎃 ¿Es para ti o para regalar?"])
        text = build_remarketing_trigger("miró Halloween", touch_number=1, **_SILENT)
        assert "lo que la tarjeta del catálogo no le dijo" in text
        assert "2 a 4 líneas" in text
        assert "1-2 frases máximo" not in text
        assert "pregunta fácil" in text
        assert "«Hola de nuevo»" in text  # como prohibición
        assert "'quedó pendiente lo de…'" not in text  # ya no se lo sugiere
        assert "La *Calabaza* huele a frutos rojos" in text
        assert "¿Te ayudo a cerrar el pedido?" not in text  # los ejemplos de retomar no van

    def test_le_prohibe_numeros_que_no_esten_en_la_ficha(self, monkeypatch) -> None:
        _store(monkeypatch)
        text = build_remarketing_trigger("miró Halloween", touch_number=1, **_SILENT)
        assert "No repitas precios" in text
        assert "NO se envía" in text

    def test_sin_descripcion_en_la_ficha_queda_el_gancho_corto_de_hoy(self, monkeypatch) -> None:
        _store(monkeypatch)
        text = build_remarketing_trigger(
            "miró Halloween", touch_number=1, **{**_SILENT, "catalog_described": False}
        )
        assert "1-2 frases máximo" in text
        assert "lo que la tarjeta del catálogo no le dijo" not in text

    def test_quien_converso_sigue_con_el_gancho_de_hoy(self, monkeypatch) -> None:
        _store(monkeypatch)
        text = build_remarketing_trigger(
            "miró Halloween", touch_number=1, **{**_SILENT, "silent_after_catalog": False}
        )
        assert "1-2 frases máximo" in text


class TestSegundoToqueResuelveLoPractico:
    def test_resuelve_envio_tiempos_y_pago_con_los_datos_de_la_tienda(self, monkeypatch) -> None:
        _store(monkeypatch, practical_facts=DATOS_TIENDA, hook_examples_practical=["Te cuento: a Bogotá llega en 1 a 2 días hábiles 🚚"])
        text = build_remarketing_trigger("miró Halloween", touch_number=2, **_SILENT)
        assert "DATOS DE LA TIENDA" in text
        assert DATOS_TIENDA in text
        assert "NUNCA una promesa" in text
        assert "mínimas" in text
        assert "Te cuento: a Bogotá llega en 1 a 2 días hábiles" in text
        assert "1-2 frases máximo" not in text

    def test_sin_datos_de_la_tienda_queda_el_seguimiento_de_hoy(self, monkeypatch) -> None:
        _store(monkeypatch, practical_facts=None)  # explícito: el dominio de un clon de forge sí los trae
        text = build_remarketing_trigger("miró Halloween", touch_number=2, **_SILENT)
        assert "DATOS DE LA TIENDA" not in text
        assert "DISTINTO" in text
        assert "1-2 frases máximo" in text

    def test_el_tercero_vuelve_al_cierre_amable(self, monkeypatch) -> None:
        _store(monkeypatch, practical_facts=DATOS_TIENDA)
        text = build_remarketing_trigger("miró Halloween", touch_number=3, **_SILENT)
        assert "ÚLTIMO" in text
        assert "DATOS DE LA TIENDA" not in text


# ── La activity que arma el contexto ───────────────────────────────────────────

_LARGA = (
    "Vela artesanal en forma de calabaza, vertida a mano en cera de palma 100 % vegetal. "
    "Su aroma a frutos rojos se despliega en tres capas: salida, corazón y fondo. "
    "Ideal para decorar la mesa de centro o la entrada de la casa en la temporada de "
    "Halloween, o para regalar a quien disfruta de las fechas especiales con un detalle "
    "hecho en Colombia. Cada pieza es única: las pequeñas variaciones de color son marca "
    "de su origen natural."
)


def _dto(title: str, handle: str, description: str = ""):
    from src.sdk.catalogkit import CatalogProductDTO, CatalogVariantDTO

    return CatalogProductDTO(
        id=f"prod_{handle}", handle=handle, title=title, status="published",
        description=description, variants=[CatalogVariantDTO(id=f"var_{handle}", title="Unico")],
        options={"Unico": ["Unico"]},
    )


class _Catalog:
    def __init__(self, products) -> None:
        self._products = products

    async def search(self, q: str = "", *, limit: int = 10, category: str | None = None):
        from types import SimpleNamespace

        return SimpleNamespace(results=self._products[:limit])


def _seed(vault, sid: str, events: list[dict]) -> None:
    import json

    d = vault / sid
    (d / "sessions").mkdir(parents=True)
    (d / "metadata.json").write_text(
        json.dumps({"tag": "INTERESADO", "motivo": "vio la colección", **_meta()}), encoding="utf-8"
    )
    (d / "sessions" / f"{sid}.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events), encoding="utf-8"
    )


def test_la_ficha_del_que_se_callo_trae_la_descripcion_entera() -> None:
    from src.plugins.chats.agent.remarketing.use_cases.context import catalog_facts_for

    calabaza = _dto("Calabaza", "calabaza", _LARGA)
    corta = catalog_facts_for([calabaza], mentioned="", named=["Calabaza"])
    larga = catalog_facts_for([calabaza], mentioned="", named=["Calabaza"], description_chars=900)
    assert "hecho en Colombia" not in corta  # la de siempre se corta a 280
    assert "hecho en Colombia" in larga


async def test_el_contexto_del_que_se_callo_tras_el_catalogo(_isolate_vault_dir, monkeypatch) -> None:
    from temporalio.testing import ActivityEnvironment

    from src.plugins.chats.agent.remarketing.activities import context as context_mod
    from src.plugins.chats.agent.remarketing.activities.context import read_remarketing_context_activity

    monkeypatch.setenv("REMARKETING_MAX_TOUCHES", "3")
    catalogo = [_dto("Calabaza", "calabaza", _LARGA), _dto("Momia", "momia", "Vela de momia, 40 horas."), _dto("Cubo Love", "cubo-love", "Cubo.")]
    monkeypatch.setattr(context_mod, "get_catalog_client", lambda: _Catalog(catalogo))
    sid = "wa_573001234567"
    _seed(_isolate_vault_dir, sid, [
        _ev("user", HALLOWEEN),
        _ev("assistant", "¡Buenas! Bienvenido a *Hubara*."),
        _catalog("Calabaza", "Momia"),
    ])

    ctx = await ActivityEnvironment().run(read_remarketing_context_activity, sid)

    assert ctx.silent_after_catalog is True
    assert ctx.catalog_described is True
    assert "hecho en Colombia" in ctx.catalog_facts  # ficha entera de lo que vio
    assert "40 horas" in ctx.catalog_facts
    assert "- Cubo Love" not in ctx.catalog_facts  # no lo vio: sin ficha
    assert ctx.total_touches == 3  # el tope de Frecuencia, no 5 fijo
    assert "40" in ctx.allowed_numbers  # la guarda deja pasar lo de la ficha


async def test_sin_descripciones_no_hay_nada_que_explicar(_isolate_vault_dir, monkeypatch) -> None:
    from temporalio.testing import ActivityEnvironment

    from src.plugins.chats.agent.remarketing.activities import context as context_mod
    from src.plugins.chats.agent.remarketing.activities.context import read_remarketing_context_activity

    monkeypatch.setattr(context_mod, "get_catalog_client", lambda: _Catalog([_dto("Calabaza", "calabaza")]))
    sid = "wa_573001234567"
    _seed(_isolate_vault_dir, sid, [_ev("user", HALLOWEEN), _ev("assistant", "¡Buenas!"), _catalog("Calabaza")])

    ctx = await ActivityEnvironment().run(read_remarketing_context_activity, sid)

    assert ctx.silent_after_catalog is True
    assert ctx.catalog_described is False


async def test_el_trigger_recibe_el_caso_y_el_tope_real(monkeypatch) -> None:
    from temporalio.testing import ActivityEnvironment

    from src.plugins.chats.agent.remarketing.activities.context import build_remarketing_trigger_v2_activity
    from src.plugins.chats.agent.remarketing.contracts import RemarketingTriggerInput

    _store(monkeypatch)
    text = await ActivityEnvironment().run(
        build_remarketing_trigger_v2_activity,
        RemarketingTriggerInput(
            motivo="miró Halloween", has_order_draft=False, transcript=_SILENT["transcript"],
            touch_number=1, silence_minutes=130, catalog_facts=FICHA,
            silent_after_catalog=True, catalog_described=True, total_touches=3,
        ),
    )
    assert "lo que la tarjeta del catálogo no le dijo" in text
    assert "toque 1 de 3" in text


def test_los_datos_de_la_tienda_de_ventas_7_son_los_de_ventas() -> None:
    # El 2º toque dice lo que dice Ventas: si cambia una tarifa o el mínimo del
    # contra entrega en `shipping.py`, el paquete tiene que cambiar con ella.
    # En un clon de forge los dos salen de `commerce` (forge/tests lo prueba) y
    # los defaults de `shipping.py` siguen siendo los de la tienda madre.
    from pathlib import Path

    import pytest
    import yaml

    if not (Path(__file__).resolve().parents[4] / "forge").is_dir():
        pytest.skip("clon de forge: los datos de la tienda salen de commerce, no de shipping.py")

    from src.plugins.chats.agent.sales.config.shipping import (
        CASH_ON_DELIVERY_MIN_PRODUCTS_COP,
        SHIPPING_RATE_BOGOTA_COP,
        SHIPPING_RATE_NATIONAL_COP,
    )
    from src.plugins.chats.shared.store_pack import BUNDLES_DIR

    vocab = yaml.safe_load((Path(BUNDLES_DIR) / "ventas-7" / "domain.yaml").read_text(encoding="utf-8"))["vocabulary"]
    facts = vocab["practical_facts"]
    for cop in (SHIPPING_RATE_BOGOTA_COP, SHIPPING_RATE_NATIONAL_COP, CASH_ON_DELIVERY_MIN_PRODUCTS_COP):
        assert f"${cop:,}".replace(",", ".") in facts
    assert numbers_in(" ".join(vocab["hook_examples_practical"])) and not invented_numbers(
        " ".join(vocab["hook_examples_practical"]), numbers_in(facts)
    ), "los ejemplos no pueden traer cifras que no estén en los datos"


async def test_la_ficha_entera_es_de_pocos_productos_y_solo_en_el_primer_toque(_isolate_vault_dir, monkeypatch) -> None:
    """Revisión de producción del PR #426: con el catálogo completo en la
    tarjeta, el gancho llevaba la ficha de 900 caracteres de hasta 30
    productos (~27k caracteres) en TODOS los toques."""
    from temporalio.testing import ActivityEnvironment

    from src.plugins.chats.agent.remarketing.activities import context as context_mod
    from src.plugins.chats.agent.remarketing.activities.context import read_remarketing_context_activity

    titles = [f"Vela {i}" for i in range(8)]
    catalogo = [_dto(t, f"vela-{i}", _LARGA) for i, t in enumerate(titles)]
    monkeypatch.setattr(context_mod, "get_catalog_client", lambda: _Catalog(catalogo))
    sid = "wa_573001234567"
    _seed(_isolate_vault_dir, sid, [_ev("user", HALLOWEEN), _ev("assistant", "¡Buenas!"), _catalog(*titles)])

    ctx = await ActivityEnvironment().run(read_remarketing_context_activity, sid)

    assert ctx.catalog_facts.count("hecho en Colombia") <= 4


class TestLoQueElGanchoNoDebeAfirmar:
    """Revisión de producción del PR #426 (2026-10-14): contradicciones del texto."""

    def test_el_segundo_no_afirma_que_el_primero_le_explico(self, monkeypatch) -> None:
        # El 1º pudo abstenerse, ser plantilla o el gancho corto.
        _store(monkeypatch, practical_facts=DATOS_TIENDA)
        text = build_remarketing_trigger("miró Halloween", touch_number=2, **_SILENT)
        assert "le explicaba lo que vio" not in text

    def test_el_primero_no_invita_a_usar_datos_que_no_ve(self, monkeypatch) -> None:
        # La guarda del 1er toque ya no deja pasar las cifras de la tienda.
        _store(monkeypatch, practical_facts=DATOS_TIENDA)
        text = build_remarketing_trigger("miró Halloween", touch_number=1, **_SILENT)
        assert "en los datos de la tienda" not in text

    def test_el_ultimo_toque_no_pide_terminar_con_pregunta(self, monkeypatch) -> None:
        # Con Frecuencia 2, el 2º es el último: «sin pregunta insistente».
        _store(monkeypatch, practical_facts=DATOS_TIENDA)
        text = build_remarketing_trigger("miró Halloween", touch_number=2, **{**_SILENT, "total_touches": 2})
        assert "Sin pregunta insistente" in text
        assert "Termina con UNA pregunta" not in text


def test_quien_ya_compro_no_es_el_caso_aunque_viera_el_catalogo() -> None:
    """Revisión de producción del PR #426: el gancho de posventa habla de su
    compra, pero el workflow igual agendaba el toque de la mañana y pasaba la
    guarda de números (el número de pedido o su total no están en la ficha)."""
    events = [
        _ev("user", "Quiero la Cruz de Vida"), _ev("assistant", "¡Listo! Tu pedido quedó registrado."),
        _ev("user", HALLOWEEN), _ev("assistant", "¡Buenas de nuevo!"), _catalog("Calabaza"),
    ]
    meta = _meta(episodes=[
        {"episode_id": "ep_1", "msgs_count_at_start": 0, "closed_at_ms": 1, "closing_tag": "COMPRA_EXITOSA"},
        {"episode_id": "ep_2", "msgs_count_at_start": 2},
    ])
    ctx = context_from_metadata(meta, events)

    assert ctx.post_purchase  # el gancho es el de posventa
    assert ctx.silent_after_catalog is False
