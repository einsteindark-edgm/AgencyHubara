"""Tests del classifier `classify_conversation_state`.

El classifier mapea el estado del backend (tags + registered_order +
timing) al `AdsState` que el frontend espera (`no_reply` | `nuevo` |
`activo` | `calificado` | `cotizado` | `ganado` | `perdido`).

Heurística pura: lee solo `metadata`, `total_msgs` y `last_inbound_ms`.
No parsea el JSONL del history. Determinístico (pasa `now_ms` por DI).
"""
from __future__ import annotations

from src.plugins.ads.classification import (
    classify_state,
)


_NOW_MS = 1_900_000_000_000  # epoch ms determinístico para tests
_ONE_HOUR_MS = 60 * 60 * 1000
_ONE_DAY_MS = 24 * _ONE_HOUR_MS


# --- Casos de alta confianza (señales fuertes) ---------------------------


def test_registered_order_success_is_ganado():
    """Pedido registrado en Medusa con success=True → ganado.
    Señal más fuerte de venta: el order existe en el sistema."""
    metadata = {
        "registered_order": {"success": True, "order_id": "order_01"},
        "tag": "NO_ETIQUETADO",  # incluso si el tag aún no se actualizó
    }
    assert (
        classify_state(metadata, total_msgs=20, last_inbound_ms=_NOW_MS, now_ms=_NOW_MS)
        == "ganado"
    )


def test_registered_order_failed_does_not_count():
    """registered_order con success=False NO cuenta como ganado.
    Cae al fallback por tag (en este caso NO_ETIQUETADO + activo)."""
    metadata = {
        "registered_order": {"success": False, "error_detail": "stock_out"},
        "tag": "NO_ETIQUETADO",
    }
    state = classify_state(
        metadata, total_msgs=10, last_inbound_ms=_NOW_MS, now_ms=_NOW_MS
    )
    assert state != "ganado"


def test_tag_compra_exitosa_is_ganado():
    """tag=COMPRA_EXITOSA → ganado (incluso si el order no se persistió por bug)."""
    metadata = {"tag": "COMPRA_EXITOSA"}
    assert (
        classify_state(metadata, total_msgs=15, last_inbound_ms=_NOW_MS, now_ms=_NOW_MS)
        == "ganado"
    )


def test_tag_rechazo_is_perdido():
    metadata = {"tag": "RECHAZO"}
    assert (
        classify_state(metadata, total_msgs=5, last_inbound_ms=_NOW_MS, now_ms=_NOW_MS)
        == "perdido"
    )


def test_tag_confirmado_sin_datos_is_cotizado():
    """Cliente confirmó pedido pero NO completó shipping (escalado a humano).
    Es un cotizado que faltó cerrar — el frontend lo agrupa con cotizado."""
    metadata = {"tag": "CONFIRMADO_SIN_DATOS"}
    assert (
        classify_state(metadata, total_msgs=10, last_inbound_ms=_NOW_MS, now_ms=_NOW_MS)
        == "cotizado"
    )


def test_tag_confirmado_pago_pendiente_is_ganado():
    """HU "verificación humana de pago": orden registrada en Medusa, falta
    verificar pago. Para el funnel ads cuenta como ganado (el cliente
    completó la intención de compra). Si el humano aborta el pedido luego,
    eso se refleja como cambio de estado en Medusa, no acá."""
    metadata = {"tag": "CONFIRMADO_PAGO_PENDIENTE"}
    assert (
        classify_state(metadata, total_msgs=10, last_inbound_ms=_NOW_MS, now_ms=_NOW_MS)
        == "ganado"
    )


# --- Calificado (interesado pero sin propuesta final) --------------------


def test_tag_interesado_is_calificado():
    """INTERESADO sin order_summary → calificado (interesado, sin propuesta final).
    En PR futuro, si detectamos `present_order_confirmation` en milestones,
    sube a `cotizado`."""
    metadata = {"tag": "INTERESADO"}
    assert (
        classify_state(metadata, total_msgs=10, last_inbound_ms=_NOW_MS, now_ms=_NOW_MS)
        == "calificado"
    )


# --- Activo (en curso) ----------------------------------------------------


def test_tag_humano_is_activo():
    """Escalación a humano = conversación todavía viva, esperando atención."""
    metadata = {"tag": "HUMANO"}
    assert (
        classify_state(metadata, total_msgs=8, last_inbound_ms=_NOW_MS, now_ms=_NOW_MS)
        == "activo"
    )


