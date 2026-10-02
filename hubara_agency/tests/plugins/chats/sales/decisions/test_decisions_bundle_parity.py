"""Paquetes de decisión (PAQUETES_DE_DECISION.md §10): cada capacidad
migrada sale del paquete `ventas` (YAML) y decide EXACTAMENTE igual
que su clase de Python.

La paridad es la compuerta de cada fase: sobre las mismas entradas, el
paquete da la misma regla, el mismo texto de estado y las mismas preguntas
byte a byte, la misma decisión en una grilla de respuestas en los bordes de
cada umbral (con la regla real de cada entrada), el mismo piso, el mismo
comparador y el mismo veredicto completo con Jev (reglas, sombra y jev).
Mientras esto pase, la clase es solo el oráculo de la prueba.
"""
from __future__ import annotations

import itertools
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from src.platform.catalog.color_families import parse_color_families
from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.decisions.bundled import BUNDLES_DIR, CATALOG_PATH, builtin_names
from src.plugins.chats.agent.sales.decisions.capabilities import decide
from src.plugins.chats.agent.sales.decisions.capabilities.agente import Abandono, CierrePorAbandono, Contactar, Contacto
from src.plugins.chats.agent.sales.decisions.capabilities.datos import Datos, DatosDelPedido
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas import Acuse, Baja, Compra, Cortesia, Retoma
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas_pedido import (
    Cantidad,
    Cupon,
    CuponEnJuego,
    FueraDeCatalogo,
    PedidoDelCliente,
    RespuestaDeCantidad,
)
from src.plugins.chats.agent.sales.decisions.capabilities.mapeos import (
    Categoria,
    CategoriaPedida,
    CiudadDeEnvio,
    ColorPedido,
    DatoDelItem,
    FamiliaDeColor,
    ItemDelPedido,
    ProductoNombrado,
    ProductosDeLaCharla,
    ZonaDeEnvio,
)
from src.plugins.chats.agent.sales.decisions.capabilities.texto import (
    Afirmacion,
    AfirmacionSinConsultar,
    Botones,
    Enumeracion,
    Frases,
    Monto,
    OracionesPrecio,
    Persona,
    Relevo,
    Selector,
    TextoAlCliente,
    TextoCatalogo,
)
from src.plugins.chats.agent.sales.decisions.egress import (
    Destinatario,
    DestinatarioDePlantilla,
    DestinatarioPorOracion,
    GreetingCheck,
    OracionesCheck,
    Portavelas,
    PortavelasCheck,
    Preambulo,
    PreambuloCheck,
    Rescate,
    Saludo,
    TextCheck,
)
from src.plugins.chats.agent.sales.decisions.readings import Inbound
from src.sdk import connectorkit
from src.sdk.catalogkit import CatalogCategoryDTO
from src.sdk.connectorkit import PerceptionResult, PromotionDTO, TypedAnswer
from src.sdk.decisionkit import Catalog, check_bundle
from tests.plugins.chats.sales.decisions.test_decisions_datos import EVENTS as SLOT_EVENTS
from tests.plugins.chats.sales.decisions.test_decisions_datos import PACKED, PACKED_VALUES
from tests.plugins.chats.sales.decisions.test_decisions_lecturas_pedido import CARTAGENA
from tests.plugins.chats.sales.decisions.test_decisions_lecturas_pedido import CATALOG as PRODUCTS

SID = "wa_573001234567"
TZ = ZoneInfo("America/Bogota")
NOW = 1_790_000_000_000
READY = "Hola, tu pedido #47 ya está listo. ¿Nos confirmas para coordinar la entrega?"
SAW = [
    {"role": "assistant", "content": READY, "timestamp": "2026-09-29T21:35:34+00:00"},
    {"role": "user", "content": "¿y el envío?", "timestamp": "2026-09-29T21:36:00+00:00"},
    {"role": "assistant", "content": "El envío sale mañana 🙌", "timestamp": "2026-09-29T21:37:00+00:00"},
]
EDGES = [None, 0.0, 0.14, 0.15, 0.16, 0.5, 0.69, 0.7, 0.79, 0.8, 0.81, 0.84, 0.85, 0.86, 0.89, 0.9, 1.0]
EDGES_FEW = [None, 0.1, 0.15, 0.16, 0.5, 0.84, 0.85, 0.9]


