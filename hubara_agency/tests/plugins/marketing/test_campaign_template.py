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


def test_campaign_template_variables_sin_saltos_tabs_ni_5_espacios() -> None:
    """Meta (Cloud API, error 100) rechaza un param de texto del body con
    salto de línea, tab o más de 4 espacios seguidos — el envío entero de la
    campaña fallaría. Header+body y el copy que el operador escribe en el
    textarea (con saltos de párrafo) deben viajar en una sola línea."""
    import re

    forbidden = re.compile(r"[\n\r\t]| {5,}")
    campaign = new_campaign(campaign_id="mkt-3", name="Promo", now_ms=1)
    campaign["message"] = {
        "header": "¡Día del padre!",
        "body": "Velas artesanales\n\npara papá.\r\n\tEnvío     gratis.",
        "footer": "",
        "cta": "",
    }
    campaign["coupon_code"] = "PAPA10"
    campaign["valid_until"] = "domingo\n21"

    variables = campaign_template_variables(
        campaign, customer_name="Camila\tRuiz"
    )

    offending = {k: v for k, v in variables.items() if forbidden.search(v)}
    assert offending == {}
    assert variables["campaign_message"] == (
        "¡Día del padre! Velas artesanales para papá. Envío gratis."
    )
    assert variables["greeting"] == "Hola Camila Ruiz"


def test_campaign_message_separa_header_sin_puntuacion_como_oracion() -> None:
    campaign = new_campaign(campaign_id="mkt-4", name="Promo", now_ms=1)
    campaign["message"]["header"] = "Velas con 15% OFF"
    campaign["message"]["body"] = "Solo hasta el viernes."

    variables = campaign_template_variables(campaign, customer_name=None)

    assert variables["campaign_message"] == (
        "Velas con 15% OFF. Solo hasta el viernes."
    )
