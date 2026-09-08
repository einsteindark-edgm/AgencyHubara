"""Outbox CAPI (auditoría 2026-09-08, puntos 1/2/4/6).

Un solo emisor para TODOS los eventos de Meta Conversions API: cualquier
productor (tool del turno, use case del ingest, API humana, worker de
orders) ENCOLA en ``metadata["capi_outbox"]``; ``flush_capi_outbox`` es el
único que habla con Meta, aplica las guardas y persiste el resultado
(incluidos los skips, que antes se perdían en logs).

Política de reintentos (L-1 del ConnectorKit): connect-error = "no se
aplicó" → queda pendiente; read-timeout tras el POST = "DESCONOCIDO" → se
registra como ``unknown`` y NO se reenvía a ciegas (Meta no deduplica
eventos de business messaging).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.platform.whatsapp.capi import (
    CAPI_EVENT_NAMES,
    PRE_PURCHASE_EVENT_NAMES,
    build_capi_event,
    make_event_id,
)
from src.platform.whatsapp.capi_outbox import (
    MAX_FLUSH_ATTEMPTS,
    CapiConfig,
    enqueue_capi_event,
    flush_capi_outbox,
    has_ctwa_attribution,
    pending_capi_events,
)

NOW_MS = 1_757_350_000_000
SESSION = "wa_573001112233"


def _cfg(vault_dir: Path, **over: Any) -> CapiConfig:
    base: dict[str, Any] = {
        "dataset_id": "DS1",
        "access_token": "TOK",
        "waba_id": "WABA1",
        "test_event_code": "",
        "vault_dir": vault_dir,
    }
    base.update(over)
    return CapiConfig(**base)


def _seed(vault_dir: Path, metadata: dict[str, Any]) -> Path:
    path = vault_dir / SESSION / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata), encoding="utf-8")
    return path


def _attributed(**extra: Any) -> dict[str, Any]:
    md: dict[str, Any] = {
        "ctwa_referrals": [{"ctwa_clid": "CLID_1", "captured_at_ms": NOW_MS - 1000}],
        "episodes": [{"episode_id": "ep_001", "started_at_ms": NOW_MS - 5000}],
    }
    md.update(extra)
    return md


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class _FakePoster:
    """Reemplaza el POST HTTP: guion de respuestas por llamada."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, url: str, body: dict[str, Any], token: str) -> httpx.Response:
        self.calls.append({"url": url, "body": body, "token": token})
        nxt = self.script.pop(0) if self.script else httpx.Response(200, json={"events_received": 1})
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


# =============================================================================
# Capa pura: vocabulario de eventos + ids estables
# =============================================================================


class TestEventVocabulary:
    def test_all_fourteen_meta_events_are_allowed(self) -> None:
        assert CAPI_EVENT_NAMES == frozenset(
            {
                "Purchase", "LeadSubmitted", "InitiateCheckout", "AddToCart",
                "ViewContent", "OrderCreated", "OrderShipped", "OrderDelivered",
                "OrderCanceled", "OrderReturned", "CartAbandoned", "QualifiedLead",
                "RatingProvided", "ReviewProvided",
            }
        )

    def test_legacy_ids_are_preserved_for_lead_and_purchase(self) -> None:
        assert make_event_id("LeadSubmitted", session_id=SESSION, episode_id="ep_001") == (
            f"lead_{SESSION}_ep_001"
        )
        assert make_event_id("Purchase", session_id=SESSION, order_id="order_9") == "purchase_order_9"

    def test_generic_ids_are_stable_per_episode_or_order(self) -> None:
        a = make_event_id("ViewContent", session_id=SESSION, episode_id="ep_001")
        b = make_event_id("ViewContent", session_id=SESSION, episode_id="ep_001")
        assert a == b == f"viewcontent_{SESSION}_ep_001"
        assert make_event_id("OrderShipped", session_id=SESSION, order_id="order_9") == (
            "ordershipped_order_9"
        )

    def test_order_scoped_events_require_order_id(self) -> None:
        with pytest.raises(ValueError, match="order_id"):
            make_event_id("OrderShipped", session_id=SESSION, episode_id="ep_001")

    def test_generic_builder_adds_custom_data_only_when_given(self) -> None:
        ev = build_capi_event(
            event_name="AddToCart", event_time=1, event_id="x", waba_id="W",
            ctwa_clid="C", value=45000, currency="COP",
        )
        assert ev.to_dict()["custom_data"] == {"value": 45000, "currency": "COP"}
        bare = build_capi_event(
            event_name="OrderShipped", event_time=1, event_id="y", waba_id="W", ctwa_clid="C"
        )
        assert "custom_data" not in bare.to_dict()
        assert bare.to_dict()["action_source"] == "business_messaging"

    def test_pre_purchase_set_excludes_post_purchase_events(self) -> None:
        assert "OrderShipped" not in PRE_PURCHASE_EVENT_NAMES
        assert {"ViewContent", "AddToCart", "InitiateCheckout", "LeadSubmitted"} <= PRE_PURCHASE_EVENT_NAMES


