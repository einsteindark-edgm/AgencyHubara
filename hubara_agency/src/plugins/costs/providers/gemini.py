"""Gemini (and the rest of Google Cloud): the billing export in BigQuery.

Google exposes no API that returns money for the Gemini API: the Cloud Billing
API only has prices and budgets, Cloud Monitoring only tokens. The real cost is
in the *standard usage cost export* of the billing account to BigQuery
(``gcp_billing_export_v1_<account>``). This adapter:

1. signs a read-only BigQuery assertion with a service account
   (``GCP_BILLING_SERVICE_ACCOUNT_JSON``, the key file as JSON text),
2. finds the export table in the configured dataset,
3. sums ``cost`` + ``credits`` (credits are negative) per Bogotá day and
   service, in the billing account's currency.

Corrections arrive as new rows, so rows are always summed, never deduplicated.
"""
from __future__ import annotations

import time
from datetime import date, datetime

import httpx

from src.plugins.costs.domain.money import to_decimal, to_micros
from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.domain.report import CostLine, ProviderReport, Source
from src.plugins.costs.providers.base import ProviderError, request_json

BIGQUERY_URL = "https://bigquery.googleapis.com/bigquery/v2"
SCOPE = "https://www.googleapis.com/auth/bigquery.readonly"
EXPORT_TABLE_PREFIX = "gcp_billing_export_v1_"
_QUERY_TIMEOUT_MS = 20_000

_HOW_TO_CONNECT = (
    "Google solo entrega el costo real de Gemini por la exportación de facturación a "
    "BigQuery: en Cloud Console → Facturación → Exportación de la facturación activa "
    "«Costo de uso estándar» en un conjunto de datos multirregión US llamado "
    "billing_export; crea una cuenta de servicio con los roles BigQuery Data Viewer y "
    "BigQuery Job User, y guarda su llave JSON como GCP_BILLING_SERVICE_ACCOUNT_JSON."
)

_SQL = """
SELECT
  CAST(DATE(usage_start_time, "America/Bogota") AS STRING) AS day,
  service.description AS service,
  currency,
  CAST(SUM(cost) AS STRING) AS cost,
  CAST(SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) AS c), 0)) AS STRING) AS credits
FROM `{table}`
WHERE usage_start_time >= @start AND usage_start_time < @end
GROUP BY day, service, currency
"""


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_rows(rows: list[dict]) -> list[CostLine]:
    daily: dict[tuple[str, str], dict[date, int]] = {}
    for row in rows:
        day_s, service, currency, cost, credits = (cell.get("v") for cell in row["f"])
        net = to_decimal(cost or 0) + to_decimal(credits or 0)
        key = (str(service or "Sin servicio"), str(currency or "USD").upper())
        by_day = daily.setdefault(key, {})
        day = date.fromisoformat(str(day_s))
        by_day[day] = by_day.get(day, 0) + to_micros(net)
    lines = [
        CostLine(
            key=f"{service}:{currency}",
            label=service,
            currency=currency,
            daily=by_day,
            group="Servicio de Google Cloud",
        )
        for (service, currency), by_day in daily.items()
    ]
    return sorted(lines, key=lambda ln: -ln.amount_micros)


class GeminiProvider:
    id = "gemini"
    label = "Gemini (Google Cloud)"

    def __init__(
        self, *, service_account: dict | None, dataset: str, project: str | None
    ) -> None:
        self._sa = service_account
        self._dataset = dataset
        self._project = project or (service_account or {}).get("project_id")

    @property
    def connected(self) -> bool:
        return bool(self._sa and self._project)

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        import jwt  # lazy: only this adapter signs

        sa = self._sa or {}
        token_uri = sa.get("token_uri") or "https://oauth2.googleapis.com/token"
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": sa.get("client_email"),
                "scope": SCOPE,
                "aud": token_uri,
                "iat": now,
                "exp": now + 3600,
            },
            sa.get("private_key"),
            algorithm="RS256",
        )
        payload = await request_json(
            client,
            "POST",
            token_uri,
            provider_label="Google",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        token = payload.get("access_token")
        if not token:
            raise ProviderError("Google no entregó un token para la cuenta de servicio.")
        return str(token)

    async def _export_table(self, client: httpx.AsyncClient, auth: dict) -> str | None:
        payload = await request_json(
            client,
            "GET",
            f"{BIGQUERY_URL}/projects/{self._project}/datasets/{self._dataset}/tables",
            provider_label="BigQuery",
            headers=auth,
            params={"maxResults": 1000},
        )
        for table in payload.get("tables") or []:
            ref = table.get("tableReference") or {}
            if str(ref.get("tableId", "")).startswith(EXPORT_TABLE_PREFIX):
                return f"{ref.get('projectId')}.{ref.get('datasetId')}.{ref['tableId']}"
        return None

    async def fetch(
        self, client: httpx.AsyncClient, rng: DateRange, now: datetime
    ) -> ProviderReport:
        if not self.connected:
            return ProviderReport.not_connected(self.id, rng, how_to_connect=_HOW_TO_CONNECT)
        try:
            auth = {"Authorization": f"Bearer {await self._access_token(client)}"}
            table = await self._export_table(client, auth)
            if table is None:
                return ProviderReport.not_connected(
                    self.id,
                    rng,
                    how_to_connect=_HOW_TO_CONNECT,
                    notes=[
                        f"El conjunto {self._project}.{self._dataset} todavía no tiene la "
                        "tabla de la exportación (Google tarda unas horas en crearla)."
                    ],
                )
            result = await request_json(
                client,
                "POST",
                f"{BIGQUERY_URL}/projects/{self._project}/queries",
                provider_label="BigQuery",
                headers=auth,
                json={
                    "query": _SQL.format(table=table),
                    "useLegacySql": False,
                    "parameterMode": "NAMED",
                    "queryParameters": [
                        {"name": "start", "parameterType": {"type": "TIMESTAMP"},
                         "parameterValue": {"value": _ts(rng.utc_start)}},
                        {"name": "end", "parameterType": {"type": "TIMESTAMP"},
                         "parameterValue": {"value": _ts(rng.utc_end_exclusive)}},
                    ],
                    "timeoutMs": _QUERY_TIMEOUT_MS,
                    "maxResults": 10_000,
                },
            )
        except ProviderError as exc:
            return ProviderReport.failed(self.id, rng, str(exc))
        if not result.get("jobComplete"):
            return ProviderReport.failed(
                self.id, rng, "BigQuery tardó demasiado en responder; intenta de nuevo."
            )
        return ProviderReport.ok(
            self.id,
            rng,
            Source.BILLED,
            parse_rows(result.get("rows") or []),
            notes=[
                "Facturación de Google Cloud (costo menos créditos), por servicio y día de "
                "Bogotá. Google tarda hasta un día en reflejar el uso.",
            ],
        )
