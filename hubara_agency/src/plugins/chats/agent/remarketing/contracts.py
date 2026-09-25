"""Boundary DTOs del dominio Remarketing.

Aplicacion de R-JSON: cualquier valor que cruce `workflow.execute_workflow` /
`workflow.run` es un dataclass plano JSON-serializable.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RemarketingSessionInput:
    """Input del `RemarketingSessionWorkflow`.

    Campos:
      * `session_id`: identificador de la conversacion (`{WHATSAPP_SESSION_PREFIX}{phone}`).
      * `motivo`: el motivo registrado por Sales cuando la conversacion quedo
        en `INTERESADO` (e.g. "el cliente dudo del precio"). Usado aguas abajo
        en `build_remarketing_trigger_activity` para personalizar el gancho.
      * `runtime_workspace_path`: ruta del workspace canonico del agente de
        Remarketing (donde viven `IDENTITY.md`, `SOUL.md`, `USER.md`,
        `TOOLS.md`, `AGENTS.md`, `memory/*` y `skills/*`). Resuelto en el
        composition root (dispatcher / interfaz) via
        `config/env.py:get_workspace_path()` y propagado como string para
        cumplir R-JSON. PR-B: la activity ahora lo consume — instancia
        `WorkspaceConfig(path=runtime_workspace_path)` y falla fast con
        `RuntimeError` si falta (analogo a sales_whatsapp PR-B /
        ADR-2026-05-06-04). El campo permanece `Optional` solo por
        compatibilidad de DTOs en flight; en produccion el dispatcher SIEMPRE
        debe wirearlo.

    NOTA: el orden de los campos importa para replay. `session_id` y `motivo`
    eran los unicos campos pre-PR-A; `runtime_workspace_path` se agrega al
    final con default `None` para no romper fixtures viejas.
    """

    session_id: str
    motivo: str
    runtime_workspace_path: str | None = None


@dataclass(frozen=True)
class RemarketingContext:
    """Lo que el gancho necesita saber de la conversación de Sales (plano, R-JSON).

    Incidente run dc32f7fe (2026-09-10): el agente de remarketing NO ve el
    historial de Sales (el HistoryStore de exoclaw se aísla por workspace) y
    el ciclo del Window Strategist le pisaba el `motivo` del tag con un string
    genérico — inventó "quedó pendiente lo de tu pedido" a un cliente que
    quería comprar cera. Esta DTO le da el motivo real, si hay pedido a
    medias, y los últimos mensajes del transcript del vault.
    """

    tag_motivo: str = ""
    has_order_draft: bool = False
    transcript: str = ""
    #: Escalera de reactivación (2026-09-18): qué toque es este (1-based) y
    #: cuánto silencio real lleva el cliente. None = contexto legacy (histories
    #: en vuelo traen el result viejo sin estos campos).
    touch_number: int | None = None
    silence_minutes: int | None = None
    #: Campaña que abrió el episodio activo (runs edbb0d8b / 8e73b7dc), ya
    #: redactada para el trigger. "" = el episodio no lo abrió una campaña.
    campaign_context: str = ""
    #: Ficha REAL del catálogo (snapshot de Ventas) de los productos nombrados
    #: en la charla + la lista de lo que existe. "" = catálogo no disponible.
    #: Incidente 2026-09-25: sin esto el gancho inventó «el Cubo Love también
    #: viene en vaso» (Cubo Love tiene una sola presentación).
    catalog_facts: str = ""
    #: Lo que el cliente pidió/mostró y NO existe en el catálogo («vaso»,
    #: «dragón»): el trigger se lo prohíbe al LLM y la guarda de salida bloquea
    #: el gancho que lo mencione igual. [] = nada detectado / sin catálogo.
    unavailable_terms: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RemarketingTriggerInput:
    """Input de `build_remarketing_trigger_v2_activity` (plano, R-JSON)."""

    motivo: str
    memory_context: str = ""
    has_order_draft: bool = False
    transcript: str = ""
    touch_number: int | None = None
    silence_minutes: int | None = None
    #: Ver `RemarketingContext.campaign_context`.
    campaign_context: str = ""
    #: Ver `RemarketingContext.catalog_facts`.
    catalog_facts: str = ""
    #: Ver `RemarketingContext.unavailable_terms`.
    unavailable_terms: list[str] = field(default_factory=list)
