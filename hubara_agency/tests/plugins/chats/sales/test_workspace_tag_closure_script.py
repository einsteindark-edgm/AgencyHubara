"""Guard: el guion que NOSOTROS dictamos no le pide al modelo una secuencia que
el runtime no permite (misma trampa que L-20, otra tool).

Run b06636a6 → 5ed9af2d: `TOOLS.md` ordenaba etiquetar RECHAZO "en el mismo
turno de la despedida". Pero un texto sin tool termina el turno, y un texto
JUNTO a la tool se descarta (default-deny): la única forma de cumplir era
`content(despedida) + tool_call(tag)` → despedida descartada → `llm_chat`
forzado → acuse ("Etiqueta registrada."). El contrato vivo: con INTERESADO o
RECHAZO `manage_conversation_tag` TERMINA el turno y la despedida viaja en su
param `customer_message`. Si el guion sigue mostrando la llamada de 2 args, es
un few-shot de omitir el param.
"""
from __future__ import annotations

import re
from pathlib import Path

from src.plugins.chats.agent.sales.prompts import build_ghosting_prompt

_WS = (
    Path(__file__).resolve().parents[4]
    / "src/plugins/chats/agent/sales/workspace"
)

# Llamada dictada con un tag AUTOSUFICIENTE: `manage_conversation_tag("RECHAZO", …)`.
_SELF_SUFFICIENT_CALL = re.compile(
    r"manage_conversation_tag\(\s*[\"'](?:INTERESADO|RECHAZO)[\"'][^)]*\)"
)


def _read(rel: str) -> str:
    return (_WS / rel).read_text(encoding="utf-8")


def test_tools_md_states_that_the_closing_line_travels_in_the_param() -> None:
    tools = _read("TOOLS.md")
    row = next(
        line for line in tools.splitlines() if line.startswith("| `manage_conversation_tag`")
    )

    assert "customer_message" in row, row
    assert "TERMINA" in row, row


def test_tools_md_no_longer_dictates_the_impossible_sequence() -> None:
    assert "en el mismo turno de la despedida" not in _read("TOOLS.md")


def test_every_dictated_closing_tag_call_carries_the_customer_message() -> None:
    offenders: list[str] = []
    audited = 0
    for rel in ("TOOLS.md", "AGENTS.md", "skills/sales_script/SKILL.md"):
        for lineno, line in enumerate(_read(rel).splitlines(), 1):
            for match in _SELF_SUFFICIENT_CALL.finditer(line):
                audited += 1
                if "customer_message" not in match.group(0):
                    offenders.append(f"{rel}:{lineno}: {match.group(0)}")

    # Piso: si la convención cambia y el guard deja de VER llamadas, que falle
    # en vez de pasar en vacío.
    assert audited >= 2, f"el guard solo vio {audited} llamadas dictadas"
    assert not offenders, (
        "Llamada dictada sin `customer_message` (few-shot de omitir la "
        "despedida):\n  " + "\n  ".join(offenders)
    )


def test_agents_md_explains_the_closing_contract() -> None:
    line = next(
        ln for ln in _read("AGENTS.md").splitlines() if "Cierre comercial natural" in ln
    )

    assert "customer_message" in line, line


def test_ghosting_prompt_says_there_is_no_customer_to_write_to() -> None:
    """Cierre por ghosting = turno admin: el cliente no está. El modelo no debe
    gastar una despedida en `customer_message` (igual no saldría). Se exige la
    NEGACIÓN pegada al param: que la palabra aparezca no alcanza (un "manda tu
    despedida en `customer_message`" también la contendría)."""
    prompt = build_ghosting_prompt()

    assert re.search(r"\bNo mandes\s+`customer_message`", prompt), prompt[-400:]
