"""Central única de la Graph API de Meta (auditoría CAPI 2026-09-08, punto 5).

Antes: 6 lugares en ``src/`` con host + versión hardcodeados (v18 en CAPI,
v21 en WhatsApp, v23 en catálogo/media/ad-names, v25 en ads). Cada desarrollo
elegía su versión; v18 venció el 2026-01-26 y solo sobrevivía por el
auto-upgrade silencioso de Meta.

Después: ``src.platform.meta.graph`` es la ÚNICA fuente de host y versión;
todo consumidor construye URLs con ``graph_url``. Este módulo es la guarda
(ratchet): ninguna otra parte de ``src/`` ni ``scripts/`` puede volver a
escribir el host ni definir su propia versión.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.platform.meta.graph import (
    META_GRAPH_API_VERSION,
    META_GRAPH_BASE_URL,
    graph_url,
    resolve_graph_api_version,
)

_HUBARA = Path(__file__).resolve().parents[2]
_CENTRAL = _HUBARA / "src" / "platform" / "meta" / "graph.py"


class TestGraphUrlBuilder:
    def test_builds_versioned_path_from_segments(self) -> None:
        url = graph_url("1704018554189395", "events")
        assert url == f"{META_GRAPH_BASE_URL}/{META_GRAPH_API_VERSION}/1704018554189395/events"

    def test_default_version_is_a_live_meta_version(self) -> None:
        # v18 venció el 2026-01-26; v25 salió el 2026-02-18. El default tiene
        # que ser >= v25 para no depender del auto-upgrade de Meta.
        major = int(META_GRAPH_API_VERSION.removeprefix("v").split(".")[0])
        assert major >= 25

    def test_keeps_format_placeholders_for_template_constants(self) -> None:
        template = graph_url("{phone_number_id}", "messages")
        assert template.format(phone_number_id="123") == (
            f"{META_GRAPH_BASE_URL}/{META_GRAPH_API_VERSION}/123/messages"
        )

    def test_strips_stray_slashes_and_rejects_empty_segments(self) -> None:
        assert graph_url("/act_1/", "insights/") == graph_url("act_1", "insights")
        with pytest.raises(ValueError):
            graph_url("")

    def test_root_url_has_no_trailing_slash(self) -> None:
        assert graph_url() == f"{META_GRAPH_BASE_URL}/{META_GRAPH_API_VERSION}"

    def test_explicit_version_override_per_call(self) -> None:
        assert graph_url("me", version="v24.0") == f"{META_GRAPH_BASE_URL}/v24.0/me"

    def test_env_override_must_look_like_a_meta_version(self, monkeypatch) -> None:
        monkeypatch.setenv("META_GRAPH_API_VERSION", "v26.0")
        assert resolve_graph_api_version() == "v26.0"
        monkeypatch.setenv("META_GRAPH_API_VERSION", "26")
        with pytest.raises(ValueError, match="META_GRAPH_API_VERSION"):
            resolve_graph_api_version()


class TestConsumersUseTheCentral:
    def test_capi_url_derives_from_central(self) -> None:
        from src.platform.whatsapp.capi import META_CAPI_API_URL

        assert META_CAPI_API_URL == graph_url("{dataset_id}", "events")

    def test_whatsapp_send_and_media_urls_derive_from_central(self) -> None:
        from src.platform.config import WHATSAPP_API_URL, WHATSAPP_MEDIA_API_URL

        assert WHATSAPP_API_URL == graph_url("{phone_number_id}", "messages")
        assert WHATSAPP_MEDIA_API_URL == graph_url("{phone_number_id}", "media")

    def test_sdk_exposes_graph_helpers_to_plugins(self) -> None:
        # Plugins (ads) no pueden importar src.platform (P-28): la central
        # viaja por el ConnectorKit, lazy como el resto del kit.
        from src.sdk import connectorkit

        assert connectorkit.graph_url is graph_url
        assert connectorkit.META_GRAPH_API_VERSION == META_GRAPH_API_VERSION


_HOST_RE = re.compile(r"graph\.facebook\.com")
_OWN_VERSION_RE = re.compile(r"^\s*_?GRAPH(_API)?_VERSION\s*=\s*['\"]v\d", re.MULTILINE)


def _offenders(pattern: re.Pattern[str], roots: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for root in roots:
        for path in sorted((_HUBARA / root).rglob("*.py")):
            if path == _CENTRAL or "__pycache__" in path.parts:
                continue
            if pattern.search(path.read_text(encoding="utf-8")):
                found.append(str(path.relative_to(_HUBARA)))
    return found


class TestSingleSourceRatchet:
    def test_no_other_module_writes_the_graph_host(self) -> None:
        offenders = _offenders(_HOST_RE, ("src", "scripts"))
        assert offenders == [], (
            "Host de la Graph API hardcodeado fuera de la central "
            f"src/platform/meta/graph.py: {offenders}. Usá graph_url()."
        )

    def test_no_other_module_defines_its_own_graph_version(self) -> None:
        offenders = _offenders(_OWN_VERSION_RE, ("src", "scripts"))
        assert offenders == [], (
            "Versión de la Graph API definida fuera de la central: "
            f"{offenders}. La versión vive SOLO en META_GRAPH_API_VERSION."
        )
