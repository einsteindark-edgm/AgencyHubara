"""Mini-DSL de tests del scorecard: arma turnos y trayectorias en una línea.

    traj(
        T(1, sent=["¡Buenos días! Bienvenido a *Hubara*"], tools=[tool("send_quick_replies")], first_contact=True),
        T(2, inbound="Voy apenas en camino a casa", signal="deferral",
          tools=[tool("request_shipping_details", ok=False, error="customer_deferred")]),
        closing_tag="INTERESADO",
    )
"""
from __future__ import annotations

from typing import Any

from src.plugins.chats.agent.sales_eval.scorecard.trajectory import (
    ToolCall,
    Trajectory,
    Turn,
    _intents_for,
)


def tool(name: str, ok: bool | None = True, error: str | None = None, notes=(), **args: Any) -> ToolCall:
    return ToolCall(name=name, ok=ok, error=error, notes=tuple(notes), args=dict(args))


def T(
    n: int,
    *,
    trigger: str = "customer",
    inbound: str = "",
    signal: str | None = None,
    sent=(),
    llm: str = "",
    suppressed: str | None = None,
    narration=(),
    tools=(),
    intents=None,
    guards=(),
    stage_in: str | None = "descubrimiento",
    stage_out: str | None = None,
    draft: dict | None = None,
    confirmed: bool | None = False,
    state: dict | None = None,
    first_contact: bool | None = None,
    at_ms: int | None = None,
) -> Turn:
    tools_t = tuple(tools)
    guards_t = tuple(guards)
    return Turn(
        turn=n,
        at_ms=at_ms if at_ms is not None else n * 60_000,
        trigger=trigger,
        inbound_text=inbound,
        signal=signal,
        sent_texts=tuple(sent),
        llm_text=llm,
        suppressed_reason=suppressed,
        discarded_narration=tuple(narration),
        tools=tools_t,
        intents=tuple(intents) if intents is not None else _intents_for(tools_t, guards_t),
        guards=guards_t,
        stage_in=stage_in,
        stage_out=stage_out if stage_out is not None else stage_in,
        draft=dict(draft) if draft is not None else {},
        confirmed=confirmed,
        state=dict(state) if state is not None else {"tag": None, "route": "ventas", "changes": []},
        first_contact=first_contact if first_contact is not None else (n == 1),
    )


def traj(
    *turns: Turn,
    fidelity: str = "trace",
    closing_tag: str | None = None,
    order_id: str | None = None,
    session_id: str = "wa_100000000001",
    episode_id: str = "ep_007",
) -> Trajectory:
    return Trajectory(
        session_id=session_id,
        episode_id=episode_id,
        fidelity=fidelity if turns or fidelity != "trace" else "empty",
        turns=tuple(turns),
        closing_tag=closing_tag,
        order_id=order_id,
    )
