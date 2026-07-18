"""Guard L-18: plugin que escribe en conversaciones ⇒ `depends_on: [chats]`.

La clase de bug (hermana de L-17 "integración sin costura era invisible"):
un plugin que manda mensajes a sesiones WhatsApp (templates/free-form vía
las activities de platform) integra FUNCIONALMENTE con chats — las
respuestas del cliente las maneja el ingest de chats (incluido el opt-out
de marketing, que ahí se cumple). Si el manifest no declara `depends_on:
[chats]`:

  1. Acktos Studio dibuja el plugin como ISLA (el edge plugin→plugin sale
     de `depends_on` — builder.py) → la integración es invisible en el mapa.
  2. Un deploy con chats deshabilitado rompe la promesa del copy (nadie
     procesa "NO MÁS") sin que ningún gate lo cace (P-6 protege solo lo
     declarado).

Caso visto: plugin `marketing` (2026-07-18) — enviaba campañas pero declaró
`depends_on: []`; eta y reengagement lo declaraban bien. La regla vivía en
prosa; este guard la vuelve determinística.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_HUBARA_ROOT = Path(__file__).resolve().parents[2]
_PLUGINS_CODE = _HUBARA_ROOT / "src" / "plugins"
_MANIFESTS = _HUBARA_ROOT.parent / "frontend_dashboard" / "src" / "plugins"

#: Símbolos que escriben en una conversación de un cliente (platform/SDK).
_SESSION_SEND_SYMBOLS = (
    "send_whatsapp_template_activity",
    "send_template_to_session",
    "send_whatsapp_message_activity",
    "send_message_to_session",
)


def _plugins_that_send_to_sessions() -> set[str]:
    hits: set[str] = set()
    for py in _PLUGINS_CODE.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(sym in text for sym in _SESSION_SEND_SYMBOLS):
            hits.add(py.relative_to(_PLUGINS_CODE).parts[0])
    return hits


def test_plugins_que_escriben_sesiones_declaran_depends_on_chats() -> None:
    offenders: list[str] = []
    for plugin_id in sorted(_plugins_that_send_to_sessions()):
        if plugin_id == "chats":
            continue  # dueño de la conversación
        manifest_path = _MANIFESTS / plugin_id / "plugin.yaml"
        assert manifest_path.exists(), f"{plugin_id}: sin plugin.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        deps = manifest.get("depends_on") or []
        if "chats" not in deps:
            offenders.append(plugin_id)
    assert not offenders, (
        f"Plugins que escriben en sesiones WhatsApp sin declarar "
        f"`depends_on: [chats]` en su manifest: {offenders}. Sin la "
        "declaración, Acktos Studio los dibuja como isla (integración "
        "invisible) y un deploy sin chats rompe el manejo de respuestas/"
        "opt-out. Declaralo con un comentario del porqué (patrón: eta, "
        "reengagement)."
    )


def test_el_guard_se_caza_a_si_mismo() -> None:
    """Self-test del detector (caso NEGATIVO primero, ley TDD de gates):
    el scan debe encontrar al menos a chats + eta + marketing — si el scan
    se rompe (paths, símbolos renombrados), este assert lo delata en vez de
    dejar el guard pasando en verde vacío."""
    hits = _plugins_that_send_to_sessions()
    assert {"chats", "eta", "marketing"} <= hits, hits
