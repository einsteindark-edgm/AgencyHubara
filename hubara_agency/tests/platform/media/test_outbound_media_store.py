"""Tests de `persist_outbound_image` — fotos que el operador humano manda.

Simétrico a `persist_inbound_image` pero para el sentido saliente: el operador
sube una foto desde el dashboard/app y la guardamos en el MISMO vault para que
el histórico del chat la pueda re-renderizar (el endpoint `GET /media/...` ya
existente la sirve sin cambios). A diferencia del inbound (keyed por el
`media_id` de Meta), acá el nombre es un token opaco que generamos nosotros —
NO es idempotente por media_id porque no lo tenemos hasta subir a Meta.
"""
from __future__ import annotations

import pytest

import src.platform.media.store as media_store


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(media_store, "WORKSPACE_VAULT_DIR", tmp_path, raising=True)
    return tmp_path


def test_persist_outbound_then_resolve_roundtrip(vault):
    filename = media_store.persist_outbound_image(
        "wa_573001", b"jpeg-bytes", "image/jpeg", token="abc123"
    )
    # Prefijo `out-` distingue salientes de entrantes en el mismo dir.
    assert filename.startswith("out-")
    assert filename.endswith(".jpg")

    path = media_store.resolve_media_file("wa_573001", filename)
    assert path is not None
    assert path.read_bytes() == b"jpeg-bytes"
    assert path == vault / "wa_573001" / "media" / filename


def test_persist_outbound_png_extension(vault):
    filename = media_store.persist_outbound_image(
        "wa_1", b"png", "image/png", token="t1"
    )
    assert filename.endswith(".png")


def test_persist_outbound_served_url_is_dashboard_endpoint(vault):
    filename = media_store.persist_outbound_image(
        "wa_1", b"x", "image/jpeg", token="t2"
    )
    assert (
        media_store.media_url_for("wa_1", filename)
        == f"/api/dashboard/media/wa_1/{filename}"
    )


def test_persist_outbound_token_is_sanitized(vault):
    # El token lo generamos nosotros (uuid), pero defendemos igual: nada de
    # `/` ni `..` en el filename resultante.
    filename = media_store.persist_outbound_image(
        "wa_1", b"x", "image/jpeg", token="../evil/../x"
    )
    assert "/" not in filename
    assert ".." not in filename
    assert media_store.resolve_media_file("wa_1", filename) is not None
