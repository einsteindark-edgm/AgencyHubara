"""Las decisiones del motor en un caso del laboratorio (auditoría del brazo B).

Cada capacidad que decide durante el caso (las lecturas del ingest, las tools
y las activities del turno) deja su rastro en `<vault del sandbox>/_decisions/`
(métricas y cola de desacuerdos), que se borra con el sandbox. Así el
laboratorio no podía mostrar qué capacidad decidió qué, ni si lo decidió Jev o
la regla porque Jev falló.

`CaseDecisions` mira cada veredicto del proceso del caso
(`capabilities.watching_verdicts`) y lo guarda con su etapa: `ingest` (con el
número del mensaje de la ráfaga), `turno` o `complemento` (el segundo turno
que agenda la verificación). `case_disagreements` junta la cola de
desacuerdos del sandbox antes de que se borre. Las dos salidas son compactas,
JSON y con el texto anonimizado como en la cola (`anonymize_text` con lo
personal del borrador).
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions.decision_log import compact_verdict, plain_value

# Lo mismo que guarda producción (`decision_log`): el laboratorio y Calidad
# LLM muestran la misma decisión compacta y anonimizada.
_plain = plain_value
_compact = compact_verdict


class CaseDecisions:
    """El observador de las decisiones de un caso (ver el módulo). `capture`:
    lo que el sandbox anota del turno; con la traza del turno ya grabada, lo
    que sigue es el complemento."""

    def __init__(self, capture: Any) -> None:
        self._capture = capture
        self._stage = "ingest"
        self._message: int | None = None
        self._rows: list[tuple[str, int | None, dict[str, Any]]] = []

    def ingest_message(self, k: int) -> None:
        """Lo que sigue lo decide el ingest del mensaje `k` de la ráfaga."""
        self._stage, self._message = "ingest", k

    def turn(self) -> None:
        """Lo que sigue lo decide el turno (tools y activities)."""
        self._stage, self._message = "turno", None

    def __call__(self, verdict: Any) -> None:
        stage = self._stage
        if stage == "turno" and getattr(self._capture, "trace_payloads", None):
            stage = "complemento"
        trace = verdict.to_trace() if hasattr(verdict, "to_trace") else dict(verdict)
        self._rows.append((stage, self._message, trace))

    def published(self, *, redact: Sequence[str]) -> list[dict[str, Any]]:
        """Las decisiones del caso en orden, compactas y anonimizadas."""
        out: list[dict[str, Any]] = []
        for stage, message, trace in self._rows:
            head: dict[str, Any] = {"stage": stage}
            if message is not None:
                head["message"] = message
            out.append({**head, **_plain(_compact(trace), redact)})
        return out


def case_redact_terms(*metadatas: dict[str, Any]) -> list[str]:
    """Lo personal de los borradores del caso (al empezar y al terminar el
    turno; un episodio cerrado también): lo que la cola tapa de un cliente."""
    from src.plugins.chats.agent.sales.decisions.context import redact_terms_from_slots
    from src.plugins.chats.agent.sales.turn_trace import draft_slots

    terms: set[str] = set()
    for metadata in metadatas:
        for episode in metadata.get("episodes") or []:
            if isinstance(episode, dict):
                terms.update(redact_terms_from_slots(draft_slots(episode)))
    return sorted(terms)


def case_disagreements(vault_dir: Path, *, redact: Sequence[str]) -> list[dict[str, Any]]:
    """La cola de desacuerdos del sandbox del caso: qué vio Jev (`state`,
    ya anonimizado por la cola) y qué dijeron la regla y Jev."""
    from src.sdk.connectorkit import DisagreementLog

    return [
        _plain({"capability": item.get("capability"), "rule": item.get("rule"), "jev": item.get("jev"),
                "state": item.get("state")}, redact)
        for item in DisagreementLog(Path(vault_dir)).items()
    ]
