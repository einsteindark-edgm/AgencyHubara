"""Evidencia compartida entre familias: confirmación del cliente y orden."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory, Turn

CONFIRMED_TAGS = ("CONFIRMADO_SIN_DATOS", "CONFIRMADO_PAGO_PENDIENTE")


def confirmed_by(traj: Trajectory, upto_turn: int) -> Turn | None:
    """Turno que sostiene la confirmación de compra vigente hasta `upto_turn`.

    La señal más reciente manda: un aplazamiento posterior a un sí anula el
    sí (el cliente cambió de idea). El botón Confirmar y el flag persistido
    `confirmed` (lo marca el ingest al detectar el sí) también cuentan.
    """
    backing: Turn | None = None
    for t in traj.turns:
        if t.turn > upto_turn:
            break
        if t.signal == "deferral":
            backing = None
        if t.confirm_button or t.signal == "affirmation":
            backing = t
        elif t.confirmed is True and backing is None and t.signal != "deferral":
            backing = t
    return backing


def registered_order_turn(traj: Trajectory, upto_turn: int | None = None) -> Turn | None:
    for t in traj.turns:
        if upto_turn is not None and t.turn > upto_turn:
            break
        if t.tool_ok("register_order"):
            return t
    return None


def tag_turn(traj: Trajectory, tags: tuple[str, ...]) -> tuple[Turn, str] | None:
    """Primer turno donde quedó una etiqueta de `tags` (no degradada)."""
    for t in traj.turns:
        for change in t.state_changes:
            if change.get("tag") in tags:
                return t, str(change["tag"])
        for call in t.tools_named("manage_conversation_tag"):
            if call.ok is True and call.args.get("tag") in tags and call.note("degraded_from") is None:
                return t, str(call.args["tag"])
    # La etiqueta del cierre del episodio (sin turno que la ponga) cae en el
    # último turno. En modo turno el cierre es futuro: no se usa.
    if traj.focus_turn is None and traj.closing_tag in tags and traj.turns:
        return traj.turns[-1], str(traj.closing_tag)
    return None
