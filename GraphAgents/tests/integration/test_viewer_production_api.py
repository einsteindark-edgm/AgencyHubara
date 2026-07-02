"""El SAVE de producción del explorer — `sdk.production` + sus endpoints.

Semántica: "guardar producción" = bendecir el cableado ACTUAL como el estado final.
- El save corre los checks del sistema y SE REHÚSA si algo está roto (no se bendice
  producción rota — L-19: ninguna etiqueta verde sin check real detrás).
- El snapshot (`production.yaml`, en la RAÍZ del subsistema — manifests/*.yaml es
  territorio de los globs de manifests) lleva un fingerprint DETERMINISTA del
  wiring (edges + node ids); cualquier connect/disconnect posterior lo deja `dirty`.
- `GET /api/production-status` es lo que el header del explorer pinta (✓ al día /
  ● cambios sin guardar / sin snapshot).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from viewer.server import api_route

GA_ROOT = Path(__file__).resolve().parents[2]

BROKEN = """\
name: roto
archetype: extractor
contract:
  inputs:
    payload: {type: object}
tools:
  - uses: no-existe@1
"""


@pytest.fixture()
def ga(tmp_path: Path) -> Path:
    shutil.copytree(GA_ROOT / "manifests", tmp_path / "manifests")
    shutil.copytree(GA_ROOT / "tools", tmp_path / "tools")
    # (el fixture arranca sin snapshot: production.yaml vive en la raíz y no se copia)
    return tmp_path


def test_status_sin_snapshot(ga):
    status, payload = api_route("GET", "/api/production-status", {}, None, ga_root=ga)
    assert status == 200
    assert payload["saved"] is False
    assert payload["dirty"] is True


def test_save_escribe_snapshot_y_status_queda_al_dia(ga):
    status, payload = api_route("POST", "/api/save", {}, {}, ga_root=ga)
    assert status == 200, payload
    assert payload["ok"] is True
    snap_file = ga / "production.yaml"
    assert snap_file.exists()
    snap = yaml.safe_load(snap_file.read_text(encoding="utf-8"))
    assert snap["fingerprint"] == payload["snapshot"]["fingerprint"]
    assert snap["pods"]  # los taskgraph roots del catálogo
    status, payload = api_route("GET", "/api/production-status", {}, None, ga_root=ga)
    assert status == 200
    assert payload["saved"] is True
    assert payload["dirty"] is False


def test_editar_el_wiring_ensucia_el_snapshot(ga):
    assert api_route("POST", "/api/save", {}, {}, ga_root=ga)[0] == 200
    st, _ = api_route("POST", "/api/connect", {},
                      {"source": "agent:roas-cac", "target": "tool:diagnose",
                       "binding": {"payload": "$state.payload"}}, ga_root=ga)
    assert st == 200
    status, payload = api_route("GET", "/api/production-status", {}, None, ga_root=ga)
    assert payload["saved"] is True
    assert payload["dirty"] is True


def test_save_se_rehusa_con_el_sistema_roto(ga):
    (ga / "manifests" / "roto.agent.yaml").write_text(BROKEN, encoding="utf-8")
    status, payload = api_route("POST", "/api/save", {}, {}, ga_root=ga)
    assert status == 422
    assert payload["ok"] is False
    assert any("roto" in e for e in payload["errors"]), payload
    assert not (ga / "production.yaml").exists()  # no se bendijo nada


def test_fingerprint_es_determinista(ga):
    from sdk.production import graph_fingerprint

    from sdk.graph import build_graph

    g1 = build_graph(ga)
    g2 = build_graph(ga)
    assert graph_fingerprint(g1) == graph_fingerprint(g2)
    # y depende del WIRING: quitar una arista lo cambia
    g2["edges"] = g2["edges"][:-1]
    assert graph_fingerprint(g1) != graph_fingerprint(g2)
