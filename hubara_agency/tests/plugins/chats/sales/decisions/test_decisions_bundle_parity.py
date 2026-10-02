"""Paquetes de decisión, fase F1 (PAQUETES_DE_DECISION.md §10): `baja` y
`cortesia` salen del paquete `hubara-ventas` (YAML) y deciden EXACTAMENTE
igual que las clases de hoy.

La paridad es la compuerta: sobre las mismas entradas, el paquete da la misma
regla, el mismo texto de estado y las mismas preguntas byte a byte, la misma
decisión en los bordes de cada umbral, el mismo piso y el mismo veredicto
completo con Jev (reglas, sombra y jev). Mientras esto pase, la clase es solo
el oráculo de la prueba.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions.bundled import (
    BUILTINS,
    BUNDLE_DIR,
    CATALOG_PATH,
    bundled_capability,
)
from src.plugins.chats.agent.sales.decisions.capabilities import decide
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas import Baja, Cortesia
from src.plugins.chats.agent.sales.decisions.readings import Inbound
from src.sdk import connectorkit
from src.sdk.connectorkit import PerceptionResult, TypedAnswer
from src.sdk.decisionkit import Catalog, check_bundle

SID = "wa_573001234567"
TZ = ZoneInfo("America/Bogota")
READY = "Hola, tu pedido #47 ya está listo. ¿Nos confirmas para coordinar la entrega?"
SAW = [
    {"role": "assistant", "content": READY, "timestamp": "2026-09-29T21:35:34+00:00"},
    {"role": "user", "content": "¿y el envío?", "timestamp": "2026-09-29T21:36:00+00:00"},
    {"role": "assistant", "content": "El envío sale mañana 🙌", "timestamp": "2026-09-29T21:37:00+00:00"},
]

INPUTS = [
    Inbound(session_id=SID, text=text, now_ms=1_790_000_000_000, events=events, tz=TZ, synthetic=synthetic)
    for text, events, synthetic in [
        ("Muchas gracias 🙏", SAW, False),
        ("Hola cómo están? Son geniales. Muchas gracias", SAW[:1], False),
        ("no me escriban más", [], False),
        ("Por favor no me manden más promociones", SAW, False),
        ("  ya lo recibí, gracias  ", [], False),
        ("Sí, confirmo", SAW, False),
        ("", SAW, False),
        ("   ", [], False),
        (None, SAW, False),
        ("Foto de una vela rosada", SAW, True),
    ]
]
EDGES = [None, 0.0, 0.14, 0.15, 0.16, 0.5, 0.79, 0.8, 0.81, 0.84, 0.85, 0.86, 1.0]
PAIRS = [("baja", Baja(), "baja.pide"), ("cortesia", Cortesia(), "cortesia.solo")]


def _result(qid: str, p: float | None) -> PerceptionResult:
    answers = () if p is None else (TypedAnswer(id=qid, kind="noul", p=p),)
    return PerceptionResult(ok=True, answers=answers, provider="fake", model="typesafe/jev-1.13-20260917")


@pytest.mark.parametrize(("name", "old", "qid"), PAIRS)
def test_rule_state_and_questions_are_byte_for_byte_the_same(name: str, old: Any, qid: str) -> None:
    new = bundled_capability(name)

    assert new.name == old.name
    assert new.thresholds == dict(old.thresholds)
    for inp in INPUTS:
        assert new.rule(inp) == old.rule(inp), inp.text
        assert new.ask(inp) == old.ask(inp), inp.text


@pytest.mark.parametrize(("name", "old", "qid"), PAIRS)
def test_the_decision_is_the_same_at_every_threshold_edge(name: str, old: Any, qid: str) -> None:
    new = bundled_capability(name)
    inp = INPUTS[0]

    for p in EDGES:
        for rule in (False, True):
            result = _result(qid, p)
            assert new.decide(inp, result, rule, new.thresholds) == old.decide(inp, result, rule, old.thresholds), (p, rule)


@pytest.mark.parametrize(("name", "old", "qid"), PAIRS)
def test_floor_and_comparison_are_the_same(name: str, old: Any, qid: str) -> None:
    new = bundled_capability(name)

    for rule in (False, True):
        for jev in (False, True):
            assert new.floor(INPUTS[0], rule, jev) == old.floor(INPUTS[0], rule, jev)
            assert new.same(rule, jev) == old.same(rule, jev)


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "old", "qid"), PAIRS)
@pytest.mark.parametrize("provider", ["reglas", "sombra", "jev"])
@pytest.mark.parametrize("p", [0.05, 0.5, 0.9])
async def test_the_whole_verdict_is_the_same(monkeypatch, name: str, old: Any, qid: str, provider: str, p: float) -> None:
    fake = FakePerceptionAdapter({qid: TypedAnswer(id=qid, kind="noul", p=p)})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    new = bundled_capability(name)

    for inp in INPUTS:
        got = await decide(new, inp, provider=provider, profile_id="jev-v3", session_id=SID)
        want = await decide(old, inp, provider=provider, profile_id="jev-v3", session_id=SID)
        assert got.bundle == "hubara-ventas@1" and want.bundle == ""
        assert replace(got, bundle="") == want, (inp.text, provider, p)


def test_the_shipped_bundle_compiles() -> None:
    assert check_bundle(BUNDLE_DIR, CATALOG_PATH) == []


def test_the_catalog_and_the_code_declare_the_same_builtins() -> None:
    """El catálogo es el "header" que lee el certificador: cada builtin que
    dice tener existe en código con la misma clase, y viceversa."""
    import yaml

    catalog = Catalog.model_validate(yaml.safe_load(Path(CATALOG_PATH).read_text(encoding="utf-8")))
    declared = {name: spec.kind for name, spec in catalog.builtins.items()}
    registered = {name: kind for kind, impls in BUILTINS.items() for name in impls}

    assert declared == registered


def test_the_cli_certifies_the_repo_bundles(capsys) -> None:
    from src.sdk.cli import main

    assert main(["decisions", "check"]) == 0
    assert "OK hubara-ventas@1" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_the_ingest_reads_baja_and_cortesia_from_the_bundle(tmp_path: Path, monkeypatch) -> None:
    """La traza de cada lectura dice con qué paquete se decidió (las que
    todavía son clases, con nada)."""
    from src.plugins.chats.agent.sales.decisions.readings import EngineReadings

    fake = FakePerceptionAdapter({"cortesia.solo": TypedAnswer(id="cortesia.solo", kind="noul", p=0.9)})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    monkeypatch.setenv("DECISIONS_BOT", "B")

    readings = await EngineReadings(tmp_path).read(INPUTS[0])

    bundles = {v["capability"]: v["bundle"] for v in readings.verdicts}
    assert bundles == {"compra": "", "retoma": "", "baja": "hubara-ventas@1", "cortesia": "hubara-ventas@1"}
    assert readings.courtesy_only is True
