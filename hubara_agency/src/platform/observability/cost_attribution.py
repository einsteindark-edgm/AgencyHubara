"""Activity de atribución de costos — resuelve el `episode_id` activo (HU-003 A7).

El costo del LLM (``gen_ai.usage.cost``) se etiqueta con ``session.id`` (= ``wa_<número>``)
+ ``episode.id`` para sectorizar el gasto por conversación en SigNoz. ``session.id`` ya
está en el workflow, pero ``episode.id`` vive en ``metadata.json`` (``episodes[]``) y es
**per-turn**. ``run_agent_turn`` llama esta activity (read-only) para resolverlo y lo
setea como baggage; el ``TracingInterceptor`` de Temporal lo propaga al span gen_ai de
OpenLIT, y el ``BaggageSpanProcessor`` lo copia como atributo.

Vive en ``platform/`` (no en el plugin ``chats``) porque la consumen los DOS sub-agentes
(sales + remarketing) vía el helper compartido ``run_agent_turn`` — regla DEHA: las
lecturas de estado compartido cruzan por ``platform/``, no por imports cross-agente
(mismo razonamiento que ``FilesystemMetadataStore`` en ``platform/state.py``).

DEHA:
  * **R-STATELESS**: sin cache module-level; lee ``metadata.json`` en cada llamada.
  * **R-JSON**: in (``str``) / out (``str``). ``""`` = sin episodio activo (evita
    ``Optional`` sobre el boundary; el workflow trata ``""`` como ausencia → no setea
    ``episode.id``).
  * **R-DIP**: NO importa nada de ``src.plugins`` — por eso inlinea el predicado de
    "episodio activo" en vez de reusar ``chats…get_active_episode`` (import-linter
    flaggea ``platform → plugins`` incluso en imports diferidos).
  * **Sin heartbeat**: read trivial de un JSON chico (R-HEARTBEAT n/a).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from temporalio import activity

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.observability.pricing import (
    compute_llm_cost_usd,
    load_pricing_table,
)
from src.platform.state import FilesystemMetadataStore


def _active_episode_id(metadata: dict[str, Any]) -> str:
    """Devuelve el ``episode_id`` del último episodio si está ACTIVO, sino ``""``.

    Espejo del canónico ``get_active_episode`` de
    ``src/plugins/chats/agent/sales/use_cases/episode_lifecycle.py`` (un episodio está
    activo sii es el último y ``closed_at_ms is None``). Se **inlinea** — NO se importa —
    porque ``platform`` no puede depender de ``plugins`` (R-DIP). Mantener en sync con
    esa función; el predicado es estable y está documentado allí.
    """
    episodes = metadata.get("episodes")
    if not episodes:
        return ""
    last = episodes[-1]
    if not isinstance(last, dict) or last.get("closed_at_ms") is not None:
        return ""
    return str(last.get("episode_id") or "")


@activity.defn(name="get_active_episode_id")
async def get_active_episode_id_activity(session_id: str) -> str:
    """Resuelve el ``episode_id`` activo de la sesión para atribución de costos.

    Read-only e idempotente. Devuelve ``""`` si no hay episodio activo o no hay
    metadata (sesión nueva) — el workflow lo trata como "atribuir solo por número".
    """
    metadata = FilesystemMetadataStore(WORKSPACE_VAULT_DIR).read(session_id)
    return _active_episode_id(metadata)


@dataclass(frozen=True)
class RecordEpisodeLLMUsageInput:
    """Input de ``record_episode_llm_usage_activity`` (R-JSON, frozen)."""

    session_id: str
    episode_id: str
    prompt_tokens: int
    completion_tokens: int
    model: str


def _apply_episode_llm_usage(
    metadata: dict[str, Any],
    *,
    episode_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    model: str,
    dedup_key: str,
    pricing_table: dict[str, dict[str, Any]],
) -> bool:
    """Suma tokens+costo al episodio ``episode_id`` dentro de ``metadata`` (mutates).

    Idempotente por ``dedup_key``: si ya se registró, no-op. Devuelve ``True`` si
    aplicó (el caller debe persistir), ``False`` si fue no-op (episodio inexistente /
    ya contado / sin tokens). **Pura** (sin I/O) → unit-testable sin contexto Temporal.
    """
    if prompt_tokens <= 0 and completion_tokens <= 0:
        return False
    episodes = metadata.get("episodes")
    if not isinstance(episodes, list):
        return False
    ep = next(
        (
            e
            for e in episodes
            if isinstance(e, dict) and e.get("episode_id") == episode_id
        ),
        None,
    )
    if ep is None:
        return False  # episodio no encontrado (cerrado/borrado)

    # Dedup (sibling interno; el API lee `llm_usage`, ignora esto).
    counted = ep.setdefault("_llm_counted", [])
    if dedup_key and dedup_key in counted:
        return False  # retry → ya contado
    if dedup_key:
        counted.append(dedup_key)

    cost = compute_llm_cost_usd(model, prompt_tokens, completion_tokens, pricing_table)
    usage = ep.setdefault(
        "llm_usage",
        {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        },
    )
    usage["prompt_tokens"] += prompt_tokens
    usage["completion_tokens"] += completion_tokens
    usage["total_tokens"] += prompt_tokens + completion_tokens
    usage["cost_usd"] = round(usage["cost_usd"] + cost, 8)
    return True


@activity.defn(name="record_episode_llm_usage")
async def record_episode_llm_usage_activity(
    input: RecordEpisodeLLMUsageInput,
) -> None:
    """Acumula tokens + costo USD del turno en ``episode.llm_usage`` (metadata.json).

    Persiste el costo LLM por episodio (= por venta) como **dato de negocio** para el
    frontend — independiente del path de observabilidad (SigNoz). El costo queda
    **congelado** a la tarifa del momento (lo que realmente costó esa conversación).

    **Idempotente** vía ``activity.info().activity_id`` (estable entre reintentos de
    Temporal) → un retry NO vuelve a sumar. Busca el episodio por id (genérico, no
    necesariamente el activo — el turno puede haberlo cerrado).

    DEHA: R-STATELESS (sin cache; lee pricing + metadata por llamada), R-JSON (in
    frozen / out None), R-DIP (NO importa ``src.plugins`` — busca el episodio por id
    sin la lógica de chats). Sin heartbeat (read+write de un JSON chico).
    """
    try:
        dedup_key = activity.info().activity_id  # estable entre reintentos
    except RuntimeError:
        dedup_key = ""  # fuera de contexto activity (tests directos)

    store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    metadata = store.read(input.session_id)
    applied = _apply_episode_llm_usage(
        metadata,
        episode_id=input.episode_id,
        prompt_tokens=input.prompt_tokens,
        completion_tokens=input.completion_tokens,
        model=input.model,
        dedup_key=dedup_key,
        pricing_table=load_pricing_table(),
    )
    if applied:
        store.write(input.session_id, metadata)
