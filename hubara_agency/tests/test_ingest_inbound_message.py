"""Tests del use case `IngestInboundMessage` con fakes (no mocks).

Verifican:
* mensajes de texto se persisten en history y delegan al `LoadOrStartSalesSession`.
* mensajes media-only surface al LLM como marker (HU-002): "[el cliente envió un image]"
  para que el agente reaccione, en vez del silencio legacy.
* mensajes audio NO encolan workflow (queda pending_transcription en metadata).
* el `session_id` se construye con prefijo `wa_` desde `from_number`.
* referral CTWA se persiste en state.json y emite analytics event.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage


# --- Fakes -----------------------------------------------------------------


class FakeHistoryStore:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def append_user_event(
        self, session_id: str, content: str, *, image_url: str | None = None
    ) -> None:
        self.events.append((session_id, content))


@dataclass
class _Call:
    session_id: str
    message: str
    phone_number_id: str | None
    extra_context: list[str] | None = None


class FakeLoadOrStart:
    def __init__(self) -> None:
        self.calls: list[_Call] = []

    async def execute(
        self,
        session_id: str,
        message: str,
        phone_number_id: str | None,
        extra_context: list[str] | None = None,
    ) -> None:
        self.calls.append(
            _Call(session_id, message, phone_number_id, extra_context)
        )


class FakeMetadataStore:
    """Fake in-memory para `last_inbound_message_id` (Fix 5 typing indicator)."""

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}
        self.writes: list[tuple[str, dict]] = []

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)
        self.writes.append((session_id, dict(data)))


def _make_text_message(
    *, from_number: str = "5491111111111", text: str | None = "hola", phone_id: str = "PID"
) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.X",
        from_number=from_number,
        phone_number_id=phone_id,
        text=text,
        media=None,
        timestamp="1714312345",
    )


def _make_media_message() -> WhatsAppMessage:
    # Video (no "image"): sigue surfaceando como marker. Las IMÁGENES ahora
    # van por el pipeline de visión (ver test_ingest_image_vision.py).
    return WhatsAppMessage(
        message_id="wamid.VID",
        from_number="5491111111111",
        phone_number_id="PID",
        text=None,
        media={"type": "video", "id": "vid1"},
        timestamp="1714312345",
    )


# --- Tests -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_media_inbound_surfaces_as_marker_text_to_llm():
    """HU-002: media no-imagen (video/document/sticker) sin caption ya no se
    descarta — surface al LLM como marker "[el cliente envió un X]" para que
    pueda reaccionar en vez de quedarse mudo. Las IMÁGENES ahora se describen
    con visión (ver test_ingest_image_vision.py)."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
    )

    await use_case.execute(_make_media_message())

    assert history.events == [("wa_5491111111111", "[el cliente envió un video]")]
    assert len(loader.calls) == 1
    assert loader.calls[0].message == "[el cliente envió un video]"


@pytest.mark.asyncio
async def test_reengagement_injects_episode_boundary_note():
    """Bug run 3b3fbaee: el cliente vuelve tras un episodio CERRADO. El ingest
    debe inyectar una nota de frontera en `extra_context` para que el agente
    arranque una conversación nueva en vez de re-surfacear (y re-taguear) el
    pedido del episodio anterior — cuyo memory_window el LLM todavía arrastra."""
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    metadata.store["wa_5491111111111"] = {
        "active_route": "ventas",
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": 1,
                "closed_at_ms": 1000,
                "closing_tag": "CONFIRMADO_PAGO_PENDIENTE",
                "order_id": "order_ABC",
            }
        ],
    }
    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message(text="Hola"))

    assert len(loader.calls) == 1
    ctx = loader.calls[0].extra_context
    assert ctx is not None and len(ctx) == 1
    note = ctx[0]
    assert "episodio NUEVO" in note
    assert "order_ABC" in note  # referencia el pedido cerrado
    assert "NO lo retomes" in note


@pytest.mark.asyncio
async def test_fresh_session_has_no_episode_boundary_note():
    """Sesión nueva (sin episodios previos) → sin nota de frontera."""
    loader = FakeLoadOrStart()
    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message(text="Hola"))

    assert len(loader.calls) == 1
    assert loader.calls[0].extra_context is None


