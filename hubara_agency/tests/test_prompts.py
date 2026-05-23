"""Tests unitarios de los policies de prompts (puros, sin Temporal)."""
from __future__ import annotations

from src.plugins.chats.agent.remarketing.prompts import build_remarketing_trigger
from src.plugins.chats.agent.sales.prompts import build_ghosting_prompt


def test_ghosting_prompt_mentions_required_tool() -> None:
    out = build_ghosting_prompt()
    assert "manage_conversation_tag" in out
    assert "INTERESADO" in out
    assert "RECHAZO" in out


def test_ghosting_prompt_is_stable() -> None:
    # Determinismo: dos llamadas devuelven exactamente el mismo string.
    assert build_ghosting_prompt() == build_ghosting_prompt()


def test_ghosting_prompt_defaults_to_interesado_when_in_doubt() -> None:
    """Post-fix #5: INTERESADO es el default cuando hay duda (evita perder leads).

    Bug original (wa_573125671604): el LLM etiqueto RECHAZO cuando el cliente
    habia mostrado interes claro (pidio ver catalogo, eligio 'Corona de
    Redencion') pero dejo de responder 1 min. Debe ser INTERESADO (programa
    remarketing). Verifica que el prompt favorece explicitamente INTERESADO.
    """
    out = build_ghosting_prompt()
    assert "INTERESADO" in out
    assert "DEFAULT" in out  # explicito en el prompt
    assert "remarketing" in out.lower()
    # El criterio de RECHAZO debe estar acotado a casos obvios.
    assert "no me interesa" in out.lower() or "obvio" in out.lower()


def test_ghosting_prompt_handles_confirmed_without_shipping_case() -> None:
    """Sesión c4e3416f: el prompt debe instruir al LLM a detectar el caso
    'cliente confirmó pedido pero NO completó datos de envío' y combinarlo
    con `escalate_to_human(reason_category="ORDER_PENDING_SHIPPING_DETAILS")`.

    Sin esta detección, sales caería a INTERESADO genérico y arrancaría
    remarketing — perdiendo el lead que estaba a un paso de cerrar."""
    out = build_ghosting_prompt()
    # La tag nueva debe estar mencionada explícitamente
    assert "CONFIRMADO_SIN_DATOS" in out
    # La razón de escalation correspondiente también
    assert "ORDER_PENDING_SHIPPING_DETAILS" in out
    # Y debe explicitar que en este caso van DOS tools (tag + escalate)
    assert "escalate_to_human" in out
    # Y que NO se debe usar INTERESADO para este caso (anti-confusión)
    assert (
        "NO uses INTERESADO" in out
        or "no es remarketing genérico" in out.lower()
    )


def test_ghosting_prompt_mentions_register_order_for_compra_exitosa() -> None:
    """COMPRA_EXITOSA debe estar atada a `register_order` para que el LLM
    no marque la venta como exitosa sin haber registrado el pedido."""
    out = build_ghosting_prompt()
    assert "register_order" in out


def test_remarketing_trigger_includes_motivo_and_memory() -> None:
    out = build_remarketing_trigger("cliente pidió tiempo", " >>memoria<<")
    assert "cliente pidió tiempo" in out
    assert ">>memoria<<" in out


def test_remarketing_trigger_works_with_empty_memory() -> None:
    out = build_remarketing_trigger("ghosting total", "")
    assert "ghosting total" in out
    # Empty memory aún produce una salida valida.
    assert len(out) > 100


def test_remarketing_trigger_does_not_force_envio_gratis() -> None:
    """Post-fix #G (bug b2fb9379): el trigger ya NO obliga al LLM a ofrecer
    envio gratis. Esa decision la toma el LLM segun AGENTS.md y el motivo.

    Si el trigger mencionara 'ofreciéndole envío gratis' como antes, el LLM
    quedaba atrapado entre dos instrucciones contradictorias y filtraba su
    razonamiento al cliente. El nuevo trigger DELEGA a AGENTS.md.
    """
    out = build_remarketing_trigger("dejó en visto", "")
    # NO debe contener una orden directa de ofrecer envio gratis.
    assert "ofreciéndole envío gratis" not in out
    assert "ofrécele envío gratis" not in out
    # SI debe referenciar AGENTS.md como fuente de la regla.
    assert "AGENTS.md" in out


def test_remarketing_trigger_has_anti_leak_rule() -> None:
    """Post-fix #G: el trigger ordena explicitamente NO filtrar razonamiento.

    El LLM debe escribir SOLO el mensaje final al cliente, sin pre-ambulo
    interno tipo 'Cliente preguntó por X... Según AGENTS.md...'.
    """
    out = build_remarketing_trigger("dejó en visto", "")
    # Frase clave que prohibe filtrar razonamiento.
    assert "NO expliques tu razonamiento" in out
    assert "NO menciones archivos internos" in out
    assert "tercera persona" in out.lower()