def _inbound(text: str | None, events: list | None = None, synthetic: bool = False) -> Inbound:
    return Inbound(session_id=SID, text=text, now_ms=NOW, events=events or [], tz=TZ, synthetic=synthetic)


INBOUND = [
    _inbound("Muchas gracias 🙏", SAW),
    _inbound("Hola cómo están? Son geniales. Muchas gracias", SAW[:1]),
    _inbound("no me escriban más"),
    _inbound("Por favor no me manden más promociones", SAW),
    _inbound("  ya lo recibí, gracias  "),
    _inbound("Sí, confirmo", SAW),
    _inbound("mañana les escribo para pagar"),
    _inbound("yo les escribo cuando tenga la plata"),
    _inbound("el viernes paso por ella"),
    _inbound("", SAW),
    _inbound("   "),
    _inbound(None, SAW),
    _inbound("Foto de una vela rosada", SAW, synthetic=True),
]
TRANSCRIPT = "cliente: hola\ntienda: ¡Hola! ¿En qué te ayudo?\ncliente: ya lo recibí, gracias"
CARD = {"role": "assistant", "kind": "ui_component", "component_kind": "order_confirmation",
        "content": "Tu pedido: Cubo Love x1. ¿Confirmas?", "timestamp": "2026-09-29T21:38:00+00:00"}
SHIPPING_CARD = {**CARD, "component_kind": "shipping_flow", "content": "Datos de envío"}
COMPRA_INBOUND = [
    *INBOUND,
    _inbound("Sí, confirmo", [*SAW, CARD]),
    _inbound("dale, mañana te pago", [*SAW, CARD]),
    _inbound("sí", [*SAW, SHIPPING_CARD]),
    replace(_inbound("Confirmar", SAW), interactive={"type": "button_reply", "id": "confirm_order", "title": "Confirmar"}),
    replace(_inbound(None, SAW), order={"product_items": [{"product_retailer_id": "cubo-love", "quantity": 1}]}),
    replace(_inbound("sí, ese", SAW), metadata={"episodes": [{"episode_id": "ep_1", "closed_at_ms": None,
                                                             "order_draft": {"slots": {"producto": "Cubo Love"}}}]},
            stage="etapa_cierre"),
]


def _promo(**kw: Any) -> PromotionDTO:
    base = dict(
        id="promo_amor", code="AMOR2026", discount_type="percentage", value=10, currency_code=None,
        target_type="items", allocation="across", max_quantity=None, product_ids=("prod_cubo",), variant_ids=(),
        collection_ids=(), min_subtotal_cop=None, is_automatic=False, status="active", starts_at_ms=None,
        ends_at_ms=None, budget_type=None, budget_limit=None, budget_used=None, description="Amor y amistad",
    )
    return PromotionDTO(**{**base, **kw})


def _coupon_md(**promo: Any) -> dict[str, Any]:
    return {"episodes": [{
        "episode_id": "ep_1", "closed_at_ms": None,
        "applied_coupon": {"code": "AMOR2026", "promotion": asdict(_promo(**promo)), "applied_at_ms": 1,
                           "eligible_products": [{"handle": "cubo-love", "title": "Cubo Love"}]},
    }]}


CATEGORIES = (
    CatalogCategoryDTO(slug="velas-aromaticas", label="Velas Aromáticas", product_count=1),
    CatalogCategoryDTO(slug="velas-religiosas", label="Velas Religiosas", product_count=2),
    CatalogCategoryDTO(slug="ninguno", label="Ninguno", product_count=1),  # choca con una opción reservada
)
FAMILIES = parse_color_families({
    "version": 1,
    "modifiers": ["claro", "clarito"],
    "families": {
        "rojo": {"label": "Rojo", "shades": ["rojo", "vinotinto", "vino"]},
        "azul": {"label": "Azul", "shades": ["azul", "celeste", "marino"]},
        "amarillo": {"label": "Amarillo", "shades": ["amarillo", "dorado", "oro"]},
    },
})
COLORS = ("Rojo", "Azul", "Amarillo", "Dorado", "Azul marino")
ITEMS = ("Duo Zodiacal", "Velón Amor Eterno", "Cubo Love")
PARA_EL_VELON = [{"role": "assistant", "content": "¿Qué aroma quieres?"},
                 {"role": "user", "content": "La lavanda es para el velón"}]