@pytest.mark.asyncio
async def test_active_episode_continuation_has_no_boundary_note():
    """Episodio activo (closed_at_ms=None) → es continuación, NO re-engagement:
    no se inyecta nota de frontera. La detección corre ANTES de
    `ensure_active_episode`, así que un episodio aún-abierto no dispara."""
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    metadata.store["wa_5491111111111"] = {
        "active_route": "ventas",
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": 1,
                "closed_at_ms": None,
                "closing_tag": None,
            }
        ],
    }
    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message(text="seguimos"))

    assert len(loader.calls) == 1
    assert loader.calls[0].extra_context is None


@pytest.mark.asyncio
async def test_active_episode_with_draft_injects_breadcrumb():
    """El order_draft del episodio activo se proyecta como breadcrumb en
    `extra_context` para que el LLM no re-pregunte datos ya confirmados
    (color, aroma, ...). Es el wiring del determinismo de slots."""
    import time

    recent_ms = int(time.time() * 1000) - 60_000  # 1 min atras: no timeout
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    metadata.store["wa_5491111111111"] = {
        "active_route": "ventas",
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": recent_ms,
                "closed_at_ms": None,
                "closing_tag": None,
                "order_id": None,
                "order_draft": {
                    "slots": {"color": "Blanco", "aroma": "Lavanda"}
                },
            }
        ],
    }
    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message(text="seguimos"))

    assert len(loader.calls) == 1
    ctx = loader.calls[0].extra_context
    assert ctx is not None
    breadcrumb = "\n".join(ctx)
    assert "DATOS DEL PEDIDO YA CONFIRMADOS" in breadcrumb
    assert "Color: Blanco" in breadcrumb
    assert "Aroma: Lavanda" in breadcrumb


@pytest.mark.asyncio
async def test_registered_order_episode_has_no_breadcrumb():
    """Post-register_order (episodio con order_id): el draft deja de
    proyectarse — la orden es la fuente de verdad, no el breadcrumb."""
    import time

    recent_ms = int(time.time() * 1000) - 60_000
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    metadata.store["wa_5491111111111"] = {
        "active_route": "ventas",
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": recent_ms,
                "closed_at_ms": None,
                "closing_tag": None,
                "order_id": "order_XYZ",
                "order_draft": {"slots": {"color": "Blanco"}},
            }
        ],
    }
    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message(text="ok"))

    assert len(loader.calls) == 1
    breadcrumb = "\n".join(loader.calls[0].extra_context or [])
    assert "DATOS DEL PEDIDO" not in breadcrumb


@pytest.mark.asyncio
async def test_human_route_does_not_rotate_episode_or_reset_tag():
    """Bug wa_573125671604: con la conversación ya en manos de un humano
    (active_route=humano), un inbound nuevo NO debe abrir un episodio ni
    resetear el tag a NO_ETIQUETADO. Eso borraría el tag=HUMANO que dejó
    escalate_to_human y sacaría el chat de la bandeja humana del dashboard
    (que filtra por tag=HUMANO): el bot no responde (route=humano) y el humano
    tampoco lo ve → chat huérfano."""
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    metadata.store["wa_5491111111111"] = {
        "active_route": "humano",
        "tag": "HUMANO",
        "motivo": "Pedido order_X registrado en Medusa, verificar pago",
        "escalation_reason": "PAYMENT_VERIFICATION_PENDING",
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": 1,
                "closed_at_ms": 1000,
                "closing_tag": "CONFIRMADO_PAGO_PENDIENTE",
                "order_id": "order_X",
            }
        ],
    }
    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message(text="ya pagué, te mando el comprobante"))

    saved = metadata.store["wa_5491111111111"]
    # El tag NO se reseteó: sigue HUMANO (visible en la bandeja humana).
    assert saved["tag"] == "HUMANO"
    # NO se abrió un episodio nuevo: sigue el único, cerrado.
    assert len(saved["episodes"]) == 1
    assert saved["episodes"][-1]["closed_at_ms"] is not None
    # El motivo de la escalación se preserva (ensure_active_episode lo borraría).
    assert saved["motivo"] == "Pedido order_X registrado en Medusa, verificar pago"
    # Sigue en ruta humana.
    assert saved["active_route"] == "humano"


