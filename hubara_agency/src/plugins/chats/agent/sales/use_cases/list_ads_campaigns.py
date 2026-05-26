"""Use case: agregar campañas y conversaciones atribuidas desde el vault.

Lee todos los `<vault_dir>/wa_*/metadata.json` y agrupa por
`origin.source_id` para construir el listado de campañas que el frontend
del plugin `ads` muestra. Cada **episodio** de cada sesión genera una
conversación atribuida (un mismo cliente con N episodios → N filas).

Datos disponibles vs faltantes:

  | Disponible hoy desde metadata                  | Faltante (queda None)         |
  |------------------------------------------------|-------------------------------|
  | id (source_id), name (headline), source_type,  | spend, revenue, impressions,  |
  | episodes_started count, first/last_seen_ms,    | reach, clicks, status,        |
  | state por episodio (classifier),               | objective, placement, audience,|
  | conversations counts agregados por estado      | ad_set, creative_title, etc.  |

El frontend marca los campos None con `"—" + icon dataPending` para que
se vea qué falta y luego se integre vía Meta Ads API en otro PR.

Episodios: una sesión puede tener N episodios a lo largo del tiempo
(cliente compra hoy + vuelve en 2 meses a cotizar). Cada episodio es una
conversación con su propio `AdsState`. Sesiones legacy sin `episodes[]`
se tratan como un solo "pseudo-episodio" derivado del estado raíz.

DEHA:
- Función pura, sin Temporal, sin async — el endpoint la llama sync.
- DTOs `@dataclass(frozen=True)` JSON-serializable (R-JSON).
- `vault_dir` por DI explícita — no se usa global del módulo (R-STATELESS).
- `now_ms` se computa una sola vez por request al inicio del listing.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from src.plugins.chats.agent.sales.use_cases.classify_conversation_state import (
    VALID_STATES,
    classify_episode_state,
    classify_state,
)

logger = logging.getLogger(__name__)


# Canales que cuentan como "campaña Meta atribuible" — se agrupan por
# `origin.source_id`. Cada source_id es una campaña distinta.
_META_CHANNELS: frozenset[str] = frozenset({"ad", "post", "web_referral"})

# El canal `direct` (cliente escribió sin venir de ad/post) NO tiene
# source_id propio. Se agrupa en una "campaña sintética" con id sentinel
# `"direct"` para que aparezca en la lista del dashboard ads y se puedan
# ver todas las conversaciones orgánicas en un solo drill-down.
DIRECT_CAMPAIGN_ID = "direct"
DIRECT_CAMPAIGN_NAME = "Clientes directos · sin campaña"


@dataclass(frozen=True)
class AdsCampaignSummary:
    """Campaña agregada desde sesiones WhatsApp con el mismo source_id.

    `started` cuenta EPISODIOS (no sesiones únicas) — un cliente con N
    episodios contribuye N veces al counter. `conversations` counts también
    son por episodio.
    """

    # --- Disponibles hoy ---
    id: str  # source_id del referral
    name: str | None  # headline del referral más reciente
    source_type: str | None  # "ad" | "post" | "web_referral" | "direct"
    started: int  # count de episodios totales con ese source_id
    first_seen_ms: int | None
    last_seen_ms: int | None
    # Counts por AdsState (nuevo/activo/calificado/cotizado/ganado/perdido/no_reply).
    # Agrega los episodios de TODAS las sesiones del grupo.
    conversations: dict[str, int] | None = None

    # --- Faltantes (queda None — frontend marca visual) ---
    spend: float | None = None
    revenue: float | None = None
    impressions: int | None = None
    reach: int | None = None
    clicks: int | None = None
    status: str | None = None
    objective: str | None = None
    placement: str | None = None
    audience: str | None = None
    ad_set: str | None = None
    creative_title: str | None = None
    template: str | None = None
    meta_campaign_id: str | None = None
    avg_ticket: float | None = None
    first_resp: str | None = None
    tendency: str | None = None
    days_run: int | None = None


@dataclass(frozen=True)
class AdsAttributedConversation:
    """Conversación WhatsApp atribuida a una campaña. **Una por episodio**
    cuando la sesión tiene `episodes[]`. Para sesiones legacy sin episodes,
    una sola fila por sesión.
    """

    # --- Disponibles hoy ---
    id: str  # "wa_<phone>__<episode_id>" o "wa_<phone>" (legacy)
    phone_number: str  # sin prefijo wa_, sin episode suffix
    episode_id: str | None  # None para legacy sin episodes[]
    started_at_ms: int
    last_msg_at_ms: int | None
    msgs_count: int
    ad_headline: str | None
    agent: str | None
    state: str | None = None  # AdsState derivado del classifier

    # --- Faltantes (queda None — frontend marca visual) ---
    name: str | None = None
    city: str | None = None
    value: float | None = None


# =============================================================================
# Lectura de vault
# =============================================================================


def _iter_session_dirs(vault_dir: Path):
    """Itera los `wa_*` subdirectorios del vault. Tolera vault inexistente."""
    if not vault_dir.exists() or not vault_dir.is_dir():
        return
    for entry in vault_dir.iterdir():
        if entry.is_dir() and entry.name.startswith("wa_"):
            yield entry


def _read_metadata(session_dir: Path) -> dict[str, Any] | None:
    """Lee `metadata.json` tolerando corrupción. None si no existe o falla."""
    path = session_dir / "metadata.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _is_meta_campaign_origin(origin: dict[str, Any] | None) -> bool:
    """True si el origin es una campaña Meta atribuible (ad/post/web_referral
    con source_id). Se agrupa por source_id en `list_ads_campaigns`."""
    if not origin:
        return False
    if origin.get("channel") not in _META_CHANNELS:
        return False
    if not origin.get("source_id"):
        return False
    return True


def _is_direct_origin(origin: dict[str, Any] | None) -> bool:
    """True si la sesión es un cliente directo (sin atribución a ad/post)."""
    return bool(origin) and origin.get("channel") == "direct"


def _history_jsonl_path(session_dir: Path) -> Path:
    """Convención de exoclaw: `<session_dir>/sessions/<session_id>.jsonl`."""
    return session_dir / "sessions" / f"{session_dir.name}.jsonl"


def _count_history_lines(jsonl_path: Path) -> int:
    """Cuenta líneas no vacías del JSONL. 0 si no existe o falla."""
    if not jsonl_path.exists():
        return 0
    try:
        with jsonl_path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def _last_msg_at_ms(
    session_dir: Path, last_touch: dict[str, Any] | None, origin: dict[str, Any]
) -> int | None:
    """Mejor estimación del `last message at` en ms epoch."""
    jsonl = _history_jsonl_path(session_dir)
    if jsonl.exists():
        try:
            return int(jsonl.stat().st_mtime * 1000)
        except OSError:
            pass
    if last_touch and isinstance(last_touch.get("seen_at_ms"), int):
        return last_touch["seen_at_ms"]
    fseen = origin.get("first_seen_ms")
    if isinstance(fseen, int):
        return fseen
    return None


def _empty_state_counts() -> dict[str, int]:
    """Dict inicializado a 0 para cada estado válido."""
    return {state: 0 for state in VALID_STATES}


def _iter_episodes(
    metadata: dict[str, Any],
    *,
    session_dir: Path,
    origin: dict[str, Any],
    last_touch: dict[str, Any] | None,
    total_msgs: int,
    last_msg_ms: int | None,
    now_ms: int,
) -> Iterator[tuple[dict[str, Any] | None, str]]:
    """Yields (episode_dict_or_None, state) por cada episodio de la sesión.

    - Si `metadata.episodes` está poblado → yields uno por episodio.
      `episode_dict` es el dict tal cual, `state` se computa con
      `classify_episode_state`. Para episodios cerrados, `current_tag=None`.
      Para el episodio activo (último sin cerrar), `current_tag=metadata.tag`.
    - Si NO hay `episodes[]` (legacy) → yields un solo `(None, state)`
      donde `state` se computa con `classify_state` legacy.
    """
    episodes = metadata.get("episodes")

    if not episodes:
        # Legacy fallback: una "pseudo-conversación" por sesión.
        state = classify_state(
            metadata,
            total_msgs=total_msgs,
            last_inbound_ms=last_msg_ms,
            now_ms=now_ms,
        )
        yield (None, state)
        return

    current_tag = metadata.get("tag")
    last_idx = len(episodes) - 1

    for idx, ep in enumerate(episodes):
        is_active = idx == last_idx and ep.get("closed_at_ms") is None
        # current_tag solo aplica al episodio activo. Para episodios cerrados,
        # closing_tag ya está en el episode dict.
        tag_for_episode = current_tag if is_active else None
        state = classify_episode_state(
            ep,
            current_tag=tag_for_episode,
            total_msgs=total_msgs,
            last_inbound_ms=last_msg_ms if is_active else ep.get("closed_at_ms"),
            now_ms=now_ms,
        )
        yield (ep, state)


def _episode_to_campaign_id(
    episode: dict[str, Any] | None,
    session_origin: dict[str, Any],
) -> str | None:
    """Determina a qué campaña pertenece un episodio (FU2: re-atribución).

    Prefiere el `referral_snapshot` del episodio sobre el `origin` sticky
    de la sesión. Caso de uso: cliente vino desde AD_A (sticky origin),
    compró, volvió 2 meses después desde AD_B → ep_002 debe contabilizar
    en AD_B aunque el origin de la sesión siga apuntando a AD_A.

    Prioridad:
      1. `episode.referral_snapshot.channel` ∈ meta + `source_id` → ese source_id.
      2. `episode.referral_snapshot.channel == direct` → DIRECT_CAMPAIGN_ID.
      3. Fallback: session_origin (sticky first-touch).
      4. Si ninguno aplica → None (defensivo, no agrupa).

    `episode=None` se interpreta como sesión legacy sin `episodes[]` →
    cae directo al fallback de session_origin.
    """
    if episode is not None:
        snap = episode.get("referral_snapshot") or {}
        snap_channel = snap.get("channel")
        snap_source = snap.get("source_id")

        if snap_channel in _META_CHANNELS and snap_source:
            return snap_source
        if snap_channel == "direct":
            return DIRECT_CAMPAIGN_ID

    # Fallback al session origin (legacy sin snapshot, o snapshot ambiguo)
    if _is_meta_campaign_origin(session_origin):
        return session_origin["source_id"]
    if _is_direct_origin(session_origin):
        return DIRECT_CAMPAIGN_ID
    return None


def _episode_source_type(
    episode: dict[str, Any] | None, session_origin: dict[str, Any]
) -> str | None:
    """Devuelve el `source_type` (ad/post/web_referral/direct) que aplica
    a un episodio para reporting. Mismo orden de prioridad que
    `_episode_to_campaign_id`: snapshot del episodio sobre origin sticky.
    """
    if episode is not None:
        snap = episode.get("referral_snapshot") or {}
        snap_channel = snap.get("channel")
        if snap_channel:
            return snap_channel
    return session_origin.get("channel")


def _episode_headline(
    episode: dict[str, Any] | None, session_origin: dict[str, Any]
) -> str | None:
    """Devuelve el headline efectivo del episodio: snapshot del episodio si
    existe, sino origin de la sesión."""
    if episode is not None:
        snap = episode.get("referral_snapshot") or {}
        if snap.get("headline"):
            return snap["headline"]
    return session_origin.get("headline")


def _episode_msgs_count(
    episode: dict[str, Any] | None, total_msgs: int
) -> int:
    """Cuenta mensajes que pertenecen al episodio (FU3).

    Prioridad:
      - Episodio cerrado con snapshots: `msgs_count_at_close - msgs_count_at_start`.
      - Episodio activo con snapshot start: `total_msgs - msgs_count_at_start`.
      - Sin snapshots (legacy / pre-FU3): `total_msgs` global como fallback.

    El fallback pierde precisión en multi-episodio legacy, pero mantiene
    consistencia con el comportamiento previo y nunca devuelve negativos.
    """
    if episode is None:
        return total_msgs
    at_start = episode.get("msgs_count_at_start")
    at_close = episode.get("msgs_count_at_close")
    if isinstance(at_start, int) and isinstance(at_close, int):
        return max(0, at_close - at_start)
    if isinstance(at_start, int):
        return max(0, total_msgs - at_start)
    return total_msgs


# =============================================================================
# Public API
# =============================================================================


def list_ads_campaigns(vault_dir: Path) -> list[AdsCampaignSummary]:
    """Lista de campañas únicas detectadas en el vault.

    Agrupación POR EPISODIO (FU2). Cada episodio se asigna a un bucket
    según su `referral_snapshot.channel` + `source_id`:

    - Snapshot meta con source_id → bucket `<source_id>`
      (`source_type` = ad/post/web_referral del snapshot).
    - Snapshot direct → bucket `DIRECT_CAMPAIGN_ID`.
    - Episodio legacy sin snapshot → fallback al `session.origin`.

    Esto permite **re-atribución por episodio**: un cliente con ep_001
    desde AD_A y ep_002 desde AD_B contribuye a ambas campañas, no solo
    a la sticky.

    Campos agregados:
      - `started` = count de EPISODIOS totales del bucket.
      - `conversations` = counts por estado, agregando todos los episodios.
      - `name` = headline del referral del episodio más reciente del bucket.
      - `first/last_seen_ms` = min/max sobre episodios del bucket.

    Retorna lista ordenada por `last_seen_ms` descendente.
    """
    now_ms = int(time.time() * 1000)
    buckets: dict[str, dict[str, Any]] = {}

    for session_dir in _iter_session_dirs(vault_dir):
        metadata = _read_metadata(session_dir)
        if metadata is None:
            continue
        origin = metadata.get("origin") or {}

        last_touch = metadata.get("last_touch")
        last_msg_ms = _last_msg_at_ms(session_dir, last_touch, origin)
        msgs_count = _count_history_lines(_history_jsonl_path(session_dir))

        for ep, state in _iter_episodes(
            metadata,
            session_dir=session_dir,
            origin=origin,
            last_touch=last_touch,
            total_msgs=msgs_count,
            last_msg_ms=last_msg_ms,
            now_ms=now_ms,
        ):
            campaign_id = _episode_to_campaign_id(ep, origin)
            if campaign_id is None:
                # Sin origin clasificable — episodio no entra al dashboard
                continue

            source_type = _episode_source_type(ep, origin)
            headline = _episode_headline(ep, origin)
            ep_started_ms = (
                ep.get("started_at_ms") if ep is not None else origin.get("first_seen_ms")
            )

            bucket = buckets.setdefault(
                campaign_id,
                {
                    "source_type": source_type,
                    "started": 0,
                    "first_seen_ms": ep_started_ms,
                    "last_seen_ms": ep_started_ms,
                    "name": headline,
                    "_name_at_ms": ep_started_ms,
                    "counts": _empty_state_counts(),
                },
            )
            bucket["started"] += 1
            bucket["counts"][state] = bucket["counts"].get(state, 0) + 1

            if isinstance(ep_started_ms, int):
                if (
                    bucket["first_seen_ms"] is None
                    or ep_started_ms < bucket["first_seen_ms"]
                ):
                    bucket["first_seen_ms"] = ep_started_ms
                if (
                    bucket["last_seen_ms"] is None
                    or ep_started_ms > bucket["last_seen_ms"]
                ):
                    bucket["last_seen_ms"] = ep_started_ms

            # name del episodio más reciente del bucket
            if (
                headline
                and isinstance(ep_started_ms, int)
                and (
                    bucket["_name_at_ms"] is None
                    or ep_started_ms > bucket["_name_at_ms"]
                )
            ):
                bucket["name"] = headline
                bucket["_name_at_ms"] = ep_started_ms

    summaries: list[AdsCampaignSummary] = []
    for camp_id, bucket in buckets.items():
        # Para el bucket direct, override del display name (ignorando
        # headlines que pudieran haber colado).
        if camp_id == DIRECT_CAMPAIGN_ID:
            name = DIRECT_CAMPAIGN_NAME
            source_type = "direct"
        else:
            name = bucket["name"]
            source_type = bucket["source_type"]
        summaries.append(
            AdsCampaignSummary(
                id=camp_id,
                name=name,
                source_type=source_type,
                started=bucket["started"],
                first_seen_ms=bucket["first_seen_ms"],
                last_seen_ms=bucket["last_seen_ms"],
                conversations=bucket["counts"],
            )
        )

    summaries.sort(
        key=lambda c: c.last_seen_ms if c.last_seen_ms is not None else -1,
        reverse=True,
    )
    return summaries


def list_attributed_conversations(
    vault_dir: Path, campaign_id: str
) -> list[AdsAttributedConversation]:
    """Conversaciones WhatsApp atribuidas a una campaña.

    **Una conversación por episodio** cuando la sesión tiene `episodes[]`.
    Sesiones legacy sin episodes generan una sola conversación con
    `episode_id=None`.

    El bucket se determina POR EPISODIO (FU2: re-atribución):
    `_episode_to_campaign_id` consulta primero el `referral_snapshot` del
    episodio (channel + source_id) y cae al session origin como fallback.

    Ordenadas por `started_at_ms` descendente.
    """
    now_ms = int(time.time() * 1000)
    convs: list[AdsAttributedConversation] = []

    for session_dir in _iter_session_dirs(vault_dir):
        metadata = _read_metadata(session_dir)
        if metadata is None:
            continue
        origin = metadata.get("origin") or {}

        session_id = session_dir.name
        phone = session_id[len("wa_") :]
        last_touch = metadata.get("last_touch")
        last_msg_ms = _last_msg_at_ms(session_dir, last_touch, origin)
        msgs_count = _count_history_lines(_history_jsonl_path(session_dir))

        for ep, state in _iter_episodes(
            metadata,
            session_dir=session_dir,
            origin=origin,
            last_touch=last_touch,
            total_msgs=msgs_count,
            last_msg_ms=last_msg_ms,
            now_ms=now_ms,
        ):
            # Filtrado por episodio (no por sesión) — re-atribución FU2.
            ep_campaign_id = _episode_to_campaign_id(ep, origin)
            if ep_campaign_id != campaign_id:
                continue

            if ep is not None:
                # Modo nuevo: una conversación por episodio
                ep_id = ep["episode_id"]
                conv_id = f"{session_id}__{ep_id}"
                started = ep.get("started_at_ms") or 0
                # Para episodio cerrado, last_msg_at_ms = closed_at_ms
                # (mejor proxy del fin de actividad del cliente que el JSONL
                # mtime global de la sesión).
                if ep.get("closed_at_ms") is not None:
                    ep_last_msg = ep["closed_at_ms"]
                else:
                    ep_last_msg = last_msg_ms
                ad_headline = _episode_headline(ep, origin)
            else:
                # Legacy: una sola "pseudo-conversación" por sesión
                ep_id = None
                conv_id = session_id
                started = origin.get("first_seen_ms") or 0
                ep_last_msg = last_msg_ms
                ad_headline = origin.get("headline")

            convs.append(
                AdsAttributedConversation(
                    id=conv_id,
                    phone_number=phone,
                    episode_id=ep_id,
                    started_at_ms=started,
                    last_msg_at_ms=ep_last_msg,
                    msgs_count=_episode_msgs_count(ep, msgs_count),
                    ad_headline=ad_headline,
                    agent=metadata.get("active_route"),
                    state=state,
                )
            )

    convs.sort(key=lambda c: c.started_at_ms, reverse=True)
    return convs
