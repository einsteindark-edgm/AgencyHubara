"""Plantillas con encabezado IMAGE en el CLI de provisioning.

Meta exige, al CREAR una plantilla con foto en el encabezado, una foto de
ejemplo subida con la Resumable Upload API (dos pasos: abrir sesión de subida
en la app → mandar los bytes → `h` = header_handle). La foto real se elige en
cada envío; esta es solo para la revisión de Meta. Solo stdlib + pytest; la red
se reemplaza por grabadores."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "whatsapp_provision.py"
_spec = importlib.util.spec_from_file_location("whatsapp_provision", _SCRIPT)
wp = importlib.util.module_from_spec(_spec)
sys.modules["whatsapp_provision"] = wp
_spec.loader.exec_module(wp)

CFG = {"APP_ID": "3662", "WABA_ID": "1763", "SYSTEM_USER_TOKEN": "EAA-token"}

PHOTO_DEF = {
    "name": "order_ready_photo_utility_v1",
    "category": "UTILITY",
    "language": "es_CO",
    "header": {"format": "IMAGE", "example_file": "assets/order_ready_example.jpg"},
    "body": "Hola, tu pedido {{1}} ya está listo.",
    "example": ["#31"],
}
TEXT_DEF = {
    "name": "human_followup_utility_v1",
    "category": "UTILITY",
    "language": "es_CO",
    "body": "Hola, seguimiento a tu consulta {{1}}.",
    "example": ["de la vela"],
}


class _Resp:
    status = 200

    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


def test_resumable_upload_opens_session_on_the_app_then_sends_bytes(monkeypatch, tmp_path) -> None:
    photo = tmp_path / "ejemplo.jpg"
    photo.write_bytes(b"\xff\xd8\xff-fake-jpeg")
    api_calls = []

    def fake_api(method, path, token, json_body=None, **fields):
        api_calls.append((method, path, fields))
        return 200, {"id": "upload:MTph?sig=ARZ"}

    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["data"] = req.data
        return _Resp({"h": "4::HANDLE"})

    monkeypatch.setattr(wp, "_api", fake_api)
    monkeypatch.setattr(wp.urllib.request, "urlopen", fake_urlopen)

    handle = wp._resumable_upload(CFG, str(photo))

    assert handle == "4::HANDLE"
    [(method, path, fields)] = api_calls
    assert (method, path) == ("POST", "3662/uploads")
    assert fields["file_length"] == str(len(photo.read_bytes()))
    assert fields["file_type"] == "image/jpeg"
    # El id de la sesión (con su `?sig=`) va tal cual en la URL, sin re-encodear.
    assert seen["url"].endswith("/upload:MTph?sig=ARZ")
    assert seen["headers"]["authorization"] == "OAuth EAA-token"
    assert seen["headers"]["file_offset"] == "0"
    assert seen["data"] == photo.read_bytes()


def _submit(monkeypatch, defs):
    posts, uploads = [], []
    monkeypatch.setattr(wp, "_load_defs", lambda name: defs)
    monkeypatch.setattr(wp, "actual_templates", lambda cfg: [])
    monkeypatch.setattr(
        wp, "_resumable_upload", lambda cfg, path: uploads.append(path) or "4::HANDLE"
    )
    monkeypatch.setattr(
        wp,
        "_api",
        lambda method, path, token, json_body=None, **f: posts.append(json_body) or (200, {"id": "9"}),
    )
    with redirect_stdout(io.StringIO()):
        wp.step_templates(CFG)
    return posts, uploads


def test_photo_template_is_submitted_with_image_header_and_example_handle(monkeypatch) -> None:
    posts, uploads = _submit(monkeypatch, [PHOTO_DEF])

    [payload] = posts
    assert payload["components"][0] == {
        "type": "HEADER",
        "format": "IMAGE",
        "example": {"header_handle": ["4::HANDLE"]},
    }
    assert payload["components"][1]["type"] == "BODY"
    # La foto de ejemplo se resuelve relativa a definitions/.
    [path] = uploads
    assert path.endswith("definitions/assets/order_ready_example.jpg")


def test_text_template_has_no_header_and_uploads_nothing(monkeypatch) -> None:
    posts, uploads = _submit(monkeypatch, [TEXT_DEF])

    [payload] = posts
    assert [c["type"] for c in payload["components"]] == ["BODY"]
    assert uploads == []


def test_failed_example_upload_skips_the_template_instead_of_submitting_it_broken(monkeypatch) -> None:
    posts = []
    monkeypatch.setattr(wp, "_load_defs", lambda name: [PHOTO_DEF])
    monkeypatch.setattr(wp, "actual_templates", lambda cfg: [])
    monkeypatch.setattr(wp, "_resumable_upload", lambda cfg, path: None)
    monkeypatch.setattr(
        wp, "_api", lambda method, path, token, json_body=None, **f: posts.append(json_body) or (200, {})
    )
    out = io.StringIO()
    with redirect_stdout(out):
        wp.step_templates(CFG)

    assert posts == []
    assert "order_ready_photo_utility_v1" in out.getvalue()
