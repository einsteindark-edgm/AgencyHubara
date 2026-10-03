"""Claude Code como juez del scorecard (decisión del operador, 2026-09-28).

Gemini queda apagado. Los checks de juez se califican en dos pases:

1. El scorecard corre igual que siempre, con `ClaudeCodeJudge` en el lugar
   del modelo. Si el prompt de un check no tiene respuesta todavía, lo deja
   en la cola (`pending.jsonl`: el prompt EXACTO, con su criterio, su
   transcript y su formato de salida) y el check queda `desconocido` con la
   crítica `PENDING_CRITIQUE`: sin adivinar y sin contar como error del juez.
2. Claude Code lee la cola, califica con los mismos criterios y guarda su
   respuesta con el mismo JSON que devolvía el juez (`answers.jsonl`). El
   siguiente pase del scorecard (recalcular) encuentra la respuesta por la
   huella del prompt y la usa como veredicto.

La huella es el sha256 del prompt: si la conversación o el catálogo cambian,
el prompt cambia y el check vuelve a la cola (nunca se aplica una respuesta a
otra pregunta). Una respuesta se valida con los mismos parsers del juez antes
de entrar: la que no se puede leer, o no califica todas las candidatas de un
prompt en modo turno, se rechaza y el check sigue esperando.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

PENDING_CRITIQUE = "pendiente: lo califica Claude Code"
JUDGE_NAME = "claude-code"

_PENDING = "pending.jsonl"
_ANSWERS = "answers.jsonl"
_CHECK_RE = re.compile(r"CRITERIO (\S+) —")
_CANDIDATES_RE = re.compile(r"SOLO los turnos marcados ★ CANDIDATA: ([^\n]+?)\.\n")


class JudgePending(Exception):
    """El prompt quedó en la cola: todavía no hay calificación."""


def queue_dir(vault_dir: Path) -> Path:
    """La cola de producción vive junto a los scorecards (`_evals/`, fuera de `wa_*`)."""
    return Path(vault_dir) / "_evals" / "judge_queue"


def prompt_id(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:24]


def prompt_check(prompt: str) -> str | None:
    match = _CHECK_RE.search(prompt)
    return match.group(1) if match else None


def prompt_turns(prompt: str) -> list[int]:
    """Turnos candidatos de un prompt en modo turno (vacío en modo episodio)."""
    match = _CANDIDATES_RE.search(prompt)
    return [int(t) for t in re.findall(r"T(\d+)", match.group(1))] if match else []


def validate_answer(prompt: str, raw: str) -> str | None:
    """`None` si la respuesta se lee como un veredicto del criterio del prompt;
    si no, el motivo del rechazo."""
    from src.plugins.chats.agent.sales_eval.scorecard.judge_checks import (
        parse_focus_output,
        parse_judge_output,
    )

    check_id = prompt_check(prompt)
    if check_id is None:
        return "el prompt no nombra su criterio"
    turns = prompt_turns(prompt)
    if turns:
        by_turn = parse_focus_output(check_id, raw, turns)
        if by_turn is None:
            return "respuesta ilegible (se espera {\"turnos\": [...]})"
        missing = [k for k in turns if k not in by_turn]
        return f"faltan las candidatas {missing}" if missing else None
    return None if parse_judge_output(check_id, raw) is not None else "respuesta ilegible"


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            out.append(item)
    return out


def _append(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


class JudgeQueue:
    """La cola de un juez en disco: `pending.jsonl` + `answers.jsonl` (append-only)."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def _queued(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for item in _read(self.directory / _PENDING):
            out.setdefault(item["id"], item)
        return out

    def _answers(self) -> dict[str, str]:
        return {a["id"]: a["raw"] for a in _read(self.directory / _ANSWERS) if isinstance(a.get("raw"), str)}

    def pending(self) -> list[dict[str, Any]]:
        """Los prompts sin respuesta, en el orden en que llegaron."""
        answered = self._answers()
        return [item for pid, item in self._queued().items() if pid not in answered]

    def answer(self, pid: str) -> str | None:
        return self._answers().get(pid)

    def add_pending(self, prompt: str, *, unit: str = "") -> str:
        pid = prompt_id(prompt)
        if pid not in self._queued():
            _append(self.directory / _PENDING, [{
                "id": pid,
                "unit": unit,
                "check_id": prompt_check(prompt),
                "turns": prompt_turns(prompt),
                "queued_at_ms": int(time.time() * 1000),
                "prompt": prompt,
            }])
        return pid

    def add_answers(self, items: Iterable[Mapping[str, Any]], *, by: str = JUDGE_NAME) -> list[str]:
        """Guarda las respuestas válidas; devuelve un motivo por cada rechazada."""
        queued = self._queued()
        errors: list[str] = []
        valid: list[dict[str, Any]] = []
        for item in items:
            pid = str(item.get("id") or "")
            raw = item.get("raw")
            if pid not in queued:
                errors.append(f"{pid or '?'}: no está en la cola")
                continue
            if not isinstance(raw, str):
                errors.append(f"{pid}: falta `raw`")
                continue
            reason = validate_answer(queued[pid]["prompt"], raw)
            if reason:
                errors.append(f"{pid}: {reason}")
                continue
            valid.append({"id": pid, "raw": raw, "by": by, "answered_at_ms": int(time.time() * 1000)})
        if valid:
            _append(self.directory / _ANSWERS, valid)
        return errors


class ClaudeCodeJudge:
    """`JudgePort` que responde con lo que calificó Claude Code, o encola."""

    name = JUDGE_NAME

    def __init__(self, queue: JudgeQueue, *, unit: str = "") -> None:
        self.queue = queue
        self.unit = unit

    def for_unit(self, unit: str) -> ClaudeCodeJudge:
        """El mismo juez (la misma cola) rotulando lo que encola con `unit`."""
        return ClaudeCodeJudge(self.queue, unit=unit)

    async def a_generate(self, prompt: str) -> str:
        raw = self.queue.answer(prompt_id(prompt))
        if raw is not None:
            return raw
        raise JudgePending(self.queue.add_pending(prompt, unit=self.unit))
