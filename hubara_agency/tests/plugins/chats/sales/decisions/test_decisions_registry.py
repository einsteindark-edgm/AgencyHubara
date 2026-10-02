"""Un solo punto decide de dónde sale cada capacidad (PAQUETES_DE_DECISION.md §10.1, F2).

Los lugares que preguntan (ingest, tools, egreso, remarketing, abandono…)
nombran SOLO la decisión: `capability("baja")`. El resolutor la toma del
paquete activo si la trae y, si no, de su clase (mientras dure la
migración). El paquete activo viene de la configuración de la tienda
(`SALES_DECISIONS_BUNDLE`, que nace en Terraform), no del código: cambiar de
tienda o de versión es un solo punto.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.decisions.registry import capability

SALES = Path(registry.__file__).resolve().parents[1]
CHATS = SALES.parents[1]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv("SALES_DECISIONS_BUNDLE", raising=False)
    registry.reset()
    yield
    registry.reset()


def test_a_migrated_capability_comes_from_the_bundle_and_the_rest_from_their_class() -> None:
    from src.plugins.chats.agent.sales.decisions.capabilities.lecturas import Compra

    assert capability("baja").bundle == "ventas@1"
    assert capability("cortesia").bundle == "ventas@1"
    assert isinstance(capability("compra"), Compra)
    assert getattr(capability("compra"), "bundle", "") == ""


def test_the_same_name_is_the_same_object() -> None:
    assert capability("baja") is capability("baja")
    assert capability("compra") is capability("compra")


def test_an_unknown_capability_says_which_exist() -> None:
    with pytest.raises(KeyError, match="no existe la capacidad 'bajas'"):
        capability("bajas")


def test_the_store_configuration_chooses_the_bundle(tmp_path: Path, monkeypatch) -> None:
    """Otra tienda (otra carpeta de paquete) con otra pregunta de baja."""
    bundles = tmp_path / "bundles"
    shutil.copytree(registry.BUNDLES_DIR, bundles)
    demo = bundles / "tienda-demo"
    shutil.move(bundles / "ventas", demo)
    head = (demo / "bundle.yaml").read_text(encoding="utf-8").replace("id: ventas", "id: tienda-demo")
    (demo / "bundle.yaml").write_text(head, encoding="utf-8")
    baja = demo / "capabilities" / "baja.yaml"
    baja.write_text(baja.read_text(encoding="utf-8").replace("de la tienda?", "de la zapatería?"), encoding="utf-8")
    monkeypatch.setattr(registry, "BUNDLES_DIR", bundles)
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "tienda-demo")

    chosen = capability("baja")

    assert chosen.bundle == "tienda-demo@1"
    [question] = chosen.ask(_inbound("no me escriban más"))[1]
    assert question.text.endswith("de la zapatería?")


def test_a_configured_bundle_that_does_not_exist_fails_loudly(monkeypatch) -> None:
    """Nunca se corre la inteligencia de otra tienda por un error de config."""
    from src.sdk.decisionkit import BundleError

    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "zapateria")

    with pytest.raises(BundleError, match="zapateria"):
        capability("baja")


def test_a_bundle_whose_id_is_not_its_folder_is_rejected(tmp_path: Path, monkeypatch) -> None:
    from src.sdk.decisionkit import BundleError

    bundles = tmp_path / "bundles"
    shutil.copytree(registry.BUNDLES_DIR, bundles)
    shutil.move(bundles / "ventas", bundles / "otra-carpeta")
    monkeypatch.setattr(registry, "BUNDLES_DIR", bundles)
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "otra-carpeta")

    with pytest.raises(BundleError, match="ventas"):
        capability("baja")


def test_no_place_names_a_capability_class_anymore() -> None:
    """Los lugares nombran la decisión; solo el resolutor (y las pruebas)
    conocen las clases. Así, cambiar una capacidad de clase a paquete (o de
    paquete) no toca a nadie."""
    classes = sorted({type(c).__name__ for c in registry.class_capabilities().values()} | {"BundledCapability"})
    pattern = re.compile(r"\b(" + "|".join(classes) + r")\(|\bbundled_capability\(")
    allowed = {
        SALES / "decisions" / "registry.py",
        SALES / "decisions" / "bundled.py",
    }
    offenders = []
    for path in sorted(CHATS.rglob("*.py")):
        if path in allowed or "capabilities" in path.parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if pattern.search(code) and not code.lstrip().startswith("class "):
                offenders.append(f"{path.relative_to(CHATS)}:{n}: {line.strip()}")
    assert offenders == []


def test_no_place_imports_a_capability_class_or_its_instance() -> None:
    """Tampoco por import: ni la clase ni su instancia global (`PERSONA`,
    `RELEVO`…) salen de `decisions/capabilities/` o del egreso, salvo hacia el
    resolutor."""
    import ast
    import importlib

    instances = registry.class_capabilities().values()
    kinds = tuple({type(c) for c in instances})
    forbidden: set[str] = {k.__name__ for k in kinds}
    sources = ["agente", "datos", "lecturas", "lecturas_pedido", "mapeos", "texto"]
    modules = [importlib.import_module(f"src.plugins.chats.agent.sales.decisions.capabilities.{m}") for m in sources]
    modules.append(importlib.import_module("src.plugins.chats.agent.sales.decisions.egress"))
    for module in modules:
        forbidden |= {name for name, value in vars(module).items() if isinstance(value, kinds)}
    origins = {m.__name__ for m in modules}
    offenders = []
    for path in sorted(CHATS.rglob("*.py")):
        if path == SALES / "decisions" / "registry.py" or "capabilities" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module in origins:
                bad = sorted(alias.name for alias in node.names if alias.name in forbidden)
                if bad:
                    offenders.append(f"{path.relative_to(CHATS)}:{node.lineno}: {', '.join(bad)}")
    assert offenders == []


def _inbound(text: str):
    from src.plugins.chats.agent.sales.decisions.readings import Inbound

    return Inbound(session_id="wa_573001234567", text=text, now_ms=1)
