"""Red de seguridad: el bot prometió que un colega lo atiende y nadie escaló.

Laboratorio caso-cortesia-1001 (r1 y r2, 2026-09-30), caso de control: el
cliente pidió que le llevaran el pedido hoy a la portería y los dos bots
contestaron «…un colega del equipo coordina contigo la entrega…» sin llamar
`escalate_to_human`. La conversación seguía en la ruta del bot: ningún humano
la veía en su bandeja y el cliente esperaba a una persona.

`ensure_promised_handoff_activity` corre antes de enviar el texto final del
turno: si el texto promete el relevo (capacidad `relevo`: la regla es piso,
Jev suma paráfrasis) y la conversación no está escalada, la escala como
`escalate_to_human`, con la promesa en el motivo. Idempotente.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.activities.episode_closure import ensure_promised_handoff_activity
from src.sdk.connectorkit import TypedAnswer

_FIXED_DT = datetime(2026, 9, 30, 21, 37, 0, tzinfo=timezone.utc)
SID = "wa_573001234567"
PROMISE = "Buenas noches 🤍 Claro que sí, un colega del equipo coordina contigo la entrega de hoy después de las 5 en la portería."
PARAPHRASE = "Listo, lo dejo anotado y la persona de despachos te contacta para cuadrar la hora."


def _seed(vault: Path, **metadata) -> Path:
    md = vault / SID / "metadata.json"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(json.dumps({"active_route": "ventas", "tag": "NO_ETIQUETADO", **metadata}), encoding="utf-8")
    return md


async def _run(text: str) -> bool:
    env = ActivityEnvironment()
    env.info = ActivityEnvironment.default_info().__class__(
        **{**env.info.__dict__, "scheduled_time": _FIXED_DT, "current_attempt_scheduled_time": _FIXED_DT}
    )
    return await env.run(ensure_promised_handoff_activity, SID, text)


async def test_a_promise_without_escalation_escalates(_isolate_vault_dir: Path) -> None:
    md = _seed(_isolate_vault_dir)

    assert await _run(PROMISE) is True

    data = json.loads(md.read_text(encoding="utf-8"))
    assert (data["active_route"], data["tag"], data["escalation_reason"]) == ("humano", "HUMANO", "OTHER")
    assert "un colega del equipo coordina contigo la entrega" in data["motivo"]
    assert data["status_history"][-1]["source"] == "safety_net"


async def test_a_conversation_already_escalated_is_left_alone(_isolate_vault_dir: Path) -> None:
    md = _seed(_isolate_vault_dir, active_route="humano", tag="HUMANO", escalation_reason="SHIPPING_ISSUE")
    before = md.read_text(encoding="utf-8")

    assert await _run(PROMISE) is False
    assert md.read_text(encoding="utf-8") == before


async def test_a_text_without_a_promise_changes_nothing(_isolate_vault_dir: Path) -> None:
    md = _seed(_isolate_vault_dir)
    before = md.read_text(encoding="utf-8")

    assert await _run("Qué alegría que ya lo tengas contigo 🤍 Cualquier cosa que necesites, aquí estamos.") is False
    assert md.read_text(encoding="utf-8") == before


async def test_todays_bot_does_not_know_a_paraphrase(_isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir)

    assert await _run(PARAPHRASE) is False


async def test_with_jev_a_paraphrase_escalates_too(_isolate_vault_dir: Path, monkeypatch) -> None:
    from src.sdk import connectorkit

    monkeypatch.setenv("DECISIONS_BOT", "B")
    fake = FakePerceptionAdapter({"relevo.promete": TypedAnswer(id="relevo.promete", kind="noul", p=0.93)})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    _seed(_isolate_vault_dir)

    assert await _run(PARAPHRASE) is True


async def test_missing_metadata_is_a_no_op(_isolate_vault_dir: Path) -> None:
    assert await _run(PROMISE) is False


@pytest.mark.parametrize("text", ["", "   "])
async def test_no_text_no_check(_isolate_vault_dir: Path, text: str) -> None:
    _seed(_isolate_vault_dir)

    assert await _run(text) is False
