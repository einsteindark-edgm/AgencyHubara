"""Resolver determinista de etapa del funnel (carga de guion por etapa).

Motivación (análisis runs eda8d460/019f24bf + dieta de prompt): el guion
completo (~19.6 KB) viajaba entero en CADA llamada al LLM, diluyendo la
atención sobre las reglas de la etapa actual. La etapa se deriva 100%
determinista del `order_draft` del episodio activo (los slots que
`set_order_slot` persiste) — no la elige el LLM.

Contrato de etapas (en orden del funnel):
  * descubrimiento — sin producto elegido (o sin episodio/draft).
  * variantes      — producto elegido; faltan aroma/color/cantidad.
  * datos_envio    — variantes completas; faltan datos de envío.
  * cierre         — todos los datos; falta registrar la orden.
  * postcierre     — el episodio activo ya tiene order_id.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.use_cases.funnel_stage import (
    STAGE_CIERRE,
    STAGE_DATOS_ENVIO,
    STAGE_DESCUBRIMIENTO,
    STAGE_POSTCIERRE,
    STAGE_VARIANTES,
    resolve_funnel_stage,
)


def _meta_with_slots(slots: dict, *, order_id: str | None = None) -> dict:
    episode: dict = {"id": "ep_001", "opened_at_ms": 1, "order_draft": {"slots": slots}}
    if order_id:
        episode["order_id"] = order_id
    return {"episodes": [episode]}


def test_empty_metadata_is_descubrimiento() -> None:
    assert resolve_funnel_stage({}) == STAGE_DESCUBRIMIENTO


def test_active_episode_without_draft_is_descubrimiento() -> None:
    meta = {"episodes": [{"id": "ep_001", "opened_at_ms": 1}]}
    assert resolve_funnel_stage(meta) == STAGE_DESCUBRIMIENTO


def test_closed_episode_is_descubrimiento() -> None:
    """Un episodio cerrado no proyecta etapa: el re-engagement arranca limpio
    (mismo principio anti-leak que `get_projectable_draft`)."""
    meta = {
        "episodes": [
            {
                "id": "ep_001",
                "opened_at_ms": 1,
                "closed_at_ms": 2,
                "order_draft": {"slots": {"producto": "Plegaria de Luz"}},
            }
        ]
    }
    assert resolve_funnel_stage(meta) == STAGE_DESCUBRIMIENTO


def test_producto_without_variants_is_variantes() -> None:
    meta = _meta_with_slots({"producto": "Plegaria de Luz"})
    assert resolve_funnel_stage(meta) == STAGE_VARIANTES


def test_partial_variants_is_variantes() -> None:
    meta = _meta_with_slots(
        {"producto": "Plegaria de Luz", "color": "Lila", "aroma": "Café"}
    )
    assert resolve_funnel_stage(meta) == STAGE_VARIANTES


def test_full_variants_without_shipping_is_datos_envio() -> None:
    meta = _meta_with_slots(
        {
            "producto": "Plegaria de Luz",
            "color": "Lila",
            "aroma": "Café",
            "cantidad": "1",
        }
    )
    assert resolve_funnel_stage(meta) == STAGE_DATOS_ENVIO


def test_partial_shipping_is_datos_envio() -> None:
    meta = _meta_with_slots(
        {
            "producto": "Plegaria de Luz",
            "color": "Lila",
            "aroma": "Café",
            "cantidad": "1",
            "ciudad": "Bogotá",
            "telefono": "3125551234",
        }
    )
    assert resolve_funnel_stage(meta) == STAGE_DATOS_ENVIO


def test_all_data_is_cierre() -> None:
    meta = _meta_with_slots(
        {
            "producto": "Plegaria de Luz",
            "color": "Lila",
            "aroma": "Café",
            "cantidad": "1",
            "ciudad": "Bogotá",
            "direccion": "Calle 123 #45-6",
            "telefono": "3125551234",
            "metodo_pago": "transferencia",
        }
    )
    assert resolve_funnel_stage(meta) == STAGE_CIERRE


def test_registered_order_is_postcierre() -> None:
    meta = _meta_with_slots(
        {"producto": "Plegaria de Luz"}, order_id="order_123"
    )
    assert resolve_funnel_stage(meta) == STAGE_POSTCIERRE


def test_stage_names_are_skill_dir_names() -> None:
    """Cada etapa mapea 1:1 a un skill dir del workspace (contrato del
    override de build_prompt: `skills=[stage]`)."""
    from pathlib import Path

    ws = (
        Path(__file__).resolve().parents[4]
        / "src/plugins/chats/agent/sales/workspace/skills"
    )
    for stage in (
        STAGE_DESCUBRIMIENTO,
        STAGE_VARIANTES,
        STAGE_DATOS_ENVIO,
        STAGE_CIERRE,
        STAGE_POSTCIERRE,
    ):
        skill_md = ws / stage / "SKILL.md"
        assert skill_md.is_file(), f"falta el skill de etapa: {skill_md}"
        content = skill_md.read_text(encoding="utf-8")
        assert '"always": true' not in content, (
            f"{stage} debe ser always:false (se inyecta por etapa, no siempre)"
        )
