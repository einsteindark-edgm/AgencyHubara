"""Tests del builder `build_template_message`.

Verifica:
  * Payload Meta shape exacto.
  * Orden POSICIONAL de parameters según spec.variables.
  * Validación delegada (missing/extra/max_length) levanta ValueError.
  * Template sin variables → sin `components` key.

Ver `src/platform/whatsapp/outbound.py::build_template_message` y
HU-WA24H-001 §4.6.
"""
from __future__ import annotations

import pytest

from src.platform.whatsapp.outbound import build_template_message
from src.platform.whatsapp.templates.registry import (
    TemplateSpec,
    TemplateVariable,
)


def _spec(
    *,
    name: str = "test_v1",
    waba_template_name: str = "test_template",
    language: str = "es_CO",
    variables: tuple[TemplateVariable, ...] = (
        TemplateVariable(
            name="customer_first_name",
            type="string",
            max_length=60,
            description=None,
        ),
        TemplateVariable(
            name="product_label",
            type="string",
            max_length=120,
            description=None,
        ),
    ),
    category: str = "utility",
    triggers_when_window_expiring: bool = True,
) -> TemplateSpec:
    return TemplateSpec(
        name=name,
        category=category,
        language=language,
        waba_template_name=waba_template_name,
        semantics="...",
        triggers_when_window_expiring=triggers_when_window_expiring,
        requires_episode_stage="awaiting_quote",
        variables=variables,
    )


class TestBuildTemplateMessagePayload:
    def test_basic_shape(self):
        payload = build_template_message(
            to="+573001112233",
            spec=_spec(),
            variables={
                "customer_first_name": "Juan",
                "product_label": "vela aromática",
            },
        )

        assert payload["messaging_product"] == "whatsapp"
        assert payload["recipient_type"] == "individual"
        assert payload["to"] == "+573001112233"
        assert payload["type"] == "template"
        assert payload["template"]["name"] == "test_template"
        assert payload["template"]["language"] == {"code": "es_CO"}

    def test_parameters_in_positional_order(self):
        """Meta usa parameters como {{1}}, {{2}}, ... — el orden MUSTmatchear
        el orden de spec.variables, no el dict order del input."""
        payload = build_template_message(
            to="+573001112233",
            spec=_spec(),
            variables={
                # Deliberadamente pasamos en orden inverso al spec
                "product_label": "vela",
                "customer_first_name": "Juan",
            },
        )

        params = payload["template"]["components"][0]["parameters"]
        # spec.variables[0] = customer_first_name → debe ir primero
        assert params[0] == {"type": "text", "text": "Juan"}
        # spec.variables[1] = product_label → segundo
        assert params[1] == {"type": "text", "text": "vela"}

    def test_components_structure(self):
        payload = build_template_message(
            to="+573001112233",
            spec=_spec(),
            variables={
                "customer_first_name": "Juan",
                "product_label": "vela",
            },
        )
        components = payload["template"]["components"]
        assert len(components) == 1
        assert components[0]["type"] == "body"
        assert len(components[0]["parameters"]) == 2

    def test_template_without_variables_omits_components(self):
        """Templates sin variables (full-text estático aprobado) → sin `components`."""
        payload = build_template_message(
            to="+573001112233",
            spec=_spec(variables=()),
            variables={},
        )
        assert "components" not in payload["template"]
        assert payload["template"]["name"] == "test_template"

    def test_different_language(self):
        payload = build_template_message(
            to="+541112345678",
            spec=_spec(language="es_AR"),
            variables={
                "customer_first_name": "Juan",
                "product_label": "vela",
            },
        )
        assert payload["template"]["language"] == {"code": "es_AR"}


class TestBuildTemplateMessageValidation:
    def test_raises_when_variable_missing(self):
        with pytest.raises(ValueError, match="Missing variables"):
            build_template_message(
                to="+573001112233",
                spec=_spec(),
                variables={"customer_first_name": "Juan"},  # falta product_label
            )

    def test_raises_when_extra_variable(self):
        with pytest.raises(ValueError, match="Unexpected variables"):
            build_template_message(
                to="+573001112233",
                spec=_spec(),
                variables={
                    "customer_first_name": "Juan",
                    "product_label": "vela",
                    "extra": "boom",
                },
            )

    def test_raises_when_variable_exceeds_max_length(self):
        with pytest.raises(ValueError, match="exceeds max"):
            build_template_message(
                to="+573001112233",
                spec=_spec(),
                variables={
                    "customer_first_name": "Juan",
                    "product_label": "x" * 200,  # max 120
                },
            )

    def test_raises_with_aggregated_errors_when_multiple_issues(self):
        """Un solo ValueError con todos los errores listados, no múltiples raises."""
        with pytest.raises(ValueError, match="Template 'test_v1' variables invalid"):
            build_template_message(
                to="+573001112233",
                spec=_spec(),
                variables={
                    "extra1": "x",
                    "extra2": "y",
                },  # missing both real vars + extra both fields
            )


class TestBuildTemplateIntegrationWithRealRegistry:
    def test_quote_ready_real_template(self):
        """E2E: cargar registry real + construir payload del quote_ready_utility_v2
        (sin saludo por nombre — incidente 2026-07-21)."""
        from src.platform.whatsapp.composition import get_template_registry

        registry = get_template_registry()
        spec = registry["quote_ready_utility_v2"]

        payload = build_template_message(
            to="+573009999999",
            spec=spec,
            variables={
                "product_or_quote_label": "kit aroma rosas (cotización #42)",
            },
        )

        assert payload["template"]["name"] == "quote_ready_utility_v2"
        assert payload["template"]["language"] == {"code": "es_CO"}
        params = payload["template"]["components"][0]["parameters"]
        assert len(params) == 1
        assert "kit aroma rosas" in params[0]["text"]
