"""Los envíos no-textuales del bot deben quedar VISIBLES en el histórico.

HU (2026-07-07): el dashboard lee el JSONL del session_history para mostrar
la conversación. `flush_pending_ui_intents_activity` envía catálogo, flows,
botones, galerías, etc. directo a Meta SIN dejar rastro — el operador ve
huecos en la conversación y no puede seguirla (mismo problema que ya
resolvieron los templates con `_append_template_to_session_history`).

Contrato nuevo:
  * Envío exitoso de un intent → se appendea al JSONL un marker
    `{"role": "assistant", "kind": "ui_component", "component_kind": <kind>,
     "content": "<descripción human-readable>", "timestamp": <ISO>}`.
  * `variant_picker` es la excepción: envía TEXTO real al cliente, así que
    se persiste como assistant message normal (sin `kind`) con el texto
    renderizado — el operador ve exactamente lo que vio el cliente.
  * Envío fallido → NO se appendea nada (no mentirle al operador).
  * El append es best-effort: un fallo de I/O no tumba el flush.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
    _build_history_event,
    flush_pending_ui_intents_activity,
)

_SESSION_ID = "wa_573000000000"


def _seed_metadata(vault, intents: list[dict]) -> None:
    session_dir = vault / _SESSION_ID
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "metadata.json").write_text(
        json.dumps(
            {
                "phone_number_id": "phone-1",
                "pending_ui_intents": intents,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _read_history(vault) -> list[dict]:
    path = vault / _SESSION_ID / "sessions" / f"{_SESSION_ID}.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _ok_result() -> SimpleNamespace:
    return SimpleNamespace(ok=True, wa_message_id="wamid.test.1", error=None)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Vault aislado + wa_client mockeado (todos los send_* exitosos)."""
    monkeypatch.setattr("src.platform.config.WORKSPACE_VAULT_DIR", tmp_path)
    import src.platform.whatsapp.client as wa_client

    for fn in (
        "send_image",
        "send_text",
        "send_interactive_list",
        "send_interactive_buttons",
        "send_product_list",
        "send_flow",
        "send_reaction",
        "send_contact",
        "send_cta_url",
    ):
        monkeypatch.setattr(wa_client, fn, AsyncMock(return_value=_ok_result()))
    return tmp_path


@pytest.mark.asyncio
async def test_quick_replies_success_appends_ui_component_note(vault):
    """El caso más doloroso: el saludo con botones (`send_quick_replies`)
    incluye el body que el cliente SÍ leyó — debe quedar en el histórico."""
    _seed_metadata(
        vault,
        [
            {
                "id": "i-1",
                "kind": "quick_replies",
                "params": {
                    "body": "Buenos días, bienvenido a Hubara 🤍",
                    "buttons": [
                        {"id": "b1", "title": "Ver catálogo"},
                        {"id": "b2", "title": "Hablar con asesor"},
                    ],
                },
            }
        ],
    )

    env = ActivityEnvironment()
    sent = await env.run(flush_pending_ui_intents_activity, _SESSION_ID)
    assert sent == 1

    events = _read_history(vault)
    assert len(events) == 1
    ev = events[0]
    assert ev["role"] == "assistant"
    assert ev["kind"] == "ui_component"
    assert ev["component_kind"] == "quick_replies"
    assert "timestamp" in ev
    # El content debe permitir SEGUIR la conversación: body + botones.
    assert "Buenos días, bienvenido a Hubara" in ev["content"]
    assert "Ver catálogo" in ev["content"]
    assert "Hablar con asesor" in ev["content"]


@pytest.mark.asyncio
async def test_shipping_flow_appends_form_note(vault, monkeypatch):
    """El formulario de datos de envío (WhatsApp Flow) debe dejar nota."""
    monkeypatch.delenv("META_FLOW_ID_SHIPPING", raising=False)
    _seed_metadata(
        vault,
        [
            {
                "id": "i-2",
                "kind": "shipping_flow",
                "params": {
                    "flow_id": "FLOW_ID_SHIPPING_PLACEHOLDER",
                    "order_total_cop": 17000,
                },
            }
        ],
    )

    env = ActivityEnvironment()
    sent = await env.run(flush_pending_ui_intents_activity, _SESSION_ID)
    assert sent == 1

    events = _read_history(vault)
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "ui_component"
    assert ev["component_kind"] == "shipping_flow"
    assert "datos de envío" in ev["content"]


@pytest.mark.asyncio
async def test_variant_picker_persists_rendered_text_as_plain_message(vault):
    """`variant_picker` manda TEXTO real (y el workflow suprime el
    final_content del LLM) — el histórico debe mostrar ESE texto como
    mensaje normal del bot, no una nota genérica."""
    _seed_metadata(
        vault,
        [
            {
                "id": "i-3",
                "kind": "variant_picker",
                "params": {
                    "variant_type": "scent",
                    "intro_text": "Tenemos estos aromas:",
                    "sections": [
                        {
                            "title": "Frescos",
                            "rows": [
                                {"id": "scent.lavanda", "title": "💜 Lavanda"},
                                {"id": "scent.menta", "title": "🌿 Menta"},
                            ],
                        }
                    ],
                },
            }
        ],
    )

    env = ActivityEnvironment()
    sent = await env.run(flush_pending_ui_intents_activity, _SESSION_ID)
    assert sent == 1

    events = _read_history(vault)
    assert len(events) == 1
    ev = events[0]
    assert ev["role"] == "assistant"
    # SIN kind → el dashboard lo clasifica agent_message (burbuja normal).
    assert "kind" not in ev
    assert "Tenemos estos aromas:" in ev["content"]
    assert "💜 Lavanda" in ev["content"]
    assert "🌿 Menta" in ev["content"]


