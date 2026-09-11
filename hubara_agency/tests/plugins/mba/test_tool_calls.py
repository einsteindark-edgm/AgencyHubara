"""Dominio puro: una llamada del connector (query o body) validada contra el
MISMO ``request_definition`` que se registra en Meta (no hay dos contratos)."""

from __future__ import annotations

import pytest

from src.plugins.mba.domain.tool_calls import (
    MAX_STRING_LEN,
    ToolCallError,
    contracts_from_config,
    parse_tool_call,
    session_key_from_phone,
)
from src.plugins.mba.service import load_agent

_PHONE = "+573001234567"


@pytest.fixture(scope="module")
def contracts():
    cfg = load_agent("sales")
    assert cfg is not None
    return contracts_from_config(cfg)


def test_session_key_is_wa_plus_digits_and_only_for_e164_phones() -> None:
    assert session_key_from_phone("+573001234567") == "wa_573001234567"
    assert session_key_from_phone("57 300 123-4567") == "wa_573001234567"
    for bad in ("", "abc", "+1", "1" * 16, "wa_573001234567", None, 573001234567):
        assert session_key_from_phone(bad) is None, bad


def test_contracts_come_from_the_authored_request_definitions(contracts) -> None:
    assert set(contracts) == {
        "search_products",
        "list_categories",
        "get_product_by_handle",
        "check_order_status",
        "set_order_slot",
        "verify_order_for_checkout",
        "register_order",
        "manage_conversation_tag",
        "escalate_to_human",
    }
    sp = contracts["search_products"]
    assert sp.method == "GET" and sp.phone_param == "customer_phone"
    assert sp.params["limit"]["type"] == "integer" and sp.required == ()
    ro = contracts["register_order"]
    assert ro.method == "POST"
    assert set(ro.required) == {
        "items",
        "ciudad",
        "direccion",
        "telefono",
        "nombre_recibe",
        "metodo_pago",
    }
    # el schema de los items viaja a Meta como JSON string; acá vuelve a ser dict
    assert ro.params["items"]["items"]["required"] == ["handle", "quantity"]
    assert (
        "customer_phone" not in ro.params
    )  # el teléfono no es un parámetro del agente


def test_get_call_coerces_query_strings_keeps_empty_q_and_drops_unknown_params(
    contracts,
) -> None:
    call = parse_tool_call(
        contracts["search_products"],
        {"customer_phone": _PHONE, "q": "", "limit": "5", "foo": "bar"},
    )
    assert call.tool == "search_products"
    assert call.session_key == "wa_573001234567"
    assert call.customer_phone == _PHONE
    assert call.params == {"q": "", "limit": 5}


def test_missing_phone_bad_types_and_missing_required_are_reported_together(
    contracts,
) -> None:
    with pytest.raises(ToolCallError) as e:
        parse_tool_call(contracts["search_products"], {"q": "x", "limit": "muchos"})
    joined = " ".join(e.value.errors)
    assert "customer_phone" in joined and "limit" in joined
    with pytest.raises(ToolCallError) as e:
        parse_tool_call(contracts["register_order"], {"customer_phone": _PHONE})
    joined = " ".join(e.value.errors)
    assert "items" in joined and "ciudad" in joined and "metodo_pago" in joined


def test_array_items_are_validated_against_their_object_schema(contracts) -> None:
    c = contracts["verify_order_for_checkout"]
    ok = parse_tool_call(
        c,
        {
            "customer_phone": _PHONE,
            "items": [
                {
                    "handle": "vela",
                    "quantity": 2,
                    "variant_label": "Lavanda, Blanco",
                    "x": 1,
                }
            ],
        },
    )
    assert ok.params["items"] == [
        {"handle": "vela", "quantity": 2, "variant_label": "Lavanda, Blanco"}
    ]
    with pytest.raises(ToolCallError) as e:
        parse_tool_call(c, {"customer_phone": _PHONE, "items": [{"handle": "vela"}]})
    assert "items[0].quantity" in " ".join(e.value.errors)
    with pytest.raises(ToolCallError):
        parse_tool_call(c, {"customer_phone": _PHONE, "items": "vela"})
    with pytest.raises(ToolCallError):  # bool NO es integer
        parse_tool_call(
            c,
            {"customer_phone": _PHONE, "items": [{"handle": "vela", "quantity": True}]},
        )
    with pytest.raises(ToolCallError):
        parse_tool_call(c, {"customer_phone": _PHONE, "items": []})


def test_oversized_strings_are_rejected(contracts) -> None:
    with pytest.raises(ToolCallError) as e:
        parse_tool_call(
            contracts["search_products"],
            {"customer_phone": _PHONE, "q": "x" * (MAX_STRING_LEN + 1)},
        )
    assert "q" in " ".join(e.value.errors)


def test_error_payload_is_what_the_endpoint_returns_as_422(contracts) -> None:
    with pytest.raises(ToolCallError) as e:
        parse_tool_call(contracts["get_product_by_handle"], {"customer_phone": _PHONE})
    assert e.value.payload == {
        "error": "invalid_request",
        "errors": ["handle: requerido"],
    }


def test_phone_digits_are_ascii_only_and_integers_have_a_sane_range(contracts) -> None:
    assert (
        session_key_from_phone("+٥٧٣٠٠١٢٣٤٥٦٧") is None
    )  # dígitos árabes: \d los aceptaría
    c = contracts["verify_order_for_checkout"]
    with pytest.raises(ToolCallError) as e:
        parse_tool_call(
            c,
            {
                "customer_phone": _PHONE,
                "items": [{"handle": "vela", "quantity": 10**30}],
            },
        )
    assert "quantity" in " ".join(e.value.errors)
    with pytest.raises(ToolCallError):
        parse_tool_call(
            contracts["search_products"],
            {"customer_phone": _PHONE, "limit": "99999999999"},
        )