@pytest.mark.asyncio
async def test_audio_inbound_defers_to_transcription():
    """HU-002 / A.5: audio inbound NO encola workflow — queda
    pending_transcription en metadata. La activity de transcripción
    procesa después y re-inyecta el texto."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    audio_msg = WhatsAppMessage(
        message_id="wamid.AUDIO",
        from_number="5491111111111",
        phone_number_id="PID",
        text=None,
        media=None,
        timestamp="1714312400",
        msg_type="audio",
        audio={"id": "media_xyz", "mime_type": "audio/ogg", "voice": True},
    )
    await use_case.execute(audio_msg)

    assert history.events == []  # no es texto LLM-ready aún
    assert loader.calls == []  # no se signaleó el workflow
    assert metadata.store["wa_5491111111111"]["pending_transcription"]["media_id"] == "media_xyz"


@pytest.mark.asyncio
async def test_referral_captured_persists_to_state_and_injects_banner():
    """HU-002 / A.0.3: cuando el cliente llega vía CTWA, el referral se
    persiste íntegro en state y el primer texto del LLM incluye un banner
    con el origen del ad."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    msg = WhatsAppMessage(
        message_id="wamid.CTWA",
        from_number="5491111111111",
        phone_number_id="PID",
        text="Hola, vi el anuncio",
        media=None,
        timestamp="1714312400",
        msg_type="text",
        referral={
            "ctwa_clid": "CLID_ABC",
            "source_type": "ad",
            "source_id": "AD_123",
            "headline": "Velas Hubara",
            "body": "Velas artesanales",
        },
    )
    await use_case.execute(msg)

    state = metadata.store["wa_5491111111111"]
    assert state["ctwa_clids_seen"] == ["CLID_ABC"]
    assert state["ctwa_referrals"][0]["source_id"] == "AD_123"
    # El history debe incluir el banner antepuesto al texto
    persisted_msg = history.events[0][1]
    assert "anuncio" in persisted_msg.lower() or "ad" in persisted_msg.lower()
    assert "Hola, vi el anuncio" in persisted_msg


@pytest.mark.asyncio
async def test_persists_user_event_and_delegates_to_load_session():
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message(text="hola mundo"))

    assert history.events == [("wa_5491111111111", "hola mundo")]
    assert len(loader.calls) == 1
    call = loader.calls[0]
    assert call.session_id == "wa_5491111111111"
    assert call.message == "hola mundo"
    assert call.phone_number_id == "PID"


@pytest.mark.asyncio
async def test_history_is_appended_before_routing():
    """El history del usuario se persiste antes de tocar Temporal (orden invariante)."""

    order: list[str] = []

    class TrackingHistory:
        def append_user_event(
            self, session_id: str, content: str, *, image_url: str | None = None
        ) -> None:
            order.append("history")

    class TrackingLoader:
        async def execute(
            self, session_id, message, phone_number_id, extra_context=None
        ) -> None:
            order.append("load_session")

    use_case = IngestInboundMessage(
        history_store=TrackingHistory(),  # type: ignore[arg-type]
        load_session=TrackingLoader(),  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message())

    assert order == ["history", "load_session"]


@pytest.mark.asyncio
async def test_persists_inbound_message_id_for_typing_indicator():
    """Fix 5: message_id se escribe a metadata para que el typing indicator
    pueda referenciarlo al inicio de cada turno."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message(text="hola"))

    assert metadata.store["wa_5491111111111"]["last_inbound_message_id"] == "wamid.X"


@pytest.mark.asyncio
async def test_metadata_write_failure_does_not_break_flow():
    """El write de metadata es best-effort: si falla, el flujo igual signaleael workflow."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()

    class BrokenMetadata:
        def read(self, session_id):
            raise OSError("disk full")

        def write(self, session_id, data):
            raise OSError("disk full")

    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=BrokenMetadata(),  # type: ignore[arg-type]
    )

    # No debe raisear
    await use_case.execute(_make_text_message(text="hola"))

    # El flujo principal (history + signal) sigue funcionando
    assert history.events == [("wa_5491111111111", "hola")]
    assert len(loader.calls) == 1


