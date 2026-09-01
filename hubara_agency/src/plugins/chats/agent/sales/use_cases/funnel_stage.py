"""Resolver determinista de la etapa del funnel de ventas.

Motivación (dieta de prompt, análisis runs eda8d460/019f24bf): el guion
conversacional completo viajaba entero (~19.6 KB) en CADA llamada al LLM.
Este módulo deriva la etapa actual de forma 100% determinista desde el
`order_draft` del episodio activo (los slots que `set_order_slot` persiste
en `metadata.json`), y el override de `build_prompt` del worker Sales
inyecta SOLO el skill de guion de esa etapa (`skills=[stage]`).

La etapa NO la elige el LLM (eso sería `load_skill`, no-determinista): es
una proyección pura del estado persistido. Mismo espíritu que
`order_draft.py` ("determinismo PREVENTIVO": inyectar el contexto correcto
antes, no corregir después).

Contrato de etapas (cada una mapea 1:1 a un skill dir en
`workspace/skills/<etapa>/SKILL.md`, `always:false`):

  * ``etapa_descubrimiento`` — sin producto elegido (o sin episodio activo /
    sin draft). Apertura, SPIN, catálogo.
  * ``etapa_variantes`` — producto elegido; falta aroma, color o cantidad.
    Pickers, recomendación sensorial.
  * ``etapa_datos_envio`` — variantes completas; faltan datos de envío
    (ciudad / dirección / teléfono / nombre de quien recibe / método de
    pago; barrio y cédula son opcionales).
  * ``etapa_cierre`` — todos los datos; falta verificar y registrar la orden.
  * ``etapa_postcierre`` — el episodio activo ya tiene ``order_id``:
    la orden es la fuente de verdad; acompañamiento post-venta.

Bordes suaves por diseño: la etapa es GUÍA (qué sección del guion se
inyecta), no un gate duro — el core del guion (`sales_script`, always:true)
mantiene el mapa completo del funnel para que el LLM nunca quede ciego en
una transición.

DEHA: función pura sobre el dict `metadata` (sin I/O, sin reloj) — el
caller (activity) lee metadata y persiste; esto solo proyecta.
"""
from __future__ import annotations

from typing import Any

from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    get_active_episode,
)

STAGE_DESCUBRIMIENTO = "etapa_descubrimiento"
STAGE_VARIANTES = "etapa_variantes"
STAGE_DATOS_ENVIO = "etapa_datos_envio"
STAGE_CIERRE = "etapa_cierre"
STAGE_POSTCIERRE = "etapa_postcierre"

ALL_STAGES: tuple[str, ...] = (
    STAGE_DESCUBRIMIENTO,
    STAGE_VARIANTES,
    STAGE_DATOS_ENVIO,
    STAGE_CIERRE,
    STAGE_POSTCIERRE,
)

# Slots que completan cada bloque del funnel. `barrio`, `cedula` y `notas`
# son opcionales a propósito (el guion los pide, pero no bloquean la etapa).
# `nombre_recibe` sí bloquea: la transportadora exige el nombre de quien
# recibe (requisito 2026-08-31).
_VARIANT_SLOTS: tuple[str, ...] = ("aroma", "color", "cantidad")
_SHIPPING_SLOTS: tuple[str, ...] = (
    "ciudad", "direccion", "telefono", "nombre_recibe", "metodo_pago"
)


def resolve_funnel_stage(metadata: dict[str, Any]) -> str:
    """Proyecta la etapa del funnel desde el estado persistido del episodio."""
    episode = get_active_episode(metadata)
    if episode is None:
        return STAGE_DESCUBRIMIENTO
    if episode.get("order_id"):
        return STAGE_POSTCIERRE

    draft = episode.get("order_draft")
    slots = draft.get("slots") if isinstance(draft, dict) else None
    if not isinstance(slots, dict) or not slots.get("producto"):
        return STAGE_DESCUBRIMIENTO
    if not all(slots.get(k) for k in _VARIANT_SLOTS):
        return STAGE_VARIANTES
    if not all(slots.get(k) for k in _SHIPPING_SLOTS):
        return STAGE_DATOS_ENVIO
    return STAGE_CIERRE
