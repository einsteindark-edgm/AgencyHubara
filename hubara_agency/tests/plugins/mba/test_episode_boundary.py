"""D1.10 — episodios con MBA al frente: cuando una tool de escritura del
connector cierra el episodio (chats responde `episode_closed`), el plugin mba
le manda a Meta Business Agent un `agent_event` `episode_closed` como NOTA DE
FRONTERA (no hay API para resetear el contexto del hilo): "si el cliente
vuelve, es una conversación nueva; no retomes el pedido anterior". Silencioso
para el cliente por instrucción; detrás de `MBA_EPISODE_BOUNDARY_EVENT`
(default OFF hasta verificar en F0 que un agent_event puede ser silencioso).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from src.plugins.mba.adapters.agent_event import FakeAgentEvent
from src.plugins.mba.domain.agent_events import (
    build_description,
    episode_closed_message,
)
from src.plugins.mba.domain.tool_calls import ToolCall
from src.plugins.mba.tools import ToolDeps, run_tool
from src.plugins.mba.tools import deps as deps_mod
from src.plugins.mba.use_cases.emit_agent_event import EmitAgentEvent
from src.sdk.runtime import FilesystemMetadataStore

CUSTOMER = "573001234567"
SESSION = f"wa_{CUSTOMER}"
NOW_MS = 1_757_400_000_000


# ── dominio ───────────────────────────────────────────────────────────────────


def test_the_boundary_note_is_silent_and_its_guidance_depends_on_the_closing() -> None:
    # con pedido en manos del equipo: derivar al colega, NO arrancar una venta nueva (M-1 del reviewer)
    for tag in ("COMPRA_EXITOSA", "CONFIRMADO_PAGO_PENDIENTE", "CONFIRMADO_SIN_DATOS"):
        msg = episode_closed_message(tag, order_reference="#22")
        assert "pedido #22" in msg and "cerrada" in msg
        assert (
            "colega" in msg
            and "ni inicies una venta nueva" in msg
            and "conversación nueva" not in msg
        )
    assert "pago pendiente" in episode_closed_message("CONFIRMADO_PAGO_PENDIENTE")
    # sin pedido: conversación nueva desde cero
    msg = episode_closed_message("RECHAZO")
    assert "no compró" in msg and "conversación nueva" in msg and "colega" not in msg
    assert "conversación nueva" in episode_closed_message("LO_QUE_SEA")
    d = build_description("episode_closed", msg)
    assert msg in d
    assert "no le escribas al cliente por esto ni lo menciones" in d.lower()
    assert "transmítele" not in d.lower()  # NO es una novedad para contarle al cliente


# ── use case: dedupe por episodio ─────────────────────────────────────────────


def _uc(vault: Path, port: FakeAgentEvent) -> EmitAgentEvent:
    return EmitAgentEvent(
        metadata_store=FilesystemMetadataStore(vault),
        port=port,
        is_customer_allowed=lambda c: True,
        is_enabled=lambda: True,
        controls_thread=lambda m, s: True,
        entity_id_fallback=lambda: "PHONE_777",
        now_ms=lambda: NOW_MS,
    )


async def test_episode_closed_is_emitted_once_per_episode(tmp_path: Path) -> None:
    FilesystemMetadataStore(tmp_path).write(
        SESSION, {"control_owner": "mba", "phone_number_id": "PHONE_777"}
    )
    port = FakeAgentEvent()
    uc = _uc(tmp_path, port)
    first = await uc.execute(
        SESSION, "episode_closed", episode_id="ep_001", message="m"
    )
    assert first.emitted is True
    again = await uc.execute(
        SESSION, "episode_closed", episode_id="ep_001", message="m"
    )
    assert (again.emitted, again.reason) == (False, "already_emitted")
    assert (
        await uc.execute(SESSION, "episode_closed", episode_id="ep_002", message="m")
    ).emitted is True
    assert len(port.calls) == 2
    events = json.loads((tmp_path / SESSION / "metadata.json").read_text())[
        "agent_events"
    ]
    assert [e["episode_id"] for e in events] == ["ep_001", "ep_002"]
    assert port.calls[0][4] == {"episode_id": "ep_001"}


# ── el hook en run_tool ───────────────────────────────────────────────────────


class _Chats:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    async def __call__(
        self, request: Any, session_key: str, action: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return dict(self.response)


class _Boundary:
    def __init__(self, exc: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, str, str | None]] = []
        self.exc = exc

    async def __call__(
        self,
        session_key: str,
        *,
        closing_tag: str,
        episode_id: str,
        order_id: str | None,
        order_reference: str | None = None,
    ) -> Any:
        self.calls.append(
            (session_key, closing_tag, episode_id, order_id, order_reference)
        )
        if self.exc is not None:
            raise self.exc
        return None


def _deps(chats: _Chats, boundary: _Boundary | None) -> ToolDeps:
    return ToolDeps(
        catalog=None,
        checkout=None,
        order_query=None,
        metadata=FilesystemMetadataStore(Path("/nonexistent")),
        chats=chats,
        episode_boundary=boundary,
    )


def _call(tool: str, **params: Any) -> ToolCall:
    return ToolCall(
        tool=tool, customer_phone=f"+{CUSTOMER}", session_key=SESSION, params=params
    )


async def test_a_write_tool_that_closes_the_episode_triggers_the_boundary_note_without_changing_its_response() -> (
    None
):
    closed = {
        "tag": "CONFIRMADO_SIN_DATOS",
        "proposed_tag": "INTERESADO",
        "applied": True,
        "reconciled": True,
        "reason": "shipping_data_without_order",
        "escalated": True,
        "episode_closed": {
            "episode_id": "ep_003",
            "closing_tag": "CONFIRMADO_SIN_DATOS",
        },
    }
    boundary = _Boundary()
    out = await run_tool(
        _call("manage_conversation_tag", tag="INTERESADO", motivo="x"),
        _deps(_Chats(closed), boundary),
    )
    assert (
        out["tag"] == "CONFIRMADO_SIN_DATOS"
        and out["episode_closed"] == closed["episode_closed"]
    )
    assert boundary.calls == [(SESSION, "CONFIRMADO_SIN_DATOS", "ep_003", None, None)]

    registered = {
        "registered": True,
        "order_id": "order_9",
        "order_reference": "#9",
        "subtotal_cop": 1,
        "episode_closed": {
            "episode_id": "ep_004",
            "closing_tag": "CONFIRMADO_PAGO_PENDIENTE",
        },
    }
    boundary = _Boundary()
    out = await run_tool(
        _call(
            "register_order",
            items=[{"handle": "x", "quantity": 1}],
            ciudad="Bogotá",
            direccion="d",
            telefono="t",
            nombre_recibe="n",
            metodo_pago="anticipado",
        ),
        _deps(_Chats(registered), boundary),
    )
    assert (
        out["registered"] is True and "episode_closed" not in out
    )  # el envelope del agente no cambia
    assert boundary.calls == [
        (SESSION, "CONFIRMADO_PAGO_PENDIENTE", "ep_004", "order_9", "#9")
    ]


async def test_no_boundary_note_when_nothing_closed_or_the_cast_failed() -> None:
    boundary = _Boundary()
    open_tag = {
        "tag": "INTERESADO",
        "proposed_tag": "INTERESADO",
        "applied": True,
        "reconciled": False,
        "reason": "applied",
        "escalated": False,
        "episode_closed": None,
    }
    await run_tool(
        _call("manage_conversation_tag", tag="INTERESADO", motivo="x"),
        _deps(_Chats(open_tag), boundary),
    )
    await run_tool(
        _call("set_order_slot", producto="x"),
        _deps(_Chats({"updated": True}), boundary),
    )
    failing = ToolDeps(
        catalog=None,
        checkout=None,
        order_query=None,
        metadata=FilesystemMetadataStore(Path("/x")),
        chats=None,
        episode_boundary=boundary,
    )
    await run_tool(_call("manage_conversation_tag", tag="RECHAZO", motivo="x"), failing)
    assert boundary.calls == []


async def test_a_failing_boundary_never_breaks_the_tool_response() -> None:
    closed = {
        "tag": "RECHAZO",
        "proposed_tag": "RECHAZO",
        "applied": True,
        "reconciled": False,
        "reason": "applied",
        "escalated": False,
        "episode_closed": {"episode_id": "ep_005", "closing_tag": "RECHAZO"},
    }
    for exc in (RuntimeError("boom"), HTTPException(status_code=503, detail="x")):
        out = await run_tool(
            _call("manage_conversation_tag", tag="RECHAZO", motivo="x"),
            _deps(_Chats(closed), _Boundary(exc)),
        )
        assert (
            out["tag"] == "RECHAZO" and out["episode_closed"]["episode_id"] == "ep_005"
        )


# ── la implementación real: en background, detrás del knob ───────────────────


async def test_the_default_boundary_emits_in_background_only_with_the_knob_on(
    tmp_path: Path, monkeypatch
) -> None:
    FilesystemMetadataStore(tmp_path).write(
        SESSION, {"control_owner": "mba", "phone_number_id": "PHONE_777"}
    )
    port = FakeAgentEvent()
    boundary = deps_mod.make_episode_boundary(lambda: _uc(tmp_path, port))

    monkeypatch.delenv("MBA_EPISODE_BOUNDARY_EVENT", raising=False)
    assert deps_mod.episode_boundary_enabled() is False
    assert (
        await boundary(
            SESSION, closing_tag="RECHAZO", episode_id="ep_001", order_id=None
        )
        is None
    )
    assert port.calls == []

    monkeypatch.setenv("MBA_EPISODE_BOUNDARY_EVENT", "1")
    assert deps_mod.episode_boundary_enabled() is True
    task = await boundary(
        SESSION,
        closing_tag="CONFIRMADO_PAGO_PENDIENTE",
        episode_id="ep_001",
        order_id="order_9",
        order_reference="#9",
    )
    assert (
        isinstance(task, asyncio.Task) and not task.done()
    )  # no bloquea la respuesta a Meta
    outcome = await task
    assert outcome.emitted is True and outcome.reason == "accepted"
    ((entity, to, event_type, description, payload),) = port.calls
    assert (entity, to, event_type) == ("PHONE_777", f"+{CUSTOMER}", "episode_closed")
    assert payload == {
        "closing_tag": "CONFIRMADO_PAGO_PENDIENTE",
        "order_id": "order_9",
        "order_reference": "#9",
        "episode_id": "ep_001",
    }
    assert (
        episode_closed_message("CONFIRMADO_PAGO_PENDIENTE", order_reference="#9")
        in description
    )
    assert (
        "pedido #9" in description and "order_9" not in description
    )  # referencia legible, no el id crudo
    assert "no le escribas al cliente" in description.lower()


async def test_the_default_boundary_swallows_use_case_errors(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("MBA_EPISODE_BOUNDARY_EVENT", "true")

    def _boom() -> EmitAgentEvent:
        raise RuntimeError("sin vault")

    task = await deps_mod.make_episode_boundary(_boom)(
        SESSION, closing_tag="RECHAZO", episode_id="ep_001", order_id=None
    )
    assert await task is None  # logueado, nunca levantado


def test_default_deps_wire_the_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps_mod, "get_catalog_client", lambda: None)
    monkeypatch.setattr(deps_mod, "get_checkout_verification_port", lambda: None)
    monkeypatch.setattr(deps_mod, "get_order_query_port", lambda: None)
    deps_mod.default_deps.cache_clear()
    try:
        assert callable(deps_mod.default_deps().episode_boundary)
    finally:
        deps_mod.default_deps.cache_clear()


async def test_the_real_path_end_to_end_posts_the_boundary_to_meta(
    tmp_path: Path, monkeypatch
) -> None:
    """Gotcha #1 / M-2 del reviewer: `default_deps()` con el use case REAL
    (adapter de Meta con respx) — `run_tool(register_order)` termina en un
    POST `agent_event` con `type=episode_closed`. Sin este test, un import
    roto en `_default_emit_agent_event` solo se vería como un warning en prod."""
    import httpx
    import respx

    from src.platform import config
    from src.sdk.runtime import FilesystemMetadataStore as _Store

    _Store(tmp_path).write(
        SESSION, {"control_owner": "mba", "phone_number_id": "PHONE_777"}
    )
    monkeypatch.setattr(deps_mod, "WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.setattr(config, "MBA_STANDBY_ENABLED", True)
    monkeypatch.setattr(config, "MBA_CUSTOMER_ALLOWLIST", frozenset({CUSTOMER}))
    monkeypatch.setenv("META_MBA_TOKEN", "tok")
    monkeypatch.setenv("MBA_EPISODE_BOUNDARY_EVENT", "1")
    for name in (
        "get_catalog_client",
        "get_checkout_verification_port",
        "get_order_query_port",
    ):
        monkeypatch.setattr(deps_mod, name, lambda: None)
    deps_mod.default_deps.cache_clear()
    try:
        deps = deps_mod.default_deps()
        deps.chats = _Chats(
            {
                "registered": True,
                "order_id": "order_9",
                "order_reference": "#9",
                "subtotal_cop": 1,
                "episode_closed": {
                    "episode_id": "ep_007",
                    "closing_tag": "CONFIRMADO_PAGO_PENDIENTE",
                },
            }
        )
        with respx.mock:
            route = respx.post("https://api.facebook.com/PHONE_777/agent_event").mock(
                return_value=httpx.Response(
                    200, json={"status": "accepted", "agent_event_id": "AE_9"}
                )
            )
            out = await run_tool(
                _call(
                    "register_order",
                    items=[{"handle": "x", "quantity": 1}],
                    ciudad="Bogotá",
                    direccion="d",
                    telefono="t",
                    nombre_recibe="n",
                    metodo_pago="anticipado",
                ),
                deps,
            )
            assert out["registered"] is True and "_episode_closed" not in out
            await asyncio.gather(*list(deps_mod._background))
            assert route.call_count == 1
            body = json.loads(route.calls.last.request.content)
            assert (
                body["to"] == f"+{CUSTOMER}"
                and body["event"]["type"] == "episode_closed"
            )
            assert "pedido #9" in body["event"]["description"]
        events = json.loads((tmp_path / SESSION / "metadata.json").read_text())[
            "agent_events"
        ]
        assert (
            events[-1]["status"] == "accepted" and events[-1]["episode_id"] == "ep_007"
        )
    finally:
        deps_mod.default_deps.cache_clear()
