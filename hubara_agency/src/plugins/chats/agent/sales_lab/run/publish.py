"""Publicación de una corrida en `runs/<corrida>/` (plan §3.4, contrato lab@v1).

La API de producción SOLO lee `runs/`. Este módulo publica el CONTROL real
(A0, el bot de producción tal como respondió):

  runs/<corrida>/manifest.json          banco, brazos, versiones
  runs/<corrida>/cases.jsonl            un caso por turno real (armado de casos)
  runs/<corrida>/bench_report.json      conteos y exclusiones con motivo
  runs/<corrida>/conversations.json     índice: turnos, episodios y veredicto por brazo
  runs/<corrida>/threads/<sid>.json     el hilo real y, por turno, la salida de cada brazo
  runs/<corrida>/turns/A0/0/<sid>.jsonl las trazas reales de los turnos del banco
  runs/<corrida>/scores/A0/0/<sid>.jsonl los checks de producción (último por episodio)
  runs/<corrida>/summary.json           por brazo, la MISMA forma que /evals/checks/stats

Los brazos simulados (A1, B y C) se agregan con el simulador (PR 11 a 15).
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales_eval.scorecard import stats, store as card_store
from src.plugins.chats.agent.sales_eval.scorecard.registry import REGISTRY_VERSION
from src.plugins.chats.agent.sales_lab.arms import ARM_PROFILES
from src.plugins.chats.agent.sales_lab.cases import CaseSet
from src.plugins.chats.agent.sales_lab.run.arena import arena_metrics
from src.sdk.labkit import LabStorePort

CONTROL = "A0"
_MESSAGE_FIELDS = ("role", "content", "timestamp", "sender", "kind", "component_kind", "wamid")
_OUTPUT_FIELDS = ("sent_texts", "tools", "guards", "suppressed_reason", "llm_text", "discarded_narration")


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return ("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n").encode()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _message(event: dict[str, Any]) -> dict[str, Any]:
    msg = {k: event[k] for k in _MESSAGE_FIELDS if k in event}
    if event.get("image_url"):
        msg["has_image"] = True  # la foto vive en el media store de producción
    return msg


def _control_cards(bench_dir: Path, wanted: set[tuple[str, str]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for card in sorted((bench_dir / "scorecards").glob("*.jsonl")):
        records.extend(_read_jsonl(card))
    latest = card_store.latest_by_unit(records)
    return [r for r in latest if (str(r.get("session_id")), str(r.get("episode_id"))) in wanted]


def publish_control(
    bench_dir: Path,
    case_set: CaseSet,
    store: LabStorePort,
    *,
    run_id: str,
    order: dict[str, Any],
) -> dict[str, Any]:
    prefix = f"runs/{run_id}"
    arms = [CONTROL, *[a for a in order.get("arms") or [] if a != CONTROL]]
    by_session: dict[str, list] = defaultdict(list)
    for case in case_set.cases:
        by_session[case.session_id].append(case)

    store.put_bytes(f"{prefix}/cases.jsonl", _jsonl([c.to_dict() for c in case_set.cases]))
    store.put_bytes(
        f"{prefix}/bench_report.json",
        json.dumps(
            {
                "bench_id": order.get("bench_id"),
                "counts": {
                    "sessions": len(by_session),
                    "cases": len(case_set.cases),
                    "excluded_turns": len(case_set.exclusions),
                },
                "exclusions": [{"id": i, "reason": r} for i, r in case_set.exclusions],
            },
            ensure_ascii=False,
        ).encode(),
    )

    wanted: set[tuple[str, str]] = set()
    for sid, cases in sorted(by_session.items()):
        sdir = bench_dir / "vault" / sid
        try:
            metadata = json.loads((sdir / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            metadata = {}
        events = _read_jsonl(sdir / "sessions" / f"{sid}.jsonl")
        turns = [
            {
                "case_id": c.case_id,
                "turn_key": c.turn_key,
                "episode_id": c.episode_id,
                "turn": c.turn,
                "at_ms": c.at_ms,
                "burst": c.burst,
                "dashboard_prefix": c.dashboard_prefix,
                "outputs": {CONTROL: {k: c.real.get(k) for k in _OUTPUT_FIELDS if k in c.real}},
            }
            for c in cases
        ]
        thread = {
            "session_id": sid,
            "episodes": metadata.get("episodes") or [],
            "messages": [_message(e) for e in events],
            "turns": turns,
        }
        store.put_bytes(f"{prefix}/threads/{sid}.json", json.dumps(thread, ensure_ascii=False).encode())
        keys = {(c.episode_id, c.turn) for c in cases}
        traces = [
            t for t in _read_jsonl(sdir / "evals" / "turn_traces.jsonl")
            if (str(t.get("episode_id") or ""), int(t.get("turn") or 0)) in keys
        ]
        traces.sort(key=lambda t: (int(t.get("turn_started_ms") or 0), int(t.get("turn") or 0)))
        store.put_bytes(f"{prefix}/turns/{CONTROL}/0/{sid}.jsonl", _jsonl(traces))
        wanted.update((sid, c.episode_id) for c in cases)

    cards = _control_cards(bench_dir, wanted)
    by_sid: dict[str, list] = defaultdict(list)
    for card in cards:
        by_sid[str(card.get("session_id"))].append(card)
    for sid, rows in by_sid.items():
        store.put_bytes(f"{prefix}/scores/{CONTROL}/0/{sid}.jsonl", _jsonl(rows))
        # Copia intacta del scorecard de producción: la evaluación re-mide A0
        # en modo turno (y pisa `scores/A0/0/`); la validación §5.3 compara
        # contra esta copia.
        store.put_bytes(f"{prefix}/production/scores/{sid}.jsonl", _jsonl(rows))
    verdicts: dict[str, dict[str, str]] = defaultdict(dict)
    for card in cards:
        verdicts[str(card.get("session_id"))][str(card.get("episode_id"))] = str(card.get("verdict") or "SIN_DATOS")
    index = [
        {
            "session_id": sid,
            "turns": len(cases),
            "episodes": sorted({c.episode_id for c in cases}),
            "last_at_ms": max(c.at_ms for c in cases),
            "verdicts": {CONTROL: verdicts.get(sid, {})},
        }
        for sid, cases in sorted(by_session.items(), key=lambda kv: -max(c.at_ms for c in kv[1]))
    ]
    store.put_bytes(f"{prefix}/conversations.json", json.dumps(index, ensure_ascii=False).encode())
    rows = [card_store.to_row(r) for r in cards]
    dates = sorted(str(r.get("episode_date") or r.get("date") or "") for r in rows if r.get("episode_date") or r.get("date"))
    weeks = stats.weeks_between(dates[0], dates[-1]) if dates else []
    summary = {
        "run_id": run_id,
        "registry_version": REGISTRY_VERSION,
        "arms": {CONTROL: {"reps": 1, **stats.compute_stats(rows, weeks=weeks)}},
        "arms_pending": [a for a in arms if a != CONTROL],
    }
    store.put_bytes(f"{prefix}/summary.json", json.dumps(summary, ensure_ascii=False).encode())
    manifest = {
        "run_id": run_id,
        "bench_id": order.get("bench_id"),
        "arms": arms,
        "reps": order.get("reps"),
        "registry_version": REGISTRY_VERSION,
        "image": order.get("image"),
        "counts": {"sessions": len(by_session), "cases": len(case_set.cases)},
    }
    store.put_bytes(f"{prefix}/manifest.json", json.dumps(manifest, ensure_ascii=False).encode())
    return manifest


def _real_identity(trace: dict[str, Any], case: dict[str, Any], *, sim_session_id: str, source: str) -> dict[str, Any]:
    """La traza del turno simulado con la identidad del caso REAL: misma
    sesión, episodio, turno y `turn_key` (el hilo del laboratorio la busca
    así). El número ficticio del sandbox no sale de la caja."""
    real = str(case["session_id"])
    body = json.dumps(trace, ensure_ascii=False)
    if sim_session_id:
        body = body.replace(sim_session_id, real).replace(sim_session_id.removeprefix("wa_"), real.removeprefix("wa_"))
    out = json.loads(body)
    out.update(
        session_id=real,
        episode_id=case.get("episode_id"),
        turn=case.get("turn"),
        turn_key=case.get("turn_key"),
        case_id=case.get("case_id"),
        source=source,
    )
    return out


_COMPLEMENT_FIELDS = ("sent_texts", "llm_text", "tools", "guards", "suppressed_reason", "steps")


def publish_arm(
    store: LabStorePort,
    *,
    run_id: str,
    arm: str,
    rep: int,
    cases: list[dict[str, Any]],
    results: dict[int, dict[str, Any]],
) -> tuple[int, int]:
    """Sube lo que corrió un brazo simulado en una repetición:
    `turns/<brazo>/<rep>/<sid>.jsonl` y, en la repetición 0, la salida del
    brazo en cada turno del hilo (`outputs.<brazo>`). El complemento del bot
    nuevo (segundo turno de sistema) viaja dentro de su caso (`complement`,
    y `complement_texts` en el hilo). Las métricas del brazo van a
    `metrics/<brazo>/<rep>.json` (arena, PR 15). Devuelve (publicados, sin
    resultado)."""
    prefix = f"runs/{run_id}"
    source = f"lab:{run_id}:{arm}:{rep}"
    by_sid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    outputs: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    missing = 0
    for index, case in enumerate(cases):
        result = results.get(index) or {}
        trace = result.get("trace")
        if not isinstance(trace, dict):
            missing += 1
            continue
        sim = str(result.get("sim_session_id") or "")
        real = _real_identity(trace, case, sim_session_id=sim, source=source)
        output = {k: real[k] for k in _OUTPUT_FIELDS if k in real}
        complement = result.get("complement_trace")
        if isinstance(complement, dict):
            second = _real_identity(complement, case, sim_session_id=sim, source=source)
            real["complement"] = {k: second[k] for k in _COMPLEMENT_FIELDS if k in second}
            output["complement_texts"] = list(second.get("sent_texts") or [])
        by_sid[str(case["session_id"])].append(real)
        outputs[str(case["session_id"])][str(case["case_id"])] = output
    for sid, rows in by_sid.items():
        store.put_bytes(f"{prefix}/turns/{arm}/{rep}/{sid}.jsonl", _jsonl(rows))
    metrics = {
        "arm": arm,
        "rep": rep,
        "profile": ARM_PROFILES.get(arm),
        "missing": missing,
        **arena_metrics(results.values()),
    }
    store.put_bytes(f"{prefix}/metrics/{arm}/{rep}.json", json.dumps(metrics, ensure_ascii=False).encode())
    if rep == 0:
        for sid, by_case in outputs.items():
            raw = store.get_bytes(f"{prefix}/threads/{sid}.json")
            if raw is None:
                continue
            thread = json.loads(raw)
            for turn in thread.get("turns") or []:
                if turn.get("case_id") in by_case:
                    turn.setdefault("outputs", {})[arm] = by_case[turn["case_id"]]
            store.put_bytes(f"{prefix}/threads/{sid}.json", json.dumps(thread, ensure_ascii=False).encode())
    return sum(len(rows) for rows in by_sid.values()), missing
