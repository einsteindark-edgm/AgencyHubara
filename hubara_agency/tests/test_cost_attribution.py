"""Tests de la atribución de costos del LLM (HU-003 A7).

Cubre las dos piezas que etiquetan el span gen_ai (de OpenLIT) con
`session.id` + `episode.id` para `GROUP BY` de costo en SigNoz:

  * `BaggageSpanProcessor` — copia el baggage del contexto a atributos del span
    en `on_start` (replaza opentelemetry-processor-baggage, que no está instalado).
  * `get_active_episode_id_activity` (+ `_active_episode_id`) — resuelve el
    `episode_id` activo desde metadata.json para que `run_agent_turn` lo setee
    como baggage.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from opentelemetry.baggage import set_baggage
from opentelemetry.context import Context

from src.platform.observability.baggage import BaggageSpanProcessor
from src.platform.observability.cost_attribution import (
    _active_episode_id,
    get_active_episode_id_activity,
)


# ---------------------------------------------------------------------------
# BaggageSpanProcessor
# ---------------------------------------------------------------------------


def _captured_attrs(span: MagicMock) -> dict[str, object]:
    return {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}


def test_baggage_processor_copies_all_baggage_to_attributes() -> None:
    ctx = set_baggage("session.id", "wa_573001234567")
    ctx = set_baggage("episode.id", "ep_002", context=ctx)
    ctx = set_baggage("whatsapp.number", "573001234567", context=ctx)

    span = MagicMock()
    BaggageSpanProcessor().on_start(span, ctx)

    assert _captured_attrs(span) == {
        "session.id": "wa_573001234567",
        "episode.id": "ep_002",
        "whatsapp.number": "573001234567",
    }


def test_baggage_processor_allowed_keys_filters() -> None:
    ctx = set_baggage("session.id", "wa_1")
    ctx = set_baggage("unrelated", "leak", context=ctx)

    span = MagicMock()
    BaggageSpanProcessor(allowed_keys=frozenset({"session.id"})).on_start(span, ctx)

    assert _captured_attrs(span) == {"session.id": "wa_1"}


def test_baggage_processor_empty_context_is_noop() -> None:
    span = MagicMock()
    BaggageSpanProcessor().on_start(span, Context())
    span.set_attribute.assert_not_called()


def test_baggage_processor_lifecycle_methods_are_safe() -> None:
    # on_end/shutdown no-op; force_flush True. No deben romper el pipeline.
    proc = BaggageSpanProcessor()
    proc.on_end(MagicMock())
    proc.shutdown()
    assert proc.force_flush() is True


# ---------------------------------------------------------------------------
# _active_episode_id (predicado puro — espejo de chats.get_active_episode)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "metadata, expected",
    [
        ({}, ""),  # sesión nueva, sin metadata
        ({"episodes": []}, ""),  # lista vacía
        ({"episodes": [{"episode_id": "ep_001", "closed_at_ms": None}]}, "ep_001"),
        ({"episodes": [{"episode_id": "ep_001", "closed_at_ms": 123}]}, ""),  # cerrado
        (
            {
                "episodes": [
                    {"episode_id": "ep_001", "closed_at_ms": 100},
                    {"episode_id": "ep_002", "closed_at_ms": None},  # activo (último)
                ]
            },
            "ep_002",
        ),
        ({"episodes": [{"closed_at_ms": None}]}, ""),  # sin episode_id
        ({"episodes": "garbage"}, ""),  # tipo inesperado → "" (defensivo)
    ],
)
def test_active_episode_id_predicate(metadata: dict, expected: str) -> None:
    assert _active_episode_id(metadata) == expected


# ---------------------------------------------------------------------------
# get_active_episode_id_activity (integración con el vault aislado)
# ---------------------------------------------------------------------------


def _write_metadata(vault: Path, session_id: str, data: dict) -> None:
    sess = vault / session_id
    sess.mkdir(parents=True, exist_ok=True)
    (sess / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


async def test_activity_returns_active_episode(_isolate_vault_dir: Path) -> None:
    session_id = "wa_573001234567"
    _write_metadata(
        _isolate_vault_dir,
        session_id,
        {"episodes": [{"episode_id": "ep_003", "closed_at_ms": None}]},
    )
    assert await get_active_episode_id_activity(session_id) == "ep_003"


async def test_activity_returns_empty_when_closed(_isolate_vault_dir: Path) -> None:
    session_id = "wa_111"
    _write_metadata(
        _isolate_vault_dir,
        session_id,
        {"episodes": [{"episode_id": "ep_001", "closed_at_ms": 999}]},
    )
    assert await get_active_episode_id_activity(session_id) == ""


async def test_activity_returns_empty_without_metadata(_isolate_vault_dir: Path) -> None:
    # Sesión sin metadata.json → "" (el workflow atribuye sólo por número).
    assert await get_active_episode_id_activity("wa_does_not_exist") == ""
