"""`is_vault_session_id` — el piso anti path-traversal de un id de sesión.

Regla de oro del SDK: `src.sdk.runtime` re-exporta el predicado de
`src.platform.state` (check por IDENTIDAD: la fachada expone EL MISMO objeto).
Consumidores: `chats` (`api/session_guard.py`, dashboard + handoff) y `orders`
(`by-session`, `vault-orders/.../retry|resolve`) — sus tests HTTP son los que
exigieron este comportamiento; acá se fija el contrato del símbolo.

Un `session_id` nombra un directorio del vault: todo id que llega de afuera y
termina en un `Path` pasa primero por acá.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.state import is_vault_session_id

# Seeds commiteados del vault: las formas REALES de id. Solo se leen NOMBRES.
_SEED_VAULT = Path(__file__).resolve().parents[2] / "hubara_vault"


def test_runtime_reexports_the_platform_predicate():
    import src.platform.state as impl
    import src.sdk.runtime as kit

    assert kit.is_vault_session_id is impl.is_vault_session_id


@pytest.mark.parametrize(
    "session_id",
    [
        "..",  # el padre del vault
        ".",  # el vault mismo
        "../x",
        "wa_1/../../etc",
        "wa_1\\..\\x",
        "",
        "wa_",  # prefijo sin cuerpo
        "wa_1\n",  # el `$` de `re.match` acepta este salto final
        "wa_1 ",
        "wa_a.b",  # el charset no puede ni expresar `..`
        "wa_1\x00",
        "_analytics",  # directorios del vault que NO son sesiones
        "_campaigns",
        "no-wa-prefix",
        "wa_" + "1" * 121,  # pasado el tope de largo
    ],
)
def test_rejects_anything_that_is_not_a_session_directory_name(session_id):
    assert is_vault_session_id(session_id) is False


@pytest.mark.parametrize(
    "session_id",
    [
        "wa_15550001111",  # prod: `wa_` + el `from` de Meta (solo dígitos)
        "wa_+15550001111",  # prefijo E.164 con `+`
        "wa_test_enum",  # sesiones de test con guion bajo
        "wa_Q1",
        "wa_" + "1" * 120,  # justo en el tope
    ],
)
def test_accepts_the_session_id_shapes_that_really_exist(session_id):
    assert is_vault_session_id(session_id) is True


def test_every_seeded_session_directory_is_a_vault_session_id():
    """Si un seed nuevo no pasa el piso, la bandeja lo listaría y no abriría."""
    if not _SEED_VAULT.is_dir():
        pytest.skip("hubara_vault/ no existe en este checkout")
    seeded = sorted(p.name for p in _SEED_VAULT.glob("wa_*") if p.is_dir())
    if not seeded:
        pytest.skip("hubara_vault/ sin seeds wa_*")

    assert [s for s in seeded if not is_vault_session_id(s)] == []
    assert is_vault_session_id("_analytics") is False
