"""P-31 — aislamiento de vendors (ratchet, F-SDK-4 / ADR-2026-06-12 §7).

Los plugins consumen PORTS (``src.sdk.connectorkit``) — jamás módulos de
vendor. El nombre del vendor (Medusa, Meta, …) es un detalle de deployment:
cambiar de vendor = otro adapter detrás del mismo port, CERO cambios en
plugins.

Namespaces de vendor congelados (los adapters aún viven mezclados con los
ports en platform — su mudanza física a ``platform/connectors/<vendor>/`` es
F-SDK-4b; este gate impide que el acople CREZCA mientras tanto):

- ``src.platform.medusa[.*]``        — client crudo Medusa
- ``src.platform.meta_catalog[.*]``  — client del Commerce Catalog de Meta
- ``src.platform.*.medusa_*``        — adapters Medusa por capability
  (``orders.medusa_order_query``, ``catalog.medusa_checkout``, …)

Misma mecánica ratchet que P-28: igualdad EXACTA con la allowlist en ambas
direcciones (nuevo → rojo con fix; drenado → rojo pidiendo borrar la línea).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SRC_ROOT: Path = Path(__file__).resolve().parents[2] / "src"
PLUGINS_ROOT: Path = SRC_ROOT / "plugins"
ALLOWLIST_PATH: Path = Path(__file__).parent / "p31_vendor_import_allowlist.txt"

_VENDOR_MODULE_RE = re.compile(
    r"^src\.platform\.(?:medusa|meta_catalog)(?:\.|$)"
    r"|^src\.platform\.\w+\.medusa_\w+$"
)


def _vendor_imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _VENDOR_MODULE_RE.match(alias.name):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and _VENDOR_MODULE_RE.match(module):
                found.add(module)
    return found


def scan_current_state() -> set[str]:
    hub_root = SRC_ROOT.parent
    entries: set[str] = set()
    for path in sorted(PLUGINS_ROOT.rglob("*.py")):
        rel = path.relative_to(hub_root).as_posix()
        for module in _vendor_imports_of(path):
            entries.add(f"{rel} -> {module}")
    return entries


def format_allowlist(entries: set[str] | None = None) -> str:
    entries = scan_current_state() if entries is None else entries
    header = (
        "# P-31 — imports de VENDOR congelados en src/plugins/ (ratchet).\n"
        "# NO agregar líneas: los plugins consumen ports (src.sdk.connectorkit).\n"
        "# Borrar la línea correspondiente al drenar (el gate lo exige).\n"
        "# Regenerar SOLO al drenar: uv run python -m tests.architecture.test_p31_vendor_isolation\n"
    )
    return header + "\n".join(sorted(entries)) + "\n"


def _load_allowlist() -> set[str]:
    lines = ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}


def test_p31_no_new_vendor_imports_in_plugins() -> None:
    """Ningún import de vendor NUEVO en plugins — consumí el port del SDK."""
    new = sorted(scan_current_state() - _load_allowlist())
    assert not new, (
        "P-31: imports de VENDOR nuevos en src/plugins/ — el vendor es un "
        "detalle de deployment, los plugins consumen ports:\n  - "
        + "\n  - ".join(new)
        + "\nFix: usá el port desde `src.sdk.connectorkit` (OrderQueryPort, "
        "CatalogPort, ...). Si la capability no tiene port, se agrega AL SDK "
        "con su fake + contract suite (docs/_sdk/07-connectorkit.md)."
    )


def test_p31_allowlist_has_no_stale_entries() -> None:
    """Entradas drenadas se BORRAN (progreso visible y monotónico)."""
    stale = sorted(_load_allowlist() - scan_current_state())
    assert not stale, (
        "P-31: estas entradas ya no existen (¡drenaste vendor coupling! 🎉) — "
        f"borralas de {ALLOWLIST_PATH.name}:\n  - " + "\n  - ".join(stale)
    )


def test_p31_connectorkit_exposes_all_ports() -> None:
    """La fachada del kit expone los 9 ports + fakes de atribución."""
    import src.sdk.connectorkit as ck

    for symbol in (
        "OrderQueryPort",
        "OrderCommandPort",
        "OrderRegistrationPort",
        "CatalogPort",
        "CheckoutVerificationPort",
        "MetaCatalogPort",
        "AudioTranscriptionPort",
        "ImageVisionPort",
        "CustomerScoringPort",
        "AttributionReadPort",
        "FilesystemAttributionStore",
        "InMemoryAttributionStore",
    ):
        assert hasattr(ck, symbol), f"connectorkit no expone {symbol!r}"


if __name__ == "__main__":
    ALLOWLIST_PATH.write_text(format_allowlist(), encoding="utf-8")
    print(f"allowlist regenerada: {ALLOWLIST_PATH} ({len(scan_current_state())} entries)")
