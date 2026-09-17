"""Guardas sobre el WORKSPACE del agente de ventas (prompts) — incidente run
ebbc203d (2026-09-16): el precio sale SOLO del catálogo y el umbral de contra
entrega se cita igual en todos lados (inclusive, "desde $45.000").

Son tests de contrato sobre texto: si alguien reintroduce "mayores a $45.000"
o vuelve a documentar `request_shipping_details(order_total_cop=...)`, el
guion y el código divergen otra vez.
"""
from __future__ import annotations

import re
from pathlib import Path

WORKSPACE = Path("src/plugins/chats/agent/sales/workspace")

_STRICT_THRESHOLD_RE = re.compile(
    r"(mayores a|superiores a|mayor a|superior a|>)\s*\$?\s*45[.,]?000", re.IGNORECASE
)


def _md_files() -> list[Path]:
    files = sorted(WORKSPACE.rglob("*.md"))
    assert files, "workspace vacío"
    return files


def test_cod_threshold_is_inclusive_everywhere() -> None:
    offenders = [
        f"{p}: {m.group(0)!r}"
        for p in _md_files()
        for m in _STRICT_THRESHOLD_RE.finditer(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], "el umbral de contra entrega es INCLUSIVO (desde $45.000)"


def test_tools_doc_describes_items_based_shipping_request() -> None:
    tools = (WORKSPACE / "TOOLS.md").read_text(encoding="utf-8")
    assert "`request_shipping_details` ⛔ |" in tools
    assert "items=[{handle, quantity}]" in tools
    assert "order_total_cop" not in tools
    stage = (WORKSPACE / "skills" / "etapa_datos_envio" / "SKILL.md").read_text(encoding="utf-8")
    assert "order_total_cop" not in stage
    assert "items" in stage


def test_price_source_rule_names_the_ad_and_the_customer() -> None:
    """La regla de 'precio = catálogo' menciona explícitamente que ni el
    anuncio ni lo que escriba el cliente son fuente de precio."""
    soul = (WORKSPACE / "SOUL.md").read_text(encoding="utf-8")
    section = soul[soul.index("## Hablar de plata"):]
    section = section[: section.index("\n## ", 1)] if "\n## " in section[1:] else section
    assert "anuncio" in section.lower()
    assert "catálogo" in section.lower()


def test_tools_doc_explains_quoted_price_mismatch() -> None:
    tools = (WORKSPACE / "TOOLS.md").read_text(encoding="utf-8")
    assert "quoted_price_mismatch" in tools
    assert "unit_price_cop" in tools
