"""Cuestionarios versionados como DATOS (`questionnaires/<id>.yaml`).

El motor le pregunta a Jev con un cuestionario: qué preguntas, con qué texto y
opciones, y cómo se arma el `state`. Vive como datos para que una pregunta
nueva sea un archivo nuevo (y un perfil que lo use), no un cambio en el
workflow ni en las tools. PURO: sin I/O salvo leer el YAML una vez.

Cada pregunta puede traer `when`: solo se hace si los hechos del turno
coinciden (`{hecho: valor}` o `{hecho: [valores]}`). Así el cuestionario crece
por etapa sin que cada llamada a Jev se vuelva gigante.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.sdk.connectorkit import TypedQuestion

QUESTIONNAIRES_DIR = Path(__file__).with_name("questionnaires")


def _offset(ms: Any, first: Any) -> str:
    if isinstance(ms, (int, float)) and isinstance(first, (int, float)):
        return f"+{round((ms - first) / 1000)} s"
    return "+? s"


def _matches(when: Mapping[str, Any] | None, facts: Mapping[str, Any]) -> bool:
    if not when:
        return True
    for key, expected in when.items():
        value = facts.get(key)
        if isinstance(expected, list):
            if value not in expected:
                return False
        elif value != expected:
            return False
    return True


def _criteria(raw: Any) -> Mapping[str, str] | tuple[str, ...]:
    if isinstance(raw, Mapping):
        return {str(k): str(v) for k, v in raw.items()}
    return tuple(str(v) for v in raw or ())


@dataclass(frozen=True)
class Questionnaire:
    """Un cuestionario cargado. `topics` en orden: el de las preguntas y el
    del plan del turno."""

    id: str
    topics: tuple[tuple[str, str, str], ...] = ()  # (id, etiqueta, pista)
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def topic_ids(self) -> tuple[str, ...]:
        return tuple(t[0] for t in self.topics)

    def label(self, topic: str) -> str:
        return next((label for tid, label, _ in self.topics if tid == topic), topic)

    # ── preguntas ────────────────────────────────────────────────────────────

    def burst_questions(
        self, messages: Sequence[dict[str, Any]], *, facts: Mapping[str, Any] | None = None
    ) -> list[TypedQuestion]:
        facts = facts or {}
        out: list[TypedQuestion] = []
        for spec in self.raw.get("questions") or []:
            if "each_topic" in spec:
                q = spec["each_topic"]
                if not _matches(q.get("when"), facts):
                    continue
                out += [
                    TypedQuestion(
                        id=str(q["id"]).format(topic=tid),
                        kind=q["kind"],
                        text=str(q["text"]).format(topic=tid, label=label, hint=hint),
                        criteria=_criteria(q.get("criteria")),
                    )
                    for tid, label, hint in self.topics
                ]
            elif "each_message" in spec:
                q = spec["each_message"]
                if not _matches(q.get("when"), facts):
                    continue
                crit = q.get("criteria") or {}
                options = {tid: label for tid, label, _ in self.topics} if crit.get("from_topics") else {}
                options |= {str(k): str(v) for k, v in (crit.get("extra") or {}).items()}
                out += [
                    TypedQuestion(
                        id=str(q["id"]).format(k=k), kind=q["kind"], text=str(q["text"]).format(k=k), criteria=options
                    )
                    for k in range(1, len(messages) + 1)
                ]
            elif _matches(spec.get("when"), facts):
                out.append(
                    TypedQuestion(
                        id=str(spec["id"]), kind=spec["kind"], text=str(spec["text"]), criteria=_criteria(spec.get("criteria"))
                    )
                )
        return out

    def verify_questions(self, plan: Any) -> list[TypedQuestion]:
        v = self.raw.get("verify") or {}
        return [
            TypedQuestion(
                id=str(v["id"]).format(topic=t.topic),
                kind=v["kind"],
                text=str(v["text"]).format(
                    label=self.label(t.topic),
                    where=str(v["where_msg"]).format(msg=t.msg) if t.msg else str(v["where_none"]),
                ),
                criteria=_criteria(v.get("criteria")),
            )
            for t in getattr(plan, "topics", ())
        ]

    # ── state ────────────────────────────────────────────────────────────────

    def burst_state(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        pending: Sequence[str] = (),
        last_bot_text: str | None = None,
    ) -> str:
        """El `state` de la ráfaga: los mensajes del cliente con su hora
        relativa, los asuntos pendientes y el último mensaje del asesor."""
        s = self.raw.get("state") or {}
        first = messages[0].get("ts_ms") if messages else None
        lines = [str(s["header"])]
        for k, m in enumerate(messages, 1):
            lines.append(
                str(s["message"]).format(k=k, offset=_offset(m.get("ts_ms"), first), text=str(m.get("text") or "").strip())
            )
        if pending:
            lines.append(str(s["pending"]).format(pending=", ".join(pending)))
        if last_bot_text:
            limit = int(s.get("last_bot_max_chars") or 400)
            text = last_bot_text.strip()
            text = text[:limit] if s.get("last_bot_keep", "start") == "start" else text[-limit:]
            lines.append(str(s["last_bot"]).format(text=text))
        return "\n".join(lines)

    def reply_state(self, messages: Sequence[dict[str, Any]], reply_text: str, components: Sequence[str]) -> str:
        """El `state` de la verificación: la ráfaga y lo que el asesor envía."""
        r = self.raw.get("reply_state") or {}
        lines = [self.burst_state(messages), str(r["header"]), reply_text.strip() or str(r["empty"])]
        if components:
            lines.append(str(r["components"]).format(components=", ".join(components)))
        return "\n".join(lines)


@lru_cache(maxsize=16)
def load_questionnaire(questionnaire_id: str) -> Questionnaire:
    path = QUESTIONNAIRES_DIR / f"{questionnaire_id}.yaml"
    if not path.is_file():
        raise KeyError(f"cuestionario desconocido: {questionnaire_id}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    topics = tuple((str(t["id"]), str(t["label"]), str(t.get("hint") or "")) for t in raw.get("topics") or [])
    return Questionnaire(id=str(raw.get("id") or questionnaire_id), topics=topics, raw=raw)


def questionnaire_ids() -> tuple[str, ...]:
    return tuple(sorted(p.stem for p in QUESTIONNAIRES_DIR.glob("*.yaml")))
