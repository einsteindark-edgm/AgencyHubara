"""Las decisiones de Jev de cada conversación (Calidad LLM, 2026-10-02).

Calidad LLM muestra, turno por turno, qué decidió cada capacidad y si lo
decidió Jev o la regla, igual que el laboratorio. En producción eso no
quedaba en ningún lado con su conversación: las métricas (`_decisions/
metrics`) no llevan sesión y la traza del turno solo trae la salida del bot
nuevo (`egress`). Cada decisión en la que Jev participa (proveedor `sombra` o
`jev`) queda en `<vault>/<sesión>/evals/decisions.jsonl`, junto a la traza
del turno, con:

  at_ms       cuándo se decidió (para ubicarla en su turno)
  stage       `ingest` (al llegar un mensaje), `turno` (tools y activities del
              turno), `remarketing` o `cierre` (fuera de un turno de ventas)
  message_id  el mensaje que la disparó (solo `ingest`, si se sabe)
  …           el veredicto compacto, con lo personal tapado (lo mismo que
              publica el laboratorio: `compact_verdict` + `plain_value`)

Con la regla de hoy (`reglas`) no se escribe nada: el bot actual no deja
rastro nuevo. Escribirla nunca frena la decisión (lo cuida `decide`).
"""
from __future__ import annotations

import dataclasses
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_ANSWER_FIELDS = ("q", "p", "choice", "confidence")


def decisions_path(vault_dir: Path, session_id: str) -> Path:
    return Path(vault_dir) / session_id / "evals" / "decisions.jsonl"


def plain_value(value: Any, redact: Sequence[str]) -> Any:
    """JSON sin texto personal: tuplas y conjuntos como listas, dataclasses
    como dicts y cada texto anonimizado (como la cola de desacuerdos)."""
    from src.sdk.connectorkit import anonymize_text

    if isinstance(value, str):
        return anonymize_text(value, redact=redact)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return {str(k): plain_value(v, redact) for k, v in value.items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return plain_value(dataclasses.asdict(value), redact)
    if isinstance(value, (set, frozenset)):
        return sorted((plain_value(v, redact) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [plain_value(v, redact) for v in value]
    return anonymize_text(str(value), redact=redact)


def _answer(answer: Any) -> dict[str, Any]:
    raw = dict(answer) if isinstance(answer, dict) else {}
    out: dict[str, Any] = {}
    for key in _ANSWER_FIELDS:
        value = raw.get(key)
        if value is None:
            continue
        out[key] = round(float(value), 3) if isinstance(value, float) else value
    return out


def _empty(value: Any) -> bool:
    """Nada que decir: None, texto vacío o una latencia de 0 (un `False` sí dice algo)."""
    if value is None or value == "":
        return True
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0


def compact_verdict(trace: dict[str, Any]) -> dict[str, Any]:
    """Un veredicto (`Verdict.to_trace()`) sin lo vacío: `rule` solo si no es
    el valor final; Jev, acuerdo, motivo, modelo, latencia y respuestas solo
    si los hay."""
    row: dict[str, Any] = {k: trace.get(k) for k in ("capability", "by", "provider", "value")}
    if trace.get("rule") != trace.get("value"):
        row["rule"] = trace.get("rule")
    for key in ("jev", "agree", "reason", "model", "latency_ms"):
        if not _empty(trace.get(key)):
            row[key] = trace[key]
    answers = [a for a in (_answer(a) for a in trace.get("answers") or ()) if a]
    if answers:
        row["answers"] = answers
    return row


def read_decisions(vault_dir: Path, session_id: str) -> list[dict[str, Any]]:
    """Las decisiones de la conversación en orden; una línea rota (un corte a
    mitad de escritura) no esconde las demás."""
    path = decisions_path(vault_dir, session_id)
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            row = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


class SessionDecisionLog:
    """Escribe las decisiones de Jev de una conversación (ver el módulo)."""

    def __init__(self, vault_dir: Path) -> None:
        self._vault = Path(vault_dir)

    def record(
        self,
        session_id: str,
        verdict: Any,
        *,
        stage: str = "turno",
        message_id: str | None = None,
        redact: Sequence[str] = (),
        at_ms: int | None = None,
    ) -> None:
        trace = verdict.to_trace() if hasattr(verdict, "to_trace") else dict(verdict)
        row: dict[str, Any] = {"at_ms": int(at_ms if at_ms is not None else time.time() * 1000), "stage": stage}
        if message_id:
            row["message_id"] = str(message_id)
        row.update(plain_value(compact_verdict(trace), redact))
        if trace.get("bundle"):
            row["bundle"] = str(trace["bundle"])
        path = decisions_path(self._vault, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
