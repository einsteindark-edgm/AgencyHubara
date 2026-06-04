"""Episode lifecycle: cada `wa_<phone>` puede tener N episodios en el tiempo.

Un episodio es un tramo de conversación con una intención propia entre el
cliente y el agente. Cliente compra hoy + vuelve en 2 meses a cotizar +
compra otra vez = 3 episodios distintos, cada uno con su `AdsState`.

Modelo en `metadata["episodes"]: list[dict]`:

    {
      "episode_id": "ep_001",                 # secuencial dentro de la sesión
      "started_at_ms": 1700000000000,         # primer inbound del episodio
      "started_inbound_message_id": "wamid....",
      "closed_at_ms": null | int,             # null si activo
      "closing_tag": null | "COMPRA_EXITOSA" | "RECHAZO" | "CONFIRMADO_SIN_DATOS",
      "closing_motivo": null | str,
      "order_id": null | str,                 # registered_order.order_id si hubo venta
      "referral_snapshot": null | dict,       # snapshot del referral al iniciar el episodio
    }

Reglas:
- Un episodio **arranca** cuando llega un inbound y NO hay episodio activo
  (o sea: `episodes[]` vacío o último con `closed_at_ms != null`).
- Un episodio **cierra** cuando se llama a `manage_conversation_tag` con un
  tag de `CLOSING_TAGS`.
- `attach_order_to_active_episode` anota la venta sin cerrar el episodio —
  el cierre formal lo hace el `manage_conversation_tag(COMPRA_EXITOSA)`
  que el agente invoca justo después.

DEHA:
- Funciones puras que MUTAN el dict `metadata` recibido por argumento.
  No tocan filesystem ni Temporal. El caller persiste con
  `FilesystemMetadataStore.write()`.
- `now_ms` por DI para tests determinísticos (R-DET implícito).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def count_session_jsonl_lines(vault_dir: Path, session_key: str) -> int:
    """Cuenta líneas no vacías del `<vault>/<session>/sessions/<session>.jsonl`.

    Helper compartido por callers que necesitan snapshotear el `msgs_count`
    de un episodio (FU3) sin replicar la lógica del path. Devuelve 0 si el
    JSONL no existe o no se puede leer.

    Convención exoclaw: el JSONL del history de una sesión vive en
    `<vault_dir>/<session_key>/sessions/<session_key>.jsonl`.
    """
    jsonl = vault_dir / session_key / "sessions" / f"{session_key}.jsonl"
    if not jsonl.exists():
        return 0
    try:
        with jsonl.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0

# Tags que cierran formalmente un episodio. Coinciden con los que
# `ManageConversationTagTool` acepta en su `_TAG_ENUM` (los 4 que indican
# fin de la intención del cliente):
#   - COMPRA_EXITOSA → cliente compró Y pago verificado (humano lo confirma)
#   - RECHAZO → cliente no compra (cierre definitivo)
#   - CONFIRMADO_SIN_DATOS → confirmó pero faltó shipping (escalado a humano)
#   - CONFIRMADO_PAGO_PENDIENTE → orden registrada + datos completos, falta
#     verificación humana del pago (transferencia / efectivo / tarjeta sin
#     pasarela). El episodio se cierra porque la intención de compra terminó
#     desde el lado del cliente — el humano solo confirma el pago.
# INTERESADO NO está acá: indica "cliente sigue dudando, programa
# remarketing" — el episodio sigue activo esperando reacción del cliente.
CLOSING_TAGS: frozenset[str] = frozenset(
    {
        "COMPRA_EXITOSA",
        "RECHAZO",
        "CONFIRMADO_SIN_DATOS",
        "CONFIRMADO_PAGO_PENDIENTE",
    }
)


# Tag sintético usado cuando el lifecycle cierra un episodio por timeout
# (sin acción del agente). NO está en CLOSING_TAGS porque no proviene del
# agente — el classifier lo trata como `no_reply`.
TIMEOUT_CLOSING_TAG: str = "TIMEOUT"


# Si el episodio activo no recibió inbound del cliente en este tiempo,
# se cierra como TIMEOUT cuando llega un inbound nuevo. Esto evita que
# episodios abandonados queden vivos eternamente impidiendo el
# re-engagement. El threshold también lo consume el classifier para
# reportar `no_reply` cuando un episodio activo es "muy viejo" sin mutar
# el state.
EPISODE_TIMEOUT_MS: int = 14 * 24 * 60 * 60 * 1000  # 14 días


# =============================================================================
# Helpers internos
# =============================================================================


def _next_episode_id(episodes: list[dict[str, Any]]) -> str:
    """Calcula el siguiente id secuencial dentro de la sesión.

    Convención: `ep_NNN` con padding 3 dígitos. NO usamos UUID porque el id
    es local al `wa_<phone>` (no necesita ser global) y la legibilidad
    importa para debug del dashboard.
    """
    next_seq = len(episodes) + 1
    return f"ep_{next_seq:03d}"


def _make_empty_episode(
    *,
    episode_id: str,
    now_ms: int,
    inbound_message_id: str | None,
    referral_snapshot: dict[str, Any] | None,
    msgs_count_at_start: int | None = None,
) -> dict[str, Any]:
    """Construye un episodio nuevo en estado activo (closed_at_ms=None).

    `msgs_count_at_start` (FU3) es el count total del JSONL al momento de
    crear el episodio. Permite derivar `msgs_in_episode` exactamente sin
    parsear timestamps de cada turno: `msgs_count_at_close - msgs_count_at_start`
    para cerrados, o `current_total - msgs_count_at_start` para activos.
    `None` para legacy / casos donde el caller no puede contar.
    """
    return {
        "episode_id": episode_id,
        "started_at_ms": now_ms,
        "started_inbound_message_id": inbound_message_id,
        "closed_at_ms": None,
        "closing_tag": None,
        "closing_motivo": None,
        "order_id": None,
        # Total congelado de la orden (COP, major units) atribuida al episodio.
        # Se setea junto al `order_id` en `attach_order_to_active_episode` — es
        # el ingreso REAL de esta conversación, frozen al cierre (mismo patrón
        # que `llm_usage`). None mientras el episodio no tenga venta registrada.
        "order_total_cop": None,
        "order_currency": None,
        "referral_snapshot": referral_snapshot,
        "msgs_count_at_start": msgs_count_at_start,
        "msgs_count_at_close": None,
    }


# =============================================================================
# Public API
# =============================================================================


def get_active_episode(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Devuelve el último episodio si está activo, sino None.

    Útil para que callers que solo leen (e.g. el classifier o un endpoint
    read-only) consulten el estado sin mutaciones.
    """
    episodes = metadata.get("episodes")
    if not episodes:
        return None
    last = episodes[-1]
    if last.get("closed_at_ms") is not None:
        return None
    return last


