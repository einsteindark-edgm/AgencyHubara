"""Activities de contexto del gancho (I/O acá; la transformación en use_cases).

`read_remarketing_context_activity` lee del vault el metadata de la sesión y
el transcript JSONL (`<vault>/<sid>/sessions/<sid>.jsonl` — convención del
`FilesystemMessageHistoryStore` de plataforma, el log que también lee el
dashboard). Tolerante: archivo ausente o líneas corruptas → contexto parcial,
nunca falla el gancho por esto.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from temporalio import activity

from src.plugins.chats.agent.remarketing.contracts import (
    RemarketingContext,
    RemarketingTriggerInput,
)
from src.plugins.chats.agent.remarketing.prompts import build_remarketing_trigger
from src.plugins.chats.agent.remarketing.use_cases.context import (
    catalog_facts_for,
    context_from_metadata,
    customer_text_for,
)
from src.plugins.chats.agent.remarketing.use_cases.product_truth import (
    unavailable_terms,
)
from src.sdk.catalogkit import CatalogError, get_catalog_client

#: el snapshot publicado son ~30 productos; el tope solo evita un prompt
#: desbordado si el catálogo crece.
_CATALOG_LIMIT = 200


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


@activity.defn(name="read_remarketing_context_activity")
async def read_remarketing_context_activity(session_id: str) -> RemarketingContext:
    # Import local: el valor se resuelve al CALL time para que el fixture
    # `_isolate_vault_dir` de tests pueda re-bindear `src.sdk.runtime`.
    from src.sdk.runtime import WORKSPACE_VAULT_DIR

    session_dir = Path(WORKSPACE_VAULT_DIR) / session_id
    metadata = _read_json(session_dir / "metadata.json")
    events = _read_jsonl(session_dir / "sessions" / f"{session_id}.jsonl")
    context = context_from_metadata(metadata, events, now_ms=int(time.time() * 1000))
    products = await _catalog_products()
    if not products:
        return context
    mentioned = f"{context.tag_motivo}\n{context.transcript}"
    return replace(
        context,
        catalog_facts=catalog_facts_for(products, mentioned=mentioned),
        unavailable_terms=unavailable_terms(customer_text_for(metadata, events), products),
    )


async def _catalog_products() -> list[Any]:
    """Productos del snapshot que lee Ventas; [] si no está (nunca tumba el gancho).

    Sin catálogo el trigger le prohíbe al LLM afirmar atributos de producto:
    degradar a "no sé" es seguro, inventar no (incidente 2026-09-25).
    """
    try:
        result = await get_catalog_client().search("", limit=_CATALOG_LIMIT)
    except (CatalogError, OSError, ValueError) as exc:
        activity.logger.warning("remarketing: catálogo no disponible para el gancho: %s", exc)
        return []
    return list(result.results)


@activity.defn(name="build_remarketing_trigger_v2_activity")
async def build_remarketing_trigger_v2_activity(input: RemarketingTriggerInput) -> str:
    """Trigger con contexto real (motivo del tag + draft + transcript)."""
    return build_remarketing_trigger(
        input.motivo,
        input.memory_context,
        has_order_draft=input.has_order_draft,
        transcript=input.transcript,
        touch_number=input.touch_number,
        silence_minutes=input.silence_minutes,
        campaign_context=input.campaign_context,
        catalog_facts=input.catalog_facts,
        unavailable_terms=input.unavailable_terms,
    )
