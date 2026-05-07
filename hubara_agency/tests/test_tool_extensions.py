"""Tests de F8: contratos `typing.Protocol` para los puertos de infraestructura.

Verifica que los adapters default cumplan los Protocols con
`@runtime_checkable`. Si en el futuro alguien agrega un metodo al Port pero
olvida implementarlo en el adapter, `isinstance(impl, Port)` devolvera False
y este test fallara.

Tambien chequea, como sanity check del cierre de NEW-5, que el mecanismo de
extension de tools funcione sin importar `src.domains.*` desde `core`.
"""
from __future__ import annotations

from pathlib import Path

from src.core.infrastructure.adapters import (
    DefaultBrainLoader,
    DefaultToolRegistry,
    DefaultWhatsAppGateway,
)
from src.core.ports import BrainLoaderPort, ToolRegistryPort, WhatsAppGatewayPort


def test_default_brain_loader_satisfies_port() -> None:
    impl = DefaultBrainLoader()
    assert isinstance(impl, BrainLoaderPort)


def test_default_whatsapp_gateway_satisfies_port() -> None:
    impl = DefaultWhatsAppGateway()
    assert isinstance(impl, WhatsAppGatewayPort)


def test_default_tool_registry_satisfies_port() -> None:
    impl = DefaultToolRegistry()
    assert isinstance(impl, ToolRegistryPort)


def test_brain_loader_returns_empty_list_for_missing_dir(tmp_path: Path) -> None:
    """El adapter debe degradar grácil: dir vacío => list vacía."""
    impl = DefaultBrainLoader()
    out = impl.load(tmp_path)
    assert out == []


def test_brain_loader_reads_existing_files(tmp_path: Path) -> None:
    (tmp_path / "identity.md").write_text("hello identity", encoding="utf-8")
    (tmp_path / "knowledge.md").write_text("hello knowledge", encoding="utf-8")
    impl = DefaultBrainLoader()
    out = impl.load(tmp_path)
    assert "hello identity" in out
    assert "hello knowledge" in out


def test_tool_extensions_apply_after_register(tmp_path: Path) -> None:
    """Sanity: el registry-port aplica las extensions registradas (NEW-5).

    Usa un fake Tool minimalista para no depender de las tools reales del
    dominio. Confirma que `apply_tool_extensions` invoca la factory con el
    workspace_path correcto.
    """
    from src.core.tool_extensions import (
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
