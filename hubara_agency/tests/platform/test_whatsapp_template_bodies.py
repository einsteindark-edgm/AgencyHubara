"""Cuerpo de las plantillas en el catálogo + plantilla de seguimiento humano.

El dashboard necesita el texto REAL de cada plantilla para previsualizar lo que
recibe el cliente (modal "Reactivar conversación") y para pintarlo en el chat
después del envío. El copy aprobado vive en
`infra/whatsapp-provisioning/definitions/templates.json`; el catálogo lo espeja
y este guard impide que diverjan en silencio.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.platform.whatsapp.templates.registry import (
    load_template_registry_from_yaml,
    render_template_body,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFINITIONS = _REPO_ROOT / "infra" / "whatsapp-provisioning" / "definitions" / "templates.json"

FOLLOWUP = "human_followup_utility_v1"


@pytest.fixture(scope="module")
def registry():
    return load_template_registry_from_yaml()


def test_every_template_declares_body_matching_its_variable_count(registry):
    for spec in registry.values():
        assert spec.body, f"{spec.name} sin body"
        slots = sorted({int(n) for n in re.findall(r"\{\{(\d+)\}\}", spec.body)})
        assert slots == list(range(1, len(spec.variables) + 1)), spec.name


def test_catalog_body_matches_provisioning_definition(registry):
    definitions = {d["name"]: d for d in json.loads(_DEFINITIONS.read_text(encoding="utf-8"))}
    for spec in registry.values():
        assert spec.waba_template_name in definitions, spec.waba_template_name
        definition = definitions[spec.waba_template_name]
        assert spec.body == definition["body"], spec.name
        assert spec.category.upper() == definition["category"], spec.name


def test_human_followup_template_is_utility_operator_only_with_one_slot(registry):
    spec = registry[FOLLOWUP]
    assert spec.category == "utility"
    assert spec.triggers_when_window_expiring is False
    assert spec.requires_episode_stage is None
    assert [v.name for v in spec.variables] == ["followup_message"]
    # Meta rechaza bodies que empiezan o terminan en variable.
    assert not spec.body.strip().startswith("{{")
    assert not spec.body.strip().endswith("}}")


def test_render_template_body_fills_slots_positionally(registry):
    spec = registry["payment_pending_utility_v2"]
    rendered = render_template_body(
        spec, {"order_reference": "#1042", "amount_currency": "$120.000 COP"}
    )
    assert rendered == "Hola, tu pago de la orden #1042 por $120.000 COP está pendiente. ¿Te ayudo a completarlo?"