AROMAS = ("Lavanda", "Vainilla", "Canela", "Limoncillo", "Caballero de la noche")
COLORES = ("Rojo", "Blanco", "Negro", "Dorado")


def _single(qid: str) -> list[list[TypedAnswer]]:
    return [[TypedAnswer(id=qid, kind="noul", p=p)] if p is not None else [] for p in EDGES]


def _noul(*ids: str) -> list[list[TypedAnswer]]:
    grids = []
    for ps in itertools.product(EDGES_FEW, repeat=len(ids)):
        grids.append([TypedAnswer(id=q, kind="noul", p=p) for q, p in zip(ids, ps) if p is not None])
    return grids


def _choice(qid: str, options: list[str], probs: tuple[float, ...] = (0.5, 0.69, 0.7, 0.84, 0.85, 0.9)) -> list[list[TypedAnswer]]:
    out: list[list[TypedAnswer]] = [[]]
    for option in options:
        for p in probs:
            out.append([TypedAnswer(id=qid, kind="choice", choice=option, probs=((option, p),), confidence=p)])
    return out


def _mix(*grids: list[list[TypedAnswer]]) -> list[list[TypedAnswer]]:
    return [list(itertools.chain(*combo)) for combo in itertools.product(*grids)]


_QUANTITY = [str(n) for n in range(1, 21)] + ["21", "otra", "ninguna"]
_COMPRA = ["confirma", "aplaza", "rechaza", "pregunta", "da_datos", "elige", "se_despide", "otro"]
_PART_CHOICES = ["mensaje_al_cliente", "razonamiento", "otra"]


def _choices(*ids: str) -> list[list[TypedAnswer]]:
    """Una opción por parte, en los bordes del umbral (0,79 / 0,8)."""
    return _mix(*(_choice(q, _PART_CHOICES, probs=(0.79, 0.8)) for q in ids))


