"""El nombre de perfil de WhatsApp llega a la bandeja: el operador ve quién le escribe, no solo un número.

Meta manda el nombre de perfil del contacto en cada webhook (`value.contacts[].profile.name`). El ingest lo
descartaba, así que la bandeja de la app nativa solo mostraba el número y los incendios decían «Cliente pide
un humano» (`mobile_rules` lee `metadata.profile.name`, que en producción casi nunca estaba).

Contrato: el ingest guarda el nombre en `metadata.profile.name` (bajo el lock del store) y
`GET /api/dashboard/sessions` expone `customer_name` y `last_message_preview`, campos nuevos y opcionales.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.parsers import parse_whatsapp_inbound
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from src.plugins.chats.api import dashboard

_FROM = "000000000150"  # ceros: nunca un teléfono real
_SID = f"wa_{_FROM}"


def _webhook(text: str, *, contacts: list[dict[str, Any]] | None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "metadata": {"phone_number_id": "PID"},
        "messages": [{"id": "wamid.N1", "from": _FROM, "timestamp": "1790000000", "type": "text",
                      "text": {"body": text}}],
    }
    if contacts is not None:
        value["contacts"] = contacts
    return {"entry": [{"changes": [{"value": value}]}]}


class _History:
    def append_user_event(self, session_id: str, content: str, **kw: Any) -> None:
        pass


class _LoadOrStart:
    async def execute(self, *a: Any, **kw: Any) -> None:
        pass


def _ingest(vault: Path) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=_LoadOrStart(),  # type: ignore[arg-type]
        metadata_store=FilesystemMetadataStore(vault),  # type: ignore[arg-type]
    )


def _meta(vault: Path) -> dict[str, Any]:
    return json.loads((vault / _SID / "metadata.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_the_ingest_keeps_the_whatsapp_profile_name_of_whoever_writes(_isolate_vault_dir: Path) -> None:
    vault = _isolate_vault_dir
    contacts = [{"profile": {"name": "  Laura Prueba  "}, "wa_id": _FROM}]

    await _ingest(vault).execute(parse_whatsapp_inbound(_webhook("hola", contacts=contacts)))

    assert _meta(vault).get("profile", {}).get("name") == "Laura Prueba"


@pytest.mark.asyncio
async def test_a_new_profile_name_replaces_the_old_one_and_a_message_without_it_keeps_it(
    _isolate_vault_dir: Path,
) -> None:
    vault = _isolate_vault_dir
    FilesystemMetadataStore(vault).write(_SID, {"profile": {"name": "Lau", "otro": 1}, "tag": "INTERESADO"})

    await _ingest(vault).execute(parse_whatsapp_inbound(
        _webhook("hola", contacts=[{"profile": {"name": "Laura Prueba"}, "wa_id": _FROM}])))
    assert _meta(vault)["profile"] == {"name": "Laura Prueba", "otro": 1}

    await _ingest(vault).execute(parse_whatsapp_inbound(_webhook("¿y el precio?", contacts=None)))
    assert _meta(vault)["profile"]["name"] == "Laura Prueba"
    assert _meta(vault)["tag"] == "INTERESADO"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    app = FastAPI()
    app.include_router(dashboard.router, prefix="/api/dashboard")
    monkeypatch.setattr(dashboard, "_resolve_ad_names", lambda ids: {})
    with patch("src.plugins.chats.api.dashboard.WORKSPACE_VAULT_DIR", tmp_path):
        yield TestClient(app), tmp_path


def _seed(vault: Path, sid: str, metadata: dict[str, Any] | None, events: list[dict[str, Any]]) -> None:
    (vault / sid / "sessions").mkdir(parents=True, exist_ok=True)
    if metadata is not None:
        (vault / sid / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    (vault / sid / "sessions" / f"{sid}.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")


def test_the_session_list_brings_the_customer_name_and_a_preview_of_the_last_message(client) -> None:
    c, vault = client
    long = "Hola, quería saber si el Dúo Zodiacal viene con los dos signos o hay que pedir cada vela aparte"
    _seed(vault, "wa_000000000151", {"profile": {"name": "Laura Prueba"}}, [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "¡Hola! ¿En qué te ayudo?"},
        {"role": "user", "content": long},
        {"role": "tool", "content": "{}"},
    ])
    _seed(vault, "wa_000000000152", None, [
        {"role": "user", "content": "buenas"},
        {"role": "assistant", "content": None, "tools_used": ["present_products"]},
    ])

    by_id = {s["session_id"]: s for s in c.get("/api/dashboard/sessions").json()["sessions"]}

    laura = by_id["wa_000000000151"]
    assert laura.get("customer_name") == "Laura Prueba"
    preview = laura.get("last_message_preview") or ""
    assert preview.startswith("Hola, quería saber si el Dúo Zodiacal") and preview.endswith("…")
    assert len(preview) <= 81
    sin_nombre = by_id["wa_000000000152"]
    assert sin_nombre.get("customer_name") is None  # sin perfil: la app cae al número
    assert sin_nombre.get("last_message_preview") == "buenas"  # lo último con texto


def test_a_customer_without_phone_is_shown_by_name_not_by_their_meta_id(client) -> None:
    """Cliente con nombre de usuario de WhatsApp: su sesión es `wa_<id de Meta>`.
    La bandeja web usa `phone_number` como nombre de la conversación; con el id
    crudo (`CO1502576394655843`) el operador no sabría quién es."""
    c, vault = client
    _seed(vault, "wa_CO1502576394655843", {"profile": {"name": "Liliana"}}, [{"role": "user", "content": "hola"}])
    _seed(vault, "wa_US9000000000003843", None, [{"role": "user", "content": "hi"}])

    by_id = {s["session_id"]: s for s in c.get("/api/dashboard/sessions").json()["sessions"]}

    assert by_id["wa_CO1502576394655843"]["phone_number"] == "Liliana (sin teléfono)"
    assert by_id["wa_US9000000000003843"]["phone_number"] == "Cliente sin teléfono ···3843"
    assert c.get("/api/dashboard/sessions/wa_CO1502576394655843").json()["phone_number"] == "Liliana (sin teléfono)"