@pytest.mark.asyncio
async def test_failed_send_appends_no_history_event(vault, monkeypatch):
    """Si Meta rechaza el envío, NO se persiste marker — el histórico no
    puede afirmar que el cliente recibió algo que no recibió."""
    import src.platform.whatsapp.client as wa_client

    monkeypatch.setattr(
        wa_client,
        "send_interactive_buttons",
        AsyncMock(
            return_value=SimpleNamespace(
                ok=False, wa_message_id=None, error="http_400"
            )
        ),
    )
    _seed_metadata(
        vault,
        [
            {
                "id": "i-4",
                "kind": "quick_replies",
                "params": {
                    "body": "hola",
                    "buttons": [{"id": "b1", "title": "Sí"}],
                },
            }
        ],
    )

    env = ActivityEnvironment()
    sent = await env.run(flush_pending_ui_intents_activity, _SESSION_ID)
    assert sent == 0
    assert _read_history(vault) == []


@pytest.mark.asyncio
async def test_multiple_intents_append_in_send_order(vault):
    """Un turno con foto + botones deja DOS markers, en el orden de envío."""
    _seed_metadata(
        vault,
        [
            {
                "id": "i-5",
                "kind": "product_detail",
                "params": {
                    "image_url": "https://cdn/vela.jpg",
                    "caption": "Vela Luz Serena",
                },
            },
            {
                "id": "i-6",
                "kind": "quick_replies",
                "params": {
                    "body": "¿Te gustó?",
                    "buttons": [{"id": "b1", "title": "Sí"}],
                },
            },
        ],
    )

    env = ActivityEnvironment()
    sent = await env.run(flush_pending_ui_intents_activity, _SESSION_ID)
    assert sent == 2

    events = _read_history(vault)
    assert [e["component_kind"] for e in events] == [
        "product_detail",
        "quick_replies",
    ]
    assert "Vela Luz Serena" in events[0]["content"]


# ── Unit: descripciones por kind (sin activity, sin I/O) ────────────────


def test_build_history_event_product_detail_mentions_photo():
    ev = _build_history_event(
        "product_detail",
        {"image_url": "https://cdn/x.jpg", "caption": "Vela Cruz de Vida"},
    )
    assert ev is not None
    assert ev["component_kind"] == "product_detail"
    assert "📷" in ev["content"]
    assert "Vela Cruz de Vida" in ev["content"]


def test_build_history_event_products_list_counts_items():
    ev = _build_history_event(
        "products_list",
        {
            "sections": [
                {"title": "A", "rows": [{"id": "1", "title": "P1"}]},
                {
                    "title": "B",
                    "rows": [
                        {"id": "2", "title": "P2"},
                        {"id": "3", "title": "P3"},
                    ],
                },
            ]
        },
    )
    assert ev is not None
    assert "3" in ev["content"]
    assert "catálogo" in ev["content"] or "productos" in ev["content"]


def test_build_history_event_product_gallery_counts_photos():
    ev = _build_history_event(
        "product_gallery",
        {"image_urls": ["u1", "u2", "u3"], "lead_caption": "Vela artesanal"},
    )
    assert ev is not None
    assert "3" in ev["content"]
    assert "Vela artesanal" in ev["content"]


def test_build_history_event_order_confirmation_mentions_summary():
    ev = _build_history_event(
        "order_confirmation",
        {"total_cop": 45000, "items": [], "reference_id": "HUB-1"},
    )
    assert ev is not None
    assert "pedido" in ev["content"].lower()


def test_build_history_event_reaction_includes_emoji():
    ev = _build_history_event("reaction", {"emoji": "🤍"})
    assert ev is not None
    assert "🤍" in ev["content"]


def test_build_history_event_cta_url_includes_button_and_url():
    ev = _build_history_event(
        "cta_url",
        {
            "url": "https://pago.hubara.co/x",
            "button_text": "Pagar ahora",
            "body_text": "Aquí puedes pagar",
        },
    )
    assert ev is not None
    assert "Pagar ahora" in ev["content"]
    assert "https://pago.hubara.co/x" in ev["content"]


def test_build_history_event_contact_card_mentions_advisor():
    ev = _build_history_event("contact_card", {})
    assert ev is not None
    assert "contacto" in ev["content"].lower()


def test_build_history_event_unknown_kind_gets_generic_note():
    """Kind futuro sin descripción específica → nota genérica, nunca None
    (si se envió con éxito, el operador merece saber que ALGO se envió)."""
    ev = _build_history_event("hologram_projection", {})
    assert ev is not None
    assert ev["component_kind"] == "hologram_projection"
    assert ev["content"]


def test_build_history_event_truncates_long_bodies():
    ev = _build_history_event(
        "quick_replies",
        {"body": "x" * 500, "buttons": [{"id": "b", "title": "Ok"}]},
    )
    assert ev is not None
    assert len(ev["content"]) < 400
