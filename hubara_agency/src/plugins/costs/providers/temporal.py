"""Temporal Cloud: the account's daily usage × Temporal's published price.

Source: ``GET saas-api.tmprl.cloud/cloud/usage`` (Cloud Ops API) with the
account API key. It answers, per UTC day and namespace, the actions and the
active / retained storage (byte-seconds). The billing report of the same API
only accepts whole months and takes ~40 s to generate, so the tab prices the
usage itself; with GB = 10^9 bytes and the plan fee (10 % of usage on the
Developer plan) this reproduces Temporal's invoice to the cent (checked against
a real monthly invoice).

Constraints of the API: midnight-UTC instants, at most 90 days back. Days are
UTC days, not Bogotá days (the card says so).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx

from src.plugins.costs.domain.money import to_micros
from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.domain.report import CostLine, ProviderReport, Source
from src.plugins.costs.providers.base import FetchContext, ProviderError, request_json, utc_day

USAGE_URL = "https://saas-api.tmprl.cloud/cloud/usage"
API_VERSION = "v0.7.0"
MAX_DAYS_BACK = 90
_BYTE_SECONDS_PER_GBH = Decimal(3600) * Decimal(10**9)
_MAX_PAGES = 20

_HOW_TO_CONNECT = "Falta TEMPORAL_API_KEY en el entorno del API (la misma llave de los workers)."

_METERS = (
    ("actions", "Acciones", "acciones"),
    ("active", "Almacenamiento activo", "GB-hora"),
    ("retained", "Almacenamiento retenido", "GB-hora"),
    ("plan_fee", "Tarifa del plan", None),
)


@dataclass(frozen=True)
class TemporalPrices:
    usd_per_million_actions: Decimal
    usd_per_active_gbh: Decimal
    usd_per_retained_gbh: Decimal
    #: Plan fee as a share of usage (Developer plan: 10 %).
    plan_fee_ratio: Decimal


def _iso(day: date) -> str:
    return f"{day.isoformat()}T00:00:00Z"


def _utc_window(rng: DateRange, now: datetime) -> tuple[date, date, bool]:
    """``[start, end)`` UTC days the API accepts, and whether start was trimmed."""
    today_utc = now.astimezone(timezone.utc).date()
    end = min(rng.end + timedelta(days=1), today_utc + timedelta(days=1))
    oldest = today_utc - timedelta(days=MAX_DAYS_BACK - 1)
    start = max(rng.start, oldest)
    return start, max(end, start), start != rng.start


class TemporalProvider:
    id = "temporal"
    label = "Temporal Cloud"

    def __init__(
        self, *, api_key: str | None, namespace: str | None, prices: TemporalPrices
    ) -> None:
        self._api_key = api_key
        self._namespace = namespace
        self._prices = prices

    @property
    def connected(self) -> bool:
        return bool(self._api_key)

    def _day_costs(self, records: dict[str, Decimal]) -> dict[str, tuple[Decimal, Decimal]]:
        """meter → (quantity, USD) for one namespace-day."""
        p = self._prices
        actions = records.get("ACTIONS", Decimal(0))
        active = records.get("ACTIVE_STORAGE", Decimal(0)) / _BYTE_SECONDS_PER_GBH
        retained = records.get("RETAINED_STORAGE", Decimal(0)) / _BYTE_SECONDS_PER_GBH
        out = {
            "actions": (actions, actions / Decimal(1_000_000) * p.usd_per_million_actions),
            "active": (active, active * p.usd_per_active_gbh),
            "retained": (retained, retained * p.usd_per_retained_gbh),
        }
        usage = sum(cost for _, cost in out.values())
        out["plan_fee"] = (Decimal(0), usage * p.plan_fee_ratio)
        return out

    def _lines(self, summaries: list[dict]) -> tuple[list[CostLine], bool]:
        usd: dict[tuple[str, str], dict[date, Decimal]] = {}
        qty: dict[tuple[str, str], Decimal] = {}
        incomplete = False
        for summary in summaries:
            incomplete = incomplete or bool(summary.get("incomplete"))
            day = utc_day(str(summary["startTime"]))
            for group in summary.get("recordGroups") or []:
                ns = next(
                    (g.get("value") for g in group.get("groupBys") or [] if g.get("value")),
                    "sin namespace",
                )
                records = {
                    str(r.get("type", "")).replace("RECORD_TYPE_", ""): Decimal(str(r.get("value") or 0))
                    for r in group.get("records") or []
                }
                for meter, (quantity, cost) in self._day_costs(records).items():
                    key = (ns, meter)
                    by_day = usd.setdefault(key, {})
                    by_day[day] = by_day.get(day, Decimal(0)) + cost
                    qty[key] = qty.get(key, Decimal(0)) + quantity
        lines: list[CostLine] = []
        for ns in sorted({ns for ns, _ in usd}):
            for meter, label, unit in _METERS:
                key = (ns, meter)
                if key not in usd:
                    continue
                lines.append(
                    CostLine(
                        key=f"{ns}:{meter}",
                        label=label,
                        currency="USD",
                        daily={d: to_micros(v) for d, v in usd[key].items()},
                        production=ns == self._namespace,
                        group=f"Namespace {ns}",
                        quantity=float(round(qty[key], 4)) if unit else None,
                        unit=unit,
                    )
                )
        return lines, incomplete

    async def fetch(
        self,
        client: httpx.AsyncClient,
        rng: DateRange,
        now: datetime,
        ctx: FetchContext | None = None,
    ) -> ProviderReport:
        if not self.connected:
            return ProviderReport.not_connected(self.id, rng, how_to_connect=_HOW_TO_CONNECT)
        start, end, trimmed = _utc_window(rng, now)
        summaries: list[dict] = []
        token = ""
        try:
            for _ in range(_MAX_PAGES):
                params = {
                    "start_time_inclusive": _iso(start),
                    "end_time_exclusive": _iso(end),
                    "page_size": 1000,
                }
                if token:
                    params["page_token"] = token
                payload = await request_json(
                    client,
                    "GET",
                    USAGE_URL,
                    provider_label="Temporal Cloud",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "temporal-cloud-api-version": API_VERSION,
                    },
                    params=params,
                )
                summaries.extend(payload.get("summaries") or [])
                token = str(payload.get("nextPageToken") or "")
                if not token:
                    break
        except ProviderError as exc:
            return ProviderReport.failed(self.id, rng, str(exc))
        lines, incomplete = self._lines(summaries)
        notes = [
            "Uso diario de Temporal Cloud (acciones y almacenamiento) por la tarifa publicada "
            "y la tarifa del plan; cuadra con la factura de Temporal.",
            "Temporal cuenta por día UTC (empieza a las 7 p. m. de Bogotá).",
        ]
        if incomplete:
            notes.append("El último día todavía se está contando.")
        warnings = []
        if trimmed:
            warnings.append(
                f"Temporal solo guarda {MAX_DAYS_BACK} días de uso: se muestra desde el "
                f"{start.isoformat()}."
            )
        return ProviderReport.ok(
            self.id, rng, Source.COMPUTED, lines, notes=notes, warnings=warnings
        )

