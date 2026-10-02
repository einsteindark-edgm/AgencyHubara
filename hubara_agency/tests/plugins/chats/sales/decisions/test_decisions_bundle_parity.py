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
from dataclasses import replace
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.decisions.bundled import BUNDLES_DIR, CATALOG_PATH, builtin_names
from src.plugins.chats.agent.sales.decisions.capabilities import decide
from src.plugins.chats.agent.sales.decisions.capabilities.agente import Abandono, CierrePorAbandono, Contactar, Contacto
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas import Baja, Cortesia, Retoma
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas_pedido import Cantidad, RespuestaDeCantidad
from src.plugins.chats.agent.sales.decisions.capabilities.mapeos import CiudadDeEnvio, ZonaDeEnvio
from src.plugins.chats.agent.sales.decisions.capabilities.texto import (
    Afirmacion,
    AfirmacionSinConsultar,
    Botones,
    Relevo,
    Selector,
    TextoAlCliente,
)
from src.plugins.chats.agent.sales.decisions.readings import Inbound
from src.sdk import connectorkit
from src.sdk.connectorkit import PerceptionResult, TypedAnswer
from src.sdk.decisionkit import Catalog, check_bundle

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

    from src.sdk.messagingkit import DEFERRAL_KIND_OPEN, OPEN_DEFERRAL_MS

    catalog = Catalog.model_validate(yaml.safe_load(Path(CATALOG_PATH).read_text(encoding="utf-8")))
    declared = {name: spec.kind for name, spec in catalog.builtins.items()}
    registered = builtin_names()

    assert declared == registered
    assert {n: c.value for n, c in catalog.constants.items()} == {
        "DEFERRAL_KIND_OPEN": DEFERRAL_KIND_OPEN,
        "OPEN_DEFERRAL_MS": OPEN_DEFERRAL_MS,
    }


def test_the_cli_certifies_the_repo_bundles(capsys) -> None:
    from src.sdk.cli import main

    assert main(["decisions", "check"]) == 0
    assert "OK ventas@1" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_the_ingest_reads_from_the_bundle(tmp_path: Path, monkeypatch) -> None:
    """La traza de cada lectura dice con qué paquete se decidió (las que
    todavía son clases, con nada)."""
    from src.plugins.chats.agent.sales.decisions.readings import EngineReadings

    fake = FakePerceptionAdapter({"cortesia.solo": TypedAnswer(id="cortesia.solo", kind="noul", p=0.9)})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    monkeypatch.setenv("DECISIONS_BOT", "B")

    readings = await EngineReadings(tmp_path).read(INBOUND[0])

    bundles = {v["capability"]: v["bundle"] for v in readings.verdicts}
    assert bundles == {"compra": "", "retoma": "ventas@1", "baja": "ventas@1", "cortesia": "ventas@1"}
    assert readings.courtesy_only is True
