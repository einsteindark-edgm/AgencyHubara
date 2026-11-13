"""Relevos al por mayor: cobertura de la nota y datos personales (revisión de la tarjeta 34).

- Un relevo al por mayor cuya nota no quedó (Jev tardó más que el tope de la
  herramienta, o falló) no desaparece: se cuenta como «sin nota».
- Lo que escribió el equipo sale hacia Jev con el nombre del cliente tapado
  (el del borrador del pedido), no solo teléfonos y correos.
Datos sintéticos.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.plugins.chats.agent.sales.tools.escalation import BULK_HANDOFF_REASONS
from src.plugins.chats.agent.sales_eval import bulk_handoffs, strategy
from src.plugins.chats.shared import turn_traces
from src.sdk.connectorkit import InMemoryOrderFacts

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
RECENT_MS = int(datetime(2026, 10, 8, 14, 0, tzinfo=UTC).timestamp() * 1000)
MINUTE_MS = 60_000


def _conversation(vault: Path, sid: str, *, reasons: tuple[str, ...], draft: dict | None = None) -> int:
    turn_traces.append_trace(vault, sid, {
        "episode_id": "ep_001", "turn": 1, "workflow": "v2", "trigger": "customer",
        "turn_started_ms": RECENT_MS + MINUTE_MS, "recorded_at_ms": RECENT_MS + MINUTE_MS + 5_000,
        "stage_out": "descubrimiento",
    })
    handoff_ms = RECENT_MS + 2 * MINUTE_MS
    history = []
    for n, reason in enumerate(reasons):
        history += [{"tag": "HUMANO", "active_route": "humano", "reason_category": reason,
                     "timestamp": (handoff_ms + n * 10 * MINUTE_MS) / 1000},
                    {"tag": "INTERESADO", "active_route": "ventas",
                     "timestamp": (handoff_ms + n * 10 * MINUTE_MS + MINUTE_MS) / 1000}]
    episode = {"episode_id": "ep_001", "started_at_ms": RECENT_MS, "closed_at_ms": None, "order_id": None}
    if draft:
        episode["order_draft"] = draft
    path = vault / sid / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"episodes": [episode], "status_history": history}), encoding="utf-8")
    return handoff_ms


def _noted(vault: Path, sid: str, at_ms: int, missing: list[str]) -> None:
    path = vault / sid / "evals" / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"stage": "turno", "capability": "datos_por_mayor", "at_ms": at_ms, "by": "reglas",
                             "provider": "sombra", "value": [], "jev": missing}) + "\n")


def _team_wrote(vault: Path, sid: str, at_ms: int, text: str) -> None:
    path = vault / sid / "sessions" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"role": "assistant", "sender": "human", "content": text,
                             "timestamp": datetime.fromtimestamp(at_ms / 1000, UTC).isoformat()}) + "\n")


async def _report(vault: Path, reader) -> dict:
    return await strategy.build_report(vault, days=7, base_days=28, now=NOW, order_facts=InMemoryOrderFacts(),
                                       team_reader=reader)


async def test_a_bulk_handoff_without_its_note_is_counted_as_such(tmp_path: Path) -> None:
    # Dos relevos al por mayor (uno sin nota) y uno por otro motivo.
    at = _conversation(tmp_path, "wa_100000000001", reasons=("WHOLESALE_B2B", "BULK_ORDER", "POST_SALE_ISSUE"))
    _noted(tmp_path, "wa_100000000001", at + 1_000, ["fecha"])

    async def reader(_text: str) -> frozenset[str]:
        return frozenset()

    now = (await _report(tmp_path, reader))["relevos_por_mayor"]["actual"]["colossus"]

    assert now["relevos"] == 1
    assert now["relevos_sin_nota"] == 1


async def test_the_customer_name_is_covered_before_the_team_text_goes_to_jev(tmp_path: Path) -> None:
    at = _conversation(tmp_path, "wa_100000000001", reasons=("WHOLESALE_B2B",),
                       draft={"slots": {"nombre_recibe": "Marisol Quintero"}})
    _noted(tmp_path, "wa_100000000001", at + 1_000, ["fecha"])
    _team_wrote(tmp_path, "wa_100000000001", at + 3 * MINUTE_MS, "Hola Marisol, ¿para qué fecha las necesitas?")
    seen: list[str] = []

    async def reader(text: str) -> frozenset[str]:
        seen.append(text)
        return frozenset({"fecha"})

    await _report(tmp_path, reader)

    [text] = seen
    assert "Marisol" not in text and "fecha las necesitas" in text


def test_the_report_and_the_tool_agree_on_which_handoffs_are_bulk() -> None:
    assert set(bulk_handoffs.BULK_HANDOFF_REASONS) == set(BULK_HANDOFF_REASONS)
