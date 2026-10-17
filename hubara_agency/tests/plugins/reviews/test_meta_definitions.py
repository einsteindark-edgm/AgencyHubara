"""Lo que se somete a Meta para las reseñas, atado a lo que usa el runtime.

* El formulario `review_v1` (Flow, endpoint-less): su pantalla es la que abre
  el botón, devuelve exactamente lo que lee `parse_review_response` y respeta
  los límites de Meta.
* La plantilla `order_review_marketing_v1`: MARKETING (dice el descuento —
  decisión del operador), foto en el encabezado, botón FLOW a ese formulario,
  la baja «NO MÁS» y el mismo texto que la invitación dentro de la ventana.
* La CLI de aprovisionamiento arma el botón FLOW con el id del Flow publicado.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from src.platform.whatsapp.templates.registry import (
    load_template_registry_from_yaml,
    render_template_body,
)
from src.plugins.reviews.domain.copy import FLOW_SCREEN, TEMPLATE_NAME, invitation_body

_REPO = Path(__file__).resolve().parents[4]
_DEFS = _REPO / "infra" / "whatsapp-provisioning" / "definitions"
_FLOW_JSON = _REPO / "hubara_agency" / "docs" / "whatsapp_flows" / "review_v1.json"


def _defs(kind: str) -> list[dict]:
    return json.loads((_DEFS / f"{kind}.json").read_text(encoding="utf-8"))


# ---------- el formulario ----------


def test_the_review_flow_is_declared_for_provisioning_with_its_ssm_key():
    [flow] = [f for f in _defs("flows") if f.get("ssm_key") == "META_FLOW_ID_REVIEW"]
    assert flow["json"] == "hubara_agency/docs/whatsapp_flows/review_v1.json"
    assert flow["publish"] is True


def test_the_flow_screen_returns_what_the_parser_reads():
    flow = json.loads(_FLOW_JSON.read_text(encoding="utf-8"))
    [screen] = flow["screens"]
    assert screen["id"] == FLOW_SCREEN and screen["terminal"] is True
    children = {c.get("name") or c["type"]: c for c in screen["layout"]["children"]}
    assert [o["id"] for o in children["rating"]["data-source"]] == ["5", "4", "3", "2", "1"]
    assert children["rating"]["required"] is True
    payload = children["Footer"]["on-click-action"]["payload"]
    assert set(payload) == {"rating", "comment", "photo", "publish_consent"}
    assert set(screen["data"]) == {"order_label", "items_summary", "reward_note"}


def test_flow_labels_respect_meta_limits():
    flow = json.loads(_FLOW_JSON.read_text(encoding="utf-8"))
    for child in flow["screens"][0]["layout"]["children"]:
        if child["type"] in ("TextArea", "TextInput"):
            assert len(child["label"]) <= 20, child["label"]
        if child["type"] == "RadioButtonsGroup":
            assert len(child["label"]) <= 30
            assert all(len(o["title"]) <= 30 for o in child["data-source"])
        if child["type"] == "OptIn":
            assert len(child["label"]) <= 120
        if child["type"] == "Footer":
            assert len(child["label"]) <= 35


# ---------- la plantilla ----------


def test_the_template_is_marketing_with_photo_flow_button_and_opt_out():
    spec = load_template_registry_from_yaml()[TEMPLATE_NAME]
    assert spec.category == "marketing"
    assert spec.header_format == "image"
    assert [(b.type, b.navigate_screen) for b in spec.buttons] == [("flow", FLOW_SCREEN)]
    assert spec.triggers_when_window_expiring is False
    assert "NO MÁS" in spec.body
    assert "5 %" in spec.body and "10 %" in spec.body


def test_template_and_in_window_invitation_say_the_same():
    spec = load_template_registry_from_yaml()[TEMPLATE_NAME]
    assert render_template_body(spec, {"order_reference": "#31"}).startswith(invitation_body("#31"))


def test_template_definition_buttons_match_the_catalog():
    definition = {d["name"]: d for d in _defs("templates")}[TEMPLATE_NAME]
    spec = load_template_registry_from_yaml()[TEMPLATE_NAME]
    [button] = definition["buttons"]
    assert button["type"] == "FLOW"
    assert button["text"] == spec.buttons[0].text
    assert button["navigate_screen"] == spec.buttons[0].navigate_screen
    flow_names = {f["name"] for f in _defs("flows")}
    assert button["flow_name"] in flow_names
    assert (_DEFS / definition["header"]["example_file"]).is_file()


# ---------- la CLI de aprovisionamiento ----------


@pytest.fixture
def provision(monkeypatch):
    path = _REPO / "infra" / "whatsapp-provisioning" / "whatsapp_provision.py"
    spec = importlib.util.spec_from_file_location("whatsapp_provision_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_resumable_upload", lambda cfg, p: "HANDLE")
    return module


def test_cli_builds_the_flow_button_with_the_published_flow_id(provision, monkeypatch):
    definition = {d["name"]: d for d in _defs("templates")}[TEMPLATE_NAME]
    flow_name = definition["buttons"][0]["flow_name"]
    monkeypatch.setattr(
        provision, "actual_flows", lambda cfg: [{"id": "FLOW_99", "name": flow_name, "status": "PUBLISHED"}]
    )
    components = provision._template_components({}, definition)
    buttons = next(c for c in components if c["type"] == "BUTTONS")
    assert buttons["buttons"] == [
        {
            "type": "FLOW",
            "text": "Calificar mi pedido",
            "flow_id": "FLOW_99",
            "flow_action": "navigate",
            "navigate_screen": "RATING",
        }
    ]


def test_cli_does_not_submit_the_template_before_the_flow_is_published(provision, monkeypatch):
    definition = {d["name"]: d for d in _defs("templates")}[TEMPLATE_NAME]
    monkeypatch.setattr(provision, "actual_flows", lambda cfg: [])
    assert provision._template_components({}, definition) is None