# ---------------------------------------------------------------------------
# Origin classification (HU follow-up de CTWA referrals)
#
# Cada sesión WhatsApp se clasifica en uno de 4 buckets para reporting:
#
#   - "ad"           — referral con ctwa_clid + source_type=ad (CTWA atribuible)
#   - "post"         — referral con ctwa_clid + source_type=post (CTWA atribuible)
#   - "web_referral" — referral SIN ctwa_clid (WhatsApp Web / vieja versión).
#                      Trae headline/source_id pero no es atribuible vía
#                      Conversions API.
#   - "direct"       — sin referral en el payload (cliente escribió al número
#                      directo, QR, link, etc.).
#
# Contrato del state:
#   metadata["origin"]     — sticky first-touch (nunca se sobreescribe)
#   metadata["last_touch"] — actualizado en CADA inbound (incluye direct)
#
# `ctwa_referrals` y `ctwa_clids_seen` siguen siendo Meta-attributable touches
# (sin cambios). No se mezclan con web_referral/direct para no romper el feed
# de Conversions API.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_origin_direct_when_no_referral():
    """Cliente que escribe sin venir de campaña → origin.channel = 'direct'."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message(text="Hola, vi su número"))

    state = metadata.store["wa_5491111111111"]
    assert state["origin"]["channel"] == "direct"
    assert state["origin"]["first_inbound_message_id"] == "wamid.X"
    assert isinstance(state["origin"]["first_seen_ms"], int)
    # last_touch también queda direct
    assert state["last_touch"]["channel"] == "direct"
    assert state["last_touch"]["inbound_message_id"] == "wamid.X"
    # NO se contamina ctwa_referrals (sigue siendo solo Meta-attributable)
    assert state.get("ctwa_referrals", []) == []
    assert state.get("ctwa_clids_seen", []) == []


@pytest.mark.asyncio
async def test_origin_web_referral_when_referral_without_clid():
    """Referral con source_id pero SIN ctwa_clid (WhatsApp Web) →
    origin.channel = 'web_referral'. Persistimos los campos útiles del ad
    aunque no podamos enviarlo a Conversions API."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    msg = WhatsAppMessage(
        message_id="wamid.WEB",
        from_number="5491111111111",
        phone_number_id="PID",
        text="Hola, vi su anuncio en Facebook",
        media=None,
        timestamp="1714312400",
        msg_type="text",
        referral={
            # SIN ctwa_clid — caso WhatsApp Web
            "source_type": "ad",
            "source_id": "AD_999",
            "headline": "Velas Hubara",
            "body": "Velas artesanales",
        },
    )
    await use_case.execute(msg)

    state = metadata.store["wa_5491111111111"]
    assert state["origin"]["channel"] == "web_referral"
    assert state["origin"]["source_id"] == "AD_999"
    assert state["origin"]["headline"] == "Velas Hubara"
    assert state["origin"]["first_inbound_message_id"] == "wamid.WEB"
    # last_touch refleja lo mismo
    assert state["last_touch"]["channel"] == "web_referral"
    assert state["last_touch"]["source_id"] == "AD_999"
    # Conversions API queda OUT: sin clid → ctwa_referrals vacío
    assert state.get("ctwa_referrals", []) == []
    assert state.get("ctwa_clids_seen", []) == []


@pytest.mark.asyncio
async def test_origin_sticky_first_touch_never_changes():
    """Cliente arranca 'direct' y después llega desde un ad → origin sigue
    'direct' (sticky first-touch), pero last_touch refleja el ad."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    # 1. Mensaje directo
    await use_case.execute(_make_text_message(text="Hola"))
    assert metadata.store["wa_5491111111111"]["origin"]["channel"] == "direct"

    # 2. Mismo cliente, ahora llega vía ad
    ad_msg = WhatsAppMessage(
        message_id="wamid.AD",
        from_number="5491111111111",
        phone_number_id="PID",
        text="Vi el anuncio",
        media=None,
        timestamp="1714312500",
        msg_type="text",
        referral={
            "ctwa_clid": "CLID_X",
            "source_type": "ad",
            "source_id": "AD_111",
            "headline": "Anuncio",
        },
    )
    await use_case.execute(ad_msg)

    state = metadata.store["wa_5491111111111"]
    # origin se mantiene direct (sticky)
    assert state["origin"]["channel"] == "direct"
    assert state["origin"]["first_inbound_message_id"] == "wamid.X"
    # last_touch refleja el último (ad)
    assert state["last_touch"]["channel"] == "ad"
    assert state["last_touch"]["ctwa_clid"] == "CLID_X"
    assert state["last_touch"]["inbound_message_id"] == "wamid.AD"
    # ctwa_referrals sí se llena (hay clid)
    assert state["ctwa_clids_seen"] == ["CLID_X"]
    assert len(state["ctwa_referrals"]) == 1


@pytest.mark.asyncio
async def test_last_touch_updates_on_every_inbound_with_referral():
    """Multi-touch: el cliente entra por ad A, después por ad B.
    last_touch se actualiza al último, origin queda en A."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    # Primer touch — ad A
    await use_case.execute(
        WhatsAppMessage(
            message_id="wamid.A",
            from_number="5491111111111",
            phone_number_id="PID",
            text="primer ad",
            media=None,
            timestamp="1714312400",
            msg_type="text",
            referral={
                "ctwa_clid": "CLID_A",
                "source_type": "ad",
                "source_id": "AD_A",
                "headline": "Anuncio A",
            },
        )
    )
    # Segundo touch — post B (clid distinto)
    await use_case.execute(
        WhatsAppMessage(
            message_id="wamid.B",
            from_number="5491111111111",
            phone_number_id="PID",
            text="segundo post",
            media=None,
            timestamp="1714312500",
            msg_type="text",
            referral={
                "ctwa_clid": "CLID_B",
                "source_type": "post",
                "source_id": "POST_B",
                "headline": "Post B",
            },
        )
    )

    state = metadata.store["wa_5491111111111"]
    # origin se queda en el primero (ad)
    assert state["origin"]["channel"] == "ad"
    assert state["origin"]["source_id"] == "AD_A"
    assert state["origin"]["first_inbound_message_id"] == "wamid.A"
    # last_touch refleja el último (post)
    assert state["last_touch"]["channel"] == "post"
    assert state["last_touch"]["ctwa_clid"] == "CLID_B"
    assert state["last_touch"]["source_id"] == "POST_B"
    assert state["last_touch"]["inbound_message_id"] == "wamid.B"
    # ctwa_referrals tiene los 2 touches (multi-touch para Conversions API)
    assert len(state["ctwa_referrals"]) == 2
    assert state["ctwa_clids_seen"] == ["CLID_A", "CLID_B"]


