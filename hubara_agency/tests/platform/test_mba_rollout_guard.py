"""Interruptores del lado de Hubara para Meta Business Agent (D1.4, pedido del
operador: ya estamos en producción; MBA solo puede tocar una lista cerrada).

* ``MBA_STANDBY_ENABLED`` — flag (default OFF): sin ella, el webhook `standby`
  se acepta y se descarta como hasta hoy.
* ``MBA_CUSTOMER_ALLOWLIST`` — lista cerrada de clientes (E.164 o dígitos,
  separados por coma). Vacía o placeholder = NADIE (fail-closed). La consulta
  el oído `standby` (chats) y el connector (mba) vía ``src.sdk.runtime``.
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("raw,expected", [
    ("", frozenset()),
    ("PLACEHOLDER_set_out_of_band", frozenset()),
    ("+573001234567", frozenset({"573001234567"})),
    ("573001234567, +573009876543 ,, wa_573001112233", frozenset({"573001234567", "573009876543", "573001112233"})),
    ("abc, +57 300", frozenset()),
])
def test_allowlist_parsing_is_strict_and_fail_closed(raw: str, expected: frozenset[str]) -> None:
    from src.platform.config import parse_customer_allowlist

    assert parse_customer_allowlist(raw) == expected


def test_customer_allowed_accepts_phone_session_key_or_e164_and_is_fail_closed(monkeypatch) -> None:
    from src.platform import config

    monkeypatch.setattr(config, "MBA_CUSTOMER_ALLOWLIST", frozenset({"573001234567"}))
    assert config.mba_customer_allowed("573001234567")
    assert config.mba_customer_allowed("+573001234567")
    assert config.mba_customer_allowed("wa_573001234567")
    assert config.mba_customer_allowed("+57 300 123-4567")  # como lo escribe Meta en customer_phone
    assert not config.mba_customer_allowed("573009876543")
    assert not config.mba_customer_allowed("")
    monkeypatch.setattr(config, "MBA_CUSTOMER_ALLOWLIST", frozenset())
    assert not config.mba_customer_allowed("573001234567")


def test_flag_parsing_defaults_to_off(monkeypatch) -> None:
    from src.platform.config import parse_flag

    assert parse_flag(None) is False and parse_flag("") is False and parse_flag("PLACEHOLDER_set_out_of_band") is False
    assert parse_flag("0") is False and parse_flag("false") is False and parse_flag("no") is False
    assert parse_flag("1") is True and parse_flag("true") is True and parse_flag("YES") is True


def test_sdk_runtime_reexports_the_guard() -> None:
    import src.platform.config as impl
    import src.sdk.runtime as kit

    assert kit.mba_customer_allowed is impl.mba_customer_allowed
