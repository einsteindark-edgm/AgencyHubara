"""Tests de `ensure_payment_pending_closure_activity` — red de seguridad orden↔tag.

La activity garantiza, de forma IDEMPOTENTE, que tras un `register_order`
exitoso el episodio quede cerrado con `CONFIRMADO_PAGO_PENDIENTE` + la sesión
escalada a humano (`PAYMENT_VERIFICATION_PENDING`), AUNQUE el LLM no haya
emitido `manage_conversation_tag` / `escalate_to_human`.

Casos cubiertos:
  1. Episodio ACTIVO (el LLM no cerró) → la red cierra + escala.
  2. Idempotente: el LLM ya cerró Y escaló → no toca nada.
  3. El LLM cerró el episodio pero NO escaló → la red solo escala.
  4. Doble ejecución (retry de Temporal) → la 2ª corrida es no-op.
  5. metadata ausente → no-op seguro.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales.activities.episode_closure import (
    ensure_closing_escalation_activity,
    ensure_payment_pending_closure_activity,
)

_FIXED_DT = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)
_FIXED_MS = int(_FIXED_DT.timestamp() * 1000)
_SESSION = "wa_573001112233"
_ORDER_ID = "draft_abc_001"
_MOTIVO = "Cliente confirmó pedido draft_abc_001 por $22000 COP, método transfer."


def _write_metadata(vault: Path, data: dict) -> Path:
    session_dir = vault / _SESSION
    session_dir.mkdir(parents=True, exist_ok=True)
    md = session_dir / "metadata.json"
    md.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return md


def _active_episode_metadata() -> dict:
    """Estado tras `register_order`: episodio ACTIVO con order_id anotado
    (attach_order_to_active_episode) pero SIN cerrar — el LLM no emitió el tag.
    """
    return {
        "active_route": "ventas",
        "tag": "NO_ETIQUETADO",
        "registered_order": {"order_id": _ORDER_ID, "success": True},
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": _FIXED_MS - 60_000,
                "started_inbound_message_id": "wamid.1",
                "closed_at_ms": None,
                "closing_tag": None,
                "closing_motivo": None,
                "order_id": _ORDER_ID,
                "referral_snapshot": None,
                "msgs_count_at_start": 0,
                "msgs_count_at_close": None,
            }
        ],
    }


async def _run(session_id: str = _SESSION, order_id: str = _ORDER_ID, motivo: str = _MOTIVO):
    """Invoca la activity con un scheduled_time fijo (idempotencia entre retries)."""
    env = ActivityEnvironment()
    env.info = ActivityEnvironment.default_info().__class__(
        **{
            **env.info.__dict__,
            "scheduled_time": _FIXED_DT,
            "current_attempt_scheduled_time": _FIXED_DT,
        }
    )
    return await env.run(
        ensure_payment_pending_closure_activity, session_id, order_id, motivo
    )


async def test_safety_net_closes_and_escalates_when_llm_didnt(_isolate_vault_dir: Path):
    """El LLM registró la orden pero NO cerró el episodio ni escaló — la red
    de seguridad debe cerrar con CONFIRMADO_PAGO_PENDIENTE + escalar."""
    md = _write_metadata(_isolate_vault_dir, _active_episode_metadata())

    result = await _run()

    assert result.acted is True
    assert result.escalated is True
    assert result.closed_episode_id == "ep_001"
    assert result.closing_tag == "CONFIRMADO_PAGO_PENDIENTE"

    data = json.loads(md.read_text(encoding="utf-8"))
    ep = data["episodes"][-1]
    # Episodio cerrado con el tag de pago pendiente + order_id preservado.
    assert ep["closed_at_ms"] == _FIXED_MS
    assert ep["closing_tag"] == "CONFIRMADO_PAGO_PENDIENTE"
    assert ep["order_id"] == _ORDER_ID
    # Escalación a humano (espejo de escalate_to_human): el tag visible pasa a
    # HUMANO, pero el closing_tag del episodio preserva el estado real.
    assert data["active_route"] == "humano"
    assert data["tag"] == "HUMANO"
    assert data["escalation_reason"] == "PAYMENT_VERIFICATION_PENDING"


async def test_idempotent_when_llm_already_closed_and_escalated(_isolate_vault_dir: Path):
    """El LLM ya cerró el episodio Y escaló — la red NO debe tocar nada."""
    data = _active_episode_metadata()
    # Simular cierre del LLM + escalación previa.
    data["episodes"][-1]["closed_at_ms"] = _FIXED_MS - 5_000
    data["episodes"][-1]["closing_tag"] = "CONFIRMADO_PAGO_PENDIENTE"
    data["active_route"] = "humano"
    data["tag"] = "HUMANO"
    data["escalation_reason"] = "PAYMENT_VERIFICATION_PENDING"
    md = _write_metadata(_isolate_vault_dir, data)
    before = md.read_text(encoding="utf-8")

    result = await _run()

    assert result.acted is False
    assert result.escalated is False
    # metadata intacto (no re-escribe cuando no hay nada que hacer).
    assert md.read_text(encoding="utf-8") == before


async def test_escalates_when_llm_closed_but_didnt_escalate(_isolate_vault_dir: Path):
    """El LLM cerró el episodio con el tag pero NO escaló — la red solo escala
    (no re-cierra un episodio ya cerrado)."""
    data = _active_episode_metadata()
    data["episodes"][-1]["closed_at_ms"] = _FIXED_MS - 5_000
    data["episodes"][-1]["closing_tag"] = "CONFIRMADO_PAGO_PENDIENTE"
    data["tag"] = "CONFIRMADO_PAGO_PENDIENTE"
    data["active_route"] = "ventas"  # NO escalado
    md = _write_metadata(_isolate_vault_dir, data)

    result = await _run()

    assert result.acted is False  # no re-cerró
    assert result.escalated is True  # sí escaló
    data2 = json.loads(md.read_text(encoding="utf-8"))
    assert data2["active_route"] == "humano"
    assert data2["escalation_reason"] == "PAYMENT_VERIFICATION_PENDING"


async def test_double_run_is_idempotent(_isolate_vault_dir: Path):
    """Retry de Temporal: correr la activity 2 veces NO debe duplicar el cierre
    ni acumular status_history infinito."""
    md = _write_metadata(_isolate_vault_dir, _active_episode_metadata())

    first = await _run()
    assert first.acted is True and first.escalated is True
    data_after_first = json.loads(md.read_text(encoding="utf-8"))
    history_len_first = len(data_after_first.get("status_history", []))
    closed_at_first = data_after_first["episodes"][-1]["closed_at_ms"]

    second = await _run()
    assert second.acted is False
    assert second.escalated is False
    data_after_second = json.loads(md.read_text(encoding="utf-8"))
    # El cierre NO se movió y no se agregaron entries de status_history.
    assert data_after_second["episodes"][-1]["closed_at_ms"] == closed_at_first
    assert len(data_after_second.get("status_history", [])) == history_len_first
    # Sigue habiendo UN solo episodio (no se creó otro).
    assert len(data_after_second["episodes"]) == 1


async def test_missing_metadata_is_noop(_isolate_vault_dir: Path):
    """Sin metadata.json (no debería pasar) la activity es no-op segura."""
    result = await _run(session_id="wa_doesnotexist")
    assert result.acted is False
    assert result.escalated is False


# ----------------------------------------------------------------------
# ensure_closing_escalation (patrón A — CONFIRMADO_SIN_DATOS)
# ----------------------------------------------------------------------


async def _run_escalation(reason: str = "ORDER_PENDING_SHIPPING_DETAILS", motivo: str = "faltan datos"):
    env = ActivityEnvironment()
    env.info = ActivityEnvironment.default_info().__class__(
        **{
            **env.info.__dict__,
            "scheduled_time": _FIXED_DT,
            "current_attempt_scheduled_time": _FIXED_DT,
        }
    )
    return await env.run(
        ensure_closing_escalation_activity, _SESSION, reason, motivo
    )


async def test_closing_escalation_escalates_when_llm_didnt(_isolate_vault_dir: Path):
    """El LLM marcó CONFIRMADO_SIN_DATOS (episodio ya cerrado) pero NO escaló —
    la red garantiza la escalación ORDER_PENDING_SHIPPING_DETAILS."""
    data = _active_episode_metadata()
    data["episodes"][-1]["closed_at_ms"] = _FIXED_MS - 5_000
    data["episodes"][-1]["closing_tag"] = "CONFIRMADO_SIN_DATOS"
    data["tag"] = "CONFIRMADO_SIN_DATOS"
    data["active_route"] = "ventas"  # NO escalado
    md = _write_metadata(_isolate_vault_dir, data)

    escalated = await _run_escalation()

    assert escalated is True
    data2 = json.loads(md.read_text(encoding="utf-8"))
    assert data2["active_route"] == "humano"
    assert data2["tag"] == "HUMANO"
    assert data2["escalation_reason"] == "ORDER_PENDING_SHIPPING_DETAILS"
    # El closing_tag del episodio se preserva (no lo pisa la escalación).
    assert data2["episodes"][-1]["closing_tag"] == "CONFIRMADO_SIN_DATOS"


async def test_closing_escalation_idempotent_when_already_human(_isolate_vault_dir: Path):
    """Si el LLM ya escaló (active_route=humano), la red NO pisa nada."""
    data = _active_episode_metadata()
    data["episodes"][-1]["closed_at_ms"] = _FIXED_MS - 5_000
    data["episodes"][-1]["closing_tag"] = "CONFIRMADO_SIN_DATOS"
    data["active_route"] = "humano"
    data["tag"] = "HUMANO"
    data["escalation_reason"] = "ORDER_PENDING_SHIPPING_DETAILS"
    md = _write_metadata(_isolate_vault_dir, data)
    before = md.read_text(encoding="utf-8")

    escalated = await _run_escalation()

    assert escalated is False
    assert md.read_text(encoding="utf-8") == before


async def test_closing_escalation_missing_metadata_is_noop(_isolate_vault_dir: Path):
    env = ActivityEnvironment()
    env.info = ActivityEnvironment.default_info().__class__(
        **{**env.info.__dict__, "scheduled_time": _FIXED_DT, "current_attempt_scheduled_time": _FIXED_DT}
    )
    escalated = await env.run(
        ensure_closing_escalation_activity, "wa_nope", "ORDER_PENDING_SHIPPING_DETAILS", "x"
    )
    assert escalated is False
