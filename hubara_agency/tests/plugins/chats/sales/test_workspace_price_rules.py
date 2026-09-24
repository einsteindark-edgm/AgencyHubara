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

from src.plugins.chats.agent.sales.config.shipping import (
    SHIPPING_RATE_BOGOTA_COP,
    SHIPPING_RATE_NATIONAL_COP,
)
from src.plugins.chats.agent.sales.tools.order_registration import RegisterOrderTool

WORKSPACE = Path("src/plugins/chats/agent/sales/workspace")
REMARKETING = Path("src/plugins/chats/agent/remarketing/workspace")
#: Todo agente que le habla al cliente: la regla vale en todos los workspaces.
AGENT_WORKSPACES = (WORKSPACE, REMARKETING)

_STRICT_THRESHOLD_RE = re.compile(
    r"(mayores a|superiores a|mayor a|superior a|>)\s*\$?\s*45[.,]?000", re.IGNORECASE
)


def _md_files() -> list[Path]:
    files = sorted(p for ws in AGENT_WORKSPACES for p in ws.rglob("*.md"))
    assert files, "workspace vacío"
    return files


def _cop(amount: int) -> str:
    return "$" + f"{amount:,}".replace(",", ".")


def test_cod_threshold_is_inclusive_everywhere() -> None:
    offenders = [
        f"{p}: {m.group(0)!r}"
        for p in _md_files()
        for m in _STRICT_THRESHOLD_RE.finditer(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], "el umbral de contra entrega es INCLUSIVO (desde $45.000)"


# --- Envío: siempre la tarifa publicada (decisión del operador 2026-09-23) ---


def test_catalog_skills_cite_the_published_shipping_rates() -> None:
    """Las tarifas que conoce cada agente son las de `config/shipping.py` (la
    skill de remarketing decía "$12.000 a $15.000")."""
    for ws in AGENT_WORKSPACES:
        text = (ws / "skills" / "hubara_catalog" / "SKILL.md").read_text(encoding="utf-8")
        assert _cop(SHIPPING_RATE_BOGOTA_COP) in text, ws
        assert _cop(SHIPPING_RATE_NATIONAL_COP) in text, ws
        assert "$12.000 a $15.000" not in text, ws


def test_remarketing_knowledge_offers_no_discounts() -> None:
    """Remarketing NUNCA ofrece descuentos ni envío gratis (AGENTS.md): su
    skill de catálogo no le da descuentos para ofrecer."""
    text = (REMARKETING / "skills" / "hubara_catalog" / "SKILL.md").read_text(encoding="utf-8")
    assert "Descuento de Bienvenida" not in text
    assert "Descuento Testimonio" not in text
    tools = (REMARKETING / "TOOLS.md").read_text(encoding="utf-8")
    assert "`always: true`" not in tools, "la skill hubara_catalog de remarketing es always: false"


def test_remarketing_knowledge_keeps_the_sale_in_the_chat() -> None:
    """AGENTS.md de remarketing prohíbe mandar al cliente a la web; su skill
    de catálogo no puede ofrecer "pago vía plataforma web"."""
    text = (REMARKETING / "skills" / "hubara_catalog" / "SKILL.md").read_text(encoding="utf-8")
    assert "plataforma web" not in text


def test_closing_script_passes_the_published_shipping_rate_even_with_cash_on_delivery() -> None:
    """Con contra entrega la confirmación no le da un total al bot: si el guion
    no le dice qué envío pasar, registra con envío 0 y `register_order` lo
    rechaza (`shipping_mismatch`)."""
    stage = (WORKSPACE / "skills" / "etapa_cierre" / "SKILL.md").read_text(encoding="utf-8")
    step3 = next(line for line in stage.splitlines() if line.startswith("3. "))
    assert "shipping_cop" in step3 and "nunca 0" in step3.lower()
    assert "contra entrega" in step3.lower()
    tools = (WORKSPACE / "TOOLS.md").read_text(encoding="utf-8")
    row = next(line for line in tools.splitlines() if line.startswith("| `register_order` |"))
    assert "shipping_cop" in row and "nunca 0" in row.lower()


def test_closing_script_retries_validation_rejections_instead_of_escalating() -> None:
    """Un rechazo de validación (`error` en el envelope) se corrige y se
    reintenta; solo el rechazo de Medusa escala como ORDER_REGISTRATION_FAILED
    (si no, un pedido contra entrega registrable le llega a un humano como si
    Medusa hubiera fallado)."""
    stage = (WORKSPACE / "skills" / "etapa_cierre" / "SKILL.md").read_text(encoding="utf-8")
    step4 = stage[stage.index("4. Lee el envelope"): stage.index("\n5. ")]
    reasons = ("shipping_mismatch", "amount_mismatch", "price_mismatch", "missing_receiver_name")
    for reason in reasons:
        assert reason in step4, reason
    tools = (WORKSPACE / "TOOLS.md").read_text(encoding="utf-8")
    row = next(
        line for line in tools.splitlines()
        if line.startswith("|") and "ORDER_REGISTRATION_FAILED" in line
    )
    assert "Medusa" in row
    for reason in reasons:
        assert reason in RegisterOrderTool.description, reason


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