DESPEDIDA = "¡Listo! Tu pedido quedó registrado. El portavelas viene en dorado. Gracias por tu compra."
_WHAT_IS_IT = ["mensaje_al_cliente", "razonamiento", "reporte_interno", "acuse_al_sistema", "deliberacion", "otra"]
CASES: dict[str, tuple[Any, list[Any], list[list[TypedAnswer]]]] = {
    "baja": (Baja(), INBOUND, _single("baja.pide")),
    "cortesia": (Cortesia(), INBOUND, _single("cortesia.solo")),
    "contactar": (
        Contactar(),
        [Contacto(TRANSCRIPT), Contacto(TRANSCRIPT, touch_number=2), Contacto(TRANSCRIPT, 3, 90),
         Contacto(TRANSCRIPT, None, 15), Contacto("   ")],
        _noul("contactar.sobra", "contactar.terminada"),
    ),
    "cierre": (
        CierrePorAbandono(),
        [Abandono(TRANSCRIPT), Abandono(TRANSCRIPT, purchase_confirmed=True), Abandono(TRANSCRIPT, True, True),
         Abandono(TRANSCRIPT, False, True), Abandono("")],
        _choice("cierre.etiqueta", ["confirmado_sin_datos", "interesado", "rechazo", "compra_exitosa", "otra_cosa"]),
    ),
    "retoma": (
        Retoma(), INBOUND,
        _mix(_noul("retoma.aplaza", "retoma.cortesia"),
             _choice("retoma.cuando", ["hoy", "otro_dia_con_fecha", "otro_dia_sin_fecha", "no_aplica"], probs=(0.9,))),
    ),
    "cantidad": (
        Cantidad(),
        [RespuestaDeCantidad("¿Cuántas quieres?", "2 porfa"), RespuestaDeCantidad("¿Cuántas quieres?", "quiero tres"),
         RespuestaDeCantidad("¿Te la envío?", "sí"), RespuestaDeCantidad("¿Cuántas?", "[foto]"),
         RespuestaDeCantidad("¿Cuántas?", "2", open_slot=False), RespuestaDeCantidad(None, "2"),
         RespuestaDeCantidad("x" * 700 + " ¿Cuántas quieres?", "5")],
        _mix(_noul("cantidad.pregunto"), _choice("cantidad.dio", _QUANTITY, probs=(0.69, 0.7, 0.84, 0.85))),
    ),
    "zona_de_envio": (
        ZonaDeEnvio(),
        [CiudadDeEnvio("Bogotá"), CiudadDeEnvio("Chía"), CiudadDeEnvio("Medellín"), CiudadDeEnvio("  "), CiudadDeEnvio(None)],
        _choice("zona.cual", ["bogota", "nacional", "ambiguo", "ninguno", "otra"]),
    ),
    "selector": (
        Selector(),
        [Botones("¿Cuál te gusta?", ("Rosado", "Azul")), Botones("¿Seguimos?", ("Sí", "No"), rule_rejected=("Sí",)),
         Botones("Elige", ("Lavanda",), by_id=("color.rosado",)), Botones("Nada", ())],
        _single("selector.elige"),
    ),
    "afirmacion": (
        AfirmacionSinConsultar(),
        [Afirmacion("Sí, hay stock y llega mañana"), Afirmacion("Gracias", ("search_products",)), Afirmacion("  ")],
        _single("afirmacion.sin_consultar"),
    ),
    "relevo": (
        Relevo(),
        [TextoAlCliente("Un colega del equipo coordina la entrega contigo"), TextoAlCliente("Listo, gracias"),
         TextoAlCliente("   ")],
        _single("relevo.promete"),
    ),
    # ── F3: las B ──
    "compra": (
        Compra(), COMPRA_INBOUND,
        _mix(_choice("compra.que_hace", _COMPRA, probs=(0.69, 0.7, 0.84, 0.85, 0.9)),
             [[], *_single("compra.pregunta_compra")[1:]][::2] + [[TypedAnswer(id="compra.pregunta_compra", kind="noul", p=0.2)],
                                                                 [TypedAnswer(id="compra.pregunta_compra", kind="noul", p=0.21)]]),
    ),
    "acuse": (Acuse(), INBOUND, _single("acuse.solo_cortesia")),
    "cupon": (
        Cupon(),
        [CuponEnJuego(_coupon_md(), "Te paso el código postal: 110111"),
         CuponEnJuego(_coupon_md(), "¿Todavía aplica el beneficio del mensaje?", SAW),
         CuponEnJuego(_coupon_md(), "   "), CuponEnJuego(_coupon_md(target_type="shipping_methods"), "¿y el cupón?"),
         CuponEnJuego({}, "¿y el descuento?")],
        _single("cupon.habla"),
    ),
    "categoria": (
        Categoria(),
        [CategoriaPedida("velas de santos", CATEGORIES), CategoriaPedida("Velas Aromáticas", CATEGORIES),
         CategoriaPedida("estampas religiosas", CATEGORIES[:2]), CategoriaPedida("  ", CATEGORIES),
         CategoriaPedida("religiosas", ())],
        _choice("categoria.cual", ["velas_aromaticas", "velas_religiosas", "ninguno_2", "ambiguo", "ninguno", "otra"]),
    ),
    "familia_de_color": (
        FamiliaDeColor(),
        [ColorPedido("bordó", COLORS, FAMILIES, "Cubo Love"), ColorPedido("oro", COLORS, FAMILIES),
         ColorPedido("rojo", COLORS, FAMILIES, "Cubo Love"), ColorPedido("  ", COLORS, FAMILIES),
         ColorPedido("azul", (), FAMILIES)],
        _choice("color.cual", ["rojo", "azul", "amarillo", "dorado", "azul_marino", "ambiguo", "ninguno", "otra"]),
    ),
    "item_del_pedido": (
        ItemDelPedido(),
        [DatoDelItem({"aroma": "Lavanda"}, ITEMS, 0, (True, True, False), PARA_EL_VELON),
         DatoDelItem({"aroma": "Lavanda", "color": "Rojo"}, ITEMS, 2, (True, None, True)),
         DatoDelItem({"aroma": "Lavanda"}, ITEMS, 0, (False, True, False)),
         DatoDelItem({"aroma": "Lavanda"}, ITEMS, 1, (True, True, True), PARA_EL_VELON)],
        _choice("item.cual", ["item_1", "item_2", "item_3", "item_4", "ambiguo", "ninguno"]),
    ),
    "producto_nombrado": (
        ProductoNombrado(),
        [ProductosDeLaCharla("el Cubo Love y la de los corazoncitos", ("Cubo Love", "Cubo de corazón", "Velón Koala"),
                             ("Cubo Love",)),
         ProductosDeLaCharla("hola", ("Cubo Love",)), ProductosDeLaCharla("  ", ("Cubo Love",)),
         ProductosDeLaCharla("la de koala", ())],
        _choice("producto.nombrado", ["cubo_love", "cubo_de_corazon", "velon_koala", "ambiguo", "ninguno", "otra"]),
    ),
    "enumeracion": (
        Enumeracion(),
        [TextoCatalogo("Tenemos estos aromas: Lavanda, Vainilla, Canela y Limoncillo. ¿Cuál te gusta?", AROMAS, COLORES),
         TextoCatalogo("Colores: Rojo, Blanco, Negro y Dorado", AROMAS, COLORES),
         TextoCatalogo("Rojo · Lavanda $20.000 y Blanco · Vainilla $22.000, también Negro · Canela y Dorado · "
                       "Limoncillo", AROMAS, COLORES),
         TextoCatalogo("Lavanda o Vainilla", AROMAS, COLORES)],
        _choice("enumeracion.que", ["aromas", "colores", "combinaciones_cupon", "productos", "nada"]),
    ),
    # ── F3: egreso (una pregunta) ──
    "destinatario": (
        Destinatario(),
        [TextCheck("Usa el código VELAS_10 al pagar"), TextCheck("ESTADO: etiqueta INTERESADO asignada", extended=False),
         TextCheck("  ")],
        _choice("egreso.destinatario", _WHAT_IS_IT),
    ),
    "destinatario_plantilla": (
        DestinatarioDePlantilla(),
        [TextCheck("Te esperamos con tu vela favorita"), TextCheck("ESTADO: etiqueta INTERESADO asignada"), TextCheck("")],
        _choice("egreso.destinatario", _WHAT_IS_IT),
    ),
    "saludo": (
        Saludo(),
        [GreetingCheck(True, (), ("¡Hola! Bienvenida a la tienda",)), GreetingCheck(True, ("send_product_card",), ()),
         GreetingCheck(False, (), ("Hola",)), GreetingCheck(True, (), ("Claro, te cuento",) * 9),
         GreetingCheck(True, (), ("  ", "Te cuento los precios"))],
        _single("egreso.saludo"),
    ),
    # ── F4: ítem por ítem (las C y el egreso por partes) ──
    "persona": (
        Persona(),
        [Frases(("Hola, soy tu asistente virtual 🤖", "Te cuento los precios.")),
         Frases(("Te paso con una persona del equipo.", "Gracias por escribirnos.", "Un humano te responde ya.")),
         Frases(()), Frases(tuple(f"Oración {i}." for i in range(13)))],
        _noul("persona.1", "persona.2", "persona.3"),
    ),
    "monto": (
        Monto(),
        [OracionesPrecio(("Desde $45.000 tienes el Cubo Love 🤍.", "El envío a Bogotá cuesta $12.900.")),
         OracionesPrecio(()), OracionesPrecio(tuple(f"Vale ${i}.000." for i in range(13)))],
        _noul("monto.1", "monto.2"),
    ),
    "datos": (
        Datos(),
        [DatosDelPedido(PACKED_VALUES, PACKED),
         DatosDelPedido((("ciudad", "Chía"), ("nombre_recibe", "Carlos Ruiz"), ("telefono", "3009998877")), SLOT_EVENTS),
         DatosDelPedido((("ciudad", "Medellín"), ("barrio", "  "), ("color", "Rojo"), ("ciudad", "Chía")), SLOT_EVENTS),
         DatosDelPedido((), SLOT_EVENTS), DatosDelPedido((("ciudad", "Cali"),), ())],
        _noul("datos.ciudad", "datos.nombre_recibe", "datos.telefono"),
    ),
    "fuera_de_catalogo": (
        FueraDeCatalogo(),
        [PedidoDelCliente(CARTAGENA, PRODUCTS), PedidoDelCliente("Estás y en vaso también", PRODUCTS),
         PedidoDelCliente("[el cliente envió una foto: vela rosa con diseño de dragón]", PRODUCTS),
         PedidoDelCliente("¿El cubo viene en azul?", PRODUCTS), PedidoDelCliente("", PRODUCTS)],
        _noul("fuera_de_catalogo.termino_1", "fuera_de_catalogo.termino_2", "fuera_de_catalogo.termino_3"),
    ),
    "preambulo": (
        Preambulo(),
        [PreambuloCheck("Aquí tienes: ¡Hola! Te cuento los precios."),
         PreambuloCheck("Claro, aquí va el mensaje para el cliente:\nHola, ¿cómo estás?\nTe cuento."),
         PreambuloCheck("Hola"), PreambuloCheck("Here's my attempt: hola, te cuento"),
         PreambuloCheck("Uno. Dos. Tres. Cuatro. Cinco. Seis. Siete.")],
        _noul("preambulo.1", "preambulo.2", "preambulo.3"),
    ),
    "destinatario_oracion": (
        DestinatarioPorOracion(),
        [OracionesCheck(("Hola, ¿cómo estás?", "ESTADO: etiqueta INTERESADO asignada", "Te cuento los precios.")),
         OracionesCheck(()), OracionesCheck(tuple(f"Parte {i}." for i in range(9)))],
        _choices("egreso.oracion.1", "egreso.oracion.2", "egreso.oracion.3"),
    ),
    "rescate": (
        Rescate(),
        [TextCheck("Hola, te cuento.\n\nESTADO: etiqueta INTERESADO asignada\n\nChao"), TextCheck("Solo un párrafo"),
         TextCheck(""), TextCheck("\n\n".join(f"Párrafo {i}." for i in range(9)))],
        _choices("egreso.rescate.1", "egreso.rescate.2", "egreso.rescate.3"),
    ),
    "portavelas": (
        Portavelas(),
        [PortavelasCheck(DESPEDIDA, True, False), PortavelasCheck(DESPEDIDA, True, True), PortavelasCheck(DESPEDIDA, False),
         PortavelasCheck("Tu pedido no incluye portavelas. Gracias.", True, None), PortavelasCheck("", True, False)],
        _noul("egreso.portavelas.1", "egreso.portavelas.2", "egreso.portavelas.3", "egreso.portavelas.4"),
    ),
}
NAMES = sorted(CASES)


