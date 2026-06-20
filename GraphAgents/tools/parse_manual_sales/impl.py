"""Lógica PURA de `parse-manual-sales` — G-AGNOSTIC: solo stdlib. Normaliza filas de
ventas manuales de WhatsApp (las inyecta el operador / el central como JSON). Valida
fuerte (fecha ISO, campos presentes) y mapea el alias total_revenue -> total_revenue_cop.
Portada de ads-analytics-engine/src/ads_engine/ingest.py (sin la rama CSV: acá entra JSON).
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation


def _to_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError(f"esperaba un int, llegó bool {value!r}")
    if isinstance(value, int):
        return value
    try:
        # Tolera "450000", "450000.0", 450000.0 — COP es pesos enteros.
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"no es entero: {value!r}") from exc


def _to_sale(row: dict) -> dict:
    if "date" not in row:
        raise ValueError(f"fila de venta sin 'date': {row!r}")
    revenue = row.get("total_revenue_cop", row.get("total_revenue"))
    if revenue is None:
        raise ValueError(f"fila de venta sin 'total_revenue'/'total_revenue_cop': {row!r}")
    if "total_orders" not in row:
        raise ValueError(f"fila de venta sin 'total_orders': {row!r}")
    iso = str(row["date"]).strip()
    date.fromisoformat(iso)  # valida la fecha; levanta ValueError si no es ISO
    return {
        "date": iso,
        "total_orders": _to_int(row["total_orders"]),
        "total_revenue_cop": _to_int(revenue),
    }


def run(*, payload: object) -> dict:
    """payload = {sales:[...]} | {data:[...]} | [filas] | JSON string → ventas normalizadas."""
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    rows = payload if isinstance(payload, list) else payload.get("sales", payload.get("data", []))
    return {"sales": [_to_sale(r) for r in rows]}
