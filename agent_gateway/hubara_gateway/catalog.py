"""Catálogo de tools del gateway: cada tool es un `GET /api/ads/*` del backend.

Espejo de `RESOURCES` en `src/shared/resources.ts` del plugin `@acktos/dsh-hubara`
(la fuente de verdad del contrato). Todos los argumentos son strings.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import quote

from hubara_gateway.window import WindowError, parse_window, window_query

DEFAULT_RUNS_LIMIT = "20"
# Ids de Meta (numéricos), "direct", "run-<hex>"… Nunca "/", espacios ni solo puntos: un id
# ".." haría que el cliente HTTP normalice la ruta hacia OTRO endpoint de /api/ads/.
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_ID_PARAMS = frozenset({"campaign_id", "adset_id", "ad_id", "run_id"})


class ArgumentError(ValueError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    path: str  # plantilla con {param} para los argumentos de ruta
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    windowed: bool = False
    daily: bool = False
    # El body del backend es un array: va envuelto como {"items": [...]}.
    wrap_items: bool = False
    # Trae datos personales de clientes: se enmascaran salvo permiso explícito (scope ads.pii).
    has_pii: bool = False
    query_defaults: dict[str, str] = field(default_factory=dict)

    @property
    def arguments(self) -> tuple[str, ...]:
        return self.required + self.optional + (("window",) if self.windowed else ())


_WINDOW_DOC = " Ventana `window`: 7d | 30d | 90d | total | YYYY-MM-DD..YYYY-MM-DD (America/Bogota), por defecto 30d."

_SPECS = (
    ToolSpec(
        "ads_get_campaigns",
        "Campañas de Meta Ads",
        "Campañas con métricas de Meta y embudo de chats atribuidos (GET /api/ads/campaigns)." + _WINDOW_DOC,
        "/api/ads/campaigns",
        windowed=True,
    ),
    ToolSpec(
        "ads_get_adsets",
        "Conjuntos de anuncios",
        "Ad sets de una campaña (GET /api/ads/campaigns/{campaign_id}/adsets)." + _WINDOW_DOC,
        "/api/ads/campaigns/{campaign_id}/adsets",
        required=("campaign_id",),
        windowed=True,
    ),
    ToolSpec(
        "ads_get_ads",
        "Anuncios de un ad set",
        "Anuncios de un ad set (GET /api/ads/campaigns/{campaign_id}/adsets/{adset_id}/ads)." + _WINDOW_DOC,
        "/api/ads/campaigns/{campaign_id}/adsets/{adset_id}/ads",
        required=("campaign_id", "adset_id"),
        windowed=True,
    ),
    ToolSpec(
        "ads_get_conversations",
        "Conversaciones atribuidas",
        "Chats atribuidos a una campaña, opcionalmente a un ad set "
        "(GET /api/ads/campaigns/{campaign_id}/conversations). Teléfono y nombre llegan enmascarados." + _WINDOW_DOC,
        "/api/ads/campaigns/{campaign_id}/conversations",
        required=("campaign_id",),
        optional=("adset_id",),
        windowed=True,
        has_pii=True,
    ),
    ToolSpec(
        "ads_get_daily",
        "Serie diaria",
        "Chats iniciados por día y estado (GET /api/ads/campaigns/{campaign_id}/daily). "
        "Con window=total pide 90 días, como el dashboard." + _WINDOW_DOC,
        "/api/ads/campaigns/{campaign_id}/daily",
        required=("campaign_id",),
        optional=("adset_id",),
        windowed=True,
        daily=True,
    ),
    ToolSpec(
        "ads_get_creative",
        "Creativo de un anuncio",
        "Texto, imagen y preview de un anuncio (GET /api/ads/ads/{ad_id}/creative). 404 si no está disponible.",
        "/api/ads/ads/{ad_id}/creative",
        required=("ad_id",),
    ),
    ToolSpec(
        "ads_get_agent_referrals",
        "Derivaciones entre agentes",
        "Chats que llegaron por derivación de otro agente (GET /api/ads/agent-referrals)." + _WINDOW_DOC,
        "/api/ads/agent-referrals",
        windowed=True,
    ),
    ToolSpec(
        "ads_get_meta_status",
        "Estado de la conexión con Meta",
        "Si la cuenta de Meta Ads está conectada y con qué permisos (GET /api/ads/meta/status).",
        "/api/ads/meta/status",
    ),
    ToolSpec(
        "ads_get_analysis_agents",
        "Agentes de análisis",
        'Agentes de análisis disponibles (GET /api/ads/analysis/agents), envueltos como {"items": [...]}.',
        "/api/ads/analysis/agents",
        wrap_items=True,
    ),
    ToolSpec(
        "ads_get_analysis_runs",
        "Corridas de análisis",
        "Corridas de análisis recientes, opcionalmente de una campaña (GET /api/ads/analysis/runs). "
        "`limit` por defecto 20 (el backend lo recorta a 1..100).",
        "/api/ads/analysis/runs",
        optional=("campaign_id", "limit"),
        query_defaults={"limit": DEFAULT_RUNS_LIMIT},
    ),
    ToolSpec(
        "ads_get_analysis_run",
        "Detalle de una corrida",
        "Una corrida de análisis con sus eventos (GET /api/ads/analysis/runs/{run_id}).",
        "/api/ads/analysis/runs/{run_id}",
        required=("run_id",),
    ),
)

TOOLS: dict[str, ToolSpec] = {spec.name: spec for spec in _SPECS}


def resolve_request(tool: str, args: dict[str, str]) -> tuple[str, dict[str, str]]:
    """`(path, query)` del GET al backend para `tool(args)`, o `ArgumentError`."""
    spec = TOOLS[tool]
    present = {k: v for k, v in args.items() if v not in (None, "")}
    for name in _ID_PARAMS & present.keys():
        value = str(present[name])
        if not _ID.fullmatch(value) or not value.strip("."):
            raise ArgumentError(f'invalid parameter "{name}": unsupported characters')

    path_params: dict[str, str] = {}
    for name in spec.required:
        if name not in present:
            raise ArgumentError(f'missing parameter "{name}"')
        path_params[name] = quote(str(present[name]), safe="")

    query: dict[str, str] = {}
    if spec.windowed:
        try:
            window = parse_window(present.get("window"))
        except WindowError as exc:
            raise ArgumentError(f"invalid window: {exc}") from exc
        query.update(window_query(window, daily=spec.daily))
    query.update(spec.query_defaults)
    for name in spec.optional:
        if name in present:
            query[name] = str(present[name])

    if "limit" in query and not query["limit"].lstrip("-").isdigit():
        raise ArgumentError(f'invalid parameter "limit": {query["limit"]!r} is not an integer')

    return spec.path.format(**path_params), query
