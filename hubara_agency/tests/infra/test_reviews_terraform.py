"""La configuración de las reseñas nace en Terraform (regla del operador).

Todo lo que `ReviewSettings` lee del entorno está declarado por tenant: los
interruptores y tiempos en `modules/review-config` (git = verdad, nace
apagado), el `flow_id` del formulario como clave secreta (lo publica el CLI de
provisioning en el WABA del tenant), y el plugin habilitado en Hubara.
"""
from __future__ import annotations

import re
from pathlib import Path

from src.plugins.reviews.agent.settings import CONFIG_ENV_KEYS, SECRET_ENV_KEYS

_TERRAFORM = Path(__file__).resolve().parents[3] / "infra" / "terraform" / "platform"


def _hcl(path: Path) -> str:
    return "\n".join(line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


def test_every_review_knob_the_code_reads_is_a_tenant_parameter() -> None:
    text = _hcl(_TERRAFORM / "modules" / "review-config" / "main.tf")
    for key in CONFIG_ENV_KEYS:
        assert re.search(rf"^\s*{key}\s*=", text, re.M), f"review-config no declara {key}"


def test_the_flow_id_is_a_secret_key_set_out_of_band() -> None:
    text = _hcl(_TERRAFORM / "variables.tf")
    for key in SECRET_ENV_KEYS:
        assert f'"{key}"' in text, f"{key} falta en secret_keys"


def test_reviews_are_born_off_and_the_mode_is_validated() -> None:
    text = _hcl(_TERRAFORM / "variables.tf")
    assert re.search(r'mode\s*=\s*optional\(string,\s*"off"\)', text)
    assert "t.reviews.mode" in text, "falta la validación off | test | on"
    main = _hcl(_TERRAFORM / "main.tf")
    assert 'source   = "./modules/review-config"' in main



def test_terraform_defaults_match_the_code_and_test_mode_needs_numbers() -> None:
    """Premortem P12/I4: el botón a Google por defecto es el mismo que el del
    código (directo a Google), y `test` sin números de prueba (o mal
    escritos) no pasa el plan — antes callaba invitando a nadie."""
    from src.plugins.reviews.agent.settings import DEFAULT_GOOGLE_REVIEW_URL

    text = _hcl(_TERRAFORM / "variables.tf")
    assert re.search(rf'google_review_url\s*=\s*optional\(string,\s*"{re.escape(DEFAULT_GOOGLE_REVIEW_URL)}"\)', text)
    assert "t.reviews.test_sessions" in text, "falta validar las sesiones de prueba"
    assert 't.reviews.mode != "test" || length(t.reviews.test_sessions) > 0' in text


def test_test_numbers_can_be_written_like_the_other_tfvars_lists() -> None:
    """Los números de prueba del operador ya viven en tfvars como «+57…»
    (`customer_allowlist`, `internal_numbers`): el de reseñas acepta lo mismo
    (y `wa_…`); el código los normaliza a la conversación `wa_<dígitos>`."""
    text = (_TERRAFORM / "variables.tf").read_text(encoding="utf-8")
    match = re.search(r'for s in t\.reviews\.test_sessions : can\(regex\("([^"]+)", s\)\)', text)
    assert match, "falta la validación de las sesiones de prueba"
    pattern = match.group(1).replace("\\\\", "\\")
    for ok in ("+573001234567", "573001234567", "wa_573001234567"):
        assert re.fullmatch(pattern, ok), ok
    for bad in ("3001234567 ", "wa_", "+57 300"):
        assert not re.fullmatch(pattern, bad), bad