def ensure_active_episode(
    metadata: dict[str, Any],
    *,
    now_ms: int,
    inbound_message_id: str | None = None,
    referral_snapshot: dict[str, Any] | None = None,
    msgs_count_at_start: int | None = None,
) -> dict[str, Any]:
    """Garantiza un episodio activo en `metadata["episodes"]`. Mutates.

    - Sin `episodes[]` o vacío → crea ep_001.
    - Último episode con `closed_at_ms != null` → crea ep_NNN+1.
    - Último episode activo → lo devuelve sin tocar (started_at_ms NO
      cambia — sigue siendo el primer inbound del episodio).

    Si crea un episodio NUEVO después de uno cerrado (re-engagement del
    mismo cliente), **resetea `metadata.tag` a "NO_ETIQUETADO"** y limpia
    `metadata.motivo` — sino el tag del episodio cerrado anterior
    (COMPRA_EXITOSA / RECHAZO) contaminaría el classifier del nuevo
    episodio activo. `status_history` se preserva entero (es el log
    histórico cross-episode).

    **Timeout**: si el último episodio está activo pero su `started_at_ms`
    es más viejo que `EPISODE_TIMEOUT_MS`, se cierra automáticamente con
    `closing_tag=TIMEOUT_CLOSING_TAG` antes de abrir uno nuevo. Esto
    captura el caso "cliente abandonó la conversación sin cierre formal y
    volvió semanas después".

    Returns el episodio activo después de la operación.
    """
    episodes: list[dict[str, Any]] = metadata.setdefault("episodes", [])

    if episodes:
        last = episodes[-1]
        if last.get("closed_at_ms") is None:
            # Episodio activo. Chequear si está muy viejo (TIMEOUT).
            started = last.get("started_at_ms")
            if isinstance(started, int) and (now_ms - started) >= EPISODE_TIMEOUT_MS:
                # Cierre lazy por timeout — el cliente abandonó el episodio
                # y volvió mucho después. Marcamos el cierre con el
                # `started_at_ms + EPISODE_TIMEOUT_MS` como `closed_at_ms`
                # (mejor aproximación de cuándo "murió" el episodio que
                # `now_ms`).
                last["closed_at_ms"] = started + EPISODE_TIMEOUT_MS
                last["closing_tag"] = TIMEOUT_CLOSING_TAG
                last["closing_motivo"] = (
                    "cierre automático por inactividad (>14 días)"
                )
                # Fall through: crearemos un nuevo episodio abajo
            else:
                return last  # activo + reciente — no tocar

    is_reengagement = bool(episodes)  # había uno anterior cerrado
    new_ep = _make_empty_episode(
        episode_id=_next_episode_id(episodes),
        now_ms=now_ms,
        inbound_message_id=inbound_message_id,
        referral_snapshot=referral_snapshot,
        msgs_count_at_start=msgs_count_at_start,
    )
    episodes.append(new_ep)

    if is_reengagement:
        # Reset del tag del episodio anterior. metadata.tag siempre describe
        # el episodio activo (no el cierre histórico — eso vive en
        # episodes[*].closing_tag).
        metadata["tag"] = "NO_ETIQUETADO"
        metadata.pop("motivo", None)

    return new_ep


