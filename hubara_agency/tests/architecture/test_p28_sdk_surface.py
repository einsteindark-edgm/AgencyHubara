"""P-28 — lockdown de la superficie SDK (ratchet, ADR-2026-06-12 / F-SDK-0).

Dirección de dependencias post-SDK: ``plugins → sdk → platform``. Los plugins
NUEVOS importan ``src.sdk``; los imports ``src.platform.*`` pre-existentes en
``src/plugins/`` quedaron CONGELADOS en ``p28_platform_import_allowlist.txt``.

El gate exige IGUALDAD EXACTA entre el estado real y la allowlist, en ambas
direcciones:

- Import nuevo a ``src.platform`` desde un plugin → FALLA con el fix
  ("importá src.sdk; si el símbolo no está en el SDK, agregalo al SDK con su
  check — regla de oro").
- Entrada de la allowlist que ya no existe (drenaste un import) → FALLA
  pidiendo borrar la línea (mantiene la lista honesta y el progreso visible).

El estado solo puede MEJORAR (ratchet). Drenaje completo ⇒ la allowlist queda
vacía y este gate se convierte en prohibición dura.

Complementos duros (no-ratchet) en ``.importlinter``: ``sdk-no-plugins`` y
``platform-no-sdk``.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT: Path = Path(__file__).resolve().parents[2] / "src"
PLUGINS_ROOT: Path = SRC_ROOT / "plugins"
ALLOWLIST_PATH: Path = Path(__file__).parent / "p28_platform_import_allowlist.txt"


def _iter_plugin_files() -> list[Path]:
    return sorted(p for p in PLUGINS_ROOT.rglob("*.py") if p.is_file())


def _platform_imports_of(path: Path) -> set[str]:
    """Módulos ``src.platform[.x]`` que ``path`` importa (Import / ImportFrom).

    Cuenta TODO acople, incluido ``if TYPE_CHECKING`` y los imports locales
    dentro de funciones — el lockdown es del grafo, no solo del runtime.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "src.platform" or alias.name.startswith("src.platform."):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and (
                module == "src.platform" or module.startswith("src.platform.")
            ):
                found.add(module)
    return found


def scan_current_state() -> set[str]:
    """Estado real: ``{"src/plugins/<file>.py -> src.platform.<module>"}``."""
    hub_root = SRC_ROOT.parent
    entries: set[str] = set()
    for path in _iter_plugin_files():
        rel = path.relative_to(hub_root).as_posix()
        for module in _platform_imports_of(path):
            entries.add(f"{rel} -> {module}")
    return entries


def format_allowlist(entries: set[str] | None = None) -> str:
    """Render canónico de la allowlist (ordenada, con header)."""
    entries = scan_current_state() if entries is None else entries
    header = (
        "# P-28 — imports `src.platform.*` CONGELADOS en src/plugins/ (ratchet).\n"
        "# NO agregar líneas: código nuevo importa `src.sdk` (ADR-2026-06-12).\n"
        "# Borrar la línea correspondiente al drenar un import (el gate lo exige).\n"
        "# Regenerar SOLO al drenar: uv run python -m tests.architecture.test_p28_sdk_surface\n"
    )
    return header + "\n".join(sorted(entries)) + "\n"


def _load_allowlist() -> set[str]:
    lines = ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}


def test_p28_no_new_platform_imports_in_plugins() -> None:
    """Ningún import `src.platform.*` NUEVO en plugins — usá `src.sdk`."""
    current = scan_current_state()
    allowed = _load_allowlist()
    new = sorted(current - allowed)
    assert not new, (
        "P-28: imports `src.platform.*` NUEVOS en src/plugins/ — los plugins "
        "importan `src.sdk` (la fachada), no la implementación privada:\n  - "
        + "\n  - ".join(new)
        + "\nFix: importá el símbolo desde `src.sdk` / `src.sdk.<kit>`. Si no "
        "está en el SDK, agregalo AL SDK con su check (regla de oro, "
        "src/sdk/CLAUDE.md) — NO agregues la línea a la allowlist."
    )


def test_p28_allowlist_has_no_stale_entries() -> None:
    """Entradas drenadas se BORRAN de la allowlist (progreso visible)."""
    current = scan_current_state()
    allowed = _load_allowlist()
    stale = sorted(allowed - current)
    assert not stale, (
        "P-28: estas entradas de la allowlist ya NO existen en el código "
        "(¡drenaste imports! 🎉) — borralas de "
        f"{ALLOWLIST_PATH.name} para fijar el progreso:\n  - "
        + "\n  - ".join(stale)
    )


def test_p28_sdk_facade_importable() -> None:
    """La fachada importa limpio y expone la superficie núcleo."""
    import src.sdk as sdk

    for symbol in (
        "get_task_queue",
        "ensure_plugin_enabled",
        "validate_enabled",
        "load_manifest",
        "all_manifests",
        "resolve_route_workflow_id",
        "ApiModule",
        "WorkerModule",
    ):
        assert hasattr(sdk, symbol), f"src.sdk no expone {symbol!r}"


if __name__ == "__main__":
    # Regeneración de la allowlist (solo para DRENAR — el gate impide crecer):
    #   cd hubara_agency && uv run python -m tests.architecture.test_p28_sdk_surface
    ALLOWLIST_PATH.write_text(format_allowlist(), encoding="utf-8")
    print(f"allowlist regenerada: {ALLOWLIST_PATH} ({len(scan_current_state())} entries)")
