"""TOOLS.md documenta las tools de catalogo y la regla closed-list."""
from __future__ import annotations

from pathlib import Path

_WORKSPACE = (
    Path(__file__).resolve().parents[2]
    / "src/plugins/chats/agent/sales/workspace"
)


def test_search_products_documented_in_tools_md():
    text = (_WORKSPACE / "TOOLS.md").read_text(encoding="utf-8")
    assert "search_products" in text


def test_get_product_by_handle_documented_in_tools_md():
    text = (_WORKSPACE / "TOOLS.md").read_text(encoding="utf-8")
    assert "get_product_by_handle" in text


def test_closed_list_rule_present():
    text = (_WORKSPACE / "TOOLS.md").read_text(encoding="utf-8").lower()
    assert "closed-list" in text or "anti-alucinación" in text
    assert "no inventes" in text or "nunca inventes" in text


def test_aroma_color_closed_list_rule_present():
    """Post-fix #H (bug b2fb9379): regla estricta contra alucinacion de
    aromas/colores/variantes que no aparezcan en `tags` del envelope.

    El LLM dijo "Disponible en lavanda, sándalo, café, coco cremoso, frutos
    rojos y vainilla" — pero "vainilla" no estaba en tags. Esta regla obliga
    al LLM a citar SOLO valores literales del envelope, no su memoria.
    """
    text = (_WORKSPACE / "TOOLS.md").read_text(encoding="utf-8").lower()
    assert "aromas" in text and "colores" in text
    assert "tags" in text
    # La regla vive como "closed-list ... del envelope" (regla 7 de TOOLS.md).
    # OJO: no asertar literales de EJEMPLO ("vainilla") — el prompt diet
    # (PR #110) reescribe la redaccion; lo durable es la semantica closed-list
    # anclada al envelope.
    assert "closed-list" in text
    assert "envelope" in text
