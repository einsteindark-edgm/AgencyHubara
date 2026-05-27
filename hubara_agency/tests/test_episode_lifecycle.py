"""Tests del episode lifecycle.

Un episodio es un tramo de conversación con una intención propia entre el
cliente y el agente. Cada `wa_<phone>` puede tener N episodios a lo largo
del tiempo:

  ep_001: cliente vino desde ad → cotizó → compró → COMPRA_EXITOSA (closed)
  ep_002: cliente volvió 2 meses después → preguntó precios → quedó frío (activo)

El lifecycle se modela en `metadata["episodes"]: list[dict]`. Las funciones
puras de este módulo mutan ese campo sin tocar filesystem ni Temporal.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    CLOSING_TAGS,
    EPISODE_TIMEOUT_MS,
    TIMEOUT_CLOSING_TAG,
    attach_order_to_active_episode,
    close_episode,
    ensure_active_episode,
    get_active_episode,
)


_NOW_MS = 1_900_000_000_000
_LATER_MS = _NOW_MS + 60 * 60 * 1000  # +1 hora
_TWO_MONTHS_LATER_MS = _NOW_MS + 60 * 24 * 60 * 60 * 1000
# Justo por encima del threshold (14 días). El timeout dispara con >=.
_AFTER_TIMEOUT_MS = _NOW_MS + EPISODE_TIMEOUT_MS + 1_000


def test_timeout_closes_stale_active_episode_and_opens_new_one():
    """Episodio activo muy viejo (>14 días) se cierra automáticamente con
    `closing_tag=TIMEOUT` cuando llega un inbound nuevo. Se abre uno
    nuevo en re-engagement."""
    metadata: dict = {}
    ensure_active_episode(metadata, now_ms=_NOW_MS, inbound_message_id="wamid.A")
    # Cliente desaparece. Vuelve 15 días después.
    new_ep = ensure_active_episode(
        metadata,
        now_ms=_AFTER_TIMEOUT_MS,
        inbound_message_id="wamid.NEW",
    )
    assert len(metadata["episodes"]) == 2
    # El primer episodio quedó cerrado por TIMEOUT
    old = metadata["episodes"][0]
    assert old["closed_at_ms"] is not None
    assert old["closing_tag"] == TIMEOUT_CLOSING_TAG
    assert "inactividad" in old["closing_motivo"].lower()
    # closed_at_ms se calcula como started_at_ms + EPISODE_TIMEOUT_MS
    # (mejor proxy que now_ms para "cuándo realmente murió").
    assert old["closed_at_ms"] == _NOW_MS + EPISODE_TIMEOUT_MS
    # El nuevo arranca limpio
    assert new_ep["closed_at_ms"] is None
    assert new_ep["episode_id"] == "ep_002"
    # Reset del tag global (es re-engagement)
    assert metadata["tag"] == "NO_ETIQUETADO"


def test_timeout_does_not_trigger_when_episode_is_recent():
    """Episodio activo reciente (<14 días) NO se cierra por timeout."""
    metadata: dict = {}
    first = ensure_active_episode(
        metadata, now_ms=_NOW_MS, inbound_message_id="wamid.A"
    )
    # Cliente responde a las 13 días — sigue dentro del threshold
    just_below_ms = _NOW_MS + EPISODE_TIMEOUT_MS - 1_000
    second = ensure_active_episode(
        metadata, now_ms=just_below_ms, inbound_message_id="wamid.B"
    )
    assert first is second
    assert len(metadata["episodes"]) == 1
    assert metadata["episodes"][0]["closed_at_ms"] is None


def test_ensure_persists_msgs_count_at_start():
    """FU3: el caller puede pasar msgs_count_at_start; el episodio lo persiste
    para que el listing calcule msgs_in_episode exacto downstream."""
    metadata: dict = {}
    ep = ensure_active_episode(
        metadata,
        now_ms=_NOW_MS,
        inbound_message_id="wamid.A",
        msgs_count_at_start=42,
    )
    assert ep["msgs_count_at_start"] == 42
    assert ep["msgs_count_at_close"] is None


def test_close_persists_msgs_count_at_close():
    """FU3: close_episode snapshotea el count al cerrar."""
    metadata: dict = {}
    ensure_active_episode(
        metadata,
        now_ms=_NOW_MS,
        inbound_message_id="wamid.A",
        msgs_count_at_start=10,
    )
    closed = close_episode(
        metadata,
        closing_tag="COMPRA_EXITOSA",
        closing_motivo="venta",
        now_ms=_LATER_MS,
        msgs_count_at_close=18,
    )
    assert closed is not None
    assert closed["msgs_count_at_start"] == 10
    assert closed["msgs_count_at_close"] == 18


def test_msgs_count_snapshots_default_to_none():
    """Backward-compat: si no se pasan los counts, queda None."""
    metadata: dict = {}
    ep = ensure_active_episode(metadata, now_ms=_NOW_MS, inbound_message_id="x")
    assert ep["msgs_count_at_start"] is None
    closed = close_episode(
        metadata, closing_tag="RECHAZO", closing_motivo="x", now_ms=_LATER_MS
    )
    assert closed is not None
    assert closed["msgs_count_at_close"] is None


def test_timeout_does_not_trigger_when_episode_already_closed():
    """Si el último episodio ya tiene closing_tag explícito (e.g.
    RECHAZO), no se le pisa con TIMEOUT — el closing del agente gana."""
    metadata: dict = {}
    ensure_active_episode(metadata, now_ms=_NOW_MS, inbound_message_id="wamid.A")
    close_episode(
        metadata, closing_tag="RECHAZO", closing_motivo="no quiso", now_ms=_LATER_MS
    )
    # Cliente vuelve 15 días después
    ensure_active_episode(
        metadata, now_ms=_AFTER_TIMEOUT_MS, inbound_message_id="wamid.NEW"
    )
    # El primer episodio conserva el closing_tag RECHAZO (no se pisó)
    assert metadata["episodes"][0]["closing_tag"] == "RECHAZO"
    # Nuevo episodio creado normal
    assert metadata["episodes"][1]["episode_id"] == "ep_002"


# --- ensure_active_episode -------------------------------------------------


def test_ensure_creates_first_episode_when_empty():
    """Sin `episodes[]` → crea ep_001 con started_at_ms = now_ms."""
    metadata: dict = {}
    ep = ensure_active_episode(
        metadata, now_ms=_NOW_MS, inbound_message_id="wamid.A"
    )
    assert metadata["episodes"][0] is ep
    assert ep["episode_id"] == "ep_001"
    assert ep["started_at_ms"] == _NOW_MS
    assert ep["started_inbound_message_id"] == "wamid.A"
    assert ep["closed_at_ms"] is None
    assert ep["closing_tag"] is None
    assert ep["order_id"] is None


def test_ensure_does_not_touch_existing_active_episode():
    """Si último episodio está activo → no se crea otro."""
    metadata: dict = {}
    first = ensure_active_episode(
        metadata, now_ms=_NOW_MS, inbound_message_id="wamid.A"
    )
    second = ensure_active_episode(
        metadata, now_ms=_LATER_MS, inbound_message_id="wamid.B"
    )
    assert first is second
    assert len(metadata["episodes"]) == 1
    # started_at_ms NO cambió — es el primer inbound del episodio
    assert metadata["episodes"][0]["started_at_ms"] == _NOW_MS


def test_ensure_creates_new_episode_when_last_closed():
    """Si el último episodio tiene closed_at_ms → crea ep_NNN+1 nuevo."""
    metadata: dict = {}
    ensure_active_episode(metadata, now_ms=_NOW_MS, inbound_message_id="wamid.A")
    close_episode(
        metadata,
        closing_tag="COMPRA_EXITOSA",
        closing_motivo="venta cerrada",
        now_ms=_LATER_MS,
    )
    # Cliente vuelve después
    new = ensure_active_episode(
        metadata, now_ms=_TWO_MONTHS_LATER_MS, inbound_message_id="wamid.NEW"
    )
    assert len(metadata["episodes"]) == 2
    assert new["episode_id"] == "ep_002"
    assert new["started_at_ms"] == _TWO_MONTHS_LATER_MS
    assert new["closed_at_ms"] is None
    # El anterior sigue cerrado
    assert metadata["episodes"][0]["closing_tag"] == "COMPRA_EXITOSA"


def test_ensure_captures_referral_snapshot_at_episode_start():
    """Si el inbound tiene referral, se snapshotea EN el episodio.
    El `origin` del metadata raíz sigue siendo first-touch sticky."""
    metadata: dict = {}
    referral = {
        "ctwa_clid": "CLID_X",
        "source_id": "AD_001",
        "headline": "Velas",
        "channel": "ad",
    }
    ep = ensure_active_episode(
        metadata,
        now_ms=_NOW_MS,
        inbound_message_id="wamid.A",
        referral_snapshot=referral,
    )
    assert ep["referral_snapshot"] == referral


def test_ensure_resets_metadata_tag_on_reengagement():
    """Cuando se abre un episodio nuevo después de uno cerrado, `metadata.tag`
    se resetea a NO_ETIQUETADO. Sino el classifier del nuevo episodio
    activo vería el COMPRA_EXITOSA del cierre anterior."""
    metadata: dict = {}
    ensure_active_episode(metadata, now_ms=_NOW_MS, inbound_message_id="wamid.A")
    metadata["tag"] = "COMPRA_EXITOSA"  # el agente lo seteó al cerrar
    metadata["motivo"] = "venta cerrada"
    close_episode(
        metadata,
        closing_tag="COMPRA_EXITOSA",
        closing_motivo="venta cerrada",
        now_ms=_LATER_MS,
    )
    # Cliente vuelve después
    ensure_active_episode(
        metadata, now_ms=_TWO_MONTHS_LATER_MS, inbound_message_id="wamid.NEW"
    )
    # Tag global se resetea al nuevo episodio
    assert metadata["tag"] == "NO_ETIQUETADO"
    assert "motivo" not in metadata
    # Pero el episodio cerrado preserva su closing_tag
    assert metadata["episodes"][0]["closing_tag"] == "COMPRA_EXITOSA"


def test_ensure_does_not_reset_tag_on_first_episode():
    """En el primer episodio (no hay anterior cerrado), no se toca tag —
    el agente puede setearlo durante el episodio normalmente."""
    metadata: dict = {"tag": "SOME_PREEXISTING_TAG"}
    ensure_active_episode(metadata, now_ms=_NOW_MS, inbound_message_id="wamid.A")
    # No es re-engagement (no había episodes previos), tag NO se toca
    assert metadata["tag"] == "SOME_PREEXISTING_TAG"


def test_ensure_subsequent_episode_can_have_own_referral():
    """ep_002 puede capturar un referral distinto al de ep_001 (cliente
    volvió desde otro ad)."""
    metadata: dict = {}
    ensure_active_episode(
        metadata,
        now_ms=_NOW_MS,
        inbound_message_id="wamid.A",
        referral_snapshot={"source_id": "AD_001"},
    )
    close_episode(
        metadata,
        closing_tag="COMPRA_EXITOSA",
        closing_motivo="",
        now_ms=_LATER_MS,
    )
    ensure_active_episode(
        metadata,
        now_ms=_TWO_MONTHS_LATER_MS,
        inbound_message_id="wamid.B",
        referral_snapshot={"source_id": "AD_002"},
    )
    assert metadata["episodes"][0]["referral_snapshot"]["source_id"] == "AD_001"
    assert metadata["episodes"][1]["referral_snapshot"]["source_id"] == "AD_002"


# --- close_episode --------------------------------------------------------


def test_close_marks_active_episode_with_closing_data():
    metadata: dict = {}
    ensure_active_episode(metadata, now_ms=_NOW_MS, inbound_message_id="wamid.A")
    closed = close_episode(
        metadata,
        closing_tag="COMPRA_EXITOSA",
        closing_motivo="venta cerrada",
        now_ms=_LATER_MS,
        order_id="order_01",
    )
    assert closed is not None
    assert closed["closed_at_ms"] == _LATER_MS
    assert closed["closing_tag"] == "COMPRA_EXITOSA"
    assert closed["closing_motivo"] == "venta cerrada"
    assert closed["order_id"] == "order_01"


def test_close_is_idempotent_when_no_active_episode():
    """Si el último ya está closed, close_episode no truena ni rompe state.
    Devuelve None para señalar idempotencia."""
    metadata: dict = {}
    ensure_active_episode(metadata, now_ms=_NOW_MS, inbound_message_id="wamid.A")
    close_episode(
        metadata, closing_tag="RECHAZO", closing_motivo="x", now_ms=_LATER_MS
    )
    # Segundo cierre — no debe crashear
    result = close_episode(
        metadata,
        closing_tag="COMPRA_EXITOSA",
        closing_motivo="duplicado",
        now_ms=_LATER_MS + 1000,
    )
    assert result is None
    # El primer cierre sigue siendo el válido
    assert metadata["episodes"][0]["closing_tag"] == "RECHAZO"


def test_close_creates_synthetic_episode_for_legacy_session():
    """Backfill lazy: sesión sin episodes[] que recibe un close_episode
    crea uno sintético + lo cierra. Útil cuando el agente etiqueta sesiones
    legacy que arrancaron antes de este feature."""
    metadata: dict = {}  # sin episodes[]
    closed = close_episode(
        metadata,
        closing_tag="COMPRA_EXITOSA",
        closing_motivo="venta legacy",
        now_ms=_NOW_MS,
    )
    assert closed is not None
    assert len(metadata["episodes"]) == 1
    assert metadata["episodes"][0]["closing_tag"] == "COMPRA_EXITOSA"
    # started_at_ms cae a now_ms porque no tenemos histórico real
    assert metadata["episodes"][0]["started_at_ms"] == _NOW_MS


# --- attach_order_to_active_episode ---------------------------------------


def test_attach_order_sets_order_id_on_active_episode():
    """Cuando RegisterOrderTool registra una venta exitosa, anotamos el
    order_id en el episodio activo. NO cerramos el episodio — eso lo hace
    el manage_conversation_tag(COMPRA_EXITOSA) que viene después."""
    metadata: dict = {}
    ensure_active_episode(metadata, now_ms=_NOW_MS, inbound_message_id="wamid.A")
    ep = attach_order_to_active_episode(
        metadata, order_id="order_01", now_ms=_LATER_MS
    )
    assert ep is not None
    assert ep["order_id"] == "order_01"
    # El episodio sigue activo
    assert ep["closed_at_ms"] is None


def test_attach_order_creates_episode_if_none_active():
    """Defensivo: si por alguna razón no hay episodio activo (e.g. el agente
    invocó register_order sin que ingest haya creado uno), attach crea uno.
    Aceptamos esta tolerancia para no perder la venta."""
    metadata: dict = {}
    ep = attach_order_to_active_episode(
        metadata, order_id="order_01", now_ms=_NOW_MS
    )
    assert ep is not None
    assert len(metadata["episodes"]) == 1
    assert ep["order_id"] == "order_01"


# --- get_active_episode ---------------------------------------------------


def test_get_active_returns_last_when_open():
    metadata: dict = {}
    ensure_active_episode(metadata, now_ms=_NOW_MS, inbound_message_id="wamid.A")
    ep = get_active_episode(metadata)
    assert ep is not None
    assert ep["closed_at_ms"] is None


def test_get_active_returns_none_when_all_closed():
    metadata: dict = {}
    ensure_active_episode(metadata, now_ms=_NOW_MS, inbound_message_id="wamid.A")
    close_episode(
        metadata, closing_tag="RECHAZO", closing_motivo="x", now_ms=_LATER_MS
    )
    assert get_active_episode(metadata) is None


def test_get_active_returns_none_when_empty():
    assert get_active_episode({}) is None


# --- constantes ------------------------------------------------------------


def test_closing_tags_are_canonical_set():
    """Los tags que cierran episodio coinciden con los del agente.

    HU "verificación humana de pago": `CONFIRMADO_PAGO_PENDIENTE` se
    agregó al set canónico (orden registrada en Medusa, falta solo el
    paso operativo humano de verificar el pago). Cierra el episodio
    porque la intención del cliente terminó desde su lado.
    """
    assert CLOSING_TAGS == frozenset(
        {
            "COMPRA_EXITOSA",
            "RECHAZO",
            "CONFIRMADO_SIN_DATOS",
            "CONFIRMADO_PAGO_PENDIENTE",
        }
    )