@pytest.mark.asyncio
async def test_existing_ctwa_referrals_contract_unchanged():
    """REGRESIÓN: el contrato del test HU-002
    (test_referral_captured_persists_to_state_and_injects_banner) sigue
    intacto cuando agregamos origin/last_touch. Mismo inbound con
    ctwa_clid → ctwa_clids_seen y ctwa_referrals se llenan igual que antes.
    """
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    msg = WhatsAppMessage(
        message_id="wamid.CTWA",
        from_number="5491111111111",
        phone_number_id="PID",
        text="Hola, vi el anuncio",
        media=None,
        timestamp="1714312400",
        msg_type="text",
        referral={
            "ctwa_clid": "CLID_ABC",
            "source_type": "ad",
            "source_id": "AD_123",
            "headline": "Velas Hubara",
            "body": "Velas artesanales",
        },
    )
    await use_case.execute(msg)

    state = metadata.store["wa_5491111111111"]
    # Contrato preexistente (HU-002)
    assert state["ctwa_clids_seen"] == ["CLID_ABC"]
    assert state["ctwa_referrals"][0]["source_id"] == "AD_123"
    # Y ahora también:
    assert state["origin"]["channel"] == "ad"
    assert state["last_touch"]["channel"] == "ad"


# =============================================================================
# HU-WA24H-001 F1.1 + F1.3 — Service window + CTWA window timestamps
# =============================================================================


@pytest.mark.asyncio
async def test_persists_last_inbound_at_ms_and_service_window_expiry():
    """F1.1: cada inbound persiste last_inbound_at_ms + service_window_expires_at_ms
    (= last_inbound_at_ms + 24h). Es la base del watchdog del Sprint 2."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message(text="hola"))

    state = metadata.store["wa_5491111111111"]
    assert "last_inbound_at_ms" in state
    assert "service_window_expires_at_ms" in state
    assert isinstance(state["last_inbound_at_ms"], int)
    assert isinstance(state["service_window_expires_at_ms"], int)
    # Service window = last_inbound_at_ms + 24h exactos.
    twenty_four_hours_ms = 24 * 60 * 60 * 1000
    delta = state["service_window_expires_at_ms"] - state["last_inbound_at_ms"]
    assert delta == twenty_four_hours_ms


@pytest.mark.asyncio
async def test_service_window_renews_on_each_inbound():
    """F1.1: cada inbound del cliente reabre la ventana 24h. El timestamp
    del primer inbound se sobreescribe con el del segundo."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message(text="primer mensaje"))
    first_inbound_at = metadata.store["wa_5491111111111"]["last_inbound_at_ms"]
    first_expiry = metadata.store["wa_5491111111111"]["service_window_expires_at_ms"]

    # Pausa real mínima para garantizar que _now_ms() devuelva valor distinto.
    import asyncio

    await asyncio.sleep(0.01)

    await use_case.execute(_make_text_message(text="segundo mensaje"))
    second_inbound_at = metadata.store["wa_5491111111111"]["last_inbound_at_ms"]
    second_expiry = metadata.store["wa_5491111111111"]["service_window_expires_at_ms"]

    assert second_inbound_at > first_inbound_at
    assert second_expiry > first_expiry


