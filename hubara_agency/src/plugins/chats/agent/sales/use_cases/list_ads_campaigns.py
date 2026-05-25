"""Use case: agregar campañas y conversaciones atribuidas desde el vault.

Lee todos los `<vault_dir>/wa_*/metadata.json` y agrupa por
`origin.source_id` para construir el listado de campañas que el frontend
del plugin `ads` muestra. Las conversaciones atribuidas son las sesiones
WhatsApp cuyo `origin.source_id` coincide con un campaign_id dado.

Datos disponibles vs faltantes:

  | Disponible hoy desde metadata                  | Faltante (queda None)         |
  |------------------------------------------------|-------------------------------|
  | id (source_id), name (headline), source_type,  | spend, revenue, impressions,  |
  | started count, first/last_seen_ms              | reach, clicks, status,        |
  |                                                | objective, placement, audience,|
  |                                                | ad_set, creative_title, etc.  |

El frontend marca los campos None con `"—" + icon dataPending` para que
se vea qué falta y luego se integre vía Meta Ads API en otro PR.

DEHA:
- Función pura, sin Temporal, sin async — el endpoint la llama sync.
- DTOs `@dataclass(frozen=True)` JSON-serializable (R-JSON).
- `vault_dir` por DI explícita — no se usa global del módulo (R-STATELESS).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Canales que cuentan como "campaña" — `direct` queda fuera por diseño.
_CAMPAIGN_CHANNELS: frozenset[str] = frozenset({"ad", "post", "web_referral"})


@dataclass(frozen=True)
class AdsCampaignSummary:
    """Campaña agregada desde sesiones WhatsApp con el mismo source_id.

    Los campos `None` son aquellos que aún no podemos derivar del vault —
    requieren integración con Meta Ads API (impressions, spend, etc.) o
    con orders (revenue) o con un agente de clasificación (counts por
    estado conversacional).
    """

    # --- Disponibles hoy ---
    id: str  # source_id del referral
    name: str | None  # headline del referral más reciente
    source_type: str | None  # "ad" | "post" | "web_referral"
    started: int  # count de sesiones únicas con ese source_id
    first_seen_ms: int | None
    last_seen_ms: int | None

    # --- Faltantes (queda None — frontend marca visual) ---
    spend: float | None = None
    revenue: float | None = None
    impressions: int | None = None
    reach: int | None = None
    clicks: int | None = None
    status: str | None = None  # "active" | "paused" — Meta Ads API
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
    """Conversación WhatsApp atribuida a una campaña (origin.source_id match).

    Campos `None` requieren integración futura (CRM para `name`/`city`,
    agente clasificador para `state`, orders para `value`).
    """

    # --- Disponibles hoy ---
    id: str  # session_id (e.g. "wa_5491111111111")
    phone_number: str  # sin prefijo wa_
    started_at_ms: int  # origin.first_seen_ms
    last_msg_at_ms: int | None  # mtime del JSONL o last_touch.seen_at_ms
    msgs_count: int  # líneas del JSONL
    ad_headline: str | None  # origin.headline
    agent: str | None  # active_route (proxy temporal — "ventas"/etc.)

    # --- Faltantes (queda None — frontend marca visual) ---
    name: str | None = None  # nombre legible — no tenemos CRM
    city: str | None = None
    state: str | None = None  # AdsState ("nuevo"/"calificado"/...)
    value: float | None = None  # valor estimado COP — necesita orders link


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


def _is_campaign_origin(origin: dict[str, Any] | None) -> bool:
    """True si la sesión tiene un origin clasificado como campaña
    (ad/post/web_referral) y con source_id presente.

    Excluye `direct` (no es campaña) y casos defensivos donde source_id
    falta por bug upstream.
    """
    if not origin:
        return False
    if origin.get("channel") not in _CAMPAIGN_CHANNELS:
        return False
    if not origin.get("source_id"):
        return False
    return True


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
    """Mejor estimación del `last message at` en ms epoch.

    Prioridad:
      1. mtime del JSONL si existe (refleja último write real).
      2. `last_touch.seen_at_ms` (siempre lo escribimos en cada inbound).
      3. `origin.first_seen_ms` (fallback — al menos el primer touch).
    """
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


# =============================================================================
# Public API
# =============================================================================


def list_ads_campaigns(vault_dir: Path) -> list[AdsCampaignSummary]:
    """Lista de campañas únicas detectadas en el vault.

    Agrupa por `origin.source_id`. Para cada campaña:
      - `name` = headline del referral con `first_seen_ms` más reciente
        dentro del grupo (proxy de "creativo más reciente").
      - `started` = count de sesiones distintas con ese source_id.
      - `first/last_seen_ms` = min/max sobre todas las sesiones del grupo.

    Sesiones con `origin.channel='direct'` o sin origin/source_id quedan
    excluidas (no son campañas). Retorna lista ordenada por
    `last_seen_ms` descendente.
    """
    # source_id -> bucket de info acumulada
    buckets: dict[str, dict[str, Any]] = {}

    for session_dir in _iter_session_dirs(vault_dir):
        metadata = _read_metadata(session_dir)
        if metadata is None:
            continue
        origin = metadata.get("origin")
        if not _is_campaign_origin(origin):
            continue

        source_id = origin["source_id"]
        channel = origin.get("channel")
        headline = origin.get("headline")
        first_seen = origin.get("first_seen_ms")

        bucket = buckets.setdefault(
            source_id,
            {
                "source_type": channel,
                "started": 0,
                "first_seen_ms": first_seen,
                "last_seen_ms": first_seen,
                "name": headline,
                "_name_at_ms": first_seen,  # tracking para "más reciente wins"
            },
        )
        bucket["started"] += 1

        # min/max sobre first_seen del grupo (proxy de range temporal)
        if isinstance(first_seen, int):
            if bucket["first_seen_ms"] is None or first_seen < bucket["first_seen_ms"]:
                bucket["first_seen_ms"] = first_seen
            if bucket["last_seen_ms"] is None or first_seen > bucket["last_seen_ms"]:
                bucket["last_seen_ms"] = first_seen

        # headline del más reciente (proxy de "creativo actual")
        if (
            headline
            and isinstance(first_seen, int)
            and (bucket["_name_at_ms"] is None or first_seen > bucket["_name_at_ms"])
        ):
            bucket["name"] = headline
            bucket["_name_at_ms"] = first_seen

    summaries = [
        AdsCampaignSummary(
            id=source_id,
            name=bucket["name"],
            source_type=bucket["source_type"],
            started=bucket["started"],
            first_seen_ms=bucket["first_seen_ms"],
            last_seen_ms=bucket["last_seen_ms"],
        )
        for source_id, bucket in buckets.items()
    ]

    # Ordenar por last_seen descendente — None al final defensivamente
    summaries.sort(
        key=lambda c: c.last_seen_ms if c.last_seen_ms is not None else -1,
        reverse=True,
    )
    return summaries


def list_attributed_conversations(
    vault_dir: Path, campaign_id: str
) -> list[AdsAttributedConversation]:
    """Conversaciones WhatsApp cuyo `origin.source_id == campaign_id`.

    Ordenadas por `started_at_ms` descendente (más recientes primero).
    Retorna [] si el campaign_id no existe o el vault está vacío.
    """
    convs: list[AdsAttributedConversation] = []

    for session_dir in _iter_session_dirs(vault_dir):
        metadata = _read_metadata(session_dir)
        if metadata is None:
            continue
        origin = metadata.get("origin")
        if not _is_campaign_origin(origin):
            continue
        if origin["source_id"] != campaign_id:
            continue

        session_id = session_dir.name
        phone = session_id[len("wa_"):]
        started_at_ms = origin.get("first_seen_ms") or 0
        last_touch = metadata.get("last_touch")
        last_msg_ms = _last_msg_at_ms(session_dir, last_touch, origin)
        msgs_count = _count_history_lines(_history_jsonl_path(session_dir))

        convs.append(
            AdsAttributedConversation(
                id=session_id,
                phone_number=phone,
                started_at_ms=started_at_ms,
                last_msg_at_ms=last_msg_ms,
                msgs_count=msgs_count,
                ad_headline=origin.get("headline"),
                agent=metadata.get("active_route"),
                # name/city/state/value quedan None — UI marca visual
            )
        )

    convs.sort(key=lambda c: c.started_at_ms, reverse=True)
    return convs
