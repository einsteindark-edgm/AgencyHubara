"""Banco de referencia del motor de decisiones: preparar, etiquetar con Claude Code y medir a Jev.

Decisión del operador (2026-09-28): Claude Code etiqueta ~150 turnos difíciles
de un banco del laboratorio respondiendo el MISMO cuestionario que responde
Jev, leyendo la conversación completa (`sales_lab/reference_bank.py`). Todo
vive en el S3 del laboratorio, junto al banco, nunca en el repo:

    bench/<banco>/reference/<perfil>/items.jsonl           turnos: state y preguntas del perfil
    bench/<banco>/reference/<perfil>/jev.jsonl             lo que respondió Jev
    bench/<banco>/reference/labels-<cuestionario>.jsonl   etiquetas de Claude Code

Las etiquetas son por cuestionario: dos perfiles con el mismo cuestionario las
comparten; con otro cuestionario (rafaga-v1 contra rafaga-v2), no. Una
etiqueta corregida (el mismo turno otra vez) reemplaza a la anterior.

Corre desde el equipo del operador con sus credenciales de AWS. El banco se
baja a un directorio temporal que se borra al terminar: el texto de los
clientes no queda en disco.

    cd hubara_agency && uv run python scripts/lab_bench_labels.py --bucket <LAB_BUCKET> preparar <banco> [--perfil jev-v2] [--tamano 150]
    ... resumen <banco> [--perfil jev-v2]
    ... siguiente <banco> [--perfil jev-v2] [--max-chars 30000]
    ... responder <banco> [--perfil jev-v2] < etiquetas.jsonl   # {"turn_key": "...", "answers": {...}, "note": "..."}
    ... preguntar <banco> --perfil jev-v2                       # necesita OPENROUTER_API_KEY
    ... metricas <banco> --perfil jev-v2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.plugins.chats.agent.sales.decisions.profiles import get_engine_profile, load_engine_profiles  # noqa: E402
from src.plugins.chats.agent.sales_lab.cases import build_cases  # noqa: E402
from src.plugins.chats.agent.sales_lab.reference_bank import (  # noqa: E402
    CATEGORY_QUOTAS,
    READY_ACCURACY,
    READY_MIN_ANSWERS,
    READY_MIN_POSITIVES,
    READY_PRECISION,
    REFERENCE_SIZE,
    TURN_MARK,
    ask_bank,
    build_item,
    score,
    select_reference_turns,
    validate_answers,
)

DEFAULT_PROFILE = "jev-v2"
DEFAULT_PAGE_CHARS = 30_000
#: El slug del historial del LLM en el banco (el mismo con que la caja arma los
#: casos, `run/activities.SALES_WORKSPACE`). El banco de referencia no lo lee.
SALES_WORKSPACE = "app-hubara-agency-src-plugins-chats-agent-sales-workspace"
_KIND_LABELS = {"noul": "sí/no", "choice": "una opción"}


def items_key(bench_id: str, profile_id: str) -> str:
    return f"bench/{bench_id}/reference/{profile_id}/items.jsonl"


def jev_key(bench_id: str, profile_id: str) -> str:
    return f"bench/{bench_id}/reference/{profile_id}/jev.jsonl"


def labels_key(bench_id: str, questionnaire: str) -> str:
    return f"bench/{bench_id}/reference/labels-{questionnaire}.jsonl"


def _rows(raw: bytes | None) -> list[dict[str, Any]]:
    """JSONL tolerante: una línea rota se salta."""
    out: list[dict[str, Any]] = []
    for line in (raw or b"").decode("utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _jsonl(rows: Iterable[dict[str, Any]]) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")


def _last_by_turn(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(r["turn_key"]): r for r in rows if r.get("turn_key")}


def _questionnaire(profile_id: str) -> str:
    profile = get_engine_profile(profile_id)
    if profile is None:
        raise SystemExit(f"perfil del motor desconocido: {profile_id}")
    return profile.questions


def _items(store: Any, bench_id: str, profile_id: str) -> list[dict[str, Any]]:
    items = _rows(store.get_bytes(items_key(bench_id, profile_id)))
    if not items:
        raise SystemExit(f"el banco {bench_id} no tiene banco de referencia con {profile_id}: corre `preparar` primero")
    return items


def _labels(store: Any, bench_id: str, questionnaire: str) -> dict[str, dict[str, Any]]:
    return _last_by_turn(_rows(store.get_bytes(labels_key(bench_id, questionnaire))))


# ── preparar ─────────────────────────────────────────────────────────────────


def download_bench(store: Any, bench_id: str, dest: Path) -> None:
    """Baja el banco como la caja (`run/activities._download_bench`: cada clave
    `bench/<banco>/…` a `<dest>/…`), pero solo lo que lee el banco de
    referencia: el manifiesto y el vault. El historial del LLM, el catálogo y
    los scorecards no hacen falta (menos texto de clientes en disco)."""
    prefix = f"bench/{bench_id}/"
    manifest = store.get_bytes(f"{prefix}manifest.json")
    if manifest is None:
        raise SystemExit(f"el banco {bench_id} no tiene manifiesto: está incompleto o no existe")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "manifest.json").write_bytes(manifest)
    for key in store.list_keys(f"{prefix}vault/"):
        data = store.get_bytes(key)
        if data is None:
            continue
        target = dest / key[len(prefix):]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _session_events(bench_dir: Path, session_id: str) -> list[dict[str, Any]]:
    """El historial del dashboard de una conversación del banco."""
    try:
        raw = (bench_dir / "vault" / session_id / "sessions" / f"{session_id}.jsonl").read_bytes()
    except OSError:
        return []
    return _rows(raw)


def run_prepare(store: Any, bench_id: str, *, profile_id: str = DEFAULT_PROFILE, size: int = REFERENCE_SIZE) -> dict[str, Any]:
    """Elige los turnos del banco y sube sus ítems con el perfil. Otra vez con
    el mismo banco y perfil da los mismos turnos (el sorteo es determinista):
    las etiquetas que ya había siguen valiendo."""
    questionnaire = _questionnaire(profile_id)
    with tempfile.TemporaryDirectory(prefix="lab-bench-ref-") as tmp:
        bench_dir = Path(tmp) / "bench"
        download_bench(store, bench_id, bench_dir)
        cases = build_cases(bench_dir, sales_workspace=SALES_WORKSPACE).cases
        events = {sid: _session_events(bench_dir, sid) for sid in sorted({c.session_id for c in cases})}
        by_id = {c.case_id: c for c in cases}
        chosen = select_reference_turns(cases, events_by_session=events, size=size)
        items = [build_item(by_id[cid], events[by_id[cid].session_id], profile_id=profile_id) for cid in chosen]
    store.put_bytes(items_key(bench_id, profile_id), _jsonl(items))
    labeled = _labels(store, bench_id, questionnaire)
    categories = Counter(cat for item in items for cat in item["categories"])
    return {
        "banco": bench_id,
        "perfil": profile_id,
        "cuestionario": questionnaire,
        "turnos_del_cliente": sum(1 for c in cases if c.trigger == "customer"),
        "items": len(items),
        "por_categoria": {name: categories[name] for name, _ in CATEGORY_QUOTAS if categories[name]},
        "ya_etiquetados": sum(1 for item in items if item["turn_key"] in labeled),
        "clave": items_key(bench_id, profile_id),
    }


# ── resumen ──────────────────────────────────────────────────────────────────


def run_summary(store: Any, bench_id: str, *, profile_id: str = DEFAULT_PROFILE) -> dict[str, Any]:
    questionnaire = _questionnaire(profile_id)
    items = _items(store, bench_id, profile_id)
    labeled = _labels(store, bench_id, questionnaire)
    keys = {item["turn_key"] for item in items}
    jev = [r for r in _rows(store.get_bytes(jev_key(bench_id, profile_id))) if r.get("turn_key") in keys]
    by_category = {}
    for name, _ in CATEGORY_QUOTAS:
        members = [item for item in items if name in item.get("categories", [])]
        by_category[name] = {"items": len(members), "etiquetados": sum(1 for i in members if i["turn_key"] in labeled)}
    done = sum(1 for key in keys if key in labeled)
    return {
        "banco": bench_id,
        "perfil": profile_id,
        "cuestionario": questionnaire,
        "items": len(items),
        "etiquetados": done,
        "pendientes": len(items) - done,
        "por_categoria": by_category,
        "jev": {
            "respondidos": sum(1 for r in jev if r.get("ok")),
            "fallidos": sum(1 for r in jev if not r.get("ok")),
            "modelos": sorted({str(r["model"]) for r in jev if r.get("ok") and r.get("model")}),
        },
    }


# ── siguiente ────────────────────────────────────────────────────────────────


def _intro(bench_id: str, profile_id: str, questionnaire: str) -> str:
    return "\n".join([
        f"BANCO DE REFERENCIA {bench_id} · perfil {profile_id} · cuestionario {questionnaire}",
        "Lee la conversación completa y responde el MISMO cuestionario que responde Jev sobre ESTE TURNO",
        "(lo que el cliente escribió en este turno; lo anterior es contexto). Responde lo que es verdad",
        "en la conversación, no lo que crees que diría Jev. Una línea JSON por turno (la plantilla de cada",
        "uno): los sí/no con true o false, las de una opción con el id de la opción; `note` es opcional.",
        "Las opciones que repiten las de la pregunta anterior no se vuelven a mostrar.",
        "Guarda las respuestas con (el mismo --bucket):",
        f"  uv run python scripts/lab_bench_labels.py responder {bench_id} --perfil {profile_id} < respuestas.jsonl",
        "",
    ])


def _question_lines(item: dict[str, Any]) -> list[str]:
    """Cada pregunta con sus opciones; las que repiten las de la pregunta
    anterior (los 17 asuntos sí/no) no se vuelven a mostrar."""
    lines: list[str] = []
    previous: Any = None
    for q in item.get("questions") or []:
        lines.append(f"- {q['id']} ({_KIND_LABELS.get(q['kind'], q['kind'])}): {q['text']}")
        criteria = q.get("criteria")
        if criteria != previous:
            if isinstance(criteria, dict):
                lines += [f"    {option}: {meaning}" for option, meaning in criteria.items()]
            elif isinstance(criteria, list):
                lines += [f"    {level}: {meaning}" for level, meaning in enumerate(criteria)]
        previous = criteria
    return lines


def _render(item: dict[str, Any], conversation: list[str], *, index: int, total: int) -> str:
    categories = ", ".join(item.get("categories") or []) or "sin categoría"
    template = {"turn_key": item["turn_key"], "answers": {q["id"]: None for q in item.get("questions") or []}, "note": ""}
    return "\n".join([
        f"=== {index}/{total} · {item['turn_key']} · {categories} ===",
        "CONVERSACIÓN (todo lo anterior a este turno y, al final, ESTE TURNO)",
        *conversation,
        "",
        "LO QUE VE JEV (state)",
        str(item.get("state") or ""),
        "",
        "PREGUNTAS",
        *_question_lines(item),
        "",
        "RESPUESTA (una línea JSON):",
        json.dumps(template, ensure_ascii=False),
        "",
    ])


def render_item(item: dict[str, Any], *, index: int, total: int, max_chars: int | None = None) -> str:
    """Un turno para etiquetar. Con `max_chars`, si no cabe, la conversación
    pierde su principio (lo más viejo); lo de este turno siempre va."""
    conversation = [str(line) for line in item.get("conversation") or []]
    block = _render(item, conversation, index=index, total=total)
    if max_chars is None:
        return block
    context_lines = conversation.index(TURN_MARK) if TURN_MARK in conversation else 0
    cut = 0
    while len(block) > max_chars and cut < context_lines:
        cut += 1
        kept = [f"(… {cut} anteriores omitidos por espacio)", *conversation[cut:]]
        block = _render(item, kept, index=index, total=total)
    return block


def run_page(store: Any, bench_id: str, *, profile_id: str = DEFAULT_PROFILE, max_chars: int = DEFAULT_PAGE_CHARS) -> str:
    """Los turnos sin etiquetar, ENTEROS y en el orden del banco, hasta
    `max_chars` (la salida de la terminal se corta). El primero siempre va:
    si no cabe, sin el principio de su conversación."""
    questionnaire = _questionnaire(profile_id)
    items = _items(store, bench_id, profile_id)
    labeled = _labels(store, bench_id, questionnaire)
    pending = [item for item in items if item["turn_key"] not in labeled]
    if not pending:
        return f"(no queda nada por etiquetar: los {len(items)} turnos tienen etiqueta de {questionnaire})"
    out = [_intro(bench_id, profile_id, questionnaire)]
    used = len(out[0])
    shown = 0
    for index, item in enumerate(pending, 1):
        block = render_item(item, index=index, total=len(pending))
        if used + len(block) > max_chars:
            if shown:
                break
            block = render_item(item, index=index, total=len(pending), max_chars=max_chars - used)
        out.append(block)
        used += len(block)
        shown += 1
    if len(pending) > shown:
        out.append(f"(quedan {len(pending) - shown} turnos sin etiquetar después de estos)")
    return "\n".join(out)


# ── responder ────────────────────────────────────────────────────────────────


def run_respond(
    store: Any, bench_id: str, lines: list[str], *, profile_id: str = DEFAULT_PROFILE
) -> tuple[int, list[str]]:
    """JSONL `{turn_key, answers, note}` → (etiquetas guardadas, motivos de
    rechazo). Solo se guardan las que responden todas las preguntas de su
    turno con la forma de cada una; se agregan al archivo del cuestionario."""
    questionnaire = _questionnaire(profile_id)
    items = {item["turn_key"]: item for item in _items(store, bench_id, profile_id)}
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except ValueError:
            errors.append(f"línea {n}: no es JSON")
            continue
        if not isinstance(data, dict):
            errors.append(f"línea {n}: no es un objeto")
            continue
        key = str(data.get("turn_key") or "")
        item = items.get(key)
        if item is None:
            errors.append(f"línea {n}: turno desconocido en este banco: {key!r}")
            continue
        problems = validate_answers(item, data.get("answers"))
        if problems:
            errors.append(f"línea {n} ({key}): " + "; ".join(problems))
            continue
        rows.append({
            "turn_key": key,
            "questionnaire": questionnaire,
            "answers": data["answers"],
            "note": str(data.get("note") or ""),
            "at_ms": int(time.time() * 1000),
        })
    if rows:
        key = labels_key(bench_id, questionnaire)
        existing = store.get_bytes(key) or b""
        if existing and not existing.endswith(b"\n"):
            existing += b"\n"
        store.put_bytes(key, existing + _jsonl(rows))
    return len(rows), errors


# ── preguntar y métricas ─────────────────────────────────────────────────────


def run_ask(store: Any, bench_id: str, *, profile_id: str = DEFAULT_PROFILE) -> dict[str, Any]:
    """Le pregunta a Jev todo el banco (otra vez, entero: una sola versión de
    Jev por medición) y guarda sus respuestas."""
    items = _items(store, bench_id, profile_id)
    rows = asyncio.run(ask_bank(items, profile_id=profile_id))
    store.put_bytes(jev_key(bench_id, profile_id), _jsonl(rows))
    ok = [r for r in rows if r.get("ok")]
    return {
        "banco": bench_id,
        "perfil": profile_id,
        "preguntados": len(rows),
        "respondidos": len(ok),
        "fallidos": len(rows) - len(ok),
        "errores": dict(Counter(str(r.get("error")) for r in rows if not r.get("ok"))),
        "modelos": sorted({str(r["model"]) for r in ok if r.get("model")}),
        "clave": jev_key(bench_id, profile_id),
    }


def run_metrics(store: Any, bench_id: str, *, profile_id: str = DEFAULT_PROFILE) -> dict[str, Any]:
    """Jev contra las etiquetas, pregunta por pregunta (`reference_bank.score`)."""
    questionnaire = _questionnaire(profile_id)
    items = _items(store, bench_id, profile_id)
    labels = _labels(store, bench_id, questionnaire)
    jev = _rows(store.get_bytes(jev_key(bench_id, profile_id)))
    answered = {str(r["turn_key"]) for r in jev if r.get("ok") and r.get("turn_key")}
    keys = [item["turn_key"] for item in items]
    questions = score(items, labels.values(), jev, profile_id=profile_id)
    return {
        "banco": bench_id,
        "perfil": profile_id,
        "cuestionario": questionnaire,
        "modelos": sorted({str(r["model"]) for r in jev if r.get("ok") and r.get("model")}),
        "turnos": {
            "items": len(items),
            "etiquetados": sum(1 for k in keys if k in labels),
            "con_jev": sum(1 for k in keys if k in answered),
            "medidos": sum(1 for k in keys if k in labels and k in answered),
        },
        "vara": {
            "precision": READY_PRECISION,
            "positivos": READY_MIN_POSITIVES,
            "acierto": READY_ACCURACY,
            "respuestas": READY_MIN_ANSWERS,
        },
        "pueden_actuar": [qid for qid, m in questions.items() if m["ready"]],
        "preguntas": questions,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bucket", default=os.getenv("LAB_BUCKET"), help="bucket del laboratorio (o LAB_BUCKET)")
    ap.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("preparar", "resumen", "siguiente", "responder", "preguntar", "metricas"):
        cmd = sub.add_parser(name)
        cmd.add_argument("banco")
        cmd.add_argument("--perfil", default=DEFAULT_PROFILE, help=f"perfil del motor (default {DEFAULT_PROFILE})")
        if name == "preparar":
            cmd.add_argument("--tamano", type=int, default=REFERENCE_SIZE)
        if name == "siguiente":
            cmd.add_argument("--max-chars", type=int, default=DEFAULT_PAGE_CHARS)
    args = ap.parse_args(argv)
    if not args.bucket:
        ap.error("falta --bucket (o LAB_BUCKET)")
    if get_engine_profile(args.perfil) is None:
        ap.error(f"perfil del motor desconocido: {args.perfil} (hay: {', '.join(sorted(load_engine_profiles()))})")
    if args.cmd == "preguntar" and not os.getenv("OPENROUTER_API_KEY") and not os.getenv("PERCEPTION_PROVIDER"):
        ap.error("preguntar necesita OPENROUTER_API_KEY (la llave de Jev en OpenRouter)")

    from src.sdk.labkit import S3LabStore

    store = S3LabStore(args.bucket, region=args.region)
    if args.cmd == "preparar":
        print(json.dumps(run_prepare(store, args.banco, profile_id=args.perfil, size=args.tamano), ensure_ascii=False, indent=2))
    elif args.cmd == "resumen":
        print(json.dumps(run_summary(store, args.banco, profile_id=args.perfil), ensure_ascii=False, indent=2))
    elif args.cmd == "siguiente":
        print(run_page(store, args.banco, profile_id=args.perfil, max_chars=args.max_chars))
    elif args.cmd == "responder":
        saved, errors = run_respond(store, args.banco, sys.stdin.read().splitlines(), profile_id=args.perfil)
        print(f"guardadas: {saved}")
        for error in errors:
            print(f"rechazada: {error}")
        return 1 if errors else 0
    elif args.cmd == "preguntar":
        print(json.dumps(run_ask(store, args.banco, profile_id=args.perfil), ensure_ascii=False, indent=2))
    elif args.cmd == "metricas":
        print(json.dumps(run_metrics(store, args.banco, profile_id=args.perfil), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
