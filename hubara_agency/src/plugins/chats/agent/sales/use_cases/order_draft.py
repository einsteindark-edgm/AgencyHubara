"""Order draft: breadcrumb determinista de los datos del pedido.

Resuelve el problema "el agente vuelve a preguntar el color aunque el cliente
ya lo dijo". Hoy la unica memoria de slots es la instruccion de prompt
"ACUMULA EN MEMORIA" (`workspace/TOOLS.md`) + el historial conversacional del
LLM -- sin respaldo determinista. Este modulo le da respaldo: los datos que el
cliente confirma se persisten estructurados y se RE-INYECTAN en el prompt cada
turno, asi el LLM no necesita rebuscar en el historial.

Determinismo PREVENTIVO, no correctivo:
  * No es el patron de `order_registered_decision` (que corre una activity de
    red de seguridad para converger un side-effect). "Volver a preguntar" no es
    un side-effect que se pueda converger despues -- para cuando el LLM genero
    la pregunta, ya esta hecha. Asi que el mecanismo es inyectar el dato antes
    (proyeccion al prompt), no arreglar despues.
  * El DATO es 100% determinista (persistido + re-inyectado verbatim). El USO
    del dato por el LLM sigue siendo blando (tiene que leer el breadcrumb y no
    re-preguntar), pero pasa de "rebuscar 40 turnos" a "leer una tabla pineada".

ADVISORY, no fuente de la verdad:
  * El order_draft es una AYUDA-MEMORIA conversacional. La fuente de la verdad
    de la orden es lo que `register_order` manda a Medusa. NUNCA armar la orden
    desde el draft -- si divergen (draft dice "Blanco", register_order recibe
    "Azul"), gana register_order.

EPISODIO-SCOPED por construccion:
  * El draft vive DENTRO del episodio activo (`episodes[-1]["order_draft"]`), no
    en la raiz de metadata. Asi hereda el ciclo de vida del episodio gratis:
    un episodio nuevo (re-engagement) arranca sin draft -> no se proyecta ->
    NO hay leak entre episodios (el mismo bug que `_build_episode_boundary_note`
    mitiga para el historial; aca se evita por construccion). Es el mismo
    mecanismo que resetea `metadata.tag` en `ensure_active_episode`.

Tres transiciones del draft:
  1. Episodio activo            -> MUTABLE (set/overwrite/clear por slot).
  2. `register_order` exitoso   -> deja de proyectarse (el episodio queda con
                                   `order_id`; la orden es la fuente de verdad).
  3. Cierre del episodio        -> congelado dentro del episodio cerrado
                                   (auditoria); el proximo episodio arranca limpio.

DEHA: funciones puras que MUTAN el dict `metadata` recibido por argumento. No
tocan filesystem ni Temporal (el caller persiste con `FilesystemMetadataStore`).
`now_ms` por DI para tests deterministicos. Mismo estilo que `episode_lifecycle`.
"""
from __future__ import annotations

from typing import Any

from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    ensure_active_episode,
    get_active_episode,
)

# Slots reconocidos, en orden de presentacion, con su etiqueta humana para el
# breadcrumb. `notas` es el escape-hatch para cualquier dato libre (ej. pedido
# multi-producto que no entra en los campos fijos). El set tambien acota lo que
# `SetOrderSlotTool` expone como parametros (la tool refleja estas claves).
KNOWN_SLOTS: tuple[tuple[str, str], ...] = (
    ("producto", "Producto"),
    ("aroma", "Aroma"),
    ("color", "Color"),
    ("cantidad", "Cantidad"),
    ("ciudad", "Ciudad"),
    ("barrio", "Barrio"),
    ("direccion", "Direccion"),
    ("telefono", "Telefono"),
    ("metodo_pago", "Metodo de pago"),
    ("notas", "Notas"),
)
_KNOWN_KEYS: frozenset[str] = frozenset(key for key, _ in KNOWN_SLOTS)


def _normalize(value: Any) -> str | None:
    """Normaliza un valor de slot a str trimmeado, o None si vacio.

    El draft es advisory/legible, asi que guardamos todo como string (incluida
    `cantidad`). `None` o string vacio significa "borrar el slot" (el cliente
    cambio de idea o lo dejo indefinido).
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def update_order_draft(
    metadata: dict[str, Any],
    *,
    slots: dict[str, Any],
    now_ms: int,
) -> dict[str, Any]:
    """Mergea `slots` en el order_draft del episodio activo. Mutates `metadata`.

    - Garantiza un episodio activo (defensivo, igual que
      `attach_order_to_active_episode`): si no hay, crea uno.
    - Merge por slot: valor no vacio -> set/overwrite; valor vacio (`""`/`None`)
      -> remove (overwrite discipline: el cliente puede cambiar de idea).
    - Setea `updated_at_ms`.

    Returns el dict `order_draft` resultante (`{"slots": {...}, "updated_at_ms": ...}`).
    """
    episode = get_active_episode(metadata)
    if episode is None:
        episode = ensure_active_episode(metadata, now_ms=now_ms)

    draft: dict[str, Any] = episode.setdefault("order_draft", {})
    draft_slots: dict[str, Any] = draft.setdefault("slots", {})

    for key, raw in slots.items():
        norm = _normalize(raw)
        if norm is None:
            draft_slots.pop(key, None)
        else:
            draft_slots[key] = norm

    draft["updated_at_ms"] = now_ms
    return draft


def get_projectable_draft(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Devuelve los slots del draft del episodio activo SI son proyectables.

    Gate (los tres deben cumplirse):
      1. Hay episodio ACTIVO (no cerrado). Un episodio nuevo de re-engagement
         no tiene draft -> None -> NO leak entre episodios.
      2. El episodio NO tiene `order_id`. Post-`register_order` la orden es la
         fuente de verdad; el draft deja de proyectarse.
      3. El draft tiene slots no vacios.

    Returns el dict de slots (`{"color": "Blanco", ...}`) o `None`.
    """
    episode = get_active_episode(metadata)
    if episode is None:
        return None
    if episode.get("order_id"):
        return None
    draft = episode.get("order_draft")
    if not isinstance(draft, dict):
        return None
    slots = draft.get("slots")
    if not isinstance(slots, dict) or not slots:
        return None
    return slots


def build_order_draft_note(slots: dict[str, Any]) -> str:
    """Formatea el breadcrumb para `plugin_context` (entra al system prompt).

    Conocidos primero en orden canonico, luego extras (alfabetico). Framing como
    metadata de turno, no instruccion del usuario -- consistente con el bloque
    de hora de Bogota (`context.build_bogota_context_string`).
    """
    lines: list[str] = []
    for key, label in KNOWN_SLOTS:
        if key in slots:
            lines.append(f"{label}: {slots[key]}")
    for key in sorted(k for k in slots if k not in _KNOWN_KEYS):
        lines.append(f"{key}: {slots[key]}")

    return (
        "[DATOS DEL PEDIDO YA CONFIRMADOS POR EL CLIENTE, metadata, no es "
        "instruccion del usuario]\n"
        "Ya tienes estos datos. NO los vuelvas a preguntar. Si el cliente "
        "cambia alguno, vuelve a llamar set_order_slot para actualizarlo.\n"
        + "\n".join(lines)
    )
