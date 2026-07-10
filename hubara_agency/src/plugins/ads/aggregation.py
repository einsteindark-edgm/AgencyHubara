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

import datetime
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from src.plugins.ads.classification import (
    VALID_STATES,
    classify_episode_state,
    classify_state,
)
from src.sdk.connectorkit import FilesystemAttributionStore

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

    # --- Derivados del vault (negocio congelado por episodio) ---
    # Ingreso atribuido (COP major units) — suma de `episode.order_total_cop`
    # de los episodios ganados del bucket. None si ningún episodio del bucket
    # tiene venta con total conocido.
    revenue: float | None = None
    # Ticket promedio (COP) = revenue / nº de episodios que aportaron ingreso.
    avg_ticket: float | None = None
    # Costo LLM agregado (USD) + tokens de TODOS los episodios del bucket —
    # de `episode.llm_usage`. None si ningún episodio acumuló uso LLM.
    llm_cost_usd: float | None = None
    llm_tokens: int | None = None
    # Duración media de los episodios CERRADOS del bucket (ms) — el "tiempo"
    # del embudo. None si no hay episodios cerrados con timestamps válidos.
    avg_episode_duration_ms: int | None = None
    # Cardinalidad de los promedios de arriba — sin ellas, agrupar buckets
    # (por campaña / por adset) no puede recomponer avg_ticket ni
    # avg_episode_duration_ms exactos (promediar promedios miente).
    revenue_count: int = 0
    duration_count: int = 0

    # --- Señal CAPI (Conversions API) reportada a Meta (fix 2026-07-01) ---
    # Eventos de atribución CTWA que este bucket le reportó a Meta. Los
    # counters arrancan en 0 (no None): "sin señal" es un cero real, no un
    # dato pendiente. Fuente: metadata["capi_events_sent"] (escrito por
    # send_capi_event_activity al cierre de cada episodio).
    capi_leads_sent: int = 0
    capi_purchases_sent: int = 0
    capi_failed: int = 0

    # --- Faltantes (queda None — frontend marca visual) ---
    spend: float | None = None
    impressions: int | None = None
    reach: int | None = None
    clicks: int | None = None
    # Conversaciones iniciadas reportadas por Meta (insights) — KPI "conv" +
    # costo/conv del canvas. Distinto de `started` (episodios del vault).
    messaging_conversations_started: int | None = None
    status: str | None = None
    objective: str | None = None
    placement: str | None = None
    audience: str | None = None
    ad_set: str | None = None
    creative_title: str | None = None
    # Thumbnail real del creativo (Graph creative{thumbnail_url}) — el preview
    # del inspector. None hasta que el enrichment lo resuelva.
    creative_thumbnail_url: str | None = None
    template: str | None = None
    meta_campaign_id: str | None = None
    # Segmento (ad set) de Meta al que pertenece el ad de este bucket —
    # resuelto vía Graph (enrich_campaign_names). None hasta resolver.
    meta_adset_id: str | None = None
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
    # Valor de la venta atribuida al episodio (COP major units) — de
    # `episode.order_total_cop` congelado al cierre (backfill desde
    # `registered_order` para ventas previas al freeze). None si el episodio
    # no cerró venta o el total no es recuperable.
    value: float | None = None

    # Duración del episodio (ms) = closed_at_ms - started_at_ms. None si el
    # episodio sigue activo o no tiene timestamps válidos.
    duration_ms: int | None = None

    # Costo LLM del episodio (USD, congelado a la tarifa del momento) + tokens
    # totales — de `episode.llm_usage` en metadata.json. None si el episodio aún
    # no acumuló uso (sesión legacy / episodio sin turnos LLM).
    llm_cost_usd: float | None = None
    llm_tokens: int | None = None

    # Evento CAPI reportado a Meta para este episodio (fix 2026-07-01):
    # "Purchase" | "LeadSubmitted" | None (nada reportado / falló). Purchase
    # pisa a LeadSubmitted (evento terminal — espejo de capi_terminal_event).
    capi_event: str | None = None


