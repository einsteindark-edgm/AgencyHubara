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


WORKSPACE = STAGES.parent
#: El formulario trae su propio mensaje (PR #392): ningún guion le pone una
#: frase delante. Con las variantes completas la etapa ya es
#: `etapa_datos_envio` (`funnel_stage.py`), así que no basta con el paso 7:
#: lo que está cargado en ese turno (el núcleo `sales_script`) también la
#: ordenaba (segunda revisión del PR #390).
FORM_INTRO = "Para coordinar tu envío"
#: Excepción temporal: `etapa_datos_envio/SKILL.md` lo corrige el PR #392, que
#: se mergea ANTES que este. Al actualizar con main, si ya no trae la frase,
#: esta excepción se quita.
NOT_YET = {"skills/etapa_datos_envio/SKILL.md"}


def test_the_form_is_not_introduced_by_the_agent() -> None:
    offenders = [
        str(path.relative_to(WORKSPACE))
        for path in sorted(WORKSPACE.rglob("*.md"))
        if str(path.relative_to(WORKSPACE)) not in NOT_YET and FORM_INTRO in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_the_core_script_asks_for_the_yes_with_the_total_before_the_form() -> None:
    """El ejemplo del núcleo («Sí, la quiero» sin color) se saltaba el sí con
    el total y mandaba directo al formulario."""
    core = (STAGES / "sales_script" / "SKILL.md").read_text(encoding="utf-8")
    example = next(line for line in core.splitlines() if '"Sí, la quiero"' in line)

    assert "en productos, ¿lo dejamos así?" in example
    assert example.index("¿lo dejamos así?") < example.index("request_shipping_details")


@pytest.mark.parametrize("name", ["etapa_descubrimiento", "etapa_variantes"])
def test_what_the_customer_likes_is_answered_with_one_or_two_recommendations(name: str) -> None:
    text = _stage(name)
    line = next(line for line in text.splitlines() if "qué le gusta o para quién es" in line)

    assert "recomiéndale una o dos" in line
    assert "para mi mamá, le gusta la naturaleza" in line
