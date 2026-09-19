"""Contrato tool↔loop de `manage_conversation_tag` (runs b06636a6 / 5ed9af2d).

Misma clase que L-20: tras una tool TERMINAL el tool-loop pedía otro `llm_chat`
y el modelo, obligado a emitir algo, le acusaba recibo al sistema ("Etiqueta
registrada."). La tool ahora DECLARA en su envelope (`tag_closure`) si el tag
es autosuficiente y trae, ya validado, el texto para el cliente; el loop decide
el corte con eso. La validación vive acá (activity), no en el workflow: un
regex cuyo veredicto decide commands es lógica de replay (L-21).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools.tags import ManageConversationTagTool

NOW = 1_789_406_554_683
KEY = "wa_test_tag_closure"


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _active_episode(*, confirmed: bool = False) -> dict[str, Any]:
    ep: dict[str, Any] = {
        "episode_id": "ep_001",
        "started_at_ms": NOW - 5000,
        "closed_at_ms": None,
    }
    if confirmed:
        ep["order_draft"] = {
            "slots": {"producto": "Cubo Love"},
            "updated_at_ms": NOW - 1000,
            "confirmed_at_ms": NOW - 500,
            "confirmed_by": "text",
        }
    return ep


def _seed(vault: Path, md: dict[str, Any]) -> None:
    path = vault / KEY / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(md, ensure_ascii=False), encoding="utf-8")


async def _tag(tmp_path: Path, ctx: ToolContext, **params: Any) -> dict[str, Any]:
    tool = ManageConversationTagTool(workspace=str(tmp_path), vault_dir=tmp_path)
    return json.loads(await tool.execute_with_context(ctx, **params))


# ------------------------------------------------ qué tags terminan el turno


@pytest.mark.asyncio
@pytest.mark.parametrize("tag", ["INTERESADO", "RECHAZO", "COMPRA_EXITOSA"])
async def test_self_sufficient_tag_declares_it_ends_the_turn(
    ctx: ToolContext, tmp_path: Path, tag: str
) -> None:
    """Tras estos tags no queda NADA por hacer: pedirle otro mensaje al modelo
    es abrirle el canal del acuse."""
    _seed(tmp_path, {"episodes": [_active_episode()]})

    result = await _tag(tmp_path, ctx, tag=tag, motivo="cierre")

    assert result["tag_closure"] == {"tag": tag, "ends_turn": True}


@pytest.mark.asyncio
async def test_tag_that_requires_escalation_does_not_end_the_turn(
    ctx: ToolContext, tmp_path: Path
) -> None:
    """CONFIRMADO_SIN_DATOS va en combo con `escalate_to_human`: el `llm_chat`
    siguiente NO es un canal ambiguo — el modelo tiene trabajo real (escalar con
    su resumen para el colega). Cortar acá degradaría el relevo de la venta más
    valiosa al motivo genérico de la red de seguridad."""
    _seed(tmp_path, {"episodes": [_active_episode(confirmed=True)]})

    result = await _tag(
        tmp_path, ctx, tag="CONFIRMADO_SIN_DATOS", motivo="confirmó y no mandó datos"
    )

    assert result["tag_closure"] == {"tag": "CONFIRMADO_SIN_DATOS", "ends_turn": False}
    assert "ORDER_PENDING_SHIPPING_DETAILS" in result["message"]


@pytest.mark.asyncio
async def test_payment_pending_tag_does_not_end_the_turn(
    ctx: ToolContext, tmp_path: Path
) -> None:
    _seed(
        tmp_path,
        {
            "registered_order": {"success": True, "order_id": "o1"},
            "episodes": [_active_episode(confirmed=True)],
        },
    )

    result = await _tag(
        tmp_path, ctx, tag="CONFIRMADO_PAGO_PENDIENTE", motivo="pedido o1 registrado"
    )

    assert result["tag_closure"] == {
        "tag": "CONFIRMADO_PAGO_PENDIENTE",
        "ends_turn": False,
    }
    assert "PAYMENT_VERIFICATION_PENDING" in result["message"]


@pytest.mark.asyncio
async def test_degraded_confirmado_sin_datos_closes_as_interesado(
    ctx: ToolContext, tmp_path: Path
) -> None:
    """Sin confirmación de compra la etiqueta se degrada a INTERESADO y NO se
    escala ("el bot sigue a cargo"): el tag EFECTIVO es autosuficiente."""
    _seed(tmp_path, {"episodes": [_active_episode(confirmed=False)]})

    result = await _tag(
        tmp_path, ctx, tag="CONFIRMADO_SIN_DATOS", motivo="pidió datos y no llegaron"
    )

    assert result["degraded_from"] == "CONFIRMADO_SIN_DATOS"
    assert result["tag_closure"] == {"tag": "INTERESADO", "ends_turn": True}


@pytest.mark.asyncio
async def test_degraded_tag_never_declares_the_customer_message(
    ctx: ToolContext, tmp_path: Path
) -> None:
    """El modelo redactó su despedida bajo una premisa que la tool RECHAZÓ: creía
    estar cerrando un CONFIRMADO_SIN_DATOS ("un colega te escribe por los datos
    de envío") y la etiqueta se degradó a INTERESADO — nadie va a escalar. Si el
    envelope declarara ese texto, el loop cortaría el turno y le mandaría al
    cliente la promesa de un colega que no existe, y el modelo nunca leería el
    aviso de degradación (el mecanismo del fix del run 01a0a0f1). Sin texto
    declarado, en turno de cliente el loop NO corta y el modelo responde con el
    aviso a la vista; en turno admin corta igual, sin texto."""
    _seed(tmp_path, {"episodes": [_active_episode(confirmed=False)]})

    result = await _tag(
        tmp_path,
        ctx,
        tag="CONFIRMADO_SIN_DATOS",
        motivo="pidió datos y no llegaron",
        customer_message="¡Listo! Un colega del equipo te escribe para completar los datos de envío 🤍",
    )

    assert result["degraded_from"] == "CONFIRMADO_SIN_DATOS"
    assert result["tag_closure"] == {"tag": "INTERESADO", "ends_turn": True}
    assert "escalate_to_human" in result["message"], "el aviso de degradación sigue ahí"


# ------------------------------------------- el texto del cliente (param tipado)

_FAREWELL = "Con gusto, aquí estaré por si más adelante te animas 🤍"


@pytest.mark.asyncio
async def test_combo_tag_ignores_the_customer_message(
    ctx: ToolContext, tmp_path: Path
) -> None:
    """En los tags combo la despedida viaja en `escalate_to_human`: acá no se
    declara texto (el turno no termina en esta tool)."""
    _seed(tmp_path, {"episodes": [_active_episode(confirmed=True)]})

    result = await _tag(
        tmp_path,
        ctx,
        tag="CONFIRMADO_SIN_DATOS",
        motivo="confirmó y no mandó datos",
        customer_message=_FAREWELL,
    )

    assert result["tag_closure"] == {"tag": "CONFIRMADO_SIN_DATOS", "ends_turn": False}


@pytest.mark.asyncio
async def test_customer_message_travels_in_the_closure(
    ctx: ToolContext, tmp_path: Path
) -> None:
    """El guion pedía "etiquétalo en el mismo turno de la despedida", pero el
    content junto a una tool call se descarta (default-deny): la despedida solo
    puede viajar en un param de la tool (L-20)."""
    _seed(tmp_path, {"episodes": [_active_episode()]})

    result = await _tag(
        tmp_path, ctx, tag="RECHAZO", motivo="dijo que no", customer_message=_FAREWELL
    )

    assert result["tag_closure"] == {
        "tag": "RECHAZO",
        "ends_turn": True,
        "customer_message": _FAREWELL,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "safe"),
    [
        # El acuse del run b06636a6 pegado a una despedida real.
        ("Etiqueta registrada. ¡Gracias por escribirnos! 🤍", "¡Gracias por escribirnos! 🤍"),
        # Vocabulario que delata que no atendía una persona (run 5ed9af2d).
        ("Con gusto 🤍. Un humano te escribe si hace falta.", "Con gusto 🤍."),
    ],
)
async def test_unsafe_sentences_are_dropped_from_the_customer_message(
    ctx: ToolContext, tmp_path: Path, raw: str, safe: str
) -> None:
    _seed(tmp_path, {"episodes": [_active_episode()]})

    result = await _tag(
        tmp_path, ctx, tag="RECHAZO", motivo="dijo que no", customer_message=raw
    )

    assert result["tag_closure"]["customer_message"] == safe


@pytest.mark.asyncio
async def test_customer_message_with_nothing_safe_is_declared_empty(
    ctx: ToolContext, tmp_path: Path
) -> None:
    """El modelo YA habló y lo que dijo era un parte interno: la clave viaja
    vacía (≠ ausente) para que el loop termine el turno en silencio en vez de
    reabrirle el canal con otro `llm_chat`."""
    _seed(tmp_path, {"episodes": [_active_episode()]})

    result = await _tag(
        tmp_path,
        ctx,
        tag="RECHAZO",
        motivo="dijo que no",
        customer_message="Etiqueta RECHAZO registrada.",
    )

    assert result["tag_closure"] == {
        "tag": "RECHAZO",
        "ends_turn": True,
        "customer_message": "",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", "   ", "\n"])
async def test_blank_customer_message_counts_as_not_given(
    ctx: ToolContext, tmp_path: Path, blank: str
) -> None:
    """Cierre por ghosting: el cliente no está y el modelo manda "" (o nada).
    Sin texto no hay nada que declarar — la clave queda AUSENTE."""
    _seed(tmp_path, {"episodes": [_active_episode()]})

    result = await _tag(
        tmp_path, ctx, tag="INTERESADO", motivo="dejó de responder", customer_message=blank
    )

    assert result["tag_closure"] == {"tag": "INTERESADO", "ends_turn": True}


def test_customer_message_is_optional_in_the_schema() -> None:
    """Una sesión en vuelo trae las tool_definitions del bootstrap pre-deploy:
    llama sin el param y NO debe rebotar en `validate_params`."""
    schema = ManageConversationTagTool.parameters

    assert "customer_message" in schema["properties"]
    assert "customer_message" not in schema["required"]


def test_customer_message_definition_does_not_prime_bot_wording() -> None:
    """El modelo redacta la despedida con esta definición en frente: si nombra
    "humano"/"bot"/"automático", esas palabras terminan en el texto."""
    from src.platform.llm_text_sanitizer import breaks_human_persona

    definition =ManageConversationTagTool.parameters["properties"]["customer_message"]

    assert not breaks_human_persona(definition["description"])


# ------------------------------------------------ el result no invita al acuse


@pytest.mark.asyncio
@pytest.mark.parametrize("tag", ["INTERESADO", "RECHAZO"])
async def test_result_message_is_not_shaped_like_a_report(
    ctx: ToolContext, tmp_path: Path, tag: str
) -> None:
    """El viejo "Éxito. Interacción etiquetada como 'X'." es un caso de LEAK en
    `test_admin_leak_detector`: si el modelo lo parafrasea sale un parte interno,
    y en el historial es few-shot de "tras una tool se acusa recibo"."""
    from src.sdk.agentkit import looks_like_admin_leak

    _seed(tmp_path, {"episodes": [_active_episode()]})

    result = await _tag(tmp_path, ctx, tag=tag, motivo="cierre")

    assert not looks_like_admin_leak(result["message"])
    assert tag not in result["message"]


def test_every_tag_the_workflow_must_escalate_is_declared_as_combo_by_the_tool() -> None:
    """Anti-drift entre capas: la red de seguridad del WORKFLOW
    (`_CLOSING_TAGS_REQUIRING_ESCALATION`) y la declaración de la TOOL
    (`_ESCALATION_REASON_BY_TAG`) codifican lo mismo. Si un tag que exige
    escalación no está en el mapa de la tool, esta lo declara autosuficiente, el
    turno de cierre por ghosting se corta ANTES de que el modelo escale y el
    colega recibe el motivo genérico de la red en vez del resumen del modelo."""
    from src.plugins.chats.agent.sales.tools.tags import _ESCALATION_REASON_BY_TAG
    from src.plugins.chats.agent.sales.workflows.sales_session import (
        _CLOSING_TAGS_REQUIRING_ESCALATION,
    )

    for tag, reason in _CLOSING_TAGS_REQUIRING_ESCALATION.items():
        assert _ESCALATION_REASON_BY_TAG.get(tag) == reason, tag


@pytest.mark.asyncio
async def test_rejected_tag_has_no_closure(ctx: ToolContext, tmp_path: Path) -> None:
    """Una precondición fallida no etiquetó nada: el LLM debe seguir para
    corregir el orden de las tools (no hay cierre que declarar)."""
    _seed(tmp_path, {"episodes": [_active_episode()]})

    result = await _tag(
        tmp_path, ctx, tag="CONFIRMADO_PAGO_PENDIENTE", motivo="sin register_order"
    )

    assert "error" in result
    assert "tag_closure" not in result