@dataclass(frozen=True)
class AdsDailySeriesPoint:
    """Un día de la serie temporal de una campaña: chats **iniciados ese día**
    (por `started_at_ms` del episodio) segmentados por su estado actual.

    `d` es la etiqueta visible ("29 abr", formato español) — el frontend la
    parte con `d.split(" ")[0]` para la etiqueta corta del eje X. Los counts
    arrancan en 0 para que los días sin actividad rendericen una columna vacía
    (la serie es continua, sin huecos).
    """

    d: str
    ganado: int = 0
    cotizado: int = 0
    calificado: int = 0
    activo: int = 0
    nuevo: int = 0
    no_reply: int = 0
    perdido: int = 0


# =============================================================================
# Lectura de vault
# =============================================================================


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
    """Cuenta líneas no vacías del JSONL. 0 si no existe o falla.

    Lee el archivo COMPLETO (no hay forma de contar líneas sin leerlo). Por eso
    su invocación se difiere vía `_make_line_counter` — el historial de una
    conversación crece sin límite y leerlo es el costo dominante a escala.
    """
    if not jsonl_path.exists():
        return 0
    try:
        with jsonl_path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def _make_line_counter(session_dir: Path) -> Callable[[], int]:
    """Devuelve un getter MEMOIZADO del conteo de líneas del history JSONL.

    Difiere la lectura del archivo hasta que algún consumidor realmente la
    necesite, y la cachea para que un mismo session_dir se lea a lo sumo una
    vez por request. La mayoría de los episodios (cerrados: order_id /
    closing_tag / closed_at_ms) se clasifican SIN el conteo → para ellos el
    JSONL nunca se abre. Esto convierte el costo de O(bytes de todo el
    historial del vault) a O(bytes de las conversaciones aún activas).
    """
    cache: list[int | None] = [None]

    def get() -> int:
        if cache[0] is None:
            cache[0] = _count_history_lines(_history_jsonl_path(session_dir))
        return cache[0]

    return get


