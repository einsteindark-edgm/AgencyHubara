"""Contexto real del gancho de remarketing (incidente run dc32f7fe, 2026-09-10).

El agente de remarketing no ve el historial de Sales (HistoryStore aislado por
workspace) y el Window Strategist pisa el `motivo` del tag con un string
genérico. `read_remarketing_context_activity` lee del vault lo que el gancho
necesita: motivo del tag, si hay pedido a medias, últimos mensajes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.remarketing.activities.context import (
    read_remarketing_context_activity,
)
from src.plugins.chats.agent.remarketing.contracts import RemarketingContext
from src.plugins.chats.agent.remarketing.use_cases.context import (
    context_from_metadata,
    render_transcript,
)


def _ev(role: str, content: str, **extra) -> dict:
    return {"role": role, "content": content, "timestamp": "2026-09-10T02:26:00+00:00", **extra}


class TestRenderTranscript:
    def test_renders_roles_in_order_and_keeps_only_last_n(self) -> None:
        events = [_ev("user", f"u{i}") if i % 2 == 0 else _ev("assistant", f"a{i}") for i in range(20)]
        out = render_transcript(events, limit=4)
        assert out == "Cliente: u16\nAsesor: a17\nCliente: u18\nAsesor: a19"

    def test_skips_empty_and_tool_only_assistant_turns(self) -> None:
        events = [
            _ev("user", "Precio de la cera"),
            _ev("assistant", "", tool_calls=[{"name": "search_products"}]),
            _ev("assistant", "La cera no se vende aparte"),
            _ev("user", "   "),
            _ev("user", "Gracias"),
        ]
        assert render_transcript(events) == (
            "Cliente: Precio de la cera\nAsesor: La cera no se vende aparte\nCliente: Gracias"
        )

    def test_human_operator_is_labelled(self) -> None:
        events = [_ev("assistant", "Te confirmo el envío", sender="human")]
        assert render_transcript(events) == "Asesor (humano): Te confirmo el envío"

    def test_multiline_messages_collapse_to_one_line(self) -> None:
        events = [_ev("assistant", "Hola\n\nBienvenido")]
        assert render_transcript(events) == "Asesor: Hola Bienvenido"


class TestContextFromMetadata:
    def test_motivo_and_draft_from_metadata(self) -> None:
        metadata = {
            "tag": "INTERESADO",
            "motivo": "dudó del envío",
            "episodes": [{"episode_id": "ep_001", "order_draft": {"slots": {"producto": "Cubo Love"}}}],
        }
        ctx = context_from_metadata(metadata, [_ev("user", "hola")])
        assert ctx == RemarketingContext(
            tag_motivo="dudó del envío", has_order_draft=True, transcript="Cliente: hola"
        )

    def test_no_metadata_no_events_is_empty_context(self) -> None:
        assert context_from_metadata(None, []) == RemarketingContext()

    def test_draft_without_slots_is_not_a_draft(self) -> None:
        metadata = {"motivo": "x", "episodes": [{"episode_id": "ep_001", "order_draft": {"slots": {}}}]}
        assert context_from_metadata(metadata, []).has_order_draft is False


@pytest.mark.asyncio
async def test_activity_reads_vault_metadata_and_transcript(_isolate_vault_dir: Path) -> None:
    sid = "wa_573000000005"
    d = _isolate_vault_dir / sid
    (d / "sessions").mkdir(parents=True)
    (d / "metadata.json").write_text(
        json.dumps({"tag": "INTERESADO", "motivo": "vio la lista y agradeció", "episodes": [{"episode_id": "ep_001"}]}),
        encoding="utf-8",
    )
    lines = [_ev("user", "Precio de la cera"), _ev("assistant", "La cera no se vende aparte"), _ev("user", "Gracias")]
    (d / "sessions" / f"{sid}.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in lines) + "\n", encoding="utf-8"
    )

    ctx = await ActivityEnvironment().run(read_remarketing_context_activity, sid)

    assert ctx == RemarketingContext(
        tag_motivo="vio la lista y agradeció",
        has_order_draft=False,
        transcript="Cliente: Precio de la cera\nAsesor: La cera no se vende aparte\nCliente: Gracias",
        # Escalera (2026-09-18): la activity digiere qué toque es — sin toques
        # registrados, este sería el primero. Sin last_inbound no hay silencio.
        touch_number=1,
    )


@pytest.mark.asyncio
async def test_activity_tolerates_missing_files_and_corrupt_lines(_isolate_vault_dir: Path) -> None:
    sid = "wa_nada"
    assert await ActivityEnvironment().run(read_remarketing_context_activity, sid) == RemarketingContext(touch_number=1)
    d = _isolate_vault_dir / sid / "sessions"
    d.mkdir(parents=True)
    (d / f"{sid}.jsonl").write_text('{"role": "user", "content": "hola"}\n{corrupto\n', encoding="utf-8")
    ctx = await ActivityEnvironment().run(read_remarketing_context_activity, sid)
    assert ctx.transcript == "Cliente: hola"


# --- Episodio abierto por una campaña (runs edbb0d8b / 8e73b7dc) ------------

_CAMPAIGN = {
    "campaign_id": "mkt-amor",
    "campaign_name": "Amor y amistad",
    "sent_at_ms": 1,
    "message": "Dale a tu cuerpo alegría. Usa el código AMOR26 al pagar.",
    "coupon_code": "AMOR26",
    "product_handles": ["velon-amor-eterno", "cubo-love"],
}


def _campaign_session() -> tuple[dict, list[dict]]:
    """El caso real: 3 mensajes del episodio de la Trilogía, la plantilla de la
    campaña y "Me gusta" como primer mensaje del episodio nuevo."""
    events = [
        _ev("user", "Quiero la Trilogía del Terror"),
        _ev("assistant", "Quedó pendiente tu Trilogía del Terror. ¿La cerramos?"),
        _ev("assistant", "¡Hola! Amor y amistad. Usa el código AMOR26 al pagar.", kind="template"),
        _ev("user", "Me gusta"),
    ]
    metadata = {
        "tag": "INTERESADO",
        "motivo": "Mostró interés en la campaña Amor y amistad.",
        "episodes": [
            {"episode_id": "ep_004", "closed_at_ms": 5, "closing_tag": "CAMPAIGN_REPLY"},
            {
                "episode_id": "ep_005",
                "closed_at_ms": None,
                "msgs_count_at_start": 3,
                "opened_by_campaign": _CAMPAIGN,
            },
        ],
    }
    return metadata, events


class TestCampaignEpisode:
    def test_transcript_only_covers_the_active_episode(self) -> None:
        metadata, events = _campaign_session()
        ctx = context_from_metadata(metadata, events)
        assert ctx.transcript == "Cliente: Me gusta"
        assert "Trilogía" not in ctx.transcript

    def test_campaign_that_opened_the_episode_travels_to_the_hook(self) -> None:
        metadata, events = _campaign_session()
        ctx = context_from_metadata(metadata, events)
        assert "Amor y amistad" in ctx.campaign_context
        assert "AMOR26" in ctx.campaign_context
        assert "velon-amor-eterno" in ctx.campaign_context
        assert "Dale a tu cuerpo alegría" in ctx.campaign_context

    def test_episode_without_start_index_keeps_the_legacy_tail(self) -> None:
        metadata, events = _campaign_session()
        metadata["episodes"][-1].pop("msgs_count_at_start")
        metadata["episodes"][-1].pop("opened_by_campaign")
        ctx = context_from_metadata(metadata, events)
        assert "Trilogía" in ctx.transcript
        assert ctx.campaign_context == ""


def test_trigger_centers_the_hook_on_the_campaign() -> None:
    from src.plugins.chats.agent.remarketing.prompts import build_remarketing_trigger

    trigger = build_remarketing_trigger(
        "Mostró interés.",
        has_order_draft=False,
        transcript="Cliente: Me gusta",
        touch_number=1,
        campaign_context="Campaña «Amor y amistad» — cupón AMOR26",
    )
    assert "Campaña «Amor y amistad» — cupón AMOR26" in trigger
    assert "CAMPAÑA" in trigger
    assert "NO retomes" in trigger


def test_trigger_without_campaign_is_unchanged() -> None:
    from src.plugins.chats.agent.remarketing.prompts import build_remarketing_trigger

    kwargs = dict(has_order_draft=False, transcript="Cliente: hola", touch_number=1)
    assert build_remarketing_trigger("m", **kwargs) == build_remarketing_trigger(
        "m", campaign_context="", **kwargs
    )
