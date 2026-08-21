"""La tool `list_categories` queda registrada en el worker de Sales.

Guard de la lección "worker lambda missing import": el registro con lambda
carga limpio aunque la clase no esté importada — el NameError aparece recién
en runtime de la activity, tumbando conversaciones reales.
"""
from __future__ import annotations

from pathlib import Path


def test_sales_worker_registers_list_categories(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MEDUSA_BASE_URL", "http://medusa.test")
    monkeypatch.setenv("MEDUSA_ADMIN_TOKEN", "dummy")
    import src.plugins.chats.workers.sales  # noqa: F401  (registra las tools)
    from src.platform.tool_extensions import _EXTENSIONS  # type: ignore

    factory = dict(_EXTENSIONS).get("sales.list_categories")
    assert factory is not None, "sales.list_categories no está registrada"
    tool = factory(tmp_path)  # ejecuta el cuerpo del lambda (caza NameError)
    assert tool.name == "list_categories"
