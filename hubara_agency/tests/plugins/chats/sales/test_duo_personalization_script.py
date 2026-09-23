"""El Duo Zodiacal se personaliza — y el guion lo dice (2026-09-22).

Caso real: una clienta mandó la foto de un Duo Virgo (vela beige, plato lila)
y pidió "el plato con el signo de Leo y la vela con el signo de Escorpión, de
esos colores". El bot contestó "Cada set viene con UN solo signo, no se puede
combinar", le mostró Escorpio en negro y, cuando ella dijo "negro no me gusta
/ ¿se puede en este color?", repitió tres veces que el portavela "se escoge al
finalizar el pago" (ella pagaba contra entrega) y remató con "Cuando tengas
los datos de envío que te pedí, seguimos". El humano tomó el control y lo
vendió tal cual lo pidió.

Reglas confirmadas por el operador el 2026-09-23:
  1. Plato y vela pueden llevar signos distintos, al mismo precio.
  2. La vela se hace en cualquier color de la paleta del Duo, sin costo extra.
  3. Si no hay en stock se elabora en 1 a 2 días (menos de 3 del mismo signo).
  4. El color del plato se escoge después, con una foto de lo disponible.
"""
from __future__ import annotations

from pathlib import Path

_WS = (
    Path(__file__).resolve().parents[4]
    / "src/plugins/chats/agent/sales/workspace"
)


def _read(rel: str) -> str:
    return (_WS / rel).read_text(encoding="utf-8")


def test_variants_script_allows_different_signs_for_plate_and_candle() -> None:
    variantes = _read("skills/etapa_variantes/SKILL.md")

    assert "signos distintos" in variantes
    assert "mismo precio" in variantes
    assert "no se puede combinar" not in variantes


def test_variants_script_makes_candle_color_a_free_choice() -> None:
    variantes = _read("skills/etapa_variantes/SKILL.md")

    assert "cualquier color" in variantes
    assert "referencia" in variantes
    # La regla vieja (color fijo por signo) no puede sobrevivir en ningún guion.
    for rel in ("skills/etapa_variantes/SKILL.md", "TOOLS.md"):
        assert "UN color fijo" not in _read(rel), rel


def test_variants_script_tells_the_elaboration_time() -> None:
    variantes = _read("skills/etapa_variantes/SKILL.md")

    assert "1 a 2 días" in variantes
    assert "3 o más del mismo signo" in variantes


def test_variants_script_separates_candle_color_from_plate_color() -> None:
    """La clienta hablaba de la VELA negra y el bot le contestó del plato."""
    variantes = _read("skills/etapa_variantes/SKILL.md")

    assert "no confundas el color de la vela con el del plato" in variantes.lower()


def test_turn_rules_forbid_repeating_and_conditioning_answers() -> None:
    agents = _read("AGENTS.md")

    assert "NUNCA la repitas" in agents
    assert "cuando tengas los datos" in agents  # citado como prohibido
    assert "formulario" in agents


def test_vision_prompt_describes_the_colors_of_each_piece() -> None:
    """La descripción de la foto no traía colores: el bot no podía saber cuál
    era "este color" (vela beige, plato lila)."""
    from src.platform.vision.litellm_adapter import _PROMPT_ES

    assert "colores" in _PROMPT_ES
    assert "cada pieza" in _PROMPT_ES
