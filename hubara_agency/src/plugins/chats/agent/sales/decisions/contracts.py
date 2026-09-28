"""Contratos del motor de decisiones con sus consumidores (R-JSON: frozen,
solo tipos JSON).

Es lo ÚNICO que ven el workflow y las tools. Regla de evolución (diseño v2
§03): solo crece con campos opcionales, y cada campo nuevo trae un valor por
defecto que reproduce el turno de hoy. Temporal decodifica el resultado GRABADO
con el tipo de la activity: los campos que faltan toman su valor por defecto y
los desconocidos se ignoran, así una conversación en vuelo replayea con el
código nuevo. Nunca se quita ni se cambia el tipo de un campo.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Versión del contrato que escribe el motor. 0 = resultado anterior al motor
#: (no trae nota ni reglas: el turno es el de hoy).
CONTRACT_VERSION = 1


@dataclass(frozen=True)
class PerceiveInput:
    session_id: str
    profile: str
    messages: list[dict] = field(default_factory=list)  # [{text, ts_ms}]
    pending: list[str] = field(default_factory=list)
    last_bot_text: str | None = None


@dataclass(frozen=True)
class TurnDecisions:
    """Lo que el motor decidió ANTES del turno (activity `perceive_burst`).

    Campos de la capa ① de siempre (`ok` … `cost_usd`) y, desde el motor
    (`contract` ≥ 1), lo que el workflow aplica sin saber de preguntas ni
    umbrales: la nota del turno y las reglas de la capa ②."""

    ok: bool
    profile: str
    model: str = ""
    topics: list[dict] = field(default_factory=list)  # [{topic, msg, p, label}]
    stage: str | None = None
    answers: list[dict] = field(default_factory=list)  # traza: [{q, type, p, choice, picked}]
    error: str | None = None
    latency_ms: int = 0
    cost_usd: float | None = None
    # ── motor de decisiones (2026-09-28) ──
    contract: int = 0
    # {profile, questions, policy, model}: con qué se decidió (traza y laboratorio).
    versions: dict = field(default_factory=dict)
    # La única nota que se inyecta al turno; la redacta el motor.
    note: str | None = None
    # Capa ②: {asunto: {"tools": [...], "words": [...], "any_text": bool}}.
    coverage: dict = field(default_factory=dict)


#: Nombre histórico de la salida de la capa ① (laboratorio y tests).
PerceiveOutput = TurnDecisions


@dataclass(frozen=True)
class VerifyInput:
    session_id: str
    profile: str
    messages: list[dict] = field(default_factory=list)
    topics: list[dict] = field(default_factory=list)
    reply_text: str = ""
    components: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VerifyOutput:
    ok: bool
    decision: str = "send"  # send | complement | pending
    missing: list[str] = field(default_factory=list)
    answers: list[dict] = field(default_factory=list)
    model: str = ""
    error: str | None = None
    latency_ms: int = 0
    cost_usd: float | None = None
    # ── motor de decisiones (2026-09-28) ──
    # El turno de sistema del complemento, redactado por el motor.
    complement_note: str | None = None
