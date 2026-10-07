"""Secretos de un tenant: la plantilla y la verificación de `aws_bootstrap.py`.

Terraform crea el NOMBRE de cada secreto (`secret_keys` en
`infra/terraform/platform/variables.tf`) con un placeholder; el VALOR se carga
fuera de banda con `aws_bootstrap.py secrets` (nunca en git ni en el state).
Para que un sitio nuevo no dependa de la memoria de nadie:

- la plantilla `infra/scripts/secrets.example.env` sale de `secret_keys` y
  esta prueba exige que no se desfase;
- `secrets` se niega a subir una llave que Terraform no conoce y no sube las
  que vienen vacías;
- `verify --tenant` lista las llaves que faltan o siguen en placeholder sin
  que ningún valor llegue al script.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "infra" / "scripts" / "aws_bootstrap.py"
_TEMPLATE = _REPO / "infra" / "scripts" / "secrets.example.env"
_VARIABLES = _REPO / "infra" / "terraform" / "platform" / "variables.tf"
_SECRETS_MODULE = _REPO / "infra" / "terraform" / "platform" / "modules" / "secrets" / "main.tf"


def _load():
    spec = importlib.util.spec_from_file_location("aws_bootstrap", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


boot = _load()


def _declared_keys() -> list[str]:
    """Las llaves de `secret_keys`, leídas a mano (independiente del parser del script)."""
    text = _VARIABLES.read_text(encoding="utf-8")
    block = text[text.index('variable "secret_keys"'):]
    block = block[: block.index("\n}\n")]
    return re.findall(r'^\s*"([A-Z0-9_]+)",?\s*$', block, flags=re.M)


# ── La plantilla ────────────────────────────────────────────────────────────


def test_the_script_reads_every_secret_terraform_declares() -> None:
    keys = [key for key, _ in boot.secret_keys(_VARIABLES)]

    assert keys == _declared_keys()
    assert "OPENROUTER_API_KEY" in keys and "DEEPSEEK_API_KEY" in keys


def test_each_secret_keeps_the_explanation_written_above_it_in_terraform() -> None:
    notes = dict(boot.secret_keys(_VARIABLES))

    assert "HMAC" in notes["WHATSAPP_APP_SECRET"]
    assert "openssl rand -hex 32" in notes["HUBARA_SERVICE_TOKEN"]
    assert notes["DEEPSEEK_API_KEY"] == ""


def test_the_committed_template_is_generated_from_secret_keys() -> None:
    """Si esto falla, alguien cambió `secret_keys`: regenera la plantilla con
    `python3 infra/scripts/aws_bootstrap.py template` y commitéala."""
    assert _TEMPLATE.read_text(encoding="utf-8") == boot.render_template(boot.secret_keys(_VARIABLES))


def test_the_template_never_carries_a_value() -> None:
    lines = [ln for ln in _TEMPLATE.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]

    assert lines == [f"{key}=" for key in _declared_keys()]


def test_the_placeholder_is_the_one_terraform_writes() -> None:
    assert f'"{boot.PLACEHOLDER}"' in _SECRETS_MODULE.read_text(encoding="utf-8")


# ── Subir valores ───────────────────────────────────────────────────────────


def test_upload_plan_skips_empty_values_and_flags_keys_terraform_does_not_know() -> None:
    pairs = [("DEEPSEEK_API_KEY", "sk-1"), ("GEMINI_API_KEY", ""), ("OPENROUTR_API_KEY", "typo")]

    upload, empty, unknown = boot.plan_upload(pairs, {"DEEPSEEK_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"})

    assert upload == [("DEEPSEEK_API_KEY", "sk-1")]
    assert empty == ["GEMINI_API_KEY"]
    assert unknown == ["OPENROUTR_API_KEY"]


def test_secrets_refuses_a_key_terraform_does_not_know_and_uploads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "secrets.nuevo.env"
    env_file.write_text("DEEPSEEK_API_KEY=sk-1\nOPENROUTR_API_KEY=typo\n", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(boot, "run", lambda cmd, **kw: calls.append(cmd) or "")
    monkeypatch.setattr(sys, "argv", ["aws_bootstrap.py", "secrets", "--tenant", "nuevo", "--file", str(env_file)])

    with pytest.raises(SystemExit) as exit_info:
        boot.main()

    assert "OPENROUTR_API_KEY" in str(exit_info.value)
    assert calls == []


def test_secrets_uploads_only_keys_with_a_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "secrets.nuevo.env"
    env_file.write_text("# comentario\nDEEPSEEK_API_KEY=sk-1\nGEMINI_API_KEY=\n", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(boot, "run", lambda cmd, **kw: calls.append(cmd) or "")
    monkeypatch.setattr(sys, "argv", ["aws_bootstrap.py", "secrets", "--tenant", "nuevo", "--file", str(env_file)])

    boot.main()

    names = [cmd[cmd.index("--name") + 1] for cmd in calls]
    assert names == ["/hubara/nuevo/DEEPSEEK_API_KEY"]


def test_a_json_file_is_loaded_with_at_and_travels_in_one_line(tmp_path: Path) -> None:
    # Las llaves de Firebase son ARCHIVOS: la cuenta de servicio (con una llave privada de varias líneas) y el
    # google-services.json. El .env que arma el deploy desde SSM es una línea por clave: va en una sola línea.
    account = {"type": "service_account", "private_key": "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"}
    (tmp_path / "descargas").mkdir()
    (tmp_path / "descargas" / "cuenta.json").write_text(json.dumps(account, indent=2), encoding="utf-8")
    env = tmp_path / "secrets.hubara.env"
    env.write_text("FCM_SERVICE_ACCOUNT_JSON=@descargas/cuenta.json\nOPENROUTER_API_KEY=llave\n", encoding="utf-8")

    pairs = dict(boot.parse_env_file(env))

    assert "\n" not in pairs["FCM_SERVICE_ACCOUNT_JSON"]
    assert json.loads(pairs["FCM_SERVICE_ACCOUNT_JSON"]) == account
    assert pairs["OPENROUTER_API_KEY"] == "llave"


@pytest.mark.parametrize(
    ("content", "why"),
    [("linea 1\nlinea 2\n", "varias líneas"), ('{"clave": "va$lor"}', "$"), (json.dumps({"x": "a" * 5000}), "4 KB")],
    ids=["varias_lineas", "signo_pesos", "mas_de_4_kb"],
)
def test_a_file_that_would_break_the_env_or_ssm_is_refused(tmp_path: Path, content: str, why: str) -> None:
    (tmp_path / "archivo").write_text(content, encoding="utf-8")
    env = tmp_path / "secrets.env"
    env.write_text("FIREBASE_ANDROID_CONFIG_JSON=@archivo\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="FIREBASE_ANDROID_CONFIG_JSON"):
        boot.parse_env_file(env)


# ── Verificar antes del deploy ──────────────────────────────────────────────


def test_pending_lists_missing_and_placeholder_keys() -> None:
    missing, placeholder = boot.pending(
        ["A_KEY", "B_KEY", "C_KEY"], present={"A_KEY", "B_KEY"}, placeholders={"B_KEY"}
    )

    assert (missing, placeholder) == (["C_KEY"], ["B_KEY"])


def test_verify_reports_pending_secrets_without_reading_any_value(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    keys = _declared_keys()
    seen: list[list[str]] = []

    class Done:
        def __init__(self, stdout: str) -> None:
            self.returncode, self.stdout, self.stderr = 0, stdout, ""

    def fake_run(cmd, **kw):
        seen.append(cmd)
        if "get-parameters-by-path" not in cmd:
            return Done("")
        query = cmd[cmd.index("--query") + 1]
        if "Value" in query:  # los que siguen en placeholder
            return Done('["/hubara/nuevo/OPENROUTER_API_KEY"]')
        return Done("[" + ",".join(f'"/hubara/nuevo/{k}"' for k in keys if k != "TEMPORAL_API_KEY") + "]")

    monkeypatch.setattr(boot.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["aws_bootstrap.py", "verify", "--tenant", "nuevo"])

    with pytest.raises(SystemExit) as exit_info:
        boot.main()

    out = capsys.readouterr().err
    assert exit_info.value.code not in (0, None)
    assert "TEMPORAL_API_KEY" in out and "OPENROUTER_API_KEY" in out
    # Lo descifrado solo se compara dentro del CLI de AWS: al script llegan nombres.
    for cmd in seen:
        if "--with-decryption" in cmd:
            assert cmd[cmd.index("--query") + 1].endswith("].Name")
