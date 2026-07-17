"""El template MARKETING de campañas existe en el catálogo y es coherente.

La campaña SIEMPRE viaja por un template pre-aprobado por Meta (categoría
MARKETING) — nunca free-form fuera de ventana (error 131047 / sanciones).
"""
from src.platform.whatsapp.templates.registry import load_template_registry_from_yaml
from src.plugins.marketing.domain.campaigns import (
    CAMPAIGN_TEMPLATE_NAME,
    campaign_template_variables,
    new_campaign,
)


def _spec():
    registry = load_template_registry_from_yaml()
    assert CAMPAIGN_TEMPLATE_NAME in registry, (
        f"{CAMPAIGN_TEMPLATE_NAME} no está en templates/catalog.yaml"
    )
    return registry[CAMPAIGN_TEMPLATE_NAME]


def test_campaign_template_es_marketing_y_nunca_watchdog() -> None:
    spec = _spec()
    assert spec.category == "marketing"
    assert spec.triggers_when_window_expiring is False


def test_campaign_template_variables_matchean_el_spec() -> None:
    spec = _spec()
    campaign = new_campaign(campaign_id="mkt-1", name="Promo", now_ms=1)
    campaign["message"] = {
        "header": "🕯️ ¡Sólo por hoy! Velas con 15% OFF",
        "body": "Tenemos descuento en velas artesanales hasta el viernes.",
        "footer": "",
        "cta": "",
    }
    campaign["percent"] = 15
    campaign["coupon_code"] = "MAMA15"

    variables = campaign_template_variables(campaign, customer_name="Camila")

    spec_names = [v.name for v in spec.variables]
    assert sorted(variables.keys()) == sorted(spec_names)
    # Todas non-empty: Meta rechaza params vacíos.
    assert all(v.strip() for v in variables.values())
    assert variables["greeting"] == "Hola Camila"
    assert "MAMA15" in variables["campaign_offer"]


def test_campaign_template_tiene_definicion_de_provisioning() -> None:
    """El waba_template_name del catálogo tiene su definición en el CLI de
    provisioning (infra/whatsapp-provisioning) — sin eso no hay aprobación
    de Meta y el send fallaría en prod con template inexistente."""
    import json
    from pathlib import Path

    spec = _spec()
    definitions_path = (
        Path(__file__).resolve().parents[3].parent
        / "infra"
        / "whatsapp-provisioning"
        / "definitions"
        / "templates.json"
    )
    definitions = json.loads(definitions_path.read_text(encoding="utf-8"))
    by_name = {d["name"]: d for d in definitions}
    assert spec.waba_template_name in by_name
    definition = by_name[spec.waba_template_name]
    assert definition["category"] == "MARKETING"
    # {{1}}..{{3}} posicionales del body == las 3 variables del spec.
    assert definition["body"].count("{{") == len(spec.variables)
    assert len(definition["example"]) == len(spec.variables)
    # El marco aprobado DEBE incluir el opt-out (guía de marketing de Meta).
    assert "no recibir más" in definition["body"]


def test_campaign_template_variables_sin_nombre_ni_cupon() -> None:
    campaign = new_campaign(campaign_id="mkt-2", name="Lanzamiento", now_ms=1)
    campaign["goal"] = "launch"
    campaign["message"]["body"] = "Llega la Vela Buda Zen, hecha a mano."

    variables = campaign_template_variables(campaign, customer_name=None)

    assert variables["greeting"] == "Hola"
    assert all(v.strip() for v in variables.values())
