"""Tests de `EscalateToHumanTool` (platform/tools/escalation.py).

Verifica que la tool:
  * NO importa `temporal_client`,
  * NO llama `start_workflow` ni `signal`,
  * Escribe `metadata.json` con `tag=HUMANO`, `active_route=humano`,
    `motivo=summary`, `escalation_reason=reason_category` y un append a
    `status_history`,
  * Devuelve JSON con `escalation_decision` listo para que el workflow
    helper lo parsee.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.tools.escalation import EscalateToHumanTool


def _strip_python_comments(src: str) -> str:
    """Mismo helper que test_transfer_tool: ignora docstrings/comments al
    inspeccionar codigo ejecutable."""
    import io
    import tokenize

    out: list[str] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(src).readline)
        prev_toktype = tokenize.INDENT
        for tok in tokens:
            toktype, tokval = tok.type, tok.string
            if toktype == tokenize.COMMENT:
                continue
            if toktype == tokenize.STRING and prev_toktype in (
                tokenize.INDENT,
                tokenize.NEWLINE,
                tokenize.NL,
            ):
                continue
            out.append(tokval + " ")
            prev_toktype = toktype
    except tokenize.TokenizeError:
        return src
    return "".join(out)


def _ctx(session: str) -> ToolContext:
    return ToolContext(session_key=session, channel="whatsapp", chat_id=session)


def test_escalation_tool_does_not_import_temporal_client() -> None:
    import src.platform.tools.escalation as escalation_mod

    src = _strip_python_comments(inspect.getsource(escalation_mod))
    assert "get_temporal_client" not in src
    assert "from src.platform.temporal.client" not in src
    assert "start_workflow" not in src
    assert ".signal(" not in src


@pytest.mark.asyncio
async def test_escalation_tool_writes_metadata_and_emits_decision(tmp_path: Path) -> None:
    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)

    raw = await tool.execute_with_context(
        _ctx("wa_5491111111111"),
        reason_category="BULK_ORDER",
        summary="cliente quiere 50 velas para evento corporativo",
    )
    payload = json.loads(raw)

    # Envelope incluye la decision para que el workflow la consuma.
    assert "escalation_decision" in payload
    decision = payload["escalation_decision"]
    assert decision["session_id"] == "wa_5491111111111"
    assert decision["reason_category"] == "BULK_ORDER"
    assert decision["summary"] == "cliente quiere 50 velas para evento corporativo"
    assert "message" in payload

    # metadata.json se actualizo en el vault PER-SESION (no en el workspace
    # canonico, mismo patron que TransferToSalesAgentTool / ManageConversationTagTool).
    metadata_path = tmp_path / "wa_5491111111111" / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["active_route"] == "humano"
    assert metadata["tag"] == "HUMANO"
    assert metadata["motivo"] == "cliente quiere 50 velas para evento corporativo"
    assert metadata["escalation_reason"] == "BULK_ORDER"
    assert len(metadata["status_history"]) == 1
    entry = metadata["status_history"][0]
    assert entry["tag"] == "HUMANO"
    assert entry["active_route"] == "humano"
    assert entry["reason_category"] == "BULK_ORDER"
    assert isinstance(entry["timestamp"], float)


# --- Despedida del relevo (run 5ed9af2d, 2026-09-18) -------------------------
# El cliente recibió "Listo, la conversación quedó en manos del equipo humano."
# porque la tool no tenía canal para la despedida: el texto bueno del LLM
# ("te coordino con un colega del equipo…") viajó como content junto a la tool
# call y el default-deny lo descartó; después el loop forzó otro llm_chat y el
# acuse interno salió por WhatsApp. Contrato nuevo: la despedida viaja en
# `customer_message` y la tool devuelve en el envelope el texto FINAL que el
# cliente va a leer (validado; si rompe la persona → despedida aprobada).

_GOOD_FAREWELL = (
    "Para 100 unidades te coordino con un colega del equipo, que maneja ese "
    "tipo de pedidos y te responde en este mismo chat 🤍"
)


@pytest.mark.asyncio
async def test_customer_message_travels_back_in_the_envelope(tmp_path: Path) -> None:
    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)

    raw = await tool.execute_with_context(
        _ctx("wa_573001234567"),
        reason_category="BULK_ORDER",
        summary="cliente pide ~100 presentes sencillos",
        customer_message=_GOOD_FAREWELL,
    )

    assert json.loads(raw)["customer_message"] == _GOOD_FAREWELL


@pytest.mark.asyncio
async def test_persona_breaking_customer_message_is_replaced_by_approved_farewell(
    tmp_path: Path,
) -> None:
    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)

    raw = await tool.execute_with_context(
        _ctx("wa_573001234567"),
        reason_category="BULK_ORDER",
        summary="cliente pide ~100 presentes sencillos",
        # Texto literal que recibió el cliente en el run 5ed9af2d.
        customer_message="Listo, la conversación quedó en manos del equipo humano.",
    )

    farewell = json.loads(raw)["customer_message"]
    assert "humano" not in farewell.lower()
    assert "colega" in farewell.lower()


@pytest.mark.asyncio
async def test_missing_customer_message_falls_back_to_approved_farewell(
    tmp_path: Path,
) -> None:
    """Sesiones en vuelo con el schema viejo llaman sin el param: el cliente
    igual recibe una despedida aprobada, nunca silencio."""
    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)

    raw = await tool.execute_with_context(
        _ctx("wa_573001234567"),
        reason_category="EXPLICIT_REQUEST",
        summary="cliente pide hablar con alguien del equipo",
    )

    farewell = json.loads(raw)["customer_message"]
    assert "colega" in farewell.lower()
    assert "humano" not in farewell.lower()


@pytest.mark.asyncio
async def test_tool_result_does_not_seed_human_wording_nor_orders_silence(
    tmp_path: Path,
) -> None:
    """El envelope que queda en el historial del LLM no siembra vocabulario
    'humano' ni le ordena callar (la orden de callar + un llm_chat forzado fue
    lo que produjo el acuse del run 5ed9af2d)."""
    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)

    raw = await tool.execute_with_context(
        _ctx("wa_573001234567"),
        reason_category="BULK_ORDER",
        summary="cliente pide ~100 presentes sencillos",
        customer_message=_GOOD_FAREWELL,
    )

    message = json.loads(raw)["message"].lower()
    assert "human" not in message
    assert "no generes" not in message


@pytest.mark.asyncio
async def test_payment_verification_falls_back_to_the_order_registered_farewell(
    tmp_path: Path,
) -> None:
    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)

    raw = await tool.execute_with_context(
        _ctx("wa_573001234567"),
        reason_category="PAYMENT_VERIFICATION_PENDING",
        summary="Pedido 42 registrado. Verificar pago.",
        customer_message="Un humano verificará tu pago en breve.",
    )

    # Sin marca: la tool es de plataforma (multi-tenant); la despedida con
    # marca la escribe el LLM desde el guion de su workspace.
    assert json.loads(raw)["customer_message"] == (
        "Listo, tu pedido quedó registrado 🤍. Gracias por elegirnos."
    )


@pytest.mark.asyncio
async def test_only_the_persona_breaking_sentence_is_dropped(tmp_path: Path) -> None:
    """Reemplazar TODA la despedida por una palabra marcada botaba el aviso del
    portavelas (regla de negocio: el comprador debe saber que los colores se
    escogen al finalizar el pago). Se cae solo la oración que rompe la persona."""
    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)

    raw = await tool.execute_with_context(
        _ctx("wa_573001234567"),
        reason_category="PAYMENT_VERIFICATION_PENDING",
        summary="Pedido 42 registrado. Verificar pago.",
        customer_message=(
            "Listo, tu pedido quedó registrado 🤍. Un humano verificará tu pago "
            "en breve. Al finalizar el pago del pedido se escogen los colores "
            "del portavelas, según disponibilidad."
        ),
    )

    farewell = json.loads(raw)["customer_message"]
    assert "humano" not in farewell.lower()
    assert "tu pedido quedó registrado" in farewell
    assert "se escogen los colores del portavelas" in farewell


@pytest.mark.asyncio
async def test_thin_remainder_falls_back_to_the_approved_farewell(tmp_path: Path) -> None:
    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)

    raw = await tool.execute_with_context(
        _ctx("wa_573001234567"),
        reason_category="EXPLICIT_REQUEST",
        summary="cliente pide hablar con alguien del equipo",
        customer_message="Claro 🤍. Ya te paso con un asesor humano.",
    )

    assert json.loads(raw)["customer_message"] == (
        "Un colega del equipo te responde en este mismo chat 🤍"
    )


def test_customer_message_is_in_the_schema_but_optional(tmp_path: Path) -> None:
    """Opcional a propósito: una sesión en vuelo (tool_definitions viejas)
    llama sin el param y NO debe rebotar en validate_params — cae al fallback."""
    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)

    assert "customer_message" in tool.parameters["properties"]
    assert not tool.validate_params({"reason_category": "BULK_ORDER", "summary": "x"})


def test_tool_definition_does_not_prime_human_wording(tmp_path: Path) -> None:
    """Lo que el LLM lee al redactar `customer_message` no debe sembrarle la
    palabra que después rompe la persona (el nombre de la tool se conserva por
    compatibilidad con historiales y evals)."""
    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)

    definition = json.dumps(
        {"description": tool.description, "parameters": tool.parameters},
        ensure_ascii=False,
    ).lower()

    assert "human" not in definition


@pytest.mark.asyncio
async def test_escalation_tool_appends_to_existing_status_history(tmp_path: Path) -> None:
    """Si la sesion ya tenia status_history, lo extendemos sin perderlo."""
    session_id = "wa_5492222222222"
    metadata_dir = tmp_path / session_id
    metadata_dir.mkdir()
    metadata_path = metadata_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "active_route": "ventas",
                "tag": "INTERESADO",
                "motivo": "preguntó por aromas",
                "status_history": [
                    {
                        "tag": "INTERESADO",
                        "motivo": "preguntó por aromas",
                        "active_route": "ventas",
                        "timestamp": 1.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)
    await tool.execute_with_context(
        _ctx(session_id),
        reason_category="POST_SALE_ISSUE",
        summary="cliente reporta vela rota",
    )

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["tag"] == "HUMANO"
    assert metadata["active_route"] == "humano"
    # El history conserva la entrada previa + agrega la nueva.
    assert len(metadata["status_history"]) == 2
    assert metadata["status_history"][0]["tag"] == "INTERESADO"
    assert metadata["status_history"][1]["tag"] == "HUMANO"
    assert metadata["status_history"][1]["reason_category"] == "POST_SALE_ISSUE"


@pytest.mark.asyncio
async def test_escalation_tool_rejects_invalid_reason_category(tmp_path: Path) -> None:
    """El JSON schema enum rechaza categorias fuera de la taxonomia."""

    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)
    # ToolBase.validate_params es el chequeo que dispara el dispatch real.
    # Una categoria inventada debe fallar el schema validation.
    err = tool.validate_params(
        {"reason_category": "INVENTED_CATEGORY", "summary": "x"}
    )
    assert err is not None
    # validate_params puede devolver str o list[str] segun la version de
    # exoclaw.agent.tools — normalizo antes de buscar.
    err_text = " ".join(err) if isinstance(err, list) else str(err)
    assert "reason_category" in err_text or "enum" in err_text.lower()


@pytest.mark.parametrize(
    "category",
    [
        "BULK_ORDER",
        "DISCOUNT_REQUEST",
        "WHOLESALE_B2B",
        "CORPORATE_EVENT",
        "CUSTOMIZATION",
        "POST_SALE_ISSUE",
        "SHIPPING_ISSUE",
        "HEALTH_SAFETY",
        "RITUAL_GUIDANCE",
        "INTERNATIONAL",
        "PAYMENT_EDGECASE",
        "CHECKOUT_VERIFY_FAILED",
        "EXPLICIT_REQUEST",
        "CATALOG_GAP",
        # Sesión c4e3416f — agregada para el caso "cliente confirmó pero no
        # completó datos de envío" (LLM lo combina con tag CONFIRMADO_SIN_DATOS).
        "ORDER_PENDING_SHIPPING_DETAILS",
        "OTHER",
    ],
)
def test_escalation_tool_accepts_each_valid_category(category: str, tmp_path: Path) -> None:
    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)
    err = tool.validate_params(
        {"reason_category": category, "summary": "valid summary"}
    )
    # validate_params devuelve [] o None para "sin errores" segun version.
    assert not err
