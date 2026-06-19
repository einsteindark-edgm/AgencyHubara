"""Load manually-recorded WhatsApp sales from JSON or CSV.

Accepts the spec's shape ``{"date","total_orders","total_revenue"}`` (revenue is
treated as COP) as well as an explicit ``total_revenue_cop`` key.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import ManualSale


def _to_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError(f"expected an int, got bool {value!r}")
    if isinstance(value, int):
        return value
    try:
        # Tolerate "450000", "450000.0", 450000.0 — COP is whole pesos.
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not an integer: {value!r}") from exc


def _to_sale(row: dict) -> ManualSale:
    if "date" not in row:
        raise ValueError(f"sales row missing 'date': {row!r}")
    revenue = row.get("total_revenue_cop", row.get("total_revenue"))
    if revenue is None:
        raise ValueError(f"sales row missing 'total_revenue'/'total_revenue_cop': {row!r}")
    if "total_orders" not in row:
        raise ValueError(f"sales row missing 'total_orders': {row!r}")
    return ManualSale(
        date=date.fromisoformat(str(row["date"]).strip()),
        total_orders=_to_int(row["total_orders"]),
        total_revenue_cop=_to_int(revenue),
        currency=str(row.get("currency", "COP")).strip(),
    )


def load_manual_sales(path: str | Path) -> list[ManualSale]:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".csv":
        rows: list[dict] = list(csv.DictReader(text.splitlines()))
    else:
        data = json.loads(text)
        rows = data if isinstance(data, list) else data.get("data", [])
    return [_to_sale(r) for r in rows]
