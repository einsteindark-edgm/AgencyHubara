"""`ventas-9`: es `ventas-8@8` con una decisión nueva que solo anota, `objecion`,
y nada más.

Informe de estrategia del ciclo (2026-11-10): la mayoría de las conversaciones
se queda en la primera etapa y producción no guarda qué objeción puso el
cliente. `objecion` le pregunta a Jev, por cada mensaje del cliente, si puso
una objeción y de qué tipo (lista cerrada: precio, envío, tiempo de entrega,
confianza, producto, o ninguna) y la deja en las decisiones de la conversación
(`evals/decisions.jsonl`) para el informe de estrategia.

Solo anota: su valor no entra a las lecturas del ingest, no cambia el turno, no
va a la cola de desacuerdos, y si falla o se demora el mensaje sigue igual.

Textos sintéticos (nunca el de un cliente). En un clon de forge `ventas-9` no
viaja (experimento de esta tienda): se salta.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.decisions.decision_log import read_decisions
from src.plugins.chats.agent.sales.decisions.readings import EngineReadings, Inbound
from src.plugins.chats.shared.store_pack import BUNDLES_DIR, CATALOG_PATH
from src.sdk.connectorkit import TypedAnswer

V8 = BUNDLES_DIR / "ventas-8"
V9 = BUNDLES_DIR / "ventas-9"
IN_FORGE_CLONE = not (Path(__file__).resolve().parents[6] / "forge").is_dir()
SID = "wa_573001111111"
NOW = 1_790_000_000_000
NEW = "objecion"
QUESTION = "objecion.tipo"
KINDS = ("precio", "envio", "tiempo_entrega", "confianza", "producto")

#: Con la forma de lo que escribe un cliente (sintético).
TOO_EXPENSIVE = "Uy no, me parece muy cara para ser una sola vela"
JUST_ASKING = "¿Tienen velas de lavanda?"


def _need_v9() -> None:
    if IN_FORGE_CLONE:
        pytest.skip("clon de forge: los paquetes de prueba de esta tienda no viajan")
    assert (V9 / "bundle.yaml").is_file(), "falta el paquete ventas-9"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _ventas_9(monkeypatch):
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-9")
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    registry.reset()
    yield
    registry.reset()


def _says(choice: str, confidence: float = 0.92) -> FakePerceptionAdapter:
    return FakePerceptionAdapter({
        QUESTION: TypedAnswer(id=QUESTION, kind="choice", choice=choice, probs=((choice, confidence),), confidence=confidence),
    })


@pytest.fixture
def oracle(monkeypatch):
    """Ninguna prueba sale a la red: Jev es el falso."""
    from src.sdk import connectorkit

    holder: dict[str, Any] = {"fake": _says("precio")}

    def _get(_oracle: str):
        return holder["fake"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return holder


@pytest.fixture
def shadow(tmp_path: Path, monkeypatch) -> Path:
    """`objecion` en sombra, como la deja el operador por comando; lo demás, apagado."""
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "shadow")
    control = tmp_path / "_rollout" / "decisions.json"
    control.parent.mkdir(parents=True)
    control.write_text(json.dumps({"capabilities": {NEW: "shadow"}}), encoding="utf-8")
    return tmp_path


def _inbound(text: str | None, **kw: Any) -> Inbound:
    return Inbound(session_id=SID, text=text, now_ms=NOW, message_id="w1", **kw)


def _asked(oracle) -> list[str]:
    return [q for _state, ids in oracle["fake"].calls for q in ids]


def _noted(vault: Path) -> list[dict[str, Any]]:
    return [row for row in read_decisions(vault, SID) if row.get("capability") == NEW]


def _what_the_ingest_uses(readings: Any) -> Any:
    """Las lecturas sin la traza de veredictos (esa lleva el nombre del paquete)."""
    return replace(readings, verdicts=())


# ── El paquete: ventas-8 + esto, y nada más ─────────────────────────────────


def test_ventas_9_is_ventas_8_with_the_objection_note_and_nothing_else() -> None:
    _need_v9()
    files8 = {p.relative_to(V8) for p in V8.rglob("*.yaml")}
    files9 = {p.relative_to(V9) for p in V9.rglob("*.yaml")}

    assert files9 - files8 == {Path(f"capabilities/{NEW}.yaml")}
    assert files8 <= files9
    changed = {p for p in files8 if (V8 / p).read_bytes() != (V9 / p).read_bytes()}
    assert changed == {Path("bundle.yaml")}

    b8, b9 = _load(V8 / "bundle.yaml"), _load(V9 / "bundle.yaml")
    assert (b9["id"], b9["version"]) == ("ventas-9", 9)
    assert {k: v for k, v in b9.items() if k not in ("id", "version", "capabilities")} == {
        k: v for k, v in b8.items() if k not in ("id", "version", "capabilities")
    }
    assert set(b9["capabilities"]) == set(b8["capabilities"]) | {NEW}


def test_the_catalog_asks_for_the_objection_from_version_9_and_ventas_8_runs_without_it(monkeypatch) -> None:
    from src.sdk.decisionkit import check_bundle

    _need_v9()
    catalog = _load(CATALOG_PATH)

    assert check_bundle(V9, CATALOG_PATH) == []
    assert catalog["capabilities_since"][NEW] == 9
    assert registry.capability_if_present(NEW).bundle == "ventas-9@9"
    assert catalog["about"][NEW]["where"] == ["ingest"]
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-8")
    registry.reset()
    assert registry.capability_if_present(NEW) is None


def test_the_objection_has_its_switch_and_is_born_off(tmp_path: Path) -> None:
    from src.plugins.chats.agent.sales.decisions import bots

    assert NEW in bots.CAPABILITIES
    assert bots.bot_for_session(SID, vault_dir=tmp_path).provider(NEW) == "reglas"


def test_the_question_is_a_closed_list_and_no_objection_is_one_of_its_answers() -> None:
    _need_v9()
    cap = _load(V9 / f"capabilities/{NEW}.yaml")

    [question] = cap["questions"]
    assert (question["id"], question["kind"]) == (QUESTION, "choice")
    assert set(question["criteria"]) == {*KINDS, "ninguna"}
    # Lo que se anota: uno de los tipos, o nada ("" = sin objeción).
    assert set(cap["options"]) == {"", *KINDS}


# ── Apagada: nada cambia y Jev no se consulta ───────────────────────────────


async def test_born_off_nothing_is_asked_and_nothing_is_noted(tmp_path: Path, oracle) -> None:
    _need_v9()

    await EngineReadings(tmp_path).read(_inbound(TOO_EXPENSIVE))

    assert oracle["fake"].calls == []
    assert _noted(tmp_path) == []


# ── En sombra: anota y nada más ─────────────────────────────────────────────


async def test_in_shadow_the_objection_is_noted_with_its_message(shadow: Path, oracle) -> None:
    _need_v9()

    await EngineReadings(shadow).read(_inbound(TOO_EXPENSIVE))

    assert _asked(oracle) == [QUESTION]
    [(state, _ids)] = oracle["fake"].calls
    assert TOO_EXPENSIVE in state
    [row] = _noted(shadow)
    assert (row["stage"], row["message_id"], row["provider"], row["bundle"]) == ("ingest", "w1", "sombra", "ventas-9@9")
    assert row["jev"] == "precio"


@pytest.mark.parametrize("kind", KINDS)
async def test_each_kind_of_objection_is_noted_by_its_code(shadow: Path, oracle, kind: str) -> None:
    _need_v9()
    oracle["fake"] = _says(kind)

    await EngineReadings(shadow).read(_inbound(TOO_EXPENSIVE))

    [row] = _noted(shadow)
    assert row["jev"] == kind


async def test_a_message_without_objection_is_noted_as_read_and_without_kind(shadow: Path, oracle) -> None:
    """«Ninguna» se distingue de «Jev no supo»: queda leída (`agree`) y sin tipo."""
    _need_v9()
    oracle["fake"] = _says("ninguna")

    await EngineReadings(shadow).read(_inbound(JUST_ASKING))

    [row] = _noted(shadow)
    assert row.get("agree") is True
    assert not row.get("jev")


async def test_an_unsure_reading_is_noted_without_kind_and_without_agreement(shadow: Path, oracle) -> None:
    _need_v9()
    oracle["fake"] = _says("precio", confidence=0.3)

    await EngineReadings(shadow).read(_inbound(TOO_EXPENSIVE))

    [row] = _noted(shadow)
    assert "jev" not in row and "agree" not in row


async def test_the_note_never_changes_what_the_ingest_reads(shadow: Path, oracle, monkeypatch) -> None:
    """Con la objeción anotada, las lecturas del mensaje son las de ventas-8."""
    _need_v9()

    with_note = await EngineReadings(shadow).read(_inbound(TOO_EXPENSIVE))
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-8")
    registry.reset()
    before = await EngineReadings(shadow).read(_inbound(TOO_EXPENSIVE))

    assert _what_the_ingest_uses(with_note) == _what_the_ingest_uses(before)
    assert [v["capability"] for v in with_note.verdicts] == [v["capability"] for v in before.verdicts]
    assert NEW not in {v["capability"] for v in with_note.verdicts}


async def test_the_new_bot_notes_it_too_and_still_reads_the_same(tmp_path: Path, oracle, monkeypatch) -> None:
    """Con Jev decidiendo (el bot nuevo del laboratorio) también solo anota."""
    _need_v9()
    monkeypatch.setenv("DECISIONS_BOT", "B")

    with_note = await EngineReadings(tmp_path).read(_inbound(TOO_EXPENSIVE))
    [row] = _noted(tmp_path)
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-8")
    registry.reset()
    before = await EngineReadings(tmp_path).read(_inbound(TOO_EXPENSIVE))

    assert row["value"] == "precio"
    assert _what_the_ingest_uses(with_note) == _what_the_ingest_uses(before)


async def test_an_objection_never_goes_to_the_disagreement_queue(shadow: Path, oracle) -> None:
    """No hay regla con la cual discrepar: la cola del operador no se llena."""
    _need_v9()

    await EngineReadings(shadow).read(_inbound(TOO_EXPENSIVE))

    assert _noted(shadow)  # sí se anotó
    queue = shadow / "_decisions" / "disagreements.jsonl"
    assert not queue.exists() or NEW not in queue.read_text(encoding="utf-8")


async def test_a_text_the_customer_did_not_write_is_not_read(shadow: Path, oracle) -> None:
    _need_v9()

    await EngineReadings(shadow).read(_inbound("[el cliente envió una foto: una vela]", synthetic=True))

    assert QUESTION not in _asked(oracle)
    assert _noted(shadow) == []


# ── Si falla o se demora, el mensaje sigue igual ────────────────────────────


async def test_a_broken_objection_never_breaks_the_other_readings(shadow: Path, oracle, monkeypatch) -> None:
    _need_v9()
    real = registry.capability_if_present

    class _Broken:
        name = NEW
        bundle = "ventas-9@9"

        def rule(self, _inp: Any) -> Any:
            raise RuntimeError("paquete dañado")

    monkeypatch.setattr(registry, "capability_if_present", lambda name: _Broken() if name == NEW else real(name))
    broken = await EngineReadings(shadow).read(_inbound(TOO_EXPENSIVE))
    monkeypatch.setattr(registry, "capability_if_present", real)
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-8")
    registry.reset()
    before = await EngineReadings(shadow).read(_inbound(TOO_EXPENSIVE))

    assert _what_the_ingest_uses(broken) == _what_the_ingest_uses(before)


async def test_a_slow_objection_never_holds_the_message(shadow: Path, monkeypatch) -> None:
    """Jev se cuelga solo en esta pregunta: el ingest no la espera más que su
    tope (`OBSERVATION_WAIT_S`) y el mensaje sigue sin la nota."""
    from src.plugins.chats.agent.sales.decisions import readings
    from src.sdk import connectorkit

    _need_v9()

    class _Hangs(FakePerceptionAdapter):
        async def ask(self, state, questions, *, timeout_s, redact=()):
            if any(q.id == QUESTION for q in questions):
                await asyncio.sleep(30)
            return await super().ask(state, questions, timeout_s=timeout_s, redact=redact)

    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: _Hangs({}))
    monkeypatch.setattr(readings, "OBSERVATION_WAIT_S", 0.05)

    started = time.monotonic()
    result = await EngineReadings(shadow).read(_inbound(TOO_EXPENSIVE))

    assert time.monotonic() - started < 5
    assert result.purchase is not None
    assert _noted(shadow) == []
