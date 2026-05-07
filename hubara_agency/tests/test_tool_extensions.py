"""Test del mecanismo `register_tool_extension` / `apply_tool_extensions`.

Confirma que la factory recibe el workspace_path correcto cuando se
aplica una extension registrada — esto sostiene el patrón OCP sin que
`platform/` conozca tools de dominios específicos.
"""
from __future__ import annotations

from pathlib import Path


def test_tool_extensions_apply_after_register(tmp_path: Path) -> None:
    """Sanity: el registry-port aplica las extensions registradas (NEW-5).

    Usa un fake Tool minimalista para no depender de las tools reales del
    dominio. Confirma que `apply_tool_extensions` invoca la factory con el
    workspace_path correcto.
    """
    from src.platform.tool_extensions import (
        apply_tool_extensions,
        clear_tool_extensions,
        register_tool_extension,
    )

    captured: dict[str, Path] = {}

    class _FakeTool:
        name = "fake_tool"

        def __init__(self, workspace: Path) -> None:
            captured["workspace"] = workspace

    class _FakeRegistry:
        def __init__(self) -> None:
            self.registered: list[object] = []

        def register(self, tool: object) -> None:
            self.registered.append(tool)

    clear_tool_extensions()
    try:
        register_tool_extension("fake.test", lambda ws: _FakeTool(ws))
        registry = _FakeRegistry()
        apply_tool_extensions(registry, tmp_path)  # type: ignore[arg-type]
        assert captured["workspace"] == tmp_path
        assert len(registry.registered) == 1
    finally:
        clear_tool_extensions()
