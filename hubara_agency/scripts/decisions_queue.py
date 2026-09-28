"""Cola de desacuerdos regla ↔ Jev: ver lo pendiente, calificar y medir.

Mientras una capacidad del motor de decisiones corre en `sombra` (o en `jev`,
para seguir midiendo), cada vez que la regla de hoy y Jev no coinciden queda
un registro en `<vault>/_decisions/disagreements.jsonl` (el `state` ya
anonimizado). Claude Code los califica (decisión del operador, 2026-09-28):
la etiqueta es el valor correcto de la capacidad, con la MISMA forma que
`rule` y `jev`. La vara para que una capacidad actúe incluye que Jev gane en
los desacuerdos (`puntaje`).

USO (en un worker de producción, por SSM; la salida de SSM se corta en
~24.000 caracteres, por eso `siguiente` trae registros ENTEROS hasta un
presupuesto):

    python scripts/decisions_queue.py resumen
    python scripts/decisions_queue.py siguiente --max-chars 20000 [--capacidad compra]
    echo '{"id": "...", "label": ["affirmation", "text"], "note": "…"}' | python scripts/decisions_queue.py responder
    python scripts/decisions_queue.py puntaje

`--vault <dir>` cambia el vault (por defecto `WORKSPACE_VAULT_DIR`).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog  # noqa: E402


def summary(log: DisagreementLog) -> dict[str, Any]:
    pending = log.pending()
    return {
        "pendientes": len(pending),
        "por_capacidad": dict(sorted(Counter(str(i.get("capability")) for i in pending).items())),
    }


def _render(item: dict[str, Any]) -> str:
    head = f"=== {item['id']} · {item.get('capability')} · {item.get('session_id')} · {item.get('model')} ==="
    body = json.dumps(
        {"rule": item.get("rule"), "jev": item.get("jev"), "answers": item.get("answers")}, ensure_ascii=False
    )
    return f"{head}\n{item.get('state')}\n{body}\n"


def page(log: DisagreementLog, *, max_chars: int, capability: str | None = None) -> str:
    """Registros pendientes ENTEROS hasta `max_chars` (siempre al menos uno)."""
    out: list[str] = []
    used = 0
    for item in log.pending(capability):
        chunk = _render(item)
        if out and used + len(chunk) > max_chars:
            break
        out.append(chunk)
        used += len(chunk)
    return "\n".join(out) if out else "(sin pendientes)"


def respond(log: DisagreementLog, lines: list[str]) -> tuple[int, list[str]]:
    saved, errors = 0, []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            log.label(str(row["id"]), row["label"], note=str(row.get("note") or ""))
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"{line[:80]!r}: {exc!r}")
            continue
        saved += 1
    return saved, errors


def score(log: DisagreementLog) -> dict[str, dict[str, int]]:
    capabilities = sorted({str(i.get("capability")) for i in log.items()})
    return {cap: log.score(cap) for cap in capabilities}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default=None, help="vault (por defecto WORKSPACE_VAULT_DIR)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("resumen")
    nxt = sub.add_parser("siguiente")
    nxt.add_argument("--max-chars", type=int, default=20_000)
    nxt.add_argument("--capacidad", default=None)
    sub.add_parser("responder")
    sub.add_parser("puntaje")
    args = ap.parse_args(argv)
    if args.vault:
        vault = Path(args.vault)
    else:
        from src.sdk.runtime import WORKSPACE_VAULT_DIR

        vault = Path(WORKSPACE_VAULT_DIR)
    log = DisagreementLog(vault)
    if args.cmd == "resumen":
        print(json.dumps(summary(log), ensure_ascii=False, indent=2))
    elif args.cmd == "siguiente":
        print(page(log, max_chars=args.max_chars, capability=args.capacidad))
    elif args.cmd == "responder":
        saved, errors = respond(log, sys.stdin.read().splitlines())
        print(f"guardadas: {saved}")
        for e in errors:
            print(f"rechazada: {e}")
        return 1 if errors else 0
    elif args.cmd == "puntaje":
        print(json.dumps(score(log), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
