"""El aviso de ghosting cuando el motor ya decidió la etiqueta (F8, V2).

Hoy el LLM elige entre 4 etiquetas con el aviso de ghosting. Con Jev, el
aviso le dice cuál usar: el LLM solo ejecuta `manage_conversation_tag` (y,
para CONFIRMADO_SIN_DATOS, `escalate_to_human`), así la mecánica del cierre
(episodio, CAPI, remarketing, escalación) sigue exactamente igual. V1 llama
la activity sin sesión: el aviso de hoy, palabra por palabra.
"""
from __future__ import annotations

import json
from pathlib import Path

from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales.prompts import build_decided_ghosting_prompt, build_ghosting_prompt

SID = "wa_573001234567"


def test_the_decided_prompt_names_the_tag_and_keeps_the_golden_rule() -> None:
    prompt = build_decided_ghosting_prompt("RECHAZO")

    assert "tag=`RECHAZO`" in prompt and "manage_conversation_tag" in prompt
    assert "NO generes ninguna respuesta visible" in prompt
    assert "escalate_to_human" not in prompt
    assert "ORDER_PENDING_SHIPPING_DETAILS" in build_decided_ghosting_prompt("CONFIRMADO_SIN_DATOS")


def _seed(vault: Path, *, order_id: str | None = None) -> None:
    session = vault / SID
    (session / "sessions").mkdir(parents=True, exist_ok=True)
    episode = {"episode_id": "ep_1", "started_at_ms": 1, "closed_at_ms": None, **({"order_id": order_id} if order_id else {})}
    (session / "metadata.json").write_text(json.dumps({"episodes": [episode]}), encoding="utf-8")
    lines = [{"role": "assistant", "content": "¿Te lo separo?"}, {"role": "user", "content": "No gracias, ya no lo quiero"}]
    (session / "sessions" / f"{SID}.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")


def _jev(monkeypatch, choice: str, p: float):
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    monkeypatch.setenv("DECISIONS_BOT", "B")
    answer = TypedAnswer(id="cierre.etiqueta", kind="choice", choice=choice, probs=((choice, p),), confidence=p)
    fake = FakePerceptionAdapter({"cierre.etiqueta": answer})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    return fake


async def test_v1_without_a_session_gets_todays_prompt() -> None:
    from src.plugins.chats.agent.sales.activities.bootstrap_session import decide_ghosting_action

    assert await ActivityEnvironment().run(decide_ghosting_action) == build_ghosting_prompt()


async def test_with_todays_bot_the_session_gets_todays_prompt(_isolate_vault_dir: Path, monkeypatch) -> None:
    from src.plugins.chats.agent.sales.activities.bootstrap_session import decide_ghosting_action

    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    _seed(_isolate_vault_dir)

    assert await ActivityEnvironment().run(decide_ghosting_action, SID) == build_ghosting_prompt()


async def test_with_jev_the_prompt_says_which_tag(_isolate_vault_dir: Path, monkeypatch) -> None:
    from src.plugins.chats.agent.sales.activities.bootstrap_session import decide_ghosting_action

    _jev(monkeypatch, "rechazo", 0.94)
    _seed(_isolate_vault_dir)

    assert await ActivityEnvironment().run(decide_ghosting_action, SID) == build_decided_ghosting_prompt("RECHAZO")


async def test_with_a_registered_order_the_close_is_a_successful_purchase(_isolate_vault_dir: Path, monkeypatch) -> None:
    from src.plugins.chats.agent.sales.activities.bootstrap_session import decide_ghosting_action

    _jev(monkeypatch, "interesado", 0.9)
    _seed(_isolate_vault_dir, order_id="order_01")

    assert await ActivityEnvironment().run(decide_ghosting_action, SID) == build_decided_ghosting_prompt("COMPRA_EXITOSA")


async def test_when_jev_doubts_the_llm_decides_like_today(_isolate_vault_dir: Path, monkeypatch) -> None:
    from src.plugins.chats.agent.sales.activities.bootstrap_session import decide_ghosting_action

    _jev(monkeypatch, "rechazo", 0.55)
    _seed(_isolate_vault_dir)

    assert await ActivityEnvironment().run(decide_ghosting_action, SID) == build_ghosting_prompt()
