"""El guion de las variantes y del descubrimiento (incidente del 2026-10-06).

Contrato sobre texto, como `test_workspace_price_rules.py`:

* **El sí con el total antes del formulario (CON-04).** Con las variantes
  completas, el bot pregunta si confirma la compra, con el precio, ANTES de
  pedir datos; la guarda de `request_shipping_details` lo exige igual
  (`purchase_not_confirmed`, que la calificación cuenta como CON-05). El paso
  7 mandaba directo al formulario con la firma vieja
  `request_shipping_details(order_total_cop, items_summary)`. Tampoco lleva su
  propia frase de introducción: el formulario trae su mensaje (producto,
  variantes, cantidad, subtotal) y repetirlo sobra (PR #392).
* **Lo que le gusta o para quién es.** En el turno 5 el cliente dijo «quiero
  uno para mi mamá, le gusta la naturaleza» y el bot le listó 11 aromas. La
  instrucción de recomendar una o dos opciones va en el guion de la etapa (no
  en la etiqueta del asunto `gusto`, que es una opción del clasificador de
  Jev; revisión del PR #390).
"""
from __future__ import annotations

from pathlib import Path

import pytest

STAGES = Path("src/plugins/chats/agent/sales/workspace/skills")


def _stage(name: str) -> str:
    return (STAGES / name / "SKILL.md").read_text(encoding="utf-8")


def test_variants_script_asks_for_the_yes_with_the_total_before_the_form() -> None:
    stage = _stage("etapa_variantes")
    step = next(line for line in stage.splitlines() if line.startswith("7. "))

    assert "order_total_cop" not in stage and "items_summary" not in stage
    assert "request_shipping_details(items=[{handle, quantity}])" in step
    assert "2× Velón Koala (Sándalo, Café): $72.000 en productos, ¿lo dejamos así?" in step
    assert step.index("¿lo dejamos así?") < step.index("request_shipping_details")


def test_the_form_is_not_introduced_by_the_agent() -> None:
    """El formulario trae su propio mensaje (PR #392): el paso 7 no le pone
    una frase delante."""
    step = next(line for line in _stage("etapa_variantes").splitlines() if line.startswith("7. "))

    assert "Para coordinar tu envío" not in step


@pytest.mark.parametrize("name", ["etapa_descubrimiento", "etapa_variantes"])
def test_what_the_customer_likes_is_answered_with_one_or_two_recommendations(name: str) -> None:
    text = _stage(name)
    line = next(line for line in text.splitlines() if "qué le gusta o para quién es" in line)

    assert "recomiéndale una o dos" in line
    assert "para mi mamá, le gusta la naturaleza" in line
