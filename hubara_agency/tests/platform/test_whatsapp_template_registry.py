"""Tests del template registry de WhatsApp Cloud API.

Cubre:
  * Load del catalog.yaml real (4 templates iniciales).
  * Validación de variables (missing, extra, type, max_length).
  * Resolución por stage (utility automático del watchdog).
  * Reglas duras: marketing NO puede tener triggers_when_window_expiring=true.
  * Errores de carga (duplicates, category inválido, fields faltantes).

Ver `src/platform/whatsapp/templates/registry.py` y HU-WA24H-001 §4.5.
"""
from __future__ import annotations


import pytest

from src.platform.whatsapp.templates.registry import (
    TemplateSpec,
    TemplateVariable,
    _build_registry_from_dict,
    get_templates_for_stage,
    get_watchdog_template_for_stage,
    load_template_registry_from_yaml,
    validate_variables,
)


# =============================================================================
# Load del catalog real
# =============================================================================


class TestLoadCatalogReal:
    def test_loads_four_initial_templates(self):
        registry = load_template_registry_from_yaml()

        # Los 4 templates iniciales del HU (v2 sin saludo por nombre,
        # 2026-07-21) + el de campañas directas (sección Marketing, 2026-07-17).
        assert "quote_ready_utility_v2" in registry
        assert "payment_pending_utility_v2" in registry
        assert "order_status_utility_v2" in registry
        assert "cart_recovery_marketing_v2" in registry
        assert "campaign_promo_marketing_v1" in registry
        assert len(registry) == 5

    def test_quote_ready_has_correct_spec(self):
        registry = load_template_registry_from_yaml()
        spec = registry["quote_ready_utility_v2"]

        assert spec.category == "utility"
        assert spec.language == "es_CO"
        assert spec.waba_template_name == "quote_ready_utility_v2"
        assert spec.triggers_when_window_expiring is True
        assert spec.requires_episode_stage == "awaiting_quote"
        assert len(spec.variables) == 1
        assert spec.variables[0].name == "product_or_quote_label"

    def test_marketing_template_never_triggers_on_window_expiring(self):
        """Regla dura: marketing nunca por watchdog."""
        registry = load_template_registry_from_yaml()
        cart = registry["cart_recovery_marketing_v2"]
        assert cart.category == "marketing"
        assert cart.triggers_when_window_expiring is False

    def test_cart_recovery_has_no_promo_variable(self):
        """Política de copy (2026-07, guía de mensajes de marketing de Meta):
        el template de recuperación NO ofrece descuento/promoción ni envío
        gratis — solo re-engagement + opt-out. Una sola var (producto), sin
        `discount_label`.
        """
        registry = load_template_registry_from_yaml()
        cart = registry["cart_recovery_marketing_v2"]
        var_names = [v.name for v in cart.variables]
        assert var_names == ["product_label"]
        assert "discount_label" not in var_names

    def test_no_template_greets_by_customer_name(self):
        """Incidente 2026-07-21 (order #22, wa_573229041190): el nombre del
        cliente casi nunca existe (Medusa lo crea como placeholder "Cliente
        WhatsApp") y un slot vacío hace que Meta rechace el envío con 131008.
        Decisión del operador: NINGÚN template saluda por nombre — los saludos
        son "Hola," a secas. `campaign_promo_marketing_v1` es la excepción
        deliberada: su slot `greeting` es el saludo COMPLETO que arma el código
        de campañas con fallback no-vacío ("Hola"), no un nombre suelto.
        """
        registry = load_template_registry_from_yaml()
        for spec in registry.values():
            var_names = [v.name for v in spec.variables]
            assert "customer_first_name" not in var_names, (
                f"{spec.name} declara customer_first_name — los templates no "
                "saludan por nombre (slot vacío = Meta 131008)"
            )


# =============================================================================
# Constructor desde dict (unit con fixtures inline)
# =============================================================================


