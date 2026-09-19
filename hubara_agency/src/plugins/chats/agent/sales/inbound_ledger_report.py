"""Reporte del ledger durable de inbound — reconciliar "Meta contó N
conversaciones, el vault M" sin depender de los logs del container.

Uso (en la caja de prod, dentro del container del API):

    python -m src.plugins.chats.agent.sales.inbound_ledger_report \
        --from 2026-09-16 --to 2026-09-18 [--full]

Salida JSON (`summarize_ledger`): por día de Bogotá y por anuncio, cuántas
personas escribieron (`sessions` — lo comparable con "conversaciones
iniciadas" de Meta), y cuántos mensajes se ingirieron / fallaron / se
perdieron adentro. Si Meta cuenta más que `sessions`, esos webhooks NUNCA
entraron; si `lost`/`failed` > 0, entraron y los perdimos nosotros.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.plugins.chats.agent.sales.composition import build_inbound_ledger
from src.plugins.chats.agent.sales.use_cases.inbound_ledger import summarize_ledger

_BOGOTA = ZoneInfo("America/Bogota")
_PHONE = re.compile(r"\d{8,}")


def _day_start_ms(day: date) -> int:
    return int(datetime.combine(day, time.min, _BOGOTA).timestamp() * 1000)


def _present(row: dict[str, Any], *, full: bool) -> dict[str, Any]:
    session_id = row.get("session_id")
    if not full and isinstance(session_id, str):
        session_id = _PHONE.sub(lambda m: f"***{m.group(0)[-4:]}", session_id)
    at_bogota = datetime.fromtimestamp(row["at_ms"] / 1000, _BOGOTA).strftime("%Y-%m-%d %H:%M:%S")
    return row | {"session_id": session_id, "at_bogota": at_bogota}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="frm", required=True, type=date.fromisoformat, help="día de Bogotá, inclusive")
    parser.add_argument("--to", dest="to", required=True, type=date.fromisoformat, help="día de Bogotá, inclusive")
    parser.add_argument("--full", action="store_true", help="session_id sin enmascarar (para ubicar el chat)")
    args = parser.parse_args(argv)

    lo, hi = sorted((args.frm, args.to))
    records = build_inbound_ledger().read(
        since_ms=_day_start_ms(lo), until_ms=_day_start_ms(hi + timedelta(days=1))
    )
    summary = summarize_ledger(records)
    for key in ("lost", "failed"):
        summary[key] = [_present(row, full=args.full) for row in summary[key]]
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
