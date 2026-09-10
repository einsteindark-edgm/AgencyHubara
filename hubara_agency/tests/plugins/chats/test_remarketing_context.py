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
    sid = "wa_573114842180"
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
    )


@pytest.mark.asyncio
async def test_activity_tolerates_missing_files_and_corrupt_lines(_isolate_vault_dir: Path) -> None:
    sid = "wa_nada"
    assert await ActivityEnvironment().run(read_remarketing_context_activity, sid) == RemarketingContext()
    d = _isolate_vault_dir / sid / "sessions"
    d.mkdir(parents=True)
    (d / f"{sid}.jsonl").write_text('{"role": "user", "content": "hola"}\n{corrupto\n', encoding="utf-8")
    ctx = await ActivityEnvironment().run(read_remarketing_context_activity, sid)
    assert ctx.transcript == "Cliente: hola"
