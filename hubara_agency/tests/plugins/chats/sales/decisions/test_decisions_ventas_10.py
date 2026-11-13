"""`ventas-10`: es `ventas-9@9` con una decisión nueva que solo anota,
`datos_por_mayor`, y nada más.

Tarjeta 34 del ciclo (informe de estrategia del 2026-11-10): cuando un cliente
pide al por mayor, un pedido grande o un evento de empresa, Colossus pasa la
conversación al equipo apenas lo detecta y el equipo llega sin saber qué
producto, cuántas unidades, para cuándo, a qué ciudad ni si es para empresa.

En sombra, Colossus solo anota: en el momento del relevo (BULK_ORDER,
WHOLESALE_B2B o CORPORATE_EVENT), Jev lee lo último de la charla y dice, dato
por dato, si el cliente ya lo dio, y si pidió hablar con una persona. Queda en
las decisiones de la conversación (`evals/decisions.jsonl`) para el informe de
estrategia. Valor: los datos que faltan y `persona` si la pidió.

El relevo sale igual que hoy, en el mismo turno: la nota se toma después de
relevar, y si falla, se demora o el paquete no la trae, el resultado de la
herramienta es idéntico.

Textos sintéticos (nunca el de un cliente). En un clon de forge `ventas-10` no
viaja (experimento de esta tienda): se salta.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from exoclaw.agent.tools import ToolContext

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.platform.tools.escalation import EscalateToHumanTool
from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.decisions.decision_log import read_decisions
from src.plugins.chats.agent.sales.tools.escalation import guarded_escalation_tool
from src.plugins.chats.shared.store_pack import BUNDLES_DIR, CATALOG_PATH
from src.sdk.connectorkit import TypedAnswer

V9 = BUNDLES_DIR / "ventas-9"
V10 = BUNDLES_DIR / "ventas-10"
IN_FORGE_CLONE = not (Path(__file__).resolve().parents[6] / "forge").is_dir()
SID = "wa_573002222222"
NEW = "datos_por_mayor"
DATA = ("producto", "cantidad", "fecha", "ciudad", "empresa")
PERSON = "persona"
QUESTIONS = tuple(f"{NEW}.{key}" for key in (*DATA, PERSON))

#: Con la forma de lo que escribe un cliente (sintéticos).
EVERYTHING = (
    "Hola, tengo una empresa de eventos y necesito 120 velas de lavanda en caja para el 15 de diciembre, "
    "son para entregar en Medellín, a nombre de la empresa"
)
ONLY_WHOLESALE = "Buenas, ustedes venden al por mayor?"
WANTS_A_PERSON = "Vendo al por mayor, me comunicas con alguien del equipo por favor?"
NOTE_FOR_THE_TEAM = "Cliente pregunta por compra al por mayor"


def _need_v10() -> None:
    if IN_FORGE_CLONE:
        pytest.skip("clon de forge: los paquetes de prueba de esta tienda no viajan")
    assert (V10 / "bundle.yaml").is_file(), "falta el paquete ventas-10"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _ventas_10(monkeypatch):
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-10")
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    registry.reset()
    yield
    registry.reset()


def _says(given: dict[str, float]) -> FakePerceptionAdapter:
    """Jev contesta cada pregunta con su p (1 = el cliente ya lo dijo / pidió una persona)."""
    return FakePerceptionAdapter({
        f"{NEW}.{key}": TypedAnswer(id=f"{NEW}.{key}", kind="noul", p=p) for key, p in given.items()
    })


ALL_GIVEN = {**dict.fromkeys(DATA, 0.95), PERSON: 0.03}
NOTHING_GIVEN = {**dict.fromkeys(DATA, 0.04), PERSON: 0.05}


@pytest.fixture
def oracle(monkeypatch):
    """Ninguna prueba sale a la red: Jev es el falso."""
    from src.sdk import connectorkit

    holder: dict[str, Any] = {"fake": _says(NOTHING_GIVEN)}

    def _get(_oracle: str):
        return holder["fake"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return holder


def _vault(tmp_path: Path, mode: str | None) -> Path:
    vault = tmp_path / "vault"
    (vault / SID).mkdir(parents=True)
    (vault / SID / "metadata.json").write_text(
        json.dumps({"last_inbound_message_id": "wamid.A", "episodes": []}), encoding="utf-8"
    )
    if mode is not None:
        control = vault / "_rollout" / "decisions.json"
        control.parent.mkdir(parents=True)
        control.write_text(json.dumps({"capabilities": {NEW: mode}}), encoding="utf-8")
    return vault


@pytest.fixture
def shadow(tmp_path: Path, monkeypatch) -> Path:
    """`datos_por_mayor` en sombra, como la deja el operador por comando."""
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "shadow")
    return _vault(tmp_path, "shadow")


def _tool(vault: Path, said: str):
    tool_cls = guarded_escalation_tool(EscalateToHumanTool)
    return tool_cls(
        workspace=str(vault.parent), vault_dir=vault,
        history_reader=lambda _key: [
            {"role": "assistant", "content": "¡Hola! Bienvenida a la tienda, ¿en qué te ayudo?"},
            {"role": "user", "content": said},
        ],
    )


async def _escalate(tool, reason: str = "WHOLESALE_B2B") -> str:
    ctx = ToolContext(session_key=SID, channel="whatsapp", chat_id="c")
    return await tool.execute_with_context(
        ctx, reason_category=reason, summary=NOTE_FOR_THE_TEAM,
        customer_message="Para esa compra te coordino con un colega del equipo 🤍",
    )


def _route(vault: Path) -> str | None:
    return json.loads((vault / SID / "metadata.json").read_text(encoding="utf-8")).get("active_route")


def _noted(vault: Path) -> list[dict[str, Any]]:
    return [row for row in read_decisions(vault, SID) if row.get("capability") == NEW]


def _asked(oracle) -> list[str]:
    return [q for _state, ids in oracle["fake"].calls for q in ids]


def _same_result(a: str, b: str) -> bool:
    """El resultado de la herramienta sin el id de sesión (es el mismo)."""
    return json.loads(a) == json.loads(b)


# ── El paquete: ventas-9 + esto, y nada más ─────────────────────────────────


def test_ventas_10_is_ventas_9_with_the_bulk_handoff_note_and_nothing_else() -> None:
    _need_v10()
    files9 = {p.relative_to(V9) for p in V9.rglob("*.yaml")}
    files10 = {p.relative_to(V10) for p in V10.rglob("*.yaml")}

    assert files10 - files9 == {Path(f"capabilities/{NEW}.yaml")}
    assert files9 <= files10
    changed = {p for p in files9 if (V9 / p).read_bytes() != (V10 / p).read_bytes()}
    assert changed == {Path("bundle.yaml")}

    b9, b10 = _load(V9 / "bundle.yaml"), _load(V10 / "bundle.yaml")
    assert (b10["id"], b10["version"]) == ("ventas-10", 10)
    assert {k: v for k, v in b10.items() if k not in ("id", "version", "capabilities")} == {
        k: v for k, v in b9.items() if k not in ("id", "version", "capabilities")
    }
    assert set(b10["capabilities"]) == set(b9["capabilities"]) | {NEW}


def test_the_catalog_asks_for_it_from_version_10_and_ventas_9_runs_without_it(monkeypatch) -> None:
    from src.sdk.decisionkit import check_bundle

    _need_v10()
    catalog = _load(CATALOG_PATH)

    assert check_bundle(V10, CATALOG_PATH) == []
    assert catalog["capabilities_since"][NEW] == 10
    assert registry.capability_if_present(NEW).bundle == "ventas-10@10"
    assert catalog["about"][NEW]["where"] == ["herramientas"]
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-9")
    registry.reset()
    assert registry.capability_if_present(NEW) is None


def test_it_has_its_switch_and_is_born_off(tmp_path: Path) -> None:
    from src.plugins.chats.agent.sales.decisions import bots

    assert NEW in bots.CAPABILITIES
    assert bots.bot_for_session(SID, vault_dir=tmp_path).provider(NEW) == "reglas"


def test_one_question_per_datum_and_one_for_asking_for_a_person() -> None:
    _need_v10()
    cap = _load(V10 / f"capabilities/{NEW}.yaml")

    assert tuple(q["id"] for q in cap["questions"]) == QUESTIONS
    assert {q["kind"] for q in cap["questions"]} == {"noul"}


# ── Apagada: el relevo es el de hoy y Jev no se consulta ────────────────────


async def test_born_off_nothing_is_asked_nothing_is_noted_and_the_handoff_goes_out(tmp_path: Path, oracle) -> None:
    _need_v10()
    vault = _vault(tmp_path, None)

    result = json.loads(await _escalate(_tool(vault, ONLY_WHOLESALE)))

    assert "escalation_decision" in result and _route(vault) == "humano"
    assert oracle["fake"].calls == []
    assert _noted(vault) == []


# ── En sombra: anota qué faltaba y nada más ─────────────────────────────────


async def test_a_wholesaler_who_gave_everything_is_noted_with_nothing_missing(shadow: Path, oracle) -> None:
    _need_v10()
    oracle["fake"] = _says(ALL_GIVEN)

    result = json.loads(await _escalate(_tool(shadow, EVERYTHING)))

    assert "escalation_decision" in result and _route(shadow) == "humano"
    [row] = _noted(shadow)
    assert (row["stage"], row["provider"], row["bundle"]) == ("turno", "sombra", "ventas-10@10")
    assert row.get("agree") is True and not row.get("jev")


async def test_a_wholesaler_who_only_said_wholesale_misses_the_five(shadow: Path, oracle) -> None:
    _need_v10()

    await _escalate(_tool(shadow, ONLY_WHOLESALE))

    [row] = _noted(shadow)
    assert sorted(row["jev"]) == sorted(DATA)
    [(state, _ids)] = oracle["fake"].calls
    assert ONLY_WHOLESALE in state and NOTE_FOR_THE_TEAM in state


async def test_a_customer_who_asks_for_a_person_is_noted_as_such_even_with_data_missing(shadow: Path, oracle) -> None:
    """La meta: nunca habría retenido a quien pidió una persona."""
    _need_v10()
    oracle["fake"] = _says({**dict.fromkeys(DATA, 0.04), PERSON: 0.96})

    result = json.loads(await _escalate(_tool(shadow, WANTS_A_PERSON)))

    assert "escalation_decision" in result
    [row] = _noted(shadow)
    assert PERSON in row["jev"]


@pytest.mark.parametrize("reason", ["BULK_ORDER", "WHOLESALE_B2B", "CORPORATE_EVENT"])
async def test_each_bulk_reason_is_noted(shadow: Path, oracle, reason: str) -> None:
    _need_v10()

    await _escalate(_tool(shadow, "Necesito 300 velas para la feria de la empresa"), reason=reason)

    assert len(_noted(shadow)) == 1


async def test_another_reason_is_not_noted(shadow: Path, oracle) -> None:
    _need_v10()

    await _escalate(_tool(shadow, "Me llegó rota la vela"), reason="POST_SALE_ISSUE")

    assert _asked(oracle) == []
    assert _noted(shadow) == []


async def test_a_handoff_that_did_not_happen_is_not_noted(shadow: Path, oracle) -> None:
    """«36 velitas» que eran cajas: `por_mayor` lo devuelve al modelo (sin Jev,
    «duda»); no hubo relevo, no hay nota."""
    _need_v10()

    result = json.loads(await _escalate(_tool(shadow, "Hola! me vendes 36 velitas?"), reason="BULK_ORDER"))

    assert result.get("escalated") is False
    assert _noted(shadow) == []


async def test_an_unsure_reading_is_noted_without_value_and_without_agreement(shadow: Path, oracle) -> None:
    _need_v10()
    oracle["fake"] = _says({**ALL_GIVEN, "fecha": 0.5})

    await _escalate(_tool(shadow, EVERYTHING))

    [row] = _noted(shadow)
    assert "jev" not in row and "agree" not in row


# ── El relevo sale igual que hoy ────────────────────────────────────────────


async def test_the_note_never_changes_the_handoff(tmp_path: Path, oracle, monkeypatch) -> None:
    _need_v10()
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "shadow")
    with_note = await _escalate(_tool(_vault(tmp_path / "a", "shadow"), ONLY_WHOLESALE))
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-9")
    registry.reset()
    before = await _escalate(_tool(_vault(tmp_path / "b", "shadow"), ONLY_WHOLESALE))

    assert _same_result(with_note, before)


async def test_the_new_bot_notes_it_too_and_still_hands_off(tmp_path: Path, oracle, monkeypatch) -> None:
    """Con Jev decidiendo (el bot nuevo del laboratorio) también solo anota."""
    _need_v10()
    monkeypatch.setenv("DECISIONS_BOT", "B")
    vault = _vault(tmp_path, None)

    result = json.loads(await _escalate(_tool(vault, ONLY_WHOLESALE)))

    assert "escalation_decision" in result and _route(vault) == "humano"
    [row] = _noted(vault)
    assert sorted(row["value"]) == sorted(DATA)


async def test_a_broken_note_never_breaks_the_handoff(shadow: Path, oracle, monkeypatch) -> None:
    _need_v10()
    real = registry.capability_if_present

    class _Broken:
        name = NEW
        bundle = "ventas-10@10"

        def rule(self, _inp: Any) -> Any:
            raise RuntimeError("paquete dañado")

    monkeypatch.setattr(registry, "capability_if_present", lambda name: _Broken() if name == NEW else real(name))

    result = json.loads(await _escalate(_tool(shadow, ONLY_WHOLESALE)))

    assert "escalation_decision" in result and _route(shadow) == "humano"


async def test_a_slow_note_never_holds_the_handoff(shadow: Path, monkeypatch) -> None:
    """Jev se cuelga: la herramienta no espera más que el tope de una nota y el
    relevo sale sin ella."""
    from src.plugins.chats.agent.sales.tools import escalation
    from src.sdk import connectorkit

    _need_v10()

    class _Hangs(FakePerceptionAdapter):
        async def ask(self, state, questions, *, timeout_s, redact=()):
            if any(q.id.startswith(f"{NEW}.") for q in questions):
                await asyncio.sleep(30)
            return await super().ask(state, questions, timeout_s=timeout_s, redact=redact)

    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: _Hangs({}))
    monkeypatch.setattr(escalation, "NOTE_WAIT_S", 0.05)

    started = time.monotonic()
    result = json.loads(await _escalate(_tool(shadow, ONLY_WHOLESALE)))

    assert time.monotonic() - started < 5
    assert "escalation_decision" in result and _route(shadow) == "humano"
    assert _noted(shadow) == []
