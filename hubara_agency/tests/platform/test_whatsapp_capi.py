"""Tests del módulo CAPI puro + activity Temporal.

Cobertura:
  * `capi.py` puro — builders, guards, request body envelope.
  * `capi_activity.py` — activity con mocks de httpx, validando todos los
    short-circuits (no_config / no_ctwa / expired / terminal / dedup) y
    los paths felices y error (200 / 4xx / 5xx / network).

No requiere worker Temporal — la activity se llama directamente
(`@activity.defn` es metadata, el callable subyacente sigue invocable).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from temporalio.exceptions import ApplicationError

from src.platform.whatsapp.capi import (
    ACTION_SOURCE,
    ALLOWED_EVENT_NAMES,
    CTWA_ATTRIBUTION_WINDOW_MS,
    DEFAULT_CURRENCY,
    MESSAGING_CHANNEL,
    META_CAPI_API_URL,
    build_capi_request_body,
    build_lead_event,
    build_purchase_event,
    is_ctwa_clid_within_attribution_window,
    make_event_id_for_lead,
    make_event_id_for_purchase,
    validate_event_name,
)


# =============================================================================
# Pure layer (capi.py) — builders & guards
# =============================================================================


class TestBuildLeadEvent:
    def test_shape_locked_constants(self) -> None:
        event = build_lead_event(
            event_time=1700000000,
            event_id="lead_wa_+57_ep_1",
            waba_id="WABA123",
            ctwa_clid="CLID_X",
        )
        payload = event.to_dict()
        assert payload["event_name"] == "LeadSubmitted"
        assert payload["event_time"] == 1700000000
        assert payload["event_id"] == "lead_wa_+57_ep_1"
        assert payload["action_source"] == ACTION_SOURCE == "business_messaging"
        assert payload["messaging_channel"] == MESSAGING_CHANNEL == "whatsapp"
        assert payload["user_data"]["whatsapp_business_account_id"] == "WABA123"
        assert payload["user_data"]["ctwa_clid"] == "CLID_X"

    def test_no_custom_data_for_lead(self) -> None:
        event = build_lead_event(
            event_time=1700000000,
            event_id="lead_x",
            waba_id="W",
            ctwa_clid="C",
        )
        payload = event.to_dict()
        # Lead doesn't carry monetary signal → no custom_data key emitted
        assert "custom_data" not in payload


class TestBuildPurchaseEvent:
    def test_shape_with_value_and_currency(self) -> None:
        event = build_purchase_event(
            event_time=1700000000,
            event_id="purchase_order_42",
            waba_id="WABA123",
            ctwa_clid="CLID_X",
            value=80000,
            currency="COP",
        )
        payload = event.to_dict()
        assert payload["event_name"] == "Purchase"
        assert payload["custom_data"] == {"value": 80000, "currency": "COP"}

    def test_default_currency_is_cop(self) -> None:
        event = build_purchase_event(
            event_time=1700000000,
            event_id="purchase_x",
            waba_id="W",
            ctwa_clid="C",
            value=1000,
        )
        assert event.custom_data.currency == DEFAULT_CURRENCY == "COP"

    def test_value_must_be_positive_int(self) -> None:
        with pytest.raises(ValueError, match="positive int"):
            build_purchase_event(
                event_time=1700000000,
                event_id="x",
                waba_id="W",
                ctwa_clid="C",
                value=0,
            )
        with pytest.raises(ValueError, match="positive int"):
            build_purchase_event(
                event_time=1700000000,
                event_id="x",
                waba_id="W",
                ctwa_clid="C",
                value=-100,
            )

    def test_value_must_be_int_not_float(self) -> None:
        with pytest.raises(ValueError, match="positive int"):
            build_purchase_event(
                event_time=1700000000,
                event_id="x",
                waba_id="W",
                ctwa_clid="C",
                value=80000.5,  # type: ignore[arg-type]
            )

    def test_currency_must_be_3_letter_iso(self) -> None:
        with pytest.raises(ValueError, match="3-letter ISO"):
            build_purchase_event(
                event_time=1700000000,
                event_id="x",
                waba_id="W",
                ctwa_clid="C",
                value=1000,
                currency="DOLLARS",
            )
        with pytest.raises(ValueError, match="3-letter ISO"):
            build_purchase_event(
                event_time=1700000000,
                event_id="x",
                waba_id="W",
                ctwa_clid="C",
                value=1000,
                currency="",
            )


class TestEventIdStability:
    def test_lead_event_id_stable(self) -> None:
        a = make_event_id_for_lead(session_id="wa_+573001234567", episode_id="ep_42")
        b = make_event_id_for_lead(session_id="wa_+573001234567", episode_id="ep_42")
        assert a == b == "lead_wa_+573001234567_ep_42"

    def test_purchase_event_id_stable(self) -> None:
        a = make_event_id_for_purchase(order_id="order_01HXYZ")
        b = make_event_id_for_purchase(order_id="order_01HXYZ")
        assert a == b == "purchase_order_01HXYZ"


class TestBuildCapiRequestBody:
    def test_envelopes_in_data_array(self) -> None:
        event = build_lead_event(
            event_time=1700000000, event_id="x", waba_id="W", ctwa_clid="C"
        )
        body = build_capi_request_body(event)
        assert "data" in body
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 1
        assert body["data"][0]["event_name"] == "LeadSubmitted"

    def test_test_event_code_at_root_not_in_data(self) -> None:
        # Critical gotcha: Meta expects test_event_code at body root, NOT
        # inside data[]. Common mistake.
        event = build_lead_event(
            event_time=1700000000, event_id="x", waba_id="W", ctwa_clid="C"
        )
        body = build_capi_request_body(event, test_event_code="TEST12345")
        assert body["test_event_code"] == "TEST12345"
        assert "test_event_code" not in body["data"][0]

    def test_test_event_code_omitted_when_none(self) -> None:
        event = build_lead_event(
            event_time=1700000000, event_id="x", waba_id="W", ctwa_clid="C"
        )
        body = build_capi_request_body(event, test_event_code=None)
        assert "test_event_code" not in body


class TestAttributionWindow:
    def test_within_window_returns_true(self) -> None:
        now_ms = 1700000000000
        received_ms = now_ms - (3 * 24 * 60 * 60 * 1000)  # 3 days ago
        assert is_ctwa_clid_within_attribution_window(
            received_at_ms=received_ms, now_ms=now_ms
        )

    def test_at_exact_boundary_returns_false(self) -> None:
        # Strict `<` — at exact 7d the comparison fails (better skip than
        # waste HTTP call at boundary).
        now_ms = 1700000000000
        received_ms = now_ms - CTWA_ATTRIBUTION_WINDOW_MS
        assert not is_ctwa_clid_within_attribution_window(
            received_at_ms=received_ms, now_ms=now_ms
        )

    def test_just_past_window_returns_false(self) -> None:
        now_ms = 1700000000000
        received_ms = now_ms - CTWA_ATTRIBUTION_WINDOW_MS - 1
        assert not is_ctwa_clid_within_attribution_window(
            received_at_ms=received_ms, now_ms=now_ms
        )

    def test_window_is_7_days_in_ms(self) -> None:
        assert CTWA_ATTRIBUTION_WINDOW_MS == 7 * 24 * 60 * 60 * 1000


class TestValidateEventName:
    def test_accepts_lead(self) -> None:
        validate_event_name("LeadSubmitted")  # no raise

    def test_accepts_purchase(self) -> None:
        validate_event_name("Purchase")

    def test_rejects_addtocart(self) -> None:
        # Critical: AddToCart isn't supported in business_messaging action.
        with pytest.raises(ValueError, match="not supported"):
            validate_event_name("AddToCart")

    def test_rejects_initiatecheckout(self) -> None:
        with pytest.raises(ValueError, match="not supported"):
            validate_event_name("InitiateCheckout")

    def test_rejects_lowercase(self) -> None:
        # Meta is case-sensitive; "lead" isn't a valid name.
        with pytest.raises(ValueError, match="not supported"):
            validate_event_name("lead")

    def test_allowed_set_locked(self) -> None:
        assert ALLOWED_EVENT_NAMES == frozenset({"LeadSubmitted", "Purchase"})

    def test_rejects_legacy_lead_name(self) -> None:
        # Bug real cazado por smoke test 2026-07-01 (error_subcode 2804066):
        # Meta rechaza "Lead" para action_source=business_messaging — el
        # nombre valido es "LeadSubmitted". La capa pura es estricta; la
        # normalizacion legacy vive en la activity (workflows en vuelo).
        with pytest.raises(ValueError, match="not supported"):
            validate_event_name("Lead")


class TestMetaCapiApiUrl:
    def test_url_template_format(self) -> None:
        url = META_CAPI_API_URL.format(dataset_id="1234567890")
        assert url == "https://graph.facebook.com/v18.0/1234567890/events"


# =============================================================================
# Activity layer (capi_activity.py) — mocked HTTP + metadata.json
# =============================================================================


@pytest.fixture
def vault_session(tmp_path: Path) -> tuple[Path, str]:
    """Provision a vault dir with a session_id ready for metadata.json."""
    session_id = "wa_+573001234567"
    (tmp_path / session_id).mkdir(parents=True)
    return tmp_path, session_id


def _seed_metadata(
    vault_dir: Path,
    session_id: str,
    *,
    ctwa_referrals: list[dict[str, Any]] | None = None,
    registered_order: dict[str, Any] | None = None,
    capi_terminal_event: str | None = None,
    capi_events_sent: list[dict[str, Any]] | None = None,
) -> None:
    """Write metadata.json with the relevant CAPI-adjacent fields."""
    data: dict[str, Any] = {}
    if ctwa_referrals is not None:
        data["ctwa_referrals"] = ctwa_referrals
    if registered_order is not None:
        data["registered_order"] = registered_order
    if capi_terminal_event is not None:
        data["capi_terminal_event"] = capi_terminal_event
    if capi_events_sent is not None:
        data["capi_events_sent"] = capi_events_sent
    (vault_dir / session_id / "metadata.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


def _read_metadata(vault_dir: Path, session_id: str) -> dict[str, Any]:
    path = vault_dir / session_id / "metadata.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class TestActivityShortCircuits:
    """Skips that don't make HTTP calls — defensive guards before POST."""

    @pytest.mark.asyncio
    async def test_skipped_no_config_when_dataset_id_missing(
        self, vault_session: tuple[Path, str]
    ) -> None:
        vault_dir, session_id = vault_session
        from src.platform.whatsapp import capi_activity

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="",
            META_CAPI_ACCESS_TOKEN="TOKEN",
            WHATSAPP_BUSINESS_ACCOUNT_ID="WABA",
            WORKSPACE_VAULT_DIR=vault_dir,
        ):
            result = await capi_activity.send_capi_event_activity(
                session_id, "ep_1", "LeadSubmitted"
            )
        assert result.status == "skipped_no_config"

    @pytest.mark.asyncio
    async def test_legacy_lead_name_normalized_to_lead_submitted(
        self, vault_session: tuple[Path, str]
    ) -> None:
        """Workflows en vuelo agendaron la activity con "Lead" (pre-fix).
        La activity normaliza el nombre legacy en el boundary — no crashea
        y el resultado reporta el nombre nuevo."""
        vault_dir, session_id = vault_session
        from src.platform.whatsapp import capi_activity

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="",
            META_CAPI_ACCESS_TOKEN="",
            WHATSAPP_BUSINESS_ACCOUNT_ID="WABA",
            WORKSPACE_VAULT_DIR=vault_dir,
        ):
            result = await capi_activity.send_capi_event_activity(
                session_id, "ep_1", "Lead"
            )
        assert result.status == "skipped_no_config"
        assert result.event_name == "LeadSubmitted"

    @pytest.mark.asyncio
    async def test_skipped_no_config_when_token_missing(
        self, vault_session: tuple[Path, str]
    ) -> None:
        vault_dir, session_id = vault_session
        from src.platform.whatsapp import capi_activity

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="DS",
            META_CAPI_ACCESS_TOKEN="",
            WHATSAPP_BUSINESS_ACCOUNT_ID="WABA",
            WORKSPACE_VAULT_DIR=vault_dir,
        ):
            result = await capi_activity.send_capi_event_activity(
                session_id, "ep_1", "LeadSubmitted"
            )
        assert result.status == "skipped_no_config"

    @pytest.mark.asyncio
    async def test_skipped_no_waba_id(
        self, vault_session: tuple[Path, str]
    ) -> None:
        vault_dir, session_id = vault_session
        from src.platform.whatsapp import capi_activity

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="DS",
            META_CAPI_ACCESS_TOKEN="TOKEN",
            WHATSAPP_BUSINESS_ACCOUNT_ID="",
            WORKSPACE_VAULT_DIR=vault_dir,
        ):
            result = await capi_activity.send_capi_event_activity(
                session_id, "ep_1", "LeadSubmitted"
            )
        assert result.status == "skipped_no_waba_id"

    @pytest.mark.asyncio
    async def test_skipped_no_metadata(
        self, tmp_path: Path
    ) -> None:
        # Session dir not even created → metadata absent.
        from src.platform.whatsapp import capi_activity

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="DS",
            META_CAPI_ACCESS_TOKEN="TOKEN",
            WHATSAPP_BUSINESS_ACCOUNT_ID="WABA",
            WORKSPACE_VAULT_DIR=tmp_path,
        ):
            result = await capi_activity.send_capi_event_activity(
                "wa_+57missing", "ep_1", "LeadSubmitted"
            )
        assert result.status == "skipped_no_metadata"

    @pytest.mark.asyncio
    async def test_skipped_no_ctwa_clid_when_referrals_empty(
        self, vault_session: tuple[Path, str]
    ) -> None:
        vault_dir, session_id = vault_session
        _seed_metadata(vault_dir, session_id, ctwa_referrals=[])
        from src.platform.whatsapp import capi_activity

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="DS",
            META_CAPI_ACCESS_TOKEN="TOKEN",
            WHATSAPP_BUSINESS_ACCOUNT_ID="WABA",
            WORKSPACE_VAULT_DIR=vault_dir,
        ):
            result = await capi_activity.send_capi_event_activity(
                session_id, "ep_1", "LeadSubmitted"
            )
        assert result.status == "skipped_no_ctwa_clid"

    @pytest.mark.asyncio
    async def test_skipped_attribution_expired(
        self, vault_session: tuple[Path, str]
    ) -> None:
        vault_dir, session_id = vault_session
        # 10 days ago — past the 7d window
        captured_ms = (
            (int(__import__("time").time()) - 10 * 24 * 60 * 60) * 1000
        )
        _seed_metadata(
            vault_dir,
            session_id,
            ctwa_referrals=[
                {"ctwa_clid": "CLID", "captured_at_ms": captured_ms}
            ],
        )
        from src.platform.whatsapp import capi_activity

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="DS",
            META_CAPI_ACCESS_TOKEN="TOKEN",
            WHATSAPP_BUSINESS_ACCOUNT_ID="WABA",
            WORKSPACE_VAULT_DIR=vault_dir,
        ):
            result = await capi_activity.send_capi_event_activity(
                session_id, "ep_1", "LeadSubmitted"
            )
        assert result.status == "skipped_attribution_expired"

    @pytest.mark.asyncio
    async def test_skipped_terminal_event_blocks_lead(
        self, vault_session: tuple[Path, str]
    ) -> None:
        """If Purchase was already sent for this ctwa_clid, Lead is wasted."""
        vault_dir, session_id = vault_session
        now_ms = int(__import__("time").time() * 1000)
        _seed_metadata(
            vault_dir,
            session_id,
            ctwa_referrals=[{"ctwa_clid": "CLID", "captured_at_ms": now_ms}],
            capi_terminal_event="Purchase",
        )
        from src.platform.whatsapp import capi_activity

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="DS",
            META_CAPI_ACCESS_TOKEN="TOKEN",
            WHATSAPP_BUSINESS_ACCOUNT_ID="WABA",
            WORKSPACE_VAULT_DIR=vault_dir,
        ):
            result = await capi_activity.send_capi_event_activity(
                session_id, "ep_1", "LeadSubmitted"
            )
        assert result.status == "skipped_terminal_event_reached"

    @pytest.mark.asyncio
    async def test_skipped_no_registered_order_for_purchase(
        self, vault_session: tuple[Path, str]
    ) -> None:
        vault_dir, session_id = vault_session
        now_ms = int(__import__("time").time() * 1000)
        _seed_metadata(
            vault_dir,
            session_id,
            ctwa_referrals=[{"ctwa_clid": "CLID", "captured_at_ms": now_ms}],
            # No registered_order in metadata
        )
        from src.platform.whatsapp import capi_activity

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="DS",
            META_CAPI_ACCESS_TOKEN="TOKEN",
            WHATSAPP_BUSINESS_ACCOUNT_ID="WABA",
            WORKSPACE_VAULT_DIR=vault_dir,
        ):
            result = await capi_activity.send_capi_event_activity(
                session_id, "ep_1", "Purchase"
            )
        assert result.status == "skipped_no_registered_order"

    @pytest.mark.asyncio
    async def test_skipped_already_sent_lead(
        self, vault_session: tuple[Path, str]
    ) -> None:
        vault_dir, session_id = vault_session
        now_ms = int(__import__("time").time() * 1000)
        _seed_metadata(
            vault_dir,
            session_id,
            ctwa_referrals=[{"ctwa_clid": "CLID", "captured_at_ms": now_ms}],
            capi_events_sent=[
                {
                    "event_id": "lead_wa_+573001234567_ep_1",
                    "event_name": "LeadSubmitted",
                    "status": "sent",
                }
            ],
        )
        from src.platform.whatsapp import capi_activity

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="DS",
            META_CAPI_ACCESS_TOKEN="TOKEN",
            WHATSAPP_BUSINESS_ACCOUNT_ID="WABA",
            WORKSPACE_VAULT_DIR=vault_dir,
        ):
            result = await capi_activity.send_capi_event_activity(
                session_id, "ep_1", "LeadSubmitted"
            )
        assert result.status == "skipped_already_sent"


