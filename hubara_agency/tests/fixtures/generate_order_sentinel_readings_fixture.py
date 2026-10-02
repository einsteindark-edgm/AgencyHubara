"""Foto del lector de Jev del Order Sentinel ANTES de su paquete (PAQUETES_DE_DECISION.md F8).

Lo que el lector le pregunta a Jev («¿qué cambió?» y la evidencia) y la
lectura que arma con sus respuestas, en una grilla de conversaciones,
cambios, cortes de lo ya analizado y respuestas en los bordes. Pasar el
lector a su paquete no puede cambiar ni un carácter de esto:
`tests/plugins/order_sentinel/test_sentinel_bundle.py` lo compara.

Uso (solo para regenerar la foto, a propósito):

    cd hubara_agency && uv run python tests/fixtures/generate_order_sentinel_readings_fixture.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.plugins.order_sentinel.agent.cycle.use_cases import readings  # noqa: E402
from src.sdk.connectorkit import PerceptionResult, TypedAnswer  # noqa: E402

OUT = Path(__file__).with_name("order_sentinel") / "readings_frozen.json"
SID = "wa_573001234567"
CHANGES = ("nada", "preparacion", "listo", "en_camino", "entregado", "pago", "otra")
SINCE = (None, 1_003, 10**13)
#: La segunda respuesta (la evidencia): ninguna, caída, todas seguras, en el
#: borde (0,84 / 0,85), una sola, sin probabilidad o sin respuestas.
PROFILES = ("none", "caido", "todas", "borde", "una", "sin_p", "faltan")


def _convo(messages: list[tuple[str, str, bool]], *, stage: Any = "ready", paid: bool = True) -> dict[str, Any]:
    return {
        "session_id": SID,
        "order_id": "order_01SALIO",
        "current_stage": stage,
        "payment_confirmed": paid,
        "messages": [
            {"who": who, "text": text, "at_ms": 1_000 + i, "has_media": media}
            for i, (who, text, media) in enumerate(messages)
        ],
    }


CONVOS: dict[str, dict[str, Any]] = {
    "salio": _convo([
        ("customer", "hola, ¿mi pedido ya salió?", False),
        ("bot", "Ya le aviso al equipo 🙌", False),
        ("human_operator", "Hola! ya salió con el mensajero,   llega hoy", False),
        ("human_operator", "", True),
        ("customer", "gracias!!", False),
    ]),
    "pago": _convo([
        ("customer", "ya pagué por nequi", True),
        ("human_operator", "Recibimos tu pago, ¡gracias!", False),
        ("customer", "listo {gracias}", False),
    ], stage="new", paid=False),
    "largo": _convo([
        ("human_operator", "Tu pedido " + "muy " * 130 + "ya está listo", False),
        ("otro", "¿quién es?", False),
        ("customer", "   ", True),
    ], stage="preparing"),
    "muchos": _convo([
        ("customer" if i % 2 else "human_operator", f"mensaje {i} sobre el pedido", False) for i in range(20)
    ], stage="shipping"),
    "vacio": _convo([], stage=None, paid=False),
    "desconocido": _convo([("customer", "¿ya me llegó?", False)], stage="otra_etapa"),
}


def _questions(questions: list[Any]) -> list[dict[str, Any]]:
    return [{"id": q.id, "kind": q.kind, "text": q.text, "criteria": dict(q.criteria)} for q in questions]


def _first(choice: str | None, conf: float | None, *, ok: bool = True) -> PerceptionResult:
    if not ok:
        return PerceptionResult(ok=False, error="timeout", model="typesafe/jev-1.13")
    probs = ((choice, conf),) if choice is not None and conf is not None else ()
    answer = TypedAnswer(id=readings.CHANGE_QID, kind="choice", choice=choice, probs=probs, confidence=conf)
    return PerceptionResult(ok=True, model="typesafe/jev-1.13", answers=(answer,))


def _second(qids: list[str], profile: str) -> PerceptionResult | None:
    if profile == "none":
        return None
    if profile == "caido":
        return PerceptionResult(ok=False, error="provider_error", model="typesafe/jev-1.13")
    ps = {
        "todas": [0.9] * len(qids),
        "borde": [0.85 if i % 2 else 0.84 for i in range(len(qids))],
        "una": [0.86] + [0.1] * (len(qids) - 1),
        "sin_p": [None] * len(qids),
        "faltan": [],
    }[profile]
    answers = tuple(TypedAnswer(id=qid, kind="noul", p=p) for qid, p in zip(qids, ps))
    return PerceptionResult(ok=True, model="typesafe/jev-1.13", answers=answers)


def _firsts() -> dict[str, PerceptionResult]:
    out = {"caido": _first(None, None, ok=False), "sin_choice": _first(None, None), "sin_conf": _first("listo", None)}
    for change in CHANGES:
        for conf in (0.84, 0.85, 0.99):
            out[f"{change}@{conf}"] = _first(change, conf)
    return out


def build() -> dict[str, Any]:
    requests = {}
    for name, convo in CONVOS.items():
        state, questions = readings.change_request(convo)
        requests[name] = {"state": state, "questions": _questions(questions)}
    evidence: dict[str, Any] = {}
    for name, convo in CONVOS.items():
        for change in CHANGES:
            for since in SINCE:
                request = readings.evidence_request(convo, change, since_ms=since)
                if request is not None:  # el `state` es el mismo de «¿qué cambió?»
                    assert request[0] == requests[name]["state"]
                evidence[f"{name}|{change}|{since}"] = None if request is None else _questions(request[1])
    needs = {key: readings.needs_evidence(first) for key, first in _firsts().items()}
    reading: dict[str, Any] = {}
    for name, convo in CONVOS.items():
        for key, first in _firsts().items():
            change = readings.needs_evidence(first)
            # Sin cambio que probar, la segunda respuesta no se lee; la grilla
            # de la evidencia va con el cambio en el borde (0,85).
            if not change:
                combos = [(None, "none")]
            elif key.endswith("@0.85"):
                combos = [(since, profile) for since in SINCE for profile in PROFILES]
            else:
                combos = [(None, "todas")]
            for since, profile in combos:
                request = readings.evidence_request(convo, change, since_ms=since) if change else None
                qids = [q.id for q in request[1]] if request else []
                reading[f"{name}|{key}|{since}|{profile}"] = readings.reading_from(
                    convo, first, _second(qids, profile), since_ms=since
                )
    return {"requests": requests, "evidence": evidence, "needs": needs, "readings": reading}


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frozen = build()
    OUT.write_text(json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(OUT, {k: len(v) for k, v in frozen.items()})
