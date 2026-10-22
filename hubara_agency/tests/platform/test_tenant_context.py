"""De qué tienda es cada cosa (Acktos Stores, F1.1).

La caja de Hubara sigue siendo una sola tienda: fuera de todo alcance responde
`hubara`, sin que su entorno cambie. Una caja de una tienda nombra la suya con
`ACKTOS_TENANT`. La celda (`ACKTOS_CELL`) atiende varias y no tiene tienda por
defecto: leer la tienda fuera de un alcance es un error, nunca otra tienda.
"""
from __future__ import annotations

import asyncio

import pytest

from src.platform.tenant import (
    InvalidTenantId,
    TenantNotInScope,
    current_tenant,
    tenant_scope,
)


@pytest.fixture(autouse=True)
def _box_without_tenant_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACKTOS_TENANT", raising=False)
    monkeypatch.delenv("ACKTOS_CELL", raising=False)


def test_hubara_box_answers_its_own_store_outside_any_scope() -> None:
    assert current_tenant() == "hubara"


def test_a_single_store_box_answers_the_store_its_environment_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACKTOS_TENANT", "vincenzo")

    assert current_tenant() == "vincenzo"


def test_inside_a_scope_everything_belongs_to_that_store() -> None:
    with tenant_scope("acme"):
        assert current_tenant() == "acme"

    assert current_tenant() == "hubara"


def test_a_nested_scope_gives_back_the_outer_store_when_it_ends() -> None:
    with tenant_scope("acme"):
        with tenant_scope("beta"):
            assert current_tenant() == "beta"
        assert current_tenant() == "acme"


def test_the_store_is_given_back_even_when_the_block_fails() -> None:
    with pytest.raises(RuntimeError), tenant_scope("acme"):
        raise RuntimeError("el turno falló")

    assert current_tenant() == "hubara"


async def test_two_stores_served_at_the_same_time_never_see_each_other() -> None:
    seen: list[tuple[str, str]] = []

    async def serve(store: str) -> None:
        with tenant_scope(store):
            await asyncio.sleep(0)  # la otra tienda corre en el medio
            seen.append((store, current_tenant()))

    await asyncio.gather(serve("acme"), serve("beta"))

    assert sorted(seen) == [("acme", "acme"), ("beta", "beta")]


def test_a_cell_has_no_store_outside_a_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACKTOS_CELL", "1")

    with pytest.raises(TenantNotInScope):
        current_tenant()
    with tenant_scope("acme"):
        assert current_tenant() == "acme"


# La tienda nombra su carpeta del vault, el prefijo de sus IDs de Temporal, su
# árbol de SSM, su base de Medusa y su grupo de Cognito: las mismas reglas del
# slug de forge (sin «hubara», que acá sí es una tienda).
@pytest.mark.parametrize(
    "store",
    ["", "Acme", "a", "1acme", "acme_store", "acme-store", "acme store", "acme\n",
     "../acme", "a" * 21, "awsacme", "ssmacme", "amazonas", "lab", "graphagents"],
)
def test_a_scope_refuses_a_store_that_cannot_name_its_resources(store: str) -> None:
    with pytest.raises(InvalidTenantId), tenant_scope(store):
        pass


def test_a_box_whose_environment_names_an_invalid_store_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACKTOS_TENANT", "Vincenzo")

    with pytest.raises(InvalidTenantId):
        current_tenant()
