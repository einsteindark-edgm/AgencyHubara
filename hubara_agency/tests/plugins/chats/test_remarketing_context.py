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


# ── Motor de decisiones (F8, capacidad `contactar`) ──


async def test_the_context_says_whether_the_touch_is_not_needed(_isolate_vault_dir, monkeypatch) -> None:
    """Con el bot B, Jev lee la conversación antes de redactar: si el toque
    sobra, el contexto lo dice (`skip_touch`) y el workflow no gasta el
    turno del LLM. Con el bot de hoy, `skip_touch` es False (el LLM decide)."""
    import json as _json

    from temporalio.testing import ActivityEnvironment

    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.plugins.chats.agent.remarketing.activities import context as ctx_mod
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    sid = "wa_573001234567"
    session = _isolate_vault_dir / sid
    (session / "sessions").mkdir(parents=True)
    (session / "metadata.json").write_text(_json.dumps({"tag": "INTERESADO"}), encoding="utf-8")
    lines = [{"role": "user", "content": "Ya les hice el pedido por la web, gracias"},
             {"role": "assistant", "content": "¡Gracias a ti! 🤍"}]
    (session / "sessions" / f"{sid}.jsonl").write_text("\n".join(_json.dumps(x) for x in lines), encoding="utf-8")

    async def _no_catalog() -> list:
        return []

    monkeypatch.setattr(ctx_mod, "_catalog_products", _no_catalog)
    # Sin el motor conectado (worker viejo), no hay decisión: el LLM decide.
    from src.plugins.chats.shared import agent_decisions

    monkeypatch.setattr(agent_decisions, "_contact_decider", None)  # se restaura al terminar
    assert (await ActivityEnvironment().run(ctx_mod.read_remarketing_context_activity, sid)).skip_touch is False
    # El worker de remarketing conecta el motor de decisiones al arrancar.
    from src.plugins.chats.agent.sales.decisions.contact import register_contact_decision

    register_contact_decision()
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    today = await ActivityEnvironment().run(ctx_mod.read_remarketing_context_activity, sid)

    monkeypatch.setenv("DECISIONS_BOT", "B")
    fake = FakePerceptionAdapter({"contactar.sobra": TypedAnswer(id="contactar.sobra", kind="noul", p=0.95)})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    with_jev = await ActivityEnvironment().run(ctx_mod.read_remarketing_context_activity, sid)

    assert today.skip_touch is False and today.contact == {}, "con reglas el contexto grabado es el de hoy"
    assert with_jev.skip_touch is True and with_jev.contact.get("by") == "jev"


# --- Lo que ve `contactar` antes de redactar (2026-09-30) --------------------
#
# El operador: remarketing no reconoce que la conversación ya terminó. El
# gancho ve solo el episodio activo; si ese episodio lo abrió un «gracias» al
# aviso del ETA («tu pedido fue entregado»), `contactar` no veía ni el aviso ni
# que la conversación anterior terminó en una compra, y un «Ya lo recibí.
# Muchas gracias» parecía una conversación abierta.

_PURCHASE = {"episode_id": "ep_001", "closed_at_ms": 5, "closing_tag": "COMPRA_EXITOSA", "order_id": "order_X"}
_DELIVERED = "¡Tu pedido #47 fue entregado! 🎉 Esperamos que lo disfrutes."


def _after_delivery() -> tuple[dict, list[dict]]:
    events = [
        _ev("user", "Quiero el Velón Gorrión"),
        _ev("assistant", "Listo, tu pedido quedó registrado 🤍"),
        _ev("assistant", _DELIVERED),
        _ev("user", "Ya lo recibí. Muchas gracias 💪"),
        _ev("assistant", "Qué alegría, que las disfrutes mucho 🤍"),
    ]
    metadata = {
        "tag": "INTERESADO",
        "episodes": [_PURCHASE, {"episode_id": "ep_002", "closed_at_ms": None, "msgs_count_at_start": 3}],
    }
    return metadata, events


class TestContactTranscript:
    def test_it_shows_how_the_previous_conversation_ended_and_what_the_customer_answered(self) -> None:
        from src.plugins.chats.agent.remarketing.use_cases.context import contact_transcript

        metadata, events = _after_delivery()

        assert contact_transcript(metadata, events).splitlines() == [
            "(Antes de esto, la conversación anterior terminó en una compra.)",
            "Asesor: Listo, tu pedido quedó registrado 🤍",
            f"Asesor: {_DELIVERED}",
            "Cliente: Ya lo recibí. Muchas gracias 💪",
            "Asesor: Qué alegría, que las disfrutes mucho 🤍",
        ]

    def test_without_a_previous_conversation_it_is_the_hook_transcript(self) -> None:
        from src.plugins.chats.agent.remarketing.use_cases.context import contact_transcript

        events = [_ev("user", "Hola"), _ev("assistant", "¡Hola! ¿Qué estás buscando?")]
        metadata = {"episodes": [{"episode_id": "ep_001", "closed_at_ms": None, "msgs_count_at_start": 0}]}

        assert contact_transcript(metadata, events) == render_transcript(events)


@pytest.mark.asyncio
async def test_contactar_decides_with_the_previous_purchase_in_view(_isolate_vault_dir: Path) -> None:
    from src.plugins.chats.shared.agent_decisions import clear_contact_decider, register_contact_decider

    seen: dict = {}

    async def decider(**kw):
        seen.update(kw)
        return True, {"capability": "contactar"}

    sid = "wa_573001234567"
    metadata, events = _after_delivery()
    session = _isolate_vault_dir / sid
    (session / "sessions").mkdir(parents=True)
    (session / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (session / "sessions" / f"{sid}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    register_contact_decider(decider)
    try:
        context = await ActivityEnvironment().run(read_remarketing_context_activity, sid)
    finally:
        clear_contact_decider()

    assert "terminó en una compra" in seen.get("transcript", "") and _DELIVERED in seen.get("transcript", "")
    assert _DELIVERED not in context.transcript  # el gancho sigue viendo solo el episodio
    assert context.skip_touch is True