def _result(answers: list[TypedAnswer]) -> PerceptionResult:
    return PerceptionResult(ok=True, answers=tuple(answers), provider="fake", model="typesafe/jev-1.13-20260917")


@pytest.fixture(autouse=True)
def _hubara_bundle(monkeypatch):
    monkeypatch.delenv("SALES_DECISIONS_BUNDLE", raising=False)
    registry.reset()
    yield
    registry.reset()


@pytest.mark.parametrize("name", NAMES)
def test_it_comes_from_the_bundle(name: str) -> None:
    assert getattr(registry.capability(name), "bundle", "") == "ventas@1"


@pytest.mark.parametrize("name", NAMES)
def test_rule_state_and_questions_are_byte_for_byte_the_same(name: str) -> None:
    old, inputs, _ = CASES[name]
    new = registry.capability(name)

    assert new.name == old.name
    assert dict(new.thresholds) == dict(old.thresholds)
    for inp in inputs:
        assert new.rule(inp) == old.rule(inp), inp
        assert new.ask(inp) == old.ask(inp), inp
        # Byte a byte: también el orden de las opciones (Jev las lee en ese orden).
        got, want = new.ask(inp), old.ask(inp)
        if want is not None:
            assert [list(q.criteria.items()) for q in got[1]] == [list(q.criteria.items()) for q in want[1]], inp