def close_episode(
    metadata: dict[str, Any],
    *,
    closing_tag: str,
    closing_motivo: str | None,
    now_ms: int,
    order_id: str | None = None,
    msgs_count_at_close: int | None = None,
) -> dict[str, Any] | None:
    """Cierra el episodio activo (si lo hay) con metadata del cierre.

    - Si no hay `episodes[]` → crea uno sintético + lo cierra
      (backfill lazy para sesiones legacy que cierran sin haber pasado
      antes por `ensure_active_episode`).
    - Si último episode ya está closed → idempotente: NO toca y devuelve
      None. Útil para que callers que se ejecutan más de una vez (e.g.
      retry de activity) no rompan el state.
    - Si último episode está activo → marca closed_at_ms / closing_tag /
      closing_motivo / order_id (si pasado) y devuelve el episodio cerrado.
    """
    episodes: list[dict[str, Any]] = metadata.setdefault("episodes", [])

    if not episodes:
        # Backfill lazy: sesión legacy sin episodes[]. Creamos uno sintético
        # con started_at_ms = now_ms (no tenemos el inicio real) y lo cerramos.
        ep = _make_empty_episode(
            episode_id=_next_episode_id(episodes),
            now_ms=now_ms,
            inbound_message_id=None,
            referral_snapshot=None,
        )
        episodes.append(ep)
    else:
        last = episodes[-1]
        if last.get("closed_at_ms") is not None:
            # Ya cerrado — idempotente
            return None
        ep = last

    ep["closed_at_ms"] = now_ms
    ep["closing_tag"] = closing_tag
    ep["closing_motivo"] = closing_motivo
    if order_id is not None:
        ep["order_id"] = order_id
    if msgs_count_at_close is not None:
        ep["msgs_count_at_close"] = msgs_count_at_close
    return ep


def attach_order_to_active_episode(
    metadata: dict[str, Any],
    *,
    order_id: str,
    now_ms: int,
    order_total_cop: int | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    """Anota `order_id` (+ total congelado) en el episodio activo. Mutates.

    NO cierra el episodio — el cierre formal viene del
    `manage_conversation_tag(COMPRA_EXITOSA)` que el agente invoca justo
    después según el flow documentado en `workspace/TOOLS.md`.

    `order_total_cop`/`currency` congelan el ingreso de la venta en el
    episodio (major units COP). Es el dato de negocio que consume el
    dashboard ads para atribuir `revenue` por campaña — frozen al cierre,
    igual que `llm_usage`. `None` preserva el valor previo (no lo pisa con
    None si un caller no lo provee).

    Defensivo: si no hay episodio activo, crea uno (preferimos no perder
    la asociación venta↔episodio aunque haya un bug upstream que invoque
    register_order sin haber pasado por el ingest).

    Returns el episodio activo con `order_id` ya seteado.
    """
    ep = get_active_episode(metadata)
    if ep is None:
        ep = ensure_active_episode(metadata, now_ms=now_ms)
    ep["order_id"] = order_id
    if order_total_cop is not None:
        ep["order_total_cop"] = order_total_cop
    if currency is not None:
        ep["order_currency"] = currency
    return ep
