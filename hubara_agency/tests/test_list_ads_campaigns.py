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

from src.plugins.chats.agent.sales.use_cases.list_ads_campaigns import (
    AdsAttributedConversation,
    AdsCampaignSummary,
    list_ads_campaigns,
    list_attributed_conversations,
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


def test_ignores_direct_origin(_isolate_vault_dir: Path):
    """Sesiones con origin.channel='direct' no son campañas — quedan fuera."""
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
    assert list_ads_campaigns(_isolate_vault_dir) == []


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
    # Campos sin data
    assert c.name is None
    assert c.city is None
    assert c.state is None
    assert c.value is None


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
