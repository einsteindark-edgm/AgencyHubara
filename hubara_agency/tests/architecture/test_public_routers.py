"""Guard: qué módulos pueden abrirse al público sin la auth del shell.

``src/main.py`` monta SIN ``require_auth`` cualquier módulo de router que
declare ``PUBLIC_ROUTER = True`` a nivel módulo. Es una línea: sin este guard,
un plugin podría exponer rutas al público por accidente y ningún gate lo
notaría. La allowlist es explícita y cada entrada dice quién llama al router y
con qué auth propia se protege (regla de oro: flag ⇒ check).

ADR: docs/adr/2026-09-04-public-router-allowlist.md
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PLUGINS_DIR = Path(__file__).resolve().parents[2] / "src" / "plugins"
_SRC_ROOT = _PLUGINS_DIR.parents[1]  # hubara_agency/
_FLAG = re.compile(r"^PUBLIC_ROUTER\s*=\s*True\b", re.MULTILINE)

# módulo → (quién lo llama, auth propia). Agregar una entrada acá es una
# decisión de arquitectura: exige auth propia en el módulo y review.
PUBLIC_ROUTER_ALLOWLIST: dict[str, str] = {
    "src.plugins.chats.api.sales": (
        "Meta (webhook de WhatsApp): GET verify_token + POST con HMAC X-Hub-Signature-256"
    ),
    "src.plugins.mba.api.connector": (
        "Meta Business Agent (connector tools /api/mba/tools/*): header X-API-Key = "
        "HUBARA_MBA_API_KEY, fail-closed (503 sin la variable, 401 sin key)"
    ),
}


def _public_router_modules() -> set[str]:
    found: set[str] = set()
    for py in sorted(_PLUGINS_DIR.rglob("*.py")):
        if _FLAG.search(py.read_text(encoding="utf-8")):
            found.add(".".join(py.relative_to(_SRC_ROOT).with_suffix("").parts))
    return found


@pytest.mark.architecture
def test_public_routers_are_exactly_the_allowlisted_ones() -> None:
    found = _public_router_modules()
    assert found == set(PUBLIC_ROUTER_ALLOWLIST), (
        "Módulos con PUBLIC_ROUTER=True fuera de la allowlist (o allowlist stale).\n"
        f"  encontrados: {sorted(found)}\n"
        f"  allowlist:   {sorted(PUBLIC_ROUTER_ALLOWLIST)}\n"
        "Un router público necesita su propia auth (firma, API key…) y una entrada "
        "en PUBLIC_ROUTER_ALLOWLIST con quién lo llama."
    )


@pytest.mark.architecture
def test_the_scanner_itself_sees_the_known_public_modules() -> None:
    """Self-test del detector: si el regex deja de matchear, el guard sería vacío."""
    assert "src.plugins.chats.api.sales" in _public_router_modules()
