"""TOOLS.md documenta las tools de catalogo y la regla closed-list."""
from __future__ import annotations

from pathlib import Path

_WORKSPACE = (
    Path(__file__).resolve().parents[2]
    / "src/sales_whatsapp/workspace"
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