# =============================================================================
# Encolado (puro, sobre el dict de metadata)
# =============================================================================


class TestEnqueue:
    def test_enqueue_writes_entry_and_dedups_by_event_id(self) -> None:
        md = _attributed()
        eid = enqueue_capi_event(
            md, event_name="ViewContent", session_id=SESSION, episode_id="ep_001",
            source="flush_ui_intents", now_ms=NOW_MS,
        )
        again = enqueue_capi_event(
            md, event_name="ViewContent", session_id=SESSION, episode_id="ep_001",
            source="flush_ui_intents", now_ms=NOW_MS + 5,
        )
        assert eid == f"viewcontent_{SESSION}_ep_001"
        assert again is None
        assert [e["event_id"] for e in pending_capi_events(md)] == [eid]
        entry = pending_capi_events(md)[0]
        assert entry["event_name"] == "ViewContent"
        assert entry["queued_at_ms"] == NOW_MS
        assert entry["source"] == "flush_ui_intents"

    def test_enqueue_skips_event_already_sent(self) -> None:
        md = _attributed(capi_events_sent=[{"event_id": f"viewcontent_{SESSION}_ep_001", "status": "sent"}])
        assert enqueue_capi_event(
            md, event_name="ViewContent", session_id=SESSION, episode_id="ep_001",
            source="t", now_ms=NOW_MS,
        ) is None

    def test_enqueue_rejects_unknown_event_name(self) -> None:
        with pytest.raises(ValueError):
            enqueue_capi_event(
                {}, event_name="Lead", session_id=SESSION, episode_id="ep_001", source="t", now_ms=NOW_MS
            )

    def test_enqueue_without_attribution_is_a_noop(self) -> None:
        # Sesión orgánica: no encolamos (ni ruido en el vault ni HTTP).
        md = {"episodes": [{"episode_id": "ep_001"}]}
        assert has_ctwa_attribution(md) is False
        assert enqueue_capi_event(
            md, event_name="ViewContent", session_id=SESSION, episode_id="ep_001", source="t", now_ms=NOW_MS
        ) is None
        assert "capi_outbox" not in md

    def test_purchase_carries_value_and_currency(self) -> None:
        md = _attributed()
        enqueue_capi_event(
            md, event_name="Purchase", session_id=SESSION, order_id="order_9",
            value=250000, currency="COP", source="confirm_payment", now_ms=NOW_MS,
        )
        entry = pending_capi_events(md)[0]
        assert (entry["value"], entry["currency"], entry["order_id"]) == (250000, "COP", "order_9")


# =============================================================================
# Flush (I/O acotada: metadata.json + POST inyectado)
# =============================================================================


