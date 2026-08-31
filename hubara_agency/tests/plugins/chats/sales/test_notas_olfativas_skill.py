"""Guard de la skill notas_olfativas (pirámides olfativas por aroma).

El conocimiento sensorial por-aroma es cross-producto (Drakar aparece en
varias velas), así que no cabe en la description por-producto de Medusa ni
en SOUL.md (ratchet de dieta). Vive como skill on-demand cargable con
`load_skill("notas_olfativas")` — este guard impide que una dieta futura
la deje huérfana (sin puntero en TOOLS.md el LLM jamás la cargaría).
"""
from __future__ import annotations

import re
from pathlib import Path

_WS = (
    Path(__file__).resolve().parents[4]
    / "src/plugins/chats/agent/sales/workspace"
)
_SKILL = _WS / "skills" / "notas_olfativas" / "SKILL.md"


def test_skill_exists_with_documented_aromas() -> None:
    text = _SKILL.read_text(encoding="utf-8")
    for aroma in ("Ylang Ylang", "Drakar", "Chanel"):
        assert aroma in text, f"falta la pirámide olfativa de {aroma}"
    # El catálogo Medusa lo escribe "Ylan ylang" (tag real) — sin el alias
    # el LLM no conecta la skill con el resultado de search_products.
    assert "Ylan ylang" in text, "falta el alias con la grafía del catálogo"


def test_skill_is_on_demand_not_always() -> None:
    text = _SKILL.read_text(encoding="utf-8")
    assert '"always": true' not in text, (
        "notas_olfativas debe ser on-demand — always:true reabre la "
        "inflación del prompt (ver test_prompt_budget)"
    )
    # Frontmatter rule: single-line inline JSON, NO block scalar (quirk
    # del loader exoclaw, mismo guard que hubara_catalog).
    assert "metadata: |" not in text
    assert re.search(r"^metadata:\s*\{", text, flags=re.MULTILINE)


def test_tools_md_points_to_the_skill() -> None:
    tools = (_WS / "TOOLS.md").read_text(encoding="utf-8")
    assert 'load_skill("notas_olfativas")' in tools, (
        "TOOLS.md (siempre en prompt) debe enseñar cuándo cargar "
        "notas_olfativas; sin el puntero la skill es inalcanzable "
        "(mismo modo de falla que el bug de hubara_catalog)"
    )