class TestActivityHappyPath:
    @pytest.mark.asyncio
    async def test_lead_sent_persists_to_metadata(
        self, vault_session: tuple[Path, str]
    ) -> None:
        vault_dir, session_id = vault_session
        now_ms = int(__import__("time").time() * 1000)
        _seed_metadata(
            vault_dir,
            session_id,
            ctwa_referrals=[{"ctwa_clid": "CLID_X", "captured_at_ms": now_ms}],
        )
        from src.platform.whatsapp import capi_activity

        mock_response = httpx.Response(
            status_code=200,
            json={"events_received": 1, "fbtrace_id": "TRACE_X"},
        )
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="DS123",
            META_CAPI_ACCESS_TOKEN="TOKEN",
            META_CAPI_TEST_EVENT_CODE="",
            WHATSAPP_BUSINESS_ACCOUNT_ID="WABA",
            WORKSPACE_VAULT_DIR=vault_dir,
        ), patch.object(httpx, "AsyncClient", return_value=mock_client):
            result = await capi_activity.send_capi_event_activity(
                session_id, "ep_1", "LeadSubmitted"
            )

        assert result.status == "sent"
        assert result.event_id == "lead_wa_+573001234567_ep_1"
        assert result.event_name == "LeadSubmitted"
        assert result.fbtrace_id == "TRACE_X"

        # Verify metadata was updated
        md = _read_metadata(vault_dir, session_id)
        assert "capi_events_sent" in md
        assert len(md["capi_events_sent"]) == 1
        assert md["capi_events_sent"][0]["status"] == "sent"
        assert md["capi_events_sent"][0]["event_name"] == "LeadSubmitted"
        # Lead does NOT lock terminal_event (only Purchase does)
        assert "capi_terminal_event" not in md

    @pytest.mark.asyncio
    async def test_purchase_sent_sets_terminal_event(
        self, vault_session: tuple[Path, str]
    ) -> None:
        vault_dir, session_id = vault_session
        now_ms = int(__import__("time").time() * 1000)
        _seed_metadata(
            vault_dir,
            session_id,
            ctwa_referrals=[{"ctwa_clid": "CLID_X", "captured_at_ms": now_ms}],
            registered_order={
                "success": True,
                "order_id": "order_01ABC",
                "total_cop": 95000,
                "currency": "COP",
            },
        )
        from src.platform.whatsapp import capi_activity

        mock_response = httpx.Response(
            status_code=200,
            json={"events_received": 1},
        )
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="DS123",
            META_CAPI_ACCESS_TOKEN="TOKEN",
            META_CAPI_TEST_EVENT_CODE="",
            WHATSAPP_BUSINESS_ACCOUNT_ID="WABA",
            WORKSPACE_VAULT_DIR=vault_dir,
        ), patch.object(httpx, "AsyncClient", return_value=mock_client):
            result = await capi_activity.send_capi_event_activity(
                session_id, "ep_1", "Purchase"
            )

        assert result.status == "sent"
        assert result.event_id == "purchase_order_01ABC"

        md = _read_metadata(vault_dir, session_id)
        # Critical: Purchase locks the terminal_event so subsequent Lead
        # attempts skip.
        assert md["capi_terminal_event"] == "Purchase"
        assert md["capi_events_sent"][0]["event_name"] == "Purchase"

    @pytest.mark.asyncio
    async def test_purchase_value_pulled_from_registered_order(
        self, vault_session: tuple[Path, str]
    ) -> None:
        """Verify the value sent to Meta matches the registered_order.total_cop."""
        vault_dir, session_id = vault_session
        now_ms = int(__import__("time").time() * 1000)
        _seed_metadata(
            vault_dir,
            session_id,
            ctwa_referrals=[{"ctwa_clid": "CLID_X", "captured_at_ms": now_ms}],
            registered_order={
                "success": True,
                "order_id": "order_X",
                "total_cop": 250000,
                "currency": "COP",
            },
        )
        from src.platform.whatsapp import capi_activity

        captured: dict[str, Any] = {}

        async def fake_post(url: str, **kwargs: Any) -> httpx.Response:
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return httpx.Response(status_code=200, json={"events_received": 1})

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(side_effect=fake_post)

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="DS123",
            META_CAPI_ACCESS_TOKEN="TOKEN",
            META_CAPI_TEST_EVENT_CODE="",
            WHATSAPP_BUSINESS_ACCOUNT_ID="WABA",
            WORKSPACE_VAULT_DIR=vault_dir,
        ), patch.object(httpx, "AsyncClient", return_value=mock_client):
            await capi_activity.send_capi_event_activity(
                session_id, "ep_1", "Purchase"
            )

        # Verify Meta payload structure
        assert "v18.0/DS123/events" in captured["url"]
        payload = captured["json"]
        assert payload["data"][0]["custom_data"]["value"] == 250000
        assert payload["data"][0]["custom_data"]["currency"] == "COP"
        assert payload["data"][0]["action_source"] == "business_messaging"
        assert payload["data"][0]["messaging_channel"] == "whatsapp"


