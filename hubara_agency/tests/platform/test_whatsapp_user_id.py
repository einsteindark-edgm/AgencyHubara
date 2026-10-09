"""El id de Meta de un cliente con nombre de usuario (BSUID, `CO.9990000000000002`)
como DIRECCIÓN de conversación: `CO9990000000000002` (sin el punto, para que
`wa_<dirección>` siga siendo un directorio seguro del vault), y de vuelta al
campo `recipient` cuando le escribimos.
"""
from __future__ import annotations

import pytest

from src.platform.whatsapp.user_id import (
    address_from_user_id,
    is_customer_session_id,
    meta_recipient,
)


def test_a_meta_user_id_becomes_an_address_without_the_dot() -> None:
    assert address_from_user_id("CO.9990000000000002") == "CO9990000000000002"
    assert address_from_user_id("US.aB3x") == "USaB3x"


@pytest.mark.parametrize(
    "user_id",
    [None, 15, "", "CO.", "CO1502", "co.1502", "C.1502", "COL.1502", "CO.15/02", "CO.15.02", "CO.ENT.1502", "CO.1502\n", "CO." + "1" * 129],
)
def test_anything_else_is_not_a_meta_user_id(user_id) -> None:
    assert address_from_user_id(user_id) is None


def test_a_phone_is_sent_with_to() -> None:
    assert meta_recipient("573001234567") == {"to": "573001234567"}


def test_a_meta_user_id_address_is_sent_with_recipient() -> None:
    assert meta_recipient("CO9990000000000002") == {"recipient": "CO.9990000000000002"}


def test_the_address_round_trips_at_the_longest_meta_user_id() -> None:
    longest = "CO." + "a" * 128
    assert meta_recipient(address_from_user_id(longest)) == {"recipient": longest}


@pytest.mark.parametrize("session_id", ["wa_573001234567", "wa_CO9990000000000002"])
def test_both_kinds_of_customer_are_customer_sessions(session_id: str) -> None:
    assert is_customer_session_id(session_id) is True


@pytest.mark.parametrize(
    "session_id",
    ["wa_123", "573001234567", "wa_CO", "wa_co1502", "wa_CO.1502", "wa_CO1502_x", "wa_57300123456\n", "wa_test_enum"],
)
def test_other_shapes_are_not_customer_sessions(session_id: str) -> None:
    assert is_customer_session_id(session_id) is False


def test_a_customer_without_phone_gets_the_timezone_of_their_country() -> None:
    """Sin teléfono no hay prefijo `57`: el país va en las 2 letras del id de
    Meta. Sin esto las horas de silencio del remarketing corrían en UTC
    (5 h adelantadas para Colombia: un toque a las 4 a. m. pasaba)."""
    from zoneinfo import ZoneInfo

    from src.platform.whatsapp.quiet_hours import resolve_local_timezone

    assert resolve_local_timezone("wa_CO9990000000000002") == ZoneInfo("America/Bogota")
    assert resolve_local_timezone("wa_MX9990000000000002") == ZoneInfo("America/Mexico_City")
    assert resolve_local_timezone("wa_ZZ9990000000000002") == ZoneInfo("UTC")
