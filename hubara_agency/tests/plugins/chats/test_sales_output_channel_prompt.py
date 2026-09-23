"""El prompt de ventas describe el canal de salida REAL (run 28a8e407).

Thinking está apagado (`exoclaw-temporal/litellm_config.yaml`: el alias del
agente manda `thinking.type=disabled`), así que NO existe `reasoning_content`.
El prompt decía "tu razonamiento va en `reasoning_content`", pedía una
"Mentalidad operativa (interno)" y una "Auto-revisión antes de enviar": le
pedía pensar sin darle dónde, y el modelo pensaba en el mensaje que se
enviaba ("El cliente pregunta… Le aclaro y le pregunto…").

Contrato: el cliente lee solo `send_reply` (y los params de texto de las
tools que los tienen); el texto libre del modelo es borrador.
"""
from __future__ import annotations

from pathlib import Path

_WORKSPACE = (
    Path(__file__).resolve().parents[3] / "src/plugins/chats/agent/sales/workspace"
)


def _prompt_files() -> list[Path]:
    return sorted([*_WORKSPACE.glob("*.md"), *_WORKSPACE.glob("skills/*/SKILL.md")])


def test_no_prompt_promises_a_private_reasoning_channel() -> None:
    offenders = [
        p.relative_to(_WORKSPACE).as_posix()
        for p in _prompt_files()
        if "reasoning_content" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"thinking está apagado; estos prompts lo prometen: {offenders}"


def test_no_prompt_says_the_free_text_is_what_the_customer_reads() -> None:
    old_contract = ("`content` es LITERAL", "Tu campo `content` se envía")
    offenders = [
        p.relative_to(_WORKSPACE).as_posix()
        for p in _prompt_files()
        if any(phrase in p.read_text(encoding="utf-8") for phrase in old_contract)
    ]
    assert offenders == [], offenders


def test_the_prompt_names_send_reply_as_the_way_to_talk_to_the_customer() -> None:
    for name in ("SOUL.md", "AGENTS.md", "TOOLS.md"):
        assert "send_reply" in (_WORKSPACE / name).read_text(encoding="utf-8"), name
