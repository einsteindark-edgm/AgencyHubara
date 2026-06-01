"""Tests para `_dispatch_intent` de `flush_ui_intents.py`.

Anti-regresión específica: sesión 691de3ea (2026-05-22) — agregamos la
rama MPM (`interactive.product_list`) pero adentro de `_dispatch_intent`
usábamos `intent.get(...)` y `intent` NO era parámetro de esa función
(NameError silencioso → `dispatch_failed`, cliente no recibe nada).

Cubre los 4 caminos del intent `products_list`:
  1. MPM: env META_CATALOG_ID set + flag prefer_native_product_list True
     + todas las rows tienen `product_retailer_id` → `send_product_list`.
  2. Fallback por falta de env: sin META_CATALOG_ID → `send_interactive_list`.
  3. Fallback por falta de flag: con env pero sin flag → `send_interactive_list`.
  4. Fallback por row incompleta: una row sin `product_retailer_id` →
     `send_interactive_list`.

Cubre también la regresión del bug:
  5. `_dispatch_intent` se puede llamar sin que el caller sepa de `intent` —
     debe aceptar `fallback={}` y NO crashear con NameError.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
    _dispatch_intent,
)
from src.platform.whatsapp import dtos as wa_dtos


def _make_wa_client_mock() -> SimpleNamespace:
    return SimpleNamespace(
        send_product_list=AsyncMock(return_value=SimpleNamespace(ok=True)),
        send_interactive_list=AsyncMock(return_value=SimpleNamespace(ok=True)),
        send_text=AsyncMock(return_value=SimpleNamespace(ok=True)),
        send_interactive_buttons=AsyncMock(
            return_value=SimpleNamespace(ok=True)
        ),
        send_location_request=AsyncMock(
            return_value=SimpleNamespace(ok=True)
        ),
        send_flow=AsyncMock(return_value=SimpleNamespace(ok=True)),
    )


_BASE_PARAMS = {
    "intro_text": "Mirá nuestras velas:",
    "header_text": "Velas",
    "sections": [
        {
            "title": "Religiosas",
            "rows": [
                {
                    "id": "luz-serena",
                    "title": "Luz Serena",
                    "description": "$23.000 COP",
                    "product_retailer_id": "1yxc1gm6cn",
                },
                {
                    "id": "cruz-de-vida",
                    "title": "Cruz de Vida",
                    "description": "$17.000 COP",
                    "product_retailer_id": "kpirhs1jrp",
                },
            ],
        }
    ],
}


@pytest.mark.asyncio
async def test_products_list_uses_mpm_when_env_flag_and_retailer_ids_present(
    monkeypatch,
):
    monkeypatch.setenv("META_CATALOG_ID", "1468134707823015")
    wa_client = _make_wa_client_mock()

    result = await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="products_list",
        params=dict(_BASE_PARAMS),
        fallback={"prefer_native_product_list": True},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )

    assert result is not None and result.ok is True
    wa_client.send_product_list.assert_awaited_once()
    wa_client.send_interactive_list.assert_not_awaited()

    # Validar la shape del payload MPM
    call = wa_client.send_product_list.call_args
    payload = call.args[2]
    assert isinstance(payload, wa_dtos.InteractiveProductListOutbound)
    assert payload.catalog_id == "1468134707823015"
    assert payload.body == "Mirá nuestras velas:"
    assert len(payload.sections) == 1
    sec = payload.sections[0]
    assert len(sec.product_items) == 2
    retailer_ids = {pi.product_retailer_id for pi in sec.product_items}
    assert retailer_ids == {"1yxc1gm6cn", "kpirhs1jrp"}


@pytest.mark.asyncio
async def test_products_list_falls_back_when_meta_catalog_id_missing(
    monkeypatch,
):
    monkeypatch.delenv("META_CATALOG_ID", raising=False)
    wa_client = _make_wa_client_mock()

    await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="products_list",
        params=dict(_BASE_PARAMS),
        fallback={"prefer_native_product_list": True},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )

    wa_client.send_product_list.assert_not_awaited()
    wa_client.send_interactive_list.assert_awaited_once()


@pytest.mark.asyncio
async def test_products_list_falls_back_without_prefer_native_flag(
    monkeypatch,
):
    monkeypatch.setenv("META_CATALOG_ID", "1468134707823015")
    wa_client = _make_wa_client_mock()

    await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="products_list",
        params=dict(_BASE_PARAMS),
        fallback={},  # ← sin flag
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )

    wa_client.send_product_list.assert_not_awaited()
    wa_client.send_interactive_list.assert_awaited_once()


@pytest.mark.asyncio
async def test_products_list_falls_back_when_any_row_missing_retailer_id(
    monkeypatch,
):
    """Si alguna row no trae `product_retailer_id`, no podemos usar MPM —
    Meta rechazaría el mensaje. Hacemos fallback completo a interactive.list."""
    monkeypatch.setenv("META_CATALOG_ID", "1468134707823015")
    wa_client = _make_wa_client_mock()

    params_partial = {
        "intro_text": "Mirá:",
        "sections": [
            {
                "title": "Mezcla",
                "rows": [
                    {"id": "a", "title": "A", "product_retailer_id": "ra"},
                    {"id": "b", "title": "B"},  # ← falta retailer_id
                ],
            }
        ],
    }

    await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="products_list",
        params=params_partial,
        fallback={"prefer_native_product_list": True},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )

    wa_client.send_product_list.assert_not_awaited()
    wa_client.send_interactive_list.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_intent_does_not_reference_intent_in_scope(
    monkeypatch,
):
    """Regression sesión 691de3ea: `_dispatch_intent` no debe leer `intent`
    (variable de scope superior). Si la función crashea con NameError
    cuando `fallback={}`, este test falla."""
    monkeypatch.delenv("META_CATALOG_ID", raising=False)
    wa_client = _make_wa_client_mock()

    # Debe completar sin raisear NameError aunque fallback sea vacío y
    # algunas vars NO sean accesibles desde el scope del caller.
    result = await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="products_list",
        params=dict(_BASE_PARAMS),
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )
    assert result is not None


# =============================================================================
# Variant picker — sesión adc6400c: ahora renderiza como TEXTO con emojis,
# NO como interactive.list. El cliente responde libremente por chat.
# =============================================================================


_VARIANT_SCENT_PARAMS = {
    "variant_type": "scent",
    "intro_text": "Tenemos estos aromas:",
    "sections": [
        {
            "title": "Frescos",
            "rows": [
                {"id": "scent.lavanda", "title": "💜 Lavanda"},
                {"id": "scent.verde_menta", "title": "🌿 Verde menta"},
            ],
        },
        {
            "title": "Cálidos y dulces",
            "rows": [
                {"id": "scent.cafe", "title": "☕ Café"},
                {"id": "scent.sandalo", "title": "🪵 Sándalo"},
            ],
        },
    ],
}


@pytest.mark.asyncio
async def test_variant_picker_renders_as_plain_text_with_emojis():
    """ANTI-REGRESIÓN sesión adc6400c: el dispatch de `variant_picker`
    NO debe usar `send_interactive_list`. Debe enviar TEXTO formateado
    con emojis ya curados en row.title."""
    wa_client = _make_wa_client_mock()

    result = await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="variant_picker",
        params=dict(_VARIANT_SCENT_PARAMS),
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )

    assert result is not None and result.ok is True
    wa_client.send_interactive_list.assert_not_awaited()
    wa_client.send_text.assert_awaited_once()

    # Validar el shape del texto enviado
    call = wa_client.send_text.call_args
    text = call.args[2]
    assert "Tenemos estos aromas:" in text
    # Section headers en *bold*
    assert "*Frescos*" in text
    assert "*Cálidos y dulces*" in text
    # Cada row preserva el emoji curado
    assert "💜 Lavanda" in text
    assert "🌿 Verde menta" in text
    assert "☕ Café" in text
    assert "🪵 Sándalo" in text
    # Cierre invitando a responder — DEBE ser tuteo colombiano (REGLA #1 de
    # IDENTITY.md), NUNCA voseo rioplatense (bug run fe86d4e4: el tail
    # hardcodeado decía "Decime" aunque el LLM sí respetaba el tuteo).
    assert "Dime cuál te gusta" in text
    # Anti-regresión voseo: ninguna forma rioplatense puede colarse en el cierre.
    for voseo in ("Decime", "Contame", "preferís", "querés", "elegí", "mirá"):
        assert voseo not in text, f"voseo '{voseo}' filtrado en el cierre: {text!r}"


@pytest.mark.asyncio
async def test_variant_picker_color_uses_color_tail():
    """El cierre cambia según variant_type — color tiene su propio mensaje."""
    wa_client = _make_wa_client_mock()
    params = {
        "variant_type": "color",
        "intro_text": "Estos son los colores:",
        "sections": [
            {
                "title": "Claros y suaves",
                "rows": [
                    {"id": "color.blanco", "title": "🤍 Blanco"},
                    {"id": "color.rosado", "title": "🌸 Rosado"},
                ],
            }
        ],
    }

    await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="variant_picker",
        params=params,
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )

    wa_client.send_interactive_list.assert_not_awaited()
    text = wa_client.send_text.call_args.args[2]
    assert "qué color" in text.lower() or "color" in text.lower()


@pytest.mark.asyncio
async def test_variant_picker_empty_sections_returns_none():
    """Si no llegan sections (defensa), no se envía nada y devuelve None
    (kind unknown / sin contenido)."""
    wa_client = _make_wa_client_mock()

    result = await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="variant_picker",
        params={"variant_type": "scent", "sections": []},
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )

    assert result is None
    wa_client.send_text.assert_not_awaited()
    wa_client.send_interactive_list.assert_not_awaited()


# =============================================================================
# Shipping flow — sesión adc6400c: placeholder flow_id ahora renderiza
# como TEXTO plano enumerando los campos, NO como buttons con
# "Escribirlos / Compartir ubicación".
# =============================================================================


@pytest.mark.asyncio
async def test_shipping_flow_placeholder_sends_plain_text_not_buttons(
    monkeypatch,
):
    """ANTI-REGRESIÓN sesión adc6400c: cuando flow_id es el placeholder,
    NO se mandan botones (especialmente NO 'Compartir ubicación'), se
    manda un mensaje de texto enumerando los campos."""
    monkeypatch.delenv("META_FLOW_ID_SHIPPING", raising=False)
    wa_client = _make_wa_client_mock()

    result = await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="shipping_flow",
        params={
            "flow_id": "FLOW_ID_SHIPPING_PLACEHOLDER",
            "order_total_cop": 17000,
        },
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )

    assert result is not None and result.ok is True
    wa_client.send_interactive_buttons.assert_not_awaited()
    wa_client.send_text.assert_awaited_once()

    text = wa_client.send_text.call_args.args[2]
    # Cinco campos canónicos
    assert "Ciudad" in text
    assert "Barrio" in text
    assert "Dirección" in text
    assert "Teléfono" in text
    assert "Método de pago" in text
    # NO mencionar opción de compartir ubicación
    assert "ubicación" not in text.lower()
    assert "compartir" not in text.lower()
    # Total < 45000 → contra entrega NO debe aparecer
    assert "contra entrega" not in text.lower()


@pytest.mark.asyncio
async def test_shipping_flow_placeholder_offers_cod_when_total_over_45k(
    monkeypatch,
):
    """Si el pedido > $45.000 COP, el texto debe mencionar contra entrega
    como método de pago disponible."""
    monkeypatch.delenv("META_FLOW_ID_SHIPPING", raising=False)
    wa_client = _make_wa_client_mock()

    await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="shipping_flow",
        params={
            "flow_id": "FLOW_ID_SHIPPING_PLACEHOLDER",
            "order_total_cop": 90000,
        },
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )

    text = wa_client.send_text.call_args.args[2]
    assert "contra entrega" in text.lower()


@pytest.mark.asyncio
async def test_shipping_flow_with_real_flow_id_calls_send_flow(monkeypatch):
    """Cuando se configure un Flow real (no placeholder), debe ser el path
    nativo Meta — `send_flow`. El comportamiento futuro queda preservado."""
    monkeypatch.delenv("META_FLOW_ID_SHIPPING", raising=False)
    wa_client = _make_wa_client_mock()

    await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="shipping_flow",
        params={
            "flow_id": "1234567890",
            "flow_token": "tk",
            "flow_cta": "Completar",
            "flow_action": "navigate",
            "flow_action_screen": "SCREEN",
            "flow_action_data": {},
            "body": "body",
            "header_text": "header",
        },
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )

    wa_client.send_flow.assert_awaited_once()
    wa_client.send_text.assert_not_awaited()
    wa_client.send_interactive_buttons.assert_not_awaited()


@pytest.mark.asyncio
async def test_shipping_flow_env_var_overrides_placeholder_in_params(
    monkeypatch,
):
    """Cuando `META_FLOW_ID_SHIPPING` está set en env, debe pisar el
    placeholder que viene en `params.flow_id` (el patrón es: la tool SIEMPRE
    encola el placeholder; el dispatcher resuelve via env productivo)."""
    monkeypatch.setenv("META_FLOW_ID_SHIPPING", "9988776655443322")
    wa_client = _make_wa_client_mock()

    await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="shipping_flow",
        params={
            "flow_id": "FLOW_ID_SHIPPING_PLACEHOLDER",
            "flow_token": "tk-xyz",
            "flow_cta": "Completar datos",
            "flow_action": "navigate",
            "flow_action_screen": "SHIPPING_DETAILS",
            "flow_action_data": {"order_total_cop": 17000},
            "body": "Para completar tu pedido…",
            "header_text": "Datos de envío",
            "order_total_cop": 17000,
        },
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )

    # Debe haber abierto el Flow nativo, NO el fallback de texto.
    wa_client.send_flow.assert_awaited_once()
    wa_client.send_text.assert_not_awaited()

    # Y el flow_id efectivo es el del env, NO el placeholder
    sent_payload = wa_client.send_flow.call_args.args[2]
    assert sent_payload.flow_id == "9988776655443322"


@pytest.mark.asyncio
async def test_shipping_flow_env_var_placeholder_falls_back_to_text(
    monkeypatch,
):
    """Defensiva: si el operador setea accidentalmente la env var con el
    string literal del placeholder, el dispatcher NO debe intentar mandar
    al Flow — debe caer al fallback de texto."""
    monkeypatch.setenv(
        "META_FLOW_ID_SHIPPING", "FLOW_ID_SHIPPING_PLACEHOLDER"
    )
    wa_client = _make_wa_client_mock()

    await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="shipping_flow",
        params={
            "flow_id": "FLOW_ID_SHIPPING_PLACEHOLDER",
            "order_total_cop": 17000,
        },
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )

    wa_client.send_flow.assert_not_awaited()
    wa_client.send_text.assert_awaited_once()


# =============================================================================
# Location request — sesión adc6400c: kind eliminado del dispatcher.
# Cualquier intent residual con kind=location_request debe ser ignorado
# (return None — no envío, no crash).
# =============================================================================


@pytest.mark.asyncio
async def test_location_request_kind_is_no_longer_dispatched():
    """ANTI-REGRESIÓN sesión adc6400c: el tool `request_location` fue
    removido. Si por alguna razón queda un intent encolado con
    kind='location_request' (cola vieja, migración), el dispatcher debe
    devolver None silenciosamente — NO debe llamar a send_location_request."""
    wa_client = _make_wa_client_mock()

    result = await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="location_request",
        params={"body": "Compartí tu ubicación"},
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )

    assert result is None
    wa_client.send_location_request.assert_not_awaited()


# =============================================================================
# Shipping Flow — defensa en profundidad (post-mortem run bc54cb93, 2026-05-25)
# Si el send_flow nativo falla (Meta 4xx, transport error, NameError local),
# el dispatcher debe caer al texto plano. Antes se quedaba en `dispatch_failed`
# y el cliente NO recibía nada.
# =============================================================================


@pytest.mark.asyncio
async def test_shipping_flow_falls_back_to_text_when_send_flow_returns_not_ok(
    monkeypatch, tmp_path,
):
    """Si Meta rechaza el Flow nativo (4xx → OutboundResult.ok=False), el
    dispatcher debe ENVIAR texto plano al cliente — nunca quedarse en silencio."""
    monkeypatch.setenv("META_FLOW_ID_SHIPPING", "1234567890")
    # Aislar vault para que _mark_flow_awaiting_reply no toque archivos reales.
    monkeypatch.setattr(
        "src.platform.config.WORKSPACE_VAULT_DIR", tmp_path,
    )

    wa_client = _make_wa_client_mock()
    # Meta rechaza con HTTP 400
    wa_client.send_flow = AsyncMock(
        return_value=SimpleNamespace(ok=False, error="http_400: flow not found")
    )

    result = await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="shipping_flow",
        params={
            "flow_id": "FLOW_ID_SHIPPING_PLACEHOLDER",
            "flow_token": "tk",
            "flow_cta": "Completar",
            "flow_action": "navigate",
            "flow_action_screen": "SHIPPING_DETAILS",
            "flow_action_data": {},
            "body": "body",
            "order_total_cop": 50000,
        },
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )

    # Meta dijo NO al Flow → cae al texto plano. Cliente recibe la lista
    # de campos por texto, NO silencio.
    wa_client.send_flow.assert_awaited_once()
    wa_client.send_text.assert_awaited_once()
    text_sent = wa_client.send_text.call_args.args[2]
    assert "Ciudad" in text_sent
    assert "Teléfono" in text_sent
    assert "contra entrega" in text_sent.lower()  # > 45k COP
    # Result viene del send_text del fallback (ok=True)
    assert result is not None
    assert result.ok is True


@pytest.mark.asyncio
async def test_mark_flow_awaiting_reply_uses_activity_scheduled_time_for_idempotency(
    monkeypatch, tmp_path,
):
    """Verifica que el timestamp escrito a metadata viene de
    `activity.info().scheduled_time` (estable entre retries) NO de `time.time()`
    (que cambia entre retries). Esto evita que el ghosting window se renueve
    si la activity falla mid-execution y Temporal hace retry."""
    import json
    from datetime import datetime, timezone
    from temporalio.testing import ActivityEnvironment

    from src.plugins.chats.agent.sales.activities import flush_ui_intents as fui

    # Aislar vault para que la helper escriba a tmp_path.
    monkeypatch.setattr(
        "src.platform.config.WORKSPACE_VAULT_DIR", tmp_path,
    )

    # Pre-crear metadata.json vacío para que la helper no haga early-return.
    session_dir = tmp_path / "wa_573000000000"
    session_dir.mkdir(parents=True)
    (session_dir / "metadata.json").write_text("{}", encoding="utf-8")

    # Fijar un scheduled_time conocido (no "ahora") para comprobar que es ESE
    # el que se escribe, no `time.time()` (que sería diferente).
    fixed_dt = datetime(2026, 5, 25, 14, 55, 0, tzinfo=timezone.utc)
    expected_ms = int(fixed_dt.timestamp() * 1000)  # 1779721800000

    env = ActivityEnvironment()
    env.info = ActivityEnvironment.default_info().__class__(
        **{
            **env.info.__dict__,
            "scheduled_time": fixed_dt,
            "current_attempt_scheduled_time": fixed_dt,
        }
    )

    async def _runner() -> None:
        fui._mark_flow_awaiting_reply("573000000000")

    await env.run(_runner)

    # Verificar el timestamp escrito coincide con scheduled_time (no time.time())
    data = json.loads(
        (session_dir / "metadata.json").read_text(encoding="utf-8")
    )
    assert data["shipping_flow_awaiting_reply_since_ms"] == expected_ms


def test_mark_flow_awaiting_reply_falls_back_to_walltime_outside_activity(
    monkeypatch, tmp_path,
):
    """Defensive: si la helper se invoca fuera de activity context (no debería
    pasar hoy pero podría en futuras refactor), cae a `time.time()` y NO
    crashea."""
    import json
    import time

    from src.plugins.chats.agent.sales.activities import flush_ui_intents as fui

    monkeypatch.setattr(
        "src.platform.config.WORKSPACE_VAULT_DIR", tmp_path,
    )
    session_dir = tmp_path / "wa_573000000001"
    session_dir.mkdir(parents=True)
    (session_dir / "metadata.json").write_text("{}", encoding="utf-8")

    # Llamamos la helper DIRECTAMENTE — sin ActivityEnvironment.
    # `activity.info()` raisearía RuntimeError → la helper cae al fallback.
    before_ms = int(time.time() * 1000)
    fui._mark_flow_awaiting_reply("573000000001")
    after_ms = int(time.time() * 1000)

    data = json.loads(
        (session_dir / "metadata.json").read_text(encoding="utf-8")
    )
    ts = data["shipping_flow_awaiting_reply_since_ms"]
    # Debe ser cercano al wall-clock — entre el before y after de la llamada.
    assert before_ms <= ts <= after_ms


@pytest.mark.asyncio
async def test_shipping_flow_falls_back_to_text_when_send_flow_raises_exception(
    monkeypatch, tmp_path,
):
    """Si `send_flow` lanza una excepción (NameError, transport, build error),
    el dispatcher la captura y manda texto plano. Esto previene el bug que
    detectamos en run bc54cb93 (NameError 'time' lanzado en
    _mark_flow_awaiting_reply rompió toda la rama Flow)."""
    monkeypatch.setenv("META_FLOW_ID_SHIPPING", "1234567890")
    monkeypatch.setattr(
        "src.platform.config.WORKSPACE_VAULT_DIR", tmp_path,
    )

    wa_client = _make_wa_client_mock()
    wa_client.send_flow = AsyncMock(
        side_effect=RuntimeError("Simulated transport explosion")
    )

    result = await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="shipping_flow",
        params={
            "flow_id": "FLOW_ID_SHIPPING_PLACEHOLDER",
            "flow_token": "tk",
            "flow_cta": "Completar",
            "flow_action": "navigate",
            "flow_action_screen": "SHIPPING_DETAILS",
            "flow_action_data": {},
            "body": "body",
            "order_total_cop": 17000,  # < 45k — NO debe incluir COD
        },
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )

    # Exception en send_flow → caímos al texto fallback. NO dispatch_failed.
    wa_client.send_flow.assert_awaited_once()
    wa_client.send_text.assert_awaited_once()
    text_sent = wa_client.send_text.call_args.args[2]
    assert "Ciudad" in text_sent
    # Bajo $45k: NO debe ofrecer contra entrega
    assert "contra entrega" not in text_sent.lower()
    assert result is not None
    assert result.ok is True