@pytest.mark.asyncio
async def test_ctwa_window_set_only_when_ctwa_clid_present():
    """F1.3: ctwa_window_expires_at_ms se setea SOLO si referral.ctwa_clid
    está presente en el inbound (origen Click-to-WhatsApp Ad)."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    msg_ctwa = WhatsAppMessage(
        message_id="wamid.CTWA",
        from_number="5491111111111",
        phone_number_id="PID",
        text="Hola, vi el anuncio",
        media=None,
        timestamp="1714312400",
        msg_type="text",
        referral={
            "ctwa_clid": "CLID_XYZ",
            "source_type": "ad",
            "source_id": "AD_999",
            "headline": "Velas Hubara",
            "body": "Promo",
        },
    )
    await use_case.execute(msg_ctwa)

    state = metadata.store["wa_5491111111111"]
    assert "ctwa_window_expires_at_ms" in state
    seventy_two_hours_ms = 72 * 60 * 60 * 1000
    delta = state["ctwa_window_expires_at_ms"] - state["last_inbound_at_ms"]
    assert delta == seventy_two_hours_ms


@pytest.mark.asyncio
async def test_ctwa_window_not_set_for_direct_inbound():
    """F1.3: cliente sin CTWA — solo service window (24h), NO ctwa window."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    # Direct message — sin referral CTWA.
    await use_case.execute(_make_text_message(text="hola"))

    state = metadata.store["wa_5491111111111"]
    assert "service_window_expires_at_ms" in state
    assert "ctwa_window_expires_at_ms" not in state


