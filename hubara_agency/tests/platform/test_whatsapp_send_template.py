"""Tests del send_template_to_session (versión pura del activity).

Verifica:
  * Happy path: send OK → OutboundLogEntry persistido en episode activo +
    cost_summary updated.
  * Template no registrado → ApplicationError(non_retryable=True).
  * Send falla con código non-retryable (131008, 132012, 131049, 131047)
    → ApplicationError(non_retryable=True).
  * Send falla con código retryable → ApplicationError(non_retryable=False).
  * Sin episodio activo → log_entry NO se persiste en episode (sí en
    last_outbound).
  * Episodio cerrado → log_entry NO se persiste en ese episode.
  * Race con `_append_outbound_to_active_episode`: pending_count incrementa
    al primer add (cost_cents_usd=None).

Ver `src/platform/whatsapp/activities.py::send_template_to_session` y
HU-WA24H-001 §4.6, §3.1.b.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import ApplicationError

from src.platform.whatsapp.activities import (
    NON_RETRYABLE_META_ERROR_CODES,
    _parse_meta_error_code,
    send_template_to_session,
)
from src.platform.whatsapp.dtos import OutboundResult


# =============================================================================
# Helpers
# =============================================================================


def _write_metadata(vault_dir: Path, session_id: str, data: dict[str, Any]) -> None:
    """Helper: persist metadata.json para el test."""
    folder = vault_dir / session_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "metadata.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def _read_metadata(vault_dir: Path, session_id: str) -> dict[str, Any]:
    path = vault_dir / session_id / "metadata.json"
    return json.loads(path.read_text(encoding="utf-8"))


# Fixture autouse para redirigir el WORKSPACE_VAULT_DIR (los tests no deben
# tocar el vault real). `tests/conftest.py` ya tiene `_isolate_vault_dir`
# pero el activity importa WORKSPACE_VAULT_DIR directamente del config, asi
# que hacemos patch local.
@pytest.fixture
def isolated_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.platform.whatsapp.activities.WORKSPACE_VAULT_DIR", tmp_path
    )
    return tmp_path


# =============================================================================
# _parse_meta_error_code
# =============================================================================


class TestParseMetaErrorCode:
    def test_extracts_code_from_typical_error(self):
        err = (
            'http_400: {"error":{"message":"...","type":"OAuthException",'
            '"code":131008,"fbtrace_id":"..."}}'
        )
        assert _parse_meta_error_code(err) == "131008"

    def test_returns_none_when_no_code(self):
        assert _parse_meta_error_code("Connection timeout") is None

    def test_returns_none_for_none_input(self):
        assert _parse_meta_error_code(None) is None

    def test_handles_multiple_codes_picks_first(self):
        # Si hay sub-objects con "code", el primero gana — best-effort.
        err = '{"error":{"code":131049,"fb_data":{"code":9999}}}'
        assert _parse_meta_error_code(err) == "131049"


# =============================================================================
# send_template_to_session — happy path
# =============================================================================


class TestSendTemplateHappyPath:
    @pytest.mark.asyncio
    async def test_persists_log_entry_in_active_episode(
        self, isolated_vault, monkeypatch
    ):
        session_id = "wa_5491111111111"
        _write_metadata(
            isolated_vault,
            session_id,
            {
                "phone_number_id": "PHONE_TEST",
                "episodes": [
                    {
                        "episode_id": "ep_001",
                        "started_at_ms": 1_700_000_000_000,
                        "closed_at_ms": None,
                        "closing_tag": None,
                        "closing_motivo": None,
                        "order_id": None,
                        "referral_snapshot": None,
                        "msgs_count_at_start": 0,
                        "msgs_count_at_close": None,
                    }
                ],
            },
        )

        # Mock client.send_template → returns wa_message_id
        mock_send = AsyncMock(
            return_value=OutboundResult(wa_message_id="wamid.NEW", ok=True)
        )
        monkeypatch.setattr(
            "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
        )

        result = await send_template_to_session(
            session_id,
            "quote_ready_utility_v1",
            {"customer_first_name": "Juan", "product_or_quote_label": "vela"},
        )

        assert result.ok is True
        assert result.wa_message_id == "wamid.NEW"

        # Verify client was called with correct spec
        assert mock_send.call_count == 1
        call_args = mock_send.call_args
        assert call_args.args[0] == "PHONE_TEST"
        assert call_args.args[1] == "5491111111111"
        spec = call_args.args[2]
        assert spec.waba_template_name == "quote_ready_utility"

        # Verify persistence
        metadata = _read_metadata(isolated_vault, session_id)
        episode = metadata["episodes"][-1]
        assert "outbound_messages" in episode
        assert len(episode["outbound_messages"]) == 1
        log_entry = episode["outbound_messages"][0]
        assert log_entry["wa_message_id"] == "wamid.NEW"
        assert log_entry["template_name"] == "quote_ready_utility_v1"
        assert log_entry["kind"] == "template"
        assert log_entry["pricing"] is None
        assert log_entry["cost_cents_usd"] is None

        # cost_summary started empty, now pending_count == 1
        summary = episode["cost_summary"]
        assert summary["messages_count"] == 1
        assert summary["messages_pending_count"] == 1
        assert summary["total_cents_usd"] == 0

        # last_outbound mirror
        assert metadata["last_outbound"]["wa_message_id"] == "wamid.NEW"

    @pytest.mark.asyncio
    async def test_resolves_phone_id_from_env_when_not_in_metadata(
        self, isolated_vault, monkeypatch
    ):
        session_id = "wa_5491111111111"
        _write_metadata(isolated_vault, session_id, {"episodes": []})
        monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE_FROM_ENV")

        mock_send = AsyncMock(
            return_value=OutboundResult(wa_message_id="wamid.X", ok=True)
        )
        monkeypatch.setattr(
            "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
        )

        await send_template_to_session(
            session_id,
            "quote_ready_utility_v1",
            {"customer_first_name": "x", "product_or_quote_label": "y"},
        )

        assert mock_send.call_args.args[0] == "PHONE_FROM_ENV"


# =============================================================================
# send_template_to_session — error cases
# =============================================================================


class TestSendTemplateErrors:
    @pytest.mark.asyncio
    async def test_template_not_in_registry_raises_non_retryable(
        self, isolated_vault, monkeypatch
    ):
        monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE")
        session_id = "wa_5491111111111"
        _write_metadata(isolated_vault, session_id, {"episodes": []})

        with pytest.raises(ApplicationError) as exc_info:
            await send_template_to_session(
                session_id, "nonexistent_template_v99", {}
            )

        assert exc_info.value.non_retryable is True
        assert "not in registry" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_meta_131008_is_non_retryable(self, isolated_vault, monkeypatch):
        session_id = "wa_5491111111111"
        _write_metadata(
            isolated_vault, session_id, {"phone_number_id": "PHONE", "episodes": []}
        )

        # Mock send returns OutboundResult.ok=False with code 131008
        mock_send = AsyncMock(
            return_value=OutboundResult(
                wa_message_id=None,
                ok=False,
                error='http_400: {"error":{"code":131008,"message":"template not approved"}}',
            )
        )
        monkeypatch.setattr(
            "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
        )

        with pytest.raises(ApplicationError) as exc_info:
            await send_template_to_session(
                session_id,
                "quote_ready_utility_v1",
                {"customer_first_name": "x", "product_or_quote_label": "y"},
            )

        assert exc_info.value.non_retryable is True
        assert "131008" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_meta_132012_paused_is_non_retryable(
        self, isolated_vault, monkeypatch
    ):
        session_id = "wa_5491111111111"
        _write_metadata(
            isolated_vault, session_id, {"phone_number_id": "PHONE", "episodes": []}
        )

        mock_send = AsyncMock(
            return_value=OutboundResult(
                wa_message_id=None,
                ok=False,
                error='http_400: {"error":{"code":132012,"message":"template paused"}}',
            )
        )
        monkeypatch.setattr(
            "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
        )

        with pytest.raises(ApplicationError) as exc_info:
            await send_template_to_session(
                session_id,
                "quote_ready_utility_v1",
                {"customer_first_name": "x", "product_or_quote_label": "y"},
            )

        assert exc_info.value.non_retryable is True

    @pytest.mark.asyncio
    async def test_meta_131049_user_cap_is_non_retryable(
        self, isolated_vault, monkeypatch
    ):
        """131049 = per-user marketing cap. Retry no ayuda — esperar mañana."""
        session_id = "wa_5491111111111"
        _write_metadata(
            isolated_vault, session_id, {"phone_number_id": "PHONE", "episodes": []}
        )

        mock_send = AsyncMock(
            return_value=OutboundResult(
                wa_message_id=None,
                ok=False,
                error='http_429: {"error":{"code":131049,"message":"user marketing cap"}}',
            )
        )
        monkeypatch.setattr(
            "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
        )

        with pytest.raises(ApplicationError) as exc_info:
            await send_template_to_session(
                session_id,
                "quote_ready_utility_v1",
                {"customer_first_name": "x", "product_or_quote_label": "y"},
            )

        assert exc_info.value.non_retryable is True

    @pytest.mark.asyncio
    async def test_meta_unknown_code_is_retryable(self, isolated_vault, monkeypatch):
        """Códigos no en NON_RETRYABLE → retry policy de Temporal decide."""
        session_id = "wa_5491111111111"
        _write_metadata(
            isolated_vault, session_id, {"phone_number_id": "PHONE", "episodes": []}
        )

        mock_send = AsyncMock(
            return_value=OutboundResult(
                wa_message_id=None,
                ok=False,
                error='http_500: {"error":{"code":999999,"message":"server error"}}',
            )
        )
        monkeypatch.setattr(
            "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
        )

        with pytest.raises(ApplicationError) as exc_info:
            await send_template_to_session(
                session_id,
                "quote_ready_utility_v1",
                {"customer_first_name": "x", "product_or_quote_label": "y"},
            )

        # Retryable: NO non_retryable
        assert exc_info.value.non_retryable is False

    @pytest.mark.asyncio
    async def test_missing_phone_number_id_is_non_retryable(
        self, isolated_vault, monkeypatch
    ):
        session_id = "wa_5491111111111"
        _write_metadata(isolated_vault, session_id, {"episodes": []})
        monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)

        with pytest.raises(ApplicationError) as exc_info:
            await send_template_to_session(
                session_id,
                "quote_ready_utility_v1",
                {"customer_first_name": "x", "product_or_quote_label": "y"},
            )

        assert exc_info.value.non_retryable is True
        assert "WHATSAPP_PHONE_NUMBER_ID" in str(exc_info.value)


# =============================================================================
# send_template_to_session — episode edge cases
# =============================================================================


class TestSendTemplateEpisodeEdgeCases:
    @pytest.mark.asyncio
    async def test_no_episodes_does_not_break(self, isolated_vault, monkeypatch):
        """Sin episodios: log_entry NO se agrega a outbound_messages (no hay
        donde), pero sí se persiste a last_outbound."""
        session_id = "wa_5491111111111"
        _write_metadata(
            isolated_vault, session_id, {"phone_number_id": "PHONE", "episodes": []}
        )

        mock_send = AsyncMock(
            return_value=OutboundResult(wa_message_id="wamid.X", ok=True)
        )
        monkeypatch.setattr(
            "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
        )

        result = await send_template_to_session(
            session_id,
            "quote_ready_utility_v1",
            {"customer_first_name": "x", "product_or_quote_label": "y"},
        )

        assert result.ok is True
        metadata = _read_metadata(isolated_vault, session_id)
        assert metadata["episodes"] == []  # sin tocar
        assert metadata["last_outbound"]["wa_message_id"] == "wamid.X"

    @pytest.mark.asyncio
    async def test_closed_episode_does_not_get_log_entry(
        self, isolated_vault, monkeypatch
    ):
        """Episodio cerrado: NO contaminamos su outbound_messages."""
        session_id = "wa_5491111111111"
        _write_metadata(
            isolated_vault,
            session_id,
            {
                "phone_number_id": "PHONE",
                "episodes": [
                    {
                        "episode_id": "ep_001",
                        "started_at_ms": 1_700_000_000_000,
                        "closed_at_ms": 1_700_000_500_000,
                        "closing_tag": "COMPRA_EXITOSA",
                        "closing_motivo": "cerrado",
                        "order_id": "ord_1",
                        "referral_snapshot": None,
                        "msgs_count_at_start": 0,
                        "msgs_count_at_close": 5,
                    }
                ],
            },
        )

        mock_send = AsyncMock(
            return_value=OutboundResult(wa_message_id="wamid.X", ok=True)
        )
        monkeypatch.setattr(
            "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
        )

        await send_template_to_session(
            session_id,
            "quote_ready_utility_v1",
            {"customer_first_name": "x", "product_or_quote_label": "y"},
        )

        metadata = _read_metadata(isolated_vault, session_id)
        # Episode cerrado NO se tocó.
        assert "outbound_messages" not in metadata["episodes"][0]
        assert "cost_summary" not in metadata["episodes"][0]
        # last_outbound sí se actualizó.
        assert metadata["last_outbound"]["wa_message_id"] == "wamid.X"


# =============================================================================
# NON_RETRYABLE_META_ERROR_CODES — contrato
# =============================================================================


class TestNonRetryableCodes:
    def test_includes_131008(self):
        assert "131008" in NON_RETRYABLE_META_ERROR_CODES

    def test_includes_132012(self):
        assert "132012" in NON_RETRYABLE_META_ERROR_CODES

    def test_includes_131049(self):
        assert "131049" in NON_RETRYABLE_META_ERROR_CODES

    def test_includes_131047(self):
        assert "131047" in NON_RETRYABLE_META_ERROR_CODES

    def test_is_frozenset_immutable(self):
        assert isinstance(NON_RETRYABLE_META_ERROR_CODES, frozenset)
