"""Idempotent SQLite store keyed by date.

Stores RAW facts only (manual sales, Meta insights, and the joined raw totals).
Metrics are always recomputed by the deterministic core on the way out, so the
store can never drift from the math. Idempotent: re-ingesting a date overwrites.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from .merge import MergeResult, build_blended_day
from .models import ManualSale, MetaDailyInsight

DEFAULT_DB_PATH = "ads_engine.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_sales (
    date TEXT PRIMARY KEY,
    total_orders INTEGER NOT NULL,
    total_revenue_cop INTEGER NOT NULL,
    currency TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_insights (
    campaign_id TEXT NOT NULL DEFAULT '',
    campaign_name TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    spend_cop INTEGER NOT NULL,
    inline_link_clicks INTEGER NOT NULL,
    conversations INTEGER NOT NULL,
    currency TEXT NOT NULL,
    PRIMARY KEY (campaign_id, date)
);
CREATE TABLE IF NOT EXISTS blended_day (
    date TEXT PRIMARY KEY,
    spend_cop INTEGER NOT NULL,
    inline_link_clicks INTEGER NOT NULL,
    conversations INTEGER NOT NULL,
    total_orders INTEGER NOT NULL,
    total_revenue_cop INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS unmatched_date (
    date TEXT NOT NULL,
    side TEXT NOT NULL,
    PRIMARY KEY (date, side)
);
"""


class Store:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = str(path)
        with closing(self._conn()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def upsert_sales(self, sales: list[ManualSale]) -> None:
        with closing(self._conn()) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO manual_sales VALUES (?, ?, ?, ?)",
                [
                    (s.date.isoformat(), s.total_orders, s.total_revenue_cop, s.currency)
                    for s in sales
                ],
            )
            conn.commit()

    def upsert_insights(self, insights: list[MetaDailyInsight]) -> None:
        with closing(self._conn()) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO meta_insights VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        i.campaign_id or "",
                        i.campaign_name or "",
                        i.date.isoformat(),
                        i.spend_cop,
                        i.inline_link_clicks,
                        i.messaging_conversations_started,
                        i.currency,
                    )
                    for i in insights
                ],
            )
            conn.commit()

    def load_sales(self) -> list[ManualSale]:
        with closing(self._conn()) as conn:
            rows = conn.execute(
                "SELECT date, total_orders, total_revenue_cop, currency "
                "FROM manual_sales ORDER BY date"
            ).fetchall()
        return [
            ManualSale(
                date=date.fromisoformat(r[0]),
                total_orders=r[1],
                total_revenue_cop=r[2],
                currency=r[3],
            )
            for r in rows
        ]

    def load_insights(self) -> list[MetaDailyInsight]:
        with closing(self._conn()) as conn:
            rows = conn.execute(
                "SELECT campaign_id, campaign_name, date, spend_cop, inline_link_clicks, "
                "conversations, currency FROM meta_insights ORDER BY campaign_id, date"
            ).fetchall()
        return [
            MetaDailyInsight(
                date=date.fromisoformat(r[2]),
                spend_cop=r[3],
                inline_link_clicks=r[4],
                messaging_conversations_started=r[5],
                currency=r[6],
                campaign_id=r[0] or None,
                campaign_name=r[1] or None,
            )
            for r in rows
        ]

    def replace_blended(self, result: MergeResult) -> None:
        with closing(self._conn()) as conn:
            conn.execute("DELETE FROM blended_day")
            conn.execute("DELETE FROM unmatched_date")
            conn.executemany(
                "INSERT INTO blended_day VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        d.date.isoformat(),
                        d.spend_cop,
                        d.inline_link_clicks,
                        d.messaging_conversations_started,
                        d.total_orders,
                        d.total_revenue_cop,
                    )
                    for d in result.days
                ],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO unmatched_date VALUES (?, ?)",
                [(d.isoformat(), "meta") for d in result.meta_only_dates]
                + [(d.isoformat(), "sales") for d in result.sales_only_dates],
            )
            conn.commit()

    def load_blended(self, since: str | None = None, until: str | None = None) -> MergeResult:
        query = (
            "SELECT date, spend_cop, inline_link_clicks, conversations, "
            "total_orders, total_revenue_cop FROM blended_day"
        )
        clauses: list[str] = []
        params: list[str] = []
        if since:
            clauses.append("date >= ?")
            params.append(since)
        if until:
            clauses.append("date <= ?")
            params.append(until)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY date"

        with closing(self._conn()) as conn:
            rows = conn.execute(query, params).fetchall()
            unmatched = conn.execute(
                "SELECT date, side FROM unmatched_date ORDER BY date"
            ).fetchall()

        days = [
            build_blended_day(
                date.fromisoformat(r[0]),
                spend_cop=r[1],
                inline_link_clicks=r[2],
                conversations_started=r[3],
                total_orders=r[4],
                total_revenue_cop=r[5],
            )
            for r in rows
        ]
        meta_only = [date.fromisoformat(d) for d, side in unmatched if side == "meta"]
        sales_only = [date.fromisoformat(d) for d, side in unmatched if side == "sales"]
        return MergeResult(days=days, meta_only_dates=meta_only, sales_only_dates=sales_only)
