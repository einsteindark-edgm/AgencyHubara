"""Guard único del ``session_id`` que llega por URL al dashboard.

Hallazgo de endurecimiento (lectura de código, 2026-09-18; NO explotado):
``GET /api/dashboard/sessions/{session_id}`` armaba rutas bajo el vault con el
segmento crudo de la URL. ``..`` resuelve al directorio PADRE del vault y la
respuesta era un 200 con forma de sesión — incluyendo ``memory/MEMORY.md`` y
``metadata.json`` del padre si existieran — en vez de un 400. El endpoint vive
detrás de ``require_auth``, así que el riesgo es bajo, pero es una lectura de
filesystem guiada por input sin guard.

Qué llega DE VERDAD al handler por HTTP (verificado contra Starlette + httpx):

* ``%2E%2E`` → ``..`` y ``%2E`` → ``.`` (el vault mismo): un solo segmento,
  pasa el convertor ``[^/]+`` de la ruta.
* ``wa_1%0A`` → ``wa_1\\n``: el ``$`` de ``re.match`` acepta ese salto final.
* ``_analytics``: directorio real del vault que NO es una sesión.
* Las variantes con ``/`` (``..%2Fx``) las corta el router con 404 y el ``..``
  crudo lo normaliza el cliente HTTP: nunca llegan, pero el handler igual las
  rechaza (defensa en profundidad si la ruta pasara a ``{session_id:path}``).

Contrato: id inválido → 400 sin tocar el filesystem; las formas reales
(``wa_<dígitos>`` del ``from`` de Meta, ``wa_+<dígitos>`` del vault local, ids
de test con guion bajo) siguen abriendo — la bandeja lista directorios ``wa_*``
y cada uno de esos ids debe abrir su detalle.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.plugins.chats.api import dashboard as dashboard_api

_CANARY = "CANARIO-FUERA-DEL-VAULT"

# Seeds commiteados del vault (`hubara_agency/hubara_vault/wa_*`): las formas
# REALES de id que la bandeja lista. Se leen solo los NOMBRES de directorio.
_SEED_VAULT = Path(__file__).resolve().parents[3] / "hubara_vault"
_SEED_SESSION_IDS = (
    sorted(p.name for p in _SEED_VAULT.glob("wa_*") if p.is_dir())
    if _SEED_VAULT.is_dir()
    else []
)


@pytest.fixture
def vault(tmp_path, monkeypatch) -> Path:
    """Mismo arnés que `tests/test_reply_quote_bot_message.py` (parchea
    `dashboard.WORKSPACE_VAULT_DIR`), con el vault como SUBDIRECTORIO de
    tmp_path: así `..` tiene un padre controlado donde plantar el canario."""
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "test_phone")
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    monkeypatch.setattr(dashboard_api, "WORKSPACE_VAULT_DIR", vault_dir)
    return vault_dir


@pytest.fixture
def client(vault) -> TestClient:
    from src.main import app

    return TestClient(app)


def _plant_canary_outside_vault(vault: Path) -> None:
    """Lo que el handler leería del PADRE del vault si `..` pasara."""
    parent = vault.parent
    (parent / "memory").mkdir()
    (parent / "memory" / "MEMORY.md").write_text(_CANARY, encoding="utf-8")
    (parent / "metadata.json").write_text(
        json.dumps({"tag": _CANARY, "motivo": _CANARY}), encoding="utf-8"
    )


# ── El hallazgo, reproducido ─────────────────────────────────────────────────


def test_dotdot_session_id_is_rejected_and_never_reads_the_vault_parent(
    client, vault
):
    _plant_canary_outside_vault(vault)

    resp = client.get("/api/dashboard/sessions/%2E%2E")

    assert resp.status_code == 400
    assert _CANARY not in resp.text


# ── Lo que llega por HTTP ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "encoded",
    [
        "%2E%2E",  # `..`  → padre del vault
        "%2E",  # `.`   → el vault mismo
        "wa_1%0A",  # `wa_1\n` → el `$` de re.match acepta el salto final
        "_analytics",  # dir real del vault que no es sesión
    ],
)
def test_invalid_session_id_over_http_returns_400(client, vault, encoded):
    (vault / "_analytics").mkdir()
    (vault / "wa_1").mkdir()

    resp = client.get(f"/api/dashboard/sessions/{encoded}")

    assert resp.status_code == 400


@pytest.mark.parametrize("encoded", ["%2E%2E%2Fx", "wa_1%2F..%2F..%2Fetc"])
def test_slash_variants_never_return_a_session(client, vault, encoded):
    """El router ya las corta (404); si algún día llegan al handler, 400."""
    _plant_canary_outside_vault(vault)

    resp = client.get(f"/api/dashboard/sessions/{encoded}")

    assert resp.status_code in (400, 404)
    assert _CANARY not in resp.text


# ── El handler, sin el router delante (defensa en profundidad) ───────────────


@pytest.mark.parametrize(
    "session_id",
    [
        "..",
        "../x",
        "wa_1/../../etc",
        "",
        "wa_1\n",
        ".",
        "_analytics",
        "wa_",  # prefijo sin cuerpo
        "wa_1/x",
        "wa_1\\..\\x",
        "wa_a.b",  # el charset no puede ni expresar `..`
        "wa_1 ",
        "wa_" + "1" * 300,  # segmento de path desmedido
    ],
)
async def test_handler_rejects_invalid_session_id_with_400(vault, session_id):
    (vault / "_analytics").mkdir()

    with pytest.raises(HTTPException) as exc:
        await dashboard_api.get_session_history(session_id)

    assert exc.value.status_code == 400


# ── Las formas reales siguen abriendo (no romper la bandeja) ─────────────────


@pytest.mark.parametrize(
    "session_id",
    [
        "wa_15550001111",  # prod: `wa_` + el `from` de Meta (solo dígitos)
        "wa_+15550001111",  # vault local: prefijo E.164 con `+`
        "wa_test_enum",  # sesiones de test con guion bajo
        "wa_Q1",
    ],
)
def test_valid_session_id_shapes_still_open(client, vault, session_id):
    (vault / session_id).mkdir()

    # Sin encodeURIComponent, igual que `entities/session/api.ts` del frontend.
    resp = client.get(f"/api/dashboard/sessions/{session_id}")

    assert resp.status_code == 200
    assert resp.json()["session_id"] == session_id


def test_every_session_the_inbox_lists_from_the_seed_vault_opens(client, vault):
    """Listado ↔ detalle: todo `wa_*` sembrado que la bandeja lista, abre."""
    if not _SEED_SESSION_IDS:
        pytest.skip("hubara_vault/ sin seeds wa_* en este checkout")
    for session_id in _SEED_SESSION_IDS:
        (vault / session_id).mkdir()

    listed = [
        s["session_id"] for s in client.get("/api/dashboard/sessions").json()["sessions"]
    ]

    assert sorted(listed) == _SEED_SESSION_IDS
    for session_id in listed:
        resp = client.get(f"/api/dashboard/sessions/{session_id}")
        assert resp.status_code == 200, session_id


# ── Las rutas de ESCRITURA del handoff usan el mismo guard ───────────────────
#
# `handoff.py` validaba con `is_safe_segment`, que acepta `.` (el vault mismo),
# `_analytics` y `wa_1\n` (mismo `$` + `re.match`). Con `.`, `intervene`
# escribía un `metadata.json` en la RAÍZ del vault.


@pytest.fixture
def handoff_client(vault, monkeypatch) -> TestClient:
    """Arnés de `tests/test_handoff_endpoints.py`: los stores del composition
    sobre el vault temporal, y NADA de Temporal / Meta real."""
    import src.plugins.chats.api.dashboard_composition as comp
    from src.plugins.chats.api import handoff as handoff_api

    monkeypatch.setattr(comp, "WORKSPACE_VAULT_DIR", vault)
    monkeypatch.setattr(comp, "_METADATA_STORE", None)
    monkeypatch.setattr(comp, "_HISTORY_STORE", None)
    monkeypatch.setattr(
        handoff_api, "get_temporal_client", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        handoff_api, "terminate_session_workflows", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(handoff_api, "upload_media", AsyncMock(return_value="media-x"))
    from src.main import app

    return TestClient(app)


def test_dot_session_id_cannot_write_metadata_at_the_vault_root(
    handoff_client, vault
):
    resp = handoff_client.post("/api/dashboard/sessions/%2E/intervene", json={})

    assert resp.status_code == 400
    assert not (vault / "metadata.json").exists()


_HANDOFF_POSTS = [
    ("intervene", {"json": {}}),
    ("messages", {"json": {"text": "hola"}}),
    ("template-messages", {"json": {"template_name": "cualquiera"}}),
    ("return-to-bot", {"json": {"target_route": "ventas"}}),
    ("media", {"files": {"file": ("f.jpg", b"\xff\xd8\xffx", "image/jpeg")}}),
]


@pytest.mark.parametrize("encoded", ["%2E", "wa_1%0A", "_analytics"])
@pytest.mark.parametrize(
    "route, kwargs", _HANDOFF_POSTS, ids=[route for route, _ in _HANDOFF_POSTS]
)
def test_handoff_routes_reject_invalid_session_id(
    handoff_client, route, kwargs, encoded
):
    resp = handoff_client.post(
        f"/api/dashboard/sessions/{encoded}/{route}", **kwargs
    )

    assert resp.status_code == 400


def test_handoff_still_accepts_the_e164_plus_form(handoff_client, vault):
    resp = handoff_client.post(
        "/api/dashboard/sessions/wa_+15550001111/intervene", json={}
    )

    assert resp.status_code == 200
    saved = json.loads(
        (vault / "wa_+15550001111" / "metadata.json").read_text(encoding="utf-8")
    )
    assert saved["active_route"] == "humano"