def scan_ad_sessions(
    vault_dir: Path, *, since_ms: int | None = None
) -> list[tuple[Path, dict[str, Any]]]:
    """Lee + parsea los `wa_*/metadata.json` del vault una sola vez.

    Es el costo O(N sesiones) compartible: los 3 endpoints de ads consumen el
    MISMO set de metadata parseadas. La capa API lo cachea (TTL corto) y se lo
    pasa a las 3 funciones vía `sessions=`, colapsando los 3 scans por
    page-view en uno solo. Las funciones siguen siendo PURAS: con
    `sessions=None` escanean fresco (los tests no dependen del cache).

    `since_ms`: pre-filtro superset por mtime (nunca pierde data) — el filtro
    PRECISO por episodio lo hace cada use case sobre el resultado.

    F-SDK-4: el descubrimiento (glob + mtime-prefilter + parse tolerante) es
    del read model de atribución de PLATAFORMA (`AttributionReadPort` — un
    writer: el ingest; N readers: ads hoy, CAPI mañana). Este wrapper
    preserva la firma histórica `(session_dir, metadata)` que consume toda la
    agregación de este módulo + sus 53K de tests.
    """
    store = FilesystemAttributionStore(vault_dir)
    return [
        (s.session_dir, s.metadata)
        for s in store.scan_sessions(since_ms=since_ms)
    ]


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
    total_msgs_fn: Callable[[], int],
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

    `total_msgs_fn` es un getter LAZY del conteo de mensajes (lee el JSONL).
    Solo se invoca donde la clasificación realmente lo usa: el episodio ACTIVO
    sin tag de cierre (umbral "nuevo") y el fallback legacy. Los episodios
    CERRADOS se clasifican por order_id/closing_tag/closed_at_ms (reglas 1-6 de
    `classify_episode_state`) sin tocar el conteo → no se lee su historial.
    """
    episodes = metadata.get("episodes")

    if not episodes:
        # Legacy fallback: una "pseudo-conversación" por sesión.
        state = classify_state(
            metadata,
            total_msgs=total_msgs_fn(),
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
        # total_msgs solo lo consume la rama de episodio ACTIVO (umbral "nuevo").
        # Un episodio cerrado retorna antes (reglas 1-6) → no leemos su JSONL.
        total_msgs = total_msgs_fn() if is_active else 0
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
    episode: dict[str, Any] | None, total_msgs_fn: Callable[[], int]
) -> int:
    """Cuenta mensajes que pertenecen al episodio (FU3).

    Prioridad:
      - Episodio cerrado con snapshots: `msgs_count_at_close - msgs_count_at_start`
        (NO lee el JSONL — usa los snapshots congelados).
      - Episodio activo con snapshot start: `total_msgs - msgs_count_at_start`.
      - Sin snapshots (legacy / pre-FU3): `total_msgs` global como fallback.

    `total_msgs_fn` es el getter lazy: solo se invoca para episodios sin
    snapshot de cierre (activos o legacy). Los cerrados con ambos snapshots
    nunca abren el historial. El fallback pierde precisión en multi-episodio
    legacy, pero mantiene consistencia con el comportamiento previo y nunca
    devuelve negativos.
    """
    if episode is None:
        return total_msgs_fn()
    at_start = episode.get("msgs_count_at_start")
    at_close = episode.get("msgs_count_at_close")
    if isinstance(at_start, int) and isinstance(at_close, int):
        return max(0, at_close - at_start)
    if isinstance(at_start, int):
        return max(0, total_msgs_fn() - at_start)
    return total_msgs_fn()


def _session_capi_by_episode(
    metadata: dict[str, Any], session_id: str
) -> dict[str | None, dict[str, int]]:
    """Indexa `metadata["capi_events_sent"]` por episodio.

    Los event_ids son estables y llevan la atribución adentro:
      * LeadSubmitted → `lead_{session_id}_{episode_id}` (el episodio va en
        el id; también matchea los "Lead" legacy pre-fix 2026-07-01).
      * Purchase → `purchase_{order_id}` → se resuelve al episodio cuyo
        `order_id` coincide.

    Eventos no mapeables a un episodio caen bajo la key ``None`` — que es
    exactamente la key del pseudo-episodio de las sesiones legacy sin
    `episodes[]`, así sus counters no se pierden.

    Cada slot: `{"lead_sent": n, "purchase_sent": n, "failed": n}`. Los
    `skipped_*` NO cuentan (no fueron intentos de envío).
    """
    events = metadata.get("capi_events_sent")
    out: dict[str | None, dict[str, int]] = {}
    if not isinstance(events, list) or not events:
        return out

    order_to_ep: dict[str, str | None] = {}
    for ep in metadata.get("episodes") or []:
        if isinstance(ep, dict) and isinstance(ep.get("order_id"), str):
            order_to_ep[ep["order_id"]] = ep.get("episode_id")

    lead_prefix = f"lead_{session_id}_"
    for e in events:
        if not isinstance(e, dict):
            continue
        event_id = e.get("event_id") or ""
        name = e.get("event_name")
        status = e.get("status") or ""
        ep_id: str | None = None
        if event_id.startswith(lead_prefix):
            ep_id = event_id[len(lead_prefix):]
        elif event_id.startswith("purchase_"):
            ep_id = order_to_ep.get(event_id[len("purchase_"):])
        slot = out.setdefault(
            ep_id, {"lead_sent": 0, "purchase_sent": 0, "failed": 0}
        )
        if status == "sent":
            if name == "Purchase":
                slot["purchase_sent"] += 1
            elif name in ("LeadSubmitted", "Lead"):
                slot["lead_sent"] += 1
        elif status.startswith("failed"):
            slot["failed"] += 1
    return out


def _session_order_totals(metadata: dict[str, Any]) -> dict[str, int]:
    """Map `order_id → total_cop` recuperable del metadata de la sesión.

    Fuente de **backfill** para episodios cuyo `order_total_cop` aún no fue
    congelado (ventas registradas ANTES de que el write path persistiera el
    total en el episodio). Solo el `registered_order` (última venta exitosa)
    conserva su `total_cop` a nivel sesión; `registered_orders_history` no
    guarda totales. Los episodios nuevos traen su propio `order_total_cop` y
    no dependen de este map.
    """
    totals: dict[str, int] = {}
    reg = metadata.get("registered_order")
    if isinstance(reg, dict):
        oid = reg.get("order_id")
        total = reg.get("total_cop")
        if isinstance(oid, str) and isinstance(total, (int, float)) and not isinstance(
            total, bool
        ):
            totals[oid] = int(total)
    return totals


def _episode_revenue_cop(
    episode: dict[str, Any] | None, order_totals: dict[str, int]
) -> int | None:
    """Ingreso (COP major units) atribuido a un episodio, o None si no hay venta.

    Prioridad:
      1. `episode.order_total_cop` (frozen al cierre — fuente canónica).
      2. Backfill: `order_totals[episode.order_id]` (registered_order legacy).
      3. Legacy sin episodes[]: el total del único registered_order de la sesión.
    """
    if episode is not None:
        frozen = episode.get("order_total_cop")
        if isinstance(frozen, (int, float)) and not isinstance(frozen, bool):
            return int(frozen)
        oid = episode.get("order_id")
        if isinstance(oid, str) and oid in order_totals:
            return order_totals[oid]
        return None
    # Legacy (sin episodes[]): a lo sumo un registered_order por sesión.
    if order_totals:
        return sum(order_totals.values())
    return None


def _episode_duration_ms(episode: dict[str, Any] | None) -> int | None:
    """Duración del episodio (closed - started) en ms, o None si está activo /
    sin timestamps válidos."""
    if episode is None:
        return None
    started = episode.get("started_at_ms")
    closed = episode.get("closed_at_ms")
    if isinstance(started, int) and isinstance(closed, int) and closed >= started:
        return closed - started
    return None


def _episode_llm_usage(episode: dict[str, Any] | None) -> tuple[float, int] | None:
    """`(cost_usd, total_tokens)` del episodio, o None si no acumuló uso LLM."""
    if not isinstance(episode, dict):
        return None
    usage = episode.get("llm_usage")
    if not isinstance(usage, dict):
        return None
    cost = usage.get("cost_usd")
    tokens = usage.get("total_tokens")
    return (
        float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0,
        int(tokens) if isinstance(tokens, (int, float)) and not isinstance(tokens, bool) else 0,
    )


# =============================================================================
# Public API
# =============================================================================


def list_ads_campaigns(
    vault_dir: Path,
    *,
    sessions: list[tuple[Path, dict[str, Any]]] | None = None,
    since_ms: int | None = None,
    until_ms: int | None = None,
) -> list[AdsCampaignSummary]:
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

    for session_dir, metadata in (
        sessions
        if sessions is not None
        else scan_ad_sessions(vault_dir, since_ms=since_ms)
    ):
        origin = metadata.get("origin") or {}

        last_touch = metadata.get("last_touch")
        last_msg_ms = _last_msg_at_ms(session_dir, last_touch, origin)
        count_fn = _make_line_counter(session_dir)
        order_totals = _session_order_totals(metadata)
        capi_idx = _session_capi_by_episode(metadata, session_dir.name)

        for ep, state in _iter_episodes(
            metadata,
            session_dir=session_dir,
            origin=origin,
            last_touch=last_touch,
            total_msgs_fn=count_fn,
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

            # Filtro por fecha (ventana de la UI). `since_ms` = límite inferior
            # (preset o `from`); `until_ms` = límite superior EXCLUSIVO (rango
            # custom `to`). Un episodio sin `started_at_ms` no se puede ubicar en
            # una ventana acotada → se excluye si hay cualquier límite activo.
            if since_ms is not None or until_ms is not None:
                if ep_started_ms is None:
                    continue
                if since_ms is not None and ep_started_ms < since_ms:
                    continue
                if until_ms is not None and ep_started_ms >= until_ms:
                    continue

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
                    # Acumuladores de negocio (ver helpers _episode_*).
                    "revenue": 0,
                    "revenue_count": 0,
                    "llm_cost": 0.0,
                    "llm_tokens": 0,
                    "has_llm": False,
                    "dur_sum": 0,
                    "dur_count": 0,
                    "capi_leads": 0,
                    "capi_purchases": 0,
                    "capi_failed": 0,
                },
            )
            bucket["started"] += 1
            bucket["counts"][state] = bucket["counts"].get(state, 0) + 1

            # Ingreso atribuido (frozen en el episodio, backfill desde
            # registered_order). Solo episodios con venta aportan.
            rev = _episode_revenue_cop(ep, order_totals)
            if rev is not None:
                bucket["revenue"] += rev
                bucket["revenue_count"] += 1
            # Costo LLM agregado del episodio.
            usage = _episode_llm_usage(ep)
            if usage is not None:
                bucket["llm_cost"] += usage[0]
                bucket["llm_tokens"] += usage[1]
                bucket["has_llm"] = True
            # Duración (solo episodios cerrados con timestamps válidos).
            dur = _episode_duration_ms(ep)
            if dur is not None:
                bucket["dur_sum"] += dur
                bucket["dur_count"] += 1
            # Señal CAPI del episodio (key None = pseudo-episodio legacy).
            capi_slot = capi_idx.get(
                ep.get("episode_id") if ep is not None else None
            )
            if capi_slot:
                bucket["capi_leads"] += capi_slot["lead_sent"]
                bucket["capi_purchases"] += capi_slot["purchase_sent"]
                bucket["capi_failed"] += capi_slot["failed"]

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
        # Agregados de negocio — None honesto cuando no hubo data (distingue
        # "0 real" de "pendiente"), consistente con el resto de campos null.
        revenue = bucket["revenue"] if bucket["revenue_count"] > 0 else None
        avg_ticket = (
            round(bucket["revenue"] / bucket["revenue_count"])
            if bucket["revenue_count"] > 0
            else None
        )
        llm_cost_usd = round(bucket["llm_cost"], 6) if bucket["has_llm"] else None
        llm_tokens = bucket["llm_tokens"] if bucket["has_llm"] else None
        avg_episode_duration_ms = (
            round(bucket["dur_sum"] / bucket["dur_count"])
            if bucket["dur_count"] > 0
            else None
        )
        summaries.append(
            AdsCampaignSummary(
                id=camp_id,
                name=name,
                source_type=source_type,
                started=bucket["started"],
                first_seen_ms=bucket["first_seen_ms"],
                last_seen_ms=bucket["last_seen_ms"],
                conversations=bucket["counts"],
                revenue=revenue,
                avg_ticket=avg_ticket,
                llm_cost_usd=llm_cost_usd,
                llm_tokens=llm_tokens,
                avg_episode_duration_ms=avg_episode_duration_ms,
                revenue_count=bucket["revenue_count"],
                duration_count=bucket["dur_count"],
                capi_leads_sent=bucket["capi_leads"],
                capi_purchases_sent=bucket["capi_purchases"],
                capi_failed=bucket["capi_failed"],
            )
        )

    summaries.sort(
        key=lambda c: c.last_seen_ms if c.last_seen_ms is not None else -1,
        reverse=True,
    )
    return summaries


def list_attributed_conversations(
    vault_dir: Path,
    campaign_id: str,
    *,
    sessions: list[tuple[Path, dict[str, Any]]] | None = None,
    since_ms: int | None = None,
    until_ms: int | None = None,
    source_ids: frozenset[str] | None = None,
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

    for session_dir, metadata in (
        sessions
        if sessions is not None
        else scan_ad_sessions(vault_dir, since_ms=since_ms)
    ):
        origin = metadata.get("origin") or {}

        session_id = session_dir.name
        phone = session_id[len("wa_") :]
        last_touch = metadata.get("last_touch")
        last_msg_ms = _last_msg_at_ms(session_dir, last_touch, origin)
        count_fn = _make_line_counter(session_dir)
        order_totals = _session_order_totals(metadata)
        capi_idx = _session_capi_by_episode(metadata, session_id)

        for ep, state in _iter_episodes(
            metadata,
            session_dir=session_dir,
            origin=origin,
            last_touch=last_touch,
            total_msgs_fn=count_fn,
            last_msg_ms=last_msg_ms,
            now_ms=now_ms,
        ):
            # Filtrado por episodio (no por sesión) — re-atribución FU2.
            # Con `source_ids` (fila agrupada por campaña/adset = N ads), el
            # episodio matchea si su bucket cae en el set; sin él, igualdad
            # exacta con `campaign_id` (comportamiento pre-segmentación).
            ep_campaign_id = _episode_to_campaign_id(ep, origin)
            if source_ids is not None:
                if ep_campaign_id not in source_ids:
                    continue
            elif ep_campaign_id != campaign_id:
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

            # Filtro por fecha (ventana de la UI): `since_ms` (límite inferior,
            # inclusive) + `until_ms` (límite superior, EXCLUSIVO — rango custom).
            if since_ms is not None and started < since_ms:
                continue
            if until_ms is not None and started >= until_ms:
                continue

            _usage = ep.get("llm_usage") if isinstance(ep, dict) else None
            _capi_slot = capi_idx.get(ep_id) or {}
            if _capi_slot.get("purchase_sent"):
                _capi_event = "Purchase"  # terminal — pisa a LeadSubmitted
            elif _capi_slot.get("lead_sent"):
                _capi_event = "LeadSubmitted"
            else:
                _capi_event = None
            convs.append(
                AdsAttributedConversation(
                    id=conv_id,
                    phone_number=phone,
                    episode_id=ep_id,
                    started_at_ms=started,
                    last_msg_at_ms=ep_last_msg,
                    msgs_count=_episode_msgs_count(ep, count_fn),
                    ad_headline=ad_headline,
                    agent=metadata.get("active_route"),
                    state=state,
                    # Valor de la venta atribuida (frozen en el episodio,
                    # backfill desde registered_order) + duración del episodio.
                    value=_episode_revenue_cop(ep, order_totals),
                    duration_ms=_episode_duration_ms(ep),
                    llm_cost_usd=(
                        _usage.get("cost_usd") if isinstance(_usage, dict) else None
                    ),
                    llm_tokens=(
                        _usage.get("total_tokens")
                        if isinstance(_usage, dict)
                        else None
                    ),
                    capi_event=_capi_event,
                )
            )

    convs.sort(key=lambda c: c.started_at_ms, reverse=True)
    return convs


# =============================================================================
# Serie diaria
# =============================================================================

# Colombia opera en America/Bogota = UTC-5 fijo (sin DST desde 1993). Bucketeamos
# los días en hora local del operador — usar UTC correría la frontera de día 5h
# y partiría conversaciones nocturnas al día equivocado. Offset fijo en vez de
# tzdata: evita la dependencia y es exacto para CO.
_BOGOTA_OFFSET_MS = 5 * 60 * 60 * 1000

# Abreviaturas de mes en español — `datetime` no las da sin locale (y depender
# del locale del host es frágil). Espeja el formato del eje X del dashboard.
_MONTH_ABBR_ES = {
    1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
    7: "jul", 8: "ago", 9: "sep", 10: "oct", 11: "nov", 12: "dic",
}


def _bogota_date(ms: int) -> datetime.date:
    """Fecha calendario en America/Bogota (UTC-5 fijo) de un epoch ms."""
    dt = datetime.datetime.fromtimestamp(
        (ms - _BOGOTA_OFFSET_MS) / 1000, tz=datetime.timezone.utc
    )
    return dt.date()


def _day_label(d: datetime.date) -> str:
    """'29 abr' — etiqueta del eje X (día + mes abreviado español)."""
    return f"{d.day} {_MONTH_ABBR_ES[d.month]}"


def _bogota_day_start_ms(d: datetime.date) -> int:
    """Epoch ms de la medianoche (00:00 America/Bogota, UTC-5) de una fecha."""
    midnight_utc = datetime.datetime(
        d.year, d.month, d.day, tzinfo=datetime.timezone.utc
    )
    return int(midnight_utc.timestamp() * 1000) + _BOGOTA_OFFSET_MS


def bogota_day_start_ms(date_str: str) -> int | None:
    """'YYYY-MM-DD' → epoch ms de su medianoche (America/Bogota). None si no parsea.

    Inverso de `_bogota_date`. La capa API la usa para traducir el rango
    fecha-inicio/fecha-fin de la UI (`?from=&to=`) a los límites `since_ms`/
    `until_ms` del filtro — manteniendo TODA la lógica de timezone en este módulo
    (un solo lugar, sin que el frontend tenga que adivinar el huso del operador).
    """
    try:
        d = datetime.date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None
    return _bogota_day_start_ms(d)


def list_daily_series(
    vault_dir: Path,
    campaign_id: str,
    *,
    days: int = 14,
    now_ms: int | None = None,
    since_ms: int | None = None,
    until_ms: int | None = None,
    sessions: list[tuple[Path, dict[str, Any]]] | None = None,
    source_ids: frozenset[str] | None = None,
) -> list[AdsDailySeriesPoint]:
    """Serie diaria de una campaña: chats iniciados por día, por estado actual.

    Cada episodio atribuido a `campaign_id` (re-atribución FU2 — mismo criterio
    que `list_attributed_conversations`) se bucketea por el día calendario
    (America/Bogota) de su `started_at_ms` y suma 1 a su estado. Devuelve una
    serie CONTINUA — los días sin actividad vienen con counts en 0 (no se omiten)
    para que el gráfico no tenga huecos.

    Ventana:
      - Preset (default): los últimos `days` días terminando hoy.
      - Custom (`since_ms`/`until_ms`, del rango fecha-inicio/fecha-fin de la UI):
        de `since_ms` a `until_ms` (este último exclusivo). Clampeada a 90 columnas
        (un gráfico no puede tener infinitas barras) anclando el corte al final.

    `now_ms` se inyecta en tests para fijar la ventana; en producción es el
    instante de la request.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    days = max(1, min(days, 90))  # clamp defensivo del preset

    # Ventana [start_day, end_day] (días calendario Bogota, ambos inclusive):
    #  - Custom (since_ms/until_ms del rango fecha-inicio/fecha-fin de la UI):
    #    end_day = día de `until_ms` (exclusivo → −1ms cae en el último día),
    #    start_day = día de `since_ms`.
    #  - Preset (sin since/until): los últimos `days` días terminando hoy.
    if until_ms is not None:
        end_day = _bogota_date(until_ms - 1)
    else:
        end_day = _bogota_date(now_ms)
    if since_ms is not None:
        start_day = min(_bogota_date(since_ms), end_day)
        span = (end_day - start_day).days + 1
        if span > 90:  # un gráfico no puede tener infinitas columnas
            start_day = end_day - datetime.timedelta(days=89)
            span = 90
    else:
        span = days
        start_day = end_day - datetime.timedelta(days=span - 1)

    window = [start_day + datetime.timedelta(days=i) for i in range(span)]
    by_day: dict[datetime.date, dict[str, int]] = {
        d: _empty_state_counts() for d in window
    }
    # `since` para el scan directo (sin `sessions=`): inicio del primer día de la
    # ventana — superset-safe (crear/avanzar un episodio reescribe metadata, y el
    # bucketeo por `by_day` descarta con precisión lo que caiga fuera). Para el
    # preset usamos la ventana relativa, preservando el comportamiento previo.
    scan_since = (
        _bogota_day_start_ms(start_day)
        if (since_ms is not None or until_ms is not None)
        else now_ms - span * 24 * 60 * 60 * 1000
    )

    for session_dir, metadata in (
        sessions if sessions is not None else scan_ad_sessions(vault_dir, since_ms=scan_since)
    ):
        origin = metadata.get("origin") or {}
        last_touch = metadata.get("last_touch")
        last_msg_ms = _last_msg_at_ms(session_dir, last_touch, origin)
        count_fn = _make_line_counter(session_dir)

        for ep, state in _iter_episodes(
            metadata,
            session_dir=session_dir,
            origin=origin,
            last_touch=last_touch,
            total_msgs_fn=count_fn,
            last_msg_ms=last_msg_ms,
            now_ms=now_ms,
        ):
            ep_bucket = _episode_to_campaign_id(ep, origin)
            if source_ids is not None:
                if ep_bucket not in source_ids:
                    continue
            elif ep_bucket != campaign_id:
                continue
            ep_started_ms = (
                ep.get("started_at_ms") if ep is not None else origin.get("first_seen_ms")
            )
            if not isinstance(ep_started_ms, int):
                continue
            day = _bogota_date(ep_started_ms)
            counts = by_day.get(day)
            if counts is not None:  # episodio fuera de la ventana → se ignora
                counts[state] = counts.get(state, 0) + 1

    return [
        AdsDailySeriesPoint(
            d=_day_label(d),
            ganado=by_day[d].get("ganado", 0),
            cotizado=by_day[d].get("cotizado", 0),
            calificado=by_day[d].get("calificado", 0),
            activo=by_day[d].get("activo", 0),
            nuevo=by_day[d].get("nuevo", 0),
            no_reply=by_day[d].get("no_reply", 0),
            perdido=by_day[d].get("perdido", 0),
        )
        for d in window
    ]