@pytest.mark.asyncio
async def test_ctwa_window_not_overwritten_on_subsequent_inbound():
    """F1.3: la ventana CTWA NO se renueva con inbounds subsecuentes —
    el cliente solo entra una vez por CTWA. El timestamp del primer touch
    persiste aunque el cliente mande N mensajes después.

    Edge case: si el mismo cliente vuelve a click un CTWA dentro de los 72h
    (caso teórico), el ctwa_clid puede repetirse en el referral — defendemos
    contra reset de la ventana original con el chequeo `if "ctwa_window_expires_at_ms"
    not in metadata`."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    msg_ctwa = WhatsAppMessage(
        message_id="wamid.CTWA1",
        from_number="5491111111111",
        phone_number_id="PID",
        text="Hola, vi el anuncio",
        media=None,
        timestamp="1714312400",
        msg_type="text",
        referral={
            "ctwa_clid": "CLID_FIRST",
            "source_type": "ad",
            "source_id": "AD_1",
            "headline": "h",
            "body": "b",
        },
    )
    await use_case.execute(msg_ctwa)
    first_ctwa_expiry = metadata.store["wa_5491111111111"]["ctwa_window_expires_at_ms"]

    import asyncio

    await asyncio.sleep(0.01)

    # Segundo inbound, también con referral (caso teórico). NO debe pisar.
    msg_ctwa_2 = WhatsAppMessage(
        message_id="wamid.CTWA2",
        from_number="5491111111111",
        phone_number_id="PID",
        text="otra pregunta",
        media=None,
        timestamp="1714312500",
        msg_type="text",
        referral={
            "ctwa_clid": "CLID_SECOND",
            "source_type": "ad",
            "source_id": "AD_2",
            "headline": "h",
            "body": "b",
        },
    )
    await use_case.execute(msg_ctwa_2)

    second_ctwa_expiry = metadata.store["wa_5491111111111"][
        "ctwa_window_expires_at_ms"
    ]
    # CTWA window inmutable después del primer touch.
    assert second_ctwa_expiry == first_ctwa_expiry


# ---------------------------------------------------------------------------
# HU web-cart hot lead: `ref:cart_<id>` en el texto inbound → hidratación del
# carrito (best-effort) + siembra del draft + nota LEAD CALIENTE + analytics.
# Gotcha #1 del repo: verificamos COMPORTAMIENTO (la nota LLEGA al
# extra_context, los slots QUEDAN en el draft), no solo el schema.
# ---------------------------------------------------------------------------

_WEB_CART_ID = "cart_01JN2Y8FZAB3CD4EF5GH6JK7LM"
_WEB_CART_TEXT = (
    "Hola! Quiero terminar mi compra 🛒\n"
    "• 2x Vela Ángel (Lavanda)\n"
    f"ref:{_WEB_CART_ID}"
)


class FakeEventBus:
    def __init__(self) -> None:
        self.events: list = []

    async def record(self, event) -> None:
        self.events.append(event)


def _web_cart_snapshot(items=None, **kwargs):
    from src.sdk.connectorkit import WebCartItem, WebCartSnapshot

    if items is None:
        items = [
            WebCartItem(
                product_title="Vela Ángel",
                quantity=2,
                product_handle="vela-angel",
                variant_title="Lavanda",
            )
        ]
    return WebCartSnapshot(cart_id=_WEB_CART_ID, items=tuple(items), **kwargs)


def _catalog_with_vela_angel():
    from src.platform.catalog.dtos import (
        CatalogManifestDTO,
        CatalogProductDTO,
        CatalogVariantDTO,
        SearchResult,
    )

    product = CatalogProductDTO(
        id="prod_1",
        handle="vela-angel",
        title="Vela Ángel",
        status="published",
        variants=[
            CatalogVariantDTO(id="v1", title="Lavanda", options={"Aroma": "Lavanda"})
        ],
        options={"Aroma": ["Lavanda"]},
    )
    manifest = CatalogManifestDTO(
        version="test", fetched_at="2026-01-01T00:00:00Z", product_count=1
    )

    class FakeCatalog:
        async def get_by_handle(self, handle):
            if handle == product.handle:
                return product
            raise KeyError(handle)

        async def search(self, q, *, limit=10, category=None):
            hits = [product] if q.lower() in product.title.lower() else []
            return SearchResult(
                query=q,
                count=len(hits),
                truncated=False,
                stale=False,
                manifest=manifest,
                results=hits,
            )

        async def list_categories(self):
            return []

    return FakeCatalog()


def _web_cart_use_case(*, reader=None, catalog=None, bus=None):
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        tenant_id="tenant-test",
        web_cart_reader=reader,
        catalog=catalog,
    )
    return use_case, loader, metadata


async def _drain_spawned_tasks() -> None:
    """Deja correr las tasks fire-and-forget (_spawn_safe) del ingest."""
    import asyncio

    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_web_cart_ref_hydrates_draft_and_injects_hot_lead_note():
    from src.sdk.connectorkit import FakeWebCartReader

    bus = FakeEventBus()
    reader = FakeWebCartReader(
        {
            _WEB_CART_ID: _web_cart_snapshot(
                city="Bogotá", phone="573001234567", customer_name="Ana Pardo"
            )
        }
    )
    use_case, loader, metadata = _web_cart_use_case(
        reader=reader, catalog=_catalog_with_vela_angel(), bus=bus
    )

    await use_case.execute(_make_text_message(text=_WEB_CART_TEXT))
    await _drain_spawned_tasks()

    meta = metadata.store["wa_5491111111111"]
    assert meta["web_cart"]["status"] == "hydrated"
    assert meta["origin"]["channel"] == "web_cart"
    slots = meta["episodes"][-1]["order_draft"]["slots"]
    assert slots["producto"] == "Vela Ángel"
    assert slots["cantidad"] == "2"
    assert slots["aroma"] == "Lavanda"
    assert slots["ciudad"] == "Bogotá"

    # La nota LLEGA al prompt del MISMO turno (el más caliente).
    assert len(loader.calls) == 1
    notes = loader.calls[0].extra_context or []
    assert any("LEAD CALIENTE" in n for n in notes)
    # Y el breadcrumb del draft también se proyecta ya sembrado.
    assert any("Vela Ángel" in n for n in notes if "DATOS DEL PEDIDO" in n)

    captured = [e for e in bus.events if e.kind == "web_cart_captured"]
    assert len(captured) == 1
    assert captured[0].payload["status"] == "hydrated"
    assert captured[0].correlation["session_id"] == "wa_5491111111111"


@pytest.mark.asyncio
async def test_web_cart_reader_failure_degrades_and_still_signals_turn():
    class BoomReader:
        async def get_cart(self, cart_id):
            raise RuntimeError("medusa caida")

    bus = FakeEventBus()
    use_case, loader, metadata = _web_cart_use_case(
        reader=BoomReader(), catalog=_catalog_with_vela_angel(), bus=bus
    )

    await use_case.execute(_make_text_message(text=_WEB_CART_TEXT))
    await _drain_spawned_tasks()

    meta = metadata.store["wa_5491111111111"]
    assert meta["web_cart"]["status"] == "degraded"
    assert meta["web_cart"]["reason"] == "RuntimeError"
    # El turno se señala IGUAL — la misión es vender.
    assert len(loader.calls) == 1
    notes = loader.calls[0].extra_context or []
    assert any("LEAD CALIENTE" in n for n in notes)
    # Sin slots sembrados (no hubo cart).
    episode = meta["episodes"][-1]
    assert not (episode.get("order_draft") or {}).get("slots")

    captured = [e for e in bus.events if e.kind == "web_cart_captured"]
    assert captured and captured[0].payload["status"] == "degraded"


@pytest.mark.asyncio
async def test_web_cart_without_reader_wired_degrades():
    use_case, loader, metadata = _web_cart_use_case(reader=None, catalog=None)

    await use_case.execute(_make_text_message(text=_WEB_CART_TEXT))

    meta = metadata.store["wa_5491111111111"]
    assert meta["web_cart"]["status"] == "degraded"
    assert meta["web_cart"]["reason"] == "reader_unavailable"
    notes = loader.calls[0].extra_context or []
    assert any("LEAD CALIENTE" in n for n in notes)


@pytest.mark.asyncio
async def test_web_cart_unmatched_item_emits_mismatch_event():
    from src.sdk.connectorkit import FakeWebCartReader, WebCartItem

    bus = FakeEventBus()
    reader = FakeWebCartReader(
        {
            _WEB_CART_ID: _web_cart_snapshot(
                items=[WebCartItem(product_title="Vela Fantasma", quantity=3)]
            )
        }
    )
    use_case, loader, metadata = _web_cart_use_case(
        reader=reader, catalog=_catalog_with_vela_angel(), bus=bus
    )

    await use_case.execute(_make_text_message(text=_WEB_CART_TEXT))
    await _drain_spawned_tasks()

    meta = metadata.store["wa_5491111111111"]
    assert meta["web_cart"]["status"] == "hydrated"
    assert meta["web_cart"]["unmatched_titles"] == ["Vela Fantasma"]
    notes = loader.calls[0].extra_context or []
    assert any("Vela Fantasma" in n and "similar" in n for n in notes)

    mismatch = [e for e in bus.events if e.kind == "web_cart_product_mismatch"]
    assert len(mismatch) == 1
    assert mismatch[0].payload["unmatched_titles"] == ["Vela Fantasma"]


@pytest.mark.asyncio
async def test_plain_message_keeps_existing_flow_untouched():
    use_case, loader, metadata = _web_cart_use_case(
        reader=None, catalog=None, bus=FakeEventBus()
    )

    await use_case.execute(_make_text_message(text="hola, tienen velas?"))

    meta = metadata.store["wa_5491111111111"]
    assert "web_cart" not in meta
    assert meta["origin"]["channel"] == "direct"
    notes = loader.calls[0].extra_context or []
    assert not any("LEAD CALIENTE" in n for n in notes)


@pytest.mark.asyncio
async def test_same_cart_ref_twice_emits_single_captured_event():
    from src.sdk.connectorkit import FakeWebCartReader

    bus = FakeEventBus()
    reader = FakeWebCartReader({_WEB_CART_ID: _web_cart_snapshot()})
    use_case, loader, metadata = _web_cart_use_case(
        reader=reader, catalog=_catalog_with_vela_angel(), bus=bus
    )

    await use_case.execute(_make_text_message(text=_WEB_CART_TEXT))
    await use_case.execute(_make_text_message(text=_WEB_CART_TEXT))
    await _drain_spawned_tasks()

    assert len(loader.calls) == 2
    captured = [e for e in bus.events if e.kind == "web_cart_captured"]
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_web_cart_ref_is_ignored_when_human_owns_the_conversation():
    """Scenario del spec: con `active_route = humano` el ingest NO siembra
    drafts ni notas — el humano conserva el control (mismo principio que el
    guard del episode lifecycle; sembrar acá re-abriría maquinaria de bot
    sobre una conversación intervenida)."""
    from src.sdk.connectorkit import FakeWebCartReader

    bus = FakeEventBus()
    reader = FakeWebCartReader({_WEB_CART_ID: _web_cart_snapshot()})
    use_case, loader, metadata = _web_cart_use_case(
        reader=reader, catalog=_catalog_with_vela_angel(), bus=bus
    )
    metadata.store["wa_5491111111111"] = {"active_route": "humano", "tag": "HUMANO"}

    await use_case.execute(_make_text_message(text=_WEB_CART_TEXT))
    await _drain_spawned_tasks()

    meta = metadata.store["wa_5491111111111"]
    assert "web_cart" not in meta
    assert not meta.get("episodes")  # tampoco se abrió episodio (guard previo)
    notes = (loader.calls[0].extra_context or []) if loader.calls else []
    assert not any("LEAD CALIENTE" in n for n in notes)
    assert not [e for e in bus.events if e.kind == "web_cart_captured"]
