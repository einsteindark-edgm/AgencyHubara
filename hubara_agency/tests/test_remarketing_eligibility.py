"""Tests de `check_remarketing_eligibility` activity.

Post-mortem run e688685d-c676-4e61-a152-b22ff49788db (2026-05-20):
el sales workflow escaló al cliente a humano (active_route=humano, tag=HUMANO)
PERO un remarketing previamente programado con start_delay=60s arrancó después
del escalation, pisó el routing con ROUTE_REMARKETING y envió un mensaje
reactivando la conversación — violando la regla de negocio: "cuando hay
humano en el caso, ningún bot interviene hasta que el humano devuelva el
control".

La activity `check_remarketing_eligibility` es la primera invocada por
`RemarketingWorkflow.run`. Si devuelve `eligible=False`, el workflow returna
early SIN side-effects.

Casos cubiertos:
  1. metadata.json no existe → eligible (sesión nueva).
  2. metadata.json válido, active_route=ventas, tag=INTERESADO → eligible.
  3. metadata.json válido, active_route=humano → NO eligible.
  4. metadata.json válido, tag=HUMANO → NO eligible.
  5. metadata.json válido, tag=COMPRA_EXITOSA → NO eligible.
  6. metadata.json corrupto → NO eligible (fail-safe).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from temporalio.testing import ActivityEnvironment

from src.platform.constants import ROUTE_HUMANO, ROUTE_REMARKETING, ROUTE_VENTAS
from src.platform.contracts import RemarketingEligibility
from src.platform.temporal.activities import check_remarketing_eligibility


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    """Mock WORKSPACE_VAULT_DIR to point at tmp_path so each test is isolated."""
    with patch("src.platform.temporal.activities.WORKSPACE_VAULT_DIR", tmp_path):
        yield tmp_path


def _write_metadata(vault: Path, session_id: str, data: dict) -> None:
    session_dir = vault / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "metadata.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_eligible_when_no_metadata(vault_dir: Path) -> None:
    """Sesión nueva (sin metadata.json) → eligible por default."""
    env = ActivityEnvironment()
    result: RemarketingEligibility = await env.run(
        check_remarketing_eligibility, "wa_new_session"
    )
    assert result.eligible is True
    assert result.current_route == ROUTE_VENTAS
    assert result.current_tag == "NO_ETIQUETADO"
    assert result.blocked_reason == ""


@pytest.mark.asyncio
async def test_eligible_when_route_ventas_tag_interesado(vault_dir: Path) -> None:
    """metadata válido con route=ventas + tag=INTERESADO → eligible (caso normal)."""
    _write_metadata(
        vault_dir,
        "wa_interesado",
        {"active_route": ROUTE_VENTAS, "tag": "INTERESADO"},
    )
    env = ActivityEnvironment()
    result = await env.run(check_remarketing_eligibility, "wa_interesado")
    assert result.eligible is True
    assert result.current_route == ROUTE_VENTAS
    assert result.current_tag == "INTERESADO"


@pytest.mark.asyncio
async def test_not_eligible_when_route_humano(vault_dir: Path) -> None:
    """active_route=humano → bloqueado. Regla de negocio: si humano gestiona, bots quietos."""
    _write_metadata(
        vault_dir,
        "wa_escalated",
        {"active_route": ROUTE_HUMANO, "tag": "INTERESADO"},
    )
    env = ActivityEnvironment()
    result = await env.run(check_remarketing_eligibility, "wa_escalated")
    assert result.eligible is False
    assert result.current_route == ROUTE_HUMANO
    assert "humano" in result.blocked_reason.lower()


@pytest.mark.asyncio
async def test_not_eligible_when_tag_humano(vault_dir: Path) -> None:
    """tag=HUMANO (sin pasar por active_route) → bloqueado. Defense in depth."""
    _write_metadata(
        vault_dir,
        "wa_tag_humano",
        {"active_route": ROUTE_VENTAS, "tag": "HUMANO"},
    )
    env = ActivityEnvironment()
    result = await env.run(check_remarketing_eligibility, "wa_tag_humano")
    assert result.eligible is False
    assert result.current_tag == "HUMANO"
    assert "terminal" in result.blocked_reason.lower()


@pytest.mark.asyncio
async def test_not_eligible_when_tag_compra_exitosa(vault_dir: Path) -> None:
    """tag=COMPRA_EXITOSA → bloqueado. La venta ya cerró, no se reactiva."""
    _write_metadata(
        vault_dir,
        "wa_closed",
        {"active_route": ROUTE_VENTAS, "tag": "COMPRA_EXITOSA"},
    )
    env = ActivityEnvironment()
    result = await env.run(check_remarketing_eligibility, "wa_closed")
    assert result.eligible is False
    assert result.current_tag == "COMPRA_EXITOSA"


@pytest.mark.asyncio
async def test_not_eligible_when_metadata_corrupt(vault_dir: Path) -> None:
    """metadata.json corrupto → fail-safe NO eligible. Mejor un remarketing perdido
    que pisar un caso humano por accidente."""
    session_dir = vault_dir / "wa_corrupt"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "metadata.json").write_text(
        "{ this is not valid JSON",
        encoding="utf-8",
    )
    env = ActivityEnvironment()
    result = await env.run(check_remarketing_eligibility, "wa_corrupt")
    assert result.eligible is False
    assert "corrupto" in result.blocked_reason.lower() or "ilegible" in result.blocked_reason.lower()


@pytest.mark.asyncio
async def test_eligible_when_route_remarketing(vault_dir: Path) -> None:
    """active_route=remarketing → eligible (caso normal: el remarketing previo
    todavía corre, o un re-arranque legítimo)."""
    _write_metadata(
        vault_dir,
        "wa_remarketing_active",
        {"active_route": ROUTE_REMARKETING, "tag": "INTERESADO"},
    )
    env = ActivityEnvironment()
    result = await env.run(check_remarketing_eligibility, "wa_remarketing_active")
    assert result.eligible is True