@pytest.mark.parametrize("name", NAMES)
def test_the_decision_is_the_same_on_the_whole_answer_grid(name: str) -> None:
    old, inputs, grid = CASES[name]
    new = registry.capability(name)

    for inp in inputs:
        rule = old.rule(inp)
        for answers in grid:
            result = _result(answers)
            got = new.decide(inp, result, rule, new.thresholds)
            want = old.decide(inp, result, rule, old.thresholds)
            assert got == want, (inp, answers, got, want)
            assert type(got) is type(want), (inp, answers, got, want)


@pytest.mark.parametrize("name", NAMES)
def test_floor_and_comparison_are_the_same(name: str) -> None:
    old, inputs, grid = CASES[name]
    new = registry.capability(name)

    for inp in inputs:
        rule = old.rule(inp)
        values = {repr(v): v for v in (old.decide(inp, _result(a), rule, old.thresholds) for a in grid) if v is not None}
        for jev in [rule, *values.values()]:
            assert new.floor(inp, rule, jev) == old.floor(inp, rule, jev), (inp, jev)
            assert new.same(rule, jev) == old.same(rule, jev), (inp, jev)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("provider", ["reglas", "sombra", "jev"])
async def test_the_whole_verdict_is_the_same(monkeypatch, name: str, provider: str) -> None:
    old, inputs, grid = CASES[name]
    new = registry.capability(name)
    for answers in grid[:: max(1, len(grid) // 6)]:
        fake = FakePerceptionAdapter({a.id: a for a in answers})
        monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle, fake=fake: fake)
        for inp in inputs:
            got = await decide(new, inp, provider=provider, profile_id="jev-v3", session_id=SID)
            want = await decide(old, inp, provider=provider, profile_id="jev-v3", session_id=SID)
            assert got.bundle == "ventas@1" and want.bundle == ""
            assert replace(got, bundle="") == want, (name, inp, provider, answers)


def test_the_shipped_bundle_compiles() -> None:
    assert check_bundle(BUNDLES_DIR / "ventas", CATALOG_PATH) == []


def test_the_catalog_and_the_code_declare_the_same_builtins_and_constants() -> None:
    """El catálogo es el "header" que lee el certificador: cada builtin que
    dice tener existe en código con la misma clase, y viceversa; cada
    constante vale lo mismo que en el código."""
    import yaml

    from src.plugins.chats.agent.sales.variant_enumeration import MIN_ENUMERATED
    from src.sdk.messagingkit import DEFERRAL_KIND_OPEN, OPEN_DEFERRAL_MS

    catalog = Catalog.model_validate(yaml.safe_load(Path(CATALOG_PATH).read_text(encoding="utf-8")))
    declared = {name: spec.kind for name, spec in catalog.builtins.items()}
    registered = builtin_names()

    assert declared == registered
    assert {n: c.value for n, c in catalog.constants.items()} == {
        "DEFERRAL_KIND_OPEN": DEFERRAL_KIND_OPEN,
        "OPEN_DEFERRAL_MS": OPEN_DEFERRAL_MS,
        "MIN_ENUMERATED": MIN_ENUMERATED,
    }


def test_the_cli_certifies_the_repo_bundles(capsys) -> None:
    from src.sdk.cli import main

    assert main(["decisions", "check"]) == 0
    assert "OK ventas@1" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_the_ingest_reads_from_the_bundle(tmp_path: Path, monkeypatch) -> None:
    """La traza de cada lectura dice con qué paquete se decidió."""
    from src.plugins.chats.agent.sales.decisions.readings import EngineReadings

    fake = FakePerceptionAdapter({"cortesia.solo": TypedAnswer(id="cortesia.solo", kind="noul", p=0.9)})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    monkeypatch.setenv("DECISIONS_BOT", "B")

    readings = await EngineReadings(tmp_path).read(INBOUND[0])

    bundles = {v["capability"]: v["bundle"] for v in readings.verdicts}
    assert bundles == {"compra": "ventas@1", "retoma": "ventas@1", "baja": "ventas@1", "cortesia": "ventas@1"}
    assert readings.courtesy_only is True


def test_the_catalog_says_which_states_take_the_items_and_the_code_agrees() -> None:
    """`takes_items` del catálogo ⇔ el builtin de estado recibe `items=`."""
    import inspect

    import yaml

    from src.plugins.chats.agent.sales.decisions.bundled import builtin

    catalog = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    for name, spec in catalog["builtins"].items():
        if spec["kind"] != "state":
            continue
        takes = "items" in inspect.signature(builtin("state", name)).parameters
        assert bool(spec.get("takes_items")) == takes, name