def _minimal_utility_entry(**overrides):
    base = {
        "name": "test_utility_v1",
        "category": "utility",
        "language": "es_CO",
        "waba_template_name": "test_utility",
        "semantics": "...",
        "triggers_when_window_expiring": True,
        "requires_episode_stage": "awaiting_quote",
        "variables": [{"name": "x", "type": "string", "max_length": 60}],
    }
    base.update(overrides)
    return base


class TestBuildRegistryFromDict:
    def test_minimal_valid_entry(self):
        registry = _build_registry_from_dict(
            {"templates": [_minimal_utility_entry()]}
        )
        assert "test_utility_v1" in registry

    def test_raises_when_no_templates_key(self):
        with pytest.raises(ValueError, match="top-level `templates` list"):
            _build_registry_from_dict({})

    def test_raises_on_duplicate_template_name(self):
        with pytest.raises(ValueError, match="Duplicate template name"):
            _build_registry_from_dict(
                {
                    "templates": [
                        _minimal_utility_entry(),
                        _minimal_utility_entry(),
                    ]
                }
            )

    def test_raises_on_invalid_category(self):
        with pytest.raises(ValueError, match="Invalid category"):
            _build_registry_from_dict(
                {"templates": [_minimal_utility_entry(category="promo")]}
            )

    def test_raises_on_marketing_with_watchdog_trigger(self):
        """Regla dura: marketing NO puede tener triggers_when_window_expiring=true."""
        with pytest.raises(
            ValueError, match="marketing templates MUST have triggers_when_window_expiring=false"
        ):
            _build_registry_from_dict(
                {
                    "templates": [
                        _minimal_utility_entry(
                            category="marketing",
                            triggers_when_window_expiring=True,
                        )
                    ]
                }
            )

    def test_marketing_with_watchdog_trigger_false_is_ok(self):
        registry = _build_registry_from_dict(
            {
                "templates": [
                    _minimal_utility_entry(
                        name="m1",
                        category="marketing",
                        triggers_when_window_expiring=False,
                    )
                ]
            }
        )
        assert "m1" in registry
        assert registry["m1"].category == "marketing"

    def test_raises_on_missing_required_field(self):
        entry = _minimal_utility_entry()
        del entry["semantics"]
        with pytest.raises(ValueError, match="missing required fields"):
            _build_registry_from_dict({"templates": [entry]})

    def test_raises_on_variable_missing_name(self):
        with pytest.raises(ValueError, match="variable missing name/type"):
            _build_registry_from_dict(
                {
                    "templates": [
                        _minimal_utility_entry(
                            variables=[{"type": "string"}]
                        )
                    ]
                }
            )

    def test_load_from_yaml_raises_when_file_missing(self, tmp_path):
        missing = tmp_path / "nope.yaml"
        with pytest.raises(FileNotFoundError, match="Template catalog YAML not found"):
            load_template_registry_from_yaml(missing)


# =============================================================================
# validate_variables
# =============================================================================


