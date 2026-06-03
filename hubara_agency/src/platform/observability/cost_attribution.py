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

from typing import Any

from temporalio import activity

from src.platform.config import WORKSPACE_VAULT_DIR
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
