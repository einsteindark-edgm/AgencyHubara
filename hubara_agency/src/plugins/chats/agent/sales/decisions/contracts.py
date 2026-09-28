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
    # F1 · lectura del hilo: {bot_asked, bot_asked_by, answers_bot, answer, purchase…}.
    reading: dict = field(default_factory=dict)
    # F1 · sombra doble: lo que decidió el perfil en sombra (solo traza).
    shadow: dict = field(default_factory=dict)
    # F1 · calibración: {"allowed": bool, "reason"…}. Si Jev respondió con otra
    # versión que la calibrada, lo que actúa baja a sombra (sin nota ni reglas).
    acting: dict = field(default_factory=dict)
    # F6 · contrato de herramientas: {"required": [{topic, any_of, nudge, fields?}]}.
    # Lo aplican la segunda puerta del turno y la auditoría antes de enviar.
    tools: dict = field(default_factory=dict)
    # F6 · guía de la etapa: {stage, given_now, missing, going_back, stagnant, next}.
    guide: dict = field(default_factory=dict)


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


@dataclass(frozen=True)
class EgressInput:
    """Lo que el LLM escribió al cerrar el turno, antes de grabarlo y de
    enviarlo (activity `decide_egress`, workflow V2). Solo datos que el turno
    ya conoce: el texto, si es el primer contacto, las tools del turno y los
    textos que salieron en sus params, si se registró un pedido y si incluye
    portavelas (lo decide la tool contra el catálogo) y si el turno es
    administrativo (ningún texto va al cliente)."""

    session_id: str
    final_text: str = ""
    first_contact: bool = False
    tools_used: list[str] = field(default_factory=list)
    outbound_tool_texts: list[str] = field(default_factory=list)
    order_registered: bool = False
    portavelas_included: bool | None = None
    admin_turn: bool = False


@dataclass(frozen=True)
class EgressOutput:
    """Los veredictos del egreso (capacidades destinatario, rescate,
    portavelas y saludo). El workflow V2 solo los aplica: no lee el texto.

    * `text`: lo que sale al cliente ("" = nada).
    * `blocked`: el destinatario frenó el texto y el rescate no dejó nada.
    * `salvaged`: el rescate quitó párrafos que no eran para el cliente.
    * `portavelas`: se quitó el aviso del portavelas de un pedido sin él.
    * `greeting_needed`: hace falta la burbuja de bienvenida del primer contacto.
    * `llm_text`: el texto tras el rescate previo a grabar (lo que el turno
      devuelve y el LLM recuerda si no cambia después); `rescued_before_record`
      dice si ese rescate actuó.
    * `final_text`: el texto tras todas las reglas (el `llm_text` de la traza).
    * `guards`: las guardas que actuaron, en orden: `{name, before, after}`.
    * `verdicts`: la traza de cada capacidad consultada (`Verdict.to_trace`).
    """

    text: str = ""
    blocked: bool = False
    salvaged: bool = False
    portavelas: bool = False
    greeting_needed: bool = False
    verdicts: list[dict] = field(default_factory=list)
    llm_text: str = ""
    final_text: str = ""
    rescued_before_record: bool = False
    guards: list[dict] = field(default_factory=list)
    error: str | None = None
