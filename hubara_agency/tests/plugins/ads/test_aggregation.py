"""Tests del use case `list_ads_campaigns` y `list_attributed_conversations`.

Cubren la lectura del vault (`wa_*/metadata.json`) y la agregación por
`source_id` (= campaign_id). Los DTOs devueltos tienen campos `None`
explícitos para los datos que aún no podemos derivar (spend, revenue,
status Meta Ads, etc.) — el frontend marca esos slots visualmente.

El fixture autouse `_isolate_vault_dir` (conftest.py) garantiza que cada
test escribe a un tmp path. Pasamos `vault_dir` por DI explícita al use
case, así no dependemos de monkey-patching de globals.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.plugins.ads.aggregation import (
    AdsAttributedConversation,
    AdsCampaignSummary,
    list_ads_campaigns,
    list_attributed_conversations,
    list_daily_series,
)


# --- Helpers ---------------------------------------------------------------


def _write_session(
    vault: Path,
    *,
    phone: str,
    origin: dict | None = None,
    last_touch: dict | None = None,
    ctwa_referrals: list[dict] | None = None,
    active_route: str | None = None,
    history_lines: list[str] | None = None,
    extra: dict | None = None,
) -> Path:
    """Crea un wa_<phone>/metadata.json (+ sessions/<sid>.jsonl si hay
    history_lines). Devuelve el path del session dir.
    """
    session_id = f"wa_{phone}"
    session_dir = vault / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    metadata: dict = {}
    if origin is not None:
        metadata["origin"] = origin
    if last_touch is not None:
        metadata["last_touch"] = last_touch
    if ctwa_referrals is not None:
        metadata["ctwa_referrals"] = ctwa_referrals
    if active_route is not None:
        metadata["active_route"] = active_route
    if extra is not None:
        metadata.update(extra)

    (session_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )

    if history_lines is not None:
        sessions_dir = session_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        jsonl = sessions_dir / f"{session_id}.jsonl"
        jsonl.write_text("\n".join(history_lines) + "\n", encoding="utf-8")

    return session_dir


# --- list_ads_campaigns ----------------------------------------------------


def test_empty_vault_returns_empty_list(_isolate_vault_dir: Path):
    """Vault sin sesiones → []. El frontend muestra el empty state existente."""
    assert list_ads_campaigns(_isolate_vault_dir) == []


def test_ignores_sessions_without_origin(_isolate_vault_dir: Path):
    """Sesiones legacy sin `origin` no rompen y no aparecen como campañas."""
    _write_session(
        _isolate_vault_dir,
        phone="111",
        active_route="ventas",
        # SIN origin/last_touch (sesión vieja antes del feature)
    )
    assert list_ads_campaigns(_isolate_vault_dir) == []


def test_direct_origin_creates_synthetic_campaign(_isolate_vault_dir: Path):
    """Sesiones con origin.channel='direct' se agrupan en una `campaña
    sintética` con id='direct'. Es la "campaña" de clientes orgánicos /
    sin atribución a ad/post — útil para que el dashboard ads muestre
    también el volumen de mensajes que NO vienen de Meta.
    """
    _write_session(
        _isolate_vault_dir,
        phone="222",
        origin={
            "channel": "direct",
            "first_seen_ms": 1714312400000,
            "first_inbound_message_id": "wamid.X",
            "headline": None,
            "source_id": None,
        },
    )
    _write_session(
        _isolate_vault_dir,
        phone="333",
        origin={
            "channel": "direct",
            "first_seen_ms": 1714312500000,
            "first_inbound_message_id": "wamid.Y",
            "headline": None,
            "source_id": None,
        },
    )

    campaigns = list_ads_campaigns(_isolate_vault_dir)
    assert len(campaigns) == 1
    direct = campaigns[0]
    assert direct.id == "direct"
    assert direct.source_type == "direct"
    assert direct.started == 2
    assert direct.first_seen_ms == 1714312400000
    assert direct.last_seen_ms == 1714312500000
    # Naming visible — sin source_id, el name es un label fijo
    assert direct.name is not None and "directo" in direct.name.lower()


def test_direct_synthetic_coexists_with_real_campaigns(_isolate_vault_dir: Path):
    """Un ad + un direct → 2 campañas: la real (con source_id) + la
    sintética 'direct'."""
    _write_session(
        _isolate_vault_dir,
        phone="111",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312400000,
            "first_inbound_message_id": "wamid.A",
            "headline": "Velas Hubara",
            "source_id": "AD_123",
        },
    )
    _write_session(
        _isolate_vault_dir,
        phone="222",
        origin={
            "channel": "direct",
            "first_seen_ms": 1714312500000,
            "first_inbound_message_id": "wamid.D",
            "headline": None,
            "source_id": None,
        },
    )
    campaigns = list_ads_campaigns(_isolate_vault_dir)
    assert len(campaigns) == 2
    by_id = {c.id: c for c in campaigns}
    assert "AD_123" in by_id
    assert "direct" in by_id
    assert by_id["direct"].source_type == "direct"
    assert by_id["AD_123"].source_type == "ad"


def test_attributed_conversations_for_direct_campaign(_isolate_vault_dir: Path):
    """campaign_id='direct' devuelve todas las sesiones con
    origin.channel='direct' (no se filtra por source_id porque es null)."""
    _write_session(
        _isolate_vault_dir,
        phone="111",
        origin={
            "channel": "direct",
            "first_seen_ms": 1714312400000,
            "first_inbound_message_id": "wamid.A",
            "headline": None,
            "source_id": None,
        },
        active_route="ventas",
    )
    _write_session(
        _isolate_vault_dir,
        phone="222",
        origin={
            "channel": "direct",
            "first_seen_ms": 1714312500000,
            "first_inbound_message_id": "wamid.B",
            "headline": None,
            "source_id": None,
        },
        active_route="ventas",
    )
    # Un ad NO debe aparecer cuando se pide direct
    _write_session(
        _isolate_vault_dir,
        phone="333",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312600000,
            "first_inbound_message_id": "wamid.C",
            "headline": "Otro",
            "source_id": "AD_OTHER",
        },
    )

    convs = list_attributed_conversations(_isolate_vault_dir, "direct")
    assert len(convs) == 2
    phones = {c.phone_number for c in convs}
    assert phones == {"111", "222"}


def test_groups_sessions_by_source_id(_isolate_vault_dir: Path):
    """Tres sesiones con el mismo source_id se agrupan en UNA campaña
    con started=3 y headline del referral más reciente."""
    _write_session(
        _isolate_vault_dir,
        phone="111",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312400000,
            "first_inbound_message_id": "wamid.A",
            "headline": "Velas Hubara",
            "source_id": "AD_123",
        },
    )
    _write_session(
        _isolate_vault_dir,
        phone="222",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312500000,
            "first_inbound_message_id": "wamid.B",
            "headline": "Velas Hubara",
            "source_id": "AD_123",
        },
    )
    _write_session(
        _isolate_vault_dir,
        phone="333",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312600000,
            "first_inbound_message_id": "wamid.C",
            "headline": "Velas Hubara — Día de la Madre",  # más reciente
            "source_id": "AD_123",
        },
    )

    campaigns = list_ads_campaigns(_isolate_vault_dir)
    assert len(campaigns) == 1
    camp = campaigns[0]
    assert isinstance(camp, AdsCampaignSummary)
    assert camp.id == "AD_123"
    assert camp.started == 3
    assert camp.source_type == "ad"
    assert camp.first_seen_ms == 1714312400000  # mínimo
    assert camp.last_seen_ms == 1714312600000  # máximo
    # headline del más reciente
    assert camp.name == "Velas Hubara — Día de la Madre"
    # Campos sin data quedan en None
    assert camp.spend is None
    assert camp.revenue is None
    assert camp.status is None


def test_distinguishes_ad_post_web_referral_channels(_isolate_vault_dir: Path):
    """Tres campañas: una `ad` con clid, una `post` con clid, una
    `web_referral` sin clid. Cada source_id es propio."""
    _write_session(
        _isolate_vault_dir,
        phone="111",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312400000,
            "first_inbound_message_id": "wamid.A",
            "headline": "Anuncio A",
            "source_id": "AD_A",
        },
    )
    _write_session(
        _isolate_vault_dir,
        phone="222",
        origin={
            "channel": "post",
            "first_seen_ms": 1714312500000,
            "first_inbound_message_id": "wamid.B",
            "headline": "Post B",
            "source_id": "POST_B",
        },
    )
    _write_session(
        _isolate_vault_dir,
        phone="333",
        origin={
            "channel": "web_referral",
            "first_seen_ms": 1714312600000,
            "first_inbound_message_id": "wamid.C",
            "headline": "Web referral C",
            "source_id": "AD_WEB_C",
        },
    )

    campaigns = list_ads_campaigns(_isolate_vault_dir)
    assert len(campaigns) == 3
    by_id = {c.id: c for c in campaigns}
    assert by_id["AD_A"].source_type == "ad"
    assert by_id["POST_B"].source_type == "post"
    assert by_id["AD_WEB_C"].source_type == "web_referral"


def test_sorted_by_last_seen_descending(_isolate_vault_dir: Path):
    """Campañas ordenadas por last_seen_ms descendente (más reciente primero)."""
    _write_session(
        _isolate_vault_dir,
        phone="111",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312400000,
            "first_inbound_message_id": "wamid.A",
            "headline": "Vieja",
            "source_id": "OLD",
        },
    )
    _write_session(
        _isolate_vault_dir,
        phone="222",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312900000,
            "first_inbound_message_id": "wamid.B",
            "headline": "Nueva",
            "source_id": "NEW",
        },
    )
    campaigns = list_ads_campaigns(_isolate_vault_dir)
    assert [c.id for c in campaigns] == ["NEW", "OLD"]


def test_skips_sessions_with_origin_but_no_source_id(_isolate_vault_dir: Path):
    """Edge case defensivo: si origin.source_id es None (channel ad
    sin source_id por algún bug upstream), la sesión NO genera campaña."""
    _write_session(
        _isolate_vault_dir,
        phone="111",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312400000,
            "first_inbound_message_id": "wamid.X",
            "headline": "Sin source_id",
            "source_id": None,
        },
    )
    assert list_ads_campaigns(_isolate_vault_dir) == []


def test_tolerates_corrupted_metadata_json(_isolate_vault_dir: Path):
    """metadata.json corrupto → se ignora la sesión, no rompe la lista."""
    session_dir = _isolate_vault_dir / "wa_corrupt"
    session_dir.mkdir(parents=True)
    (session_dir / "metadata.json").write_text("{ not valid json", encoding="utf-8")

    # Otra sesión válida
    _write_session(
        _isolate_vault_dir,
        phone="222",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312400000,
            "first_inbound_message_id": "wamid.B",
            "headline": "OK",
            "source_id": "AD_OK",
        },
    )

    campaigns = list_ads_campaigns(_isolate_vault_dir)
    assert len(campaigns) == 1
    assert campaigns[0].id == "AD_OK"


# --- list_attributed_conversations -----------------------------------------


def test_attributed_conversations_filtered_by_campaign(_isolate_vault_dir: Path):
    """Solo las sesiones con origin.source_id == campaign_id se devuelven."""
    _write_session(
        _isolate_vault_dir,
        phone="111",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312400000,
            "first_inbound_message_id": "wamid.A",
            "headline": "Velas Hubara",
            "source_id": "AD_TARGET",
        },
        active_route="ventas",
    )
    _write_session(
        _isolate_vault_dir,
        phone="222",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312500000,
            "first_inbound_message_id": "wamid.B",
            "headline": "Otra",
            "source_id": "AD_OTHER",
        },
        active_route="ventas",
    )

    convs = list_attributed_conversations(_isolate_vault_dir, "AD_TARGET")
    assert len(convs) == 1
    c = convs[0]
    assert isinstance(c, AdsAttributedConversation)
    assert c.id == "wa_111"
    assert c.phone_number == "111"
    assert c.started_at_ms == 1714312400000
    assert c.ad_headline == "Velas Hubara"
    assert c.agent == "ventas"
    # Campos sin data (CRM/orders pendientes)
    assert c.name is None
    assert c.city is None
    assert c.value is None
    # state se deriva del classifier — esta sesión es NO_ETIQUETADO sin
    # mensajes en JSONL → `nuevo` (heurística default).
    assert c.state == "nuevo"


def test_attributed_conversations_msgs_count_from_jsonl(_isolate_vault_dir: Path):
    """msgs_count cuenta líneas del history JSONL si existe."""
    _write_session(
        _isolate_vault_dir,
        phone="111",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312400000,
            "first_inbound_message_id": "wamid.A",
            "headline": "h",
            "source_id": "AD_X",
        },
        history_lines=[
            '{"role":"user","content":"hola"}',
            '{"role":"assistant","content":"hola!"}',
            '{"role":"user","content":"info"}',
        ],
    )

    convs = list_attributed_conversations(_isolate_vault_dir, "AD_X")
    assert len(convs) == 1
    assert convs[0].msgs_count == 3
    # last_msg_at_ms viene del mtime del JSONL (existe)
    assert convs[0].last_msg_at_ms is not None


def test_attributed_conversations_msgs_count_zero_when_no_jsonl(
    _isolate_vault_dir: Path,
):
    """Si no hay JSONL, msgs_count=0 y last_msg_at_ms cae al last_touch o
    al first_seen_ms del origin (no es None)."""
    _write_session(
        _isolate_vault_dir,
        phone="111",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312400000,
            "first_inbound_message_id": "wamid.A",
            "headline": "h",
            "source_id": "AD_X",
        },
        last_touch={
            "channel": "ad",
            "seen_at_ms": 1714312900000,
            "inbound_message_id": "wamid.LAST",
            "ctwa_clid": "CLID_X",
            "headline": "h",
            "source_id": "AD_X",
        },
    )

    convs = list_attributed_conversations(_isolate_vault_dir, "AD_X")
    assert len(convs) == 1
    assert convs[0].msgs_count == 0
    # Fallback: last_touch.seen_at_ms
    assert convs[0].last_msg_at_ms == 1714312900000


def test_attributed_conversations_sorted_by_started_desc(_isolate_vault_dir: Path):
    """Lista ordenada por started_at_ms descendente (más reciente primero)."""
    _write_session(
        _isolate_vault_dir,
        phone="111",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312400000,
            "first_inbound_message_id": "wamid.A",
            "headline": "h",
            "source_id": "AD_X",
        },
    )
    _write_session(
        _isolate_vault_dir,
        phone="222",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312900000,
            "first_inbound_message_id": "wamid.B",
            "headline": "h",
            "source_id": "AD_X",
        },
    )

    convs = list_attributed_conversations(_isolate_vault_dir, "AD_X")
    assert [c.phone_number for c in convs] == ["222", "111"]


def test_attributed_conversations_empty_for_unknown_campaign(
    _isolate_vault_dir: Path,
):
    """Campaign id inexistente → []."""
    _write_session(
        _isolate_vault_dir,
        phone="111",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312400000,
            "first_inbound_message_id": "wamid.A",
            "headline": "h",
            "source_id": "AD_REAL",
        },
    )
    assert list_attributed_conversations(_isolate_vault_dir, "AD_DOES_NOT_EXIST") == []


def test_attributed_conversations_empty_vault(_isolate_vault_dir: Path):
    """Vault vacío → []."""
    assert list_attributed_conversations(_isolate_vault_dir, "AD_X") == []


# ---------------------------------------------------------------------------
# State + conversations counts (classifier integration)
# ---------------------------------------------------------------------------


def test_conversation_with_registered_order_is_state_ganado(_isolate_vault_dir: Path):
    """Sesión con registered_order.success=True → state=ganado.
    Es la señal de venta cerrada más fuerte (Medusa)."""
    _write_session(
        _isolate_vault_dir,
        phone="111",
        origin={
            "channel": "direct",
            "first_seen_ms": 1714312400000,
            "first_inbound_message_id": "wamid.A",
            "headline": None,
            "source_id": None,
        },
        # Adicionalmente seteamos registered_order via raw merge:
    )
    # Mutamos el metadata escrito para agregar el registered_order
    import json as _json
    meta_path = _isolate_vault_dir / "wa_111" / "metadata.json"
    data = _json.loads(meta_path.read_text())
    data["registered_order"] = {"success": True, "order_id": "order_xyz"}
    meta_path.write_text(_json.dumps(data))

    convs = list_attributed_conversations(_isolate_vault_dir, "direct")
    assert len(convs) == 1
    assert convs[0].state == "ganado"


def test_campaign_conversations_counts_aggregate_states(_isolate_vault_dir: Path):
    """Cuatro sesiones del mismo ad: 2 perdido, 1 ganado, 1 calificado.
    El bucket de la campaña debe sumar counts correctamente."""
    import json as _json

    def _add(phone: str, extra_meta: dict):
        _write_session(
            _isolate_vault_dir,
            phone=phone,
            origin={
                "channel": "ad",
                "first_seen_ms": 1714312000000 + int(phone) * 1000,
                "first_inbound_message_id": f"wamid.{phone}",
                "headline": "Velas Hubara",
                "source_id": "AD_MULTI",
            },
        )
        meta_path = _isolate_vault_dir / f"wa_{phone}" / "metadata.json"
        data = _json.loads(meta_path.read_text())
        data.update(extra_meta)
        meta_path.write_text(_json.dumps(data))

    _add("100", {"tag": "RECHAZO"})
    _add("200", {"tag": "RECHAZO"})
    _add("300", {"registered_order": {"success": True, "order_id": "x"}})
    _add("400", {"tag": "INTERESADO"})

    campaigns = list_ads_campaigns(_isolate_vault_dir)
    assert len(campaigns) == 1
    counts = campaigns[0].conversations
    assert counts is not None
    assert counts["perdido"] == 2
    assert counts["ganado"] == 1
    assert counts["calificado"] == 1
    # Resto en 0
    assert counts["nuevo"] == 0
    assert counts["activo"] == 0
    assert counts["cotizado"] == 0
    assert counts["no_reply"] == 0
    # started total = 4
    assert campaigns[0].started == 4
    # Counts suman al total
    assert sum(counts.values()) == 4


def test_direct_bucket_conversations_counts(_isolate_vault_dir: Path):
    """El bucket sintético `direct` también acumula counts por estado."""
    import json as _json

    def _add(phone: str, extra_meta: dict):
        _write_session(
            _isolate_vault_dir,
            phone=phone,
            origin={
                "channel": "direct",
                "first_seen_ms": 1714312000000 + int(phone) * 1000,
                "first_inbound_message_id": f"wamid.{phone}",
                "headline": None,
                "source_id": None,
            },
        )
        meta_path = _isolate_vault_dir / f"wa_{phone}" / "metadata.json"
        data = _json.loads(meta_path.read_text())
        data.update(extra_meta)
        meta_path.write_text(_json.dumps(data))

    _add("100", {"registered_order": {"success": True, "order_id": "o1"}})
    _add("200", {"tag": "NO_ETIQUETADO"})  # nuevo (0 msgs)

    campaigns = list_ads_campaigns(_isolate_vault_dir)
    assert len(campaigns) == 1
    direct = campaigns[0]
    assert direct.id == "direct"
    assert direct.conversations is not None
    assert direct.conversations["ganado"] == 1
    assert direct.conversations["nuevo"] == 1
    assert direct.started == 2


def test_session_with_multiple_episodes_produces_one_conversation_per_episode(
    _isolate_vault_dir: Path,
):
    """Una sesión con 2 episodios (ep_001 cerrado COMPRA_EXITOSA + ep_002
    activo) → 2 conversaciones en la lista de atribuidas, con states
    distintos. El id incluye el episode_id para diferenciar."""
    import json as _json

    _write_session(
        _isolate_vault_dir,
        phone="555",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714000000000,
            "first_inbound_message_id": "wamid.A1",
            "headline": "Velas Hubara",
            "source_id": "AD_MULTI_EP",
        },
    )
    meta_path = _isolate_vault_dir / "wa_555" / "metadata.json"
    data = _json.loads(meta_path.read_text())
    # Simulamos 2 episodios: uno cerrado con orden, otro activo recién creado
    data["episodes"] = [
        {
            "episode_id": "ep_001",
            "started_at_ms": 1714000000000,
            "started_inbound_message_id": "wamid.A1",
            "closed_at_ms": 1714001000000,
            "closing_tag": "COMPRA_EXITOSA",
            "closing_motivo": "compró",
            "order_id": "order_xyz",
            "referral_snapshot": None,
        },
        {
            "episode_id": "ep_002",
            "started_at_ms": 1715000000000,
            "started_inbound_message_id": "wamid.A2",
            "closed_at_ms": None,
            "closing_tag": None,
            "closing_motivo": None,
            "order_id": None,
            "referral_snapshot": None,
        },
    ]
    data["tag"] = "NO_ETIQUETADO"  # reset por el episodio nuevo
    meta_path.write_text(_json.dumps(data))

    convs = list_attributed_conversations(_isolate_vault_dir, "AD_MULTI_EP")
    assert len(convs) == 2

    by_ep = {c.episode_id: c for c in convs}
    assert "ep_001" in by_ep
    assert "ep_002" in by_ep
    # ep_001 cerrado con order → ganado
    assert by_ep["ep_001"].state == "ganado"
    assert by_ep["ep_001"].id == "wa_555__ep_001"
    # ep_002 activo recién abierto sin msgs → nuevo
    assert by_ep["ep_002"].state == "nuevo"
    assert by_ep["ep_002"].id == "wa_555__ep_002"
    # Mismo phone_number en ambas
    assert all(c.phone_number == "555" for c in convs)


def test_campaign_started_counts_episodes_not_sessions(
    _isolate_vault_dir: Path,
):
    """AdsCampaignSummary.started cuenta EPISODIOS (no sesiones únicas).
    1 cliente con 2 episodios + 1 cliente con 1 episodio = started=3."""
    import json as _json

    # Cliente A: 2 episodios
    _write_session(
        _isolate_vault_dir,
        phone="A",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714000000000,
            "first_inbound_message_id": "wamid.A1",
            "headline": "h",
            "source_id": "AD_X",
        },
    )
    meta_a = _isolate_vault_dir / "wa_A" / "metadata.json"
    data_a = _json.loads(meta_a.read_text())
    data_a["episodes"] = [
        {
            "episode_id": "ep_001",
            "started_at_ms": 1714000000000,
            "started_inbound_message_id": "wamid.A1",
            "closed_at_ms": 1714001000000,
            "closing_tag": "RECHAZO",
            "closing_motivo": "no quiso",
            "order_id": None,
            "referral_snapshot": None,
        },
        {
            "episode_id": "ep_002",
            "started_at_ms": 1715000000000,
            "started_inbound_message_id": "wamid.A2",
            "closed_at_ms": None,
            "closing_tag": None,
            "closing_motivo": None,
            "order_id": None,
            "referral_snapshot": None,
        },
    ]
    meta_a.write_text(_json.dumps(data_a))

    # Cliente B: 1 episodio
    _write_session(
        _isolate_vault_dir,
        phone="B",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714500000000,
            "first_inbound_message_id": "wamid.B1",
            "headline": "h",
            "source_id": "AD_X",
        },
    )
    meta_b = _isolate_vault_dir / "wa_B" / "metadata.json"
    data_b = _json.loads(meta_b.read_text())
    data_b["episodes"] = [
        {
            "episode_id": "ep_001",
            "started_at_ms": 1714500000000,
            "started_inbound_message_id": "wamid.B1",
            "closed_at_ms": None,
            "closing_tag": None,
            "closing_motivo": None,
            "order_id": None,
            "referral_snapshot": None,
        },
    ]
    meta_b.write_text(_json.dumps(data_b))

    campaigns = list_ads_campaigns(_isolate_vault_dir)
    assert len(campaigns) == 1
    camp = campaigns[0]
    # Total de episodios: 2 + 1 = 3
    assert camp.started == 3
    counts = camp.conversations
    assert counts is not None
    assert counts["perdido"] == 1  # ep_001 de A
    assert counts["nuevo"] == 2    # ep_002 de A + ep_001 de B


def test_legacy_session_without_episodes_still_yields_one_conversation(
    _isolate_vault_dir: Path,
):
    """Sesión legacy SIN `episodes[]` sigue generando 1 conversación con
    episode_id=None (id sin sufijo)."""
    _write_session(
        _isolate_vault_dir,
        phone="LEGACY",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714000000000,
            "first_inbound_message_id": "wamid.L",
            "headline": "h",
            "source_id": "AD_LEGACY",
        },
    )

    convs = list_attributed_conversations(_isolate_vault_dir, "AD_LEGACY")
    assert len(convs) == 1
    assert convs[0].episode_id is None
    assert convs[0].id == "wa_LEGACY"


def test_msgs_count_uses_episode_snapshots_when_present(
    _isolate_vault_dir: Path,
):
    """FU3: cada conversación muestra msgs_in_episode (snapshot close - snapshot start),
    no el count global del JSONL de la sesión."""
    import json as _json

    _write_session(
        _isolate_vault_dir,
        phone="777",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714000000000,
            "first_inbound_message_id": "wamid.A1",
            "headline": "Velas",
            "source_id": "AD_FU3",
        },
        # Fake JSONL con 25 líneas — total global
        history_lines=[f'{{"role":"user","content":"msg {i}"}}' for i in range(25)],
    )
    meta_path = _isolate_vault_dir / "wa_777" / "metadata.json"
    data = _json.loads(meta_path.read_text())
    data["episodes"] = [
        # ep_001: msgs 0..14 = 15 mensajes (cerrado)
        {
            "episode_id": "ep_001",
            "started_at_ms": 1714000000000,
            "started_inbound_message_id": "wamid.A1",
            "closed_at_ms": 1714001000000,
            "closing_tag": "COMPRA_EXITOSA",
            "closing_motivo": "compró",
            "order_id": "order_a",
            "referral_snapshot": None,
            "msgs_count_at_start": 0,
            "msgs_count_at_close": 15,
        },
        # ep_002: msgs 15..24 = 10 mensajes (activo, no closed)
        {
            "episode_id": "ep_002",
            "started_at_ms": 1715000000000,
            "started_inbound_message_id": "wamid.B1",
            "closed_at_ms": None,
            "closing_tag": None,
            "closing_motivo": None,
            "order_id": None,
            "referral_snapshot": None,
            "msgs_count_at_start": 15,
            "msgs_count_at_close": None,
        },
    ]
    data["tag"] = "NO_ETIQUETADO"
    meta_path.write_text(_json.dumps(data))

    convs = list_attributed_conversations(_isolate_vault_dir, "AD_FU3")
    by_ep = {c.episode_id: c for c in convs}
    # ep_001 cerrado: at_close - at_start = 15 - 0 = 15
    assert by_ep["ep_001"].msgs_count == 15
    # ep_002 activo: total - at_start = 25 - 15 = 10
    assert by_ep["ep_002"].msgs_count == 10


def test_msgs_count_fallback_when_no_snapshots(_isolate_vault_dir: Path):
    """Sesión legacy sin snapshots → msgs_count cae al total del JSONL."""
    _write_session(
        _isolate_vault_dir,
        phone="LEGACY",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714000000000,
            "first_inbound_message_id": "wamid.L1",
            "headline": "Velas",
            "source_id": "AD_LEG_FU3",
        },
        history_lines=['{"role":"user"}' for _ in range(7)],
    )

    convs = list_attributed_conversations(_isolate_vault_dir, "AD_LEG_FU3")
    assert len(convs) == 1
    assert convs[0].msgs_count == 7  # total del JSONL


def test_reattribution_episode_goes_to_snapshot_campaign_not_session_origin(
    _isolate_vault_dir: Path,
):
    """FU2: cliente con ep_001 desde AD_A y ep_002 desde AD_B → ep_001
    cuenta en AD_A, ep_002 cuenta en AD_B (no en el sticky origin)."""
    import json as _json

    _write_session(
        _isolate_vault_dir,
        phone="999",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714000000000,
            "first_inbound_message_id": "wamid.A1",
            "headline": "Velas A",
            "source_id": "AD_A",
        },
    )
    meta_path = _isolate_vault_dir / "wa_999" / "metadata.json"
    data = _json.loads(meta_path.read_text())
    data["episodes"] = [
        {
            "episode_id": "ep_001",
            "started_at_ms": 1714000000000,
            "started_inbound_message_id": "wamid.A1",
            "closed_at_ms": 1714001000000,
            "closing_tag": "COMPRA_EXITOSA",
            "closing_motivo": "compró",
            "order_id": "order_a",
            "referral_snapshot": {
                "channel": "ad",
                "source_id": "AD_A",
                "headline": "Velas A",
            },
        },
        {
            "episode_id": "ep_002",
            "started_at_ms": 1715000000000,
            "started_inbound_message_id": "wamid.B1",
            "closed_at_ms": None,
            "closing_tag": None,
            "closing_motivo": None,
            "order_id": None,
            # ep_002 vino desde OTRO ad (AD_B)
            "referral_snapshot": {
                "channel": "ad",
                "source_id": "AD_B",
                "headline": "Velas B",
            },
        },
    ]
    data["tag"] = "NO_ETIQUETADO"
    meta_path.write_text(_json.dumps(data))

    campaigns = list_ads_campaigns(_isolate_vault_dir)
    by_id = {c.id: c for c in campaigns}
    # AD_A tiene el ep_001 (ganado)
    assert "AD_A" in by_id
    assert by_id["AD_A"].started == 1
    assert by_id["AD_A"].conversations is not None
    assert by_id["AD_A"].conversations["ganado"] == 1
    # AD_B tiene el ep_002 (nuevo)
    assert "AD_B" in by_id
    assert by_id["AD_B"].started == 1
    assert by_id["AD_B"].conversations is not None
    assert by_id["AD_B"].conversations["nuevo"] == 1
    # El name de AD_B sale del snapshot, no del origin sticky
    assert by_id["AD_B"].name == "Velas B"

    # Drill-down: ep_002 sale al consultar AD_B
    b_convs = list_attributed_conversations(_isolate_vault_dir, "AD_B")
    assert len(b_convs) == 1
    assert b_convs[0].episode_id == "ep_002"
    # Drill-down: ep_001 sale al consultar AD_A
    a_convs = list_attributed_conversations(_isolate_vault_dir, "AD_A")
    assert len(a_convs) == 1
    assert a_convs[0].episode_id == "ep_001"


def test_reattribution_to_direct_when_episode_has_no_referral(
    _isolate_vault_dir: Path,
):
    """Cliente vino desde un ad inicialmente (ep_001), pero ep_002 abrió
    direct (sin referral). ep_002 cuenta en el bucket DIRECT, no en AD_A."""
    import json as _json

    _write_session(
        _isolate_vault_dir,
        phone="888",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714000000000,
            "first_inbound_message_id": "wamid.A1",
            "headline": "Velas",
            "source_id": "AD_X",
        },
    )
    meta_path = _isolate_vault_dir / "wa_888" / "metadata.json"
    data = _json.loads(meta_path.read_text())
    data["episodes"] = [
        {
            "episode_id": "ep_001",
            "started_at_ms": 1714000000000,
            "started_inbound_message_id": "wamid.A1",
            "closed_at_ms": 1714001000000,
            "closing_tag": "RECHAZO",
            "closing_motivo": "no quiso",
            "order_id": None,
            "referral_snapshot": {
                "channel": "ad",
                "source_id": "AD_X",
                "headline": "Velas",
            },
        },
        {
            "episode_id": "ep_002",
            "started_at_ms": 1715000000000,
            "started_inbound_message_id": "wamid.B1",
            "closed_at_ms": None,
            "closing_tag": None,
            "closing_motivo": None,
            "order_id": None,
            # Direct: cliente volvió sin venir de un ad
            "referral_snapshot": {"channel": "direct"},
        },
    ]
    data["tag"] = "NO_ETIQUETADO"
    meta_path.write_text(_json.dumps(data))

    campaigns = list_ads_campaigns(_isolate_vault_dir)
    by_id = {c.id: c for c in campaigns}
    # ep_001 va a AD_X (sticky + snapshot coinciden)
    assert by_id["AD_X"].started == 1
    assert by_id["AD_X"].conversations["perdido"] == 1
    # ep_002 va a direct (NO a AD_X aunque la sesión origin sea AD_X)
    assert "direct" in by_id
    assert by_id["direct"].started == 1
    assert by_id["direct"].conversations["nuevo"] == 1
    # AD_X NO incluye ep_002
    assert sum(by_id["AD_X"].conversations.values()) == 1


def test_campaign_with_zero_classifiable_sessions_has_empty_counts(
    _isolate_vault_dir: Path,
):
    """Campaña con 1 sesión válida → conversations dict NO es None.
    Si el bucket tiene sesiones, el dict siempre viene poblado (aunque
    todas vayan al mismo bucket — never None for non-empty)."""
    _write_session(
        _isolate_vault_dir,
        phone="111",
        origin={
            "channel": "ad",
            "first_seen_ms": 1714312000000,
            "first_inbound_message_id": "wamid.A",
            "headline": "h",
            "source_id": "AD_ONE",
        },
    )
    campaigns = list_ads_campaigns(_isolate_vault_dir)
    assert len(campaigns) == 1
    counts = campaigns[0].conversations
    assert counts is not None
    # No mensajes en JSONL + sin tag → nuevo
    assert counts["nuevo"] == 1
    assert sum(counts.values()) == 1


# ---------------------------------------------------------------------------
# Agregados de negocio: revenue, costo LLM, duración + serie diaria
# ---------------------------------------------------------------------------


def _ep(
    episode_id: str,
    *,
    started_at_ms: int,
    closed_at_ms: int | None = None,
    order_id: str | None = None,
    order_total_cop: int | None = None,
    closing_tag: str | None = None,
    llm_cost_usd: float | None = None,
    llm_tokens: int | None = None,
) -> dict:
    """Construye un episodio con shape de `episode_lifecycle._make_empty_episode`.
    `referral_snapshot=None` → la atribución cae al `origin` de la sesión."""
    ep: dict = {
        "episode_id": episode_id,
        "started_at_ms": started_at_ms,
        "started_inbound_message_id": None,
        "closed_at_ms": closed_at_ms,
        "closing_tag": closing_tag,
        "closing_motivo": None,
        "order_id": order_id,
        "order_total_cop": order_total_cop,
        "order_currency": "COP" if order_total_cop is not None else None,
        "referral_snapshot": None,
        "msgs_count_at_start": None,
        "msgs_count_at_close": None,
    }
    if llm_cost_usd is not None or llm_tokens is not None:
        ep["llm_usage"] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": llm_tokens or 0,
            "cost_usd": llm_cost_usd if llm_cost_usd is not None else 0.0,
        }
    return ep


def _write_episodic_session(
    vault: Path,
    *,
    phone: str,
    source_id: str,
    episodes: list[dict],
    channel: str = "ad",
    headline: str = "Velas",
    registered_order: dict | None = None,
    first_seen_ms: int = 1714312400000,
) -> Path:
    """Crea wa_<phone>/metadata.json con origin (ad/source_id) + episodes[]."""
    session_dir = vault / f"wa_{phone}"
    session_dir.mkdir(parents=True, exist_ok=True)
    metadata: dict = {
        "origin": {
            "channel": channel,
            "first_seen_ms": first_seen_ms,
            "first_inbound_message_id": f"wamid.{phone}",
            "headline": headline,
            "source_id": source_id,
        },
        "active_route": "ventas",
        "episodes": episodes,
    }
    if registered_order is not None:
        metadata["registered_order"] = registered_order
    (session_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    return session_dir


def _only(campaigns: list[AdsCampaignSummary], camp_id: str) -> AdsCampaignSummary:
    by_id = {c.id: c for c in campaigns}
    assert camp_id in by_id, f"{camp_id} no encontrada en {list(by_id)}"
    return by_id[camp_id]


def test_aggregates_llm_cost_and_tokens_per_campaign(_isolate_vault_dir: Path):
    """`llm_cost_usd`/`llm_tokens` suman `episode.llm_usage` de todos los
    episodios del bucket."""
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep("ep_001", started_at_ms=1, closed_at_ms=2, llm_cost_usd=0.002, llm_tokens=1000),
            _ep("ep_002", started_at_ms=3, llm_cost_usd=0.001, llm_tokens=500),
        ],
    )
    camp = _only(list_ads_campaigns(_isolate_vault_dir), "AD_X")
    assert camp.llm_cost_usd == pytest.approx(0.003)
    assert camp.llm_tokens == 1500


def test_attributed_conversations_scope_matches_any_source_id(
    _isolate_vault_dir: Path,
):
    """Segmentación (2026-07-10): una fila del listado agrupado (campaña o
    adset) abarca VARIOS ads — el scope `source_ids` matchea episodios de
    cualquiera de ellos en una sola llamada."""
    _write_episodic_session(
        _isolate_vault_dir, phone="111", source_id="AD_1",
        episodes=[_ep("ep_001", started_at_ms=100, closed_at_ms=200)],
    )
    _write_episodic_session(
        _isolate_vault_dir, phone="222", source_id="AD_2",
        episodes=[_ep("ep_001", started_at_ms=300, closed_at_ms=400)],
    )
    _write_episodic_session(
        _isolate_vault_dir, phone="333", source_id="AD_OTRA_CAMPANA",
        episodes=[_ep("ep_001", started_at_ms=500, closed_at_ms=600)],
    )
    convs = list_attributed_conversations(
        _isolate_vault_dir, "CAMP_9", source_ids=frozenset({"AD_1", "AD_2"})
    )
    assert {c.phone_number for c in convs} == {"111", "222"}


def test_daily_series_scope_matches_any_source_id(_isolate_vault_dir: Path):
    day_ms = 24 * 60 * 60 * 1000
    now_ms = 1_750_000_000_000
    _write_episodic_session(
        _isolate_vault_dir, phone="111", source_id="AD_1",
        episodes=[_ep("ep_001", started_at_ms=now_ms - day_ms, closed_at_ms=now_ms)],
    )
    _write_episodic_session(
        _isolate_vault_dir, phone="222", source_id="AD_2",
        episodes=[_ep("ep_001", started_at_ms=now_ms - day_ms, closed_at_ms=now_ms)],
    )
    points = list_daily_series(
        _isolate_vault_dir, "CAMP_9", days=3, now_ms=now_ms,
        source_ids=frozenset({"AD_1", "AD_2"}),
    )
    total = sum(
        p.ganado + p.cotizado + p.calificado + p.activo + p.nuevo
        + p.no_reply + p.perdido
        for p in points
    )
    assert total == 2


def test_campaign_exposes_revenue_and_duration_counts(_isolate_vault_dir: Path):
    """Segmentación (2026-07-10): el summary expone `revenue_count` y
    `duration_count` — sin ellos, agrupar buckets por campaña/adset no puede
    recomponer `avg_ticket` ni `avg_episode_duration_ms` exactos (promediar
    promedios miente con buckets de tamaño distinto)."""
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep("ep_001", started_at_ms=100, closed_at_ms=200,
                order_id="o1", order_total_cop=50_000),
            _ep("ep_002", started_at_ms=300, closed_at_ms=700,
                order_id="o2", order_total_cop=30_000),
            _ep("ep_003", started_at_ms=900),  # activo: no aporta a ninguno
        ],
    )
    camp = _only(list_ads_campaigns(_isolate_vault_dir), "AD_X")
    assert camp.revenue_count == 2
    assert camp.duration_count == 2
    # coherencia: avg_ticket == revenue / revenue_count
    assert camp.avg_ticket == round(camp.revenue / camp.revenue_count)


def test_campaign_without_llm_usage_has_none(_isolate_vault_dir: Path):
    """Si ningún episodio acumuló uso LLM → None (distinto de 0)."""
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[_ep("ep_001", started_at_ms=1, closed_at_ms=2)],
    )
    camp = _only(list_ads_campaigns(_isolate_vault_dir), "AD_X")
    assert camp.llm_cost_usd is None
    assert camp.llm_tokens is None


def test_aggregates_revenue_from_frozen_episode_total(_isolate_vault_dir: Path):
    """`revenue` suma `episode.order_total_cop`; `avg_ticket` = revenue / nº de
    episodios que aportaron ingreso."""
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep("ep_001", started_at_ms=1, closed_at_ms=2, order_id="o1", order_total_cop=198000),
            _ep("ep_002", started_at_ms=3, closed_at_ms=4, order_id="o2", order_total_cop=102000),
        ],
    )
    camp = _only(list_ads_campaigns(_isolate_vault_dir), "AD_X")
    assert camp.revenue == 300000
    assert camp.avg_ticket == 150000


def test_revenue_backfills_from_registered_order(_isolate_vault_dir: Path):
    """Episodio ganado SIN `order_total_cop` (venta previa al freeze) →
    backfill desde `registered_order.total_cop` por order_id."""
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[_ep("ep_001", started_at_ms=1, closed_at_ms=2, order_id="ORDER_OLD")],
        registered_order={
            "order_id": "ORDER_OLD",
            "total_cop": 250000,
            "success": True,
        },
    )
    camp = _only(list_ads_campaigns(_isolate_vault_dir), "AD_X")
    assert camp.revenue == 250000


def test_campaign_without_sales_has_none_revenue(_isolate_vault_dir: Path):
    """Sin ventas (episodio rechazado) → revenue/avg_ticket None."""
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep("ep_001", started_at_ms=1, closed_at_ms=2, closing_tag="RECHAZO"),
        ],
    )
    camp = _only(list_ads_campaigns(_isolate_vault_dir), "AD_X")
    assert camp.revenue is None
    assert camp.avg_ticket is None


def test_avg_episode_duration_over_closed_episodes(_isolate_vault_dir: Path):
    """`avg_episode_duration_ms` promedia (closed−started) de los episodios
    CERRADOS; el activo (closed=None) se ignora."""
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep("ep_001", started_at_ms=1000, closed_at_ms=4000),  # 3000
            _ep("ep_002", started_at_ms=5000, closed_at_ms=6000),  # 1000
            _ep("ep_003", started_at_ms=7000),  # activo → ignorado
        ],
    )
    camp = _only(list_ads_campaigns(_isolate_vault_dir), "AD_X")
    assert camp.avg_episode_duration_ms == 2000  # (3000 + 1000) / 2


def test_attributed_conversation_value_and_duration(_isolate_vault_dir: Path):
    """La conversación atribuida expone `value` (order_total_cop) +
    `duration_ms` (closed−started) + costo LLM del episodio."""
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep(
                "ep_001",
                started_at_ms=1000,
                closed_at_ms=4000,
                order_id="o1",
                order_total_cop=198000,
                llm_cost_usd=0.002,
                llm_tokens=900,
            ),
        ],
    )
    convs = list_attributed_conversations(_isolate_vault_dir, "AD_X")
    assert len(convs) == 1
    c = convs[0]
    assert c.value == 198000
    assert c.duration_ms == 3000
    assert c.llm_cost_usd == pytest.approx(0.002)
    assert c.llm_tokens == 900


def test_daily_series_buckets_by_day_and_is_continuous(_isolate_vault_dir: Path):
    """La serie diaria bucketea episodios por día (Bogota), devuelve 14 puntos
    continuos y segmenta por estado."""
    day_ms = 24 * 60 * 60 * 1000
    # Mediodía Bogota (07:00) — lejos de la frontera de medianoche.
    now_ms = 1778889600000 + 12 * 60 * 60 * 1000
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            # ganado HOY
            _ep("ep_001", started_at_ms=now_ms, closed_at_ms=now_ms, order_id="o1", order_total_cop=100000),
            # cotizado hace 2 días
            _ep("ep_002", started_at_ms=now_ms - 2 * day_ms, closed_at_ms=now_ms - 2 * day_ms, closing_tag="CONFIRMADO_SIN_DATOS"),
        ],
    )
    series = list_daily_series(_isolate_vault_dir, "AD_X", days=14, now_ms=now_ms)
    assert len(series) == 14
    assert series[-1].ganado == 1  # hoy
    assert series[-3].cotizado == 1  # hace 2 días
    total = sum(
        p.ganado + p.cotizado + p.calificado + p.activo + p.nuevo + p.no_reply + p.perdido
        for p in series
    )
    assert total == 2
    assert all(isinstance(p.d, str) and p.d for p in series)


def test_daily_series_empty_campaign_is_continuous_zeros(_isolate_vault_dir: Path):
    """Campaña inexistente → serie de 14 puntos en 0 (no rompe, sin huecos)."""
    series = list_daily_series(
        _isolate_vault_dir, "NOPE", days=14, now_ms=1778889600000
    )
    assert len(series) == 14
    assert all(
        p.ganado == 0 and p.cotizado == 0 and p.activo == 0 and p.nuevo == 0
        for p in series
    )


def test_sessions_param_matches_fresh_scan(_isolate_vault_dir: Path):
    """Pasar un scan pre-parseado (`sessions=`) da EXACTAMENTE el mismo
    resultado que escanear fresco en las 3 funciones. Guarda del refactor de
    performance: el cache compartido de la capa API no debe cambiar la data,
    solo evitar releer el vault 3 veces por page-view."""
    from src.plugins.ads.aggregation import (
        scan_ad_sessions,
    )

    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep("ep_001", started_at_ms=1, closed_at_ms=2, order_id="o1", order_total_cop=198000, llm_cost_usd=0.002, llm_tokens=900),
            _ep("ep_002", started_at_ms=3),  # activo
        ],
    )
    scan = scan_ad_sessions(_isolate_vault_dir)
    now = 1779000000000

    assert list_ads_campaigns(_isolate_vault_dir) == list_ads_campaigns(
        _isolate_vault_dir, sessions=scan
    )
    assert list_attributed_conversations(
        _isolate_vault_dir, "AD_X"
    ) == list_attributed_conversations(_isolate_vault_dir, "AD_X", sessions=scan)
    assert list_daily_series(
        _isolate_vault_dir, "AD_X", days=7, now_ms=now
    ) == list_daily_series(_isolate_vault_dir, "AD_X", days=7, now_ms=now, sessions=scan)


# ---------------------------------------------------------------------------
# Filtro por fecha (ventana) — empujado al backend
# ---------------------------------------------------------------------------

_DAY_MS = 24 * 60 * 60 * 1000
_BASE_MS = 1779000000000


def test_since_ms_filters_campaign_episodes_by_start_date(_isolate_vault_dir: Path):
    """`since_ms` recorta la agregación: solo episodios iniciados en/después del
    corte cuentan (revenue/started reflejan la ventana)."""
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep("ep_old", started_at_ms=_BASE_MS - 60 * _DAY_MS, closed_at_ms=_BASE_MS - 60 * _DAY_MS + 1000, order_id="o1", order_total_cop=100000),
            _ep("ep_new", started_at_ms=_BASE_MS - 5 * _DAY_MS, closed_at_ms=_BASE_MS - 5 * _DAY_MS + 1000, order_id="o2", order_total_cop=300000),
        ],
    )
    full = _only(list_ads_campaigns(_isolate_vault_dir), "AD_X")
    assert full.started == 2 and full.revenue == 400000

    since = _BASE_MS - 30 * _DAY_MS
    windowed = _only(list_ads_campaigns(_isolate_vault_dir, since_ms=since), "AD_X")
    assert windowed.started == 1
    assert windowed.revenue == 300000  # solo ep_new


def test_since_ms_excludes_campaign_with_no_in_window_episodes(_isolate_vault_dir: Path):
    """Campaña con TODA su actividad fuera de ventana no aparece."""
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_OLD",
        episodes=[_ep("ep_001", started_at_ms=_BASE_MS - 100 * _DAY_MS, closed_at_ms=_BASE_MS - 100 * _DAY_MS + 1000, order_id="o1", order_total_cop=100000)],
    )
    assert list_ads_campaigns(_isolate_vault_dir, since_ms=_BASE_MS - 30 * _DAY_MS) == []


def test_since_ms_filters_conversations_by_start_date(_isolate_vault_dir: Path):
    """`since_ms` deja solo las conversaciones iniciadas en la ventana."""
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep("ep_old", started_at_ms=_BASE_MS - 60 * _DAY_MS, closed_at_ms=_BASE_MS - 60 * _DAY_MS + 1),
            _ep("ep_new", started_at_ms=_BASE_MS - 5 * _DAY_MS, closed_at_ms=_BASE_MS - 5 * _DAY_MS + 1),
        ],
    )
    convs = list_attributed_conversations(
        _isolate_vault_dir, "AD_X", since_ms=_BASE_MS - 30 * _DAY_MS
    )
    assert len(convs) == 1
    assert convs[0].episode_id == "ep_new"


def test_scan_skips_sessions_untouched_since_via_mtime(_isolate_vault_dir: Path):
    """`scan_ad_sessions(since_ms=...)` saltea (por mtime, sin parsear) sesiones
    cuyo metadata.json no se tocó desde el corte — el skip que hace que el scan
    escale con la ventana, no con todo el historial."""
    import os

    from src.plugins.ads.aggregation import (
        scan_ad_sessions,
    )

    old = _write_episodic_session(
        _isolate_vault_dir, phone="old", source_id="AD_OLD",
        episodes=[_ep("ep_001", started_at_ms=_BASE_MS - 100 * _DAY_MS, closed_at_ms=_BASE_MS - 100 * _DAY_MS + 1)],
    )
    new = _write_episodic_session(
        _isolate_vault_dir, phone="new", source_id="AD_NEW",
        episodes=[_ep("ep_001", started_at_ms=_BASE_MS - 1 * _DAY_MS, closed_at_ms=_BASE_MS - 1 * _DAY_MS + 1)],
    )
    # Forzamos mtime real de los archivos: viejo = hace 100 días, nuevo = ayer.
    old_s = (_BASE_MS - 100 * _DAY_MS) / 1000
    new_s = (_BASE_MS - 1 * _DAY_MS) / 1000
    os.utime(old / "metadata.json", (old_s, old_s))
    os.utime(new / "metadata.json", (new_s, new_s))

    since = _BASE_MS - 30 * _DAY_MS
    scanned = scan_ad_sessions(_isolate_vault_dir, since_ms=since)
    names = {sd.name for sd, _ in scanned}
    assert names == {"wa_new"}  # wa_old salteado por mtime sin parsear

    # Sin since_ms, escanea todas.
    assert {sd.name for sd, _ in scan_ad_sessions(_isolate_vault_dir)} == {"wa_old", "wa_new"}


# ---------------------------------------------------------------------------
# Rango custom (fecha inicio / fecha fin) — `until_ms` (límite superior) +
# ventana diaria explícita. Complementa el filtro `since_ms` de arriba.
# ---------------------------------------------------------------------------


def test_until_ms_filters_campaign_episodes_by_start_date(_isolate_vault_dir: Path):
    """`until_ms` (límite superior EXCLUSIVO) recorta la agregación: los
    episodios iniciados en/después del corte no cuentan."""
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep("ep_early", started_at_ms=_BASE_MS - 20 * _DAY_MS, closed_at_ms=_BASE_MS - 20 * _DAY_MS + 1000, order_id="o1", order_total_cop=100000),
            _ep("ep_late", started_at_ms=_BASE_MS - 2 * _DAY_MS, closed_at_ms=_BASE_MS - 2 * _DAY_MS + 1000, order_id="o2", order_total_cop=300000),
        ],
    )
    windowed = _only(
        list_ads_campaigns(_isolate_vault_dir, until_ms=_BASE_MS - 10 * _DAY_MS), "AD_X"
    )
    assert windowed.started == 1
    assert windowed.revenue == 100000  # solo ep_early (antes del corte superior)


def test_window_since_and_until_bound_campaigns(_isolate_vault_dir: Path):
    """`since_ms` + `until_ms` acotan ambos extremos: solo el episodio dentro de
    [since, until) entra a la agregación."""
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep("ep_before", started_at_ms=_BASE_MS - 40 * _DAY_MS, closed_at_ms=_BASE_MS - 40 * _DAY_MS + 1000, order_id="o1", order_total_cop=100000),
            _ep("ep_in", started_at_ms=_BASE_MS - 20 * _DAY_MS, closed_at_ms=_BASE_MS - 20 * _DAY_MS + 1000, order_id="o2", order_total_cop=200000),
            _ep("ep_after", started_at_ms=_BASE_MS - 2 * _DAY_MS, closed_at_ms=_BASE_MS - 2 * _DAY_MS + 1000, order_id="o3", order_total_cop=300000),
        ],
    )
    camp = _only(
        list_ads_campaigns(
            _isolate_vault_dir,
            since_ms=_BASE_MS - 30 * _DAY_MS,
            until_ms=_BASE_MS - 10 * _DAY_MS,
        ),
        "AD_X",
    )
    assert camp.started == 1
    assert camp.revenue == 200000  # solo ep_in


def test_until_ms_filters_conversations_by_start_date(_isolate_vault_dir: Path):
    """`until_ms` deja fuera las conversaciones iniciadas en/después del corte."""
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep("ep_early", started_at_ms=_BASE_MS - 20 * _DAY_MS, closed_at_ms=_BASE_MS - 20 * _DAY_MS + 1),
            _ep("ep_late", started_at_ms=_BASE_MS - 2 * _DAY_MS, closed_at_ms=_BASE_MS - 2 * _DAY_MS + 1),
        ],
    )
    convs = list_attributed_conversations(
        _isolate_vault_dir, "AD_X", until_ms=_BASE_MS - 10 * _DAY_MS
    )
    assert len(convs) == 1
    assert convs[0].episode_id == "ep_early"


def test_daily_series_custom_window_uses_since_until(_isolate_vault_dir: Path):
    """Con `since_ms`/`until_ms` la serie cubre [from_day, to_day] (ambos
    inclusive) en vez de 'últimos N días terminando hoy', y bucketea por día."""
    from src.plugins.ads.aggregation import (
        bogota_day_start_ms,
    )

    since = bogota_day_start_ms("2026-05-01")
    until = bogota_day_start_ms("2026-05-07") + _DAY_MS  # `to` inclusive → 7 días
    in_window = bogota_day_start_ms("2026-05-03") + 12 * 60 * 60 * 1000  # mediodía Bogota
    out_window = bogota_day_start_ms("2026-05-20") + 12 * 60 * 60 * 1000
    _write_episodic_session(
        _isolate_vault_dir,
        phone="111",
        source_id="AD_X",
        episodes=[
            _ep("ep_in", started_at_ms=in_window, closed_at_ms=in_window, order_id="o1", order_total_cop=100000),
            _ep("ep_out", started_at_ms=out_window, closed_at_ms=out_window, order_id="o2", order_total_cop=200000),
        ],
    )
    series = list_daily_series(
        _isolate_vault_dir, "AD_X", since_ms=since, until_ms=until
    )
    assert len(series) == 7  # 1..7 may inclusive (no "últimos 14 terminando hoy")
    assert series[0].d == "1 may"
    assert series[-1].d == "7 may"
    assert series[2].ganado == 1  # 3 may
    assert sum(p.ganado for p in series) == 1  # ep_out (20 may) fuera de ventana


def test_daily_series_custom_window_clamps_to_90_columns(_isolate_vault_dir: Path):
    """Un rango custom > 90 días se clampa a 90 columnas ancladas al final (`to`)."""
    from src.plugins.ads.aggregation import (
        bogota_day_start_ms,
    )

    since = bogota_day_start_ms("2026-01-01")
    until = bogota_day_start_ms("2026-06-30") + _DAY_MS  # ~180 días
    series = list_daily_series(
        _isolate_vault_dir, "NOPE", since_ms=since, until_ms=until
    )
    assert len(series) == 90
    assert series[-1].d == "30 jun"  # ancla al final del rango


def test_bogota_day_start_ms_roundtrips_with_bogota_date(_isolate_vault_dir: Path):
    """`bogota_day_start_ms` es el inverso de `_bogota_date` y maneja la frontera
    de medianoche Bogota; fecha inválida → None (la API degrada a ventana abierta)."""
    import datetime as dt

    from src.plugins.ads.aggregation import (
        _bogota_date,
        bogota_day_start_ms,
    )

    ms = bogota_day_start_ms("2026-05-15")
    assert ms is not None
    assert _bogota_date(ms) == dt.date(2026, 5, 15)
    assert _bogota_date(ms - 1) == dt.date(2026, 5, 14)  # 1ms antes = día anterior
    assert bogota_day_start_ms("not-a-date") is None


# --- CAPI en tableros (fix 2026-07-01) --------------------------------------


_AD_ORIGIN_CAPI = {
    "channel": "ad",
    "source_id": "AD_CAPI",
    "headline": "Chatea con nosotros",
    "first_seen_ms": 1_714_000_000_000,
}


def _capi_session_extra() -> dict:
    """Sesión con 2 episodios: ep_001 compró (Purchase sent), ep_002 tuvo
    LeadSubmitted fallido. Un LeadSubmitted sent para ep_001 también (fue
    superado por el Purchase — ambos cuentan como enviados)."""
    return {
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": 1_714_000_000_000,
                "closed_at_ms": 1_714_000_100_000,
                "closing_tag": "COMPRA_EXITOSA",
                "order_id": "order_A1",
                "order_total_cop": 80000,
                "referral_snapshot": {"channel": "ad", "source_id": "AD_CAPI"},
            },
            {
                "episode_id": "ep_002",
                "started_at_ms": 1_714_100_000_000,
                "closed_at_ms": 1_714_100_100_000,
                "closing_tag": "CONFIRMADO_PAGO_PENDIENTE",
                "order_id": None,
                "referral_snapshot": {"channel": "ad", "source_id": "AD_CAPI"},
            },
        ],
        "capi_events_sent": [
            {
                "event_id": "lead_wa_573001_ep_001",
                "event_name": "LeadSubmitted",
                "status": "sent",
            },
            {
                "event_id": "purchase_order_A1",
                "event_name": "Purchase",
                "status": "sent",
            },
            {
                "event_id": "lead_wa_573001_ep_002",
                "event_name": "LeadSubmitted",
                "status": "failed_4xx",
            },
        ],
    }


def test_campaign_aggregates_capi_counters(_isolate_vault_dir: Path):
    """Los eventos CAPI del metadata suman al bucket de la campaña:
    sent LeadSubmitted → capi_leads_sent, sent Purchase →
    capi_purchases_sent, failed_* → capi_failed."""
    _write_session(
        _isolate_vault_dir,
        phone="573001",
        origin=_AD_ORIGIN_CAPI,
        extra=_capi_session_extra(),
    )
    campaigns = list_ads_campaigns(_isolate_vault_dir)
    camp = next(c for c in campaigns if c.id == "AD_CAPI")
    assert camp.capi_leads_sent == 1
    assert camp.capi_purchases_sent == 1
    assert camp.capi_failed == 1


def test_campaign_capi_counters_default_zero(_isolate_vault_dir: Path):
    """Campaña sin eventos CAPI → counters en 0 (no None): el frontend
    los muestra siempre y distingue 'sin señal' de 'pendiente'."""
    _write_session(
        _isolate_vault_dir,
        phone="573002",
        origin=_AD_ORIGIN_CAPI,
    )
    campaigns = list_ads_campaigns(_isolate_vault_dir)
    camp = next(c for c in campaigns if c.id == "AD_CAPI")
    assert camp.capi_leads_sent == 0
    assert camp.capi_purchases_sent == 0
    assert camp.capi_failed == 0


def test_conversation_carries_capi_event(_isolate_vault_dir: Path):
    """Cada conversación (episodio) expone su evento CAPI reportado:
    Purchase pisa a LeadSubmitted (terminal); fallidos no cuentan."""
    _write_session(
        _isolate_vault_dir,
        phone="573001",
        origin=_AD_ORIGIN_CAPI,
        extra=_capi_session_extra(),
    )
    convs = list_attributed_conversations(_isolate_vault_dir, "AD_CAPI")
    by_ep = {c.episode_id: c for c in convs}
    assert by_ep["ep_001"].capi_event == "Purchase"
    # ep_002: su LeadSubmitted falló → no se reportó nada a Meta
    assert by_ep["ep_002"].capi_event is None
