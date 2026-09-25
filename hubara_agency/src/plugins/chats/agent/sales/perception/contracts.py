"""DTOs de las capas ① y ③ (R-JSON: frozen, solo tipos JSON)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PerceiveInput:
    session_id: str
    profile: str
    messages: list[dict] = field(default_factory=list)  # [{text, ts_ms}]
    pending: list[str] = field(default_factory=list)
    last_bot_text: str | None = None


@dataclass(frozen=True)
class PerceiveOutput:
    ok: bool
    profile: str
    model: str = ""
    topics: list[dict] = field(default_factory=list)  # [{topic, msg, p}]
    stage: str | None = None
    answers: list[dict] = field(default_factory=list)  # traza: [{q, type, p, choice, picked, msg}]
    error: str | None = None
    latency_ms: int = 0
    cost_usd: float | None = None


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
