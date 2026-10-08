"""Calidad LLM: la pestaña «Motor de decisiones» (pedido del operador, 2026-10-02).

Qué versión del motor corre la tienda (el paquete de decisión y su versión,
el oráculo, el perfil del turno) y cada decisión que toma, por la parte del
software donde actúa, con lo que resuelve y quién la decide hoy. Los textos
salen del catálogo del motor (`builtins.yaml: places` y `about`): el
certificador exige que cada decisión que el código pide traiga su
explicación, así que una decisión nueva nunca llega muda a la pantalla.

  GET /api/chats/perception/engine   (contrato perception-rollout@v1; cast de Agents)
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.chats.agent.sales.decisions import bots, registry
from src.plugins.chats.api import perception as api

#: forge no viaja a un clon (`copy_exclude`): sin él, esto es otra tienda.
IN_FORGE_CLONE = not (Path(__file__).resolve().parents[6] / "forge").is_dir()


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(api, "_vault_dir", lambda: tmp_path)
    monkeypatch.delenv("SALES_DECISIONS_BUNDLE", raising=False)
    monkeypatch.delenv("SALES_PERCEPTION_PROFILE", raising=False)
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "canary")
    registry.reset()
    app = FastAPI()
    app.include_router(api.router, prefix="/api/chats")
    yield TestClient(app)
    registry.reset()


def test_it_says_which_version_of_the_engine_the_store_runs(client: TestClient) -> None:
    body = client.get("/api/chats/perception/engine").json()

    assert body["bundle"] == {"id": "ventas", "version": 1, "ref": "ventas@1", "oracle": "jev-1.13", "engine_contract": 1,
                              "code_default": "ventas"}
    assert body["profile"] == bots.DEFAULT_PROFILE
    assert body["turn"]["policy"] and body["turn"]["topics"] > 0 and body["turn"]["questions"] > 0


def test_every_decision_says_where_it_acts_and_what_it_solves(client: TestClient) -> None:
    body = client.get("/api/chats/perception/engine").json()

    places = [p["id"] for p in body["places"]]
    assert places[0] == "ingest" and all(p["label"] for p in body["places"])
    decisions = {d["capability"]: d for d in body["decisions"]}
    # Las de la tienda y las de la App Operador (paquete `operador`), cada una con su paquete.
    assert set(decisions) == set(registry.active_bundle().capabilities) | {"burbuja", "incendio"}
    assert {d["bundle"] for d in body["decisions"]} == {"ventas@1", "operador-2@2"}
    for d in decisions.values():
        assert d["name"] and len(d["solves"]) > 40 and d["where"] and set(d["where"]) <= set(places), d
    assert decisions["compra"]["where"] == ["ingest"]
    assert decisions["destinatario_oracion"]["variant_of"] == "destinatario" and decisions["compra"]["variant_of"] is None


def test_each_decision_says_who_decides_it_today(client: TestClient, tmp_path: Path) -> None:
    """El modo guardado, dentro del techo de Terraform; una variante decide
    con el interruptor de su decisión."""
    bots.write_capability_modes(tmp_path, {"compra": "on", "baja": "shadow", "destinatario": "on"})

    decisions = {d["capability"]: d["mode"] for d in client.get("/api/chats/perception/engine").json()["decisions"]}

    assert (decisions["compra"], decisions["baja"], decisions["cantidad"]) == ("canary", "shadow", "off")
    assert decisions["destinatario_oracion"] == decisions["destinatario"] == "canary"


@pytest.mark.skipif(IN_FORGE_CLONE, reason="clon de forge: los paquetes de prueba de esta tienda no viajan")
def test_a_store_that_chose_another_bundle_shows_it(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-2")
    registry.reset()

    bundle = client.get("/api/chats/perception/engine").json()["bundle"]

    assert (bundle["ref"], bundle["code_default"]) == ("ventas-2@2", "ventas")


def test_a_bundle_that_does_not_compile_says_so(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "no-existe")
    registry.reset()

    res = client.get("/api/chats/perception/engine")

    assert res.status_code == 503 and "no-existe" in res.json()["detail"]


def test_the_operator_app_decisions_show_up_with_their_own_bundle(client: TestClient, tmp_path: Path) -> None:
    """La App Operador decide con el motor oficial y su paquete `operador`: la
    pestaña lo nombra junto al de la tienda y pone sus decisiones donde actúan."""
    bots.write_capability_modes(tmp_path, {"incendio": "shadow"})

    body = client.get("/api/chats/perception/engine").json()

    assert [b["ref"] for b in body["bundles"]] == ["ventas@1", "operador-2@2"]
    assert all(b["name"] for b in body["bundles"])
    places = {p["id"]: p["label"] for p in body["places"]}
    decisions = {d["capability"]: d for d in body["decisions"]}
    assert decisions["burbuja"]["where"] == ["chat_operador"] and places["chat_operador"]
    assert decisions["incendio"]["where"] == ["incendios"] and places["incendios"]
    assert (decisions["burbuja"]["mode"], decisions["incendio"]["mode"]) == ("off", "shadow")


def test_the_command_moves_an_operator_app_decision(client: TestClient, tmp_path: Path) -> None:
    """Como las de la tienda, por comando (`decisions/control.py`, desde el 2026-10-06 el
    panel es de solo lectura): la pestaña muestra el modo nuevo."""
    from src.plugins.chats.agent.sales.decisions import control

    out = control.set_capability(tmp_path, "burbuja", "shadow", actor="prueba")

    assert out["capabilities"]["burbuja"]["mode"] == "shadow"
    decisions = {d["capability"]: d["mode"] for d in client.get("/api/chats/perception/engine").json()["decisions"]}
    assert decisions["burbuja"] == "shadow"


def test_a_broken_operator_bundle_never_hides_the_store_engine(client: TestClient, monkeypatch) -> None:
    """Si el paquete de la App Operador no compila, la pestaña sigue mostrando el de la tienda."""
    from src.plugins.chats.shared.operator import decisions as operator
    from src.sdk.decisionkit import BundleError, Diagnostic

    def broken():
        raise BundleError([Diagnostic("DB001", "bundles/operador", "roto a propósito")])

    monkeypatch.setattr(operator, "active_bundle", broken)

    r = client.get("/api/chats/perception/engine")

    assert r.status_code == 200, r.text
    assert [b["ref"] for b in r.json()["bundles"]] == ["ventas@1"]