def test_tag_retoma_venta_is_activo():
    """RETOMA_VENTA: agente retomó después de handoff humano. Conversación viva."""
    metadata = {"tag": "RETOMA_VENTA"}
    assert (
        classify_state(metadata, total_msgs=12, last_inbound_ms=_NOW_MS, now_ms=_NOW_MS)
        == "activo"
    )


# --- Sin tag (heurística por timing/conteo) ------------------------------


def test_no_etiquetado_few_messages_is_nuevo():
    """NO_ETIQUETADO con <=2 mensajes → nuevo (recién entró)."""
    metadata = {"tag": "NO_ETIQUETADO"}
    assert (
        classify_state(metadata, total_msgs=1, last_inbound_ms=_NOW_MS, now_ms=_NOW_MS)
        == "nuevo"
    )
    assert (
        classify_state(metadata, total_msgs=2, last_inbound_ms=_NOW_MS, now_ms=_NOW_MS)
        == "nuevo"
    )


def test_no_etiquetado_old_last_reply_is_no_reply():
    """NO_ETIQUETADO con >2 msgs Y último inbound del cliente >24h ago →
    no_reply. El cliente quedó frío después de iniciar conversación."""
    metadata = {"tag": "NO_ETIQUETADO"}
    old_ms = _NOW_MS - (25 * _ONE_HOUR_MS)
    assert (
        classify_state(metadata, total_msgs=8, last_inbound_ms=old_ms, now_ms=_NOW_MS)
        == "no_reply"
    )


def test_no_etiquetado_recent_and_engaged_is_activo():
    """NO_ETIQUETADO + >2 msgs + actividad reciente → activo."""
    metadata = {"tag": "NO_ETIQUETADO"}
    recent_ms = _NOW_MS - (10 * 60 * 1000)  # hace 10 min
    assert (
        classify_state(
            metadata, total_msgs=8, last_inbound_ms=recent_ms, now_ms=_NOW_MS
        )
        == "activo"
    )


def test_no_etiquetado_threshold_boundary_at_24h():
    """Boundary check: exactamente 24h → no_reply (umbral cumplido)."""
    metadata = {"tag": "NO_ETIQUETADO"}
    boundary_ms = _NOW_MS - _ONE_DAY_MS
    assert (
        classify_state(
            metadata, total_msgs=8, last_inbound_ms=boundary_ms, now_ms=_NOW_MS
        )
        == "no_reply"
    )


# --- Defaults defensivos --------------------------------------------------


def test_empty_metadata_is_nuevo():
    """metadata vacío + 0 msgs → nuevo (default seguro: arrancó pero nada pasó)."""
    assert (
        classify_state({}, total_msgs=0, last_inbound_ms=None, now_ms=_NOW_MS) == "nuevo"
    )


def test_no_last_inbound_falls_back_to_nuevo():
    """Sin last_inbound_ms pero tag NO_ETIQUETADO + pocos msgs → nuevo.
    El classifier no debe romper si falta el timestamp."""
    metadata = {"tag": "NO_ETIQUETADO"}
    assert (
        classify_state(metadata, total_msgs=1, last_inbound_ms=None, now_ms=_NOW_MS)
        == "nuevo"
    )


def test_unknown_tag_falls_through_to_heuristic():
    """Tag desconocido (e.g. legacy o futuro) cae a la heurística por timing/msgs."""
    metadata = {"tag": "TAG_DESCONOCIDO_LEGACY"}
    # Con pocos msgs → nuevo (default seguro)
    assert (
        classify_state(metadata, total_msgs=1, last_inbound_ms=_NOW_MS, now_ms=_NOW_MS)
        == "nuevo"
    )


# --- Prioridad de señales -------------------------------------------------


def test_registered_order_beats_tag_rechazo():
    """Si hay un order success=True PERO también un tag=RECHAZO (bug:
    se etiquetó como rechazo después de cerrar), el order gana. La venta
    real en Medusa es la verdad."""
    metadata = {
        "registered_order": {"success": True, "order_id": "order_01"},
        "tag": "RECHAZO",
    }
    assert (
        classify_state(metadata, total_msgs=20, last_inbound_ms=_NOW_MS, now_ms=_NOW_MS)
        == "ganado"
    )
