"""Anonimización, perfiles y composición del puerto de percepción (PR 4).

* Las ráfagas salen anonimizadas hacia proveedores nuevos (OpenRouter y
  TypeSafe; decisión 2 del plan): teléfonos, correos, direcciones y los
  nombres que el llamador pida tapar.
* Los perfiles viven versionados en `profiles.yaml` con ids FIJOS (L-23:
  nunca "latest", que cambia de modelo sin avisar).
* `get_perception_port(perfil)` elige el adaptador del perfil; un perfil
  desconocido o `PERCEPTION_PROVIDER=off` dan el adaptador nulo (fail-open).
"""
from __future__ import annotations

import pytest

from src.platform.perception import composition
from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.platform.perception.adapters.null import NullPerceptionAdapter
from src.platform.perception.adapters.openrouter_decisions import OpenRouterDecisionsAdapter
from src.platform.perception.anonymize import anonymize_text
from src.platform.perception.profiles import load_profiles


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("mi número es 3001234567", "3001234567"),
        ("escríbeme al +57 300 123 4567", "300 123 4567"),
        ("wa_573001234567 escribió", "573001234567"),
        ("el correo es caro.perez@example.com", "caro.perez@example.com"),
        ("vivo en la Calle 45 # 12-30, Chapinero", "45 # 12-30"),
        ("envíalo a la Cra 7 No. 32-16 apto 501", "7 No. 32-16"),
        ("Transversal 5B #40-12", "5B #40-12"),
    ],
)
def test_anonymize_removes_contact_data(text: str, secret: str) -> None:
    assert secret not in anonymize_text(text)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        # Direcciones escritas sin "#" ni "No." (así las escribe mucha gente).
        ("mándalo a la cra 7 45-12 por favor", "7 45-12"),
        ("es en la Cra 7 12 34 apto 501", "7 12 34"),
        ("calle 10 20 30, barrio Suba", "10 20 30"),
        # Nombres que el cliente dice de sí mismo o de quien recibe.
        ("me llamo Carolina Pérez", "Carolina Pérez"),
        ("mi nombre es Juan Carlos Gómez", "Juan Carlos Gómez"),
        ("va a nombre de Luisa Fernanda Ruiz", "Luisa Fernanda Ruiz"),
        ("lo recibe Andrés Mejía en la portería", "Andrés Mejía"),
    ],
)
def test_anonymize_removes_addresses_and_names_the_way_customers_write_them(text: str, secret: str) -> None:
    assert secret not in anonymize_text(text)


def test_anonymize_blanks_the_personal_fields_of_the_shipping_form() -> None:
    """El Flow de envío llega como `[datos de envío recibidos] k=v; k=v`: los
    valores personales se tapan; la ciudad (el clasificador la necesita para
    el asunto del envío) queda."""
    text = (
        "[datos de envío recibidos] nombre_recibe=Carolina Pérez; direccion=Cra 7 12 34 apto 501; "
        "barrio=Chapinero Alto; telefono=3001234567; cedula=1020304050; ciudad=Bogotá"
    )

    out = anonymize_text(text)

    for secret in ("Carolina", "Cra 7", "Chapinero", "3001234567", "1020304050"):
        assert secret not in out
    assert out.startswith("[datos de envío recibidos]")
    assert "ciudad=Bogotá" in out


def test_anonymize_keeps_what_the_classifier_needs() -> None:
    text = "quiero 2 velas de $45.000 para el 14 de febrero, envío a Bogotá"

    assert anonymize_text(text) == text
    # "soy de …" es una ciudad, no un nombre
    assert anonymize_text("soy de Medellín, ¿tienen envío?") == "soy de Medellín, ¿tienen envío?"


def test_anonymize_redacts_given_names_as_whole_words_case_insensitive() -> None:
    out = anonymize_text("Hola, soy CAROLINA. carolina pide la Cubo Love", redact=("Carolina",))

    assert "carolina" not in out.lower()
    assert "Cubo Love" in out


