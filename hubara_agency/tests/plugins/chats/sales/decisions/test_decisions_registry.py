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
import importlib
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


def test_every_capability_comes_from_the_bundle() -> None:
    assert capability("baja").bundle == "ventas@1"
    assert capability("compra").bundle == "ventas@1"
    assert capability("datos").bundle == "ventas@1"


def test_the_default_bundle_has_every_capability() -> None:
    """F4: las 29 capacidades salen del paquete; las clases quedan solo como
    oráculo de la paridad (y se pueden borrar)."""
    assert set(registry.active_bundle().capabilities) == set(registry._CLASS_PATHS)


def test_a_variant_answers_to_the_control_it_shares() -> None:
    """`destinatario_plantilla` es la misma decisión preguntada de otra forma:
    mismo interruptor en el panel y misma traza que `destinatario`."""
    assert capability("destinatario_plantilla").name == "destinatario"
    assert capability("destinatario_plantilla").bundle == "ventas@1"
    assert capability("destinatario_plantilla") is not capability("destinatario")


def test_the_same_name_is_the_same_object() -> None:
    assert capability("baja") is capability("baja")
    assert capability("datos") is capability("datos")


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


def _broken_bundles(tmp_path: Path, monkeypatch) -> Path:
    bundles = tmp_path / "bundles"
    shutil.copytree(registry.BUNDLES_DIR, bundles)
    compra = bundles / "ventas" / "capabilities" / "compra.yaml"
    compra.write_text(compra.read_text(encoding="utf-8").replace("decide:", "decide_mal:", 1), encoding="utf-8")
    monkeypatch.setattr(registry, "BUNDLES_DIR", bundles)
    monkeypatch.delenv("SALES_DECISIONS_BUNDLE", raising=False)
    registry.reset()
    return bundles


def test_a_bundle_that_does_not_compile_is_compiled_once_not_on_every_call(tmp_path: Path, monkeypatch) -> None:
    """Premortem 2026-10-02: el caché no guardaba el error y cada decisión
    recompilaba el paquete entero (~0,5 s de CPU en el event loop de la API)."""
    from src.sdk.decisionkit import BundleError

    _broken_bundles(tmp_path, monkeypatch)
    calls: list[str] = []
    real = registry.load_bundle
    monkeypatch.setattr(registry, "load_bundle", lambda *a, **kw: calls.append("x") or real(*a, **kw))

    for _ in range(3):
        with pytest.raises(BundleError):
            capability("baja")

    assert calls == ["x"]
    registry.reset()


def test_warm_up_compiles_the_store_bundle_and_says_which(monkeypatch) -> None:
    import structlog

    monkeypatch.delenv("SALES_DECISIONS_BUNDLE", raising=False)
    registry.reset()

    with structlog.testing.capture_logs() as logs:
        registry.warm_up()

    assert registry._bundle.cache_info().currsize == 1
    assert any(e["event"] == "decisions.bundle_ready" and e["bundle"] == "ventas@1" for e in logs), logs


def test_warm_up_with_a_broken_bundle_logs_an_error_and_does_not_raise(tmp_path: Path, monkeypatch) -> None:
    """El arranque no cae (el ingest sigue con las reglas del código y el
    deploy ya lo frena antes): queda un error claro con el motivo."""
    import structlog

    _broken_bundles(tmp_path, monkeypatch)

    with structlog.testing.capture_logs() as logs:
        registry.warm_up()

    [error] = [e for e in logs if e["event"] == "decisions.bundle_broken"]
    assert error["log_level"] == "error" and "DB001" in error["error"]
    registry.reset()


@pytest.mark.parametrize("module", ["src.plugins.chats.workers.sales", "src.plugins.chats.workers.remarketing"])
def test_the_workers_warm_up_the_store_bundle_before_listening(module: str) -> None:
    """La primera decisión no compila el paquete dentro de una activity, y el
    log de arranque dice qué paquete corre."""
    import inspect

    main = importlib.import_module(module).main
    source = inspect.getsource(main)

    assert "warm_up" in source and source.index("warm_up") < source.index("worker.run()")


async def test_the_api_warms_up_the_store_bundle_off_the_event_loop(monkeypatch) -> None:
    import asyncio

    from src.plugins.chats.api import sales as sales_api

    warmed: list[str] = []
    monkeypatch.setattr(registry, "warm_up", lambda: warmed.append("ok"))
    hooks = [h for h in sales_api.router.on_startup if h.__name__ == "_warm_decisions"]

    assert len(hooks) == 1
    await hooks[0]()
    for _ in range(50):
        if warmed:
            break
        await asyncio.sleep(0.01)
    assert warmed == ["ok"]


def test_the_catalog_lists_every_capability_the_code_asks_for() -> None:
    import yaml

    from src.plugins.chats.shared.store_pack import CATALOG_PATH

    catalog = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))

    assert set(catalog["capabilities"]) == set(registry._CLASS_PATHS)


def test_a_capability_missing_from_the_bundle_never_falls_back_to_a_class(tmp_path: Path, monkeypatch) -> None:
    """Premortem 2026-10-02: «nunca se corre la inteligencia de otra tienda»."""
    monkeypatch.delenv("SALES_DECISIONS_BUNDLE", raising=False)
    registry.reset()
    bundle = registry.active_bundle()
    monkeypatch.setattr(type(bundle), "capabilities", property(lambda self: {"baja": None}), raising=False)

    with pytest.raises(KeyError, match="cortesia"):
        capability("cortesia")
    registry.reset()


def test_a_builtin_that_fails_never_breaks_the_caller(monkeypatch) -> None:
    """Premortem 2026-10-02: una excepción de un builtin al armar la pregunta
    (o al decidir) tumbaba la guarda. La capacidad no pregunta (o duda) y
    decide la regla; queda un error con el paquete y la capacidad."""
    import structlog

    from src.plugins.chats.agent.sales.decisions import bundled

    monkeypatch.delenv("SALES_DECISIONS_BUNDLE", raising=False)
    registry.reset()
    baja = capability("baja")
    real = bundled.builtin

    def broken(kind: str, name: str):
        if kind in ("state", "view", "options", "items"):
            def fails(*_a, **_kw):
                raise TypeError("got an unexpected keyword argument 'items'")
            return fails
        return real(kind, name)

    monkeypatch.setattr(bundled, "builtin", broken)

    with structlog.testing.capture_logs() as logs:
        asked = baja.ask(_inbound("no me escriban más"))

    assert asked is None
    assert any(e["event"] == "decisions.builtin_failed" and e["log_level"] == "error" and e["bundle"] == "ventas@1"
               and e["capability"] == "baja" for e in logs), logs
    registry.reset()
