"""Cola del juez Claude Code de una corrida del laboratorio: ver, responder y aplicar.

Claude Code es el juez (decisión del operador, 2026-09-28). La caja deja los
prompts de los checks de juez en `runs/<corrida>/judge/pending.jsonl` del S3
del laboratorio; Claude Code los califica con los mismos criterios y sus
respuestas van a `runs/<corrida>/judge/answers.jsonl` (solo las que se leen
como veredicto de su criterio y cubren todas las candidatas). `aplicar` prende
la caja y le ordena una corrida en modo solo evaluar, con la misma imagen de la
corrida: califica de nuevo sin simular y rearma el resumen.

Corre desde el equipo del operador con sus credenciales de AWS. La copia local
de la cola vive en un directorio temporal que se borra al terminar: el texto de
los clientes no queda en disco.

    cd hubara_agency && uv run python scripts/lab_judge.py --bucket <LAB_BUCKET> resumen <corrida>
    ... siguiente <corrida> --max-chars 40000 [--unidad A1/0/<sesión>/<episodio>]
    ... ver <corrida> <id> --desde 0 --largo 40000
    ... responder <corrida> < respuestas.jsonl      # {"id": "...", "raw": "{\"turnos\": [...]}"}
    ... aplicar <corrida>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import claude_judge  # noqa: E402
from src.plugins.chats.agent.sales_eval.scorecard.claude_judge import JudgeQueue  # noqa: E402

_FILES = ("pending.jsonl", "answers.jsonl")


def _key(run_id: str, name: str) -> str:
    return f"runs/{run_id}/judge/{name}"


@contextmanager
def _mirror(store: Any, run_id: str) -> Iterator[tuple[JudgeQueue, Path]]:
    """La cola de la corrida en un directorio temporal (se borra al salir)."""
    with tempfile.TemporaryDirectory(prefix="lab-judge-") as tmp:
        root = Path(tmp)
        for name in _FILES:
            raw = store.get_bytes(_key(run_id, name))
            if raw is not None:
                (root / name).write_bytes(raw)
        yield JudgeQueue(root), root


def run_summary(store: Any, run_id: str) -> dict[str, Any]:
    with _mirror(store, run_id) as (queue, _):
        return claude_judge.summary(queue)


def run_page(store: Any, run_id: str, *, max_chars: int, unit: str | None = None) -> str:
    with _mirror(store, run_id) as (queue, _):
        return claude_judge.page(queue, max_chars=max_chars, unit=unit)


def run_show(store: Any, run_id: str, pid: str, *, start: int = 0, length: int = 40_000) -> str:
    with _mirror(store, run_id) as (queue, _):
        return claude_judge.show(queue, pid, start=start, length=length)


def run_respond(store: Any, run_id: str, lines: list[str]) -> tuple[int, list[str]]:
    """Valida contra la cola y sube `answers.jsonl` entero (la caja nunca lo escribe)."""
    with _mirror(store, run_id) as (queue, root):
        saved, errors = claude_judge.respond(queue, lines)
        if saved:
            store.put_bytes(_key(run_id, "answers.jsonl"), (root / "answers.jsonl").read_bytes())
        return saved, errors


def run_apply(store: Any, run_id: str, launcher: Any) -> str:
    """Prende la caja y le ordena solo evaluar la corrida, con su misma imagen."""
    order = json.loads(store.get_bytes(f"orders/{run_id}.json") or b"{}")
    image = str(order.get("image") or "")
    if not image:
        raise SystemExit(f"la corrida {run_id} no tiene orden con imagen en S3")
    launcher.start_box()
    return launcher.evaluate(run_id, image)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bucket", default=os.getenv("LAB_BUCKET"), help="bucket del laboratorio (o LAB_BUCKET)")
    ap.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("resumen", "responder", "aplicar"):
        sub.add_parser(name).add_argument("corrida")
    nxt = sub.add_parser("siguiente")
    nxt.add_argument("corrida")
    nxt.add_argument("--max-chars", type=int, default=40_000)
    nxt.add_argument("--unidad", default=None)
    ver = sub.add_parser("ver")
    ver.add_argument("corrida")
    ver.add_argument("id")
    ver.add_argument("--desde", type=int, default=0)
    ver.add_argument("--largo", type=int, default=40_000)
    args = ap.parse_args(argv)
    if not args.bucket:
        ap.error("falta --bucket (o LAB_BUCKET)")

    from src.sdk.labkit import S3LabStore

    store = S3LabStore(args.bucket, region=args.region)
    if args.cmd == "resumen":
        print(json.dumps(run_summary(store, args.corrida), ensure_ascii=False, indent=2))
    elif args.cmd == "siguiente":
        print(run_page(store, args.corrida, max_chars=args.max_chars, unit=args.unidad))
    elif args.cmd == "ver":
        print(run_show(store, args.corrida, args.id, start=args.desde, length=args.largo))
    elif args.cmd == "responder":
        saved, errors = run_respond(store, args.corrida, sys.stdin.read().splitlines())
        print(f"guardadas: {saved}")
        for e in errors:
            print(f"rechazada: {e}")
        return 1 if errors else 0
    elif args.cmd == "aplicar":
        from src.sdk.labkit import get_lab_launcher

        print(run_apply(store, args.corrida, get_lab_launcher()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