class TestActivityErrors:
    @pytest.mark.asyncio
    async def test_4xx_raises_non_retryable(
        self, vault_session: tuple[Path, str]
    ) -> None:
        vault_dir, session_id = vault_session
        now_ms = int(__import__("time").time() * 1000)
        _seed_metadata(
            vault_dir,
            session_id,
            ctwa_referrals=[{"ctwa_clid": "CLID", "captured_at_ms": now_ms}],
        )
        from src.platform.whatsapp import capi_activity

        mock_response = httpx.Response(
            status_code=400,
            json={"error": {"code": 100, "message": "Invalid parameter"}},
        )
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="DS",
            META_CAPI_ACCESS_TOKEN="TOKEN",
            WHATSAPP_BUSINESS_ACCOUNT_ID="WABA",
            WORKSPACE_VAULT_DIR=vault_dir,
        ), patch.object(httpx, "AsyncClient", return_value=mock_client):
            with pytest.raises(ApplicationError) as exc_info:
                await capi_activity.send_capi_event_activity(
                    session_id, "ep_1", "LeadSubmitted"
                )

        assert exc_info.value.non_retryable is True
        # Verify metadata recorded the failure
        md = _read_metadata(vault_dir, session_id)
        assert md["capi_events_sent"][0]["status"] == "failed_4xx"

    @pytest.mark.asyncio
    async def test_5xx_raises_retryable(
        self, vault_session: tuple[Path, str]
    ) -> None:
        vault_dir, session_id = vault_session
        now_ms = int(__import__("time").time() * 1000)
        _seed_metadata(
            vault_dir,
            session_id,
            ctwa_referrals=[{"ctwa_clid": "CLID", "captured_at_ms": now_ms}],
        )
        from src.platform.whatsapp import capi_activity

        mock_response = httpx.Response(
            status_code=503,
            json={"error": "service unavailable"},
        )
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="DS",
            META_CAPI_ACCESS_TOKEN="TOKEN",
            WHATSAPP_BUSINESS_ACCOUNT_ID="WABA",
            WORKSPACE_VAULT_DIR=vault_dir,
        ), patch.object(httpx, "AsyncClient", return_value=mock_client):
            with pytest.raises(ApplicationError) as exc_info:
                await capi_activity.send_capi_event_activity(
                    session_id, "ep_1", "LeadSubmitted"
                )

        assert exc_info.value.non_retryable is False
        md = _read_metadata(vault_dir, session_id)
        assert md["capi_events_sent"][0]["status"] == "failed_5xx"

    @pytest.mark.asyncio
    async def test_network_error_raises_retryable(
        self, vault_session: tuple[Path, str]
    ) -> None:
        vault_dir, session_id = vault_session
        now_ms = int(__import__("time").time() * 1000)
        _seed_metadata(
            vault_dir,
            session_id,
            ctwa_referrals=[{"ctwa_clid": "CLID", "captured_at_ms": now_ms}],
        )
        from src.platform.whatsapp import capi_activity

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("DNS resolution failed")
        )

        with patch.multiple(
            capi_activity,
            META_CAPI_DATASET_ID="DS",
            META_CAPI_ACCESS_TOKEN="TOKEN",
            WHATSAPP_BUSINESS_ACCOUNT_ID="WABA",
            WORKSPACE_VAULT_DIR=vault_dir,
        ), patch.object(httpx, "AsyncClient", return_value=mock_client):
            with pytest.raises(ApplicationError) as exc_info:
                await capi_activity.send_capi_event_activity(
                    session_id, "ep_1", "LeadSubmitted"
                )

        # Transport errors are retryable
        assert exc_info.value.non_retryable is False
        md = _read_metadata(vault_dir, session_id)
        assert md["capi_events_sent"][0]["status"] == "failed_5xx"