class TestValidateVariables:
    def _spec(self, **overrides) -> TemplateSpec:
        defaults = dict(
            name="x",
            category="utility",
            language="es_CO",
            waba_template_name="x",
            semantics="...",
            triggers_when_window_expiring=True,
            requires_episode_stage="awaiting_quote",
            variables=(
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
        )
        defaults.update(overrides)
        return TemplateSpec(**defaults)

    def test_returns_empty_when_all_match(self):
        spec = self._spec()
        errors = validate_variables(
            spec, {"customer_first_name": "Juan", "product_label": "vela"}
        )
        assert errors == []

    def test_reports_missing_variables(self):
        spec = self._spec()
        errors = validate_variables(spec, {"customer_first_name": "Juan"})
        assert any("Missing variables" in e for e in errors)
        assert any("product_label" in e for e in errors)

    def test_reports_extra_variables(self):
        spec = self._spec()
        errors = validate_variables(
            spec,
            {
                "customer_first_name": "Juan",
                "product_label": "vela",
                "extra_field": "boom",
            },
        )
        assert any("Unexpected variables" in e for e in errors)
        assert any("extra_field" in e for e in errors)

    def test_reports_non_str_value(self):
        spec = self._spec()
        errors = validate_variables(
            spec,
            {"customer_first_name": 123, "product_label": "vela"},  # type: ignore[dict-item]
        )
        assert any("must be str" in e for e in errors)

    def test_reports_value_exceeding_max_length(self):
        spec = self._spec()
        long_name = "x" * 100
        errors = validate_variables(
            spec, {"customer_first_name": long_name, "product_label": "vela"}
        )
        assert any("exceeds max" in e for e in errors)

    def test_reports_empty_string_value(self):
        """Meta rechaza params de template con texto vacío (131008 "Parameter
        of type text is missing text value" — incidente 2026-07-21). El
        registry lo ataja LOCAL y fail-fast, antes del POST a Meta.
        """
        spec = self._spec()
        errors = validate_variables(
            spec, {"customer_first_name": "", "product_label": "vela"}
        )
        assert any("empty" in e for e in errors)
        assert any("customer_first_name" in e for e in errors)

    def test_reports_whitespace_only_value(self):
        """Un valor solo-espacios es vacío para Meta — mismo rechazo 131008."""
        spec = self._spec()
        errors = validate_variables(
            spec, {"customer_first_name": "   ", "product_label": "vela"}
        )
        assert any("empty" in e for e in errors)

    def test_no_max_length_means_no_limit(self):
        spec = self._spec(
            variables=(
                TemplateVariable(
                    name="freeform",
                    type="string",
                    max_length=None,
                    description=None,
                ),
            )
        )
        errors = validate_variables(spec, {"freeform": "x" * 10_000})
        assert errors == []


# =============================================================================
# get_templates_for_stage + get_watchdog_template_for_stage
# =============================================================================


class TestStageResolution:
    def test_get_templates_for_stage_exact_match(self):
        registry = load_template_registry_from_yaml()
        matches = get_templates_for_stage(registry, "awaiting_quote")
        names = {s.name for s in matches}
        assert "quote_ready_utility_v2" in names
        assert "payment_pending_utility_v2" not in names

    def test_get_templates_for_stage_any(self):
        """`requires_episode_stage: any` matchea cualquier stage."""
        registry = load_template_registry_from_yaml()
        matches = get_templates_for_stage(registry, "awaiting_quote")
        names = {s.name for s in matches}
        # cart_recovery tiene requires_episode_stage: any
        assert "cart_recovery_marketing_v2" in names

    def test_get_templates_for_stage_no_match(self):
        registry = load_template_registry_from_yaml()
        matches = get_templates_for_stage(registry, "stage_inexistente")
        # Solo el "any" matchea (cart_recovery).
        names = {s.name for s in matches}
        assert names == {"cart_recovery_marketing_v2"}

    def test_get_watchdog_template_only_utility_with_window_flag(self):
        """El watchdog NUNCA selecciona marketing (triggers_when_window_expiring=false)."""
        registry = load_template_registry_from_yaml()
        result = get_watchdog_template_for_stage(registry, "awaiting_quote")
        assert result is not None
        assert result.name == "quote_ready_utility_v2"
        assert result.category == "utility"

    def test_get_watchdog_template_returns_none_when_no_utility_for_stage(self):
        registry = load_template_registry_from_yaml()
        result = get_watchdog_template_for_stage(registry, "stage_inexistente")
        # cart_recovery es marketing — no califica para watchdog
        assert result is None

    def test_get_watchdog_template_payment_pending(self):
        registry = load_template_registry_from_yaml()
        result = get_watchdog_template_for_stage(registry, "awaiting_payment")
        assert result is not None
        assert result.name == "payment_pending_utility_v2"

    def test_get_watchdog_template_post_purchase(self):
        registry = load_template_registry_from_yaml()
        result = get_watchdog_template_for_stage(registry, "post_purchase")
        assert result is not None
        assert result.name == "order_status_utility_v2"


# =============================================================================
# composition factory (singleton)
# =============================================================================


class TestCompositionFactory:
    def test_get_template_registry_singleton(self):
        """get_template_registry() devuelve el mismo dict (cache)."""
        from src.platform.whatsapp.composition import get_template_registry

        a = get_template_registry()
        b = get_template_registry()
        assert a is b  # mismo object — cached
