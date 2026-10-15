"""Un argumento que el LLM se inventa no tumba el turno (incidente 2026-10-15).

Con Gemini 3.5 Flash-Lite de respaldo (DeepSeek caído), el bot llamó
`present_products(intro_text=…, intro_title=…)`. `intro_title` no existe en la
tool: `execute_with_context(**params)` lanzó `TypeError: got an unexpected
keyword argument 'intro_title'`, la activity `execute_tool` falló sus 2
intentos con los mismos argumentos y el turno murió sin responderle a un
cliente que venía de un anuncio. El operador respondió a mano.

Ahora el dispatch descarta lo que el schema de la tool no declara, deja un
WARNING (tool, argumento, sesión) y la tool corre con los argumentos válidos.
Se prueba con la activity real de producción y la tool real.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from exoclaw_temporal.config import ExecuteToolInput, WorkspaceConfig
from loguru import logger
from temporalio.testing import ActivityEnvironment

from src.platform import tool_extensions
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.platform.temporal.activities import execute_tool
from src.plugins.chats.agent.sales.tools.ui_intents import PresentProductsTool

SESSION = "wa_573001234567"


def _raw(handle: str) -> dict[str, Any]:
    return {
        "id": f"prod_{handle}",
        "handle": handle,
        "title": handle.replace("-", " ").title(),
        "status": "published",
        "thumbnail": f"https://img.test/{handle}.webp",
        "variants": [
            {
                "id": f"variant_{handle}",
                "title": "Unico",
                "sku": f"HUB-{handle.upper()}",
                "prices": [{"amount": "23000", "currency_code": "cop"}],
            }
        ],
        "categories": ["velones"],
        "category_labels": {"velones": "Velones"},
    }


def _catalog(tmp_path: Path) -> LocalSnapshotCatalogClient:
    snap = tmp_path / "catalog"
    snap.mkdir()
    products = [_raw("velon-rojo"), _raw("velon-verde")]
    (snap / "snapshot.json").write_text(json.dumps(products), encoding="utf-8")
    (snap / "manifest.json").write_text(
        json.dumps(
            {
                "version": "v-test",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "product_count": len(products),
            }
        ),
        encoding="utf-8",
    )
    return LocalSnapshotCatalogClient(snap)


@pytest.fixture
def _only_present_products(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """El worker de ventas registra `present_products` así (factory por
    workspace); acá es la única tool del registry."""
    catalog = _catalog(tmp_path)
    monkeypatch.setattr(
        tool_extensions,
        "_EXTENSIONS",
        {"sales.present_products": lambda ws: PresentProductsTool(workspace=str(ws), catalog=catalog)},
    )


@pytest.fixture
def warnings() -> Any:
    records: list[Any] = []
    sink = logger.add(lambda m: records.append(m.record), level="WARNING")
    yield records
    logger.remove(sink)


async def _execute(tmp_path: Path, params: dict[str, Any]) -> str:
    return await ActivityEnvironment().run(
        execute_tool,
        ExecuteToolInput(
            name="present_products",
            params=params,
            session_id=SESSION,
            channel="whatsapp",
            chat_id=SESSION,
            workspace=WorkspaceConfig(path=str(tmp_path / "ws")),
        ),
    )


@pytest.mark.asyncio
async def test_present_products_with_an_invented_arg_runs_with_the_valid_ones(
    tmp_path: Path, _only_present_products: None, warnings: list[Any]
) -> None:
    out = await _execute(tmp_path, {"intro_text": "Mira nuestros velones:", "intro_title": "Velones"})

    payload = json.loads(out)
    assert payload["queued"] is True
    assert payload["count"] == 2
    [warning] = [r for r in warnings if "intro_title" in r["message"]]
    assert "present_products" in warning["message"]
    assert SESSION in warning["message"]