@pytest.mark.asyncio
class TestFlush:
    async def test_sends_pending_in_order_and_persists_outcomes(self, tmp_path: Path) -> None:
        md = _attributed()
        enqueue_capi_event(md, event_name="ViewContent", session_id=SESSION, episode_id="ep_001", source="t", now_ms=NOW_MS)
        enqueue_capi_event(md, event_name="AddToCart", session_id=SESSION, episode_id="ep_001", value=45000, currency="COP", source="t", now_ms=NOW_MS + 1)
        path = _seed(tmp_path, md)
        poster = _FakePoster([
            httpx.Response(200, json={"events_received": 1, "fbtrace_id": "T1"}),
            httpx.Response(200, json={"events_received": 1, "fbtrace_id": "T2"}),
        ])

        result = await flush_capi_outbox(SESSION, config=_cfg(tmp_path), post=poster, now_ms=NOW_MS + 100)

        assert result.sent == 2 and result.failed == 0 and result.skipped == 0 and result.pending == 0
        names = [c["body"]["data"][0]["event_name"] for c in poster.calls]
        assert names == ["ViewContent", "AddToCart"]
        first = poster.calls[0]["body"]["data"][0]
        assert first["user_data"] == {"whatsapp_business_account_id": "WABA1", "ctwa_clid": "CLID_1"}
        assert first["event_time"] == NOW_MS // 1000  # momento en que ocurrió, no el flush
        assert poster.calls[1]["body"]["data"][0]["custom_data"] == {"value": 45000, "currency": "COP"}
        assert poster.calls[0]["url"].endswith("/DS1/events")
        saved = _read(path)
        assert saved["capi_outbox"] == []
        assert [(e["event_name"], e["status"], e["fbtrace_id"]) for e in saved["capi_events_sent"]] == [
            ("ViewContent", "sent", "T1"), ("AddToCart", "sent", "T2"),
        ]

    async def test_skips_are_persisted_not_just_logged(self, tmp_path: Path) -> None:
        # ctwa expirado: antes se perdía en un log; ahora queda en el vault.
        md = _attributed()
        md["ctwa_referrals"][0]["captured_at_ms"] = NOW_MS - 8 * 24 * 3600 * 1000
        enqueue_capi_event(md, event_name="LeadSubmitted", session_id=SESSION, episode_id="ep_001", source="t", now_ms=NOW_MS)
        path = _seed(tmp_path, md)
        poster = _FakePoster([])

        result = await flush_capi_outbox(SESSION, config=_cfg(tmp_path), post=poster, now_ms=NOW_MS)

        assert (result.sent, result.skipped, result.pending) == (0, 1, 0)
        assert poster.calls == []
        saved = _read(path)
        assert saved["capi_outbox"] == []
        assert saved["capi_events_sent"][0]["status"] == "skipped_attribution_expired"
        assert saved["capi_events_sent"][0]["event_name"] == "LeadSubmitted"

    async def test_no_config_skips_everything_and_keeps_vault_clean(self, tmp_path: Path) -> None:
        md = _attributed()
        enqueue_capi_event(md, event_name="ViewContent", session_id=SESSION, episode_id="ep_001", source="t", now_ms=NOW_MS)
        path = _seed(tmp_path, md)
        result = await flush_capi_outbox(SESSION, config=_cfg(tmp_path, dataset_id=""), post=_FakePoster([]), now_ms=NOW_MS)
        assert result.skipped == 1
        assert _read(path)["capi_events_sent"][0]["status"] == "skipped_no_config"

    async def test_terminal_lock_blocks_only_pre_purchase_events(self, tmp_path: Path) -> None:
        md = _attributed(capi_terminal_event="Purchase")
        enqueue_capi_event(md, event_name="LeadSubmitted", session_id=SESSION, episode_id="ep_001", source="t", now_ms=NOW_MS)
        enqueue_capi_event(md, event_name="OrderShipped", session_id=SESSION, order_id="order_9", source="t", now_ms=NOW_MS)
        path = _seed(tmp_path, md)
        poster = _FakePoster([])

        result = await flush_capi_outbox(SESSION, config=_cfg(tmp_path), post=poster, now_ms=NOW_MS)

        assert (result.sent, result.skipped) == (1, 1)
        assert [c["body"]["data"][0]["event_name"] for c in poster.calls] == ["OrderShipped"]
        statuses = {e["event_name"]: e["status"] for e in _read(path)["capi_events_sent"]}
        assert statuses == {"LeadSubmitted": "skipped_terminal_event_reached", "OrderShipped": "sent"}

    async def test_purchase_sent_sets_terminal_lock(self, tmp_path: Path) -> None:
        md = _attributed()
        enqueue_capi_event(md, event_name="Purchase", session_id=SESSION, order_id="order_9", value=250000, currency="COP", source="t", now_ms=NOW_MS)
        path = _seed(tmp_path, md)
        await flush_capi_outbox(SESSION, config=_cfg(tmp_path), post=_FakePoster([]), now_ms=NOW_MS)
        assert _read(path)["capi_terminal_event"] == "Purchase"

    async def test_4xx_is_final_and_recorded(self, tmp_path: Path) -> None:
        md = _attributed()
        enqueue_capi_event(md, event_name="ViewContent", session_id=SESSION, episode_id="ep_001", source="t", now_ms=NOW_MS)
        path = _seed(tmp_path, md)
        poster = _FakePoster([httpx.Response(400, json={"error": {"message": "bad", "fbtrace_id": "TB"}})])
        result = await flush_capi_outbox(SESSION, config=_cfg(tmp_path), post=poster, now_ms=NOW_MS)
        assert (result.failed, result.pending) == (1, 0)
        saved = _read(path)
        assert saved["capi_outbox"] == []
        assert saved["capi_events_sent"][0]["status"] == "failed_4xx"
        assert saved["capi_events_sent"][0]["fbtrace_id"] == "TB"

    async def test_5xx_and_connect_errors_keep_entry_pending_with_attempts(self, tmp_path: Path) -> None:
        md = _attributed()
        enqueue_capi_event(md, event_name="ViewContent", session_id=SESSION, episode_id="ep_001", source="t", now_ms=NOW_MS)
        path = _seed(tmp_path, md)
        poster = _FakePoster([httpx.Response(503, json={"error": "down"})])
        result = await flush_capi_outbox(SESSION, config=_cfg(tmp_path), post=poster, now_ms=NOW_MS)
        assert (result.failed, result.pending) == (1, 1)
        entry = _read(path)["capi_outbox"][0]
        assert entry["attempts"] == 1 and "503" in entry["last_error"]
        assert _read(path).get("capi_events_sent", []) == []  # todavía no es final

        poster2 = _FakePoster([httpx.ConnectError("dns")])
        result2 = await flush_capi_outbox(SESSION, config=_cfg(tmp_path), post=poster2, now_ms=NOW_MS)
        assert result2.pending == 1
        assert _read(path)["capi_outbox"][0]["attempts"] == 2

        # Y cuando Meta vuelve, se manda (mismo event_id, un solo envío).
        poster3 = _FakePoster([])
        result3 = await flush_capi_outbox(SESSION, config=_cfg(tmp_path), post=poster3, now_ms=NOW_MS)
        assert (result3.sent, result3.pending) == (1, 0)

    async def test_gives_up_after_max_attempts(self, tmp_path: Path) -> None:
        md = _attributed()
        enqueue_capi_event(md, event_name="ViewContent", session_id=SESSION, episode_id="ep_001", source="t", now_ms=NOW_MS)
        md["capi_outbox"][0]["attempts"] = MAX_FLUSH_ATTEMPTS - 1
        path = _seed(tmp_path, md)
        await flush_capi_outbox(SESSION, config=_cfg(tmp_path), post=_FakePoster([httpx.Response(502)]), now_ms=NOW_MS)
        saved = _read(path)
        assert saved["capi_outbox"] == []
        assert saved["capi_events_sent"][0]["status"] == "failed_gave_up"

    async def test_ambiguous_timeout_after_post_is_unknown_and_never_resent(self, tmp_path: Path) -> None:
        """L-1: un read-timeout NO prueba que Meta no lo recibió. Meta no
        deduplica business messaging → reenviar un Purchase duplicaría
        revenue. Se registra `unknown` y se deja de intentar."""
        md = _attributed()
        enqueue_capi_event(md, event_name="Purchase", session_id=SESSION, order_id="order_9", value=250000, currency="COP", source="t", now_ms=NOW_MS)
        path = _seed(tmp_path, md)
        poster = _FakePoster([httpx.ReadTimeout("slow")])
        result = await flush_capi_outbox(SESSION, config=_cfg(tmp_path), post=poster, now_ms=NOW_MS)
        assert (result.failed, result.pending) == (1, 0)
        saved = _read(path)
        assert saved["capi_outbox"] == []
        assert saved["capi_events_sent"][0]["status"] == "unknown"
        assert "capi_terminal_event" not in saved

        # Re-encolar el mismo event_id no vuelve a mandar.
        md2 = _read(path)
        assert enqueue_capi_event(md2, event_name="Purchase", session_id=SESSION, order_id="order_9", value=250000, currency="COP", source="t", now_ms=NOW_MS) is None

    async def test_empty_outbox_is_cheap_and_touches_nothing(self, tmp_path: Path) -> None:
        path = _seed(tmp_path, _attributed())
        before = path.read_text(encoding="utf-8")
        result = await flush_capi_outbox(SESSION, config=_cfg(tmp_path), post=_FakePoster([]), now_ms=NOW_MS)
        assert (result.sent, result.pending) == (0, 0)
        assert path.read_text(encoding="utf-8") == before

    async def test_missing_metadata_is_a_noop(self, tmp_path: Path) -> None:
        result = await flush_capi_outbox("wa_nadie", config=_cfg(tmp_path), post=_FakePoster([]), now_ms=NOW_MS)
        assert (result.sent, result.pending, result.skipped) == (0, 0, 0)

    async def test_test_event_code_goes_in_body_root(self, tmp_path: Path) -> None:
        md = _attributed()
        enqueue_capi_event(md, event_name="ViewContent", session_id=SESSION, episode_id="ep_001", source="t", now_ms=NOW_MS)
        _seed(tmp_path, md)
        poster = _FakePoster([])
        await flush_capi_outbox(SESSION, config=_cfg(tmp_path, test_event_code="TEST123"), post=poster, now_ms=NOW_MS)
        assert poster.calls[0]["body"]["test_event_code"] == "TEST123"
