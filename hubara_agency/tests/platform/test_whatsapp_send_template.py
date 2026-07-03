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
    al primer add (cost_usd_micros=None).

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
        assert log_entry["cost_usd_micros"] is None

        # cost_summary started empty, now pending_count == 1
        summary = episode["cost_summary"]
        assert summary["messages_count"] == 1
        assert summary["messages_pending_count"] == 1
        assert summary["total_usd_micros"] == 0

        # last_outbound mirror
        assert metadata["last_outbound"]["wa_message_id"] == "wamid.NEW"

        # HU-WA24H-001 pre-mortem F2.2: el template send también se persiste
        # al JSONL del session_history para que el dashboard del operador lo
        # vea como parte del chat.
        history_path = (
            isolated_vault / session_id / "sessions" / f"{session_id}.jsonl"
        )
        assert history_path.exists()
        lines = history_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        history_event = json.loads(lines[0])
        assert history_event["role"] == "assistant"
        assert history_event["kind"] == "template"
        assert history_event["template_name"] == "quote_ready_utility_v1"
        assert history_event["waba_template_name"] == "quote_ready_utility"
        assert history_event["variables"] == {
            "customer_first_name": "Juan",
            "product_or_quote_label": "vela",
        }
        assert "[Template: quote_ready_utility_v1]" in history_event["content"]
        assert "customer_first_name=Juan" in history_event["content"]
        assert "T" in history_event["timestamp"]

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


# =============================================================================
# Idempotencia de template sends (fix integridad — patrón B)
# =============================================================================


class TestSendTemplateIdempotency:
    """Un retry de Temporal NO debe reenviar el mismo template (doble cobro)."""

    @pytest.mark.asyncio
    async def test_duplicate_send_is_deduplicated(self, isolated_vault, monkeypatch):
        session_id = "wa_5491111111111"
        _write_metadata(
            isolated_vault, session_id, {"phone_number_id": "PHONE", "episodes": []}
        )
        mock_send = AsyncMock(
            return_value=OutboundResult(wa_message_id="wamid.ONCE", ok=True)
        )
        monkeypatch.setattr(
            "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
        )
        args = (
            session_id,
            "quote_ready_utility_v1",
            {"customer_first_name": "Ana", "product_or_quote_label": "vela"},
        )

        first = await send_template_to_session(*args)
        # Segunda llamada idéntica (simula el retry de la activity tras crash).
        second = await send_template_to_session(*args)

        # Meta fue golpeado UNA sola vez; la segunda reusó el wa_message_id.
        assert mock_send.call_count == 1
        assert first.wa_message_id == "wamid.ONCE"
        assert second.wa_message_id == "wamid.ONCE"
        assert second.ok is True
        # El outbound NO se duplicó en el episodio (un solo registro).
        metadata = _read_metadata(isolated_vault, session_id)
        assert len(metadata["recent_template_sends"]) == 1

    @pytest.mark.asyncio
    async def test_distinct_content_is_not_deduplicated(
        self, isolated_vault, monkeypatch
    ):
        session_id = "wa_5491111111111"
        _write_metadata(
            isolated_vault, session_id, {"phone_number_id": "PHONE", "episodes": []}
        )
        mock_send = AsyncMock(
            side_effect=[
                OutboundResult(wa_message_id="wamid.A", ok=True),
                OutboundResult(wa_message_id="wamid.B", ok=True),
            ]
        )
        monkeypatch.setattr(
            "src.platform.whatsapp.activities.whatsapp_client.send_template", mock_send
        )

        await send_template_to_session(
            session_id, "quote_ready_utility_v1",
            {"customer_first_name": "Ana", "product_or_quote_label": "vela"},
        )
        # Variables DISTINTAS → es un mensaje distinto, debe enviarse.
        await send_template_to_session(
            session_id, "quote_ready_utility_v1",
            {"customer_first_name": "Beto", "product_or_quote_label": "difusor"},
        )

        assert mock_send.call_count == 2


class TestSendTemplateStampsSendPolicy:
    """WS2 — el send de template consulta la central y estampa la estimación
    de costo/lane en `last_outbound_policy`. Este test EJECUTA el send (no
    grep) — así el import roto (ruff --fix borra imports entre edits, L-6) se
    caza como NameError en runtime, no en prod.
    """

    @pytest.mark.asyncio
    async def test_stamps_last_outbound_policy_from_central(
        self, isolated_vault, monkeypatch
    ):
        session_id = "wa_5492222222222"
        _write_metadata(
            isolated_vault,
            session_id,
            {
                "phone_number_id": "PHONE_TEST",
                # Sin ventanas abiertas → template utility cobrado (no free).
                "episodes": [
                    {
                        "episode_id": "ep_pol",
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
        mock_send = AsyncMock(
            return_value=OutboundResult(wa_message_id="wamid.POL", ok=True)
        )
        monkeypatch.setattr(
            "src.platform.whatsapp.activities.whatsapp_client.send_template",
            mock_send,
        )

        await send_template_to_session(
            session_id,
            "quote_ready_utility_v1",
            {"customer_first_name": "Ana", "product_or_quote_label": "vela"},
        )

        metadata = _read_metadata(isolated_vault, session_id)
        policy = metadata["last_outbound_policy"]
        assert policy["channel"] == "template"
        assert policy["category"] == "utility"
        assert policy["allowed"] is True
        assert policy["is_free"] is False  # sin ventanas abiertas
        # utility = 800 micros (estable entre co_2026q2 y q4).
        assert policy["expected_cost_micros"] == 800