class TestWorkflowMapper:
    """The _map_closing_tag_to_capi_event helper lives in the workflow
    module — assert the contract."""

    def test_compra_exitosa_maps_to_purchase(self) -> None:
        from src.plugins.chats.agent.sales.workflows.sales_session import (
            _map_closing_tag_to_capi_event,
        )

        assert _map_closing_tag_to_capi_event("COMPRA_EXITOSA") == "Purchase"

    def test_confirmado_pago_pendiente_maps_to_lead(self) -> None:
        from src.plugins.chats.agent.sales.workflows.sales_session import (
            _map_closing_tag_to_capi_event,
        )

        assert _map_closing_tag_to_capi_event("CONFIRMADO_PAGO_PENDIENTE") == "LeadSubmitted"

    def test_confirmado_sin_datos_maps_to_lead(self) -> None:
        from src.plugins.chats.agent.sales.workflows.sales_session import (
            _map_closing_tag_to_capi_event,
        )

        assert _map_closing_tag_to_capi_event("CONFIRMADO_SIN_DATOS") == "LeadSubmitted"

    def test_rechazo_maps_to_none(self) -> None:
        from src.plugins.chats.agent.sales.workflows.sales_session import (
            _map_closing_tag_to_capi_event,
        )

        assert _map_closing_tag_to_capi_event("RECHAZO") is None

    def test_unknown_tag_maps_to_none(self) -> None:
        from src.plugins.chats.agent.sales.workflows.sales_session import (
            _map_closing_tag_to_capi_event,
        )

        assert _map_closing_tag_to_capi_event("GHOSTED") is None
        assert _map_closing_tag_to_capi_event("TIMEOUT") is None
        assert _map_closing_tag_to_capi_event("") is None


# =============================================================================
# Conftest: avoid touching real vault
# =============================================================================
# The autouse `_isolate_vault_dir` in tests/conftest.py already redirects
# WORKSPACE_VAULT_DIR to tmp_path. Our patches of capi_activity's bound
# WORKSPACE_VAULT_DIR ALSO override it for the activity's I/O. Belt-and-
# suspenders so we never touch ./hubara_vault/.