def test_oracle_profiles_pin_fixed_model_ids() -> None:
    """Perfiles del ORÁCULO: proveedor, modelo fijo, tiempo máximo y
    anonimización. Cuestionarios, políticas y umbrales son del motor
    (`sales/decisions/profiles.yaml`), no de la plataforma."""
    profiles = load_profiles()

    assert profiles["jev-1.13"].provider == "openrouter_decisions"
    assert profiles["jev-1.13"].model == "typesafe/jev-1.13"
    for p in profiles.values():
        assert "latest" not in p.model.lower(), f"{p.id}: id móvil (L-23)"
        # La espera de Jev es 10 s (decisión del operador 2026-09-29: «es
        # indispensable que siempre funcione con Jev»); tope de cordura 15 s
        # para no dejar al cliente esperando de más.
        assert p.timeout_s <= 15
    assert profiles["jev-1.13"].timeout_s == 10
    # Sin respuesta a los 2 s, un reenvío; a los 5 s, otro (una de cada diez
    # llamadas se cuelga: el reenvío la salva, esperar más no).
    assert profiles["jev-1.13"].resend_after_s == (2.0, 5.0)


def test_only_jev_answers_no_openai_rival() -> None:
    """Decisión del operador (2026-09-28): 100 % Jev. El único proveedor
    externo es la Decisions API de OpenRouter."""
    assert {p.provider for p in load_profiles().values()} <= {"openrouter_decisions", "fake", "null"}
    assert "openai-lp-v1" not in load_profiles()


def test_every_profile_that_leaves_the_box_anonymizes() -> None:
    for p in load_profiles().values():
        if p.provider == "openrouter_decisions":
            assert p.anonymize, p.id


@pytest.fixture(autouse=True)
def _clear_cache():
    composition.get_perception_port.cache_clear()
    yield
    composition.get_perception_port.cache_clear()


def test_composition_builds_the_adapter_of_each_profile(monkeypatch) -> None:
    monkeypatch.delenv("PERCEPTION_PROVIDER", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    jev = composition.get_perception_port("jev-1.13")

    assert isinstance(jev, OpenRouterDecisionsAdapter) and jev.model == "typesafe/jev-1.13"
    # El perfil del rival OpenAI ya no existe: sin adaptador (fail-open).
    assert isinstance(composition.get_perception_port("openai-lp-v1"), NullPerceptionAdapter)


def test_unknown_profile_is_the_null_adapter(monkeypatch) -> None:
    monkeypatch.delenv("PERCEPTION_PROVIDER", raising=False)

    assert isinstance(composition.get_perception_port("no-existe"), NullPerceptionAdapter)


@pytest.mark.parametrize(("env", "cls"), [("fake", FakePerceptionAdapter), ("off", NullPerceptionAdapter)])
def test_env_override_for_tests_and_the_kill_switch(monkeypatch, env: str, cls: type) -> None:
    monkeypatch.setenv("PERCEPTION_PROVIDER", env)

    assert isinstance(composition.get_perception_port("jev-1.13"), cls)


def test_sdk_exposes_the_port_lazily() -> None:
    import src.sdk.connectorkit as ck

    for name in ("PerceptionPort", "TypedQuestion", "TypedAnswer", "PerceptionResult", "get_perception_port", "anonymize_text"):
        assert name in dir(ck), name
    assert ck.get_perception_port is composition.get_perception_port


def test_ssm_placeholder_key_counts_as_no_key(monkeypatch) -> None:
    """Terraform crea la llave con un placeholder hasta que el operador la carga
    (plan §9): con el placeholder no se llama a OpenRouter (sería un 401 por turno)."""
    monkeypatch.delenv("PERCEPTION_PROVIDER", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "PLACEHOLDER_set_out_of_band")

    port = composition.get_perception_port("jev-1.13")

    assert isinstance(port, OpenRouterDecisionsAdapter) and not port.has_api_key
