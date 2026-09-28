"""Cola del juez Claude Code: ver lo pendiente, responder y aplicar.

Claude Code es el juez del scorecard (decisión del operador, 2026-09-28;
`scorecard/claude_judge.py`). El scorecard deja en la cola el prompt EXACTO de
cada check de juez sin calificar; Claude Code lo lee, califica con los mismos
criterios y responde con el mismo JSON que devolvía el juez. `aplicar`
recalcula las unidades que ya no tienen nada pendiente (el mismo camino que
"Recalcular con juez" del dashboard), y el registro nuevo reemplaza al viejo.

USO (en el worker `sales_eval` de producción, por SSM; la salida de SSM se
corta en ~24.000 caracteres, por eso `siguiente` trae prompts ENTEROS hasta un
presupuesto y `ver` pagina uno grande):

    python scripts/claude_judge.py resumen
    python scripts/claude_judge.py siguiente --max-chars 20000 [--unidad <sesión>/<episodio>]
    python scripts/claude_judge.py ver <id> --desde 0 --largo 20000
    echo '{"id": "...", "raw": "{...}"}' | python scripts/claude_judge.py responder
    python scripts/claude_judge.py aplicar [--dry-run]

`--cola <dir>` cambia la cola (por defecto `<vault>/_evals/judge_queue`).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.plugins.chats.agent.sales_eval.scorecard.claude_judge import JudgeQueue, queue_dir  # noqa: E402


def _header(item: dict[str, Any]) -> str:
    turns = f" · candidatas {item['turns']}" if item.get("turns") else ""
    return f"=== {item['id']} · {item.get('check_id')} · {item.get('unit')}{turns} ==="


def summary(queue: JudgeQueue) -> dict[str, Any]:
    pending = queue.pending()
    answered = len(queue._answers())
    return {
        "pendientes": len(pending),
        "respondidas": answered,
        "por_check": dict(sorted(Counter(str(i.get("check_id")) for i in pending).items())),
        "unidades_pendientes": len({i.get("unit") for i in pending}),
        "listas_para_aplicar": len(ready_units(queue)),
    }


def page(queue: JudgeQueue, *, max_chars: int, unit: str | None = None) -> str:
    """Prompts pendientes ENTEROS, en orden de llegada, hasta `max_chars`."""
    items = [i for i in queue.pending() if unit is None or i.get("unit") == unit]
    if not items:
        return "(no hay nada pendiente)"
    out: list[str] = []
    used = 0
    for item in items:
        block = f"{_header(item)}\n{item['prompt']}\n"
        if used + len(block) > max_chars:
            if not out:
                return (
                    f"{_header(item)}\n(el prompt tiene {len(item['prompt'])} caracteres y no cabe en "
                    f"{max_chars}: léelo por partes con `ver {item['id']} --desde 0 --largo {max_chars}`)"
                )
            break
        out.append(block)
        used += len(block)
    rest = len(items) - len(out)
    if rest:
        out.append(f"(quedan {rest} pendientes)")
    return "\n".join(out)


def show(queue: JudgeQueue, pid: str, *, start: int = 0, length: int = 20_000) -> str:
    item = next((i for i in queue._queued().values() if i["id"] == pid), None)
    if item is None:
        return f"(no está en la cola: {pid})"
    return item["prompt"][start:start + length]


def respond(queue: JudgeQueue, lines: list[str]) -> tuple[int, list[str]]:
    """JSONL `{"id", "raw"}` → (respuestas guardadas, motivos de rechazo)."""
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            errors.append(f"línea {n}: no es JSON")
            continue
        if not isinstance(item, dict):
            errors.append(f"línea {n}: no es un objeto")
            continue
        items.append(item)
    rejected = queue.add_answers(items)
    return len(items) - len(rejected), errors + rejected


def ready_units(queue: JudgeQueue) -> list[str]:
    """Unidades con alguna respuesta y nada pendiente: listas para recalcular."""
    pending_units = {i.get("unit") for i in queue.pending()}
    answered = queue._answers()
    units = {i.get("unit") for pid, i in queue._queued().items() if pid in answered}
    return sorted(u for u in units if u and u not in pending_units)


async def _apply(units: list[str]) -> None:
    from scripts.rescore_scorecards import rescore_units

    pairs = [tuple(u.split("/", 1)) for u in units if "/" in u]
    await rescore_units(pairs, with_judge=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cola", type=Path, default=None, help="directorio de la cola (default: la del vault)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("resumen")
    nxt = sub.add_parser("siguiente")
    nxt.add_argument("--max-chars", type=int, default=20_000)
    nxt.add_argument("--unidad", default=None)
    ver = sub.add_parser("ver")
    ver.add_argument("id")
    ver.add_argument("--desde", type=int, default=0)
    ver.add_argument("--largo", type=int, default=20_000)
    sub.add_parser("responder")
    apl = sub.add_parser("aplicar")
    apl.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.cola is None:
        from src.plugins.chats.agent.sales_eval.evals import composition

        args.cola = queue_dir(composition.get_vault_dir())
    queue = JudgeQueue(args.cola)
    if args.cmd == "resumen":
        print(json.dumps(summary(queue), ensure_ascii=False, indent=2))
    elif args.cmd == "siguiente":
        print(page(queue, max_chars=args.max_chars, unit=args.unidad))
    elif args.cmd == "ver":
        print(show(queue, args.id, start=args.desde, length=args.largo))
    elif args.cmd == "responder":
        saved, errors = respond(queue, sys.stdin.read().splitlines())
        print(f"guardadas: {saved}")
        for e in errors:
            print(f"rechazada: {e}")
        return 1 if errors else 0
    elif args.cmd == "aplicar":
        units = ready_units(queue)
        print(f"{len(units)} unidades listas para recalcular")
        for u in units:
            print(f"  …{u.split('/', 1)[0][-4:]} {u.split('/', 1)[-1]}")
        if units and not args.dry_run:
            asyncio.run(_apply(units))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
